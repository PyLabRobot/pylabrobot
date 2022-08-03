from pyhamilton.liquid_handling.backends import LiquidHandlerBackend


class Mock(LiquidHandlerBackend):
  """ A liquid handling backend that does absolutely nothing. """

  def __init__(self): pass
  def setup(self): pass
  def stop(self): pass

  def pickup_tips(self, *args, **kwargs): pass
  def discard_tips(self, *args, **kwargs): pass
  def aspirate(self, *args, **kwargs): pass
  def dispense(self, *args, **kwargs): pass

  def pickup_tips96(self, *args, **kwargs): pass
  def discard_tips96(self, *args, **kwargs): pass
  def aspirate96(self, *args, **kwargs): pass
  def dispense96(self, *args, **kwargs): pass

  def move_plate(self, *args, **kwargs): pass

  # def assigned_resource_callback(self, *args, **kwargs): pass
  # def unassigned_resource_callback(self, *args, **kwargs): pass
