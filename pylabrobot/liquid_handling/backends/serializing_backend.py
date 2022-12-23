from abc import ABCMeta, abstractmethod
import sys
from typing import Any, Dict, Optional, List

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.resources import Resource, TipRack
from pylabrobot.liquid_handling.standard import (
  Pickup,
  Drop,
  Aspiration,
  Dispense,
  Move,
)

if sys.version_info >= (3, 8):
  from typing import TypedDict
else:
  from typing_extensions import TypedDict


class SerializingBackend(LiquidHandlerBackend, metaclass=ABCMeta):
  """ A backend that serializes all commands received, and sends them to `self.send_command` for
  processing. The implementation of `send_command` is left to the subclasses. """

  def __init__(self, num_channels: int):
    super().__init__()
    self._num_channels = num_channels

  @property
  def num_channels(self) -> int:
    return self._num_channels

  @abstractmethod
  def send_command(self, command: str, data: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    raise NotImplementedError

  def setup(self):
    self.send_command(command="setup")

  def stop(self):
    self.send_command(command="stop")

  def assigned_resource_callback(self, resource: Resource):
    self.send_command(command="resource_assigned", data=dict(resource=resource.serialize(),
      parent_name=(resource.parent.name if resource.parent else None)))

  def unassigned_resource_callback(self, name: str):
    self.send_command(command="resource_unassigned", data=dict(resource_name=name))

  def pick_up_tips(self, ops: List[Pickup], use_channels: List[int]):
    serialized = [op.serialize() for op in ops]
    self.send_command(
      command="pick_up_tips",
      data=dict(channels=serialized, use_channels=use_channels))

  def drop_tips(self, ops: List[Drop], use_channels: List[int]):
    serialized = [op.serialize() for op in ops]
    self.send_command(
      command="drop_tips",
      data=dict(channels=serialized, use_channels=use_channels))

  def aspirate(self, ops: List[Aspiration], use_channels: List[int]):
    serialized = [op.serialize() for op in ops]
    self.send_command(
      command="aspirate",
      data=dict(channels=serialized, use_channels=use_channels))

  def dispense(self, ops: List[Dispense], use_channels: List[int]):
    serialized = [op.serialize() for op in ops]
    self.send_command(
      command="dispense",
      data=dict(channels=serialized, use_channels=use_channels))

  def pick_up_tips96(self, tip_rack: TipRack):
    self.send_command(command="pick_up_tips96", data=dict(resource_name=tip_rack.name))

  def drop_tips96(self, tip_rack: TipRack):
    self.send_command(command="drop_tips96", data=dict(resource_name=tip_rack.name))

  def aspirate96(self, aspiration: Aspiration):
    self.send_command(command="aspirate96", data=dict(aspiration=aspiration.serialize()))

  def dispense96(self, dispense: Dispense):
    self.send_command(command="dispense96", data=dict(dispense=dispense.serialize()))

  def move_resource(self, move: Move, **backend_kwargs):
    self.send_command(command="move", data=dict(move=move.serialize()), **backend_kwargs)


class SerializingSavingBackend(SerializingBackend):
  """ A backend that saves all serialized commands in `self.sent_commands`, wrote for testing. """

  class Command(TypedDict):
    command: str
    data: Optional[Dict[str, Any]]

  def setup(self):
    self.sent_commands: List[SerializingSavingBackend.Command] = []
    self.setup_finished = True

  def stop(self):
    self.setup_finished = False

  def send_command(self, command: str, data: Optional[Dict[str, Any]] = None):
    self.sent_commands.append(dict(command=command, data=data))

  def clear(self):
    self.sent_commands = []

  def get_first_data_for_command(self, command: str) -> Optional[Dict[str, Any]]:
    for sent_command in self.sent_commands:
      if sent_command["command"] == command:
        return sent_command["data"]
    return None
