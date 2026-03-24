from typing import List, Optional

from pylabrobot.capabilities.temperature_controlling import (
  TemperatureControlCapability,
)
from pylabrobot.capabilities.thermocycling import (
  ThermocyclingCapability,
)
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, ResourceHolder

from .block_backend import ThermoFisherBlockBackend
from .driver import ThermoFisherThermocyclerDriver
from .lid_backend import ThermoFisherLidBackend
from .thermocycling_backend import ThermoFisherThermocyclingBackend


class ThermoFisherThermocycler(ResourceHolder, Device):
  """ThermoFisher thermocycler device using the capability composition architecture.

  Creates a shared SCPI driver and exposes per-block capabilities for
  temperature control (block + lid) and thermocycling (protocol execution).
  """

  def __init__(
    self,
    name: str,
    ip: str,
    num_blocks: int,
    num_temp_zones: int,
    supports_lid_control: bool = False,
    use_ssl: bool = False,
    serial_number: Optional[str] = None,
    block_idle_temp: float = 25.0,
    cover_idle_temp: float = 105.0,
    child_location: Coordinate = Coordinate.zero(),
    size_x: float = 300.0,
    size_y: float = 300.0,
    size_z: float = 200.0,
  ):
    self._driver = ThermoFisherThermocyclerDriver(
      ip=ip,
      use_ssl=use_ssl,
      serial_number=serial_number,
    )

    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
    )
    Device.__init__(self, backend=self._driver)

    self._block_idle_temp = block_idle_temp
    self._cover_idle_temp = cover_idle_temp

    self.blocks: List[TemperatureControlCapability] = []
    self.lids: List[TemperatureControlCapability] = []
    self.thermocycling: List[ThermocyclingCapability] = []

    for block_id in range(num_blocks):
      block_be = ThermoFisherBlockBackend(driver=self._driver, block_id=block_id)
      lid_be = ThermoFisherLidBackend(driver=self._driver, block_id=block_id)
      tc_be = ThermoFisherThermocyclingBackend(
        driver=self._driver,
        block_id=block_id,
        supports_lid_control=supports_lid_control,
      )

      block_cap = TemperatureControlCapability(backend=block_be)
      lid_cap = TemperatureControlCapability(backend=lid_be)
      tc_cap = ThermocyclingCapability(backend=tc_be, block=block_cap, lid=lid_cap)

      self.blocks.append(block_cap)
      self.lids.append(lid_cap)
      self.thermocycling.append(tc_cap)

    self._capabilities = [
      cap for triple in zip(self.blocks, self.lids, self.thermocycling) for cap in triple
    ]

  async def setup(self, **backend_kwargs):
    """Set up the thermocycler: authenticate, power on, discover blocks, set idle temps."""
    await self._driver.setup(
      block_idle_temp=self._block_idle_temp,
      cover_idle_temp=self._cover_idle_temp,
    )
    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True
