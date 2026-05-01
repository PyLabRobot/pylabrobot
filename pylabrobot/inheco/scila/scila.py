import logging
from typing import Dict, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.loading_tray import LoadingTray
from pylabrobot.capabilities.loading_tray.backend import LoadingTrayBackend
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, Resource

from .scila_backend import SCILADriver, SCILATemperatureBackend

logger = logging.getLogger(__name__)


class SCILADrawerLoadingTrayBackend(LoadingTrayBackend):
  """Loading tray backend for a single SCILA drawer."""

  def __init__(self, driver: SCILADriver, drawer_id: int):
    if drawer_id not in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    self._driver = driver
    self._drawer_id = drawer_id

  async def open(self, backend_params: Optional[BackendParams] = None):
    await self._driver.send_command("PrepareForInput", position=self._drawer_id)
    try:
      await self._driver.send_command("OpenDoor")
    except RuntimeError as e:
      # SCILA raises a non-fatal CO2-flow warning as an exception; log and continue.
      if "warning" not in str(e).lower():
        raise
      logger.warning("drawer %d open: %s", self._drawer_id, e)

  async def close(self, backend_params: Optional[BackendParams] = None):
    await self._driver.send_command("PrepareForOutput", position=self._drawer_id)
    try:
      await self._driver.send_command("CloseDoor")
    except RuntimeError as e:
      # SCILA raises a non-fatal CO2-flow warning as an exception; log and continue.
      if "warning" not in str(e).lower():
        raise
      logger.warning("drawer %d close: %s", self._drawer_id, e)


class SCILA(Resource, Device):
  """Inheco SCILA incubator with 4 drawers and temperature control."""

  def __init__(
    self,
    name: str,
    scila_ip: str,
    client_ip: Optional[str] = None,
    size_x: float = 0.0,  # TODO: measure
    size_y: float = 0.0,  # TODO: measure
    size_z: float = 0.0,  # TODO: measure
  ):
    driver = SCILADriver(scila_ip=scila_ip, client_ip=client_ip)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Inheco SCILA",
    )
    Device.__init__(self, driver=driver)
    self.driver: SCILADriver = driver
    self.tc = TemperatureController(backend=SCILATemperatureBackend(driver=driver))

    self.drawers: Dict[int, LoadingTray] = {}
    for drawer_id in range(1, 5):
      tray = LoadingTray(
        backend=SCILADrawerLoadingTrayBackend(driver=driver, drawer_id=drawer_id),
        name=f"{name}_drawer_{drawer_id}",
        size_x=0.0,  # TODO: measure
        size_y=0.0,  # TODO: measure
        size_z=0.0,  # TODO: measure
        child_location=Coordinate.zero(),  # TODO: measure
      )
      self.drawers[drawer_id] = tray
      self.assign_child_resource(tray, location=Coordinate.zero())  # TODO: measure

    self._capabilities = [self.tc, *self.drawers.values()]

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}
