"""OpentronsOT2Driver -- owns the ot_api HTTP connection and device-level ops."""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional, cast

from pylabrobot import utils
from pylabrobot.device import Driver
from pylabrobot.resources import Coordinate
from pylabrobot.resources.opentrons import OTDeck
from pylabrobot.resources.tip_rack import TipRack

try:
  import ot_api
  import ot_api.requestor as _req

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e


class OpentronsOT2Driver(Driver):
  """Driver for the Opentrons OT-2 liquid handling robot.

  Owns the HTTP connection (via ``ot_api``), run lifecycle, pipette
  discovery, homing, module queries, and shared labware registry.
  """

  def __init__(self, host: str, port: int = 31950):
    super().__init__()

    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
      )

    self.host = host
    self.port = port

    ot_api.set_host(host)
    ot_api.set_port(port)

    self.ot_api_version: Optional[str] = None
    self.left_pipette: Optional[Dict[str, str]] = None
    self.right_pipette: Optional[Dict[str, str]] = None
    self._run_id: Optional[str] = None
    self._tip_racks: Dict[str, int] = {}
    self._plr_name_to_load_name: Dict[str, str] = {}

  async def setup(self):
    self._run_id = ot_api.runs.create()
    ot_api.set_run(self._run_id)
    self.left_pipette, self.right_pipette = ot_api.lh.add_mounted_pipettes()
    health = ot_api.health.get()
    self.ot_api_version = health["api_version"]
    self._tip_racks = {}
    self._plr_name_to_load_name = {}

  async def stop(self):
    if self._run_id:
      try:
        _req.post(f"/runs/{self._run_id}/cancel")
      except Exception:
        try:
          _req.post(f"/runs/{self._run_id}/actions/cancel")
        except Exception:
          try:
            _req.delete(f"/runs/{self._run_id}")
          except Exception:
            pass
    self._run_id = None
    self.left_pipette = None
    self.right_pipette = None
    self._tip_racks = {}
    self._plr_name_to_load_name = {}

  def serialize(self) -> dict:
    return {**super().serialize(), "host": self.host, "port": self.port}

  # -- device-level operations --

  async def home(self):
    ot_api.health.home()

  async def list_connected_modules(self) -> List[dict]:
    return cast(List[dict], ot_api.modules.list_connected_modules())

  # -- shared labware registry --

  def get_ot_name(self, plr_resource_name: str) -> str:
    """Map a PLR resource name to an OT-compatible name (^[a-z0-9._]+$)."""
    if plr_resource_name not in self._plr_name_to_load_name:
      self._plr_name_to_load_name[plr_resource_name] = uuid.uuid4().hex
    return self._plr_name_to_load_name[plr_resource_name]

  def assign_tip_rack(self, tip_rack: TipRack, tip) -> None:
    """Register a tip rack with the OT-2 if not already registered."""
    if tip_rack.name in self._tip_racks:
      return
    ot_slot_size_y = 86
    lw = {
      "schemaVersion": 2, "version": 1, "namespace": "pylabrobot",
      "metadata": {
        "displayName": self.get_ot_name(tip_rack.name),
        "displayCategory": "tipRack", "displayVolumeUnits": "µL",
      },
      "brand": {"brand": "unknown"},
      "parameters": {
        "format": "96Standard", "isTiprack": True,
        "tipLength": tip.total_tip_length, "tipOverlap": tip.fitting_depth,
        "loadName": self.get_ot_name(tip_rack.name), "isMagneticModuleCompatible": False,
      },
      "ordering": utils.reshape_2d(
        [self.get_ot_name(s.name) for s in tip_rack.get_all_items()],
        (tip_rack.num_items_x, tip_rack.num_items_y),
      ),
      "cornerOffsetFromSlot": {
        "x": 0, "y": ot_slot_size_y - tip_rack.get_absolute_size_y(), "z": 0,
      },
      "dimensions": {
        "xDimension": tip_rack.get_absolute_size_x(),
        "yDimension": tip_rack.get_absolute_size_y(),
        "zDimension": tip_rack.get_absolute_size_z(),
      },
      "wells": {
        self.get_ot_name(c.name): {
          "depth": c.get_absolute_size_z(),
          "x": cast(Coordinate, c.location).x + c.get_absolute_size_x() / 2,
          "y": cast(Coordinate, c.location).y + c.get_absolute_size_y() / 2,
          "z": cast(Coordinate, c.location).z,
          "shape": "circular", "diameter": c.get_absolute_size_x(),
          "totalLiquidVolume": tip.maximal_volume,
        }
        for c in tip_rack.children
      },
      "groups": [{
        "wells": [self.get_ot_name(s.name) for s in tip_rack.get_all_items()],
        "metadata": {"displayName": None, "displayCategory": "tipRack", "wellBottomShape": "flat"},
      }],
    }
    data = self._define_labware(lw)
    namespace, definition, version = data["data"]["definitionUri"].split("/")
    labware_uuid = self.get_ot_name(tip_rack.name)
    deck = tip_rack.parent
    assert isinstance(deck, OTDeck)
    slot = deck.get_slot(tip_rack)
    assert slot is not None, "tip rack must be on deck"
    self._add_labware(
      load_name=definition, namespace=namespace, ot_location=slot,
      version=version, labware_id=labware_uuid,
      display_name=self.get_ot_name(tip_rack.name),
    )
    self._tip_racks[tip_rack.name] = slot

  def is_tip_rack_assigned(self, tip_rack_name: str) -> bool:
    return tip_rack_name in self._tip_racks

  # -- private wire methods --

  def _move_arm(self, pipette_id, location_x, location_y, location_z,
                minimum_z_height=None, speed=None, force_direct=False):
    ot_api.lh.move_arm(pipette_id=pipette_id, location_x=location_x,
      location_y=location_y, location_z=location_z,
      minimum_z_height=minimum_z_height, speed=speed, force_direct=force_direct)

  def _pick_up_tip(self, labware_id, well_name, pipette_id, offset_x, offset_y, offset_z):
    ot_api.lh.pick_up_tip(labware_id=labware_id, well_name=well_name,
      pipette_id=pipette_id, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z)

  def _drop_tip(self, labware_id, well_name, pipette_id, offset_x, offset_y, offset_z):
    ot_api.lh.drop_tip(labware_id=labware_id, well_name=well_name,
      pipette_id=pipette_id, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z)

  def _aspirate_in_place(self, volume, flow_rate, pipette_id):
    ot_api.lh.aspirate_in_place(volume=volume, flow_rate=flow_rate, pipette_id=pipette_id)

  def _dispense_in_place(self, volume, flow_rate, pipette_id):
    ot_api.lh.dispense_in_place(volume=volume, flow_rate=flow_rate, pipette_id=pipette_id)

  def _define_labware(self, definition: dict) -> dict:
    return ot_api.labware.define(definition)

  def _add_labware(self, load_name, namespace, ot_location, version, labware_id, display_name):
    ot_api.labware.add(load_name=load_name, namespace=namespace, ot_location=ot_location,
      version=version, labware_id=labware_id, display_name=display_name)

  def _save_position(self, pipette_id: str) -> dict:
    return ot_api.lh.save_position(pipette_id=pipette_id)

  def _move_to_addressable_area_for_drop_tip(self, pipette_id, offset_x, offset_y, offset_z):
    ot_api.lh.move_to_addressable_area_for_drop_tip(
      pipette_id=pipette_id, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z)

  def _drop_tip_in_place(self, pipette_id):
    ot_api.lh.drop_tip_in_place(pipette_id=pipette_id)
