from typing import Dict, Optional

from pylabrobot.capabilities.microscopy import ImagingMode, MicroscopyCapability, Objective
from pylabrobot.device import Device
from pylabrobot.resources import Resource, Rotation

from .backend import PicoBackend


class Pico(Resource, Device):
  """Molecular Devices ImageXpress Pico automated microscope.

  Args:
    name: Unique resource name.
    host: IP address or hostname of the instrument.
    port: gRPC port (default 8091).
    lock_timeout: Instrument lock timeout in seconds.
    objectives: Mapping from 0-indexed turret position to :class:`Objective`.
    filter_cubes: Mapping from 0-indexed filter wheel position to :class:`ImagingMode`.
    size_x: Instrument footprint X in mm.
    size_y: Instrument footprint Y in mm.
    size_z: Instrument footprint Z in mm.
  """

  def __init__(
    self,
    name: str,
    host: str,
    port: int = 8091,
    lock_timeout: int = 3600,
    objectives: Optional[Dict[int, Objective]] = None,
    filter_cubes: Optional[Dict[int, ImagingMode]] = None,
    size_x: float = 460.0,
    size_y: float = 430.0,
    size_z: float = 480.0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = "microscope",
    model: Optional[str] = "ImageXpress Pico",
  ):
    backend = PicoBackend(
      host=host,
      port=port,
      lock_timeout=lock_timeout,
      objectives=objectives,
      filter_cubes=filter_cubes,
    )
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
    )
    Device.__init__(self, backend=backend)
    self._backend: PicoBackend = backend

    self.microscopy = MicroscopyCapability(backend=backend)
    self._capabilities = [self.microscopy]
