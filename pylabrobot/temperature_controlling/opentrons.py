from pylabrobot.resources import Coordinate, ItemizedResource, Tube
from pylabrobot.resources.itemized_resource import create_equally_spaced
from pylabrobot.temperature_controlling.temperature_controller import TemperatureController
from pylabrobot.temperature_controlling.opentrons_backend import OpentronsTemperatureModuleBackend


class OpentronsTemperatureModuleV2(TemperatureController):
  """ Opentrons temperature module v2.

  https://opentrons.com/products/modules/temperature/
  """

  def __init__(self, name: str, opentrons_id: str):
    """ Create a new Opentrons temperature module v2.

    Args:
      name: Name of the temperature module.
      opentrons_id: Opentrons ID of the temperature module. Get it from
        `OpentronsTemperatureModuleBackend.list_connected_modules()`.
    """

    TemperatureController.__init__(
      self=self,
      name=name,
      size_x=112.0,
      size_y=73.6,

      # size_x=127.0,
      # size_y=86.0,

      size_z=140.0,
      backend=OpentronsTemperatureModuleBackend(opentrons_id=opentrons_id),
      category="temperature_controller",
      model="opentrons_temperature_module_v2"
    )

    b = OpentronsTemperatureModuleBackend(opentrons_id=opentrons_id)

    # verified guesses
    # TODO: make this a proper TubeRack
    self.tube_rack = ItemizedResource(
      name=f"{name}_tube_rack",
      size_x=112.0,
      size_y=73.6,
      size_z=140.0,

      items=create_equally_spaced(
        Tube,
        num_items_x=6,
        num_items_y=4,
        dx=22,
        dy=18,
        # dx=22 - (86 - 73.6),
        # dy=18 - (86 - 73.6),
        dz=45.0,

        item_dx=17.2,
        item_dy=17.2,

        size_x=10.0,
        size_y=10.0,
        size_z=40.0,
      ),

      category="temperature_module",
      model="opentrons_temperature_module_v2",
    )
    self.assign_child_resource(self.tube_rack, location=Coordinate(
      x=0,
      y=0,
      # x=(127 - 112.0)/2,
      # y=(86 - 73.6)/2,
      z=0
    ))

    self.backend: OpentronsTemperatureModuleBackend = b # fix type
