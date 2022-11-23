from typing import Any, Dict, List

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend


class SaverBackend(LiquidHandlerBackend):
  """ A backend that saves all commands received in a list, for testing purposes. """

  def __init__(self, num_channels: int, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.commands_received: List[Dict[str, Any]] = []
    self._num_channels = num_channels

  @property
  def num_channels(self) -> int:
    return self._num_channels

  def setup(self):
    super().setup()
    self.commands_received = []

  def send_command(self, command: str, data: Dict[str, Any]):
    self.commands_received.append(dict(command=command, data=data))

  def assigned_resource_callback(self, *args, **kwargs):
    self.commands_received.append(
      dict(command="assigned_resource_callback", args=args, kwargs=kwargs))

  def unassigned_resource_callback(self, *args, **kwargs):
    self.commands_received.append(
      dict(command="unassigned_resource_callback", args=args, kwargs=kwargs))

  def pick_up_tips(self, *args, **kwargs):
    self.commands_received.append(dict(command="pick_up_tips", args=args, kwargs=kwargs))

  def discard_tips(self, *args, **kwargs):
    self.commands_received.append(dict(command="discard_tips", args=args, kwargs=kwargs))

  def aspirate(self, *args, **kwargs):
    self.commands_received.append(dict(command="aspirate", args=args, kwargs=kwargs))

  def dispense(self, *args, **kwargs):
    self.commands_received.append(dict(command="dispense", args=args, kwargs=kwargs))

  def pick_up_tips96(self, *args, **kwargs):
    self.commands_received.append(dict(command="pick_up_tips96", args=args, kwargs=kwargs))

  def discard_tips96(self, *args, **kwargs):
    self.commands_received.append(dict(command="discard_tips96", args=args, kwargs=kwargs))

  def aspirate96(self, *args, **kwargs):
    self.commands_received.append(dict(command="aspirate96", args=args, kwargs=kwargs))

  def dispense96(self, *args, **kwargs):
    self.commands_received.append(dict(command="dispense96", args=args, kwargs=kwargs))

  def move_resource(self, *args, **kwargs):
    self.commands_received.append(dict(command="move_resource", args=args, kwargs=kwargs))

  # Saver specific methods

  def clear(self):
    self.commands_received = []

  def get_commands_for_event(self, event: str) -> List[Dict[str, Any]]:
    return [command for command in self.commands_received if command["command"] == event]
