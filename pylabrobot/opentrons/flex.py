import logging
import uuid
from typing import Any, Dict, List, Literal, Optional, Sequence, cast

from pylabrobot.opentrons.robot import OpentronsError, OpentronsRobot, PipetteInfo
from pylabrobot.resources import Container, Coordinate, Resource, TipSpot, Trash
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.tip import Tip

logger = logging.getLogger(__name__)

_OT_NAMESPACE = "opentrons"
_OT_VERSION = 1

# Flex-managed positioning flow-rate defaults (uL/s).
_DEFAULT_ASPIRATE_FLOW_RATE = 35.0
_DEFAULT_DISPENSE_FLOW_RATE = 57.0

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
    self._loaded_labware: Dict[str, str] = {}
    self._channel_tips: List[Optional[Tip]] = [None] * 8

  async def _model_setup(self) -> None:
    await self.home()

  def _require_pipette(self) -> PipetteInfo:
    """Return ``self.pipette``, narrowed to non-``None`` for mypy and callers.

    Raises if ``setup()`` (which discovers and loads the pipette) hasn't run.
    """
    assert self.pipette is not None, "No pipette loaded. Call setup() first."
    return self.pipette

  @staticmethod
  def _require_itemized_parent(item: Resource) -> ItemizedResource:
    """Return ``item.parent``, asserted to be an addressable-by-name container."""
    parent = item.parent
    assert isinstance(
      parent, ItemizedResource
    ), f"'{item.name}' has no itemized parent resource (rack/plate)."
    return parent

  async def pick_up_tips(
    self,
    tip_spots: List[TipSpot],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
  ) -> None:
    use_channels = use_channels if use_channels is not None else list(range(len(tip_spots)))
    pipette = self._require_pipette()
    rack = self._require_itemized_parent(tip_spots[0])
    labware_id = await self._ensure_labware_loaded(rack)
    well_name = rack.get_child_identifier(tip_spots[0])
    params: Dict[str, Any] = {
      "pipetteId": pipette.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
    }
    if offsets is not None and offsets[0] is not None:
      offset = offsets[0]
      params["wellLocation"] = {
        "origin": "top",
        "offset": {"x": offset.x, "y": offset.y, "z": offset.z},
      }
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
    pipette = self._require_pipette()
    target = tip_spots[0]
    if isinstance(target, Trash) or isinstance(target.parent, Trash):
      await self.execute_command(
        "moveToAddressableAreaForDropTip",
        {
          "pipetteId": pipette.pipette_id,
          "addressableAreaName": "movableTrashA3",
          "alternateDropLocation": True,
        },
      )
      await self.execute_command("dropTipInPlace", {"pipetteId": pipette.pipette_id})
    else:
      rack = self._require_itemized_parent(target)
      labware_id = await self._ensure_labware_loaded(rack)
      well_name = rack.get_child_identifier(target)
      params: Dict[str, Any] = {
        "pipetteId": pipette.pipette_id,
        "labwareId": labware_id,
        "wellName": well_name,
      }
      if offsets is not None and offsets[0] is not None:
        offset = offsets[0]
        params["wellLocation"] = {
          "origin": "top",
          "offset": {"x": offset.x, "y": offset.y, "z": offset.z},
        }
      await self.execute_command("dropTip", params)
    for ch, spot in zip(use_channels, tip_spots):
      tip = self._channel_tips[ch]
      if tip is not None and not isinstance(spot, Trash) and not isinstance(spot.parent, Trash):
        spot.tracker.add_tip(tip)
        spot.tracker.commit()
      self._channel_tips[ch] = None

  async def aspirate(
    self,
    resources: Sequence[Container],
    vols: List[float],
    use_channels: Optional[List[int]] = None,
    flow_rates: Optional[List[Optional[float]]] = None,
    offsets: Optional[List[Optional[Coordinate]]] = None,
    liquid_height: Optional[List[Optional[float]]] = None,
    blow_out_air_volume: Optional[List[Optional[float]]] = None,
    spread: Literal["wide", "tight", "custom"] = "wide",
  ) -> None:
    pipette = self._require_pipette()
    parent = self._require_itemized_parent(resources[0])
    labware_id = await self._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(resources[0])
    flow_rate = (flow_rates[0] if flow_rates else None) or _DEFAULT_ASPIRATE_FLOW_RATE
    params: Dict[str, Any] = {
      "pipetteId": pipette.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "volume": vols[0],
      "flowRate": flow_rate,
    }
    well_location = self._well_location(offsets, liquid_height)
    if well_location is not None:
      params["wellLocation"] = well_location
    await self.execute_command("aspirate", params)
    for well, vol in zip(resources, vols):
      well.tracker.remove_liquid(vol)
      well.tracker.commit()

  async def dispense(
    self,
    resources: Sequence[Container],
    vols: List[float],
    use_channels: Optional[List[int]] = None,
    flow_rates: Optional[List[Optional[float]]] = None,
    offsets: Optional[List[Optional[Coordinate]]] = None,
    liquid_height: Optional[List[Optional[float]]] = None,
    blow_out_air_volume: Optional[List[Optional[float]]] = None,
    spread: Literal["wide", "tight", "custom"] = "wide",
  ) -> None:
    pipette = self._require_pipette()
    parent = self._require_itemized_parent(resources[0])
    labware_id = await self._ensure_labware_loaded(parent)
    well_name = parent.get_child_identifier(resources[0])
    flow_rate = (flow_rates[0] if flow_rates else None) or _DEFAULT_DISPENSE_FLOW_RATE
    params: Dict[str, Any] = {
      "pipetteId": pipette.pipette_id,
      "labwareId": labware_id,
      "wellName": well_name,
      "volume": vols[0],
      "flowRate": flow_rate,
    }
    well_location = self._well_location(offsets, liquid_height)
    if well_location is not None:
      params["wellLocation"] = well_location
    await self.execute_command("dispense", params)
    for well, vol in zip(resources, vols):
      well.tracker.add_liquid(vol)
      well.tracker.commit()

  @staticmethod
  def _well_location(
    offsets: Optional[List[Optional[Coordinate]]],
    liquid_height: Optional[List[Optional[float]]],
  ) -> Optional[dict]:
    """Build the Flex ``wellLocation`` param from an offset and/or liquid height.

    Merges an explicit x/y/z offset with ``liquid_height`` (added to z); the
    origin is always ``"bottom"``. Returns ``None`` if neither is given.
    """
    offset = None
    if offsets is not None and offsets[0] is not None:
      o = offsets[0]
      offset = {"x": o.x, "y": o.y, "z": o.z}
    if liquid_height is not None and liquid_height[0] is not None:
      offset = offset or {"x": 0, "y": 0, "z": 0}
      offset["z"] += liquid_height[0]
    if offset is None:
      return None
    return {"origin": "bottom", "offset": offset}

  async def _ensure_labware_loaded(self, resource: Resource) -> str:
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

    result = await self.execute_command(
      "loadLabware",
      {
        "loadName": load_name,
        "location": {"slotName": slot},
        "namespace": _OT_NAMESPACE,
        "version": _OT_VERSION,
        "labwareId": labware_id,
        "displayName": name,
      },
    )
    labware_id = cast(str, result.get("result", {}).get("labwareId", labware_id))

    self._loaded_labware[name] = labware_id
    logger.info(
      "Loaded labware '%s' at slot %s -> ID: %s (OT: %s)",
      name,
      slot,
      labware_id,
      load_name,
    )
    return labware_id

  @staticmethod
  def _ot_load_name(resource: Resource) -> str:
    """Resolve a PLR resource to its Opentrons labware load name."""
    if hasattr(resource, "ot_load_name"):
      return cast(str, resource.ot_load_name)

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
