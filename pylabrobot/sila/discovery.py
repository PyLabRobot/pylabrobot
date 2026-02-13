"""SiLA 2 device discovery via mDNS.

SiLA 2 instruments advertise themselves as ``_sila._tcp.local.`` services.
This module provides a simple way to find them on the local network.

Example:
  >>> from pylabrobot.sila import discover
  >>> devices = await discover()
  >>> for d in devices:
  ...     print(d.host, d.port, d.name)
"""

import asyncio
import dataclasses
import socket
from typing import List

from zeroconf import ServiceBrowser, Zeroconf


@dataclasses.dataclass(frozen=True)
class SiLADevice:
  """A SiLA 2 device found on the network."""

  host: str
  port: int
  name: str

  def __str__(self) -> str:
    return f"{self.name} @ {self.host}:{self.port}"


SILA_MDNS_TYPE = "_sila._tcp.local."


async def discover(timeout: float = 5.0) -> List[SiLADevice]:
  """Discover SiLA 2 devices on the local network via mDNS.

  Args:
    timeout: How long to listen for responses, in seconds.

  Returns:
    List of discovered devices.

  """

  devices: List[SiLADevice] = []

  class _Listener:
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      info = zc.get_service_info(type_, name)
      if info and info.addresses:
        host = socket.inet_ntoa(info.addresses[0])
        devices.append(SiLADevice(host=host, port=info.port, name=info.server))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      pass

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
      pass

  zc = Zeroconf()
  try:
    ServiceBrowser(zc, SILA_MDNS_TYPE, _Listener())
    await asyncio.sleep(timeout)
  finally:
    zc.close()

  return devices


if __name__ == "__main__":
  import argparse

  parser = argparse.ArgumentParser(description="Discover SiLA 2 devices on the local network")
  parser.add_argument(
    "-t", "--timeout", type=float, default=5.0, help="discovery timeout in seconds (default: 5)"
  )
  args = parser.parse_args()

  devices = asyncio.run(discover(args.timeout))
  if not devices:
    print("No SiLA 2 devices found.")
  else:
    for d in devices:
      print(f"{d.host}\t{d.port}\t{d.name}")
