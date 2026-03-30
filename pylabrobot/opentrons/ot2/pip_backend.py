"""OpentronsOT2PIPBackend -- per-mount protocol translation for single-channel pipetting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Union, cast

from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.resources import Coordinate, Tip
from pylabrobot.resources.tip_rack import TipRack

if TYPE_CHECKING:
  from pylabrobot.capabilities.capability import BackendParams
  from pylabrobot.resources.opentrons import OTDeck

from .driver import OpentronsOT2Driver

# https://github.com/Opentrons/opentrons/issues/14590
_OT_DECK_IS_ADDRESSABLE_AREA_VERSION = "7.1.0"

Mount = Literal["left", "right"]


class OpentronsOT2PIPBackend(PIPBackend):
  """PIP backend for a single OT-2 pipette mount (left or right).

  Each instance controls one physical pipette.  The OT-2 Device creates two of
  these -- one per mount -- so that ``ot2.left`` and ``ot2.right`` are independent
  PIP capabilities.

  Shared state (labware registry, OT name mapping) lives on the driver so that
  both mounts see the same registered tip racks.
  """

  pipette_name2volume = {
    "p10_single": 10, "p10_multi": 10,
    "p20_single_gen2": 20, "p20_multi_gen2": 20,
    "p50_single": 50, "p50_multi": 50,
    "p300_single": 300, "p300_multi": 300,
    "p300_single_gen2": 300, "p300_multi_gen2": 300,
    "p1000_single": 1000, "p1000_single_gen2": 1000,
    "p300_single_gen3": 300, "p1000_single_gen3": 1000,
  }

  def __init__(self, driver: OpentronsOT2Driver, mount: Mount):
    self._driver = driver
    self._mount: Mount = mount
    self.traversal_height = 120
    self._has_tip = False
    self._deck: Optional[OTDeck] = None

  def set_deck(self, deck: OTDeck):
    self._deck = deck

  @property
  def deck(self) -> OTDeck:
    assert self._deck is not None, "Deck not set"
    return self._deck

  @property
  def _pipette(self) -> Optional[Dict[str, str]]:
    return self._driver.left_pipette if self._mount == "left" else self._driver.right_pipette

  @property
  def _pipette_id(self) -> str:
    p = self._pipette
    assert p is not None, f"No pipette on {self._mount} mount"
    return cast(str, p["pipetteId"])

  @property
  def _pipette_name(self) -> str:
    p = self._pipette
    assert p is not None, f"No pipette on {self._mount} mount"
    return cast(str, p["name"])

  @property
  def _max_volume(self) -> float:
    return self.pipette_name2volume[self._pipette_name]

  async def _on_setup(self):
    self._has_tip = False

  async def _on_stop(self):
    self._has_tip = False

  @property
  def num_channels(self) -> int:
    return 1 if self._pipette is not None else 0

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    if channel_idx != 0 or self._pipette is None:
      return False
    vol = self._max_volume
    tv = tip.maximal_volume
    if vol == 20:
      return tv in {10, 20}
    if vol == 300:
      return tv in {200, 300}
    if vol == 1000:
      return tv in {1000}
    raise ValueError(f"Unknown channel volume: {vol}")

  # -- flow rates --

  def _get_default_aspiration_flow_rate(self) -> float:
    return {
      "p300_multi_gen2": 94, "p10_single": 5, "p10_multi": 5,
      "p50_single": 25, "p50_multi": 25, "p300_single": 150, "p300_multi": 150,
      "p1000_single": 500, "p20_single_gen2": 3.78, "p300_single_gen2": 46.43,
      "p1000_single_gen2": 137.35, "p20_multi_gen2": 7.6,
    }[self._pipette_name]

  def _get_default_dispense_flow_rate(self) -> float:
    return {
      "p300_multi_gen2": 94, "p10_single": 10, "p10_multi": 10,
      "p50_single": 50, "p50_multi": 50, "p300_single": 300, "p300_multi": 300,
      "p1000_single": 1000, "p20_single_gen2": 7.56, "p300_single_gen2": 92.86,
      "p1000_single_gen2": 274.7, "p20_multi_gen2": 7.6,
    }[self._pipette_name]

  # -- PIPBackend operations --

  async def pick_up_tips(
    self, ops: List[Pickup], use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    assert len(ops) == 1
    op = ops[0]
    assert not self._has_tip, f"{self._mount} mount already has a tip"
    offset_x, offset_y, offset_z = op.offset.x, op.offset.y, op.offset.z

    tip_rack = op.resource.parent
    assert isinstance(tip_rack, TipRack)
    self._driver.assign_tip_rack(tip_rack, op.tip)

    offset_z += op.tip.total_tip_length
    self._driver._pick_up_tip(
      labware_id=self._driver.get_ot_name(tip_rack.name),
      well_name=self._driver.get_ot_name(op.resource.name),
      pipette_id=self._pipette_id,
      offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
    )
    self._has_tip = True

  async def drop_tips(
    self, ops: List[TipDrop], use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    assert len(ops) == 1
    op = ops[0]
    assert self._has_tip, f"{self._mount} mount has no tip to drop"

    use_fixed_trash = (
      cast(str, self._driver.ot_api_version) >= _OT_DECK_IS_ADDRESSABLE_AREA_VERSION
      and op.resource.name == "trash"
    )
    if use_fixed_trash:
      labware_id = "fixedTrash"
    else:
      tip_rack = op.resource.parent
      assert isinstance(tip_rack, TipRack)
      self._driver.assign_tip_rack(tip_rack, op.tip)
      labware_id = self._driver.get_ot_name(tip_rack.name)

    offset_x, offset_y, offset_z = op.offset.x, op.offset.y, op.offset.z
    offset_z += 10

    if use_fixed_trash:
      self._driver._move_to_addressable_area_for_drop_tip(
        pipette_id=self._pipette_id, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
      )
      self._driver._drop_tip_in_place(pipette_id=self._pipette_id)
    else:
      self._driver._drop_tip(
        labware_id=labware_id, well_name=self._driver.get_ot_name(op.resource.name),
        pipette_id=self._pipette_id, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
      )
    self._has_tip = False

  async def aspirate(
    self, ops: List[Aspiration], use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    assert len(ops) == 1
    op = ops[0]
    assert self._has_tip, f"{self._mount} mount has no tip"
    flow_rate = op.flow_rate or self._get_default_aspiration_flow_rate()

    location = (
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset + Coordinate(z=op.liquid_height or 0)
    )
    self._move_to(location)

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        self._driver._aspirate_in_place(volume=op.mix.volume, flow_rate=op.mix.flow_rate, pipette_id=self._pipette_id)
        self._driver._dispense_in_place(volume=op.mix.volume, flow_rate=op.mix.flow_rate, pipette_id=self._pipette_id)

    self._driver._aspirate_in_place(volume=op.volume, flow_rate=flow_rate, pipette_id=self._pipette_id)
    self._move_to_traversal(op)

  async def dispense(
    self, ops: List[Dispense], use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    assert len(ops) == 1
    op = ops[0]
    assert self._has_tip, f"{self._mount} mount has no tip"
    flow_rate = op.flow_rate or self._get_default_dispense_flow_rate()

    location = (
      op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom")
      + op.offset + Coordinate(z=op.liquid_height or 0)
    )
    self._move_to(location)
    self._driver._dispense_in_place(volume=op.volume, flow_rate=flow_rate, pipette_id=self._pipette_id)

    if op.mix is not None:
      for _ in range(op.mix.repetitions):
        self._driver._aspirate_in_place(volume=op.mix.volume, flow_rate=op.mix.flow_rate, pipette_id=self._pipette_id)
        self._driver._dispense_in_place(volume=op.mix.volume, flow_rate=op.mix.flow_rate, pipette_id=self._pipette_id)

    self._move_to_traversal(op)

  # -- movement helpers --

  def _move_to(self, location: Coordinate):
    self._driver._move_arm(
      pipette_id=self._pipette_id, location_x=location.x, location_y=location.y,
      location_z=location.z, minimum_z_height=self.traversal_height,
    )

  def _move_to_traversal(self, op: Union[Aspiration, Dispense]):
    loc = op.resource.get_location_wrt(self.deck, "c", "c", "cavity_bottom") + op.offset
    loc.z = self.traversal_height
    self._move_to(loc)
