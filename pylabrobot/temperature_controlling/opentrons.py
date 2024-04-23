from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.temperature_controlling.temperature_controller import TemperatureController
from pylabrobot.temperature_controlling.opentrons_backend import OpentronsTemperatureModuleBackend

class OpentronsTemperatureModuleV2(TemperatureController):
  """ Opentrons temperature module v2.

  https://opentrons.com/products/modules/temperature/
  https://shop.opentrons.com/aluminum-block-set/
  """

  def __init__(self, name: str, opentrons_id: str, tube_rack: ItemizedResource = None):
    """ Create a new Opentrons temperature module v2.

    Args:
      name: Name of the temperature module.
      opentrons_id: Opentrons ID of the temperature module. Get it from
        `OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()`.
      tube_rack: Optional tube rack to use on the temperature controlled module. Get it from
        `from pylabrobot.resources.opentrons.tube_racks import *`
    """

    super().__init__(
      name=name,
      size_x=193.5,
      size_y=89.2,
      size_z=84.0,  # height without any aluminum block
      backend=OpentronsTemperatureModuleBackend(opentrons_id=opentrons_id),
      category="temperature_controller",
      model="opentrons_temperature_module_v2"
    )

    self.backend = OpentronsTemperatureModuleBackend(opentrons_id=opentrons_id)

    if tube_rack is not None:
      self.assign_child_resource(tube_rack, location=Coordinate(x=0, y=0, z=0))


