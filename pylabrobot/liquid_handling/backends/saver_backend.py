from typing import Any, Dict, List

from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend


class SaverBackend(LiquidHandlerBackend):
  """ A backend that saves all commands received in a list, for testing purposes. """

  def __init__(self, num_channels: int, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.commands_received: List[Dict[str, Any]] = []
    self._num_channels = num_channels

  @property
  def num_channels(self) -> int:
    return self._num_channels

  async def setup(self):
    await super().setup()
    self.commands_received = []

  async def stop(self):
    await super().stop()

  async def send_command(self, command: str, data: Dict[str, Any]):
    self.commands_received.append({"command": command, "data": data})

  async def assigned_resource_callback(self, *args, **kwargs):
    self.commands_received.append(
      {"command": "assigned_resource_callback", "args": args, "kwargs": kwargs})

  async def unassigned_resource_callback(self, *args, **kwargs):
    self.commands_received.append(
      {"command": "unassigned_resource_callback", "args": args, "kwargs": kwargs})

  async def pick_up_tips(self, *args, **kwargs):
    self.commands_received.append({"command": "pick_up_tips", "args": args, "kwargs": kwargs})

  async def drop_tips(self, *args, **kwargs):
    self.commands_received.append({"command": "drop_tips", "args": args, "kwargs": kwargs})

  async def aspirate(self, *args, **kwargs):
    self.commands_received.append({"command": "aspirate", "args": args, "kwargs": kwargs})

  async def dispense(self, *args, **kwargs):
    self.commands_received.append({"command": "dispense", "args": args, "kwargs": kwargs})

  async def pick_up_tips96(self, *args, **kwargs):
    self.commands_received.append({"command": "pick_up_tips96", "args": args, "kwargs": kwargs})

  async def drop_tips96(self, *args, **kwargs):
    self.commands_received.append({"command": "drop_tips96", "args": args, "kwargs": kwargs})

  async def aspirate96(self, *args, **kwargs):
    self.commands_received.append({"command": "aspirate96", "args": args, "kwargs": kwargs})

  async def dispense96(self, *args, **kwargs):
    self.commands_received.append({"command": "dispense96", "args": args, "kwargs": kwargs})

  async def move_resource(self, *args, **kwargs):
    self.commands_received.append({"command": "move_resource", "args": args, "kwargs": kwargs})

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "num_channels": self.num_channels,
    }

  # Saver specific methods

  def clear(self):
    self.commands_received = []

  def get_commands_for_event(self, event: str) -> List[Dict[str, Any]]:
    return [command for command in self.commands_received if command["command"] == event]
