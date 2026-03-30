"""OpentronsOT2PIPBackend -- protocol translation for single-channel pipetting."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union, cast

from pylabrobot import utils
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.resources import Coordinate, Tip
from pylabrobot.resources.opentrons import OTDeck
from pylabrobot.resources.tip_rack import TipRack

if TYPE_CHECKING:
  from pylabrobot.capabilities.capability import BackendParams

from .driver import OpentronsOT2Driver

# https://github.com/Opentrons/opentrons/issues/14590
_OT_DECK_IS_ADDRESSABLE_AREA_VERSION = "7.1.0"


class OpentronsOT2PIPBackend(PIPBackend):
  """Translates PIP capability operations into OT-2 driver commands.

  All OT-2-specific protocol encoding (labware definition, pipette selection,
  name mapping, flow rate defaults, offset calculation) lives here.
  """

  pipette_name2volume = {
    "p10_single": 10,
    "p10_multi": 10,
    "p20_single_gen2": 20,
    "p20_multi_gen2": 20,
    "p50_single": 50,
    "p50_multi": 50,
    "p300_single": 300,
    "p300_multi": 300,
    "p300_single_gen2": 300,
    "p300_multi_gen2": 300,
    "p1000_single": 1000,
    "p1000_single_gen2": 1000,
    "p300_single_gen3": 300,
    "p1000_single_gen3": 1000,
  }

  def __init__(self, driver: OpentronsOT2Driver):
    self._driver = driver
    self.traversal_height = 120
    self._tip_racks: Dict[str, int] = {}
    self._plr_name_to_load_name: Dict[str, str] = {}
    self.left_pipette_has_tip = False
    self.right_pipette_has_tip = False
    self._deck: Optional[OTDeck] = None

  def set_deck(self, deck: OTDeck):
    self._deck = deck

  @property
  def deck(self) -> OTDeck:
    assert self._deck is not None, "Deck not set"
    return self._deck

  async def _on_setup(self):
    self.left_pipette_has_tip = False
    self.right_pipette_has_tip = False
    self._tip_racks = {}
    self._plr_name_to_load_name = {}

  async def _on_stop(self):
    self._plr_name_to_load_name = {}
    self._tip_racks = {}
    self.left_pipette_has_tip = False
    self.right_pipette_has_tip = False

  @property
  def num_channels(self) -> int:
    return len(
      [p for p in [self._driver.left_pipette, self._driver.right_pipette] if p is not None]
    )

  # -- name mapping --

  def get_ot_name(self, plr_resource_name: str) -> str:
    """Map a PLR resource name to an OT-compatible name (^[a-z0-9._]+$)."""
    if plr_resource_name not in self._plr_name_to_load_name:
      self._plr_name_to_load_name[plr_resource_name] = uuid.uuid4().hex
    return self._plr_name_to_load_name[plr_resource_name]

  # -- pipette selection --

  def select_tip_pipette(self, tip: Tip, with_tip: bool) -> Optional[str]:
    if self.can_pick_up_tip(0, tip) and with_tip == self.left_pipette_has_tip:
      assert self._driver.left_pipette is not None
      return cast(str, self._driver.left_pipette["pipetteId"])
    if self.can_pick_up_tip(1, tip) and with_tip == self.right_pipette_has_tip:
      assert self._driver.right_pipette is not None
      return cast(str, self._driver.right_pipette["pipetteId"])
    return None

  def select_liquid_pipette(self, volume: float) -> Optional[str]:
    if self._driver.left_pipette is not None:
      left_volume = self.pipette_name2volume[self._driver.left_pipette["name"]]
      if left_volume >= volume and self.left_pipette_has_tip:
        return cast(str, self._driver.left_pipette["pipetteId"])
    if self._driver.right_pipette is not None:
      right_volume = self.pipette_name2volume[self._driver.right_pipette["name"]]
      if right_volume >= volume and self.right_pipette_has_tip:
        return cast(str, self._driver.right_pipette["pipetteId"])
    return None

  def get_pipette_name(self, pipette_id: str) -> str:
    if self._driver.left_pipette is not None and pipette_id == self._driver.left_pipette["pipetteId"]:
      return cast(str, self._driver.left_pipette["name"])
    if self._driver.right_pipette is not None and pipette_id == self._driver.right_pipette["pipetteId"]:
      return cast(str, self._driver.right_pipette["name"])
    raise ValueError(f"Unknown pipette id: {pipette_id}")

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    def supports_tip(channel_vol: float, tip_vol: float) -> bool:
      if channel_vol == 20:
        return tip_vol in {10, 20}
      if channel_vol == 300:
        return tip_vol in {200, 300}
      if channel_vol == 1000:
        return tip_vol in {1000}
      raise ValueError(f"Unknown channel volume: {channel_vol}")

    if channel_idx == 0:
      if self._driver.left_pipette is None:
        return False
      left_volume = self.pipette_name2volume[self._driver.left_pipette["name"]]
      return supports_tip(left_volume, tip.maximal_volume)
    if channel_idx == 1:
      if self._driver.right_pipette is None:
        return False
      right_volume = self.pipette_name2volume[self._driver.right_pipette["name"]]
      return supports_tip(right_volume, tip.maximal_volume)
    return False

  # -- tip state --

  def _set_tip_state(self, pipette_id: str, has_tip: bool):
    if self._driver.left_pipette is not None and pipette_id == self._driver.left_pipette["pipetteId"]:
      self.left_pipette_has_tip = has_tip
      return
    if self._driver.right_pipette is not None and pipette_id == self._driver.right_pipette["pipetteId"]:
      self.right_pipette_has_tip = has_tip
      return
    raise ValueError(f"Unknown or unconfigured pipette_id {pipette_id!r} in _set_tip_state.")

  def _get_pickup_pipette(self, ops: List[Pickup]) -> str:
    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]
    assert op.resource.parent is not None, "must not be a floating resource"
    pipette_id = self.select_tip_pipette(op.tip, with_tip=False)
    if not pipette_id:
      from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
      raise ChannelizedError("No pipette channel of right type with no tip available.")
    return pipette_id

  def _get_drop_pipette(self, ops: List[TipDrop]) -> str:
    assert len(ops) == 1, "only one channel supported for now"
    op = ops[0]
    assert op.resource.parent is not None, "must not be a floating resource"
    pipette_id = self.select_tip_pipette(op.tip, with_tip=True)
    if not pipette_id:
      from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
      raise ChannelizedError("No pipette channel of right type with tip available.")
    return pipette_id

  def _get_liquid_pipette(self, ops: Union[List[Aspiration], List[Dispense]]) -> str:
    assert len(ops) == 1, "only one channel supported for now"
    pipette_id = self.select_liquid_pipette(ops[0].volume)
    if pipette_id is None:
      from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
      raise ChannelizedError("No pipette channel of right type with tip available.")
    return pipette_id

  # -- labware assignment --

  async def _assign_tip_rack(self, tip_rack: TipRack, tip: Tip):
    ot_slot_size_y = 86
    lw = {
      "schemaVersion": 2,
      "version": 1,
      "namespace": "pylabrobot",
      "metadata": {
        "displayName": self.get_ot_name(tip_rack.name),
        "displayCategory": "tipRack",
        "displayVolumeUnits": "µL",
      },
      "brand": {"brand": "unknown"},
      "parameters": {
        "format": "96Standard",
        "isTiprack": True,
        "tipLength": tip.total_tip_length,
        "tipOverlap": tip.fitting_depth,
        "loadName": self.get_ot_name(tip_rack.name),
        "isMagneticModuleCompatible": False,
      },
      "ordering": utils.reshape_2d(
        [self.get_ot_name(tip_spot.name) for tip_spot in tip_rack.get_all_items()],
        (tip_rack.num_items_x, tip_rack.num_items_y),
      ),
      "cornerOffsetFromSlot": {
        "x": 0,
        "y": ot_slot_size_y - tip_rack.get_absolute_size_y(),
        "z": 0,
      },
      "dimensions": {
        "xDimension": tip_rack.get_absolute_size_x(),
        "yDimension": tip_rack.get_absolute_size_y(),
        "zDimension": tip_rack.get_absolute_size_z(),
      },
      "wells": {
        self.get_ot_name(child.name): {
          "depth": child.get_absolute_size_z(),
          "x": cast(Coordinate, child.location).x + child.get_absolute_size_x() / 2,
          "y": cast(Coordinate, child.location).y + child.get_absolute_size_y() / 2,
          "z": cast(Coordinate, child.location).z,
          "shape": "circular",
          "diameter": child.get_absolute_size_x(),
          "totalLiquidVolume": tip.maximal_volume,
        }
        for child in tip_rack.children
      },
      "groups": [
        {
          "wells": [self.get_ot_name(tip_spot.name) for tip_spot in tip_rack.get_all_items()],
          "metadata": {
            "displayName": None,
            "displayCategory": "tipRack",
            "wellBottomShape": "flat",
          },
        }
      ],
    }

    data = self._driver.define_labware(lw)
    namespace, definition, version = data["data"]["definitionUri"].split("/")

    labware_uuid = self.get_ot_name(tip_rack.name)
    deck = tip_rack.parent
    assert isinstance(deck, OTDeck)
    slot = deck.get_slot(tip_rack)
    assert slot is not None, "tip rack must be on deck"

    self._driver.add_labware(
      load_name=definition, namespace=namespace, ot_location=slot,
      version=version, labware_id=labware_uuid,
      display_name=self.get_ot_name(tip_rack.name),
    )
    self._tip_racks[tip_rack.name] = slot

  # -- flow rates --

  def _get_default_aspiration_flow_rate(self, pipette_name: str) -> float:
    return {
      "p300_multi_gen2": 94, "p10_single": 5, "p10_multi": 5,
      "p50_single": 25, "p50_multi": 25, "p300_single": 150, "p300_multi": 150,
      "p1000_single": 500, "p20_single_gen2": 3.78, "p300_single_gen2": 46.43,
      "p1000_single_gen2": 137.35, "p20_multi_gen2": 7.6,
    }[pipette_name]

  def _get_default_dispense_flow_rate(self, pipette_name: str) -> float:
    return {
      "p300_multi_gen2": 94, "p10_single": 10, "p10_multi": 10,
      "p50_single": 50, "p50_multi": 50, "p300_single": 300, "p300_multi": 300,
      "p1000_single": 1000, "p20_single_gen2": 7.56, "p300_single_gen2": 92.86,
      "p1000_single_gen2": 274.7, "p20_multi_gen2": 7.6,
    }[pipette_name]

  # -- PIPBackend operations --

  async def pick_up_tips(
    self, ops: List[Pickup], use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    pipette_id = self._get_pickup_pipette(ops)
    op = ops[0]
    offset_x, offset_y, offset_z = op.offset.x, op.offset.y, op.offset.z

    tip_rack = op.resource.parent
    assert isinstance(tip_rack, TipRack), "TipSpot's parent must be a TipRack."
    if tip_rack.name not in self._tip_racks:
      await self._assign_tip_rack(tip_rack, op.tip)

    offset_z += op.tip.total_tip_length

    self._driver.pick_up_tip_raw(
      labware_id=self.get_ot_name(tip_rack.name),
      well_name=self.get_ot_name(op.resource.name),
      pipette_id=pipette_id,
      offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
    )
    self._set_tip_state(pipette_id, True)

  async def drop_tips(
    self, ops: List[TipDrop], use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    pipette_id = self._get_drop_pipette(ops)
    op = ops[0]

    use_fixed_trash = (
      cast(str, self._driver.ot_api_version) >= _OT_DECK_IS_ADDRESSABLE_AREA_VERSION
      and op.resource.name == "trash"
    )
    if use_fixed_trash:
      labware_id = "fixedTrash"
    else:
      tip_rack = op.resource.parent
      assert isinstance(tip_rack, TipRack), "TipSpot's parent must be a TipRack."
      if tip_rack.name not in self._tip_racks:
        await self._assign_tip_rack(tip_rack, op.tip)
      labware_id = self.get_ot_name(tip_rack.name)

    offset_x, offset_y, offset_z = op.offset.x, op.offset.y, op.offset.z
    offset_z += 10  # ad-hoc offset for smoother drop

    if use_fixed_trash:
      self._driver.move_to_addressable_area_for_drop_tip(
        pipette_id=pipette_id, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
      )
      self._driver.drop_tip_in_place(pipette_id=pipette_id)
    else:
      self._driver.drop_tip_raw(
        labware_id=labware_id, well_name=self.get_ot_name(op.resource.name),
        pipette_id=pipette_id, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
      )
    self._set_tip_state(pipette_id, False)

  async def aspirate(
    self, ops: List[Aspiration], use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    pipette_id = self._get_liquid_pipette(ops)
    op = ops[0]
    volume = op.volume
    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_aspiration_flow_rate(pipette_name)

    location = (
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset + Coordinate(z=op.liquid_height or 0)
    )
    await self._move_pipette_head(
      location=location, minimum_z_height=self.traversal_height, pipette_id=pipette_id,
    )

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        self._driver.aspirate_in_place(volume=op.mix.volume, flow_rate=op.mix.flow_rate, pipette_id=pipette_id)
        self._driver.dispense_in_place(volume=op.mix.volume, flow_rate=op.mix.flow_rate, pipette_id=pipette_id)

    self._driver.aspirate_in_place(volume=volume, flow_rate=flow_rate, pipette_id=pipette_id)

    traversal_location = op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom") + op.offset
    traversal_location.z = self.traversal_height
    await self._move_pipette_head(
      location=traversal_location, minimum_z_height=self.traversal_height, pipette_id=pipette_id,
    )

  async def dispense(
    self, ops: List[Dispense], use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    pipette_id = self._get_liquid_pipette(ops)
    op = ops[0]
    volume = op.volume
    pipette_name = self.get_pipette_name(pipette_id)
    flow_rate = op.flow_rate or self._get_default_dispense_flow_rate(pipette_name)

    location = (
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset + Coordinate(z=op.liquid_height or 0)
    )
    await self._move_pipette_head(
      location=location, minimum_z_height=self.traversal_height, pipette_id=pipette_id,
    )

    self._driver.dispense_in_place(volume=volume, flow_rate=flow_rate, pipette_id=pipette_id)

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        self._driver.aspirate_in_place(volume=op.mix.volume, flow_rate=op.mix.flow_rate, pipette_id=pipette_id)
        self._driver.dispense_in_place(volume=op.mix.volume, flow_rate=op.mix.flow_rate, pipette_id=pipette_id)

    traversal_location = op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom") + op.offset
    traversal_location.z = self.traversal_height
    await self._move_pipette_head(
      location=traversal_location, minimum_z_height=self.traversal_height, pipette_id=pipette_id,
    )

  # -- channel movement --

  def _pipette_id_for_channel(self, channel: int) -> str:
    pipettes = []
    if self._driver.left_pipette is not None:
      pipettes.append(self._driver.left_pipette["pipetteId"])
    if self._driver.right_pipette is not None:
      pipettes.append(self._driver.right_pipette["pipetteId"])
    if channel < 0 or channel >= len(pipettes):
      from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
      raise ChannelizedError(f"Channel {channel} not available on this OT-2 setup.")
    return pipettes[channel]

  def _current_channel_position(self, channel: int) -> Tuple[str, Coordinate]:
    pipette_id = self._pipette_id_for_channel(channel)
    try:
      res = self._driver.save_position(pipette_id=pipette_id)
      pos = res["data"]["result"]["position"]
      current = Coordinate(pos["x"], pos["y"], pos["z"])
    except Exception as exc:
      raise RuntimeError("Failed to query current pipette position") from exc
    return pipette_id, current

  async def _move_pipette_head(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    pipette_id: Optional[str] = None,
    force_direct: bool = False,
  ):
    if self._driver.left_pipette is not None and pipette_id == "left":
      pipette_id = self._driver.left_pipette["pipetteId"]
    elif self._driver.right_pipette is not None and pipette_id == "right":
      pipette_id = self._driver.right_pipette["pipetteId"]

    if pipette_id is None:
      raise ValueError("No pipette id given or left/right pipette not available.")

    self._driver.move_arm(
      pipette_id=pipette_id, location_x=location.x, location_y=location.y,
      location_z=location.z, minimum_z_height=minimum_z_height,
      speed=speed, force_direct=force_direct,
    )
