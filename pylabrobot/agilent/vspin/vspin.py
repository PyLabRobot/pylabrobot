import asyncio
import ctypes
import json
import logging
import math
import os
import time
import warnings
from typing import Optional

from pylabrobot.events import device_reference, evented_operation, resource_reference
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, ResourceHolder

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
  "Please rotate the bucket to bucket 1 using go_to_position and "
  "then calling set_bucket_1_position_to_current."
)


def _vspin_event_context(
  self: "VSpin",
  g: float = 500,
  duration: float = 60,
  acceleration: float = 0.8,
  deceleration: float = 0.8,
) -> dict:
  """Describe the currently loaded buckets and requested centrifuge run."""
  bucket_resources = [
    {
      "holder": resource_reference(bucket),
      "resource": resource_reference(bucket.resource),
    }
    for bucket in (self.bucket1, self.bucket2)
    if bucket.resource is not None
  ]
  return {
    "device": device_reference(self, name=self.name),
    "resources": [bucket["resource"] for bucket in bucket_resources],
    "bucket_resources": bucket_resources,
    "relative_centrifugal_force": g,
    "duration": duration,
    "acceleration_fraction": acceleration,
    "deceleration_fraction": deceleration,
  }


# ---------------------------------------------------------------------------
# VSpin Driver — FTDI I/O and hardware queries
# ---------------------------------------------------------------------------


class VSpin:
  """FTDI driver for the Agilent VSpin Centrifuge.

  Owns the USB connection, low-level command protocol, and hardware status queries.
  """

  def __init__(self, name: str, device_id: Optional[str] = None):
    """
    Args:
      device_id: The libftdi id for the centrifuge. Find using
        `python -m pylibftdi.examples.list_devices`
    """
    super().__init__()
    self.name = name
    self.io = FTDI(human_readable_device_name="Agilent VSpin Centrifuge", device_id=device_id)
    self.device_id = device_id
    self._bucket_1_remainder: Optional[int] = None
    if device_id is not None:
      self._bucket_1_remainder = _load_vspin_calibrations(device_id)

    self.bucket1 = ResourceHolder(
      name=f"{name}_bucket1",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )
    self.bucket2 = ResourceHolder(
      name=f"{name}_bucket2",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )

    # Door and rotor state, tracked from the commands we issue: the controller has no query for
    # which bucket is parked at the load position.
    self._door_open = False
    self._at_bucket: Optional[ResourceHolder] = None

  @property
  def door_open(self) -> bool:
    """Whether the door was left open by the last door command."""
    return self._door_open

  @property
  def at_bucket(self) -> Optional[ResourceHolder]:
    """The bucket parked at the load position, or None if the rotor is elsewhere."""
    return self._at_bucket

  async def setup(self):
    logger.info("[vSpin %s] connected", self.device_id)
    await self.io.setup()
    for _ in range(3):
      await self.configure_and_initialize()
      await self.send_command(bytes.fromhex("aa002101ff21"))
    await self.send_command(bytes.fromhex("aa002101ff21"))
    await self.send_command(bytes.fromhex("aa01132034"))
    await self.send_command(bytes.fromhex("aa002102ff22"))
    await self.send_command(bytes.fromhex("aa02132035"))
    await self.send_command(bytes.fromhex("aa002103ff23"))
    await self.send_command(bytes.fromhex("aaff1a142d"))

    await self.io.set_baudrate(57600)
    await self.io.set_rts(True)
    await self.io.set_dtr(True)

    await self.send_command(bytes.fromhex("aa01121f32"))
    for _ in range(8):
      await self.send_command(bytes.fromhex("aa0220ff0f30"))
    await self.send_command(bytes.fromhex("aa0220df0f10"))
    await self.send_command(bytes.fromhex("aa0220df0e0f"))
    await self.send_command(bytes.fromhex("aa0220df0c0d"))
    await self.send_command(bytes.fromhex("aa0220df0809"))
    for _ in range(4):
      await self.send_command(bytes.fromhex("aa0226000028"))
    await self.send_command(bytes.fromhex("aa02120317"))
    for _ in range(5):
      await self.send_command(bytes.fromhex("aa0226200048"))
      await self.send_command(bytes.fromhex("aa0226000028"))
    await self.lock_door()

    await self.send_command(bytes.fromhex("aa0226000028"))

    await self.send_command(bytes.fromhex("aa0117021a"))
    await self.send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send_command(bytes.fromhex("aa0117041c"))
    await self.send_command(bytes.fromhex("aa01170119"))

    await self.send_command(bytes.fromhex("aa010b0c"))
    await self.send_command(bytes.fromhex("aa010001"))
    await self.send_command(bytes.fromhex("aa01e605006400000000003200e80301006e"))
    await self.send_command(bytes.fromhex("aa0194b61283000012010000f3"))
    await self.send_command(bytes.fromhex("aa01192842"))

    resp = 0x89
    while resp == 0x89:
      resp = (await self.request_positions_and_tachometer()).status

    # --- almost the same as go to position ---
    await self.send_command(bytes.fromhex("aa0117021a"))
    await self.send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send_command(bytes.fromhex("aa0117041c"))
    await self.send_command(bytes.fromhex("aa01170119"))

    await self.send_command(bytes.fromhex("aa010b0c"))
    await self.send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    new_position = (0).to_bytes(4, byteorder="little")
    await self.send_command(
      bytes.fromhex("aa01d497") + new_position + bytes.fromhex("c3f52800d71a000049")
    )
    # -----------------------------------------

    resp = 0x08
    while resp != 0x09:
      resp = (await self.request_positions_and_tachometer()).status

    await self.send_command(bytes.fromhex("aa0117021a"))

    await self.lock_door()

  async def stop(self):
    logger.info("[vSpin %s] disconnected", self.device_id)
    await self.configure_and_initialize()
    await self.io.stop()

  # -- low-level protocol --

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

  async def send_command(self, cmd: bytes, read_timeout=0.2) -> bytes:
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
    await self.send_command(bytes.fromhex("aaff0f0e"))

  # -- hardware status queries --

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

  async def request_positions_and_tachometer(self) -> "VSpin._StatusPositionTachometer":
    resp = await self.send_command(bytes.fromhex("aa010e0f"))
    if len(resp) == 0:
      raise IOError("Empty status from centrifuge")
    return VSpin._StatusPositionTachometer.from_buffer_copy(resp)

  async def request_position(self) -> int:
    return (await self.request_positions_and_tachometer()).current_position  # type: ignore

  async def request_tachometer(self) -> int:
    """Current speed in rpm."""
    tack_to_rpm = -14.69320388
    return (await self.request_positions_and_tachometer()).tachometer * tack_to_rpm  # type: ignore

  async def request_home_position(self) -> int:
    """Changes during a run, but the bucket 1 position relative to it does not."""
    return (await self.request_positions_and_tachometer()).home_position  # type: ignore

  async def _request_status(self):
    resp = await self.send_command(bytes.fromhex("aa020e10"))
    if len(resp) == 0:
      raise IOError("Empty status from centrifuge. Is the machine on?")
    return resp

  async def request_bucket_locked(self) -> bool:
    resp = await self._request_status()
    return resp[2] & 0b0001 != 0  # type: ignore

  async def request_door_open(self) -> bool:
    resp = await self._request_status()
    return resp[2] & 0b0010 != 0  # type: ignore

  async def request_door_locked(self) -> bool:
    resp = await self._request_status()
    return resp[2] & 0b0100 == 0  # type: ignore

  # -- bucket calibration --

  @property
  def bucket_1_remainder(self) -> int:
    if self._bucket_1_remainder is None:
      raise bucket_1_not_set_error
    return self._bucket_1_remainder

  async def set_bucket_1_position_to_current(self) -> None:
    """Set the current position as bucket 1 position and save calibration."""
    current_position = await self.request_position()
    device_id = await self.io.request_serial()
    remainder = await self.request_home_position() - current_position
    self._bucket_1_remainder = current_position % FULL_ROTATION
    _save_vspin_calibrations(device_id, remainder)

  async def request_bucket_1_position(self) -> int:
    """Get the bucket 1 position based on calibration."""
    if self._bucket_1_remainder is None:
      raise bucket_1_not_set_error
    home_position = await self.request_home_position()
    bucket_1_position_mod_full_rotation = home_position - self.bucket_1_remainder
    current_position = await self.request_position()
    bucket_1_position = (
      FULL_ROTATION
      * math.floor((current_position - bucket_1_position_mod_full_rotation) / FULL_ROTATION + 1)
      + bucket_1_position_mod_full_rotation
    )
    return bucket_1_position

  # -- CentrifugeBackend interface --

  async def open_door(self):
    if await self.request_door_open():
      self._door_open = True
      return
    logger.info("[vSpin %s] open door", self.device_id)
    await self.send_command(bytes.fromhex("aa022600062e"))
    await asyncio.sleep(4)
    self._door_open = True

  async def close_door(self):
    if not (await self.request_door_open()):
      self._door_open = False
      return
    logger.info("[vSpin %s] close door", self.device_id)
    await self.send_command(bytes.fromhex("aa022600042c"))
    await asyncio.sleep(2)
    self._door_open = False

  async def lock_door(self):
    if await self.request_door_open():
      raise RuntimeError("Cannot lock door while it is open.")
    if await self.request_door_locked():
      return
    logger.info("[vSpin %s] lock door", self.device_id)
    await self.send_command(bytes.fromhex("aa0226000028"))

  async def unlock_door(self):
    if not await self.request_door_locked():
      return
    await self.send_command(bytes.fromhex("aa022600042c"))

  async def lock_bucket(self):
    if await self.request_bucket_locked():
      return
    await self.send_command(bytes.fromhex("aa022600072f"))

  async def unlock_bucket(self):
    if not await self.request_bucket_locked():
      return
    await self.send_command(bytes.fromhex("aa022600062e"))

  async def go_to_bucket1(self):
    await self.go_to_position(await self.request_bucket_1_position())
    self._at_bucket = self.bucket1

  async def go_to_bucket2(self):
    await self.go_to_position(await self.request_bucket_1_position() + FULL_ROTATION // 2)
    self._at_bucket = self.bucket2

  async def go_to_position(self, position: int):
    logger.info("[vSpin %s] go_to_position: position=%d", self.device_id, position)
    await self.close_door()
    await self.lock_door()

    position_bytes = position.to_bytes(4, byteorder="little")
    byte_string = bytes.fromhex("aa01d497") + position_bytes + bytes.fromhex("c3f52800d71a0000")
    sum_byte = (sum(byte_string) - 0xAA) & 0xFF
    byte_string += sum_byte.to_bytes(1, byteorder="little")
    await self.send_command(bytes.fromhex("aa0226000028"))
    await self.send_command(bytes.fromhex("aa0117021a"))
    await self.send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send_command(bytes.fromhex("aa0117041c"))
    await self.send_command(bytes.fromhex("aa01170119"))
    await self.send_command(bytes.fromhex("aa010b0c"))
    await self.send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send_command(byte_string)

    while abs(await self.request_position() - position) > 10:
      await asyncio.sleep(0.1)
    await self.open_door()

  @staticmethod
  def g_to_rpm(g: float) -> int:
    r = 10
    rpm = int((g / (1.118 * 10**-5 * r)) ** 0.5)
    return rpm

  @evented_operation("centrifuge.spin", _vspin_event_context)
  async def spin(
    self,
    g: float = 500,
    duration: float = 60,
    acceleration: float = 0.8,
    deceleration: float = 0.8,
  ) -> None:
    """Start a spin cycle.

    Args:
      g: relative centrifugal force, also known as g-force
      duration: time in seconds spent at speed (g)
      acceleration: Acceleration rate as a fraction of maximum (0 to 1, exclusive of 0).
      deceleration: Deceleration rate as a fraction of maximum (0 to 1, exclusive of 0).
    """
    if acceleration <= 0 or acceleration > 1:
      raise ValueError("Acceleration must be within 0-1.")
    if deceleration <= 0 or deceleration > 1:
      raise ValueError("Deceleration must be within 0-1.")
    if g < 1 or g > 1000:
      raise ValueError("G-force must be within 1-1000")
    if duration < 1:
      raise ValueError("Spin time must be at least 1 second")

    if await self.request_door_open():
      await self.close_door()
    if not await self.request_door_locked():
      await self.lock_door()
    if await self.request_bucket_locked():
      await self.unlock_bucket()

    rpm = VSpin.g_to_rpm(g)
    logger.info(
      "[vSpin %s] spin: g=%.1f rpm=%d duration=%.1fs acceleration=%.2f deceleration=%.2f",
      self.device_id,
      g,
      rpm,
      duration,
      acceleration,
      deceleration,
    )

    acceleration_ticks_per_second2 = 12903.2 * acceleration
    rounds_per_second = rpm / 60
    ticks_per_second = rounds_per_second * 8000
    distance_during_acceleration = int(0.5 * (ticks_per_second**2) / acceleration_ticks_per_second2)

    distance_at_speed = ticks_per_second * duration

    current_position = await self.request_position()
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

    await self.send_command(bytes.fromhex("aa0226000028"))
    await self.send_command(bytes.fromhex("aa0117021a"))
    await self.send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self.send_command(bytes.fromhex("aa0117041c"))
    await self.send_command(bytes.fromhex("aa01170119"))
    await self.send_command(bytes.fromhex("aa010b0c"))
    await self.send_command(bytes.fromhex("aa01e60500640000000000fd00803e01000c"))

    await self.send_command(byte_string)

    while (
      await self.request_tachometer() < rpm * 0.95
      and await self.request_position() < final_position
    ):
      await asyncio.sleep(0.1)

    if await self.request_position() < final_position:
      decel_start_position = await self.request_position() + distance_at_speed

      while await self.request_position() < decel_start_position:
        await asyncio.sleep(0.1)

    await self.send_command(bytes.fromhex("aa01e60500640000000000fd00803e01000c"))
    decc = int(9.15 * 100 * deceleration).to_bytes(2, byteorder="little")
    decel_command = bytes.fromhex("aa0194b600000000") + decc + bytes.fromhex("0000")
    decel_command += ((sum(decel_command) - 0xAA) & 0xFF).to_bytes(1, byteorder="little")
    await self.send_command(decel_command)

    await asyncio.sleep(2)

    async def _reset_to_zero():
      await self.send_command(bytes.fromhex("aa0117021a"))
      await self.send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
      await self.send_command(bytes.fromhex("aa0117041c"))
      await self.send_command(bytes.fromhex("aa01170119"))
      await self.send_command(bytes.fromhex("aa010b0c"))
      await self.send_command(bytes.fromhex("aa010001"))
      await self.send_command(bytes.fromhex("aa01e605006400000000003200e80301006e"))
      await self.send_command(bytes.fromhex("aa0194b61283000012010000f3"))
      await self.send_command(bytes.fromhex("aa01192842"))

    await _reset_to_zero()

    start = await self.request_home_position()
    num_tries = 0
    while await self.request_home_position() == start:
      await asyncio.sleep(0.1)
      num_tries += 1
      if num_tries % 25 == 0:
        await _reset_to_zero()
      if num_tries > 100:
        raise RuntimeError("Home position did not change after spin.")

    # The rotor has moved off whichever bucket was parked at the load position.
    self._at_bucket = None
