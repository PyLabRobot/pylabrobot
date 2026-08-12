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
from typing import TYPE_CHECKING

from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.resources.resource import Resource

if TYPE_CHECKING:
  from pylabrobot.opentrons.flex import OpentronsFlex

logger = logging.getLogger(__name__)

# Gripper moves are slow physical operations (pick + travel + place), so give
# them far more headroom than the 30s ``_execute_command`` default.
_MOVE_LABWARE_TIMEOUT = 120.0


class FlexGripper:
  """The Opentrons Flex gripper (extension mount).

  Constructed by ``OpentronsFlex._model_setup()`` when instrument discovery
  reports a gripper; access it as ``flex.gripper``.
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
