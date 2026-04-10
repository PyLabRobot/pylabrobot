from __future__ import annotations

import logging
from typing import List, Optional

from pylabrobot.capabilities.capability import Capability
from pylabrobot.capabilities.loading_tray import LoadingTray
from pylabrobot.capabilities.microscopy import Microscopy
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource

from .biotek import BioTekBackend
from .cytation_microscopy_backend import CytationImagingConfig, CytationMicroscopyBackend
from .loading_tray_backend import BioTekLoadingTrayBackend

logger = logging.getLogger(__name__)


class _CytationBase(Resource, Device):
  """Shared base for Cytation 1 and Cytation 5 devices.

  Handles driver creation, resource init, plate holder, lifecycle (stop/open/close),
  and capability wiring for microscopy, temperature, and loading tray
  (common to both models). Subclasses add model-specific capabilities in setup().
  """

  _model_name: str  # set by subclass

  def __init__(
    self,
    name: str,
    camera_serial: Optional[str] = None,
    device_id: Optional[str] = None,
    imaging_config: Optional[CytationImagingConfig] = None,
    use_cam: bool = True,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
  ):
    driver = BioTekBackend(
      device_id=device_id,
      human_readable_device_name=self._model_name,
    )

    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model=self._model_name,
    )
    Device.__init__(self, driver=driver)
    self.driver: BioTekBackend = driver

    self._microscopy_backend = CytationMicroscopyBackend(
      driver=driver,
      camera_serial=camera_serial,
      imaging_config=imaging_config,
      use_cam=use_cam,
    )

    self.microscopy: Microscopy  # set in _setup_base()
    self.temperature: TemperatureController  # set in _setup_base()
    self.loading_tray: LoadingTray  # set in _setup_base()
    self._capabilities: List[Capability] = []

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  async def _setup_base(self) -> None:
    """Set up driver and wire shared capabilities."""
    await self.driver.setup()

    self.microscopy = Microscopy(backend=self._microscopy_backend)
    self.temperature = TemperatureController(backend=self.driver)
    self.loading_tray = LoadingTray(
      backend=BioTekLoadingTrayBackend(driver=self.driver),
      name=self.name + "_loading_tray",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )

  async def stop(self) -> None:
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self.driver.stop()
    self._setup_finished = False
    logger.info("%s stopped", self.__class__.__name__)

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  async def open(self, slow: bool = False) -> None:
    await self.loading_tray.open(
      backend_params=BioTekLoadingTrayBackend.OpenParams(slow=slow)
    )

  async def close(self, slow: bool = False) -> None:
    await self.loading_tray.close(
      backend_params=BioTekLoadingTrayBackend.CloseParams(slow=slow)
    )
