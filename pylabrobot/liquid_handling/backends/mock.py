""" Define mock backend. """

# pylint: disable=multiple-statements

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend


class Mock(LiquidHandlerBackend):
  """ A liquid handling backend that does absolutely nothing. """

  def pickup_tips(self, *args, **kwargs): pass
  def discard_tips(self, *args, **kwargs): pass
  def aspirate(self, *args, **kwargs): pass
  def dispense(self, *args, **kwargs): pass

  def pickup_tips96(self, *args, **kwargs): pass
  def discard_tips96(self, *args, **kwargs): pass
  def aspirate96(self, *args, **kwargs): pass
  def dispense96(self, *args, **kwargs): pass

  def move_plate(self, *args, **kwargs): pass
  def move_lid(self, *args, **kwargs): pass
