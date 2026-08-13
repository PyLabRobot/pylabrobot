"""Wire-level facts shared by the Flex device, its heads and its gripper.

Small pieces that more than one of :mod:`~pylabrobot.opentrons.flex`,
:mod:`~pylabrobot.opentrons.flex_head` and
:mod:`~pylabrobot.opentrons.flex_gripper` needs, and that belong to none of
them: how a deck slot is spelled on the wire, and the notice every
not-yet-hardware-verified op logs. They live here so the always-present device
module does not have to reach into the optional gripper module (or the heads)
for them.
"""

from typing import Dict, FrozenSet

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


def slot_wire_location(slot: str) -> Dict[str, str]:
  """The ``loadLabware``/``moveLabware`` location for a Flex slot name."""
  if slot in STAGING_SLOT_NAMES:
    return {"addressableAreaName": slot}
  return {"slotName": slot}
