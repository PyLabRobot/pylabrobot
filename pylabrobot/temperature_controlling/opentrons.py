from pylabrobot.resources import Coordinate, ItemizedResource, Tube
from pylabrobot.resources.itemized_resource import create_equally_spaced
from pylabrobot.temperature_controlling.temperature_controller import TemperatureController
from pylabrobot.temperature_controlling.opentrons_backend import OpentronsTemperatureModuleBackend


class OpentronsTemperatureModuleV2(TemperatureController):
  """ Opentrons temperature module v2.

  https://opentrons.com/products/modules/temperature/
  https://shop.opentrons.com/aluminum-block-set/
  """

  def __init__(self, name: str, opentrons_id: str):
    """ Create a new Opentrons temperature module v2.

    Args:
      name: Name of the temperature module.
      opentrons_id: Opentrons ID of the temperature module. Get it from
        `OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()`.
    """

    TemperatureController.__init__(
      self=self,
      name=name,
      size_x=193.5,
      size_y=89.2,
      size_z=84.0,  # height without any aluminum block
      backend=OpentronsTemperatureModuleBackend(opentrons_id=opentrons_id),
      category="temperature_controller",
      model="opentrons_temperature_module_v2"
    )

    b = OpentronsTemperatureModuleBackend(opentrons_id=opentrons_id)

    self.well_block_96 = ItemizedResource(
      name=f"{name}_96_well_block",
      size_x=128.0,
      size_y=85.5,
      size_z=19.0,

      items = create_equally_spaced(Tube,
      num_items_x=12,
      num_items_y=8,

      dx=9.0,
      dy=9.0,
      dz=14.5,

      item_dx=9.0,
      item_dy=9.0,
      size_x=6.0,
      size_y=6.0,
      size_z=14.5,
    )
    )

    self.well_block_24 = ItemizedResource(
      name=f"{name}_24_well_block",
      size_x=128.0,
      size_y=85.5,
      size_z=42.0,

      items=create_equally_spaced(Tube,
        num_items_x=6,
        num_items_y=4,
        dx=18.5,  # distance from center of one well to center of next well on x axis
        dy=18.5,  # distance from center of one well to center of next well on y axis
        dz=37.0,  # depth of wells from openings on down

        item_dx=17.2,  # size of square around well in x
        item_dy=17.2,  # size of square around well in y

        size_x=12.0,  # diameter of well
        size_y=12.0,  # diameter of well
        size_z=37.0,  # depth of well (same as dz)
      ),

      category="temperature_module",
      model="opentrons_temperature_module_v2"
    )

    # todo allow user to specify block
    self.assign_child_resource(self.well_block_24, location=Coordinate(
      x=0,
      y=0,
      z=0
    ))

    self.backend: OpentronsTemperatureModuleBackend = b # fix type
