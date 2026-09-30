"""IPv4 mDNS service discovery."""

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class MDNSService:
  """A resolved service advertisement on the local network."""

  name: str
  host: str
  port: int
  properties: Dict[bytes, Optional[bytes]]


def discover_mdns(service_type: str, timeout: float = 5.0) -> List[MDNSService]:
  """Browse for services, then resolve their IPv4 addresses and TXT records.

  Args:
    service_type: Fully qualified service type, such as ``_http._tcp.local.``.
    timeout: Positive, finite browsing duration in seconds. Resolving discovered
      names takes up to one additional second per batch of services.

  Requires the optional ``zeroconf`` package. Closes the browser and sockets on
  completion or failure. Returns one entry per advertised IPv4
  address, sorted by service name and address.
  """
  if not math.isfinite(timeout) or timeout <= 0:
    raise ValueError("timeout must be finite and greater than zero")
  # Zeroconf must own its event loop even when called from a Jupyter notebook.
  with ThreadPoolExecutor(max_workers=1) as executor:
    return executor.submit(_browse_mdns, service_type, timeout).result()


def _browse_mdns(service_type: str, timeout: float) -> List[MDNSService]:
  """Run the blocking browser outside the caller's event loop."""
  try:
    from zeroconf import IPVersion, ServiceBrowser, ServiceStateChange, Zeroconf
  except ImportError as error:
    raise ImportError("mDNS discovery requires zeroconf: pip install zeroconf") from error

  names: Set[str] = set()

  def on_service_state_change(
    zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
  ) -> None:
    """Track advertised names on the browser's callback thread."""
    if state_change == ServiceStateChange.Removed:
      names.discard(name)
    else:
      names.add(name)

  def resolve(name: str) -> List[MDNSService]:
    """Resolve one advertisement without blocking other services."""
    info = zeroconf.get_service_info(service_type, name, timeout=1000)
    if info is None or info.port is None:
      return []
    return [
      MDNSService(
        name=name.removesuffix("." + service_type),
        host=address,
        port=info.port,
        properties=dict(info.properties),
      )
      for address in info.parsed_addresses(IPVersion.V4Only)
    ]

  zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
  try:
    browser = ServiceBrowser(zeroconf, service_type, handlers=[on_service_state_change])
    try:
      time.sleep(timeout)
    finally:
      browser.cancel()
    # The browser is joined before names are read, so no callbacks can mutate it.
    with ThreadPoolExecutor() as executor:
      results = list(executor.map(resolve, sorted(names)))
    return sorted(
      (service for services in results for service in services),
      key=lambda service: (service.name, service.host, service.port),
    )
  finally:
    zeroconf.close()
