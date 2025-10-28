import asyncio
import json
import logging
import os
import time
import warnings
from typing import Optional, Union

from pylabrobot.io.ftdi import FTDI

from .backend import CentrifugeBackend, LoaderBackend
from .standard import LoaderNoPlateError

logger = logging.getLogger(__name__)


class Access2Backend(LoaderBackend):
  def __init__(
    self,
    device_id: str,
    timeout: int = 60,
  ):
    """
    Args:
      device_id: The libftdi id for the loader. Find using
        `python3 -m pylibftdi.examples.list_devices`
    """
    self.io = FTDI(device_id=device_id)
    self.timeout = timeout

  async def _read(self) -> bytes:
    x = b""
    r = None
    start = time.time()
    while r != b"" or x == b"":
      r = await self.io.read(1)
      x += r
      if r == b"":
        await asyncio.sleep(0.1)
      if x == b"" and (time.time() - start) > self.timeout:
        raise TimeoutError("No data received within the specified timeout period")
    return x

  async def send_command(self, command: bytes) -> bytes:
    logger.debug("[loader] Sending %s", command.hex())
    await self.io.write(command)
    return await self._read()

  async def setup(self):
    logger.debug("[loader] setup")

    await self.io.setup()
    await self.io.set_baudrate(115384)

    status = await self.get_status()
    if not status.startswith(bytes.fromhex("1105")):
      raise RuntimeError("Failed to get status")

    await self.send_command(bytes.fromhex("110500030014000072b1"))
    await self.send_command(bytes.fromhex("1105000300100000ae71"))
    await self.send_command(bytes.fromhex("110500070024040000008000be89"))
    await self.send_command(bytes.fromhex("11050007002404008000800063b1"))
    await self.send_command(bytes.fromhex("11050007002404000001800089b9"))
    await self.send_command(bytes.fromhex("1105000700240400800180005481"))
    await self.send_command(bytes.fromhex("110500070024040000024000c6bd"))
    await self.send_command(bytes.fromhex("1105000300400000f0bf"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    # await self.send_command(bytes.fromhex("11050003002000006bd4"))
    await self.send_command(bytes.fromhex("1105000e00440b00000000000000007041020203c7"))
    # await self.send_command(bytes.fromhex("11050003002000006bd4"))

  async def stop(self):
    logger.debug("[loader] stop")
    await self.io.stop()

  def serialize(self):
    return {"io": self.io.serialize(), "timeout": self.timeout}

  async def get_status(self) -> bytes:
    logger.debug("[loader] get_status")
    return await self.send_command(bytes.fromhex("11050003002000006bd4"))

  async def park(self):
    logger.debug("[loader] park")
    await self.send_command(bytes.fromhex("1105000e00440b0000000000410000704103007539"))

  async def close(self):
    logger.debug("[loader] close")
    await self.send_command(bytes.fromhex("1105000a00420700010000803f02008c64"))

  async def open(self):
    logger.debug("[loader] open")
    await self.send_command(bytes.fromhex("1105000a0042070001000080bf0200b73e"))

  async def load(self):
    """only tested for 1cm plate, 3mm pickup height"""
    logger.debug("[loader] load")

    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b000100004040000020410200a5cb"))

    # laser check
    r = await self.send_command(bytes.fromhex("1105000300500000b3dc"))
    if r == bytes.fromhex("1105000800510500000300000079f1"):
      raise LoaderNoPlateError("no plate found on stage")

    await self.send_command(bytes.fromhex("1105000a00460700018fc2b540020023dc"))
    await self.send_command(bytes.fromhex("1105000e00440b000200004040000020410300ee00"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020015fd"))
    await self.send_command(bytes.fromhex("1105000e00440b0000000040400000204102007d82"))

  async def unload(self):
    """only tested for 1cm plate, 3mm pickup height"""
    logger.debug("[loader] unload")

    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b000200004040000020410200dd31"))

    # laser check
    r = await self.send_command(bytes.fromhex("1105000300500000b3dc"))
    if r == bytes.fromhex("1105000800510500000300000079f1"):
      raise LoaderNoPlateError("no plate found in centrifuge")

    await self.send_command(bytes.fromhex("1105000a00460700017b14b6400200d57a"))
    await self.send_command(bytes.fromhex("1105000e00440b00010000404000002041030096fa"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020015fd"))
    await self.send_command(bytes.fromhex("1105000e00440b00000000000000002041020056be"))
    # await self.send_command(bytes.fromhex("11050003002000006bd4"))


_vspin_bucket_calibrations_path = os.path.join(
  os.path.expanduser("~"),
  ".pylabrobot",
  "vspin_bucket_calibrations.json",
)


def _load_vspin_calibrations(device_id: str) -> Optional[int]:
  if not os.path.exists(_vspin_bucket_calibrations_path):
    return None
  with open(_vspin_bucket_calibrations_path, "r") as f:
    return json.load(f).get(device_id)  # type: ignore


def _save_vspin_calibrations(device_id, remainder: int):
  if os.path.exists(_vspin_bucket_calibrations_path):
    with open(_vspin_bucket_calibrations_path, "r") as f:
      data = json.load(f)
  else:
    data = {}
  data[device_id] = remainder
  os.makedirs(os.path.dirname(_vspin_bucket_calibrations_path), exist_ok=True)
  with open(_vspin_bucket_calibrations_path, "w") as f:
    json.dump(data, f)


FULL_ROTATION: int = 8000


class VSpinBackend(CentrifugeBackend):
  """Backend for the Agilent Centrifuge.
  Note that this is not a complete implementation."""

  def __init__(self, device_id: str):
    """
    Args:
      device_id: The libftdi id for the centrifuge. Find using `python -m pylibftdi.examples.list_devices`
    """
    self.io = FTDI(device_id=device_id)
    # TODO: can device_id be loaded?
    self.device_id = device_id
    self._bucket_1_remainder: Optional[int] = None
    if device_id is not None:
      self._bucket_1_remainder = _load_vspin_calibrations(device_id)
    if self._bucket_1_remainder is None:
      warnings.warn(
        f"No calibration found for VSpin with device id {device_id}. "
        "Please set the bucket 1 position using `set_bucket_1_position_to_current` method after setup.",
        UserWarning,
      )

  async def setup(self):
    await self.io.setup()
    # TODO: add functionality where if robot has been initialized before nothing needs to happen
    for _ in range(3):
      await self.configure_and_initialize()
      await self.send(bytes.fromhex("aa002101ff21"))
    await self.send(bytes.fromhex("aa002101ff21"))
    await self.send(bytes.fromhex("aa01132034"))
    await self.send(bytes.fromhex("aa002102ff22"))
    await self.send(bytes.fromhex("aa02132035"))
    await self.send(bytes.fromhex("aa002103ff23"))
    await self.send(bytes.fromhex("aaff1a142d"))

    await self.io.set_baudrate(57600)
    await self.io.set_rts(True)
    await self.io.set_dtr(True)

    await self.send(bytes.fromhex("aa01121f32"))
    for _ in range(8):
      await self.send(bytes.fromhex("aa0220ff0f30"))
    await self.send(bytes.fromhex("aa0220df0f10"))
    await self.send(bytes.fromhex("aa0220df0e0f"))
    await self.send(bytes.fromhex("aa0220df0c0d"))
    await self.send(bytes.fromhex("aa0220df0809"))
    for _ in range(4):
      await self.send(bytes.fromhex("aa0226000028"))
    await self.send(bytes.fromhex("aa02120317"))
    for _ in range(5):
      await self.send(bytes.fromhex("aa0226200048"))
      await self.send(bytes.fromhex("aa0226000028"))
    await self.lock_door()

    await self.send(bytes.fromhex("aa0226000028"))

    await self.send(bytes.fromhex("aa0117021a"))
    await self.send(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send(bytes.fromhex("aa0117041c"))
    await self.send(bytes.fromhex("aa01170119"))

    await self.send(bytes.fromhex("aa010b0c"))
    await self.send(bytes.fromhex("aa010001"))
    await self.send(bytes.fromhex("aa01e605006400000000003200e80301006e"))
    await self.send(bytes.fromhex("aa0194b61283000012010000f3"))
    await self.send(bytes.fromhex("aa01192842"))

    resp = 0x89
    while resp == 0x89:
      resp = stat[0]

    await self.send(bytes.fromhex("aa0117021a"))
    await self.send(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send(bytes.fromhex("aa0117041c"))
    await self.send(bytes.fromhex("aa01170119"))

    await self.send(bytes.fromhex("aa010b0c"))
    await self.send(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    new_position = (0).to_bytes(4, byteorder="little")  # arbitrary
    await self.send(bytes.fromhex("aa01d497") + new_position + bytes.fromhex("c3f52800d71a000049"))

    resp = 0x08
    while resp != 0x09:
      resp = stat[0]

    await self.send(bytes.fromhex("aa0117021a"))

    await self.lock_door()

  @property
  def bucket_1_remainder(self) -> int:
    if self._bucket_1_remainder is None:
      raise RuntimeError(
        "Bucket 1 position not set. Please set it using `set_bucket_1_position_to_current` method."
      )
    return self._bucket_1_remainder

  async def set_bucket_1_position_to_current(self) -> None:
    """Set the current position as bucket 1 position and save calibration."""
    current_position = await self.get_position()
    device_id = await self.io.get_serial()
    remainder = await self.get_home_position() - current_position
    self._bucket_1_remainder = current_position % FULL_ROTATION
    _save_vspin_calibrations(device_id, remainder)

  async def get_bucket_1_position(self) -> int:
    """Get the bucket 1 position based on calibration."""
    if self._bucket_1_remainder is None:
      raise RuntimeError(
        "Bucket 1 position not set. Please set it using `set_bucket_1_position_to_current` method."
      )
    home_position = await self.get_home_position()
    bucket_1_position = (home_position - self.bucket_1_remainder) % FULL_ROTATION
    return bucket_1_position

  async def stop(self):
    await self.configure_and_initialize()
    await self.io.stop()

  async def get_status(self):
    """Returns 14 bytes

    Example:
      11 22 25 00 00 4f 00 00 18 e0 05 00 00 a4

      - First byte (index 0):
        - 11 = idle
        - 13 = unknown
        - 08 = spinning
        - 09 = also spinning but different
        - 19 = unknown
      - 2nd to 5th byte (index 1-4) = Position
      - 10th to 13th byte (index 9-12) = Homing Position
      - Last byte (index 13) = checksum
    """
    if len(resp) == 0:
      raise IOError("Empty status from centrifuge")
    return resp

  async def get_position(self):
    resp = await self.get_status()
    return int.from_bytes(resp[1:5], byteorder="little")

  async def get_home_position(self):
    resp = await self.get_status()
    return int.from_bytes(resp[9:13], byteorder="little")

  # Centrifuge communication: read_resp, send

  async def read_resp(self, timeout=20) -> bytes:
    """Read a response from the centrifuge. If the timeout is reached, return the data that has
    been read so far."""
    data = b""
    end_byte_found = False
    start_time = time.time()

    while True:
      chunk = await self.io.read(25)
      if chunk:
        data += chunk
        end_byte_found = data[-1] == 0x0D
        if len(chunk) < 25 and end_byte_found:
          break
      else:
        if end_byte_found or time.time() - start_time > timeout:
          break
        await asyncio.sleep(0.0001)

    logger.debug("Read %s", data.hex())
    return data

  async def send(self, cmd: Union[bytearray, bytes], read_timeout=0.2) -> bytes:
    written = await self.io.write(bytes(cmd))  # TODO: why decode? .decode("latin-1")

    if written != len(cmd):
      raise RuntimeError("Failed to write all bytes")
    return await self.read_resp(timeout=read_timeout)

  async def configure_and_initialize(self):
    await self.set_configuration_data()
    await self.initialize()

  async def set_configuration_data(self):
    """Set the device configuration data."""
    await self.io.set_latency_timer(16)
    await self.io.set_line_property(bits=8, stopbits=1, parity=0)
    await self.io.set_flowctrl(0)
    await self.io.set_baudrate(19200)

  async def initialize(self):
    await self.io.write(b"\x00" * 20)
    for i in range(33):
      packet = b"\xaa" + bytes([i & 0xFF, 0x0E, 0x0E + (i & 0xFF)]) + b"\x00" * 8
      await self.io.write(packet)
    await self.send(bytes.fromhex("aaff0f0e"))

  # Centrifuge operations

  async def open_door(self):
    await self.send(bytes.fromhex("aa022600072f"))
    # we can't tell when the door is fully open, so we just wait a bit
    await asyncio.sleep(4)

  async def close_door(self):
    await self.send(bytes.fromhex("aa022600052d"))
    # we can't tell when the door is fully closed, so we just wait a bit
    await asyncio.sleep(2)

  async def lock_door(self):
    await self.send(bytes.fromhex("aa0226000129"))

  async def unlock_door(self):
    await self.send(bytes.fromhex("aa022600052d"))

  async def lock_bucket(self):
    await self.send(bytes.fromhex("aa022600072f"))

  async def unlock_bucket(self):
    await self.send(bytes.fromhex("aa022600062e"))

  async def go_to_bucket1(self):
    await self.go_to_position(await self.get_bucket_1_position())

  async def go_to_bucket2(self):
    await self.go_to_position(await self.get_bucket_1_position() + FULL_ROTATION // 2)

  async def rotate_distance(self, distance):
    current_position = await self.get_position()
    await self.go_to_position(current_position + distance)

  async def go_to_position(self, position: int):
    await self.close_door()
    await self.lock_door()

    position_bytes = position.to_bytes(4, byteorder="little")
    byte_string = bytes.fromhex("aa01d497") + position_bytes + bytes.fromhex("c3f52800d71a0000")
    sum_byte = (sum(byte_string) - 0xAA) & 0xFF
    byte_string += sum_byte.to_bytes(1, byteorder="little")
    await self.send(bytes.fromhex("aa0226000028"))
    await self.send(bytes.fromhex("aa020e10"))
    await self.send(bytes.fromhex("aa0117021a"))
    await self.send(bytes.fromhex("aa010e0f"))
    await self.send(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send(bytes.fromhex("aa0117041c"))
    await self.send(bytes.fromhex("aa01170119"))
    await self.send(bytes.fromhex("aa010b0c"))
    await self.send(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send(byte_string)

    await asyncio.sleep(2)

    await self.send(bytes.fromhex("aa0117021a"))
    await self.open_door()

  async def start_spin_cycle(
    self,
    g: float = 500,
    duration: float = 60,
    acceleration: float = 0.8,
    deceleration: float = 0.8,
  ) -> None:
    """Start a spin cycle. spin spin spin spin

    Args:
      g: relative centrifugal force, also known as g-force
      duration: time in seconds spent at speed (g)
      acceleration: 1-100% of total acceleration
      deceleration: 1-100% of total deceleration

    Examples:
      Spin with 1000 g-force (close to 3000rpm) for 5 minutes at 100% acceleration

      >>> cf.start_spin_cycle(g = 1000, duration = 300, acceleration = .8, deceleration = .8)
    """

    if acceleration <= 0 or acceleration > 1:
      raise ValueError("Acceleration must be within 0-1.")
    if g < 1 or g > 1000:
      raise ValueError("G-force must be within 1-1000")
    if duration < 1:
      raise ValueError("Spin time must be at least 1 second")

    await self.close_door()
    await self.lock_door()

    # 1 - compute the final position
    # g to rpm: https://en.wikipedia.org/wiki/Centrifugation#Mathematical_formula
    r = 10
    rpm = int((g / (1.118 * 10**-5 * r)) ** 0.5)

    # compute the distance traveled during the acceleration period
    # distance = 1/2 * v^2 / a. area under 0 to t (triangle). t = a/v_max
    # 12903.2 is 100% acceleration
    acceleration_ticks_per_second2 = 12903.2 * acceleration
    speed_per_second = rpm / 60
    distance_during_acceleration = (speed_per_second * speed_per_second / acceleration) // 2

    # compute the distance traveled at speed
    distance_at_speed = speed_per_second * duration

    current_position = await self.get_position()
    final_position = current_position + distance_during_acceleration + distance_at_speed
    if final_position > 2**32 - 1:
      raise NotImplementedError(
        "We don't know what happens if the position exceeds 2^32-1. "
        "Please report this issue on discuss.pylabrobot.org."
      )
    position = final_position.to_bytes(4, byteorder="little")

    # 2 - encode the rpm
    rpm_b = int(rpm * 4473.925).to_bytes(4, byteorder="little")

    # 3 - encode the acceleration
    acc = int(9.15 * 10 * acceleration).to_bytes(2, byteorder="little")

    byte_string = bytes.fromhex("aa01d497") + position + rpm_b + acc + bytes.fromhex("0000")
    last_byte = (sum(byte_string) - 0xAA) & 0xFF
    byte_string += last_byte.to_bytes(1, byteorder="little")
    print(
      f"Final position: {final_position}, RPM: {rpm}, Acceleration: {acceleration}, current position: {current_position}, duration: {duration}, byte_string: {byte_string.hex()}"
    )

    await self.send(bytes.fromhex("aa0226000028"))
    # await self.send(bytes.fromhex("aa020e10"))
    await self.send(bytes.fromhex("aa0117021a"))
    # await self.send(bytes.fromhex("aa010e0f"))
    await self.send(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send(bytes.fromhex("aa0117041c"))
    await self.send(bytes.fromhex("aa01170119"))
    await self.send(bytes.fromhex("aa010b0c"))
    # await self.send(bytes.fromhex("aa010e0f"))
    await self.send(bytes.fromhex("aa01e60500640000000000fd00803e01000c"))
    await self.send(byte_string)

    status_resp = await self.get_status()
    status = status_resp[0]
    while status == 0x08:
      await asyncio.sleep(1)
      status_resp = await self.get_status()
      status = status_resp[0]

    await self.send(bytes.fromhex("aa01e60500640000000000fd00803e01000c"))
    # aa0194b600000000dc02000029: decel at 80
    # aa0194b6000000000a03000058: decel at 85
    decc = int(9.15 * 10 * deceleration).to_bytes(2, byteorder="little")
    decel_command = bytes.fromhex("aa0194b600000000") + decc + bytes.fromhex("0000")
    decel_command += ((sum(decel_command) - 0xAA) & 0xFF).to_bytes(1, byteorder="little")
    await self.send(decel_command)

    await asyncio.sleep(2)

    # reset position back to 0ish
    # this part is needed because otherwise calling go_to_position will not work after
    await self.send(bytes.fromhex("aa0117021a"))
    await self.send(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send(bytes.fromhex("aa0117041c"))
    await self.send(bytes.fromhex("aa01170119"))
    await self.send(bytes.fromhex("aa010b0c"))
    await self.send(bytes.fromhex("aa010001"))  # set position back to 0 (exactly)
    await self.send(bytes.fromhex("aa01e605006400000000003200e80301006e"))
    await self.send(bytes.fromhex("aa0194b61283000012010000f3"))
    await self.send(bytes.fromhex("aa01192842"))  # it starts moving again


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class VSpin:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`VSpin` is deprecated. Please use `VSpinBackend` instead. ")
