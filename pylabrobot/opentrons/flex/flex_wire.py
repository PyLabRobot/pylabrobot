"""Flex axis names, deck locations, and robot command version requirements."""

from typing import FrozenSet, Optional

from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.run import (
  _version_at_least,
  _version_tuple,
  slot_wire_location,  # noqa: F401
)

OFFLINE_API_VERSION = "dry-run"

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


def _require_robot_commands(command: str, api_version: Optional[str]) -> None:
  """Raise unless the robot's software supports the robot/* command family.

  ``api_version`` is the ``GET /health`` ``api_version`` the owning robot
  stored at setup (``flex.api_version``). Released builds report a plain
  numeric version and are gated against ``_ROBOT_COMMANDS_MIN_VERSION``.

  Two exemptions, both narrow. An UNTAGGED build reports "0.0.0.dev*" (the
  version an unreleased source checkout carries, including the simulated
  robot-server) and runs current code, so it passes -- but a build cut off a
  real tag reports that tag plus a dev suffix ("8.1.0.dev5"), which is gated
  on the tag like any release. The offline tests' "dry-run" sentinel passes
  too. Any other unparseable version raises rather than silently passing
  the gate.
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
  if not any(version) and "dev" in api_version:
    return
  if not _version_at_least(api_version, _ROBOT_COMMANDS_MIN_VERSION):
    raise OpentronsError(
      "Robot software too old",
      f"{command} requires Opentrons robot software {_ROBOT_COMMANDS_MIN_VERSION} or newer, "
      f"but this robot reports {api_version}.",
    )
