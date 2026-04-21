"""SiLA device discovery.

Supports both SiLA 1 (NetBIOS + GetDeviceIdentification) and SiLA 2 (mDNS) protocols.

Example:
  >>> from pylabrobot.io.sila.discovery import discover
  >>> devices = await discover()
  >>> for d in devices:
  ...     print(d.host, d.port, d.name)
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import socket
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, AsyncGenerator, Optional

import anyio

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


def _interface_name_for_ip_sync(ip: str) -> Optional[str]:
  """Return the OS interface name (e.g. 'en13', 'eth0') bound to the given IP, or None.

  Uses ``ifconfig`` on macOS/BSD and ``ip`` on Linux.
  """
  # Build a pattern that matches the IP as a whole token (not a substring).
  # Escaped so 169.254.1.1 won't match 169.254.1.10.
  ip_pattern = re.compile(r"(?<!\d)" + re.escape(ip) + r"(?!\d)")

  if sys.platform == "linux":
    try:
      out = subprocess.check_output(
        ["ip", "-o", "-4", "addr", "show"],
        stderr=subprocess.DEVNULL,
        timeout=2,
      ).decode(errors="replace")
      for line in out.splitlines():
        if ip_pattern.search(line):
          # Format: "2: eth0    inet 169.254.229.18/16 ..."
          parts = line.split()
          if len(parts) >= 2:
            return parts[1].rstrip(":")
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
      pass
  else:
    # macOS/BSD: parse ifconfig
    try:
      out = subprocess.check_output(
        ["ifconfig"],
        stderr=subprocess.DEVNULL,
        timeout=2,
      ).decode(errors="replace")
      current_iface: Optional[str] = None
      for line in out.splitlines():
        if not line.startswith(("\t", " ")):
          # Interface header, e.g. "en13: flags=..."
          current_iface = line.split(":")[0]
        elif ip_pattern.search(line) and current_iface is not None:
          return current_iface
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
      pass

  return None


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
  results: dict[str, str] = {}

  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  sock.bind((interface, 0))
  # Use a short blocking timeout so recvfrom in the executor thread returns
  # promptly rather than blocking forever, while still allowing AnyIO
  # move_on_after to enforce the overall deadline.
  sock.settimeout(0.5)

  # Link-local is always a /16 subnet (169.254.0.0/16), so broadcast to x.x.255.255.
  parts = interface.split(".")
  broadcast = f"{parts[0]}.{parts[1]}.255.255"

  try:
    await anyio.to_thread.run_sync(lambda: sock.sendto(_NBNS_WILDCARD_QUERY, (broadcast, 137)))
  except OSError:
    logger.debug("NetBIOS broadcast failed on %s", interface)
    sock.close()
    return results

  with anyio.move_on_after(timeout):
    while True:
      try:
        data, (addr, _) = await anyio.to_thread.run_sync(lambda: sock.recvfrom(65535))
      except (socket.timeout, OSError):
        continue

      if addr == interface:
        continue

      name = _decode_nbns_name(data)
      if name:
        results[addr] = name

  sock.close()
  return results


async def _ping_broadcast(interface: str) -> None:
  """Ping the link-local broadcast address to populate the ARP table.

  Many devices won't respond to NetBIOS but will respond to ARP requests
  triggered by a broadcast ping. We send the ping and wait briefly for
  responses so that the subsequent ARP table read finds them.

  On macOS, ``ping -b <iface_name>`` is required to bind to the correct
  interface — without it the broadcast goes out on the default route.
  On Linux, ``ping -I <iface_name>`` serves the same purpose.
  """
  parts = interface.split(".")
  broadcast = f"{parts[0]}.{parts[1]}.255.255"

  if sys.platform == "win32":
    cmd = ["ping", "-n", "3", "-w", "1000", broadcast]
  elif sys.platform == "linux":
    iface_name = await anyio.to_thread.run_sync(_interface_name_for_ip_sync, interface)
    if iface_name:
      cmd = ["ping", "-c", "3", "-W", "1", "-I", iface_name, broadcast]
    else:
      cmd = ["ping", "-c", "3", "-W", "1", broadcast]
  else:
    # macOS / BSD: -b binds to a named interface
    iface_name = await anyio.to_thread.run_sync(_interface_name_for_ip_sync, interface)
    if iface_name:
      cmd = ["ping", "-c", "3", "-W", "1", "-b", iface_name, broadcast]
    else:
      cmd = ["ping", "-c", "3", "-W", "1", broadcast]

  try:
    with anyio.move_on_after(5):
      await anyio.run_process(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  except subprocess.CalledProcessError:
    pass
  except FileNotFoundError:
    pass

  # Give devices a moment to respond so ARP entries are populated.
  await anyio.sleep(0.5)


async def _arp_scan(interface: str) -> dict[str, str]:
  """Ping the link-local broadcast, then read the ARP table for live hosts.

  First sends a broadcast ping to force ARP resolution for all devices on the
  subnet, then reads the OS ARP table. This ensures devices that don't respond
  to NetBIOS are still discoverable.

  Returns a dict mapping IP -> hostname (or empty string if unknown).

  Works on macOS (``arp -an``), Linux (``/proc/net/arp``), and Windows (``arp -a``).
  """
  await _ping_broadcast(interface)

  if sys.platform == "linux":
    return await _arp_scan_linux(interface)
  elif sys.platform == "win32":
    return await _arp_scan_windows(interface)
  else:
    return await _arp_scan_bsd(interface)


async def _arp_scan_bsd(interface: str) -> dict[str, str]:
  """Parse ``arp -an`` output (macOS / BSD), filtering to entries on the correct interface.

  Example line::

      ? (169.254.245.237) at 0:5:51:e:e5:7e on en13 [ethernet]
  """
  # Resolve our IP to an interface name (e.g. "en13") so we can filter ARP entries.
  iface_name = await anyio.to_thread.run_sync(_interface_name_for_ip_sync, interface)
  if not iface_name:
    logger.debug("could not resolve interface name for %s, skipping ARP scan", interface)
    return {}

  try:
    with anyio.move_on_after(5) as cancel_scope:
      result = await anyio.run_process(["arp", "-an"])
      stdout = result.stdout

    if cancel_scope.cancel_called:
      return {}
  except (FileNotFoundError, subprocess.CalledProcessError):
    return {}

  results: dict[str, str] = {}
  for line in stdout.decode(errors="replace").splitlines():
    if "incomplete" in line:
      continue
    if f"on {iface_name} " not in line and not line.endswith(f"on {iface_name}"):
      continue
    m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", line)
    if not m:
      continue
    ip = m.group(1)
    if not ip.startswith("169.254."):
      continue
    if ip == interface:
      continue
    results[ip] = ""

  return results


async def _arp_scan_linux(interface: str) -> dict[str, str]:
  """Parse ``/proc/net/arp`` (Linux).

  Example content::

      IP address       HW type     Flags       HW address            Mask     Device
      169.254.245.237  0x1         0x2         00:05:51:0e:e5:7e     *        eth0
  """
  if not os.path.exists("/proc/net/arp"):
    # Fall back to arp -an on non-procfs Linux systems.
    return await _arp_scan_bsd(interface)

  from anyio import Path

  try:
    path = Path("/proc/net/arp")
    text = await path.read_text()
  except OSError:
    return {}

  # Determine the OS-level interface name for our IP so we can filter entries.
  iface_name = await anyio.to_thread.run_sync(_interface_name_for_ip_sync, interface)
  if not iface_name:
    logger.debug("could not resolve interface name for %s, skipping ARP scan", interface)
    return {}

  results: dict[str, str] = {}
  for line in text.splitlines()[1:]:  # skip header
    parts = line.split()
    if len(parts) < 6:
      continue
    ip, flags, device = parts[0], parts[2], parts[5]
    if flags == "0x0":  # incomplete entry
      continue
    if not ip.startswith("169.254."):
      continue
    if ip == interface:
      continue
    if device != iface_name:
      continue
    results[ip] = ""

  return results


async def _arp_scan_windows(interface: str) -> dict[str, str]:
  """Parse ``arp -a`` output on Windows.

  Example output::

      Interface: 169.254.229.18 --- 0x5
        Internet Address      Physical Address      Type
        169.254.245.237       00-05-51-0e-e5-7e     dynamic

  Windows groups entries by interface, so we find the section matching our IP.
  """
  try:
    with anyio.move_on_after(5) as cancel_scope:
      result = await anyio.run_process(["arp", "-a"])
      stdout = result.stdout

    if cancel_scope.cancel_called:
      return {}
  except (FileNotFoundError, subprocess.CalledProcessError):
    return {}

  results: dict[str, str] = {}
  in_our_interface = False
  for line in stdout.decode(errors="replace").splitlines():
    line = line.strip()
    if not line:
      in_our_interface = False
      continue
    # Detect interface header: "Interface: 169.254.229.18 --- 0x5"
    if line.startswith("Interface:"):
      in_our_interface = f" {interface} " in line
      continue
    if not in_our_interface:
      continue
    # Skip the column header line
    if "Internet Address" in line or "Physical Address" in line:
      continue
    parts = line.split()
    if len(parts) < 3:
      continue
    ip_addr = parts[0]
    if not ip_addr.startswith("169.254."):
      continue
    if ip_addr == interface:
      continue
    results[ip_addr] = ""

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
  """Query a single host for SiLA 1 GetDeviceIdentification.

  The entire operation (connect + send + recv) is bounded by a single ``timeout`` deadline.
  """
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

  try:
    with anyio.fail_after(timeout):
      async with await anyio.connect_tcp(host, port, local_host=interface) as stream:
        await stream.send(request)

        resp = b""
        try:
          while True:
            chunk = await stream.receive()
            resp += chunk
        except anyio.EndOfStream:
          pass

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
  except (OSError, TimeoutError):
    pass
  return None


async def _discover_sila1(
  timeout: float = 5.0,
  interface: Optional[str] = None,
  port: int = 8080,
) -> list[SiLADevice]:
  """Discover SiLA 1 devices using NetBIOS broadcast + ARP fallback + GetDeviceIdentification.

  1. Run NetBIOS scan and ARP table lookup in parallel to find live hosts.
  2. For each discovered host, query port 8080 with GetDeviceIdentification.
  """
  if not interface:
    logger.debug("no interface provided for SiLA 1 discovery, skipping")
    return []

  devices: list[SiLADevice] = []
  hosts: dict[str, str] = {}

  with anyio.move_on_after(timeout):
    # Run host discovery methods in parallel.
    # Cap NetBIOS at 3s — any device that responds will do so within a second or two.
    scan_results = {}
    async with anyio.create_task_group() as tg:

      async def do_netbios():
        scan_results["netbios"] = await _netbios_scan(interface, timeout=min(timeout, 3.0))

      async def do_arp():
        scan_results["arp"] = await _arp_scan(interface)

      tg.start_soon(do_netbios)
      tg.start_soon(do_arp)

    hosts.update(scan_results.get("netbios", {}))
    for ip, name in scan_results.get("arp", {}).items():
      if ip not in hosts:
        logger.debug("found %s via ARP (not NetBIOS)", ip)
        hosts[ip] = name

    if not hosts:
      return []

    host_list = [ip for ip in hosts if not ip.endswith(".255")]

    identification_results = {}
    async with anyio.create_task_group() as tg:

      async def do_query(ip):
        identification_results[ip] = await _get_device_identification(
          ip, port, interface=interface, timeout=timeout
        )

      for ip in host_list:
        tg.start_soon(do_query, ip)

    for ip in host_list:
      r = identification_results.get(ip)
      if isinstance(r, SiLADevice):
        devices.append(r)
      else:
        # Host is reachable but didn't respond to GetDeviceIdentification.
        # Include it with whatever we know (name from NetBIOS, or just the IP).
        name = hosts.get(ip, "") or ip
        devices.append(SiLADevice(host=ip, port=port, name=name, sila_version=1))

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

  zc = await anyio.to_thread.run_sync(Zeroconf)
  try:
    listener = _Listener()
    await anyio.to_thread.run_sync(lambda: ServiceBrowser(zc, SILA_MDNS_TYPE, listener))
    await anyio.sleep(timeout)
  finally:
    with anyio.CancelScope(shield=True):
      await anyio.to_thread.run_sync(zc.close)

  return devices


# ---------------------------------------------------------------------------
# Combined discovery
# ---------------------------------------------------------------------------


async def discover_iter(
  timeout: float = 5.0,
  interface: Optional[str] = None,
) -> AsyncGenerator[SiLADevice, None]:
  """Async generator that yields :class:`SiLADevice` instances as they are found.

  Runs SiLA 1 and SiLA 2 probes concurrently and yields each device immediately
  upon discovery, without waiting for all probes to finish.

  Args:
    timeout: How long to listen for responses, in seconds.
    interface: Local IP address of the interface to use for SiLA 1 discovery.
      If None, auto-detects all link-local interfaces.

  Yields:
    SiLADevice instances as they are discovered.
  """
  if interface:
    interfaces = [interface]
  else:
    interfaces = _get_link_local_interfaces()
    if not interfaces:
      logger.debug("no link-local interfaces found, SiLA 1 discovery will be skipped")

  send_stream, receive_stream = anyio.create_memory_object_stream(100)

  async def worker(s_stream, func, *args):
    async with s_stream:
      try:
        result = await func(*args)
        if isinstance(result, list):
          for d in result:
            await s_stream.send(d)
      except Exception:
        pass

  seen: set[tuple[str, int]] = set()

  async with anyio.create_task_group() as tg:
    async with send_stream:
      for iface in interfaces:
        tg.start_soon(worker, send_stream.clone(), _discover_sila1, timeout, iface)
      tg.start_soon(worker, send_stream.clone(), _discover_sila2, timeout)

    async for d in receive_stream:
      key = (d.host, d.port)
      if key not in seen:
        seen.add(key)
        yield d


async def discover(
  timeout: float = 5.0,
  interface: Optional[str] = None,
) -> list[SiLADevice]:
  """Discover SiLA devices on the local network.

  Convenience wrapper around :func:`discover_iter` that collects all results into a list.

  Args:
    timeout: How long to listen for responses, in seconds.
    interface: Local IP address of the interface to use for SiLA 1 discovery.
      If None, auto-detects all link-local interfaces.

  Returns:
    List of discovered devices.
  """
  return [d async for d in discover_iter(timeout=timeout, interface=interface)]


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

  async def main():
    found = False
    async for d in discover_iter(args.timeout, interface=args.interface):
      found = True
      parts = [d.host, str(d.port), d.name, f"SiLA {d.sila_version}"]
      if d.serial_number:
        parts.append(d.serial_number)
      print("\t".join(parts))
    if not found:
      print("No SiLA devices found.")

  anyio.run(main)
