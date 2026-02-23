"""SiLA device discovery.

Supports both SiLA 1 (NetBIOS + GetDeviceIdentification) and SiLA 2 (mDNS) protocols.

Example:
  >>> from pylabrobot.io.sila.discovery import discover
  >>> devices = await discover()
  >>> for d in devices:
  ...     print(d.host, d.port, d.name)
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import socket
import struct
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Optional

try:
  from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

  HAS_ZEROCONF = True
except ImportError:
  HAS_ZEROCONF = False

if TYPE_CHECKING:
  from zeroconf import Zeroconf

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class SiLADevice:
  """A SiLA device found on the network."""

  host: str
  port: int
  name: str
  serial_number: Optional[str] = None
  firmware_version: Optional[str] = None
  sila_version: int = 2

  def __str__(self) -> str:
    return f"{self.name} @ {self.host}:{self.port} (SiLA {self.sila_version})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_link_local_interfaces() -> list[str]:
  """Return local IPs of all interfaces that have a 169.254.x.x address."""
  result: list[str] = []
  try:
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
      ip = str(info[4][0])
      if ip.startswith("169.254."):
        result.append(ip)
  except socket.gaierror:
    pass

  return result


# ---------------------------------------------------------------------------
# SiLA 1 – NetBIOS name query + GetDeviceIdentification on port 8080
# ---------------------------------------------------------------------------

# NetBIOS wildcard NBSTAT query: asks any host to report its name table.
_NBNS_WILDCARD_QUERY = (
  b"\x00\x01"  # Transaction ID
  b"\x00\x00"  # Flags: query
  b"\x00\x01"  # Questions: 1
  b"\x00\x00"  # Answer RRs
  b"\x00\x00"  # Authority RRs
  b"\x00\x00"  # Additional RRs
  b"\x20"  # Name length (32 encoded bytes)
  b"CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # Encoded '*' (wildcard) padded to 16 bytes
  b"\x00"  # Name terminator
  b"\x00\x21"  # Type: NBSTAT
  b"\x00\x01"  # Class: IN
)

# Raw SOAP envelope for SiLA 1 GetDeviceIdentification.  We build the HTTP request manually
# rather than pulling in an HTTP library, since this runs on a controlled lab network against
# known SiLA 1 endpoints that speak HTTP/1.1.
_SILA1_ID_SOAP = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:sila="http://sila.coop">
  <soap:Body>
    <sila:GetDeviceIdentification>
      <sila:requestId>1</sila:requestId>
    </sila:GetDeviceIdentification>
  </soap:Body>
</soap:Envelope>"""


def _decode_nbns_name(data: bytes) -> Optional[str]:
  """Extract the first NetBIOS name from an NBSTAT response."""
  try:
    idx = data.find(b"\x00\x21\x00\x01", 12)  # NBSTAT type in answer
    if idx < 0:
      return None
    idx += 4  # skip type + class
    idx += 4  # skip TTL
    idx += 2  # skip rdlength
    num_names = data[idx]
    idx += 1
    if num_names < 1:
      return None
    # First name entry: 15 bytes name + 1 byte suffix + 2 bytes flags
    name = data[idx : idx + 15].decode("ascii", errors="replace").strip()
    return name
  except (IndexError, struct.error):
    return None


async def _netbios_scan(interface: str, timeout: float = 3.0) -> dict[str, str]:
  """Send a broadcast NetBIOS wildcard query and collect responses.

  Returns a dict mapping IP -> NetBIOS name.
  """
  loop = asyncio.get_running_loop()
  results: dict[str, str] = {}

  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  sock.bind((interface, 0))
  # Use a short blocking timeout so recvfrom in the executor thread returns
  # promptly rather than blocking forever, while still allowing the asyncio
  # wait_for to enforce the overall deadline.
  sock.settimeout(0.5)

  # Link-local is always a /16 subnet (169.254.0.0/16), so broadcast to x.x.255.255.
  parts = interface.split(".")
  broadcast = f"{parts[0]}.{parts[1]}.255.255"

  # Use run_in_executor for sendto/recvfrom since the async loop equivalents
  # (loop.sock_sendto / loop.sock_recvfrom) require Python 3.11+.
  await loop.run_in_executor(None, lambda: sock.sendto(_NBNS_WILDCARD_QUERY, (broadcast, 137)))

  deadline = loop.time() + timeout
  while loop.time() < deadline:
    try:
      data, (addr, _) = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: sock.recvfrom(65535)),
        timeout=max(0.1, deadline - loop.time()),
      )
    except (asyncio.TimeoutError, socket.timeout, OSError):
      continue

    if addr == interface:
      continue

    name = _decode_nbns_name(data)
    if name:
      results[addr] = name

  sock.close()
  return results


def _parse_device_identification(host: str, port: int, xml_bytes: bytes) -> Optional[SiLADevice]:
  """Parse a GetDeviceIdentification SOAP response."""
  try:
    root = ET.fromstring(xml_bytes)
  except ET.ParseError:
    return None

  name = ""
  serial: Optional[str] = None
  firmware: Optional[str] = None
  for elem in root.iter():
    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
    if tag == "DeviceName" and elem.text:
      name = elem.text
    elif tag == "DeviceSerialNumber" and elem.text:
      serial = elem.text
    elif tag == "DeviceFirmwareVersion" and elem.text:
      firmware = elem.text

  if not name:
    return None

  return SiLADevice(
    host=host,
    port=port,
    name=name,
    serial_number=serial,
    firmware_version=firmware,
    sila_version=1,
  )


async def _get_device_identification(
  host: str,
  port: int,
  interface: Optional[str] = None,
  timeout: float = 3.0,
) -> Optional[SiLADevice]:
  """Query a single host for SiLA 1 GetDeviceIdentification."""
  body = _SILA1_ID_SOAP.encode("utf-8")
  request = (
    f"POST / HTTP/1.1\r\n"
    f"Host: {host}:{port}\r\n"
    f"Content-Type: text/xml; charset=utf-8\r\n"
    f"Content-Length: {len(body)}\r\n"
    f'SOAPAction: "http://sila.coop/GetDeviceIdentification"\r\n'
    f"Connection: close\r\n"
    f"\r\n"
  ).encode() + body

  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  try:
    sock.settimeout(timeout)
    if interface:
      sock.bind((interface, 0))
    sock.setblocking(False)

    loop = asyncio.get_running_loop()
    await asyncio.wait_for(loop.sock_connect(sock, (host, port)), timeout=timeout)
    await asyncio.wait_for(loop.sock_sendall(sock, request), timeout=timeout)

    resp = b""
    while True:
      try:
        chunk = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=timeout)
        if not chunk:
          break
        resp += chunk
      except asyncio.TimeoutError:
        break

    # Extract XML from HTTP response
    text = resp.decode("utf-8", errors="replace")
    if "<?xml" in text:
      xml_start = text.index("<?xml")
      xml_text = text[xml_start:]
      # Strip trailing chunked encoding artifacts
      for suffix in ["\r\n0\r\n\r\n", "\n0\n\n", "\r\n0"]:
        if xml_text.endswith(suffix):
          xml_text = xml_text[: -len(suffix)]
          break
      return _parse_device_identification(host, port, xml_text.encode("utf-8"))
  except (OSError, asyncio.TimeoutError):
    pass
  finally:
    sock.close()
  return None


async def _discover_sila1(
  timeout: float = 5.0,
  interface: Optional[str] = None,
  port: int = 8080,
) -> list[SiLADevice]:
  """Discover SiLA 1 devices using NetBIOS broadcast + GetDeviceIdentification.

  1. Send a broadcast NetBIOS NBSTAT query to find live hosts on the link-local network.
  2. For each responder, query port 8080 with GetDeviceIdentification.
  """
  if not interface:
    logger.debug("no interface provided for SiLA 1 discovery, skipping")
    return []

  hosts = await _netbios_scan(interface, timeout=min(timeout, 3.0))
  if not hosts:
    return []

  devices: list[SiLADevice] = []
  coros = [_get_device_identification(ip, port, interface=interface, timeout=3.0) for ip in hosts]
  results = await asyncio.gather(*coros, return_exceptions=True)
  for r in results:
    if isinstance(r, SiLADevice):
      devices.append(r)

  return devices


# ---------------------------------------------------------------------------
# SiLA 2 – mDNS
# ---------------------------------------------------------------------------

SILA_MDNS_TYPE = "_sila._tcp.local."


async def _discover_sila2(timeout: float = 5.0) -> list[SiLADevice]:
  if not HAS_ZEROCONF:
    logger.warning("zeroconf not installed, skipping SiLA 2 discovery")
    return []

  devices: list[SiLADevice] = []

  class _Listener(ServiceListener):
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      info = zc.get_service_info(type_, name)
      if info and info.addresses and info.port is not None and info.server:
        host = socket.inet_ntoa(info.addresses[0])
        devices.append(SiLADevice(host=host, port=info.port, name=info.server, sila_version=2))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      pass

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      pass

  loop = asyncio.get_running_loop()
  zc = await loop.run_in_executor(None, Zeroconf)
  try:
    listener = _Listener()
    await loop.run_in_executor(None, lambda: ServiceBrowser(zc, SILA_MDNS_TYPE, listener))
    await asyncio.sleep(timeout)
  finally:
    await loop.run_in_executor(None, zc.close)

  return devices


# ---------------------------------------------------------------------------
# Combined discovery
# ---------------------------------------------------------------------------


async def discover(
  timeout: float = 5.0,
  interface: Optional[str] = None,
) -> list[SiLADevice]:
  """Discover SiLA devices on the local network.

  Runs SiLA 1 (NetBIOS + GetDeviceIdentification) and SiLA 2 (mDNS) probes in parallel.

  For SiLA 1, the ``interface`` parameter specifies which local IP to send NetBIOS broadcasts
  from. If not provided, all link-local (169.254.x.x) interfaces are scanned automatically.

  Args:
    timeout: How long to listen for responses, in seconds.
    interface: Local IP address of the interface to use for SiLA 1 discovery.
      If None, auto-detects all link-local interfaces.

  Returns:
    List of discovered devices.
  """

  if interface:
    interfaces = [interface]
  else:
    interfaces = _get_link_local_interfaces()
    if not interfaces:
      logger.debug("no link-local interfaces found, SiLA 1 discovery will be skipped")

  coros: list = [_discover_sila1(timeout=timeout, interface=iface) for iface in interfaces]
  coros.append(_discover_sila2(timeout))

  results = await asyncio.gather(*coros, return_exceptions=True)

  seen: set[tuple[str, int]] = set()
  devices: list[SiLADevice] = []
  for r in results:
    if isinstance(r, list):
      for d in r:
        key = (d.host, d.port)
        if key not in seen:
          seen.add(key)
          devices.append(d)
  return devices


if __name__ == "__main__":
  import argparse

  parser = argparse.ArgumentParser(description="Discover SiLA devices on the local network")
  parser.add_argument(
    "-t",
    "--timeout",
    type=float,
    default=5.0,
    help="discovery timeout in seconds (default: 5)",
  )
  parser.add_argument(
    "--interface",
    type=str,
    default=None,
    help="local IP of interface for SiLA 1 scan (e.g. 169.254.183.87)",
  )
  args = parser.parse_args()

  found = asyncio.run(discover(args.timeout, interface=args.interface))
  if not found:
    print("No SiLA devices found.")
  else:
    for d in found:
      parts = [d.host, str(d.port), d.name, f"SiLA {d.sila_version}"]
      if d.serial_number:
        parts.append(d.serial_number)
      print("\t".join(parts))
