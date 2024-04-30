from typing import Optional

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.resources.opentrons.module import OTModule
from pylabrobot.thermocycling.thermocycler import Thermocycler
from pylabrobot.thermocycling.opentrons_backend import OpentronsThermocyclerModuleBackend

class OpentronsThermocyclerModuleV1(Thermocycler, OTModule):
  """ Opentrons thermocycler v1.

  https://opentrons.com/products/modules/thermocycler/
  """

  def __init__(self, name: str, opentrons_id: str, child: Optional[ItemizedResource] = None):
    """ Create a new Opentrons thermocycler module v1.

    Args:
      name: Name of the thermocycler module.
      opentrons_id: Opentrons ID of the thermocycler module. Get it from
        `OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()`.
      child: Optional child resource like a well plate to use on the thermocycler module.
    """

    super().__init__(
      name=name,
      size_x=172.0,  # dimensions of entire box = 172.0 mm
      size_y=249.0,  # dimensions of entire box = 249.0 mm
      size_z=155.0,
      backend=OpentronsThermocyclerModuleBackend(opentrons_id=opentrons_id),
      category="thermocycler",
      model="thermocyclerModuleV1"
    )

    self.backend = OpentronsThermocyclerModuleBackend(opentrons_id=opentrons_id)
    self.child = child
    if child is not None:
      # todo: maybe allow single tubes or rows of tubes to be specified as child resources
      self.assign_child_resource(child, location=Coordinate(x=0, y=0, z=500))