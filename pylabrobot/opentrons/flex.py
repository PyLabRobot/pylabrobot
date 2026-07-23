import logging
import uuid
from typing import List, Optional

from pylabrobot.opentrons.robot import OpentronsError, OpentronsRobot
from pylabrobot.resources import Coordinate, TipSpot, Trash
from pylabrobot.resources.opentrons.flex_deck import FlexDeck

logger = logging.getLogger(__name__)

_OT_NAMESPACE = "opentrons"
_OT_VERSION = 1

_TIP_RACK_MAP = {
  "flex_96_tiprack_50ul": "opentrons_flex_96_tiprack_50ul",
  "flex_96_tiprack_200ul": "opentrons_flex_96_tiprack_200ul",
  "flex_96_tiprack_1000ul": "opentrons_flex_96_tiprack_1000ul",
  "flex_96_tiprack_20ul": "opentrons_flex_96_tiprack_20ul",
  "flex_96_filtertiprack_50ul": "opentrons_flex_96_filtertiprack_50ul",
  "flex_96_filtertiprack_200ul": "opentrons_flex_96_filtertiprack_200ul",
  "flex_96_filtertiprack_1000ul": "opentrons_flex_96_filtertiprack_1000ul",
  "flex_96_filtertiprack_20ul": "opentrons_flex_96_filtertiprack_20ul",
}


class OpentronsFlex(OpentronsRobot):
  """Opentrons Flex liquid handler (plain class, post-#1180 architecture).

  Tip ops commit state to the resource-tree trackers (``TipSpot.tracker``),
  not to a private per-device copy. Only which physical channel holds which
  tip is genuine device-local state (``self._channel_tips``).
  """

  def __init__(self, deck: FlexDeck, host: str, port: int = 31950) -> None:
    super().__init__(host=host, port=port)
    self.deck = deck
    self._loaded_labware: dict = {}
    self._channel_tips: List[Optional[object]] = [None] * 8

  async def _model_setup(self) -> None:
    await self.home()

  async def pick_up_tips(
    self,
    tip_spots: List[TipSpot],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
  ) -> None:
    use_channels = use_channels if use_channels is not None else list(range(len(tip_spots)))
    labware_id = await self._ensure_labware_loaded(tip_spots[0].parent)
    well_name = tip_spots[0].parent.get_child_identifier(tip_spots[0])
    params = {
      "pipetteId": self.pipette.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
    }
    if offsets is not None and offsets[0] is not None:
      offset = offsets[0]
      params["wellLocation"] = {"origin": "top", "offset": {"x": offset.x, "y": offset.y, "z": offset.z}}
    await self.execute_command("pickUpTip", params)
    for ch, spot in zip(use_channels, tip_spots):
      tip = spot.get_tip()
      spot.tracker.remove_tip()
      spot.tracker.commit()
      self._channel_tips[ch] = tip

  async def drop_tips(
    self,
    tip_spots: List[TipSpot],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
  ) -> None:
    use_channels = use_channels if use_channels is not None else list(range(len(tip_spots)))
    target = tip_spots[0]
    if isinstance(target, Trash) or isinstance(target.parent, Trash):
      await self.execute_command("moveToAddressableAreaForDropTip", {
        "pipetteId": self.pipette.pipette_id,
        "addressableAreaName": "movableTrashA3",
        "alternateDropLocation": True,
      })
      await self.execute_command("dropTipInPlace", {"pipetteId": self.pipette.pipette_id})
    else:
      labware_id = await self._ensure_labware_loaded(target.parent)
      well_name = target.parent.get_child_identifier(target)
      params = {
        "pipetteId": self.pipette.pipette_id,
        "labwareId": labware_id,
        "wellName": well_name,
      }
      if offsets is not None and offsets[0] is not None:
        offset = offsets[0]
        params["wellLocation"] = {"origin": "top", "offset": {"x": offset.x, "y": offset.y, "z": offset.z}}
      await self.execute_command("dropTip", params)
    for ch, spot in zip(use_channels, tip_spots):
      tip = self._channel_tips[ch]
      if tip is not None and not isinstance(spot, Trash) and not isinstance(spot.parent, Trash):
        spot.tracker.add_tip(tip)
        spot.tracker.commit()
      self._channel_tips[ch] = None

  async def _ensure_labware_loaded(self, resource) -> str:
    """Load labware into the Flex run if not already loaded."""
    name = getattr(resource, "name", str(resource))
    if name in self._loaded_labware:
      return self._loaded_labware[name]

    slot = self.deck.get_slot(resource)
    if slot is None:
      raise OpentronsError(
        "Resource not on deck",
        f"'{name}' is not on a deck slot. Use deck.assign_child_at_slot(resource, slot='C1').",
      )

    load_name = self._ot_load_name(resource)
    labware_id = uuid.uuid4().hex[:12]

    result = await self.execute_command("loadLabware", {
      "loadName": load_name,
      "location": {"slotName": slot},
      "namespace": _OT_NAMESPACE,
      "version": _OT_VERSION,
      "labwareId": labware_id,
      "displayName": name,
    })
    labware_id = result.get("result", {}).get("labwareId", labware_id)

    self._loaded_labware[name] = labware_id
    logger.info(
      "Loaded labware '%s' at slot %s -> ID: %s (OT: %s)",
      name, slot, labware_id, load_name,
    )
    return labware_id

  @staticmethod
  def _ot_load_name(resource) -> str:
    """Resolve a PLR resource to its Opentrons labware load name."""
    if hasattr(resource, "ot_load_name"):
      return resource.ot_load_name

    name_lower = getattr(resource, "name", "").lower()

    for key, ot_name in _TIP_RACK_MAP.items():
      if key in name_lower:
        return ot_name

    if name_lower.startswith("opentrons_"):
      return name_lower

    raise OpentronsError(
      "Cannot determine Opentrons load name",
      f"'{name_lower}' — set resource.ot_load_name = 'opentrons_flex_96_tiprack_50ul' "
      f"or use a standard Flex labware name.",
    )
