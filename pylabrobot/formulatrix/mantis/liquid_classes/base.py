from pylabrobot.serializer import SerializableMixin


class MantisLiquidClass(SerializableMixin):
  """Dispense tuning for one viscosity band on one chip type.

  The diaphragm delays, and the pressure and vacuum of each step.
  """

  def __init__(
    self,
    *,
    large_fill_delay: int,
    large_empty_delay: int,
    small_fill_delay: int,
    small_empty_delay: int,
    recovery_fill_delay: int,
    recovery_empty_delay: int,
    chip_pressure: float,
    bottle_pressure: float,
    vacuum: float,
    prime_pressure: float,
    prime_vacuum: float,
    wash_pressure: float,
    wash_vacuum: float,
    recovery_pressure: float,
    recovery_vacuum: float,
  ):
    """
    Args:
      large_fill_delay: the large diaphragm's fill delay.
      large_empty_delay: the large diaphragm's empty delay.
      small_fill_delay: the small diaphragm's fill delay.
      small_empty_delay: the small diaphragm's empty delay.
      recovery_fill_delay: the fill delay while drawing reagent back to the input.
      recovery_empty_delay: the empty delay while drawing reagent back to the input.
      chip_pressure: the chip's pressure while dispensing.
      bottle_pressure: the bottle's pressure while dispensing.
      vacuum: the vacuum while dispensing.
      prime_pressure: the chip's pressure while priming.
      prime_vacuum: the vacuum while priming.
      wash_pressure: the chip's pressure while washing.
      wash_vacuum: the vacuum while washing.
      recovery_pressure: the chip's pressure while recovering reagent.
      recovery_vacuum: the vacuum while recovering reagent.
    """
    self.large_fill_delay = large_fill_delay
    self.large_empty_delay = large_empty_delay
    self.small_fill_delay = small_fill_delay
    self.small_empty_delay = small_empty_delay
    self.recovery_fill_delay = recovery_fill_delay
    self.recovery_empty_delay = recovery_empty_delay
    self.chip_pressure = chip_pressure
    self.bottle_pressure = bottle_pressure
    self.vacuum = vacuum
    self.prime_pressure = prime_pressure
    self.prime_vacuum = prime_vacuum
    self.wash_pressure = wash_pressure
    self.wash_vacuum = wash_vacuum
    self.recovery_pressure = recovery_pressure
    self.recovery_vacuum = recovery_vacuum
