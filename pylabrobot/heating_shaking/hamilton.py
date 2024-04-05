from typing import Literal
from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.machines.backends.usb import USBBackend
from enum import Enum

class PlateLockPosition(Enum):
  LOCKED = 1
  UNLOCKED = 0

class HamiltonHeatShaker(HeaterShakerBackend, USBBackend):
  """
  Backend for Hamilton Heater Shaker devices connected through
  an Heat Shaker Box
  """

  def __init__(self, shaker_index:int, id_vendor:int = 0x8af, id_product:int = 0x8002) -> None:
    """
    Multiple Hamilton Heater Shakers can be connected to the same Heat Shaker Box. Each has A
    separate 'shaker index'
    """
    assert shaker_index >= 0, "Shaker index must be non-negative"
    self.shaker_index = shaker_index
    self.command_id = 0

    HeaterShakerBackend.__init__(self)
    USBBackend.__init__(self, id_vendor, id_product)

  async def setup(self):
    await USBBackend.setup(self)

  async def stop(self):
    await USBBackend.stop(self)

  def _send_command(self, command: str, **kwargs):
    assert len(command) == 2, "Command must be 2 characters long"
    args = "".join([f"{key}{value}" for key, value in kwargs.items()])
    USBBackend.write(
      self,
      f"T{self.shaker_index}{command}id{str(self.command_id).zfill(4)}{args}",
    )

    self.command_id = (self.command_id + 1) % 10_000
    return USBBackend.read(self)

  def _move_plate_lock(self, position: PlateLockPosition):
    self._send_command("lp", lp=position.value)

  async def shake(
    self,
    speed: float = 800,
    direction: Literal[0,1] = 0,
    acceleration: float = 1_000,
  ):
    """
    speed: steps per second
    direction: 0 for positive, 1 for negative
    acceleration: increments per second
    """
    assert 20 <= speed <= 2_000, "Speed must be between 20 and 2_000"
    assert direction in [0, 1], "Direction must be 0 or 1"
    assert 500 <= acceleration <= 10_000, "Acceleration must be between 500 and 10_000"

    # Initialize plate lock
    self._send_command("li")
    self._move_plate_lock(PlateLockPosition.UNLOCKED)

    speed_str = str(speed).zfill(4)
    acceleration_str = str(acceleration).zfill(5)
    self._send_command("sb", st=direction, sv=speed_str, sr=acceleration_str) # Start shaking

  async def stop_shaking(self):
    self._send_command("sc") # Stop shaking
    self._send_command("sw") # Wait for stop
    self._move_plate_lock(PlateLockPosition.LOCKED)
