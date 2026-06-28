import warnings
from typing import List, Optional

from pylabrobot.capabilities.automated_retrieval import AutomatedRetrieval
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.humidity_controlling import HumidityController
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import (
  Coordinate,
  PlateCarrier,
  PlateHolder,
  Rotation,
)
from pylabrobot.resources.resource import Resource

from .driver import HighResSampleStorageDriver


class _HighResSampleStorage(Resource, Device):
  """Base device for HighRes Biosolutions sample stores.

  The TundraStore, SteriStore and AmbiStore are the same machine family behind a
  shared port-1000 API, so all of the implementation lives here and the concrete
  devices are thin subclasses. Each rack is a *stacker* (a vertical column of
  plate slots); plates enter and leave through one of the device's *nests*
  (transfer stations), exposed as the loading trays of the
  :class:`AutomatedRetrieval` capability (:attr:`retrieval`). Storage bookkeeping
  and the fetch/store operations live on the capability; address a particular
  nest with its ``tray_index`` (0-based, defaulting to the first nest).

  Subclasses set :attr:`_model_name` and :attr:`_has_environment_control` (the
  latter controls whether the temperature/humidity capabilities are wired).
  """

  _model_name: str = "HighResSampleStorage"
  _has_environment_control: bool = True

  def __init__(
    self,
    name: str,
    driver: HighResSampleStorageDriver,
    racks: List[PlateCarrier],
    nest_locations: List[Coordinate],
    size_x: float = 0,
    size_y: float = 0,
    size_z: float = 0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = "plate_store",
    model: Optional[str] = None,
  ):
    """
    Args:
      racks: Storage racks; rack *i* maps to device stacker ``i + 1``.
      nest_locations: One :class:`Coordinate` per transfer nest (the device has
        two). ``nest_locations[i]`` is the location of nest/tray ``i``.
    """
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model or self._model_name,
    )
    Device.__init__(self, driver=driver)
    self.driver: HighResSampleStorageDriver = driver

    self.nests: List[PlateHolder] = []
    for i, location in enumerate(nest_locations):
      nest = PlateHolder(
        name=f"{name}_nest_{i + 1}", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
      )
      self.assign_child_resource(nest, location=location)
      self.nests.append(nest)

    self._racks = racks
    for rack in self._racks:
      self.assign_child_resource(rack, location=None)

    self.retrieval = AutomatedRetrieval(
      backend=driver.automated_retrieval, racks=self._racks, loading_trays=self.nests
    )
    self._capabilities = [self.retrieval]

    if self._has_environment_control:
      self.tc = TemperatureController(backend=driver.temperature)
      self.humidity = HumidityController(backend=driver.humidity)
      self._capabilities = [self.tc, self.humidity, self.retrieval]

  @property
  def racks(self) -> List[PlateCarrier]:
    return self._racks

  async def setup(self, backend_params: Optional[BackendParams] = None):
    await super().setup(backend_params=backend_params)
    await self.driver.automated_retrieval.set_racks(self._racks)

  def serialize(self) -> dict:
    from pylabrobot.serializer import serialize

    return {
      **Device.serialize(self),
      **Resource.serialize(self),
      "racks": [rack.serialize() for rack in self._racks],
      "nest_locations": [serialize(nest.location) for nest in self.nests],
    }


class TundraStore(_HighResSampleStorage):
  """HighRes Biosolutions TundraStore refrigerated plate store."""

  _model_name = "TundraStore"


class SteriStore(_HighResSampleStorage):
  """HighRes Biosolutions SteriStore plate store (same API as the TundraStore)."""

  _model_name = "SteriStore"


class AmbiStore(_HighResSampleStorage):
  """HighRes Biosolutions AmbiStore plate store.

  WORK IN PROGRESS: the AmbiStore is ambient (no refrigeration), so it exposes
  only the retrieval capability — no temperature/humidity control. Whether it
  has any environment control at all is not yet confirmed against hardware.
  """

  _model_name = "AmbiStore"
  _has_environment_control = False

  def __init__(self, *args, **kwargs):
    warnings.warn(
      "AmbiStore support is a work in progress and unverified against hardware; "
      "it currently exposes only the retrieval capability (no environment control).",
      stacklevel=2,
    )
    super().__init__(*args, **kwargs)
