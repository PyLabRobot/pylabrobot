from typing import Optional

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.resources.opentrons.module import OTModule
from pylabrobot.temperature_controlling.backend import TemperatureControllerBackend
from pylabrobot.temperature_controlling.opentrons_backend import (
  OpentronsTemperatureModuleBackend,
)
from pylabrobot.temperature_controlling.opentrons_backend_usb import (
  OpentronsTemperatureModuleUSBBackend,
)
from pylabrobot.temperature_controlling.temperature_controller import (
  TemperatureController,
)


class OpentronsTemperatureModuleV2(TemperatureController, OTModule):
  """Opentrons temperature module v2.

  https://opentrons.com/products/modules/temperature/
  https://shop.opentrons.com/aluminum-block-set/
  """

  def __init__(
    self,
    name: str,
    opentrons_id: Optional[str] = None,
    serial_port: Optional[str] = None,
    child_location: Coordinate = Coordinate(
      0, 0, 80.1
    ),  # dimensional drawing from OT (x and y are not changed wrt parent)
    child: Optional[ItemizedResource] = None,
  ):
    """Create a new Opentrons temperature module v2.

    Args:
      name: Name of the temperature module.
      opentrons_id: Opentrons ID of the temperature module. Get it from
        `OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()`. Exactly one of `opentrons_id` or `serial_port` must be provided.
      serial_port: Serial port for USB communication. Exactly one of `opentrons_id` or `serial_port` must be provided.
      child: Optional child resource like a tube rack or well plate to use on the
        temperature controller module.
    """

    if opentrons_id is None and serial_port is None:
      raise ValueError("Exactly one of `opentrons_id` or `serial_port` must be provided.")
    if opentrons_id is not None and serial_port is not None:
      raise ValueError("Exactly one of `opentrons_id` or `serial_port` must be provided.")

    backend: TemperatureControllerBackend
    if serial_port is not None:
      backend = OpentronsTemperatureModuleUSBBackend(port=serial_port)
    else:
      assert opentrons_id is not None
      backend = OpentronsTemperatureModuleBackend(opentrons_id=opentrons_id)

    super().__init__(
      name=name,
      size_x=193.5,
      size_y=89.2,
      size_z=84.0,  # height without any aluminum block
      child_location=child_location,
      backend=backend,
      category="temperature_controller",
      model="temperatureModuleV2",  # Must match OT moduleModel in list_connected_modules()
    )

    self.child = child

    if child is not None:
      self.assign_child_resource(child)
