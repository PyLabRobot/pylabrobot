# pylint: disable=unused-argument

from typing import List

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.resources import Resource, TipRack
from pylabrobot.liquid_handling.standard import (
  Aspiration,
  Dispense,
  Pickup,
  Drop,
  Move
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

  def pick_up_tips(self, ops: List[Pickup], use_channels: List[int], **backend_kwargs):
    print(f"Picking up tips {ops}.")

  def drop_tips(self, ops: List[Drop], use_channels: List[int], **backend_kwargs):
    print(f"Dropping tips {ops}.")

  def aspirate(self, ops: List[Aspiration], use_channels: List[int], **backend_kwargs):
    print(f"Aspirating {ops}.")

  def dispense(self, ops: List[Dispense], use_channels: List[int], **backend_kwargs):
    print(f"Dispensing {ops}.")

  def pick_up_tips96(self, tip_rack: TipRack, **backend_kwargs):
    print(f"Picking up tips from {tip_rack}.")

  def drop_tips96(self, tip_rack: TipRack, **backend_kwargs):
    print(f"Dropping tips to {tip_rack}.")

  def aspirate96(self, aspiration: Aspiration):
    print(f"Aspirating {aspiration.volume} from {aspiration.resource}.")

  def dispense96(self, dispense: Dispense):
    print(f"Dispensing {dispense.volume} to {dispense.resource}.")

  def move_resource(self, move: Move, **backend_kwargs):
    print(f"Moving {move}.")
