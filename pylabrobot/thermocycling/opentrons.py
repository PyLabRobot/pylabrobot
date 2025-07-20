from typing import Optional, cast

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.resources.opentrons.module import OTModule
from pylabrobot.thermocycling.opentrons_backend import OpentronsThermocyclerBackend
from pylabrobot.thermocycling.thermocycler import Thermocycler


class OpentronsThermocyclerModuleV1(Thermocycler, OTModule):
  """Opentrons Thermocycler GEN1/GEN2 wrapper.

  Dimensions (closed-lid): 244.95 mm (x) × 172 mm (y) × 170.35 mm (z).
  """

  def __init__(
    self,
    name: str,
    opentrons_id: str,
    child_location: Coordinate = Coordinate.zero(),
    child: Optional[ItemizedResource] = None,
    backend: Optional[OpentronsThermocyclerBackend] = None,
    **_ignored,
  ):
    """Args:
    name:           Human-readable name.
    opentrons_id:   OT-API module “id” for your thermocycler.
    child_location: Position where a plate sits on the block.
    child:          Optional plate/rack already loaded on the module.
    """
    backend = backend or OpentronsThermocyclerBackend(opentrons_id=opentrons_id)
    super().__init__(
      name=name,
      size_x=244.95,  # mm - front/back footprint
      size_y=172.0,  # mm - left/right footprint
      size_z=170.35,  # mm - closed-lid height
      backend=backend,
      child_location=child_location,
      category="thermocycler",
      model="thermocyclerModuleV1",  # must match OT API “moduleModel”
    )

    self.backend = backend
    self.child = child
    if child is not None:
      self.assign_child_resource(child, location=child_location)

  def serialize(self) -> dict:
    """Return a serialized representation of the thermocycler."""
    return {
      **super().serialize(),
      "opentrons_id": cast(OpentronsThermocyclerBackend, self.backend).opentrons_id,
    }


class OpentronsThermocyclerModuleV2(Thermocycler, OTModule):
  """Opentrons Thermocycler GEN1/GEN2 wrapper.

  Dimensions (closed-lid): 244.95 mm (x) × 172 mm (y) × 170.35 mm (z).
  """

  def __init__(
    self,
    name: str,
    opentrons_id: str,
    child_location: Coordinate = Coordinate.zero(),
    child: Optional[ItemizedResource] = None,
    backend: Optional[OpentronsThermocyclerBackend] = None,
    **_ignored,
  ):
    """Args:
    name:           Human-readable name.
    opentrons_id:   OT-API module “id” for your thermocycler.
    child_location: Position where a plate sits on the block.
    child:          Optional plate/rack already loaded on the module.
    """
    backend = backend or OpentronsThermocyclerBackend(opentrons_id=opentrons_id)
    super().__init__(
      name=name,
      size_x=244.95,  # mm – front/back footprint
      size_y=172.0,  # mm – left/right footprint
      size_z=170.35,  # mm – closed-lid height
      backend=backend,
      child_location=child_location,
      category="thermocycler",
      model="thermocyclerModuleV2",  # must match OT API “moduleModel”
    )

    self.backend = backend
    self.child = child
    if child is not None:
      self.assign_child_resource(child, location=child_location)

  def serialize(self) -> dict:
    """Return a serialized representation of the thermocycler."""
    return {
      **super().serialize(),
      "opentrons_id": cast(OpentronsThermocyclerBackend, self.backend).opentrons_id,
    }
