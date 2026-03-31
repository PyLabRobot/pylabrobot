from __future__ import annotations

from abc import ABCMeta, abstractmethod

from pylabrobot.legacy.machines.backend import MachineBackend


class BulkDispenserBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract class for bulk dispenser backends.

  Volumes are specified in microliters (float). Concrete backends are responsible
  for converting to instrument-specific units.
  """

  @abstractmethod
  async def dispense(self) -> None:
    pass

  @abstractmethod
  async def prime(self, volume: float) -> None:
    pass

  @abstractmethod
  async def empty(self, volume: float) -> None:
    pass

  @abstractmethod
  async def shake(self, time: float, distance: int, speed: int) -> None:
    """Shake the plate.

    Args:
      time: Shake duration in seconds.
      distance: Shake distance in mm (1-5).
      speed: Shake frequency in Hz (1-20).
    """

  @abstractmethod
  async def move_plate_out(self) -> None:
    pass

  @abstractmethod
  async def set_plate_type(self, plate_type: int) -> None:
    pass

  @abstractmethod
  async def set_cassette_type(self, cassette_type: int) -> None:
    pass

  @abstractmethod
  async def set_column_volume(self, column: int, volume: float) -> None:
    """Set dispense volume for a column.

    Args:
      column: Column number (0 = all columns).
      volume: Volume in microliters.
    """

  @abstractmethod
  async def set_dispensing_height(self, height: int) -> None:
    """Set dispensing height.

    Args:
      height: Height in 1/100 mm (500-5500).
    """

  @abstractmethod
  async def set_pump_speed(self, speed: int) -> None:
    """Set pump speed as percentage of cassette range.

    Args:
      speed: Speed percentage (1-100).
    """

  @abstractmethod
  async def set_dispensing_order(self, order: int) -> None:
    """Set dispensing order.

    Args:
      order: 0 = row-wise, 1 = column-wise.
    """

  @abstractmethod
  async def abort(self) -> None:
    pass
