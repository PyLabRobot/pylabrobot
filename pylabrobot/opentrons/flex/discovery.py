"""Find Flex robots from their live mDNS advertisements."""

from typing import Optional

from pylabrobot.io.mdns import discover_mdns


def find_flex_ip(name: Optional[str] = None, timeout: float = 5.0) -> str:
  """Return the IPv4 address of a Flex on the local network.

  Browses HTTP advertisements on port 31950 with an OT-3 model. Requires
  ``pip install 'pylabrobot[opentrons]'``. This synchronous helper starts no
  robot run and moves no hardware. The address comes from an advertisement;
  call ``await flex.connect()`` to check the robot's HTTP health endpoint.

  Args:
    name: Exact advertised robot name, useful when multiple robots are present.
    timeout: Positive, finite browsing duration in seconds. Address resolution
      may take additional time (up to one second per batch of services).

  Raises:
    RuntimeError: No matching Flex, or more than one matching address was found.
    ValueError: The timeout is not positive and finite.
    ImportError: The optional zeroconf dependency is not installed.

  Example:
    >>> flex = Flex(FlexDeck(), host=find_flex_ip())
  """
  matches = {
    (service.name, service.host)
    for service in discover_mdns("_http._tcp.local.", timeout=timeout)
    if service.port == 31950
    and service.properties.get(b"robotModel") in (b"OT-3", b"OT-3 Standard")
    and (name is None or service.name == name)
  }
  if not matches:
    target = "" if name is None else f" named {name!r}"
    raise RuntimeError(f"No Flex{target} found on the local network.")
  addresses = {host for _, host in matches}
  if len(addresses) != 1:
    candidates = ", ".join(f"{robot_name} ({host})" for robot_name, host in sorted(matches))
    raise RuntimeError(
      f"Multiple Flex addresses found: {candidates}. "
      "Select a robot with find_flex_ip(name=...), or supply its host explicitly."
    )
  return addresses.pop()
