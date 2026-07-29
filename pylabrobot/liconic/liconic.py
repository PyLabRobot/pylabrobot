from typing import List, Optional, Union

from pylabrobot.capabilities.automated_retrieval import NoFreeSiteError, RandomAccessRetrieval
from pylabrobot.capabilities.barcode_scanning import BarcodeScanner
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.humidity_controlling import HumidityController
from pylabrobot.capabilities.shaking import Shaker
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import (
  Coordinate,
  PlateCarrier,
  PlateHolder,
  Resource,
  Rotation,
)

from .backend import LiconicBackend, LiconicType

__all__ = ["Liconic", "NoFreeSiteError"]


class Liconic(Resource, Device):
  def __init__(
    self,
    name: str,
    liconic_model: Union[LiconicType, str],
    port: str,
    racks: List[PlateCarrier],
    loading_tray_location: Coordinate,
    has_shaker: bool = False,
    barcode_scanner: Optional[BarcodeScanner] = None,
    size_x: float = 0,
    size_y: float = 0,
    size_z: float = 0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    if isinstance(liconic_model, str):
      liconic_model = LiconicType(liconic_model)

    backend = LiconicBackend(model=liconic_model, port=port)
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
    Device.__init__(self, driver=backend)
    self.driver: LiconicBackend = backend

    self.loading_tray = PlateHolder(
      name=f"{name}_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(self.loading_tray, location=loading_tray_location)

    self._racks = racks
    for rack in self._racks:
      self.assign_child_resource(rack, location=None)

    self.retrieval = RandomAccessRetrieval(
      backend=backend, racks=self._racks, loading_tray=self.loading_tray
    )
    self.tc = (
      TemperatureController(backend=backend) if liconic_model.has_temperature_control else None
    )
    self.humidity_controller = (
      HumidityController(backend=backend) if liconic_model.has_humidity_control else None
    )
    self.shaker = Shaker(backend=backend) if has_shaker else None
    self.barcode_scanner = barcode_scanner

    self._capabilities = [
      c
      for c in [
        self.retrieval,
        self.tc,
        self.humidity_controller,
        self.shaker,
        self.barcode_scanner,
      ]
      if c is not None
    ]

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  async def setup(self, backend_params: Optional[BackendParams] = None, **backend_kwargs):
    if self.barcode_scanner is not None:
      await self.barcode_scanner.backend._on_setup()
    await super().setup(backend_params=backend_params)
    await self.driver.set_racks(self._racks)

  async def stop(self):
    await super().stop()
    if self.barcode_scanner is not None:
      await self.barcode_scanner.backend._on_stop()

  def serialize(self):
    from pylabrobot.serializer import serialize

    return {
      **Device.serialize(self),
      **Resource.serialize(self),
      "racks": [rack.serialize() for rack in self._racks],
      "loading_tray_location": serialize(self.loading_tray.location),
    }
