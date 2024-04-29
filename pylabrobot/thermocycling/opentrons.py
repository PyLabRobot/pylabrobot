from pylabrobot.resources.opentrons.module import OTModule
from pylabrobot.thermocycling.thermocycler import Thermocycler
from pylabrobot.thermocycling.opentrons_backend import OpentronsThermocyclerModuleBackend

class OpentronsThermocyclerModuleV1(Thermocycler, OTModule):
  """ Opentrons thermocycler v1.

  https://opentrons.com/products/modules/thermocycler/
  """

  def __init__(self, name: str, opentrons_id: str):
    """ Create a new Opentrons thermocycler module v1.

    Args:
      name: Name of the thermocycler module.
      opentrons_id: Opentrons ID of the thermocycler module. Get it from
        `OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()`.
    """

    Thermocycler.__init__(
      self=self,
      name=name,
      size_x=172.0,  # dimensions of entire box = 172.0 mm
      size_y=249.0,  # dimensions of entire box = 249.0 mm
      size_z=155.0,
      backend=OpentronsThermocyclerModuleBackend(opentrons_id=opentrons_id),
      category="thermocycler",
      model="opentrons_thermocycler_module_v1"
    )

    b = OpentronsThermocyclerModuleBackend(opentrons_id=opentrons_id)
