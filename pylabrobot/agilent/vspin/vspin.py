import asyncio
import ctypes
import json
import logging
import math
import os
import time
import warnings
from dataclasses import dataclass
from typing import Optional

from pylabrobot.capabilities.centrifuging import CentrifugeBackend as _NewCentrifugeBackend
from pylabrobot.capabilities.centrifuging import CentrifugingCapability
from pylabrobot.capabilities.centrifuging.errors import (
  BucketHasPlateError,
  BucketNoPlateError,
  CentrifugeDoorError,
  LoaderNoPlateError,
  NotAtBucketError,
)
from pylabrobot.device import Device, DeviceBackend
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, Resource, ResourceHolder
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.serializer import SerializableMixin

logger = logging.getLogger(__name__)


_vspin_bucket_calibrations_path = os.path.join(
  os.path.expanduser("~"),
  ".pylabrobot",
  "vspin_bucket_calibrations.json",
)


def _load_vspin_calibrations(device_id: str) -> Optional[int]:
  if not os.path.exists(_vspin_bucket_calibrations_path):
    warnings.warn(
      f"No calibration found for VSpin with device id {device_id}. "
      "Please set the bucket 1 position using `set_bucket_1_position_to_current` method after setup.",
      UserWarning,
    )
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


bucket_1_not_set_error = RuntimeError(
  "Bucket 1 position not set. "
  "Please rotate the bucket to bucket 1 using VSpinBackend.go_to_position and "
  "then calling VSpinBackend.set_bucket_1_position_to_current."
)


class VSpinBackend(_NewCentrifugeBackend):
  """Backend for the Agilent VSpin Centrifuge."""

  def __init__(self, device_id: Optional[str] = None):
    """
    Args:
      device_id: The libftdi id for the centrifuge. Find using
        `python -m pylibftdi.examples.list_devices`
    """
    super().__init__()
    self.io = FTDI(human_readable_device_name="Agilent VSpin Centrifuge", device_id=device_id)
    self._bucket_1_remainder: Optional[int] = None
    if device_id is not None:
      self._bucket_1_remainder = _load_vspin_calibrations(device_id)

  async def setup(self):
    await self.io.setup()
    for _ in range(3):
      await self.configure_and_initialize()
      await self._send_command(bytes.fromhex("aa002101ff21"))
    await self._send_command(bytes.fromhex("aa002101ff21"))
    await self._send_command(bytes.fromhex("aa01132034"))
    await self._send_command(bytes.fromhex("aa002102ff22"))
    await self._send_command(bytes.fromhex("aa02132035"))
    await self._send_command(bytes.fromhex("aa002103ff23"))
    await self._send_command(bytes.fromhex("aaff1a142d"))

    await self.io.set_baudrate(57600)
    await self.io.set_rts(True)
    await self.io.set_dtr(True)

    await self._send_command(bytes.fromhex("aa01121f32"))
    for _ in range(8):
      await self._send_command(bytes.fromhex("aa0220ff0f30"))
    await self._send_command(bytes.fromhex("aa0220df0f10"))
    await self._send_command(bytes.fromhex("aa0220df0e0f"))
    await self._send_command(bytes.fromhex("aa0220df0c0d"))
    await self._send_command(bytes.fromhex("aa0220df0809"))
    for _ in range(4):
      await self._send_command(bytes.fromhex("aa0226000028"))
    await self._send_command(bytes.fromhex("aa02120317"))
    for _ in range(5):
      await self._send_command(bytes.fromhex("aa0226200048"))
      await self._send_command(bytes.fromhex("aa0226000028"))
    await self.lock_door()

    await self._send_command(bytes.fromhex("aa0226000028"))

    await self._send_command(bytes.fromhex("aa0117021a"))
    await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self._send_command(bytes.fromhex("aa0117041c"))
    await self._send_command(bytes.fromhex("aa01170119"))

    await self._send_command(bytes.fromhex("aa010b0c"))
    await self._send_command(bytes.fromhex("aa010001"))
    await self._send_command(bytes.fromhex("aa01e605006400000000003200e80301006e"))
    await self._send_command(bytes.fromhex("aa0194b61283000012010000f3"))
    await self._send_command(bytes.fromhex("aa01192842"))

    resp = 0x89
    while resp == 0x89:
      resp = (await self._get_positions_and_tachometer()).status

    # --- almost the same as go to position ---
    await self._send_command(bytes.fromhex("aa0117021a"))
    await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self._send_command(bytes.fromhex("aa0117041c"))
    await self._send_command(bytes.fromhex("aa01170119"))

    await self._send_command(bytes.fromhex("aa010b0c"))
    await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    new_position = (0).to_bytes(4, byteorder="little")  # arbitrary
    await self._send_command(
      bytes.fromhex("aa01d497") + new_position + bytes.fromhex("c3f52800d71a000049")
    )
    # -----------------------------------------

    resp = 0x08
    while resp != 0x09:
      resp = (await self._get_positions_and_tachometer()).status

    await self._send_command(bytes.fromhex("aa0117021a"))

    await self.lock_door()

    # If we have not set the calibration yet, load it now.
    if self._bucket_1_remainder is None:
      device_id = await self.io.get_serial()
      self._bucket_1_remainder = _load_vspin_calibrations(device_id)

  @property
  def bucket_1_remainder(self) -> int:
    if self._bucket_1_remainder is None:
      raise bucket_1_not_set_error
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
      raise bucket_1_not_set_error
    home_position = await self.get_home_position()
    bucket_1_position_mod_full_rotation = home_position - self.bucket_1_remainder
    current_position = await self.get_position()
    bucket_1_position = (
      FULL_ROTATION
      * math.floor((current_position - bucket_1_position_mod_full_rotation) / FULL_ROTATION + 1)
      + bucket_1_position_mod_full_rotation
    )
    return bucket_1_position

  async def stop(self):
    await self.configure_and_initialize()
    await self.io.stop()

  class _StatusPositionTachometer(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
      ("status", ctypes.c_uint8),
      ("current_position", ctypes.c_uint32),
      ("unknown1", ctypes.c_uint8),
      ("tachometer", ctypes.c_int16),
      ("unknown2", ctypes.c_uint8),
      ("home_position", ctypes.c_uint32),
      ("checksum", ctypes.c_uint8),
    ]

  async def _get_positions_and_tachometer(self) -> _StatusPositionTachometer:
    resp = await self._send_command(bytes.fromhex("aa010e0f"))
    if len(resp) == 0:
      raise IOError("Empty status from centrifuge")
    return VSpinBackend._StatusPositionTachometer.from_buffer_copy(resp)

  async def get_position(self) -> int:
    return (await self._get_positions_and_tachometer()).current_position  # type: ignore

  async def get_tachometer(self) -> int:
    """Current speed in rpm."""
    tack_to_rpm = -14.69320388
    return (await self._get_positions_and_tachometer()).tachometer * tack_to_rpm  # type: ignore

  async def get_home_position(self) -> int:
    """Changes during a run, but the bucket 1 position relative to it does not."""
    return (await self._get_positions_and_tachometer()).home_position  # type: ignore

  async def _get_status(self):
    resp = await self._send_command(bytes.fromhex("aa020e10"))
    if len(resp) == 0:
      raise IOError("Empty status from centrifuge. Is the machine on?")
    return resp

  async def get_bucket_locked(self) -> bool:
    resp = await self._get_status()
    return resp[2] & 0b0001 != 0  # type: ignore

  async def get_door_open(self) -> bool:
    resp = await self._get_status()
    return resp[2] & 0b0010 != 0  # type: ignore

  async def get_door_locked(self) -> bool:
    resp = await self._get_status()
    return resp[2] & 0b0100 == 0  # type: ignore

  # Centrifuge communication: read_resp, send

  async def _read_resp(self, timeout: float = 20) -> bytes:
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

  async def _send_command(self, cmd: bytes, read_timeout=0.2) -> bytes:
    written = await self.io.write(bytes(cmd))

    if written != len(cmd):
      raise RuntimeError("Failed to write all bytes")
    return await self._read_resp(timeout=read_timeout)

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
    await self._send_command(bytes.fromhex("aaff0f0e"))

  # Centrifuge operations (CentrifugeBackend interface)

  async def open_door(self):
    if await self.get_door_open():
      return
    await self._send_command(bytes.fromhex("aa022600062e"))
    await asyncio.sleep(4)

  async def close_door(self):
    if not (await self.get_door_open()):
      return
    await self._send_command(bytes.fromhex("aa022600042c"))
    await asyncio.sleep(2)

  async def lock_door(self):
    if await self.get_door_open():
      raise RuntimeError("Cannot lock door while it is open.")
    if await self.get_door_locked():
      return
    await self._send_command(bytes.fromhex("aa0226000028"))

  async def unlock_door(self):
    if not await self.get_door_locked():
      return
    await self._send_command(bytes.fromhex("aa022600042c"))

  async def lock_bucket(self):
    if await self.get_bucket_locked():
      return
    await self._send_command(bytes.fromhex("aa022600072f"))

  async def unlock_bucket(self):
    if not await self.get_bucket_locked():
      return
    await self._send_command(bytes.fromhex("aa022600062e"))

  async def go_to_bucket1(self):
    await self.go_to_position(await self.get_bucket_1_position())

  async def go_to_bucket2(self):
    await self.go_to_position(await self.get_bucket_1_position() + FULL_ROTATION // 2)

  async def go_to_position(self, position: int):
    await self.close_door()
    await self.lock_door()

    position_bytes = position.to_bytes(4, byteorder="little")
    byte_string = bytes.fromhex("aa01d497") + position_bytes + bytes.fromhex("c3f52800d71a0000")
    sum_byte = (sum(byte_string) - 0xAA) & 0xFF
    byte_string += sum_byte.to_bytes(1, byteorder="little")
    await self._send_command(bytes.fromhex("aa0226000028"))
    await self._send_command(bytes.fromhex("aa0117021a"))
    await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self._send_command(bytes.fromhex("aa0117041c"))
    await self._send_command(bytes.fromhex("aa01170119"))
    await self._send_command(bytes.fromhex("aa010b0c"))
    await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self._send_command(byte_string)

    while abs(await self.get_position() - position) > 10:
      await asyncio.sleep(0.1)
    await self.open_door()

  @staticmethod
  def g_to_rpm(g: float) -> int:
    r = 10
    rpm = int((g / (1.118 * 10**-5 * r)) ** 0.5)
    return rpm

  @dataclass
  class SpinParams(BackendParams):
    acceleration: float = 0.8
    deceleration: float = 0.8

  async def spin(
    self,
    g: float = 500,
    duration: float = 60,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    """Start a spin cycle.

    Args:
      g: relative centrifugal force, also known as g-force
      duration: time in seconds spent at speed (g)
      backend_params: VSpinBackend.SpinParams with acceleration and deceleration (0-1).
    """
    if not isinstance(backend_params, self.SpinParams):
      backend_params = VSpinBackend.SpinParams()

    acceleration = backend_params.acceleration
    deceleration = backend_params.deceleration

    if acceleration <= 0 or acceleration > 1:
      raise ValueError("Acceleration must be within 0-1.")
    if deceleration <= 0 or deceleration > 1:
      raise ValueError("Deceleration must be within 0-1.")
    if g < 1 or g > 1000:
      raise ValueError("G-force must be within 1-1000")
    if duration < 1:
      raise ValueError("Spin time must be at least 1 second")

    if await self.get_door_open():
      await self.close_door()
    if not await self.get_door_locked():
      await self.lock_door()
    if await self.get_bucket_locked():
      await self.unlock_bucket()

    rpm = VSpinBackend.g_to_rpm(g)

    acceleration_ticks_per_second2 = 12903.2 * acceleration
    rounds_per_second = rpm / 60
    ticks_per_second = rounds_per_second * 8000
    distance_during_acceleration = int(0.5 * (ticks_per_second**2) / acceleration_ticks_per_second2)

    distance_at_speed = ticks_per_second * duration

    current_position = await self.get_position()
    final_position = int(current_position + distance_during_acceleration + distance_at_speed)

    if final_position > 2**32 - 1:
      raise NotImplementedError(
        "We don't know what happens if the destination position exceeds 2^32-1. "
        "Please report this issue on discuss.pylabrobot.org."
      )

    position_b = final_position.to_bytes(4, byteorder="little")
    rpm_b = int(rpm * 4473.925).to_bytes(4, byteorder="little")
    acceleration_b = int(9.15 * 100 * acceleration).to_bytes(4, byteorder="little")

    byte_string = bytes.fromhex("aa01d497") + position_b + rpm_b + acceleration_b
    checksum = (sum(byte_string) - 0xAA) & 0xFF
    byte_string += checksum.to_bytes(1, byteorder="little")

    await self._send_command(bytes.fromhex("aa0226000028"))
    await self._send_command(bytes.fromhex("aa0117021a"))
    await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self._send_command(bytes.fromhex("aa0117041c"))
    await self._send_command(bytes.fromhex("aa01170119"))
    await self._send_command(bytes.fromhex("aa010b0c"))
    await self._send_command(bytes.fromhex("aa01e60500640000000000fd00803e01000c"))

    await self._send_command(byte_string)

    while await self.get_tachometer() < rpm * 0.95 and await self.get_position() < final_position:
      await asyncio.sleep(0.1)

    if await self.get_position() < final_position:
      decel_start_position = await self.get_position() + distance_at_speed

      while await self.get_position() < decel_start_position:
        await asyncio.sleep(0.1)

    await self._send_command(bytes.fromhex("aa01e60500640000000000fd00803e01000c"))
    decc = int(9.15 * 100 * deceleration).to_bytes(2, byteorder="little")
    decel_command = bytes.fromhex("aa0194b600000000") + decc + bytes.fromhex("0000")
    decel_command += ((sum(decel_command) - 0xAA) & 0xFF).to_bytes(1, byteorder="little")
    await self._send_command(decel_command)

    await asyncio.sleep(2)

    async def _reset_to_zero():
      await self._send_command(bytes.fromhex("aa0117021a"))
      await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
      await self._send_command(bytes.fromhex("aa0117041c"))
      await self._send_command(bytes.fromhex("aa01170119"))
      await self._send_command(bytes.fromhex("aa010b0c"))
      await self._send_command(bytes.fromhex("aa010001"))
      await self._send_command(bytes.fromhex("aa01e605006400000000003200e80301006e"))
      await self._send_command(bytes.fromhex("aa0194b61283000012010000f3"))
      await self._send_command(bytes.fromhex("aa01192842"))

    await _reset_to_zero()

    start = await self.get_home_position()
    num_tries = 0
    while await self.get_home_position() == start:
      await asyncio.sleep(0.1)
      num_tries += 1
      if num_tries % 25 == 0:
        await _reset_to_zero()
      if num_tries > 100:
        raise RuntimeError("Home position did not change after spin.")


class Access2Backend(DeviceBackend):
  """Backend for the Agilent Access2 centrifuge loader."""

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
    super().__init__()
    self.io = FTDI(human_readable_device_name="Agilent Access2 Loader", device_id=device_id)
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
    await self.send_command(bytes.fromhex("1105000e00440b00000000000000007041020203c7"))

  async def stop(self):
    logger.debug("[loader] stop")
    await self.io.stop()

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
    """Only tested for 1cm plate, 3mm pickup height."""
    logger.debug("[loader] load")

    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b000100004040000020410200a5cb"))

    r = await self.send_command(bytes.fromhex("1105000300500000b3dc"))
    if r == bytes.fromhex("1105000800510500000300000079f1"):
      raise RuntimeError("no plate found on stage")

    await self.send_command(bytes.fromhex("1105000a00460700018fc2b540020023dc"))
    await self.send_command(bytes.fromhex("1105000e00440b000200004040000020410300ee00"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020015fd"))
    await self.send_command(bytes.fromhex("1105000e00440b0000000040400000204102007d82"))

  async def unload(self):
    """Only tested for 1cm plate, 3mm pickup height."""
    logger.debug("[loader] unload")

    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b000200004040000020410200dd31"))

    r = await self.send_command(bytes.fromhex("1105000300500000b3dc"))
    if r == bytes.fromhex("1105000800510500000300000079f1"):
      raise RuntimeError("no plate found in centrifuge")

    await self.send_command(bytes.fromhex("1105000a00460700017b14b6400200d57a"))
    await self.send_command(bytes.fromhex("1105000e00440b00010000404000002041030096fa"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020015fd"))
    await self.send_command(bytes.fromhex("1105000e00440b00000000000000002041020056be"))


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class VSpin(Resource, Device):
  """Agilent VSpin Centrifuge."""

  def __init__(
    self,
    name: str,
    device_id: Optional[str] = None,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
  ):
    backend = VSpinBackend(device_id=device_id)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Agilent VSpin",
      category="centrifuge",
    )
    Device.__init__(self, backend=backend)
    self._backend: VSpinBackend = backend

    bucket1 = ResourceHolder(
      name=f"{name}_bucket1",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )
    bucket2 = ResourceHolder(
      name=f"{name}_bucket2",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )
    self.assign_child_resource(bucket1, location=Coordinate.zero())
    self.assign_child_resource(bucket2, location=Coordinate.zero())

    self.centrifuging = CentrifugingCapability(backend=backend, buckets=(bucket1, bucket2))
    self._capabilities = [self.centrifuging]

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}


class Access2(ResourceHolder, Device):
  """Agilent Access2 centrifuge loader."""

  def __init__(
    self,
    name: str,
    device_id: str,
    vspin: VSpin,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
  ):
    backend = Access2Backend(device_id=device_id)
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Agilent Access2",
      category="loader",
      child_location=Coordinate.zero(),
    )
    Device.__init__(self, backend=backend)
    self._backend: Access2Backend = backend
    self._vspin = vspin

  def serialize(self) -> dict:
    return {**ResourceHolder.serialize(self), **Device.serialize(self)}

  async def load(self) -> None:
    centrifuging = self._vspin.centrifuging

    if not centrifuging.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to load a plate.")
    if centrifuging.at_bucket is None:
      raise NotAtBucketError(
        "Centrifuge must be at a bucket to load a plate. "
        "Use centrifuging.go_to_bucket1() or centrifuging.go_to_bucket2()."
      )
    if self.resource is None:
      raise LoaderNoPlateError("Loader must have a plate to load.")
    if centrifuging.at_bucket.resource is not None:
      raise BucketHasPlateError("Bucket must be empty to load a plate.")

    await self._backend.load()

    centrifuging.at_bucket.assign_child_resource(self.resource, location=Coordinate.zero())

  async def unload(self) -> None:
    centrifuging = self._vspin.centrifuging

    if not centrifuging.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to unload a plate.")
    if centrifuging.at_bucket is None:
      raise NotAtBucketError(
        "Centrifuge must be at a bucket to unload a plate. "
        "Use centrifuging.go_to_bucket1() or centrifuging.go_to_bucket2()."
      )
    if centrifuging.at_bucket.resource is None:
      raise BucketNoPlateError("Bucket must have a plate to unload.")

    await self._backend.unload()

    self.assign_child_resource(centrifuging.at_bucket.resource)
