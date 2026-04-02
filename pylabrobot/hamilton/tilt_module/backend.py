import re
from typing import Optional

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.capabilities.tilting.backend import TilterBackend, TiltModuleError
from pylabrobot.device import Driver
from pylabrobot.io.serial import Serial


class HamiltonTiltModuleDriver(Driver):
  """Serial driver for the Hamilton tilt module.

  Owns the hardware connection. Knows how to send bytes on the wire.
  """

  def __init__(
    self,
    com_port: str,
    write_timeout: float = 10,
    timeout: float = 10,
  ):
    if not HAS_SERIAL:
      raise RuntimeError(
        f"pyserial is required for the Hamilton tilt module backend. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )

    super().__init__()
    self.com_port = com_port
    self.io = Serial(
      port=self.com_port,
      baudrate=1200,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=write_timeout,
      timeout=timeout,
      human_readable_device_name="Hamilton Tilt Module",
    )

  async def setup(self):
    await self.io.setup()

  async def stop(self):
    await self.io.stop()

  async def send_command(self, command: str, parameter: Optional[str] = None) -> str:
    """Send a command to the tilt module."""

    if parameter is None:
      parameter = ""

    await self.io.write(f"99{command}{parameter}\r\n".encode("utf-8"))
    resp = ""
    while not resp.startswith("T1" + command):
      resp = (await self.io.read(128)).decode("utf-8")

    # Check for error.
    error_matches = re.search("er[0-9]{2}", resp)
    if error_matches is not None:
      err_code = int(error_matches.group(0)[2:])
      if 1 <= err_code <= 7:
        raise TiltModuleError(
          {
            1: "Init Position not found",
            2: "**Step** loss",
            3: "Not initialized",
            5: "Stepper Motor end stage defective",
            6: "Parameter out **of** Range",
            7: "Undefined Command",
          }[err_code]
        )
      if err_code != 0:
        raise RuntimeError(f"Unexpected error code: {err_code}")

    return resp


class HamiltonTiltModuleTilterBackend(TilterBackend):
  """Translates TilterBackend interface into Hamilton tilt module driver commands.

  Protocol encoding lives here -- the backend knows that set_angle means
  calling tilt_go_to_position via the driver's send_command.
  """

  def __init__(self, driver: HamiltonTiltModuleDriver):
    self.driver = driver

  async def _on_setup(self):
    await self.tilt_initial_offset(0)
    await self.tilt_initialize()

  async def set_angle(self, angle: float):
    """Set the tilt module to rotate by a given angle."""

    if not (0 <= angle <= 10):
      raise ValueError("Angle must be between 0 and 10 degrees.")

    await self.tilt_go_to_position(round(angle))

  async def tilt_initialize(self):
    """Initialize a daisy chained tilt module."""

    return await self.driver.send_command("SI")

  async def tilt_move_to_absolute_step_position(self, position: float):
    """Move the tilt module to an absolute position.

    Args:
      position: absolute position (-10...120)
    """

    if not (-10 <= position <= 120):
      raise ValueError("Position must be between -10 and 120.")

    return await self.driver.send_command(
      command="SA",
      parameter=str(position),
    )

  async def tilt_move_to_relative_step_position(self, steps: float):
    """Move the tilt module to a relative position.

    .. warning:: This method has the potential to decalibrate the tilt module.

    Args:
      steps: the number of steps (+-10000)
    """

    if not (-10000 <= steps <= 10000):
      raise ValueError("Steps must be between -10000 and 10000.")

    return await self.driver.send_command(command="SR", parameter=str(steps))

  async def tilt_go_to_position(self, position: int):
    """Go to position (0...10).

    Args:
      position: 0 = horizontal, 10 = degrees
    """

    if not (0 <= position <= 10):
      raise ValueError("Position must be between 0 and 10.")

    return await self.driver.send_command(command="GP", parameter=str(position))

  async def tilt_set_speed(self, speed: int):
    """Set the speed on the tilt module.

    Args:
      speed: 1 is slow, 9 = fast. Default speed is 1.
    """

    if not (1 <= speed <= 9):
      raise ValueError("Speed must be between 1 and 9.")

    return await self.driver.send_command(command="SV", parameter=str(speed))

  async def tilt_power_off(self):
    """Power off the tilt module."""

    return await self.driver.send_command(command="PO")

  async def tilt_request_error(self) -> Optional[str]:
    """Request the error of the tilt module.

    Returns: the error, if it exists, else `None`
    """

    # send_command will automatically raise an error, if one exists
    return await self.driver.send_command("RE")

  async def tilt_request_sensor(self) -> Optional[str]:
    """Request sensor status.

    0 = LS 1 Input, 1 = LS 2 Input, 2 = LS 3 Input,
    3 = PNP Input 1, 4 = PNP Input 2, 5 = PNP Input 3,
    6 = NPN Input 1, 7 = NPN Input 2
    """

    resp = await self.driver.send_command(command="RX")
    resp = resp[:-2].split(" ")[1]
    code = int(resp)

    if code == 0:
      return None
    if 1 <= code <= 7:
      return {
        0: "LS 1 Input",
        1: "LS 2 Input",
        2: "LS 3 Input",
        3: "PNP Input 1",
        4: "PNP Input 2",
        5: "PNP Input 3",
        6: "NPN Input 1",
        7: "NPN Input 2",
      }[code]
    raise RuntimeError(f"Unexpected error code: {code}")

  async def tilt_request_offset_between_light_barrier_and_init_position(self) -> int:
    """Request Offset between Light Barrier and Init Position"""

    resp = await self.driver.send_command(command="RO")
    resp = resp[:-2].split(" ")[1]
    return int(resp)

  async def tilt_port_set_open_collector(self, open_collector: int):
    """Port set open collector.

    Args:
      open_collector: 1...8
    """

    if not (1 <= open_collector <= 8):
      raise ValueError("open_collector must be between 1 and 8")

    return await self.driver.send_command(command="PS", parameter=str(open_collector))

  async def tilt_port_clear_open_collector(self, open_collector: int):
    """Tilt port clear open collector.

    Args:
      open_collector: 1...8
    """

    if not (1 <= open_collector <= 8):
      raise ValueError("open_collector must be between 1 and 8")

    return await self.driver.send_command(command="PC", parameter=str(open_collector))

  async def tilt_set_temperature(self, temperature: float):
    """Set the temperature (10-50 degrees C).

    Args:
      temperature: temperature in Celsius, between 10 and 50
    """

    if not (10 <= temperature <= 50):
      raise ValueError("Temperature must be between 10 and 50.")

    return await self.driver.send_command(command="ST", parameter=str(int(temperature * 10)))

  async def tilt_switch_off_temperature_controller(self):
    """Switch off the temperature controller on the tilt module."""

    return await self.driver.send_command(command="TO")

  async def tilt_set_drain_time(self, drain_time: float):
    """Set the drain time on the tilt module.

    Args:
      drain_time: drain time in seconds, between 5 and 250
    """

    if not (5 <= drain_time <= 250):
      raise ValueError("Drain time must be between 5 and 250.")

    return await self.driver.send_command(command="DT", parameter=str(int(drain_time * 10)))

  async def tilt_set_waste_pump_on(self):
    """Turn the waste pump on."""

    return await self.driver.send_command(command="WP")

  async def tilt_set_waste_pump_off(self):
    """Turn the waste pump off."""

    return await self.driver.send_command(command="WO")

  async def tilt_set_name(self, name: str):
    """Set the tilt module name.

    Args:
      name: the desired name, must be 2 characters long
    """

    if len(name) != 2:
      raise ValueError("name must be 2 characters long")

    return await self.driver.send_command(command="MN", parameter=name)

  async def tilt_switch_encoder(self, on: bool):
    """Switch the encoder on or off.

    Args:
      on: if True, the encoder will be turned on, else off.
    """

    return await self.driver.send_command(command="EN", parameter=str(int(on)))

  async def tilt_initial_offset(self, offset: int):
    """Set the initial offset on the tilt module.

    Args:
      offset: the initial offset steps, between -100 and 100
    """

    if not (-100 <= offset <= 100):
      raise ValueError("Offset must be between -100 and 100.")

    return await self.driver.send_command(command="SO", parameter=str(offset))


class HamiltonTiltModuleChatterboxTilterBackend(TilterBackend):
  """No-op backend for testing without hardware."""

  async def set_angle(self, angle: float):
    pass
