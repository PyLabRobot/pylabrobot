"""Find Opentrons robots from their live mDNS advertisements."""

from typing import Optional, Tuple

from pylabrobot.io.mdns import discover_mdns


def _find_robot_ip(
  models: Tuple[bytes, ...], label: str, helper: str, name: Optional[str], timeout: float
) -> str:
  """Find one unambiguous robot address matching its advertised model."""
  matches = {
    (service.name, service.host)
    for service in discover_mdns("_http._tcp.local.", timeout=timeout)
    if service.port == 31950
    and service.properties.get(b"robotModel") in models
    and (name is None or service.name == name)
  }
  if not matches:
    target = "" if name is None else f" named {name!r}"
    raise RuntimeError(f"No {label}{target} found on the local network.")
  addresses = {host for _, host in matches}
  if len(addresses) != 1:
    candidates = ", ".join(f"{robot_name} ({host})" for robot_name, host in sorted(matches))
    raise RuntimeError(
      f"Multiple {label} addresses found: {candidates}. "
      f"Select a robot with {helper}(name=...), or supply its host explicitly."
    )
  return addresses.pop()


def find_flex_ip(name: Optional[str] = None, timeout: float = 5.0) -> str:
  """Find one Flex via live mDNS; optionally select its exact advertised name.

  Requires zeroconf. Browses for ``timeout`` seconds plus address resolution.
  Raises RuntimeError for missing or ambiguous matches. Starts no robot run.
  """
  return _find_robot_ip((b"OT-3", b"OT-3 Standard"), "Flex", "find_flex_ip", name, timeout)


def find_ot2_ip(name: Optional[str] = None, timeout: float = 5.0) -> str:
  """Find one OT-2 via live mDNS with the same selection rules as Flex."""
  return _find_robot_ip((b"OT-2", b"OT-2 Standard"), "OT-2", "find_ot2_ip", name, timeout)
