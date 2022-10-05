from pylabrobot.liquid_handling.backends import LiquidHandlerBackend


class SaverBackend(LiquidHandlerBackend):
  """ A backend that saves all commands received in a list, for testing purposes.

  TODO: This class is suspiciously similar to the WebSocketBackendEventCatcher, we should probably
  merge them.
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.commands_received = []

  def setup(self):
    super().setup()
    self.commands_received = []

  def clear(self):
    self.commands_received = []

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

  def move_plate(self, *args, **kwargs):
    self.commands_received.append(dict(command="move_plate", args=args, kwargs=kwargs))

  def move_lid(self, *args, **kwargs):
    self.commands_received.append(dict(command="move_lid", args=args, kwargs=kwargs))
