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
import threading
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional

import anyio
from anyio.streams.memory import MemoryObjectSendStream

from pylabrobot.concurrency import AsyncExitStackWithShielding

try:
  from zeroconf import (  # type: ignore[import-not-found]
    ServiceBrowser,
    ServiceListener,
    Zeroconf,
  )

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


async def _netbios_scan(
  interface: str, timeout: float = 3.0, results: Optional[dict[str, str]] = None
) -> dict[str, str]:
  """Send a broadcast NetBIOS wildcard query and collect responses.

  Returns a dict mapping IP -> NetBIOS name. When provided, ``results`` is
  updated as replies arrive so callers retain them if the scan is cancelled.
  """
  if results is None:
    results = {}
  deadline = anyio.current_time() + timeout

  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((interface, 0))
    # Link-local is always a /16 subnet (169.254.0.0/16), so broadcast to x.x.255.255.
    parts = interface.split(".")
    broadcast = f"{parts[0]}.{parts[1]}.255.255"

    remaining = deadline - anyio.current_time()
    if remaining <= 0:
      return results
    # Bound the send worker too; it owns the socket until it finishes.
    sock.settimeout(min(0.5, remaining))
    try:
      await anyio.to_thread.run_sync(
        lambda: sock.sendto(_NBNS_WILDCARD_QUERY, (broadcast, 137)),
        abandon_on_cancel=False,
      )
    except OSError:
      logger.debug("NetBIOS broadcast failed on %s", interface)
      return results

    while (remaining := deadline - anyio.current_time()) > 0:
      # The worker must finish its recv before cancellation can close the socket.
      sock.settimeout(min(0.5, remaining))
      try:
        data, (addr, _) = await anyio.to_thread.run_sync(
          lambda: sock.recvfrom(65535), abandon_on_cancel=False
        )
      except socket.timeout:
        continue
      except OSError:
        break

      if addr == interface:
        continue

      name = _decode_nbns_name(data)
      if name:
        results[addr] = name
    return results
  finally:
    sock.close()


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
    iface_name = await anyio.to_thread.run_sync(
      _interface_name_for_ip_sync, interface, abandon_on_cancel=False
    )
    if iface_name:
      cmd = ["ping", "-c", "3", "-W", "1", "-I", iface_name, broadcast]
    else:
      cmd = ["ping", "-c", "3", "-W", "1", broadcast]
  else:
    # macOS / BSD: -b binds to a named interface
    iface_name = await anyio.to_thread.run_sync(
      _interface_name_for_ip_sync, interface, abandon_on_cancel=False
    )
    if iface_name:
      cmd = ["ping", "-c", "3", "-W", "1", "-b", iface_name, broadcast]
    else:
      cmd = ["ping", "-c", "3", "-W", "1", broadcast]

  try:
    with anyio.fail_after(5):
      await anyio.run_process(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
      )
  except (FileNotFoundError, TimeoutError):
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
  iface_name = await anyio.to_thread.run_sync(
    _interface_name_for_ip_sync, interface, abandon_on_cancel=False
  )
  if not iface_name:
    logger.debug("could not resolve interface name for %s, skipping ARP scan", interface)
    return {}

  try:
    with anyio.fail_after(5):
      completed = await anyio.run_process(
        ["arp", "-an"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False
      )
  except (FileNotFoundError, TimeoutError):
    return {}

  results: dict[str, str] = {}
  for line in completed.stdout.decode(errors="replace").splitlines():
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

  try:

    def _read():
      with open("/proc/net/arp") as f:
        return f.read()

    text = await anyio.to_thread.run_sync(_read, abandon_on_cancel=False)
  except OSError:
    return {}

  # Determine the OS-level interface name for our IP so we can filter entries.
  iface_name = await anyio.to_thread.run_sync(
    _interface_name_for_ip_sync, interface, abandon_on_cancel=False
  )
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
    with anyio.fail_after(5):
      completed = await anyio.run_process(
        ["arp", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False
      )
  except (FileNotFoundError, TimeoutError):
    return {}

  results: dict[str, str] = {}
  in_our_interface = False
  for line in completed.stdout.decode(errors="replace").splitlines():
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
      stream = await anyio.connect_tcp(
        remote_host=host,
        remote_port=port,
        local_host=interface,
      )
      async with AsyncExitStackWithShielding() as stack:
        stack.push_shielded_async_callback(stream.aclose)
        await stream.send(request)

        response = bytearray()
        while True:
          try:
            chunk = await stream.receive(4096)
          except anyio.EndOfStream:
            break
          if not chunk:
            break
          response.extend(chunk)

    text = response.decode("utf-8", errors="replace")
    if "<?xml" in text:
      xml_start = text.index("<?xml")
      xml_text = text[xml_start:]
      # Strip trailing chunked encoding artifacts
      for suffix in ["\r\n0\r\n\r\n", "\n0\n\n", "\r\n0"]:
        if xml_text.endswith(suffix):
          xml_text = xml_text[: -len(suffix)]
          break
      return _parse_device_identification(host, port, xml_text.encode("utf-8"))
  except (OSError, TimeoutError, anyio.BrokenResourceError):
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

  deadline = anyio.current_time() + timeout

  scan_results: list[dict[str, str]] = [{}, {}]

  async def scan(index: int, probe: Any, *args: Any) -> None:
    try:
      scan_results[index] = await probe(*args)
    except Exception:
      return

  with anyio.CancelScope(deadline=deadline):
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(scan, 0, _netbios_scan, interface, min(timeout, 3.0), scan_results[0])
      task_group.start_soon(scan, 1, _arp_scan, interface)

  hosts: dict[str, str] = {}
  hosts.update(scan_results[0])
  for ip, name in scan_results[1].items():
    if ip not in hosts:
      logger.debug("found %s via ARP (not NetBIOS)", ip)
      hosts[ip] = name

  if not hosts:
    return []

  devices: list[SiLADevice] = []
  host_list = [ip for ip in hosts if not ip.endswith(".255")]
  identifications: dict[str, Optional[SiLADevice]] = {}

  async def identify(ip: str) -> None:
    try:
      identifications[ip] = await _get_device_identification(
        ip, port, interface=interface, timeout=max(0, deadline - anyio.current_time())
      )
    except Exception:
      identifications[ip] = None

  if anyio.current_time() < deadline:
    with anyio.CancelScope(deadline=deadline):
      async with anyio.create_task_group() as task_group:
        for ip in host_list:
          task_group.start_soon(identify, ip)

  for ip in host_list:
    device = identifications.get(ip)
    if device is not None:
      devices.append(device)
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


async def _discover_sila2(
  timeout: float = 5.0, deadline: Optional[float] = None
) -> list[SiLADevice]:
  if not HAS_ZEROCONF:
    logger.warning("zeroconf not installed, skipping SiLA 2 discovery")
    return []

  if deadline is None:
    deadline = anyio.current_time() + timeout
  if anyio.current_time() >= deadline:
    return []
  callback_devices: list[SiLADevice] = []
  callback_lock = threading.Lock()

  class _Listener(ServiceListener):
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      info = zc.get_service_info(type_, name)
      if info and info.addresses and info.port is not None and info.server:
        host = socket.inet_ntoa(info.addresses[0])
        device = SiLADevice(host=host, port=info.port, name=info.server, sila_version=2)
        with callback_lock:
          callback_devices.append(device)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      pass

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      pass

  with anyio.CancelScope(deadline=deadline):
    async with AsyncExitStackWithShielding() as stack:
      # Cancellation cannot interrupt ownership transfer of a constructed Zeroconf.
      with anyio.CancelScope(shield=True):
        zc = await anyio.to_thread.run_sync(Zeroconf, abandon_on_cancel=False)

        async def close_zeroconf() -> None:
          await anyio.to_thread.run_sync(zc.close, abandon_on_cancel=False)

        stack.push_shielded_async_callback(close_zeroconf)
      listener = _Listener()
      await anyio.to_thread.run_sync(
        lambda: ServiceBrowser(zc, SILA_MDNS_TYPE, listener), abandon_on_cancel=False
      )
      await anyio.sleep_until(deadline)

  with callback_lock:
    return list(callback_devices)


# ---------------------------------------------------------------------------
# Combined discovery
# ---------------------------------------------------------------------------


async def discover_iter(
  timeout: float = 5.0,
  interface: Optional[str] = None,
) -> AsyncGenerator[SiLADevice, None]:
  """Async generator that yields :class:`SiLADevice` instances as they are found.

  Runs SiLA 1 and SiLA 2 probes concurrently, then yields their results after all
  discovery resources have left their scopes.

  Args:
    timeout: Overall discovery budget in seconds, starting on the first iteration.
    interface: Local IP address of the interface to use for SiLA 1 discovery.
      If None, auto-detects all link-local interfaces.

  Yields:
    SiLADevice instances as they are discovered.
  """
  deadline = anyio.current_time() + timeout

  send_stream, receive_stream = anyio.create_memory_object_stream[SiLADevice](100)

  async def worker(send: anyio.abc.ObjectSendStream[SiLADevice], probe: Any, *args: Any) -> None:
    async with send:
      if anyio.current_time() >= deadline:
        return
      try:
        devices = await probe(*args)
        for device in devices:
          await send.send(device)
      except Exception:
        return

  async def sila1_probe(iface: str) -> list[SiLADevice]:
    return await _discover_sila1(max(0, deadline - anyio.current_time()), iface)

  seen: set[tuple[str, int]] = set()
  devices: list[SiLADevice] = []
  async with anyio.create_task_group() as task_group:

    async def detect_interfaces(send: MemoryObjectSendStream[SiLADevice]) -> None:
      async with send:
        interfaces: list[str] = []
        with anyio.CancelScope(deadline=deadline):
          interfaces = await anyio.to_thread.run_sync(
            _get_link_local_interfaces, abandon_on_cancel=False
          )
        if not interfaces:
          logger.debug("no link-local interfaces found, SiLA 1 discovery will be skipped")
        for iface in interfaces:
          remaining = deadline - anyio.current_time()
          if remaining <= 0:
            break
          task_group.start_soon(worker, send.clone(), sila1_probe, iface)

    if interface:
      task_group.start_soon(worker, send_stream.clone(), sila1_probe, interface)
    else:
      task_group.start_soon(detect_interfaces, send_stream.clone())
    task_group.start_soon(
      worker,
      send_stream.clone(),
      _discover_sila2,
      max(0, deadline - anyio.current_time()),
      deadline,
    )
    await send_stream.aclose()

    async with receive_stream:
      async for device in receive_stream:
        key = (device.host, device.port)
        if key not in seen:
          seen.add(key)
          devices.append(device)

  for device in devices:
    yield device


async def discover(
  timeout: float = 5.0,
  interface: Optional[str] = None,
) -> list[SiLADevice]:
  """Discover SiLA devices on the local network.

  Convenience wrapper around :func:`discover_iter` that collects all results into a list.

  Args:
    timeout: Overall discovery budget in seconds, starting when discovery begins.
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
