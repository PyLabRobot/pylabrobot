"""Gripper sub-object for :class:`~pylabrobot.opentrons.flex.OpentronsFlex`.

The Flex gripper rides the extension mount and moves labware between deck
slots. Like the heads (:mod:`pylabrobot.opentrons.flex_head`), it is a
plain-class sub-object: it holds a back-reference to the owning
``OpentronsFlex`` and issues commands through the shared transport via
``self.flex._execute_command``. It is composed by ``OpentronsFlex.setup()``
when ``GET /instruments`` reports a gripper -- ``flex.gripper`` is ``None``
on a Flex without one.

The robot-server's ``moveLabware`` command is atomic (pick + travel + place
in one command), so a gripper move is a single wire call rather than a
pick/move/drop sequence. Grip geometry comes from the robot's own labware
definition for the loaded ``loadName``; PLR does not upload one.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.resources.resource import Resource

if TYPE_CHECKING:
  from pylabrobot.opentrons.flex import OpentronsFlex

logger = logging.getLogger(__name__)

# Gripper moves are slow physical operations (pick + travel + place), so give
# them far more headroom than the 30s ``_execute_command`` default.
_MOVE_LABWARE_TIMEOUT = 120.0

# The robot/* direct-motion command family (robot/moveTo,
# robot/openGripperJaw, robot/closeGripperJaw) landed in robot-server 8.2.0.
_ROBOT_COMMANDS_MIN_VERSION = "8.2.0"

# Grip-force bounds (Newtons) the robot-server accepts for closeGripperJaw.
_GRIPPER_MIN_FORCE = 2.0
_GRIPPER_MAX_FORCE = 30.0


def _version_tuple(version: str) -> Tuple[int, ...]:
  """Parse a dotted robot-software version into comparable integers.

  Comparing these as strings puts "10.0.0" below "7.1.0", so the version gate
  compares numerically. Each dotted segment contributes its leading integer
  ("0-beta" -> 0); a segment with no leading digit stops the parse. Only used
  to gate at coarse major.minor granularity, where the exact handling of a
  pre-release suffix does not change the outcome.
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
  return tuple(parts)


def _require_robot_commands(command: str, api_version: Optional[str]) -> None:
  """Raise unless the robot's software supports the robot/* command family.

  ``api_version`` is the ``GET /health`` ``api_version`` the owning robot
  stored at setup (``flex.api_version``). Released builds report a plain
  numeric version and are gated against ``_ROBOT_COMMANDS_MIN_VERSION``;
  dev/simulator builds ("0.0.0.dev0") and offline stand-in transports report
  non-release strings but run current code, so they pass.
  """
  if api_version is None:
    raise OpentronsError(
      "Robot version unknown",
      f"{command} requires setup() to have run, to read the robot's version.",
    )
  if "dev" in api_version:
    return
  version = _version_tuple(api_version)
  if version and version < _version_tuple(_ROBOT_COMMANDS_MIN_VERSION):
    raise OpentronsError(
      "Robot software too old",
      f"{command} requires Opentrons robot software {_ROBOT_COMMANDS_MIN_VERSION} or newer, "
      f"but this robot reports {api_version}.",
    )


class FlexGripper:
  """The Opentrons Flex gripper (extension mount).

  Constructed by ``OpentronsFlex._model_setup()`` when instrument discovery
  reports a gripper; access it as ``flex.gripper``.

  The Flex gripper has NO rotation capability (a hardware limitation, not a
  missing API): labware keeps its orientation through every gripper motion,
  so a plate cannot be re-oriented between slots.
  """

  def __init__(self, flex: "OpentronsFlex", gripper_model: str) -> None:
    self.flex = flex
    self.gripper_model = gripper_model

  async def move_labware(self, resource: Resource, to_slot: str) -> None:
    """Move ``resource`` from its current deck slot to ``to_slot`` with the gripper.

    Validates PLR-side first (resource on deck, destination a valid empty
    slot), then sends ONE atomic ``moveLabware`` command. On wire success the
    deck is re-parented to match; on wire failure the deck is left untouched
    and the error propagates.

    Args:
      resource: A resource currently placed on the deck.
      to_slot: Destination slot, e.g. ``"C2"`` (standard) or ``"B4"`` (staging).

    Raises:
      OpentronsError: If the resource is not on the deck, or ``to_slot`` is
        invalid or occupied. Raised before any wire command is sent.
    """
    deck = self.flex.deck
    name = getattr(resource, "name", str(resource))

    from_slot = deck.get_slot(resource)
    if from_slot is None:
      raise OpentronsError(
        "Resource not on deck",
        f"'{name}' is not on a deck slot. Use deck.assign_child_at_slot(resource, slot='C1').",
      )

    to_slot = to_slot.upper()
    try:
      occupant = deck.get_resource_at_slot(to_slot)
    except ValueError as e:
      raise OpentronsError("Invalid destination slot", str(e)) from e
    if occupant is not None:
      occupant_name = getattr(occupant, "name", str(occupant))
      raise OpentronsError(
        "Destination slot occupied",
        f"Slot {to_slot} is already occupied by '{occupant_name}'.",
      )

    labware_id = await self.flex._ensure_labware_loaded(resource)
    await self.flex._execute_command(
      "moveLabware",
      {
        "labwareId": labware_id,
        "newLocation": {"slotName": to_slot},
        "strategy": "usingGripper",
      },
      timeout=_MOVE_LABWARE_TIMEOUT,
    )

    deck.unassign_child_at_slot(from_slot)
    deck.assign_child_at_slot(resource, to_slot)
    logger.info("Gripper moved '%s' from %s to %s", name, from_slot, to_slot)

  async def ungrip(self) -> None:
    """Open the gripper jaw (homing it) to release any held labware.

    Recovery command: after an interrupted ``moveLabware`` the gripper may
    still be holding the labware; this releases it so the operator can
    recover the plate by hand.
    """
    await self.flex._execute_command("unsafe/ungripLabware", {})

  # --- robot/*: direct gripper motion and jaw control ---

  async def move_to(self, x: float, y: float, z: float, speed: Optional[float] = None) -> None:
    """Move the gripper to an absolute deck-frame position, in mm.

    Uses ``robot/moveTo`` with the extension mount rather than the
    ``robot/moveAxes*`` family: those infer the mount from the axis map, and
    the server's offset table has no gripper entry, so an ``extensionZ``
    target fails on the robot with ``KeyError: Mount.EXTENSION``. The gripper
    also has a lower z ceiling than the pipette mounts, so a z a pipette
    accepts can still be out of bounds. ``speed`` is in mm/s (robot default
    if None).
    """
    _require_robot_commands("robot/moveTo", self.flex.api_version)
    # The robot/* commands take snake_case params, unlike the rest of the API.
    params: Dict[str, Any] = {"mount": "extension", "destination": {"x": x, "y": y, "z": z}}
    if speed is not None:
      params["speed"] = speed
    await self.flex._execute_command("robot/moveTo", params)

  async def grip(self, force: Optional[float] = None) -> None:
    """Close the gripper jaw around whatever sits between its paddles.

    Args:
      force: Grip force in Newtons, between 2.0 and 30.0. The robot applies
        its own default when None. There is no jaw-width parameter; the jaw
        closes until it grips.

    Raises:
      OpentronsError: If ``force`` is outside the accepted range -- raised
        before any wire command is sent.
    """
    _require_robot_commands("robot/closeGripperJaw", self.flex.api_version)
    params: Dict[str, Any] = {}
    if force is not None:
      if not _GRIPPER_MIN_FORCE <= force <= _GRIPPER_MAX_FORCE:
        raise OpentronsError(
          "Invalid grip force",
          f"Grip force must be between {_GRIPPER_MIN_FORCE} and {_GRIPPER_MAX_FORCE} Newtons, "
          f"got {force}.",
        )
      params["force"] = force
    await self.flex._execute_command("robot/closeGripperJaw", params)

  async def open_jaw(self) -> None:
    """Open the gripper jaw -- the robot opens by HOMING the jaw to fully open.

    Releases anything held; there is no partial-open width parameter.
    """
    _require_robot_commands("robot/openGripperJaw", self.flex.api_version)
    await self.flex._execute_command("robot/openGripperJaw", {})
