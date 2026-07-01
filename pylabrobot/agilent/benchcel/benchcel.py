"""Agilent BenchCel 4R microplate handler (v1 device)."""

from __future__ import annotations

from typing import List, Optional, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.stacker import Stacker
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Resource, Rotation
from pylabrobot.resources.resource_stack import ResourceStack

from .driver import BenchCel4RBackend
from .labware import BenchCelLabwareSettings, resolve_benchcel_labware_settings
from .stacks import benchcel_4r_stacks

__all__ = ["BenchCel4R"]


class BenchCel4R(Resource, Device):
  """Agilent BenchCel 4R microplate handler.

  The BenchCel is a sequential ("stacking access") storage device: each of its four stackers is a
  single-ended LIFO stack of plates. It is modelled with the ``Stacker`` capability
  (:attr:`stacker`), and the driver (:class:`~pylabrobot.agilent.benchcel.driver.BenchCel4RBackend`)
  implements the ``StackerBackend`` ``downstack``/``upstack`` transfers.

  The four ``ResourceStack`` stacks track the expected plate order/content of each stacker.

  Args:
    name: Resource name for the device.
    host: IP address or DNS name of the BenchCel Ethernet interface.
    loading_tray_teachpoint_id: Teachpoint target ID used as the transfer point by the stacker's
      ``downstack``/``upstack``. The BenchCel has no fixed loading position; this must be a
      teachpoint taught on the device. Transfers raise unless it is set. There is no default
      because an unset/wrong teachpoint can send the arm to a home-like pose.
    stacks: Optionally provide custom ``ResourceStack`` stacks; defaults to four generic stacks.
    loading_tray_location: Cosmetic ``Coordinate`` of the loading-tray resource (resource tree /
      visualization only; the real transfer position is the teachpoint on the device).
  """

  def __init__(
    self,
    name: str,
    host: str,
    *,
    port: int = BenchCel4RBackend.DEFAULT_PORT,
    timeout: float = 30.0,
    read_poll_timeout: float = 0.25,
    loading_tray_teachpoint_id: Optional[int] = None,
    source_ip: Optional[str] = None,
    backend: Optional[BenchCel4RBackend] = None,
    stacks: Optional[List[ResourceStack]] = None,
    labware: Optional[Union[Plate, BenchCelLabwareSettings, dict]] = None,
    loading_tray_location: Optional[Coordinate] = None,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = "Agilent BenchCel 4R",
  ):
    labware_settings = resolve_benchcel_labware_settings(labware) if labware is not None else None

    if backend is None:
      backend = BenchCel4RBackend(
        host=host,
        port=port,
        timeout=timeout,
        read_poll_timeout=read_poll_timeout,
        loading_tray_teachpoint_id=loading_tray_teachpoint_id,
        source_ip=source_ip,
        labware=labware_settings,
      )
    elif labware_settings is not None:
      existing_labware = backend.labware_settings
      if existing_labware is not None and existing_labware != labware_settings:
        raise ValueError(
          f"BenchCel4R backend labware {existing_labware.name!r} does not match factory labware "
          f"{labware_settings.name!r}"
        )
      backend.labware_settings = labware_settings

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
    self.driver: BenchCel4RBackend = backend

    self.loading_tray = PlateHolder(
      name=f"{name}_tray", size_x=127.76, size_y=85.48, size_z=0, pedestal_size_z=0
    )
    self.assign_child_resource(
      self.loading_tray, location=loading_tray_location or Coordinate.zero()
    )

    self._stacks = stacks if stacks is not None else benchcel_4r_stacks()
    for stack in self._stacks:
      self.assign_child_resource(stack, location=None)

    self.stacker = Stacker(backend=backend, stacks=self._stacks, loading_tray=self.loading_tray)
    self._capabilities = [self.stacker]

  @property
  def stacks(self) -> List[ResourceStack]:
    return self._stacks

  async def setup(self, backend_params: Optional[BackendParams] = None, **backend_kwargs):
    await super().setup(backend_params=backend_params)
    await self.driver.set_stacks(self._stacks)

  def serialize(self) -> dict:
    from pylabrobot.serializer import serialize

    return {
      **Device.serialize(self),
      **Resource.serialize(self),
      "loading_tray_location": serialize(self.loading_tray.location),
    }
