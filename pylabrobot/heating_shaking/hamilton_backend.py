import abc
import time
import warnings
from enum import Enum
from typing import Dict, Literal, Optional

from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.io.usb import USB


class PlateLockPosition(Enum):
  LOCKED = 1
  UNLOCKED = 0


class HamiltonHeaterShakerInterface(abc.ABC):
  """Either a control box or a STAR: the api is the same"""

  @abc.abstractmethod
  async def send_hhs_command(self, index: int, command: str, **kwargs) -> str:
    pass


class HamiltonHeaterShakerBox(HamiltonHeaterShakerInterface):
  def __init__(
    self,
    id_vendor: int = 0x8AF,
    id_product: int = 0x8002,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
  ):
    self.io = USB(
      id_vendor=id_vendor,
      id_product=id_product,
      device_address=device_address,
      serial_number=serial_number,
    )
    self._id = 0

  def _generate_id(self) -> int:
    """continuously generate unique ids 0 <= x < 10000."""
    self._id += 1
    return self._id % 10000

  async def setup(self):
    """
    If io.setup() fails, ensure that libusb drivers were installed for the HHS as per docs.
    """
    await self.io.setup()

  async def stop(self):
    await self.io.stop()

  async def send_hhs_command(self, index: int, command: str, **kwargs) -> str:
    args = "".join([f"{key}{value}" for key, value in kwargs.items()])
    id_ = str(self._generate_id()).zfill(4)
    await self.io.write(f"T{index}{command}id{id_}{args}".encode())
    return (await self.io.read()).decode("utf-8")


class HamiltonHeaterShakerBackend(HeaterShakerBackend):
  """Backend for Hamilton Heater Shaker devices connected through an Heater Shaker Box"""

  @property
  def supports_active_cooling(self) -> bool:
    return False

  def __init__(self, index: int, interface: HamiltonHeaterShakerInterface) -> None:
    """
    Multiple Hamilton Heater Shakers can be connected to the same Heat Shaker Box. Each has A
    unique 'shaker index'
    """
    assert index >= 0, "Shaker index must be non-negative"
    self.index = index

    super().__init__()
    self.interface = interface

  async def setup(self):
    """
    If io.setup() fails, ensure that libusb drivers were installed for the HHS as per docs.
    """
    await self._initialize_lock()
    await self._initialize_shaker_drive()

  async def stop(self):
    pass

  def serialize(self) -> dict:
    warnings.warn("The interface is not serialized.")

    heater_shaker_serialized = HeaterShakerBackend.serialize(self)
    return {
      **heater_shaker_serialized,
      "index": self.index,
      "interface": None,  # TODO: implement serialization
    }

  async def shake(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    """
    if the plate is not locked, it will be locked.

    speed: steps per second
    direction: 0 for positive, 1 for negative
    acceleration: increments per second
    """

    await self.lock_plate()

    int_speed = int(speed)
    assert 20 <= int_speed <= 2_000, "Speed must be between 20 and 2_000"
    assert direction in [0, 1], "Direction must be 0 or 1"
    assert 500 <= acceleration <= 10_000, "Acceleration must be between 500 and 10_000"

    now = time.time()
    while True:
      await self._start_shaking(direction=direction, speed=int_speed, acceleration=acceleration)
      if await self.get_is_shaking():
        break
      if timeout is not None and time.time() - now > timeout:
        raise TimeoutError("Failed to start shaking within timeout")

  async def stop_shaking(self):
    await self._stop_shaking()
    await self._wait_for_stop()

  async def get_is_shaking(self) -> bool:
    response = await self.interface.send_hhs_command(index=self.index, command="RD")
    return response.endswith("1")  # type: ignore[no-any-return] # what

  async def _move_plate_lock(self, position: PlateLockPosition):
    return await self.interface.send_hhs_command(index=self.index, command="LP", lp=position.value)

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    await self._move_plate_lock(PlateLockPosition.LOCKED)

  async def unlock_plate(self):
    await self._move_plate_lock(PlateLockPosition.UNLOCKED)

  async def _initialize_lock(self):
    """Firmware command initialize lock."""
    return await self.interface.send_hhs_command(index=self.index, command="LI")

  async def _initialize_shaker_drive(self):
    """Initialize the shaker drive, homing to absolute position 0"""
    return await self.interface.send_hhs_command(index=self.index, command="SI")

  async def _start_shaking(self, direction: int, speed: int, acceleration: int):
    """Firmware command for starting shaking."""
    speed_str = str(speed).zfill(4)
    acceleration_str = str(acceleration).zfill(5)
    return await self.interface.send_hhs_command(
      index=self.index, command="SB", st=direction, sv=speed_str, sr=acceleration_str
    )

  async def _stop_shaking(self):
    """Firmware command for stopping shaking."""
    return await self.interface.send_hhs_command(index=self.index, command="SC")

  async def _wait_for_stop(self):
    """Firmware command for waiting for shaking to stop."""
    return await self.interface.send_hhs_command(index=self.index, command="SW")

  async def set_temperature(self, temperature: float):
    """set temperature in Celsius"""
    assert 0 < temperature <= 105
    temp_str = f"{round(10*temperature):04d}"
    return await self.interface.send_hhs_command(index=self.index, command="TA", ta=temp_str)

  async def _get_current_temperature(self) -> Dict[str, float]:
    """get temperature in Celsius"""
    response = await self.interface.send_hhs_command(index=self.index, command="RT")
    response = response.split("rt")[1]
    middle_temp = float(str(response).split(" ")[0].strip("+")) / 10
    edge_temp = float(str(response).split(" ")[1].strip("+")) / 10
    return {"middle": middle_temp, "edge": edge_temp}

  async def get_current_temperature(self) -> float:
    """get temperature in Celsius"""
    response = await self._get_current_temperature()
    return response["middle"]

  async def get_edge_temperature(self) -> float:
    """get temperature in Celsius"""
    response = await self._get_current_temperature()
    return response["edge"]

  async def deactivate(self):
    """turn off heating"""
    return await self.interface.send_hhs_command(index=self.index, command="TO")
