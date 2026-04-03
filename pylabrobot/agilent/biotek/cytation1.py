"""Cytation 1 device — imager with temperature control.

The Cytation 1 has a microscopy camera and temperature control,
but no plate reading capabilities (no absorbance/fluorescence/luminescence).

The backend must implement MicroscopyBackend (capture). Two options:
  - CytationBackend (cytation.py) — uses PySpin for camera
  - CytationAravisBackend (cytation_aravis.py) — uses Aravis for camera

Example::

    from pylabrobot.agilent.biotek.cytation_aravis import CytationAravisBackend
    from pylabrobot.agilent.biotek.cytation1 import Cytation1

    backend = CytationAravisBackend(camera_serial="22580842")
    cytation = Cytation1(name="cytation1", backend=backend)
    await cytation.setup()
    result = await cytation.microscopy.capture(...)
"""

from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.capabilities.microscopy import Microscopy
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource

from .biotek import BioTekBackend

logger = logging.getLogger(__name__)


class Cytation1(Resource, Device):
  """Agilent BioTek Cytation 1 — imager with temperature control.

  Takes a backend that provides microscopy via MicroscopyBackend.
  Pass either CytationBackend (PySpin) or CytationAravisBackend (Aravis).

  Capabilities:
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
      model="Agilent BioTek Cytation 1",
    )
    Device.__init__(self, driver=backend)
    self.driver: BioTekBackend = backend

    self.microscopy = Microscopy(backend=backend)
    self.temperature = TemperatureController(backend=backend)
    self._capabilities = [self.microscopy, self.temperature]

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
