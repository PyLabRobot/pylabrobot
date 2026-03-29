from typing import Optional

from pylabrobot.capabilities.temperature_controlling import (
  TemperatureController,
  TemperatureControllerBackend,
)
from pylabrobot.device import Device, Driver
from pylabrobot.resources import Coordinate, ItemizedResource, ResourceHolder
from pylabrobot.resources.opentrons.module import OTModule

from .http_driver import (
  OpentronsTemperatureModuleDriver,
  OpentronsTemperatureModuleTemperatureBackend,
)
from .usb_driver import (
  OpentronsTemperatureModuleUSBDriver,
  OpentronsTemperatureModuleUSBTemperatureBackend,
)


class OpentronsTemperatureModuleV2(ResourceHolder, Device, OTModule):
  """Opentrons Temperature Module v2.

  https://opentrons.com/products/modules/temperature/
  https://shop.opentrons.com/aluminum-block-set/

  Example:
    >>> from pylabrobot.opentrons.temperature_module import OpentronsTemperatureModuleV2
    >>> mod = OpentronsTemperatureModuleV2("temp_mod", serial_port="/dev/ttyACM0")
    >>> await mod.setup()
    >>> await mod.tc.set_temperature(37.0)
    >>> await mod.tc.get_temperature()
    37.0
  """

  def __init__(
    self,
    name: str,
    opentrons_id: Optional[str] = None,
    serial_port: Optional[str] = None,
    child_location: Coordinate = Coordinate(0, 0, 80.1),
    child: Optional[ItemizedResource] = None,
  ):
    """Create a new Opentrons Temperature Module v2.

    Args:
      name: Name of the temperature module.
      opentrons_id: Opentrons ID of the temperature module. Exactly one of
        ``opentrons_id`` or ``serial_port`` must be provided.
      serial_port: Serial port for USB communication. Exactly one of
        ``opentrons_id`` or ``serial_port`` must be provided.
      child_location: Location of the child resource relative to this module.
      child: Optional child resource like a tube rack or well plate.
    """
    if opentrons_id is None and serial_port is None:
      raise ValueError("Exactly one of `opentrons_id` or `serial_port` must be provided.")
    if opentrons_id is not None and serial_port is not None:
      raise ValueError("Exactly one of `opentrons_id` or `serial_port` must be provided.")

    driver: Driver
    tc_backend: TemperatureControllerBackend
    if serial_port is not None:
      driver = OpentronsTemperatureModuleUSBDriver(port=serial_port)
      tc_backend = OpentronsTemperatureModuleUSBTemperatureBackend(driver=driver)
    else:
      assert opentrons_id is not None
      driver = OpentronsTemperatureModuleDriver(opentrons_id=opentrons_id)
      tc_backend = OpentronsTemperatureModuleTemperatureBackend(driver=driver)

    ResourceHolder.__init__(
      self,
      name=name,
      size_x=193.5,
      size_y=89.2,
      size_z=84.0,
      child_location=child_location,
      category="temperature_controller",
      model="temperatureModuleV2",
    )
    Device.__init__(self, driver=driver)
    self._driver = driver
    self.tc = TemperatureController(backend=tc_backend)
    self._capabilities = [self.tc]

    if child is not None:
      self.assign_child_resource(child)

  def serialize(self) -> dict:
    return {**ResourceHolder.serialize(self), **Device.serialize(self)}
