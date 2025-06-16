import asyncio
import dataclasses
import enum
import time
from typing import Optional, Set

import serial

from pylabrobot.io.serial import Serial
from pylabrobot.sealing.backend import SealerBackend


class A4SBackend(SealerBackend):
  def __init__(self, port: str, timeout=20) -> None:
    super().__init__()
    self.port = port
    self.timeout = timeout
    self.io = Serial(
      port=self.port,
      baudrate=19200,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
    )

  async def setup(self):
    await self.io.setup()
    await self.system_reset()

  async def stop(self):
    await self.set_heater(on=False)
    await self.io.stop()

  async def set_heater(self, on: bool):
    """Set the heater on or off."""
    command = "*00H1ZZ" if on else "*00H0ZZ"
    await self.send_command(command)
    return await self._wait_for_status({A4SBackend.Status.SystemStatus.idle})

  @dataclasses.dataclass
  class Status:
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
      # no_connect: bool

    current_temperature: float
    system_status: SystemStatus
    heater_block_status: HeaterBlockStatus
    error_code: int
    warning_code: int
    sensor_status: SensorStatus
    remaining_time: int

  async def _read_message(self) -> str:
    """read a message. we are not sure what format it is."""
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

  async def get_status(self) -> Status:
    # read until we get a system status message
    message: str
    while True:
      message = await self._read_message()
      if message[1] == "T":  # read system status
        break
      # message[1] == b"D": # Operation Status Message Format
      # message[1] == b"Y": # Response of Command Accepted Message Format
      # message[1] == b"N": # Response of Command Rejected Message Format
      # message[1] == b"X": # Communication Busy

    # parsing response
    message = message.split("!")[0]
    parameters = message[:-4].split("=")[1].split(",")

    error_code = int(str(parameters[3]))  # 0 is good
    if error_code != 0:
      raise RuntimeError(f"An error occurred: response {message}")

    sensor_status = int(str(parameters[5]))

    return A4SBackend.Status(
      current_temperature=int(str(parameters[0])) / 10,
      system_status=A4SBackend.Status.SystemStatus(int(str(parameters[1]))),
      heater_block_status=A4SBackend.Status.HeaterBlockStatus(int(str(parameters[2]))),
      error_code=error_code,
      warning_code=int(str(parameters[4])),
      sensor_status=A4SBackend.Status.SensorStatus(
        shuttle_middle_sensor=sensor_status & 0x0001 != 0,
        shuttle_open_sensor=sensor_status & 0x0002 != 0,
        shuttle_close_sensor=sensor_status & 0x0004 != 0,
        clean_door_sensor=sensor_status & 0x0008 != 0,
        seal_roll_sensor=sensor_status & 0x0010 != 0,
        heater_motor_up_sensor=sensor_status & 0x0020 != 0,
        heater_motor_down_sensor=sensor_status & 0x0040 != 0,
        # no_connect = sensor_status & 0x0080 != 0,
      ),
      remaining_time=int(str(parameters[6])),
    )

  async def _wait_for_status(self, statuses: Set["A4SBackend.Status.SystemStatus"]) -> Status:
    start = time.time()
    while True:
      status = await self.get_status()

      if status.system_status == A4SBackend.Status.SystemStatus.error:
        raise RuntimeError(f"An error occurred: {status.error_code}")

      if status.system_status in statuses:
        return status

      if time.time() - start > self.timeout:
        raise TimeoutError("Timeout while waiting for response")

      await asyncio.sleep(0.01)

  async def send_command(self, command: str):
    # command accepted: *Y01PL!
    # Command index: 01
    await self.io.write(command.encode())
    await asyncio.sleep(0.1)

  async def seal(self, temperature: int, duration: float):
    await self.set_temperature(temperature)
    await self.set_time(duration)
    await self.send_command("*00GS=zz!")  # Command to conduct seal action
    await self._wait_for_status({A4SBackend.Status.SystemStatus.single_cycle})
    return await self._wait_for_status(
      {A4SBackend.Status.SystemStatus.idle, A4SBackend.Status.SystemStatus.finish}
    )

  async def _wait_for_temperature(self, degrees: float, timeout: float, tolerance: float = 0.5):
    start = time.time()
    while True:
      current_temperature = await self.get_temperature()
      if abs(current_temperature - degrees) < tolerance:
        break
      if time.time() - start > timeout:
        raise TimeoutError("Timeout while waiting for temperature")
      await asyncio.sleep(0.1)

  async def _wait_for_shuttle_open_sensor(self, shuttle_open: bool, timeout: float = 30.0) -> Status:
    start = time.time()
    while True:
      status = await self.get_status()
      if status.sensor_status.shuttle_open_sensor == shuttle_open:
        return status
      if time.time() - start > timeout:
        raise TimeoutError("Timeout while waiting for shuttle open sensor")

  async def set_temperature(self, temperature: float):
    if not (50 <= temperature <= 200):
      raise ValueError("Temperature out of range. Please enter a value between 50 and 200.")
    command = f"*00DH={round(temperature):04d}zz!"
    await self.send_command(command)
    await self._wait_for_temperature(temperature, timeout=300)

  async def set_time(self, seconds: float):
    deciseconds = seconds * 10
    if not (0 <= deciseconds <= 9999):
      raise ValueError("Time out of range. Please enter a value between 0 and 9999.")
    command = f"*00DT={deciseconds:04d}zz!"
    return await self.send_command(command)

  async def open(self) -> Status:
    await self.send_command("*00MO=zz!")
    return await self._wait_for_shuttle_open_sensor(True)

  async def close(self) -> Status:
    await self.send_command("*00MC=zz!")
    return await self._wait_for_shuttle_open_sensor(False)

  async def system_reset(self):
    await self.send_command("*00SR=zz!")
    return await self._wait_for_status({A4SBackend.Status.SystemStatus.idle})

  async def get_temperature(self) -> float:
    status = await self.get_status()
    return status.current_temperature

  async def get_remaining_time(self) -> int:
    status = await self.get_status()
    return status.remaining_time
