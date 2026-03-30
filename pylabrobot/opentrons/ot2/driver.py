"""OpentronsOT2Driver -- owns the ot_api HTTP connection and device-level ops."""

from typing import Dict, List, Optional, cast

from pylabrobot.device import Driver

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
  discovery, homing, and module queries.  Exposes generic wire methods
  that :class:`OpentronsOT2PIPBackend` uses for protocol translation.
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

  async def setup(self):
    self._run_id = ot_api.runs.create()
    ot_api.set_run(self._run_id)

    self.left_pipette, self.right_pipette = ot_api.lh.add_mounted_pipettes()

    health = ot_api.health.get()
    self.ot_api_version = health["api_version"]

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

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "host": self.host,
      "port": self.port,
    }

  # -- device-level operations --

  async def home(self):
    ot_api.health.home()

  async def list_connected_modules(self) -> List[dict]:
    return cast(List[dict], ot_api.modules.list_connected_modules())

  # -- generic wire methods used by capability backends --

  def move_arm(
    self,
    pipette_id: str,
    location_x: float,
    location_y: float,
    location_z: float,
    minimum_z_height: Optional[float] = None,
    speed: Optional[float] = None,
    force_direct: bool = False,
  ):
    ot_api.lh.move_arm(
      pipette_id=pipette_id,
      location_x=location_x,
      location_y=location_y,
      location_z=location_z,
      minimum_z_height=minimum_z_height,
      speed=speed,
      force_direct=force_direct,
    )

  def pick_up_tip_raw(
    self, labware_id: str, well_name: str, pipette_id: str,
    offset_x: float, offset_y: float, offset_z: float,
  ):
    ot_api.lh.pick_up_tip(
      labware_id=labware_id, well_name=well_name, pipette_id=pipette_id,
      offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
    )

  def drop_tip_raw(
    self, labware_id: str, well_name: str, pipette_id: str,
    offset_x: float, offset_y: float, offset_z: float,
  ):
    ot_api.lh.drop_tip(
      labware_id=labware_id, well_name=well_name, pipette_id=pipette_id,
      offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
    )

  def aspirate_in_place(self, volume: float, flow_rate: float, pipette_id: str):
    ot_api.lh.aspirate_in_place(volume=volume, flow_rate=flow_rate, pipette_id=pipette_id)

  def dispense_in_place(self, volume: float, flow_rate: float, pipette_id: str):
    ot_api.lh.dispense_in_place(volume=volume, flow_rate=flow_rate, pipette_id=pipette_id)

  def define_labware(self, definition: dict) -> dict:
    return ot_api.labware.define(definition)

  def add_labware(
    self, load_name: str, namespace: str, ot_location: int,
    version: str, labware_id: str, display_name: str,
  ):
    ot_api.labware.add(
      load_name=load_name, namespace=namespace, ot_location=ot_location,
      version=version, labware_id=labware_id, display_name=display_name,
    )

  def save_position(self, pipette_id: str) -> dict:
    return ot_api.lh.save_position(pipette_id=pipette_id)

  def move_to_addressable_area_for_drop_tip(
    self, pipette_id: str, offset_x: float, offset_y: float, offset_z: float,
  ):
    ot_api.lh.move_to_addressable_area_for_drop_tip(
      pipette_id=pipette_id, offset_x=offset_x, offset_y=offset_y, offset_z=offset_z,
    )

  def drop_tip_in_place(self, pipette_id: str):
    ot_api.lh.drop_tip_in_place(pipette_id=pipette_id)
