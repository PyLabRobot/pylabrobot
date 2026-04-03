"""Cytation 5 device — plate reader + imager.

Follows the STAR pattern: device creates driver internally based on
the ``camera`` parameter, setup() wires capabilities.

Example::

    # Aravis (default)
    cytation = Cytation5(name="cytation5", camera_serial="22580842")

    # PySpin
    cytation = Cytation5(name="cytation5", camera="pyspin")

    await cytation.setup()
    result = await cytation.microscopy.capture(...)
    await cytation.stop()
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from pylabrobot.capabilities.microscopy import Microscopy
from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.plate_reading.fluorescence import Fluorescence
from pylabrobot.capabilities.plate_reading.luminescence import Luminescence
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource

logger = logging.getLogger(__name__)


class Cytation5(Resource, Device):
  """Agilent BioTek Cytation 5 — plate reader + imager.

  Creates the appropriate driver based on the ``camera`` parameter:
    - ``"aravis"`` (default): CytationAravisDriver (Aravis/GenICam)
    - ``"pyspin"``: CytationBackend (PySpin/Spinnaker SDK)

  Capabilities:
    - absorbance, fluorescence, luminescence (plate reading)
    - microscopy (imaging)
    - temperature (incubation)
  """

  def __init__(
    self,
    name: str,
    camera: Literal["aravis", "pyspin"] = "aravis",
    camera_serial: Optional[str] = None,
    device_id: Optional[str] = None,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
  ):
    if camera == "aravis":
      from .cytation_aravis_driver import CytationAravisDriver
      driver = CytationAravisDriver(
        camera_serial=camera_serial,
        device_id=device_id,
      )
    elif camera == "pyspin":
      from .cytation import CytationBackend, CytationImagingConfig
      config = CytationImagingConfig(camera_serial_number=camera_serial)
      driver = CytationBackend(
        device_id=device_id,
        imaging_config=config,
      )
    else:
      raise ValueError(f"Unknown camera backend: {camera!r}. Use 'aravis' or 'pyspin'.")

    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Agilent BioTek Cytation 5",
    )
    Device.__init__(self, driver=driver)
    self.driver = driver
    self._camera = camera

    self.absorbance: Absorbance  # set in setup()
    self.luminescence: Luminescence  # set in setup()
    self.fluorescence: Fluorescence  # set in setup()
    self.microscopy: Microscopy  # set in setup()
    self.temperature: TemperatureController  # set in setup()

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  async def setup(self) -> None:
    if self._camera == "aravis":
      await self.driver.setup()
      self.microscopy = Microscopy(backend=self.driver.microscopy_backend)
    else:
      await self.driver.setup(use_cam=True)
      self.microscopy = Microscopy(backend=self.driver)

    # Plate reading + temperature use the driver directly
    # (BioTekBackend implements these backend ABCs)
    self.absorbance = Absorbance(backend=self.driver)
    self.luminescence = Luminescence(backend=self.driver)
    self.fluorescence = Fluorescence(backend=self.driver)
    self.temperature = TemperatureController(backend=self.driver)

    self._capabilities = [
      self.absorbance,
      self.luminescence,
      self.fluorescence,
      self.microscopy,
      self.temperature,
    ]

    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True
    logger.info("Cytation5 setup complete (camera=%s)", self._camera)

  async def stop(self) -> None:
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self.driver.stop()
    self._setup_finished = False
    logger.info("Cytation5 stopped")

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  async def open(self, slow: bool = False) -> None:
    await self.driver.open(slow=slow)

  async def close(self, slow: bool = False) -> None:
    await self.driver.close(slow=slow)
