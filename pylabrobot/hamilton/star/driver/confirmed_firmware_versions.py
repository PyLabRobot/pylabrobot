"""STAR firmware versions verified against hardware.

A STAR is not one controller. The master (``C0``) sits on an internal bus of independently
versioned boards, each of which answers ``RF`` with its own version string, and each of which is
replaced and updated on its own. So the versions are recorded per capability rather than as whole
stacks: two machines that differ only in their 96-head would otherwise have every other version
written down twice.

The consequence is that a combination is confirmed when each of its parts is, not as a set. That
is the right reading for boards that are swapped independently, and it is weaker than checking
whole stacks: a combination nobody has ever run can pass. Use it as "has this board ever been
driven", not "has this machine ever worked".

Verified readings
-----------------
* a STAR, 54 slots, with 8 pipetting channels, a 96-head, an iSWAP and autoload, 2026-05-27,
  read-only ``RF`` probe of every module.
"""

from typing import Dict, FrozenSet, Optional

CONFIRMED_FIRMWARE_VERSIONS: Dict[str, FrozenSet[str]] = {
  "master": frozenset({"7.6S 25 2021_11_05 (GRU C0)"}),
  "pipettes": frozenset({"4.0S j 2022-03-16"}),
  "x_arm": frozenset({"1.4S 2012-04-25"}),
  "head96": frozenset({"5.0S i 2021-10-22 (H0 XE167)"}),
  "iswap": frozenset({"4.1S 2011-12-19"}),
  "autoload": frozenset({"3.4S f 2017-01-09"}),
}
"""What each capability has been seen running, keyed by the capability it belongs to."""


def is_confirmed(capability: str, version: str) -> bool:
  """Whether a capability has been driven on this version before.

  Args:
    capability: which capability reported it, as keyed in `CONFIRMED_FIRMWARE_VERSIONS`.
    version: the version it reported, without the `rf` field marker.

  Returns:
    True if that version is recorded for that capability.
  """
  return version in CONFIRMED_FIRMWARE_VERSIONS.get(capability, frozenset())


def unconfirmed(versions: Dict[str, Optional[str]]) -> Dict[str, str]:
  """Which of the versions a machine reported have not been driven before.

  Args:
    versions: what each capability reported, keyed by capability. A capability that reported
      nothing is ignored.

  Returns:
    The capabilities whose version is not recorded, and what they reported.
  """
  return {
    capability: version
    for capability, version in versions.items()
    if version is not None and not is_confirmed(capability, version)
  }


def suggest_entry(capability: str, version: str) -> str:
  """Format a version as the line to add to `CONFIRMED_FIRMWARE_VERSIONS`.

  Args:
    capability: which capability reported it.
    version: the version it reported.

  Returns:
    The line, to paste into that capability's set once the machine has been driven successfully.
  """
  return f'  "{capability}": frozenset({{..., "{version}"}}),'
