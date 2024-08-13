import re
from typing import Optional, cast

try:
  import serial
  HAS_SERIAL = True
except ImportError:
  HAS_SERIAL = False

from pylabrobot.tilting.tilter_backend import TilterBackend, TiltModuleError


class HamiltonTiltModuleBackend(TilterBackend):
  """ Backend for the Hamilton tilt module. """

  def __init__(self, com_port: str, write_timeout: float = 10, timeout: float = 10):
    self.setup_finished = False
    self.com_port = com_port
    self.serial: Optional[serial.Serial] = None
    self.timeout = timeout
    self.write_timeout = write_timeout
    self.ser: Optional[serial.Serial] = None

  async def setup(self, initial_offset: int = 0):
    if not HAS_SERIAL:
      raise RuntimeError("pyserial not installed.")
    self.ser = serial.Serial(
      port=self.com_port,
      baudrate=1200,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=self.write_timeout,
      timeout=self.timeout)

    await self.tilt_initial_offset(initial_offset)
    await self.send_command("SI")

    self.setup_finished = True

  async def stop(self):
    if self.ser is not None:
      self.ser.close()
      self.ser = None
    self.setup_finished = False

  async def send_command(self, command: str, parameter: Optional[str] = None) -> str:
    """ Send a command to the tilt module. """

    if self.ser is None:
      raise RuntimeError("Tilt module not setup.")

    if parameter is None:
      parameter = ""

    self.ser.write(f"99{command}{parameter}\r\n".encode("utf-8"))
    resp = self.ser.read(128).decode("utf-8")

    # Check for error.
    error_matches = re.search("er[0-9]{2}", resp)
    if error_matches is not  None:
      err_code = int(error_matches.group(0)[2:])
      if 1 <= err_code <= 7:
        raise TiltModuleError({
          1: "Init Position not found",
          2: "**Step** loss",
          3: "Not initialized",
          5: "Stepper Motor end stage defective",
          6: "Parameter out **of** Range",
          7: "Undefined Command",
        }[err_code])
      if err_code != 0:
        raise RuntimeError(f"Unexpected error code: {err_code}")

    return cast(str, resp) # must do stupid because mypy will not recognize that pyserial is typed..

  async def set_angle(self, angle: float):
    """ Set the tilt module to rotate by a given angle. """

    assert 0 <= angle <= 10, "Angle must be between 0 and 10 degrees."

    await self.tilt_go_to_position(round(angle))

  async def tilt_initialize(self):
    """ Initialize a daisy chained tilt module. """

    return await self.send_command("SI")

  async def tilt_move_to_absolute_step_position(self, position: float):
    """ Move the tilt module to an absolute position.

    Args:
      position: absolute position (-10...120)
    """

    assert -10 <= position <= 120, "Position must be between -10 and 120."

    return await self.send_command(
      command="SA",
      parameter=str(position),
    )

  async def tilt_move_to_relative_step_position(self, steps: float):
    """ Move the tilt module to a relative position.

    .. warning:: This method has the potential to decalibrate the tilt module.

    Args:
      steps: the number of steps (±10000)
    """

    assert -10000 <= steps <= 10000, "Steps must be between -10000 and 10000."

    return await self.send_command(command="SR", parameter=str(steps))

  async def tilt_go_to_position(self, position: int):
    """ Go to position (0...10).

    Args:
      position: 0 = horizontal, 10 = degrees
    """

    assert 0 <= position <= 10, "Position must be between 0 and 10."

    return await self.send_command(command="GP", parameter=str(position))

  async def tilt_set_speed(self, speed: int):
    """ Set the speed on the tilt module.

    Args:
      speed: 1 is slow, 9 = fast. Default speed is 1.
    """

    assert 1 <= speed <= 9, "Speed must be between 1 and 9."

    return await self.send_command(command="SV", parameter=str(speed))

  async def tilt_power_off(self):
    """ Power off the tilt module. """

    return await self.send_command(command="PO")

  async def tilt_request_error(self) -> Optional[str]:
    """ Request the error of the tilt module.

    Returns: the error, if it exists, else `None`
    """

    # send_command will automatically raise an error, if one exists
    return await self.send_command("RE")

  async def tilt_request_sensor(self) -> Optional[str]:
    """ It is unclear what this method does. The documentation lists the following map:

    0 = LS 1 Input
    1 = LS 2 Input
    2 = LS 3 Input
    3 = PNP Input 1
    4 = PNP Input 2
    5 = PNP Input 3
    6 = NPN Input 1
    7 = NPN Input 2
    """

    resp = await self.send_command(command="RX")
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
    """ Request Offset between Light Barrier and Init Position """

    resp = await self.send_command(command="RO")
    resp = resp[:-2].split(" ")[1]
    return int(resp)

  # Open Collectors

  async def tilt_port_set_open_collector(self, open_collector: int):
    """ Port set open collector

    Args:
      open_collector: 1...8 # TODO: what?
    """

    assert 1 <= open_collector <= 8, "open_collector must be between 1 and 8"

    return await self.send_command(command="PS", parameter=str(open_collector))

  async def tilt_port_clear_open_collector(self, open_collector: int):
    """ Tilt port clear open collector

    Args:
      open_collector: 1...8 # TODO: what?
    """

    assert 1 <= open_collector <= 8, "open_collector must be between 1 and 8"

    return await self.send_command(command="PC", parameter=str(open_collector))

  # Single Commands **with** **Option** “Heating”:

  async def tilt_set_temperature(self, temperature: float):
    """ Tilt set the temperature 10.. 50 Grad C [1/10 Grad C]

    Args:
      temperature: temperature in Celcius, between 10 and 50
    """

    assert 10 <= temperature <= 50, "Temperature must be between 10 and 50."

    return await self.send_command(command="ST", parameter=str(int(temperature*10)))

  async def tilt_switch_off_temperature_controller(self):
    """ Switch off the temperature controller on the tilt module. """

    return await self.send_command(
      command="TO",
    )

  # Single Commands **with** **Option** “Waste Pump (PWM2)”:

  async def tilt_set_drain_time(self, drain_time: float):
    """ Set the drain time on the tilt module.

    Args:
      drain_time: drain time in seconds, between 5 and 250
    """

    assert 5 <= drain_time <= 250, "Drain time must be between 5 and 250."

    return await self.send_command(command="DT", parameter=str(int(drain_time*10)))

  async def tilt_set_waste_pump_on(self):
    """ Turn the waste pump on the tilt module on """

    return await self.send_command(
      command="WP",
    )

  async def tilt_set_waste_pump_off(self):
    """ Turn the waste pump on the tilt module off """

    return await self.send_command(
      command="WO",
    )

  # Adjustment Commands:

  async def tilt_set_name(self, name: str):
    """ Set the tilt module name.

    Args:
      name: the desired name, must be 2 characters long
    """

    assert len(name) == 2, "name must be 2 characters long"

    return await self.send_command(
      command="MN",
      parameter=name
    )

  async def tilt_switch_encoder(self, on: bool):
    """ Switch the encoder on the tilt module on or off.

    Args:
      on: if `True`, the encoder will be turned on, else, it will be turned off.
    """

    return await self.send_command(command="EN", parameter=str(int(on)))

  async def tilt_initial_offset(self, offset: int):
    """ Set the initial offset on the tilt module

    Args:
      offset: the initial offset steps, steps between -100 and 100
    """

    assert -100 <= offset <= 100, "Offset must be between -100 and 100."

    return await self.send_command(command="SO", parameter=str(offset))
