import time
import typing

from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.io.hid import HID


class InhecoThermoShake(HeaterShakerBackend):
  """Backend for Inheco Thermoshake devices

  https://www.inheco.com/thermoshake-ac.html
  """

  def __init__(self, vid=0x03EB, pid=0x2023, serial_number=None):
    self.io = HID(vid=vid, pid=pid, serial_number=serial_number)

  async def setup(self):
    await self.io.setup()

  async def stop(self):
    await self.stop_shaking()
    await self.stop_temperature_control()
    await self.io.stop()

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      **self.io.serialize(),
    }

  @typing.no_type_check
  def _generate_packets(self, msg):
    """Generate packets for the given message.

    Splits the message into packets of 7 bytes each. The last packet contains the checksum.
    """

    ch_array1 = [None] * 128
    report_buffer = [0] * 9
    crc = 161
    for i, char in enumerate(msg):
      ch_array1[i] = char if ord(char) <= ord("`") or char == "#" else chr(ord(char) - 32)
      if ch_array1[i] != "#":
        crc = self._crc8(ord(ch_array1[i]), crc=crc)
    ch_array1[len(msg)] = "w" if crc in (35, 0) else chr(crc)
    num1 = len(msg) + 1
    if num1 < 2:
      report_buffer[0] = 0
    if num1 <= 8:
      for i in range(num1):
        report_buffer[i + 1] = ord(ch_array1[i])
      return [report_buffer]
    else:
      num2 = (num1 - 1) // 7  # number of packets
      command = []
      for index1 in range(num2 + 1):
        for index2 in range(7):
          if index2 + index1 * 7 >= num1:
            report_buffer[index2 + 1] = 0
          else:
            report_buffer[index2 + 1] = ord(ch_array1[index2 + index1 * 7])
        if index1 != num2:
          report_buffer[8] = ord("#")
        else:
          if (num2 + 1) * 7 + 1 == num1:
            report_buffer[8] = ord(ch_array1[num1 - 1])
            if report_buffer[8] > 96:
              report_buffer[8] -= 32
          else:
            report_buffer[8] = 0
        command.append(report_buffer)
      return command

  def _crc8(self, data, crc: int) -> int:
    """Meme crc8 implementation"""
    num = 8
    while num > 0:
      if ((data ^ crc) & 1) == 1:
        crc ^= 24
        crc >>= 1
        crc |= 128
      else:
        crc >>= 1
      data >>= 1
      num -= 1
    return crc

  def _read_until_end(self, timeout: int) -> str:
    """Read until a packet ends with a \\x00 byte. May read multiple packets."""
    start = time.time()
    response = b""
    while time.time() - start < timeout:
      packet = self.io.read(64, timeout=timeout)
      if packet is not None and packet != b"":
        if packet.endswith(b"\x00"):
          response += packet.rstrip(b"\x00")  # strip trailing \x00's
          break
        elif packet.endswith(b"#"):
          response += packet[:-1]
          continue
        else:
          # I have never seen this happen, commands always end with \x00 or '#'
          print("weird packet, please report", packet)
          response += packet

    return response.decode("unicode_escape")

  def _read_response(self, command: str, timeout: int = 60) -> str:
    """Read the response for a given command.

    "The MTC/STC replies to the first four characters of every command with a modified echo. The
    modification changes the capitals of the commands to small letters. i.e. the reply to 5ASE1
    is 5ase0. Therefore it is easy to identify correct answers to the commands. This feature may
    increase integrity of the communication."
    """

    start = time.time()
    while time.time() - start < timeout:
      response = self._read_until_end(timeout=int(timeout - (time.time() - start)))

      if response[:4] == command[:4].lower():
        return response

    raise TimeoutError("Timeout while waiting for response from device.")

  async def send_command(self, command: str, timeout: int = 3):
    """Send a command to the device and return the response"""
    packets = self._generate_packets(command)
    for packet in packets:
      self.io.write(bytes(packet))

    response = self._read_response(command, timeout=timeout)

    if response[4] != "0":
      raise RuntimeError(f"Error response from device: {response}")

    return response[5:-1]  # cut off command, error status, and final checksum byte

  # -- shaker

  async def shake(self, speed: float, shape: int = 0):
    """Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
    """

    await self.set_shaker_speed(speed=speed)
    await self.set_shaker_shape(shape=shape)
    await self.start_shaking()

  async def stop_shaking(self):
    """Stop shaking the device"""

    return await self.send_command("1ASE0")

  # -- temperature control

  async def set_temperature(self, temperature: float):
    await self.set_target_temperature(temperature)
    await self.start_temperature_control()

  async def get_current_temperature(self) -> float:
    response = await self.send_command("1RAT0")
    return float(response) / 10

  async def deactivate(self):
    await self.stop_temperature_control()

  # -- firmware commands

  # --- firmware shaking

  async def set_shaker_speed(self, speed: float):
    """Set the shaker speed on the device, but do not start shaking yet. Use `start_shaking` for
    that.
    """

    # # 60 ... 2000
    # # Thermoshake and Teleshake
    assert speed in range(60, 2001), "Speed must be in the range 60 to 2000 RPM"

    # Thermoshake AC, Teleshake95 AC and Teleshake AC
    # 150 ... 3000
    # assert speed in range(150, 3001), "Speed must be in the range 150 to 3000 RPM"

    return await self.send_command(f"1SSR{speed}")

  async def start_shaking(self):
    """Start shaking the device at the speed set by `set_shaker_speed`"""

    return await self.send_command("1ASE1")

  async def set_shaker_shape(self, shape: int):
    """Set the shape of the figure that should be shaked.

    Args:
      shape: 0 = Circle anticlockwise, 1 = Circle clockwise, 2 = Up left down right, 3 = Up right
        down left, 4 = Up-down, 5 = Left-right
    """

    assert shape in range(6), "Shape must be in the range 0 to 5"

    return await self.send_command(f"1SSS{shape}")

  # --- firmware temp

  async def set_target_temperature(self, temperature: float):
    temperature = int(temperature * 10)
    await self.send_command(f"1STT{temperature}")

  async def start_temperature_control(self):
    """Start the temperature control"""

    return await self.send_command("1ATE1")

  async def stop_temperature_control(self):
    """Stop the temperature control"""

    return await self.send_command("1ATE0")

  # --- firmware misc

  async def get_device_info(self, info_type: int):
    """Get device information

    - 0 Bootstrap Version
    - 1 Application Version
    - 2 Serial number
    - 3 Current hardware version
    - 4 INHECO copyright
    """

    assert info_type in range(5), "Info type must be in the range 0 to 4"
    return await self.send_command(f"1RFV{info_type}")

  async def reset_action_display(self):
    return await self.send_command("1ASD")

  async def activate_touchscreen(self):
    """De/activate the touchscreen"""

    await self.send_command("0ADD1")
    await self.reset_action_display()

  async def deactivate_touchscreen(self):
    """De/activate the touchscreen"""

    return await self.send_command("0ADD0")
