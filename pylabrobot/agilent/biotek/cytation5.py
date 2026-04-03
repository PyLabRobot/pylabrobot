"""Cytation 5 device — plate reader + imager.

The Cytation 5 combines plate reading (absorbance, fluorescence,
luminescence) with microscopy imaging. The backend must implement
both BioTekBackend (serial protocol) and MicroscopyBackend (capture).

Two backend options:
  - CytationBackend (cytation.py) — uses PySpin for camera
  - CytationAravisBackend (cytation_aravis.py) — uses Aravis for camera

Example::

    from pylabrobot.agilent.biotek.cytation_aravis import CytationAravisBackend
    from pylabrobot.agilent.biotek.cytation5 import Cytation5

    backend = CytationAravisBackend(camera_serial="22580842")
    cytation = Cytation5(name="cytation5", backend=backend)
    await cytation.setup()
    result = await cytation.microscopy.capture(...)
"""

from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.capabilities.microscopy import Microscopy
from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.plate_reading.fluorescence import Fluorescence
from pylabrobot.capabilities.plate_reading.luminescence import Luminescence
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource

from .biotek import BioTekBackend

logger = logging.getLogger(__name__)


class Cytation5(Resource, Device):
  """Agilent BioTek Cytation 5 — plate reader + imager.

  Takes a backend that provides both plate reading (via BioTekBackend)
  and microscopy (via MicroscopyBackend). Pass either CytationBackend
  (PySpin) or CytationAravisBackend (Aravis).

  Capabilities:
    - absorbance, fluorescence, luminescence (plate reading)
    - microscopy (imaging via camera)
    - temperature (incubation)
  """

  def __init__(
    self,
    name: str,
    backend: BioTekBackend,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
  ):
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Agilent BioTek Cytation 5",
    )
    Device.__init__(self, driver=backend)
    self.driver: BioTekBackend = backend

    self.absorbance = Absorbance(backend=backend)
    self.luminescence = Luminescence(backend=backend)
    self.fluorescence = Fluorescence(backend=backend)
    self.microscopy = Microscopy(backend=backend)
    self.temperature = TemperatureController(backend=backend)
    self._capabilities = [
      self.absorbance,
      self.luminescence,
      self.fluorescence,
      self.microscopy,
      self.temperature,
    ]

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  async def open(self, slow: bool = False) -> None:
    await self.driver.open(slow=slow)

  async def close(self, slow: bool = False) -> None:
    await self.driver.close(slow=slow)
