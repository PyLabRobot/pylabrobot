import time
import typing

from pylabrobot.io.hid import HID


class InhecoTECControlBox:
  def __init__(
    self,
    vid=0x03EB,
    pid=0x2023,
    serial_number=None,
  ):
    self.io = HID(vid=vid, pid=pid, serial_number=serial_number)

  async def setup(self):
    """
    If io.setup() fails, ensure that libusb drivers were installed as per docs.
    """
    await self.io.setup()

  async def stop(self):
    await self.io.stop()

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

  async def _read_until_end(self, timeout: int) -> str:
    """Read until a packet ends with a \\x00 byte. May read multiple packets."""
    start = time.time()
    response = b""
    while time.time() - start < timeout:
      packet = await self.io.read(64, timeout=timeout)
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

  async def _read_response(self, command: str, timeout: int = 60) -> str:
    """Read the response for a given command.

    "The MTC/STC replies to the first four characters of every command with a modified echo. The
    modification changes the capitals of the commands to small letters. i.e. the reply to 5ASE1
    is 5ase0. Therefore it is easy to identify correct answers to the commands. This feature may
    increase integrity of the communication."
    """

    start = time.time()
    while time.time() - start < timeout:
      response = await self._read_until_end(timeout=int(timeout - (time.time() - start)))

      if response[:4] == command[:4].lower():
        return response

    raise TimeoutError("Timeout while waiting for response from device.")

  async def send_command(self, command: str, timeout: int = 3):
    """Send a command to the device and return the response"""
    packets = self._generate_packets(command)
    for packet in packets:
      await self.io.write(bytes(packet[1:]), report_id=bytes(packet[0]))

    response = await self._read_response(command, timeout=timeout)

    if response[4] != "0":
      raise RuntimeError(f"Error response from device: {response}")

    return response[5:-1]  # cut off command, error status, and final checksum byte

  async def set_touchscreen(self, active: bool):
    await self.send_command(f"0ADD{1 if active else 0}")
    await self.reset_action_display()

  async def reset_action_display(self):
    return await self.send_command("0ASD")
