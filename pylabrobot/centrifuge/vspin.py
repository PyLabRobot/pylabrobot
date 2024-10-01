import asyncio
import logging
import time
from typing import Optional, Union

from .backend import CentrifugeBackend

try:
  from pylibftdi import Device
  USE_FTDI = True
except ImportError:
  USE_FTDI = False


logger = logging.getLogger("pylabrobot.centrifuge.vspin")


class VSpin(CentrifugeBackend):
  """ Backend for the Agilent Centrifuge.
  Note that this is not a complete implementation. """

  def __init__(self, bucket_1_position: int, device_id: Optional[str] = None):
    """
    Args:
      device_id: The libftdi id for the centrifuge. Find using
        `python3 -m pylibftdi.examples.list_devices`
      bucket_1_position: The position of bucket 1 in the centrifuge. At first run, intialize with
        an arbitrary value, move to the bucket, and call get_position() to get the position. Then
        use this value for future runs.
    """
    if not USE_FTDI:
      raise RuntimeError("pylibftdi is not installed.")
    self.dev = Device(lazy_open=True, device_id=device_id)
    self.bucket_1_position = bucket_1_position
    self.homing_position = 0

  async def setup(self):
    self.dev = Device()
    self.dev.open()
    logger.debug("open")
    # TODO: add functionality where if robot has been intialized before nothing needs to happen
    for _ in range(3):
      await self.configure_and_initialize()
      await self.send(b"\xaa\x00\x21\x01\xff\x21")
    await self.send(b"\xaa\x00\x21\x01\xff\x21")
    await self.send(b"\xaa\x01\x13\x20\x34")
    await self.send(b"\xaa\x00\x21\x02\xff\x22")
    await self.send(b"\xaa\x02\x13\x20\x35")
    await self.send(b"\xaa\x00\x21\x03\xff\x23")
    await self.send(b"\xaa\xff\x1a\x14\x2d")

    self.dev.baudrate = 57600
    self.dev.ftdi_fn.ftdi_setrts(1)
    self.dev.ftdi_fn.ftdi_setdtr(1)

    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x01\x12\x1f\x32")
    for _ in range(8):
      await self.send(b"\xaa\x02\x20\xff\x0f\x30")
    await self.send(b"\xaa\x02\x20\xdf\x0f\x10")
    await self.send(b"\xaa\x02\x20\xdf\x0e\x0f")
    await self.send(b"\xaa\x02\x20\xdf\x0c\x0d")
    await self.send(b"\xaa\x02\x20\xdf\x08\x09")
    for _ in range(4):
      await self.send(b"\xaa\x02\x26\x00\x00\x28")
    await self.send(b"\xaa\x02\x12\x03\x17")
    for _ in range(5):
      await self.send(b"\xaa\x02\x26\x20\x00\x48")
      await self.send(b"\xaa\x02\x0e\x10")
      await self.send(b"\xaa\x02\x26\x00\x00\x28")
      await self.send(b"\xaa\x02\x0e\x10")
    await self.send(b"\xaa\x02\x0e\x10")
    await self.lock_door()

    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x02\x0e\x10")

    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x02\x0e\x10")

    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x02\x0e\x10")

    await self.send(b"\xaa\x02\x0e\x10")
    await self.send(b"\xaa\x01\x0e\x0f")

    await self.send(b"\xaa\x02\x0e\x10")
    await self.send(b"\xaa\x02\x26\x00\x00\x28")
    await self.send(b"\xaa\x02\x0e\x10")

    await self.send(b"\xaa\x02\x0e\x10")
    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x02\x0e\x10")

    await self.send(b"\xaa\x01\x17\x02\x1a")
    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x01\xe6\xc8\x00\xb0\x04\x96\x00\x0f\x00\x4b\x00\xa0\x0f\x05\x00\x07")
    await self.send(b"\xaa\x01\x17\x04\x1c")
    await self.send(b"\xaa\x01\x17\x01\x19")

    await self.send(b"\xaa\x01\x0b\x0c")
    await self.send(b"\xaa\x01\x00\x01")
    await self.send(b"\xaa\x01\xe6\x05\x00\x64\x00\x00\x00\x00\x00\x32\x00\xe8\x03\x01\x00\x6e")
    await self.send(b"\xaa\x01\x94\xb6\x12\x83\x00\x00\x12\x01\x00\x00\xf3")
    await self.send(b"\xaa\x01\x19\x28\x42")
    await self.send(b"\xaa\x01\x0e\x0f")

    resp = 0x89
    while resp == 0x89:
      await self.send(b"\xaa\x02\x0e\x10")
      stat = await self.send(b"\xaa\x01\x0e\x0f")
      resp = stat[0]

    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x01\x0e\x0f")

    await self.send(b"\xaa\x01\x17\x02\x1a")
    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x01\xe6\xc8\x00\xb0\x04\x96\x00\x0f\x00\x4b\x00\xa0\x0f\x05\x00\x07")
    await self.send(b"\xaa\x01\x17\x04\x1c")
    await self.send(b"\xaa\x01\x17\x01\x19")

    await self.send(b"\xaa\x01\x0b\x0c")
    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x01\xe6\xc8\x00\xb0\x04\x96\x00\x0f\x00\x4b\x00\xa0\x0f\x05\x00\x07")
    new_position = (self.homing_position + 8000).to_bytes(4, byteorder="little")
    await self.send(b"\xaa\x01\xd4\x97" + new_position + b"\xc3\xf5\x28\x00\xd7\x1a\x00\x00\x49")
    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x01\x0e\x0f")

    resp = 0x08
    while resp != 0x09:
      stat = await self.send(b"\xaa\x01\x0e\x0f")
      await self.send(b"\xaa\x01\x0e\x0f")
      resp = stat[0]

    await self.send(b"\xaa\x01\x0e\x0f")
    await self.send(b"\xaa\x01\x0e\x0f")

    await self.send(b"\xaa\x01\x17\x02\x1a")

    await self.send(b"\xaa\x02\x0e\x10")
    await self.lock_door()

    await self.send(b"\xaa\x01\x0e\x0f")

  async def stop(self):
    await self.send(b"\xaa\x02\x0e\x10")
    await self.configure_and_initialize()
    if self.dev:
      self.dev.close()

  async def get_status(self):
    """Returns 14 bytes

    Example:
     11 22 25 00 00 4f 00 00 18 e0 05 00 00 a4

     First byte (index 0):
      - 11 = idle
      - 13 = unknown
      - 08 = spinning
      - 09 = also spinning but different
      - 19 = unknown
     2nd to 5th byte (index 1-4) = Position
     10th to 13th byte (index 9-12) = Homing Position
     Last byte (index 13) = checksum

    """
    resp = await self.send(b"\xaa\x01\x0e\x0f")
    if len(resp) == 0:
      raise IOError("Empty status from centrifuge")
    return resp

  async def get_position(self):
    resp = await self.get_status()
    return int.from_bytes(resp[1:5], byteorder="little")

# Centrifuge communication: read_resp, send, send_payloads

  async def read_resp(self, timeout=20) -> bytes:
    """Read a response from the centrifuge. If the timeout is reached, return the data that has
    been read so far."""
    if not self.dev:
      raise RuntimeError("Device not initialized")

    data = b""
    end_byte_found = False
    start_time = time.time()

    while True:
      chunk = self.dev.read(25)
      if chunk:
        data += chunk
        end_byte_found = data[-1] == 0x0d
        if len(chunk) < 25 and end_byte_found:
          break
      else:
        if end_byte_found or time.time() - start_time > timeout:
          logger.warning("Timed out reading response")
          break
        await asyncio.sleep(0.0001)

    logger.debug("Read %s", data.hex())
    return data

  async def send(self, cmd: Union[bytearray, bytes], read_timeout=0.2) -> bytes:
    logger.debug("Sending %s", cmd.hex())
    written = self.dev.write(cmd.decode("latin-1"))
    logger.debug("Wrote %s bytes", written)

    if written != len(cmd):
      raise RuntimeError("Failed to write all bytes")
    return await self.read_resp(timeout=read_timeout)

  async def send_payloads(self, payloads) -> None:
    """Send a list of commands to the centrifuge."""
    for tx in payloads:
      if isinstance(tx, str):
        byte_literal = bytes.fromhex(tx)
        await self.send(byte_literal)
      else:
        await self.send(tx)

  async def configure_and_initialize(self):
    await self.set_configuration_data()
    await self.initialize()

  async def set_configuration_data(self):
    """Set the device configuration data."""
    self.dev.ftdi_fn.ftdi_set_latency_timer(16)
    self.dev.ftdi_fn.ftdi_set_line_property(8, 1, 0)
    self.dev.ftdi_fn.ftdi_setflowctrl(0)
    self.dev.baudrate = 19200

  async def initialize(self):
    if self.dev:
      self.dev.write(b"\x00" * 20)
      for i in range(33):
        packet = b"\xaa" + bytes([i & 0xFF, 0x0e, 0x0e + (i & 0xFF)]) + b"\x00" * 8
        self.dev.write(packet)
      await self.send(b"\xaa\xff\x0f\x0e")

# Centrifuge operations

  async def open_door(self):
    await self.send(b"\xaa\x02\x26\x00\x07\x2f")
    await self.send(b"\xaa\x02\x0e\x10")
    # we can't tell when the door is fully open, so we just wait a bit
    await asyncio.sleep(4)

  async def close_door(self):
    await self.send(b"\xaa\x02\x26\x00\x05\x2d")
    await self.send(b"\xaa\x02\x0e\x10")
    # we can't tell when the door is fully closed, so we just wait a bit
    await asyncio.sleep(2)

  async def lock_door(self):
    await self.send(b"\xaa\x02\x26\x00\x01\x29")
    await self.send(b"\xaa\x02\x0e\x10")

  async def unlock_door(self):
    await self.send(b"\xaa\x02\x26\x00\x05\x2d")
    await self.send(b"\xaa\x02\x0e\x10")

  async def lock_bucket(self):
    await self.send(b"\xaa\x02\x26\x00\x07\x2f")
    await self.send(b"\xaa\x02\x0e\x10")

  async def unlock_bucket(self):
    await self.send(b"\xaa\x02\x26\x00\x06\x2e")
    await self.send(b"\xaa\x02\x0e\x10")

  async def go_to_bucket1(self):
    await self.go_to_position(self.bucket_1_position)

  async def go_to_bucket2(self):
    half_rotation = 4000
    await self.go_to_position(self.bucket_1_position + half_rotation)

  async def rotate_distance(self, distance):
    current_position = await self.get_position()
    await self.go_to_position(current_position + distance)

  async def go_to_position(self, position: int):
    await self.close_door()
    await self.lock_door()

    position_bytes = position.to_bytes(4, byteorder="little")
    byte_string = b"\xaa\x01\xd4\x97" + position_bytes + b"\xc3\xf5\x28\x00\xd7\x1a\x00\x00"
    sum_byte = (sum(byte_string)-0xaa)&0xff
    byte_string += sum_byte.to_bytes(1, byteorder="little")
    move_bucket = [
      "aa 02 26 00 00 28",
      "aa 02 0e 10",
      "aa 01 17 02 1a",
      "aa 01 0e 0f",
      "aa 01 e6 c8 00 b0 04 96 00 0f 00 4b 00 a0 0f 05 00 07",
      "aa 01 17 04 1c",
      "aa 01 17 01 19",
      "aa 01 0b 0c",
      "aa 01 e6 c8 00 b0 04 96 00 0f 00 4b 00 a0 0f 05 00 07",
      byte_string
    ]
    await self.send_payloads(move_bucket)

    await asyncio.sleep(2)

    await self.send(b"\xaa\x01\x17\x02\x1a")
    await self.open_door()

  async def start_spin_cycle(
    self,
    g: float = 500,
    duration: float = 60,
    acceleration: float = 80,
  ) -> None:
    """Start a spin cycle. spin spin spin spin

    Args:
      g: relative centrifugal force, also known as g-force
      duration: How much time spent actually spinning at the desired g in seconds
      acceleration: 1-100% of total acceleration

    Examples:
      Spin with 1000 g-force (close to 3000rpm) for 5 minutes at 100% acceleration

      >>> cf.start_spin_cycle(g = 1000, duration = 300, acceleration = 100)
    """

    if acceleration < 1 or acceleration > 100:
      raise ValueError("Acceleration must be within 1-100.")
    if g < 1 or g > 1000:
      raise ValueError("G-force must be within 1-1000")
    if duration < 1:
      raise ValueError("Spin time must be at least 1 second")

    await self.close_door()
    await self.lock_door()

    rpm = int((g/(1.118*(10**(-4))))**0.5)
    base = int(107007 - 328*rpm + 1.13*(rpm**2))
    rpm_b = (int(4481*rpm + 10852)).to_bytes(4, byteorder="little")
    acc = (int(915*acceleration/100)).to_bytes(2, byteorder="little")
    maxp = min((await self.get_position() + base + 4000*rpm//30*duration), 4294967294)
    position = maxp.to_bytes(4, byteorder="little")

    byte_string = b"\xaa\x01\xd4\x97" + position + rpm_b + acc+b"\x00\x00"
    last_byte = (sum(byte_string)-0xaa)&0xff
    byte_string += last_byte.to_bytes(1, byteorder="little")

    payloads = [
      "aa 02 26 00 00 28",
      "aa 02 0e 10",
      "aa 01 17 02 1a",
      "aa 01 0e 0f",
      "aa 01 e6 c8 00 b0 04 96 00 0f 00 4b 00 a0 0f 05 00 07",
      "aa 01 17 04 1c",
      "aa 01 17 01 19",
      "aa 01 0b 0c",
      "aa 01 0e 0f",
      "aa 01 e6 05 00 64 00 00 00 00 00 fd 00 80 3e 01 00 0c",
      byte_string,
    ]
    await self.send_payloads(payloads)

    status_resp = await self.get_status()
    status = status_resp[0]
    while status == 0x08:
      await asyncio.sleep(1)
      status_resp = await self.get_status()
      status = status_resp[0]

    await asyncio.sleep(2)

    # reset position back to 0ish
    # this part is needed because otherwise calling go_to_position will not work after
    payloads = [
      "aa 01 e6 c8 00 b0 04 96 00 0f 00 4b 00 a0 0f 05 00 07",
      "aa 01 17 04 1c",
      "aa 01 17 01 19",
      "aa 01 0b 0c",
      "aa 01 00 01",
      "aa 01 e6 05 00 64 00 00 00 00 00 32 00 e8 03 01 00 6e",
      "aa 01 94 b6 12 83 00 00 12 01 00 00 f3",
      "aa 01 19 28 42"
    ]

    await self.send_payloads(payloads)
