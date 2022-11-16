from abc import ABCMeta, abstractmethod
from typing import Any, Dict, Optional

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.standard import (
  Pickup,
  Discard,
  Aspiration,
  Dispense,
  Move,
)


class SerializingBackend(LiquidHandlerBackend, metaclass=ABCMeta):
  """ A backend that serializes all commands received, and sends them to `self.send_command` for
  processing. The implementation of `send_command` is left to the subclasses. """

  @abstractmethod
  def send_command(self, command: str, data: Dict[str, Any] = None):
    raise NotImplementedError

  def setup(self):
    self.send_command(command="setup")

  def stop(self):
    self.send_command(command="stop")

  def assigned_resource_callback(self, resource):
    self.send_command(command="resource_assigned", data=dict(resource=resource.serialize(),
      parent_name=(resource.parent.name if resource.parent else None)))

  def unassigned_resource_callback(self, name):
    self.send_command(command="resource_unassigned", data=dict(resource_name=name))

  def pick_up_tips(self, *channels: Optional[Pickup]):
    channels = [channel.serialize() if channel is not None else None for channel in channels]
    self.send_command(command="pick_up_tips", data=dict(channels=channels))

  def discard_tips(self, *channels: Optional[Discard]):
    channels = [channel.serialize() if channel is not None else None for channel in channels]
    self.send_command(command="discard_tips", data=dict(channels=channels))

  def aspirate(self, *channels: Optional[Aspiration]):
    channels = [channel.serialize() for channel in channels]
    self.send_command(command="aspirate", data=dict(channels=channels))

  def dispense(self, *channels: Optional[Dispense]):
    channels = [channel.serialize() for channel in channels]
    self.send_command(command="dispense", data=dict(channels=channels))

  def pick_up_tips96(self, resource):
    self.send_command(command="pick_up_tips96", data=dict(resource=resource.serialize()))

  def discard_tips96(self, resource):
    self.send_command(command="discard_tips96", data=dict(resource=resource.serialize()))

  def aspirate96(self, aspiration: Aspiration):
    self.send_command(command="aspirate96", data=dict(aspiration=aspiration.serialize()))

  def dispense96(self, dispense: Dispense):
    self.send_command(command="dispense96", data=dict(dispense=dispense.serialize()))

  def move_resource(self, move: Move, **backend_kwargs):
    self.send_command(command="move", data=dict(move=move.serialize()), **backend_kwargs)


class SerializingSavingBackend(SerializingBackend):
  """ A backend that saves all serialized commands in `self.sent_commands`, wrote for testing. """

  def setup(self):
    self.sent_commands = []
    self.setup_finished = True

  def stop(self):
    self.setup_finished = False

  def send_command(self, command: str, data: Dict[str, Any] = None):
    self.sent_commands.append(dict(command=command, data=data))

  def clear(self):
    self.sent_commands = []

  def get_first_data_for_command(self, command: str):
    for sent_command in self.sent_commands:
      if sent_command["command"] == command:
        return sent_command["data"]
    return None
