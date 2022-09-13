# pylint: disable=unused-argument

from typing import List, Union, Optional

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.resources import (
  Coordinate,
  Plate,
  Resource,
  Lid,
  Tip,
)
from pylabrobot.liquid_handling.standard import (
  Aspiration,
  Dispense
)


class ChatterBoxBackend(LiquidHandlerBackend):
  """ Chatter box backend for 'How to Open Source' """

  def setup(self):
    print("Setting up the robot.")

  def stop(self):
    print("Stopping the robot.")

  def __enter__(self):
    self.setup()
    return self

  def __exit__(self, *exc):
    self.stop()
    return False

  def assigned_resource_callback(self, resource: Resource):
    print(f"Resource {resource.name} was assigned to the robot.")

  def unassigned_resource_callback(self, name: str):
    print(f"Resource {name} was unassigned from the robot.")

  def pickup_tips(self, *channels: List[Optional[Tip]], **backend_kwargs):
    print(f"Picking up tips {channels}.")

  def discard_tips(self, *channels: List[Optional[Tip]], **backend_kwargs):
    print(f"Discarding tips {channels}.")

  def aspirate(self, *channels: Optional[Aspiration], **backend_kwargs):
    print(f"Aspirating {channels}.")

  def dispense(self, *channels: Optional[Dispense], **backend_kwargs):
    print(f"Dispensing {channels}.")

  def pickup_tips96(self, resource: Resource, **backend_kwargs):
    print(f"Picking up tips from {resource}.")

  def discard_tips96(self, resource: Resource, **backend_kwargs):
    print(f"Discarding tips to {resource}.")

  def dispense96(
    self,
    resource: Resource,
    pattern: List[List[bool]],
    volume: float,
    **backend_kwargs
  ):
    print(f"Dispensing {volume} to {resource}.")

  def move_plate(self, plate: Plate, to: Union[Resource, Coordinate], **backend_kwargs):
    print(f"Moving plate {plate} to {to}.")

  def move_lid(self, lid: Lid, to: Union[Resource, Coordinate], **backend_kwargs):
    print(f"Moving lid {lid} to {to}.")
