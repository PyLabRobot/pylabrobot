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

  def __init__(self, shaker_index:int, id_vendor: int = 0x8af, id_product: int = 0x8002) -> None:
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
    """
    If USBBackend.setup() fails, ensure that libusb drivers were installed
    for the HHS as per PyLabRobot documentation.
    """
    await USBBackend.setup(self)
    await self._initialize_lock()

  async def stop(self):
    await USBBackend.stop(self)

  def serialize(self) -> dict:
    usb_backend_serialized = USBBackend.serialize(self)
    heater_shaker_serialized = HeaterShakerBackend.serialize(self)
    return {**usb_backend_serialized, **heater_shaker_serialized, "shaker_index": self.shaker_index}

  def _send_command(self, command: str, **kwargs):
    assert len(command) == 2, "Command must be 2 characters long"
    args = "".join([f"{key}{value}" for key, value in kwargs.items()])
    USBBackend.write(
      self,
      f"T{self.shaker_index}{command}id{str(self.command_id).zfill(4)}{args}",
    )

    self.command_id = (self.command_id + 1) % 10_000
    return USBBackend.read(self)

  async def shake(
    self,
    speed: float = 800,
    direction: Literal[0,1] = 0,
    acceleration: int = 1_000,
  ):
    """
    speed: steps per second
    direction: 0 for positive, 1 for negative
    acceleration: increments per second
    """
    int_speed = int(speed)
    assert 20 <= int_speed <= 2_000, "Speed must be between 20 and 2_000"
    assert direction in [0, 1], "Direction must be 0 or 1"
    assert 500 <= acceleration <= 10_000, "Acceleration must be between 500 and 10_000"

    await self._start_shaking(direction=direction, speed=int_speed, acceleration=acceleration)

  async def stop_shaking(self):
    """ Shaker `stop_shaking` implementation. """
    await self._stop_shaking()
    await self._wait_for_stop()

  async def _move_plate_lock(self, position: PlateLockPosition):
    return self._send_command("LP", lp=position.value)

  async def lock_plate(self):
    await self._move_plate_lock(PlateLockPosition.LOCKED)

  async def unlock_plate(self):
    await self._move_plate_lock(PlateLockPosition.UNLOCKED)

  async def _initialize_lock(self):
    """ Firmware command initialize lock. """
    result = self._send_command("LI")
    return result

  async def _start_shaking(self, direction: int, speed: int, acceleration: int):
    """ Firmware command for starting shaking. """
    speed_str = str(speed).zfill(4)
    acceleration_str = str(acceleration).zfill(5)
    return self._send_command("SB", st=direction, sv=speed_str, sr=acceleration_str)

  async def _stop_shaking(self):
    """ Firmware command for stopping shaking. """
    return self._send_command("SC")

  async def _wait_for_stop(self):
    """ Firmware command for waiting for shaking to stop. """
    return self._send_command("SW")

  async def set_temperature(self, temperature: float):
    """set temperature in Celsius"""
    temp_str = f"{round(10*temperature):04d}"
    return self._send_command("TA", ta=temp_str)

  async def get_current_temperature(self) -> float:
    """get temperature in Celsius"""
    response = self._send_command("RT").decode("ascii")
    temp = str(response).split(" ")[1].strip("+")
    return float(temp) / 10

  async def deactivate(self):
    """turn off heating"""
    return self._send_command("TO")
