import dataclasses
import enum
import logging
from typing import Set

import anyio

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)


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


class A4S(HasLifespan):
  """Driver for the Azenta a4S thermal sealer.

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

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Own the serial transport, reset the device, and disable its heater on exit."""
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io.lifespan())
    stack.push_shielded_async_callback(self._disable_heater, shield_timeout=self.timeout)
    await self.system_reset()
    logger.info("[A4S %s] connected and reset", self.port)

  async def _disable_heater(self) -> None:
    """Disable the heater while the serial transport remains open."""
    await self.set_heater(on=False)
    logger.info("[A4S %s] heater disabled", self.port)

  # -- serial protocol --

  async def send_command(self, command: str):
    await self.io.write(command.encode())
    await anyio.sleep(0.1)

  async def read_message(self) -> str:
    r, x = b"", b""
    has_read_r = False
    try:
      with anyio.fail_after(self.timeout):
        while True:
          if r:
            with anyio.move_on_after(1.0) as gap:
              x = await self.io.read()
            if gap.cancel_called:
              break
          else:
            x = await self.io.read()
          if has_read_r:
            r += x
          if x == b"\r":
            if not has_read_r:
              has_read_r = True
            else:
              break
    except TimeoutError:
      raise TimeoutError("Timeout while waiting for response") from None
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
      logger.error("[A4S %s] error code %d in status response: %s", self.port, error_code, message)
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

  def _status_matches(self, status: A4SStatus, statuses: Set[A4SStatus.SystemStatus]) -> bool:
    """Return whether a status reached its target, raising for a device error."""
    if status.system_status == A4SStatus.SystemStatus.error:
      logger.error("[A4S %s] device error: %d", self.port, status.error_code)
      raise RuntimeError(f"An error occurred: {status.error_code}")
    return status.system_status in statuses

  async def wait_for_status(self, statuses: Set[A4SStatus.SystemStatus]) -> A4SStatus:
    try:
      with anyio.fail_after(self.timeout):
        while True:
          status = await self.request_status()

          if self._status_matches(status, statuses):
            return status

          await anyio.sleep(0.01)
    except TimeoutError:
      raise TimeoutError("Timeout while waiting for response") from None

  async def wait_for_shuttle_open_sensor(
    self, shuttle_open: bool, timeout: float = 30.0
  ) -> A4SStatus:
    try:
      with anyio.fail_after(timeout):
        while True:
          status = await self.request_status()
          if status.sensor_status.shuttle_open_sensor == shuttle_open:
            return status
    except TimeoutError:
      raise TimeoutError("Timeout while waiting for shuttle open sensor") from None

  async def system_reset(self):
    await self.send_command("*00SR=zz!")
    return await self.wait_for_status({A4SStatus.SystemStatus.idle})

  async def _send_mutation_and_reconcile(self, command: str) -> A4SStatus:
    """Send a mutating command and finish its current response before cancellation."""
    await anyio.lowlevel.checkpoint()
    with anyio.CancelScope(shield=True):
      await self.send_command(command)
      status = await self.request_status()
    await anyio.lowlevel.checkpoint()
    return status

  async def set_heater(self, on: bool):
    command = "*00H1ZZ" if on else "*00H0ZZ"
    target = {A4SStatus.SystemStatus.idle}
    status = await self._send_mutation_and_reconcile(command)
    if self._status_matches(status, target):
      return status
    await anyio.sleep(0.01)
    return await self.wait_for_status(target)

  async def set_time(self, seconds: float):
    deciseconds = seconds * 10
    if not (0 <= deciseconds <= 9999):
      raise ValueError("Time out of range. Please enter a value between 0 and 9999.")
    command = f"*00DT={deciseconds:04d}zz!"
    return await self.send_command(command)

  async def request_remaining_time(self) -> int:
    status = await self.request_status()
    return status.remaining_time

  async def seal(self, temperature: int, duration: float):
    logger.info("[A4S %s] sealing at %d C for %.1fs", self.port, temperature, duration)
    await self.send_command(f"*00DH={round(temperature):04d}zz!")
    await self._wait_for_temperature(temperature, timeout=300)
    await self.set_time(duration)
    target = {A4SStatus.SystemStatus.single_cycle}
    status = await self._send_mutation_and_reconcile("*00GS=zz!")
    if not self._status_matches(status, target):
      await anyio.sleep(0.01)
      await self.wait_for_status(target)
    return await self.wait_for_status({A4SStatus.SystemStatus.idle, A4SStatus.SystemStatus.finish})

  async def open(self):
    logger.info("[A4S %s] open shuttle", self.port)
    await self.send_command("*00MO=zz!")
    return await self.wait_for_shuttle_open_sensor(True)

  async def close(self):
    logger.info("[A4S %s] close shuttle", self.port)
    await self.send_command("*00MC=zz!")
    return await self.wait_for_shuttle_open_sensor(False)

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    if not (50 <= temperature <= 200):
      raise ValueError("Temperature out of range. Please enter a value between 50 and 200.")
    logger.info("[A4S %s] setting temperature to %.1f C", self.port, temperature)
    command = f"*00DH={round(temperature):04d}zz!"
    await self.send_command(command)
    await self._wait_for_temperature(temperature, timeout=300)

  async def _wait_for_temperature(self, degrees: float, timeout: float, tolerance: float = 0.5):
    try:
      with anyio.fail_after(timeout):
        while True:
          current_temperature = await self.request_current_temperature()
          if abs(current_temperature - degrees) < tolerance:
            break
          await anyio.sleep(0.1)
    except TimeoutError:
      raise TimeoutError("Timeout while waiting for temperature") from None

  async def request_current_temperature(self) -> float:
    status = await self.request_status()
    temp = status.current_temperature
    logger.info("[A4S %s] read temperature: actual=%.1f C", self.port, temp)
    return temp

  async def deactivate(self):
    logger.info("[A4S %s] deactivate heater", self.port)
    await self._disable_heater()
