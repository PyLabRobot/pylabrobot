"""Simulator variants for device-free OT-2 testing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.device import Driver
from pylabrobot.resources import Coordinate

from .pip_backend import Mount, OpentronsOT2PIPBackend

if TYPE_CHECKING:
  from pylabrobot.capabilities.capability import BackendParams

logger = logging.getLogger(__name__)


class OpentronsOT2SimulatorDriver(Driver):
  """Simulator driver for the OT-2.  No ``ot_api`` dependency."""

  pipette_name2volume = OpentronsOT2PIPBackend.pipette_name2volume

  def __init__(
    self,
    left_pipette_name: Optional[str] = "p300_single_gen2",
    right_pipette_name: Optional[str] = "p20_single_gen2",
  ):
    super().__init__()
    pv = self.pipette_name2volume
    if left_pipette_name is not None and left_pipette_name not in pv:
      raise ValueError(f"Unknown left pipette: {left_pipette_name}")
    if right_pipette_name is not None and right_pipette_name not in pv:
      raise ValueError(f"Unknown right pipette: {right_pipette_name}")

    self._left_pipette_name = left_pipette_name
    self._right_pipette_name = right_pipette_name

    self.ot_api_version: Optional[str] = "7.0.1"
    self.left_pipette: Optional[Dict[str, str]] = None
    self.right_pipette: Optional[Dict[str, str]] = None
    self._positions: Dict[str, Coordinate] = {}
    self._tip_racks: Dict[str, int] = {}
    self._plr_name_to_load_name: Dict[str, str] = {}

  def _init_pipettes(self):
    self.left_pipette = (
      {"name": self._left_pipette_name, "pipetteId": "sim-left"}
      if self._left_pipette_name else None
    )
    self.right_pipette = (
      {"name": self._right_pipette_name, "pipetteId": "sim-right"}
      if self._right_pipette_name else None
    )
    self._positions = {}
    if self.left_pipette is not None:
      self._positions["sim-left"] = Coordinate.zero()
    if self.right_pipette is not None:
      self._positions["sim-right"] = Coordinate.zero()

  async def setup(self):
    self._init_pipettes()
    self._tip_racks = {}
    self._plr_name_to_load_name = {}
    logger.info("OpentronsOT2SimulatorDriver setup: left=%s, right=%s",
                self._left_pipette_name, self._right_pipette_name)

  async def stop(self):
    self.left_pipette = None
    self.right_pipette = None
    self._tip_racks = {}
    self._plr_name_to_load_name = {}
    logger.info("OpentronsOT2SimulatorDriver stopped.")

  async def home(self):
    logger.info("Homing (simulated).")

  async def list_connected_modules(self) -> List[dict]:
    return []

  def serialize(self) -> dict:
    return {**super().serialize(),
            "left_pipette_name": self._left_pipette_name,
            "right_pipette_name": self._right_pipette_name}

  # -- shared labware registry (no-op for simulator) --

  def get_ot_name(self, plr_resource_name: str) -> str:
    if plr_resource_name not in self._plr_name_to_load_name:
      import uuid
      self._plr_name_to_load_name[plr_resource_name] = uuid.uuid4().hex
    return self._plr_name_to_load_name[plr_resource_name]

  def assign_tip_rack(self, tip_rack, tip) -> None:
    if tip_rack.name in self._tip_racks:
      return
    self._tip_racks[tip_rack.name] = 0
    logger.info("assign_tip_rack %s (simulated)", tip_rack.name)

  def is_tip_rack_assigned(self, tip_rack_name: str) -> bool:
    return tip_rack_name in self._tip_racks

  # -- private wire methods (no-op / logging) --

  def _move_arm(self, pipette_id, location_x, location_y, location_z,
                minimum_z_height=None, speed=None, force_direct=False):
    loc = Coordinate(location_x, location_y, location_z)
    self._positions[pipette_id] = loc
    logger.info("Moved %s to %s (simulated).", pipette_id, loc)

  def _pick_up_tip(self, labware_id, well_name, pipette_id, offset_x, offset_y, offset_z):
    logger.info("_pick_up_tip %s well=%s pipette=%s (simulated)", labware_id, well_name, pipette_id)

  def _drop_tip(self, labware_id, well_name, pipette_id, offset_x, offset_y, offset_z):
    logger.info("_drop_tip %s well=%s pipette=%s (simulated)", labware_id, well_name, pipette_id)

  def _aspirate_in_place(self, volume, flow_rate, pipette_id):
    logger.info("_aspirate_in_place %.2f µL pipette=%s (simulated)", volume, pipette_id)

  def _dispense_in_place(self, volume, flow_rate, pipette_id):
    logger.info("_dispense_in_place %.2f µL pipette=%s (simulated)", volume, pipette_id)

  def _define_labware(self, definition):
    name = definition.get("metadata", {}).get("displayName", "unknown")
    return {"data": {"definitionUri": f"pylabrobot/{name}/1"}}

  def _add_labware(self, load_name, namespace, ot_location, version, labware_id, display_name):
    logger.info("_add_labware %s at slot %s (simulated)", display_name, ot_location)

  def _save_position(self, pipette_id):
    pos = self._positions.get(pipette_id, Coordinate.zero())
    return {"data": {"result": {"position": {"x": pos.x, "y": pos.y, "z": pos.z}}}}

  def _move_to_addressable_area_for_drop_tip(self, pipette_id, offset_x, offset_y, offset_z):
    logger.info("_move_to_addressable_area_for_drop_tip pipette=%s (simulated)", pipette_id)

  def _drop_tip_in_place(self, pipette_id):
    logger.info("_drop_tip_in_place pipette=%s (simulated)", pipette_id)


class OpentronsOT2SimulatorPIPBackend(OpentronsOT2PIPBackend):
  """Simulator PIP backend -- skips real labware registration."""

  def __init__(self, driver: OpentronsOT2SimulatorDriver, mount: Mount):
    super().__init__(driver, mount=mount)

  async def pick_up_tips(self, ops: List[Pickup], use_channels: List[int],
                         backend_params: Optional[BackendParams] = None):
    assert len(ops) == 1
    assert not self._has_tip, f"{self._mount} mount already has a tip"
    self._has_tip = True
    logger.info("Picked up tip from %s with %s mount", ops[0].resource.name, self._mount)

  async def drop_tips(self, ops: List[TipDrop], use_channels: List[int],
                      backend_params: Optional[BackendParams] = None):
    assert len(ops) == 1
    assert self._has_tip, f"{self._mount} mount has no tip to drop"
    self._has_tip = False
    logger.info("Dropped tip from %s mount", self._mount)

  async def aspirate(self, ops: List[Aspiration], use_channels: List[int],
                     backend_params: Optional[BackendParams] = None):
    assert len(ops) == 1
    logger.info("Aspirated %.2f µL from %s with %s mount", ops[0].volume, ops[0].resource.name, self._mount)

  async def dispense(self, ops: List[Dispense], use_channels: List[int],
                     backend_params: Optional[BackendParams] = None):
    assert len(ops) == 1
    logger.info("Dispensed %.2f µL to %s with %s mount", ops[0].volume, ops[0].resource.name, self._mount)
