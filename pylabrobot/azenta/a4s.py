import asyncio
import dataclasses
import enum
import time
from typing import Optional, Set

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.capabilities.sealing import Sealer, SealerBackend
from pylabrobot.capabilities.temperature_controlling import (
  TemperatureController,
  TemperatureControllerBackend,
)
from pylabrobot.device import Device, Driver
from pylabrobot.io.serial import Serial
from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import PlateHolder


@dataclasses.dataclass
class A4SStatus:
  class SystemStatus(enum.Enum):
    idle = 0
    single_cycle = 1
    repeat_cycle = 2
    error = 3
    finish = 4

  class HeaterBlockStatus(enum.Enum):
    heater_off = 0
    ready = 1
    heating = 2
    cooling = 3
    converging = 4

  @dataclasses.dataclass
  class SensorStatus:
    shuttle_middle_sensor: bool
    shuttle_open_sensor: bool
    shuttle_close_sensor: bool
    clean_door_sensor: bool
    seal_roll_sensor: bool
    heater_motor_up_sensor: bool
    heater_motor_down_sensor: bool

  current_temperature: float
  system_status: SystemStatus
  heater_block_status: HeaterBlockStatus
  error_code: int
  warning_code: int
  sensor_status: SensorStatus
  remaining_time: int


class A4SDriver(Driver):
  """Serial driver for the Azenta a4S thermal sealer.

  Owns I/O, connection lifecycle, and device-level operations (status polling,
  system reset, heater on/off, timing).

  https://web.azenta.com/hubfs/azenta-files/resources/tech-drawings/TD-automated-roll-heat-sealer.pdf
  """

  def __init__(self, port: str, timeout: int = 20) -> None:
    super().__init__()
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    self.port = port
    self.timeout = timeout
    self.io = Serial(
      human_readable_device_name="Azenta a4S Thermal Sealer",
      port=self.port,
      baudrate=19200,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
    )

  async def setup(self):
    await super().setup()  # type: ignore[safe-super]
    await self.io.setup()
    await self.system_reset()

  async def stop(self):
    await self.set_heater(on=False)
    await super().stop()  # type: ignore[safe-super]
    await self.io.stop()

  # -- serial protocol --

  async def send_command(self, command: str):
    await self.io.write(command.encode())
    await asyncio.sleep(0.1)

  async def read_message(self) -> str:
    start = time.time()
    r, x = b"", b""
    has_read_r = False
    while x != b"" or (len(r) == 0 and x == b""):
      x = await self.io.read()
      if has_read_r:
        r += x
      if x == b"\r":
        if not has_read_r:
          has_read_r = True
        else:
          break
      if time.time() - start > self.timeout:
        raise TimeoutError("Timeout while waiting for response")
    return r.decode("utf-8")

  # -- status --

  async def request_status(self) -> A4SStatus:
    while True:
      message = await self.read_message()
      if message[1] == "T":
        break

    message = message.split("!")[0]
    parameters = message[:-4].split("=")[1].split(",")

    error_code = int(str(parameters[3]))
    if error_code != 0:
      raise RuntimeError(f"An error occurred: response {message}")

    sensor_status = int(str(parameters[5]))

    return A4SStatus(
      current_temperature=int(str(parameters[0])) / 10,
      system_status=A4SStatus.SystemStatus(int(str(parameters[1]))),
      heater_block_status=A4SStatus.HeaterBlockStatus(int(str(parameters[2]))),
      error_code=error_code,
      warning_code=int(str(parameters[4])),
      sensor_status=A4SStatus.SensorStatus(
        shuttle_middle_sensor=sensor_status & 0x0001 != 0,
        shuttle_open_sensor=sensor_status & 0x0002 != 0,
        shuttle_close_sensor=sensor_status & 0x0004 != 0,
        clean_door_sensor=sensor_status & 0x0008 != 0,
        seal_roll_sensor=sensor_status & 0x0010 != 0,
        heater_motor_up_sensor=sensor_status & 0x0020 != 0,
        heater_motor_down_sensor=sensor_status & 0x0040 != 0,
      ),
      remaining_time=int(str(parameters[6])),
    )

  async def wait_for_status(self, statuses: Set[A4SStatus.SystemStatus]) -> A4SStatus:
    start = time.time()
    while True:
      status = await self.request_status()

      if status.system_status == A4SStatus.SystemStatus.error:
        raise RuntimeError(f"An error occurred: {status.error_code}")

      if status.system_status in statuses:
        return status

      if time.time() - start > self.timeout:
        raise TimeoutError("Timeout while waiting for response")

      await asyncio.sleep(0.01)

  async def wait_for_shuttle_open_sensor(
    self, shuttle_open: bool, timeout: float = 30.0
  ) -> A4SStatus:
    start = time.time()
    while True:
      status = await self.request_status()
      if status.sensor_status.shuttle_open_sensor == shuttle_open:
        return status
      if time.time() - start > timeout:
        raise TimeoutError("Timeout while waiting for shuttle open sensor")

  async def system_reset(self):
    await self.send_command("*00SR=zz!")
    return await self.wait_for_status({A4SStatus.SystemStatus.idle})

  async def set_heater(self, on: bool):
    command = "*00H1ZZ" if on else "*00H0ZZ"
    await self.send_command(command)
    return await self.wait_for_status({A4SStatus.SystemStatus.idle})

  async def set_time(self, seconds: float):
    deciseconds = seconds * 10
    if not (0 <= deciseconds <= 9999):
      raise ValueError("Time out of range. Please enter a value between 0 and 9999.")
    command = f"*00DT={deciseconds:04d}zz!"
    return await self.send_command(command)

  async def request_remaining_time(self) -> int:
    status = await self.request_status()
    return status.remaining_time

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.port, "timeout": self.timeout}


class A4SSealerBackend(SealerBackend):
  """Translates SealerBackend operations into A4S driver commands."""

  def __init__(self, driver: A4SDriver):
    self.driver = driver

  async def seal(self, temperature: int, duration: float):
    await self.driver.send_command(f"*00DH={round(temperature):04d}zz!")
    await self._wait_for_temperature(temperature, timeout=300)
    await self.driver.set_time(duration)
    await self.driver.send_command("*00GS=zz!")
    await self.driver.wait_for_status({A4SStatus.SystemStatus.single_cycle})
    return await self.driver.wait_for_status(
      {A4SStatus.SystemStatus.idle, A4SStatus.SystemStatus.finish}
    )

  async def open(self):
    await self.driver.send_command("*00MO=zz!")
    return await self.driver.wait_for_shuttle_open_sensor(True)

  async def close(self):
    await self.driver.send_command("*00MC=zz!")
    return await self.driver.wait_for_shuttle_open_sensor(False)

  async def _wait_for_temperature(self, degrees: float, timeout: float, tolerance: float = 0.5):
    start = time.time()
    while True:
      status = await self.driver.request_status()
      if abs(status.current_temperature - degrees) < tolerance:
        break
      if time.time() - start > timeout:
        raise TimeoutError("Timeout while waiting for temperature")
      await asyncio.sleep(0.1)


class A4STemperatureBackend(TemperatureControllerBackend):
  """Translates TemperatureControllerBackend operations into A4S driver commands."""

  def __init__(self, driver: A4SDriver):
    self.driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    if not (50 <= temperature <= 200):
      raise ValueError("Temperature out of range. Please enter a value between 50 and 200.")
    command = f"*00DH={round(temperature):04d}zz!"
    await self.driver.send_command(command)
    await self._wait_for_temperature(temperature, timeout=300)

  async def _wait_for_temperature(self, degrees: float, timeout: float, tolerance: float = 0.5):
    start = time.time()
    while True:
      current_temperature = await self.request_current_temperature()
      if abs(current_temperature - degrees) < tolerance:
        break
      if time.time() - start > timeout:
        raise TimeoutError("Timeout while waiting for temperature")
      await asyncio.sleep(0.1)

  async def request_current_temperature(self) -> float:
    status = await self.driver.request_status()
    return status.current_temperature

  async def deactivate(self):
    await self.driver.set_heater(on=False)


class A4S(PlateHolder, Device):
  """Azenta a4S automated thermal sealer.

  222 x 500 x 276 mm
  """

  def __init__(
    self,
    name: str,
    port: str,
    timeout: int = 20,
    size_x: float = 222,
    size_y: float = 500,
    size_z: float = 276,
    child_location: Coordinate = Coordinate(0, 0, 0),  # TODO
    pedestal_size_z: float = 0,  # TODO
    category: str = "sealer",
    model: Optional[str] = None,
  ):
    raise NotImplementedError("A4S is missing resource definition.")
    driver = A4SDriver(port=port, timeout=timeout)
    PlateHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      pedestal_size_z=pedestal_size_z,
      category=category,
      model=model,
    )
    Device.__init__(self, driver=driver)
    self.driver: A4SDriver = driver
    self.sealer = Sealer(backend=A4SSealerBackend(driver))
    self.tc = TemperatureController(backend=A4STemperatureBackend(driver))
    self._capabilities = [self.tc, self.sealer]

  def serialize(self) -> dict:
    return {
      **Device.serialize(self),
      **PlateHolder.serialize(self),
    }
