"""Wire-level facts shared by the Flex device, its heads and its gripper.

Small pieces that more than one of :mod:`~pylabrobot.opentrons.flex`,
:mod:`~pylabrobot.opentrons.flex_head` and
:mod:`~pylabrobot.opentrons.flex_gripper` needs, and that belong to none of
them: how a deck slot is spelled on the wire, which axes the robot addresses by
name, the software-version gate on the robot/* command family, and the notice
every not-yet-hardware-verified op logs. They live here so the always-present
device module does not have to reach into the optional gripper module (or the
heads) for them.
"""

from typing import Dict, FrozenSet, List, Optional, Tuple

from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import OFFLINE_API_VERSION

# Shared by the heads and the gripper so the notice reads identically
# everywhere; each module logs it through its own logger.
UNTESTED_HARDWARE_WARNING = (
  "%s.%s is coded but NOT YET VERIFIED on real Opentrons Flex hardware -- "
  "tested only against ChatterboxTransport/simulated transport. Verify behavior "
  "on real hardware before relying on it in a production protocol."
)

# The robot-server's DeckSlotName covers only the A1-D3 grid; the column-4
# staging slots are addressable areas and ride a different location key.
STAGING_SLOT_NAMES: FrozenSet[str] = frozenset({"A4", "B4", "C4", "D4"})

# Every axis the robot addresses by name: gantry, each mount's z and plunger,
# the gripper's z and jaw (extensionZ/extensionJaw), the 96-channel head's cam.
ROBOT_AXES: FrozenSet[str] = frozenset(
  {
    "x",
    "y",
    "leftZ",
    "rightZ",
    "leftPlunger",
    "rightPlunger",
    "extensionZ",
    "extensionJaw",
    "axis96ChannelCam",
  }
)

# The robot/* direct-motion command family (moveTo, moveAxesTo,
# moveAxesRelative, openGripperJaw, closeGripperJaw) landed in robot-server 8.2.0.
_ROBOT_COMMANDS_MIN_VERSION = "8.2.0"


def slot_wire_location(slot: str) -> Dict[str, str]:
  """The ``loadLabware``/``moveLabware`` location for a Flex slot name."""
  if slot in STAGING_SLOT_NAMES:
    return {"addressableAreaName": slot}
  return {"slotName": slot}


def _version_tuple(version: str) -> Tuple[int, ...]:
  """Parse a dotted robot-software version into comparable integers.

  Comparing these as strings puts "10.0.0" below "7.1.0", so the version gate
  compares numerically. Each dotted segment contributes its leading integer
  ("0-beta" -> 0); a segment with no leading digit stops the parse, and short
  results pad with zeros so "8.2" compares equal to "8.2.0".

  Raises:
    ValueError: If the version has no leading numeric segment at all.
  """
  parts: List[int] = []
  for part in version.split("."):
    digits = ""
    for char in part:
      if not char.isdigit():
        break
      digits += char
    if digits == "":
      break
    parts.append(int(digits))
  if not parts:
    raise ValueError(f"unparseable version string: {version!r}")
  while len(parts) < 3:
    parts.append(0)
  return tuple(parts)


def _require_robot_commands(command: str, api_version: Optional[str]) -> None:
  """Raise unless the robot's software supports the robot/* command family.

  ``api_version`` is the ``GET /health`` ``api_version`` the owning robot
  stored at setup (``flex.api_version``). Released builds report a plain
  numeric version and are gated against ``_ROBOT_COMMANDS_MIN_VERSION``.

  Two exemptions, both narrow. An UNTAGGED build reports "0.0.0.dev*" (the
  version an unreleased source checkout carries, including the simulated
  robot-server) and runs current code, so it passes -- but a build cut off a
  real tag reports that tag plus a dev suffix ("8.1.0.dev5"), which is gated
  on the tag like any release. ``ChatterboxTransport``'s offline "dry-run"
  sentinel passes too; it reaches no robot at all. Any other unparseable
  version raises rather than silently passing the gate.
  """
  if api_version is None:
    raise OpentronsError(
      "Robot version unknown",
      f"{command} requires setup() to have run, to read the robot's version.",
    )
  if api_version == OFFLINE_API_VERSION:
    return
  try:
    version = _version_tuple(api_version)
  except ValueError:
    raise OpentronsError(
      "Robot version unrecognized",
      f"{command} is gated on robot software {_ROBOT_COMMANDS_MIN_VERSION} or newer, but this "
      f"robot reports the unrecognized version {api_version!r}.",
    ) from None
  if version == (0, 0, 0) and "dev" in api_version:
    return
  if version < _version_tuple(_ROBOT_COMMANDS_MIN_VERSION):
    raise OpentronsError(
      "Robot software too old",
      f"{command} requires Opentrons robot software {_ROBOT_COMMANDS_MIN_VERSION} or newer, "
      f"but this robot reports {api_version}.",
    )
