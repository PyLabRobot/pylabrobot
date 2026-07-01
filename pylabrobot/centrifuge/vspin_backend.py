import asyncio
import ctypes
import json
import logging
import math
import os
import time
import warnings
from typing import Literal, Optional

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

  async def load(self, grip_steps: Literal[1, 2, 3, 4] = 1):
    """Load a plate into the centrifuge.

    Args:
      grip_steps: Number of steps taken to tighten the grip.
        Higher values may improve grip for certain plate types.
    """
    if not grip_steps not in (1, 2, 3, 4):
      raise ValueError("grip_steps must be between 1 and 4")
    logger.debug("[loader] load")

    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b000100004040000020410200a5cb"))

    # laser check
    r = await self.send_command(bytes.fromhex("1105000300500000b3dc"))
    if r == bytes.fromhex("1105000800510500000300000079f1"):
      raise LoaderNoPlateError("no plate found on stage")

    await self.send_command(bytes.fromhex("1105000a00460700018fc2b540020023dc"))
    for _ in range(grip_steps):
      await self.send_command(bytes.fromhex("1105000a00420700010000803f02008c64"))
    await self.send_command(bytes.fromhex("1105000e00440b000200004040000020410300ee00"))
    await self.send_command(bytes.fromhex("1105000a004607000100000000020015fd"))
    await self.send_command(bytes.fromhex("1105000e00440b0000000040400000204102007d82"))

  async def unload(self, grip_steps: Literal[1, 2, 3, 4] = 1):
    """Unload a plate from the centrifuge.

    Args:
      grip_steps:  Number of steps taken to tighten the grip.
        Higher values may improve grip for certain plate types.
    """
    if grip_steps not in (1, 2, 3, 4):
      raise ValueError("grip_steps must be between 1 and 4")
    logger.debug("[loader] unload")

    await self.send_command(bytes.fromhex("1105000a004607000100000000020235bf"))
    await self.send_command(bytes.fromhex("1105000e00440b000200004040000020410200dd31"))

    # laser check
    r = await self.send_command(bytes.fromhex("1105000300500000b3dc"))
    if r == bytes.fromhex("1105000800510500000300000079f1"):
      raise LoaderNoPlateError("no plate found in centrifuge")

    await self.send_command(bytes.fromhex("1105000a00460700017b14b6400200d57a"))
    for _ in range(grip_steps):
      await self.send_command(bytes.fromhex("1105000a00420700010000803f02008c64"))
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
    warnings.warn(
      f"No calibration found for VSpin with device id {device_id}. "
      f"Using the default bucket 1 offset of home + {DEFAULT_BUCKET_1_OFFSET} ticks. "
      "Use `set_bucket_1_position_to_current` after setup to override it.",
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
DEFAULT_BUCKET_1_OFFSET: int = 5337
DEFAULT_BUCKET_1_REMAINDER: int = -DEFAULT_BUCKET_1_OFFSET
DEFAULT_READ_TIMEOUT: float = 0.2
COMMAND_GAP_SECONDS: float = 0.05
CONTROLLER_CONNECT_SETTLE_SECONDS: float = 2.0
INITIALIZE_PACKET_GAP_SECONDS: float = 0.01
STATUS_POLL_INTERVAL: float = 0.15
POSITION_TOLERANCE: int = 15
POSITION_SETTLE_TOLERANCE: int = 200
POSITION_MOVE_ATTEMPTS: int = 2
SPIN_START_ATTEMPTS: int = 2
HOMING_TIMEOUT: float = 60.0
HOMING_MIN_WAIT_SECONDS: float = 1.0
TACH_TO_RPM: float = -14.69320388
DOOR_OPEN_SETTLE_SECONDS: float = 2.0
DOOR_UNLOCK_TO_OPEN_SETTLE_SECONDS: float = 0.5
DOOR_CLOSE_SETTLE_SECONDS: float = 1.0
PNEUMATIC_SETTLE_SECONDS: float = 0.35

_KNOWN_VSPIN_STATUSES = {0x08, 0x09, 0x0B, 0x11, 0x18, 0x19, 0x88, 0x89, 0x91, 0x99}
_IDLE_VSPIN_STATUSES = {0x09, 0x0B, 0x11, 0x89, 0x91}

_VSPIN_MODEL_ALIASES = {
  "agilent": "agilent",
  "velocity11": "velocity11",
  "v11": "velocity11",
}

_VSPIN_HUMAN_READABLE_NAMES = {
  "agilent": "Agilent VSpin Centrifuge",
  "velocity11": "Velocity11 VSpin Centrifuge",
}

_VSPIN_COMMANDS = {
  "agilent": {
    "open_door": bytes.fromhex("aa022600062e"),
    "close_door": bytes.fromhex("aa022600042c"),
    "lock_door": bytes.fromhex("aa0226000028"),
    "unlock_door": bytes.fromhex("aa022600042c"),
    "lock_bucket": bytes.fromhex("aa022600072f"),
    "unlock_bucket": bytes.fromhex("aa022600062e"),
  },
  "velocity11": {
    "open_door": bytes.fromhex("aa022600072f"),
    "close_door": bytes.fromhex("aa022600052d"),
    "lock_door": bytes.fromhex("aa0226000028"),
    "unlock_door": bytes.fromhex("aa022600042c"),
    "lock_bucket": bytes.fromhex("aa0226000129"),
    "unlock_bucket": bytes.fromhex("aa0226200048"),
  },
}


def _normalize_vspin_model(model: str) -> str:
  normalized_model = model.lower()
  if normalized_model not in _VSPIN_MODEL_ALIASES:
    raise ValueError("model must be 'agilent', 'velocity11', or 'v11'")
  return _VSPIN_MODEL_ALIASES[normalized_model]


def _with_vspin_checksum(cmd: bytes) -> bytes:
  """Return ``cmd`` with the final VSpin checksum byte recomputed."""
  if len(cmd) <= 2 or cmd[0] != 0xAA:
    return cmd
  payload = cmd[1:-1]
  return b"\xaa" + payload + bytes([sum(payload) & 0xFF])


def _build_vspin_position_command(position: int) -> bytes:
  position_bytes = int(position).to_bytes(4, byteorder="little")
  payload = b"\x01\xd4\x97" + position_bytes + bytes.fromhex("c3f52800d71a0000")
  return _with_vspin_checksum(b"\xaa" + payload + b"\x00")


def _build_vspin_spin_command(
  current_position: int,
  rpm: int,
  duration: float,
  acceleration: float,
) -> tuple[bytes, int]:
  ticks_per_second = (int(rpm) / 60.0) * FULL_ROTATION
  acceleration_ticks_per_second2 = 12903.2 * float(acceleration)
  distance_during_acceleration = int(0.5 * (ticks_per_second**2) / acceleration_ticks_per_second2)
  distance_at_speed = ticks_per_second * float(duration)
  final_position = int(current_position + distance_during_acceleration + distance_at_speed)

  if final_position > 2**32 - 1:
    raise NotImplementedError(
      "We don't know what happens if the destination position exceeds 2^32-1. "
      "Please report this issue on discuss.pylabrobot.org."
    )

  position_bytes = final_position.to_bytes(4, byteorder="little")
  rpm_bytes = int(int(rpm) * 4473.925).to_bytes(4, byteorder="little")
  acceleration_bytes = int(9.15 * 100 * float(acceleration)).to_bytes(
    4, byteorder="little"
  )
  payload = b"\x01\xd4\x97" + position_bytes + rpm_bytes + acceleration_bytes
  return _with_vspin_checksum(b"\xaa" + payload + b"\x00"), final_position


def _build_vspin_deceleration_command(deceleration: float) -> bytes:
  deceleration_bytes = int(9.15 * 100 * float(deceleration)).to_bytes(
    2, byteorder="little"
  )
  return _with_vspin_checksum(
    bytes.fromhex("aa0194b600000000") + deceleration_bytes + b"\x00\x00\x00"
  )


def _normalize_vspin_home_position(home_position: int) -> int:
  return int(home_position) % FULL_ROTATION


def _vspin_position_matches_target(position: int, target: int, tolerance: int) -> bool:
  absolute_delta = abs(int(position) - int(target))
  if absolute_delta <= tolerance:
    return True

  angular_delta = abs((int(position) % FULL_ROTATION) - (int(target) % FULL_ROTATION))
  angular_delta = min(angular_delta, FULL_ROTATION - angular_delta)
  return angular_delta <= tolerance


bucket_1_not_set_error = RuntimeError(
  "Bucket 1 position not set. "
  "Please rotate the bucket to bucket 1 using VSpinBackend.go_to_position and "
  "then calling VSpinBackend.set_bucket_1_position_to_current."
)


class VSpinBackend(CentrifugeBackend):
  """Backend for Agilent and Velocity11 VSpin centrifuges.

  The Agilent and Velocity11 labels share most of the wire protocol. Known
  firmware differences are isolated behind ``model`` so parsing, polling,
  homing, and spin behavior stay in one implementation.
  """

  def __init__(
    self,
    device_id: Optional[str] = None,
    model: str = "agilent",
    try_runtime_attach_after_startup_failure: bool = False,
  ):
    """
    Args:
      device_id: The libftdi id for the centrifuge.
        Find using `python -m pylibftdi.examples.list_devices`.
      model: VSpin command flavor. Use ``"agilent"`` for the existing backend
        behavior and ``"velocity11"`` or ``"v11"`` for older firmware.
      try_runtime_attach_after_startup_failure: Try to attach to a controller
        that is already in 57600-baud runtime mode before cold startup, and
        retry that attach path if the normal 19200-baud startup handshake fails.
        This only applies to the Velocity11 model path.
    """
    self._model = _normalize_vspin_model(model)
    self.io = FTDI(
      human_readable_device_name=_VSPIN_HUMAN_READABLE_NAMES[self._model],
      device_id=device_id,
    )
    self._bucket_1_remainder: Optional[int] = (
      None if self._model == "agilent" else DEFAULT_BUCKET_1_REMAINDER
    )
    self._last_position: int = 0
    self._last_home_position: int = 0
    self._home_sensor_position: Optional[int] = None
    self._motion_is_prepared = False
    self._stop_requested = False
    self._command_lock: Optional[asyncio.Lock] = None
    self._last_command_at = 0.0
    self._try_runtime_attach_after_startup_failure = try_runtime_attach_after_startup_failure
    # only attempt loading calibration if device_id is not None
    # if it is None, we will load it after setup when we can query the device id from the io
    if device_id is not None:
      calibration = _load_vspin_calibrations(device_id)
      if calibration is not None:
        self._bucket_1_remainder = calibration

  async def setup(self):
    await self.io.setup()
    try:
      self._command_lock = asyncio.Lock()
      self._motion_is_prepared = False

      if self._model == "agilent":
        await self._setup_agilent()
      elif self._model == "velocity11":
        await self._setup_velocity11()
      else:
        raise ValueError(f"Unsupported VSpin model: {self._model}")

      if self._bucket_1_remainder is None:
        device_id = await self.io.get_serial()
        self._bucket_1_remainder = _load_vspin_calibrations(device_id)
      elif self._bucket_1_remainder == DEFAULT_BUCKET_1_REMAINDER:
        device_id = await self.io.get_serial()
        calibration = _load_vspin_calibrations(device_id)
        if calibration is not None:
          self._bucket_1_remainder = calibration
    except Exception:
      await self._close_connection_cleanly()
      raise

  async def _setup_velocity11(self) -> None:
    attached_to_runtime = False
    if self._try_runtime_attach_after_startup_failure:
      attached_to_runtime = await self._try_attach_to_runtime_controller()
      if attached_to_runtime:
        logger.info("[vspin] Attached to runtime controller before cold startup")

    if not attached_to_runtime:
      await self.configure_and_initialize()
      try:
        await self._startup_handshake()
        await self._enable_telemetry_and_pneumatics()
      except TimeoutError as e:
        if (
          self._try_runtime_attach_after_startup_failure
          and await self._try_attach_to_runtime_controller()
        ):
          logger.info("[vspin] Recovered controller after partial startup")
        else:
          raise TimeoutError(
            "VSpin did not respond to the 19200-baud startup handshake. "
            "Power-cycle or restart the VSpin controller, then try setup again."
          ) from e

    await self._home_rotor()

  async def _setup_agilent(self) -> None:
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

    await self._send_command(bytes.fromhex("aa0117021a"))
    await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    await self._send_command(bytes.fromhex("aa0117041c"))
    await self._send_command(bytes.fromhex("aa01170119"))

    await self._send_command(bytes.fromhex("aa010b0c"))
    await self._send_command(bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"))
    new_position = (0).to_bytes(4, byteorder="little")
    await self._send_command(
      bytes.fromhex("aa01d497") + new_position + bytes.fromhex("c3f52800d71a000049")
    )

    resp = 0x08
    while resp != 0x09:
      resp = (await self._get_positions_and_tachometer()).status

    await self._send_command(bytes.fromhex("aa0117021a"))
    await self.lock_door()

  @property
  def bucket_1_remainder(self) -> int:
    if self._bucket_1_remainder is None:
      raise bucket_1_not_set_error
    return self._bucket_1_remainder

  async def set_bucket_1_position_to_current(self) -> None:
    """Set the current position as bucket 1 position and save calibration."""
    current_position = await self.get_position()
    device_id = await self.io.get_serial()
    home_sensor_position = (
      self._home_sensor_position
      if self._home_sensor_position is not None
      else await self.get_home_position()
    )
    home_sensor_position = _normalize_vspin_home_position(home_sensor_position)
    home_rotations = (current_position - home_sensor_position) // FULL_ROTATION
    home_position = home_sensor_position + home_rotations * FULL_ROTATION
    remainder = home_position - current_position
    self._bucket_1_remainder = remainder
    _save_vspin_calibrations(device_id, remainder)

  async def get_bucket_1_position(self) -> int:
    """Get the bucket 1 position based on calibration.
    Normally it is the home position minus the remainder (calibration).
    The bucket 1 position must be greater than the current position, so we find
    the first position greater than the current position by adding full rotations if needed.
    """
    return await self._get_bucket_position(1)

  async def _get_bucket_position(self, bucket_num: int) -> int:
    if bucket_num not in (1, 2):
      raise ValueError("bucket_num must be 1 or 2")
    if self._bucket_1_remainder is None:
      raise bucket_1_not_set_error

    home_position = self._home_sensor_position
    if home_position == 0:
      home_position = None
    if home_position is None:
      live_home_position = await self.get_home_position()
      if live_home_position == 0:
        await self._home_rotor()
        home_position = self._home_sensor_position
        if home_position == 0:
          home_position = None
      else:
        home_position = live_home_position

    if home_position is None:
      raise RuntimeError("VSpin home sensor position is unknown. Run setup or _home_rotor first.")

    current_position = await self.get_position()
    target_position = home_position - self.bucket_1_remainder
    if bucket_num == 2:
      target_position += FULL_ROTATION // 2

    while target_position <= current_position + POSITION_TOLERANCE:
      target_position += FULL_ROTATION

    return target_position

  async def stop(self):
    await self._close_connection_cleanly()

  def _command(self, name: str) -> bytes:
    return _VSPIN_COMMANDS[self._model][name]

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

  @staticmethod
  def _make_status(
    status: int,
    current_position: int,
    unknown1: int = 0,
    tachometer: int = 0,
    unknown2: int = 0,
    home_position: int = 0,
    checksum: int = 0,
  ) -> _StatusPositionTachometer:
    parsed = VSpinBackend._StatusPositionTachometer()
    parsed.status = status
    parsed.current_position = current_position
    parsed.unknown1 = unknown1
    parsed.tachometer = tachometer
    parsed.unknown2 = unknown2
    parsed.home_position = home_position
    parsed.checksum = checksum
    return parsed

  @staticmethod
  def _find_status_packet(resp: bytes) -> Optional[_StatusPositionTachometer]:
    for start in range(max(0, len(resp) - 13)):
      packet = resp[start : start + 14]
      if len(packet) < 14 or packet[0] not in _KNOWN_VSPIN_STATUSES:
        continue
      if (sum(packet[:-1]) & 0xFF) != packet[-1]:
        continue
      return VSpinBackend._StatusPositionTachometer.from_buffer_copy(packet)
    return None

  @staticmethod
  def _find_short_status(resp: bytes) -> Optional[int]:
    if len(resp) == 5 and resp[0] == 0x00 and (sum(resp[:-1]) & 0xFF) == resp[-1]:
      if resp[2] in _KNOWN_VSPIN_STATUSES:
        return resp[2]

    for index, value in enumerate(resp):
      if value not in _KNOWN_VSPIN_STATUSES:
        continue
      if len(resp) == 1:
        return value
      if index + 1 < len(resp) and resp[index + 1] == value:
        return value
    return None

  def _parse_position_status(self, resp: bytes) -> _StatusPositionTachometer:
    full_status = self._find_status_packet(resp)
    if full_status is not None:
      return full_status

    short_status = self._find_short_status(resp)
    if short_status is not None:
      return self._make_status(
        status=short_status,
        current_position=self._last_position,
        home_position=self._last_home_position,
      )

    return self._make_status(
      status=0x19,
      current_position=self._last_position,
      home_position=self._last_home_position,
    )

  async def _get_positions_and_tachometer(self) -> _StatusPositionTachometer:
    """Returns 14 bytes

    Example:
      11 22 25 00 00 4f 00 00 18 e0 05 00 00 a4
                                             ^^ checksum
                                 ^^ ^^ ^^ ^^ home position
                              ^^ ? (probably binary status objects)
                        ^^ ^^ tachometer
                     ^^ ? (probably binary status objects)
         ^^ ^^ ^^ ^^ current position
      ^^
      - First byte (index 0):
        - 11 = 0b0001011 = idle
        - 13 = 0b0001101 = unknown
        - 08 = 0b0001000 = spinning
        - 09 = 0b0001001 = also spinning but different
        - 19 = 0b0010011 = unknown
        - 88 = 0b1011000 = unknown
        - 89 = 0b1011001 = unknown
      - 10th to 13th byte (index 9-12) = Homing Position
      - Last byte (index 13) = checksum
    """
    resp = await self._send_command(bytes.fromhex("aa010e0f"), expected_len=14)
    status = self._parse_position_status(resp)
    self._last_position = int(status.current_position)
    self._last_home_position = int(status.home_position)
    return status

  async def _get_full_positions_and_tachometer(self) -> _StatusPositionTachometer:
    resp = await self._send_command(
      bytes.fromhex("aa01121f32"),
      read_timeout=0.40,
      expected_len=14,
    )
    status = self._parse_position_status(resp)
    self._last_position = int(status.current_position)
    self._last_home_position = int(status.home_position)
    return status

  async def get_position(self) -> int:
    return (await self._get_positions_and_tachometer()).current_position  # type: ignore

  async def get_tachometer(self) -> int:
    """current speed in rpm"""
    return (await self._get_positions_and_tachometer()).tachometer * TACH_TO_RPM  # type: ignore

  async def get_home_position(self) -> int:
    """changes during a run, but the bucket 1 position relative to it does not"""
    return (await self._get_positions_and_tachometer()).home_position  # type: ignore

  async def _get_status(self):
    """
    examples:
    - 0080d0015
    - 0080f0015
    """

    resp = await self._send_command(bytes.fromhex("aa020e10"), expected_len=5)
    if len(resp) < 3:
      raise IOError(f"Invalid status from centrifuge: {resp.hex() or '(empty)'}")
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

  async def _read_resp(
    self,
    timeout: float = DEFAULT_READ_TIMEOUT,
    expected_len: Optional[int] = None,
    quiet_time: float = 0.05,
  ) -> bytes:
    """Read raw binary VSpin responses.

    VSpin status replies are usually 2, 5, or 14 raw bytes and do not end in
    CR. Waiting for 0x0d creates avoidable timeouts on normal status polling.
    """
    data = b""
    start_time = time.monotonic()
    last_data_time: Optional[float] = None

    while time.monotonic() - start_time < timeout:
      chunk = await self.io.read(25)
      if chunk:
        data += bytes(chunk)
        last_data_time = time.monotonic()
        if expected_len is not None and len(data) >= expected_len:
          break
        continue

      if (
        data
        and expected_len is None
        and last_data_time is not None
        and time.monotonic() - last_data_time >= quiet_time
      ):
        break

      await asyncio.sleep(0.003)

    logger.debug("[vspin] Read %s", data.hex())
    return data

  async def _send_safe(
    self,
    cmd: bytes,
    retries: int = 3,
    timeout: float = DEFAULT_READ_TIMEOUT,
    expect_response: bool = True,
    expected_len: Optional[int] = None,
  ) -> bytes:
    for attempt in range(1, retries + 1):
      resp = await self._send_command(
        cmd,
        read_timeout=timeout,
        expected_len=expected_len,
      )
      if resp or not expect_response:
        return resp

      logger.debug(
        "[vspin] Empty response to %s (attempt %d/%d)",
        cmd.hex(),
        attempt,
        retries,
      )
      await asyncio.sleep(0.15)

    raise TimeoutError(f"No response to VSpin command {cmd.hex()}")

  @staticmethod
  def _expected_response_len(cmd: bytes) -> Optional[int]:
    if cmd == bytes.fromhex("aa010e0f"):
      return 14
    if cmd == bytes.fromhex("aa020e10"):
      return 5
    if cmd in (bytes.fromhex("aa002101ff21"), bytes.fromhex("aa002102ff22")):
      return 2
    if cmd in (bytes.fromhex("aa01132034"), bytes.fromhex("aa02132035")):
      return 4
    return None

  def _get_command_lock(self) -> asyncio.Lock:
    if self._command_lock is None:
      self._command_lock = asyncio.Lock()
    return self._command_lock

  async def _send_command(
    self,
    cmd: bytes,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    expected_len: Optional[int] = None,
  ) -> bytes:
    cmd = _with_vspin_checksum(bytes(cmd))
    expected_len = expected_len or self._expected_response_len(cmd)
    lock = self._get_command_lock()

    logger.debug("[vspin] Sending %s", cmd.hex())
    async with lock:
      command_gap = COMMAND_GAP_SECONDS - (time.monotonic() - self._last_command_at)
      if command_gap > 0:
        await asyncio.sleep(command_gap)
      written = await self.io.write(cmd)
      if written != len(cmd):
        raise RuntimeError(f"Failed to write all bytes ({written}/{len(cmd)} bytes written)")
      resp = await self._read_resp(timeout=read_timeout, expected_len=expected_len)
      self._last_command_at = time.monotonic()

    logger.debug("[vspin] Response %s", resp.hex())
    return resp

  async def _startup_handshake(self) -> None:
    await self._send_safe(bytes.fromhex("aa002101ff21"), timeout=0.60, expected_len=2)
    await self._send_safe(bytes.fromhex("aa01132034"), timeout=0.60, expected_len=4)
    await self._send_safe(bytes.fromhex("aa002102ff22"), timeout=0.60, expected_len=2)
    await self._send_safe(bytes.fromhex("aa02132035"), timeout=0.60, expected_len=4)

    # The original software writes this and then tolerates silence for roughly
    # two seconds while the controller transitions into its startup state.
    await self._send_safe(
      bytes.fromhex("aa002103ff23"),
      timeout=0.15,
      expect_response=False,
    )
    await self._drain_startup_silence(2.0)

    await self._send_safe(bytes.fromhex("aaff1a142d"), timeout=0.12, expect_response=False)
    await self._send_safe(
      bytes.fromhex("aa010e0f"),
      timeout=0.30,
      expected_len=None,
      expect_response=False,
    )
    await self._send_safe(
      bytes.fromhex("aa020e10"),
      timeout=0.30,
      expected_len=5,
      expect_response=False,
    )

    await self.io.set_baudrate(57600)
    await self.io.set_rts(True)
    await self.io.set_dtr(True)

  async def _drain_startup_silence(self, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
      await self._read_resp(timeout=0.08, expected_len=None, quiet_time=0.01)
      await asyncio.sleep(0.03)

  @staticmethod
  def _is_runtime_attach_status(status: _StatusPositionTachometer) -> bool:
    return int(status.current_position) != 0 or int(status.home_position) != 0

  async def _try_attach_to_runtime_controller(self) -> bool:
    """Attach to a VSpin controller that is already in 57600-baud runtime mode."""
    await self.io.set_baudrate(57600)
    await self.io.set_rts(True)
    await self.io.set_dtr(True)
    await self._purge_io_buffers()
    await self._drain_startup_silence(0.25)

    for _ in range(2):
      for status_command in (
        bytes.fromhex("aa010e0f"),
        bytes.fromhex("aa01121f32"),
      ):
        resp = await self._send_command(
          status_command,
          read_timeout=0.40,
          expected_len=14,
        )
        status = self._find_status_packet(resp)
        if status is not None:
          if not self._is_runtime_attach_status(status):
            logger.debug(
              "[vspin] Ignoring blank runtime attach status "
              "(status=0x%02x, position=%d, home=%d)",
              status.status,
              status.current_position,
              status.home_position,
            )
            continue
          self._last_position = int(status.current_position)
          self._last_home_position = int(status.home_position)
          logger.info(
            "[vspin] Attached to runtime controller "
            "(status=0x%02x, position=%d, home=%d)",
            status.status,
            status.current_position,
            status.home_position,
          )
          return True
      await asyncio.sleep(0.20)

    return False

  async def _enable_telemetry_and_pneumatics(self) -> None:
    await self._send_safe(bytes.fromhex("aa01121f32"), timeout=0.35, expected_len=14)

    for _ in range(8):
      await self._send_safe(bytes.fromhex("aa0220ff0f30"), timeout=0.12)

    for cmd in (
      bytes.fromhex("aa0220df0f10"),
      bytes.fromhex("aa0220df0e0f"),
      bytes.fromhex("aa0220df0c0d"),
      bytes.fromhex("aa0220df0809"),
    ):
      await self._send_safe(cmd, timeout=0.12)

    for _ in range(4):
      await self._send_safe(bytes.fromhex("aa0226000028"), timeout=0.12)

    await self._send_safe(bytes.fromhex("aa02120317"), timeout=0.12)

    for _ in range(5):
      await self._send_safe(bytes.fromhex("aa0226200048"), timeout=0.15)
      await self._send_safe(bytes.fromhex("aa020e10"), timeout=0.12, expected_len=5)
      await self._send_safe(bytes.fromhex("aa0226000028"), timeout=0.15)
      await self._send_safe(bytes.fromhex("aa020e10"), timeout=0.12, expected_len=5)

    await self._send_safe(bytes.fromhex("aa020e10"), timeout=0.12, expected_len=5)
    await self._send_safe(bytes.fromhex("aa0226000129"), timeout=0.15)
    await self._poll_io_status(0.35)
    await self._send_safe(bytes.fromhex("aa0226000028"), timeout=0.15)
    await self._poll_io_status(0.35)
    self._motion_is_prepared = True

  async def _poll_io_status(self, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
      await self._send_safe(bytes.fromhex("aa020e10"), timeout=0.10, expected_len=5)
      await asyncio.sleep(0.12)

  async def _motor_enable(self) -> None:
    await self._send_safe(bytes.fromhex("aa0117021a"), timeout=0.30, expected_len=14)
    await self._send_safe(
      bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"),
      timeout=0.30,
      expected_len=14,
    )
    await self._send_safe(bytes.fromhex("aa0117041c"), timeout=0.30, expected_len=14)
    await self._send_safe(bytes.fromhex("aa01170119"), timeout=0.30, expected_len=14)
    await self._send_safe(bytes.fromhex("aa010b0c"), timeout=0.30, expected_len=14)

  async def _home_rotor(self) -> None:
    pre_home_status = await self._get_positions_and_tachometer()
    homing_started_at = time.monotonic()
    await self._motor_enable()
    await self._send_safe(bytes.fromhex("aa010001"), timeout=0.30, expected_len=14)
    await self._send_safe(
      bytes.fromhex("aa01e605006400000000003200e80301006e"),
      timeout=0.30,
      expected_len=14,
    )
    await self._send_safe(
      bytes.fromhex("aa0194b61283000012010000f3"),
      timeout=0.30,
      expected_len=14,
    )
    await self._send_safe(bytes.fromhex("aa01192842"), timeout=0.30, expected_len=14)

    status = await self._wait_for_idle(
      label="homing",
      timeout=HOMING_TIMEOUT,
      min_wait=HOMING_MIN_WAIT_SECONDS,
      require_activity_from=pre_home_status,
      activity_tolerance=POSITION_SETTLE_TOLERANCE,
    )

    if not self._has_fresh_home_reference(
      status=status,
      previous_home_position=int(pre_home_status.home_position),
    ):
      remaining_timeout = max(0.0, HOMING_TIMEOUT - (time.monotonic() - homing_started_at))
      logger.debug(
        "[vspin] Waiting for fresh homing reference "
        "(previous home=%d, current home=%d)",
        pre_home_status.home_position,
        status.home_position,
      )
      status = await self._wait_for_homed_status(
        pre_home_status=pre_home_status,
        timeout=remaining_timeout,
      )

    self._last_position = int(status.current_position)
    self._last_home_position = int(status.home_position)
    self._home_sensor_position = _normalize_vspin_home_position(status.home_position)

  @staticmethod
  def _has_fresh_home_reference(
    status: _StatusPositionTachometer,
    previous_home_position: int,
  ) -> bool:
    if int(status.home_position) == 0:
      return False
    if int(previous_home_position) == 0:
      return True
    return int(status.home_position) != int(previous_home_position)

  async def _wait_for_homed_status(
    self,
    pre_home_status: _StatusPositionTachometer,
    timeout: float,
  ) -> _StatusPositionTachometer:
    end = time.monotonic() + timeout
    previous_home_position = int(pre_home_status.home_position)
    last_status: Optional[VSpinBackend._StatusPositionTachometer] = None

    while time.monotonic() < end:
      status = await self._get_full_positions_and_tachometer()
      last_status = status
      is_idle_status = status.status in _IDLE_VSPIN_STATUSES
      is_stopped = abs(status.tachometer) <= 2
      if (
        is_idle_status
        and is_stopped
        and self._has_fresh_home_reference(status, previous_home_position)
      ):
        return status
      await asyncio.sleep(STATUS_POLL_INTERVAL)

    if last_status is None:
      last_status = self._make_status(
        0x19,
        self._last_position,
        home_position=self._last_home_position,
      )
    raise TimeoutError(
      "VSpin homing did not report a fresh home position within "
      f"{timeout:.1f}s (previous home={previous_home_position}, "
      f"last status=0x{last_status.status:02x}, "
      f"position={last_status.current_position}, "
      f"tachometer={last_status.tachometer}, "
      f"home={last_status.home_position})"
    )

  async def _wait_for_full_status(
    self,
    timeout: float,
    allow_zero_home_fallback: bool = False,
  ) -> _StatusPositionTachometer:
    end = time.monotonic() + timeout
    last_raw = b""
    last_full_status: Optional[VSpinBackend._StatusPositionTachometer] = None
    while time.monotonic() < end:
      resp = await self._send_command(
        bytes.fromhex("aa010e0f"),
        read_timeout=0.40,
        expected_len=14,
      )
      last_raw = resp
      status = self._find_status_packet(resp)
      if status is not None and status.home_position != 0:
        self._last_position = int(status.current_position)
        self._last_home_position = int(status.home_position)
        return status
      if status is not None:
        last_full_status = status
      await asyncio.sleep(0.10)

    if allow_zero_home_fallback and last_full_status is not None:
      self._last_position = int(last_full_status.current_position)
      self._last_home_position = int(last_full_status.home_position)
      return last_full_status

    raise TimeoutError(
      "VSpin homing reached idle, but no full 14-byte status packet with a "
      f"home position was received. Last raw response: {last_raw.hex() or '(empty)'}"
    )

  async def _wait_for_idle(
    self,
    label: str,
    timeout: float,
    target_position: Optional[int] = None,
    tolerance: int = POSITION_TOLERANCE,
    min_wait: float = 0.0,
    require_activity_from: Optional[_StatusPositionTachometer] = None,
    activity_tolerance: int = POSITION_TOLERANCE,
  ) -> _StatusPositionTachometer:
    start = time.monotonic()
    last_status = self._make_status(
      0x19,
      self._last_position,
      home_position=self._last_home_position,
    )
    observed_activity = require_activity_from is None

    while time.monotonic() - start <= timeout:
      status = await self._get_positions_and_tachometer()
      last_status = status
      is_idle_status = status.status in _IDLE_VSPIN_STATUSES
      is_stopped = abs(status.tachometer) <= 2
      should_probe_full_status = is_idle_status and is_stopped and (
        (require_activity_from is not None and not observed_activity)
        or (
          target_position is not None
          and not _vspin_position_matches_target(
            position=int(status.current_position),
            target=int(target_position),
            tolerance=tolerance,
          )
        )
      )
      if should_probe_full_status:
        full_status = await self._get_full_positions_and_tachometer()
        if full_status.status in _IDLE_VSPIN_STATUSES and abs(full_status.tachometer) <= 2:
          status = full_status
          last_status = status
          is_idle_status = True
          is_stopped = True

      if require_activity_from is not None and not observed_activity:
        observed_activity = (
          not is_idle_status
          or not is_stopped
          or not _vspin_position_matches_target(
            position=int(status.current_position),
            target=int(require_activity_from.current_position),
            tolerance=activity_tolerance,
          )
          or (
            int(status.home_position) != 0
            and int(status.home_position) != int(require_activity_from.home_position)
          )
        )
      is_at_target = (
        target_position is None
        or _vspin_position_matches_target(
          position=int(status.current_position),
          target=int(target_position),
          tolerance=tolerance,
        )
      )

      if (
        is_idle_status
        and is_stopped
        and is_at_target
        and observed_activity
        and time.monotonic() - start >= min_wait
      ):
        return status

      await asyncio.sleep(STATUS_POLL_INTERVAL)
    raise TimeoutError(
      f"VSpin {label} did not become idle within {timeout:.1f}s "
      f"(status=0x{last_status.status:02x}, position={last_status.current_position}, "
      f"tachometer={last_status.tachometer}, home={last_status.home_position})"
    )

  async def _prepare_bucket_motion(self) -> None:
    if self._model == "agilent":
      await self._send_safe(self._command("lock_door"), timeout=0.20)
      self._motion_is_prepared = True
      return

    await self._send_safe(self._command("lock_bucket"), timeout=0.20)
    await self._wait_for_io_state(
      label="bucket motion lock",
      timeout=1.5,
      door_open=False,
      door_locked=True,
      bucket_locked=True,
    )
    await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
    await self._send_safe(bytes.fromhex("aa0226000028"), timeout=0.20)
    await self._wait_for_io_state(
      label="bucket motion ready",
      timeout=2.5,
      door_open=False,
      door_locked=True,
      bucket_locked=False,
      settled=True,
    )
    await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
    self._motion_is_prepared = True

  async def _prepare_spin_motion(self) -> None:
    if self._model == "agilent":
      await self._send_safe(self._command("lock_door"), timeout=0.20)
      self._motion_is_prepared = True
      return

    await self._send_safe(self._command("lock_bucket"), timeout=0.20)
    await self._wait_for_io_state(
      label="spin bucket lock",
      timeout=1.5,
      door_open=False,
      door_locked=True,
      bucket_locked=True,
    )
    await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
    await self._send_safe(bytes.fromhex("aa0226000028"), timeout=0.20)
    await self._wait_for_io_state(
      label="spin ready",
      timeout=2.5,
      door_open=False,
      door_locked=True,
      bucket_locked=False,
      settled=True,
    )
    await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
    self._motion_is_prepared = True

  async def _wait_for_io_state(
    self,
    label: str,
    timeout: float,
    door_open: Optional[bool] = None,
    door_locked: Optional[bool] = None,
    bucket_locked: Optional[bool] = None,
    settled: bool = False,
  ) -> None:
    end = time.monotonic() + timeout
    last_status = b""
    while time.monotonic() < end:
      try:
        last_status = await self._get_status()
      except IOError:
        await asyncio.sleep(0.05)
        continue

      if len(last_status) >= 3:
        value = last_status[2]
        matches = []
        if door_open is not None:
          matches.append((value & 0b0010 != 0) is door_open)
        if door_locked is not None:
          matches.append((value & 0b0100 == 0) is door_locked)
        if bucket_locked is not None:
          matches.append((value & 0b0001 != 0) is bucket_locked)
        if settled:
          matches.append(last_status[1] in (0x00, 0x10))
        if all(matches):
          return

      await asyncio.sleep(0.05)

    raise TimeoutError(
      f"VSpin {label} IO state was not reached within {timeout:.1f}s "
      f"(last status={last_status.hex() or '(empty)'})"
    )

  async def _send_deceleration(self, deceleration: float) -> None:
    await self._send_safe(
      bytes.fromhex("aa01e60500640000000000fd00803e01000c"),
      timeout=0.25,
    )
    await self._send_safe(_build_vspin_deceleration_command(deceleration), timeout=0.25)

  async def _wait_for_door(self, open_expected: bool, timeout: float) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      try:
        if await self.get_door_open() is open_expected:
          return
      except IOError:
        pass
      await asyncio.sleep(0.12)

    expected = "open" if open_expected else "closed"
    raise TimeoutError(f"VSpin door did not report {expected} within {timeout:.1f}s")

  async def _wait_for_speed_or_motion(
    self,
    rpm: int,
    final_position: int,
    timeout: float = 25.0,
  ) -> None:
    deadline = time.monotonic() + timeout
    last_status = self._make_status(
      0x19,
      self._last_position,
      home_position=self._last_home_position,
    )
    last_live_rpm = 0.0
    while time.monotonic() < deadline and not self._stop_requested:
      status = await self._get_positions_and_tachometer()
      last_status = status
      last_live_rpm = abs(status.tachometer * TACH_TO_RPM)
      if last_live_rpm >= rpm * 0.92:
        return
      await asyncio.sleep(0.25)

    raise TimeoutError(
      f"VSpin did not reach target speed {rpm} rpm within {timeout:.1f}s "
      f"(last rpm={last_live_rpm:.1f}, position={last_status.current_position}, "
      f"home={last_status.home_position})"
    )

  async def _hold_spin(self, duration: float) -> None:
    started = time.monotonic()
    while not self._stop_requested and time.monotonic() - started < duration:
      await self._get_positions_and_tachometer()
      await asyncio.sleep(min(1.0, duration))

  async def configure_and_initialize(self):
    await self.set_configuration_data()
    await self._settle_controller_connection()
    await self.initialize()

  async def set_configuration_data(self):
    """Set the device configuration data."""
    await self._set_serial_line_defaults()
    await self.io.set_baudrate(19200)

  async def _set_serial_line_defaults(self) -> None:
    await self.io.set_latency_timer(16)
    await self.io.set_line_property(bits=8, stopbits=1, parity=0)
    await self.io.set_flowctrl(0)
    await self.io.set_rts(True)
    await self.io.set_dtr(True)

  async def _settle_controller_connection(self) -> None:
    await asyncio.sleep(CONTROLLER_CONNECT_SETTLE_SECONDS)

  async def _purge_io_buffers(self) -> None:
    try:
      await self.io.usb_purge_rx_buffer()
      await self.io.usb_purge_tx_buffer()
    except Exception as e:
      logger.debug("[vspin] Ignoring buffer purge during close/setup: %s", e)

  async def _close_connection_cleanly(self) -> None:
    try:
      await asyncio.sleep(0.20)
      await self._purge_io_buffers()
      try:
        await self.io.set_dtr(False)
        await self.io.set_rts(False)
      except Exception as e:
        logger.debug("[vspin] Ignoring control-line reset during close: %s", e)
      await asyncio.sleep(0.20)
    finally:
      await self.io.stop()

  async def initialize(self):
    for _ in range(2):
      await self.io.write(b"\x00" * 20)
      await asyncio.sleep(INITIALIZE_PACKET_GAP_SECONDS)
      for i in range(33):
        packet = b"\xaa" + bytes([i & 0xFF, 0x0E, 0x0E + (i & 0xFF)]) + b"\x00" * 8
        await self.io.write(packet)
        await asyncio.sleep(INITIALIZE_PACKET_GAP_SECONDS)
      await self._send_command(bytes.fromhex("aaff0f0e"), read_timeout=0.08)
      await asyncio.sleep(COMMAND_GAP_SECONDS)

  # Centrifuge operations

  async def open_door(self):
    try:
      if await self.get_door_open():
        await asyncio.sleep(DOOR_OPEN_SETTLE_SECONDS)
        return
    except IOError:
      pass

    try:
      await self.unlock_door()
    except IOError:
      pass

    await self._send_safe(self._command("open_door"), timeout=0.30)
    await self._wait_for_door(open_expected=True, timeout=4.0)
    await asyncio.sleep(DOOR_OPEN_SETTLE_SECONDS)

  async def close_door(self):
    try:
      if not await self.get_door_open():
        return
    except IOError:
      pass

    await self._send_safe(self._command("close_door"), timeout=0.30)
    await self._wait_for_door(open_expected=False, timeout=4.0)
    await asyncio.sleep(DOOR_CLOSE_SETTLE_SECONDS)
    self._motion_is_prepared = False

  async def lock_door(self):
    if await self.get_door_open():
      raise RuntimeError("Cannot lock door while it is open.")
    if await self.get_door_locked():
      return
    await self._send_safe(self._command("lock_door"), timeout=0.20)
    if self._model == "agilent":
      await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
      return
    await self._wait_for_io_state(
      label="door lock",
      timeout=2.5,
      door_open=False,
      door_locked=True,
      bucket_locked=False,
      settled=True,
    )
    await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)

  async def unlock_door(self):
    if not await self.get_door_locked():
      return
    await self._send_safe(self._command("unlock_door"), timeout=0.20)
    await asyncio.sleep(DOOR_UNLOCK_TO_OPEN_SETTLE_SECONDS)

  async def lock_bucket(self):
    if await self.get_bucket_locked():
      return
    await self._send_safe(self._command("lock_bucket"), timeout=0.25)
    if self._model == "agilent":
      await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
      self._motion_is_prepared = False
      return
    await self._wait_for_io_state(
      label="bucket lock",
      timeout=1.5,
      bucket_locked=True,
    )
    await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
    self._motion_is_prepared = False

  async def unlock_bucket(self):
    if not await self.get_bucket_locked():
      return
    await self._send_safe(self._command("unlock_bucket"), timeout=0.25)
    if self._model == "agilent":
      await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
      self._motion_is_prepared = True
      return
    await self._wait_for_io_state(
      label="bucket unlock",
      timeout=1.5,
      bucket_locked=False,
    )
    await asyncio.sleep(PNEUMATIC_SETTLE_SECONDS)
    self._motion_is_prepared = True

  async def go_to_bucket1(self):
    await self.go_to_position(await self._get_bucket_position(1))

  async def go_to_bucket2(self):
    await self.go_to_position(await self._get_bucket_position(2))

  async def go_to_position(self, position: int):
    position = int(position)
    if await self.get_door_open():
      await self.close_door()
    if not await self.get_door_locked():
      await self.lock_door()
    if not self._motion_is_prepared:
      await self._prepare_bucket_motion()

    for attempt in range(1, POSITION_MOVE_ATTEMPTS + 1):
      if attempt > 1:
        logger.warning(
          "[vspin] Retrying position move to %d after wrong idle position",
          position,
        )
        self._motion_is_prepared = False
        await self._prepare_bucket_motion()

      await self._motor_enable()
      await self._send_safe(
        bytes.fromhex("aa01e6c800b00496000f004b00a00f050007"),
        timeout=0.20,
        expected_len=14,
      )
      await self._send_safe(
        _build_vspin_position_command(position),
        timeout=0.25,
        expected_len=14,
      )
      try:
        await self._wait_for_idle(
          label=f"position {position}",
          timeout=25.0,
          target_position=position,
          tolerance=POSITION_SETTLE_TOLERANCE,
        )
        break
      except TimeoutError:
        if attempt == POSITION_MOVE_ATTEMPTS:
          raise

    await self.lock_bucket()
    await self.open_door()

  @staticmethod
  def g_to_rpm(g: float) -> int:
    # https://en.wikipedia.org/wiki/Centrifugation#Mathematical_formula
    r = 10
    rpm = int((g / (1.118 * 10**-5 * r)) ** 0.5)
    return rpm

  @staticmethod
  def rpm_to_g(rpm: float) -> float:
    # https://en.wikipedia.org/wiki/Centrifugation#Mathematical_formula
    r = 10
    return 1.118 * 10**-5 * r * float(rpm) ** 2

  async def spin(
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
      acceleration: 0-1 of total acceleration
      deceleration: 0-1 of total deceleration
    """

    if acceleration <= 0 or acceleration > 1:
      raise ValueError("Acceleration must be within 0-1.")
    if deceleration <= 0 or deceleration > 1:
      raise ValueError("Deceleration must be within 0-1.")
    if g < 1 or g > 1000:
      raise ValueError("G-force must be within 1-1000")
    if duration < 1:
      raise ValueError("Spin time must be at least 1 second")

    await self.spin_rpm(
      rpm=VSpinBackend.g_to_rpm(g),
      duration=duration,
      acceleration=acceleration,
      deceleration=deceleration,
    )

  async def spin_rpm(
    self,
    rpm: int,
    duration: float,
    acceleration: float = 0.8,
    deceleration: float = 0.8,
  ) -> None:
    """Start a spin cycle at a target RPM.

    This is a convenience wrapper around the same command path used by
    :meth:`spin`. The public PLR centrifuge API remains g-based.
    """
    rpm = int(rpm)
    duration = float(duration)

    if rpm < 1 or rpm > 3000:
      raise ValueError("RPM must be within 1-3000.")
    if acceleration <= 0 or acceleration > 1:
      raise ValueError("Acceleration must be within 0-1.")
    if deceleration <= 0 or deceleration > 1:
      raise ValueError("Deceleration must be within 0-1.")
    if duration < 1:
      raise ValueError("Spin time must be at least 1 second")

    if await self.get_door_open():
      await self.close_door()

    self._stop_requested = False

    try:
      for attempt in range(1, SPIN_START_ATTEMPTS + 1):
        try:
          await self._prepare_spin_motion()
          await self._motor_enable()
          # Sample before entering the spin profile; D4 97 must follow it immediately.
          current_position = await self.get_position()
          await self._send_safe(
            bytes.fromhex("aa01e60500640000000000fd00803e01000c"),
            timeout=0.25,
            expected_len=14,
          )

          spin_command, final_position = _build_vspin_spin_command(
            current_position=current_position,
            rpm=rpm,
            duration=duration,
            acceleration=acceleration,
          )
          await self._send_safe(spin_command, timeout=0.25, expected_len=14)

          await self._wait_for_speed_or_motion(rpm=rpm, final_position=final_position)
          await self._hold_spin(duration)
          await self._send_deceleration(deceleration)
          break
        except TimeoutError:
          if attempt == SPIN_START_ATTEMPTS:
            raise
          logger.warning("[vspin] Retrying spin start after target speed was not reached")
          await self._send_deceleration(deceleration)
          await self._wait_for_idle(label="spin retry rundown", timeout=45.0)
          self._motion_is_prepared = False
    except asyncio.CancelledError:
      await self._send_deceleration(deceleration)
      raise
    except Exception:
      logger.exception("[vspin] Spin failed; attempting deceleration")
      try:
        await self._send_deceleration(deceleration)
      except Exception:
        logger.exception("[vspin] Deceleration after failed spin also failed")
      raise
    finally:
      self._stop_requested = False

    await self._wait_for_idle(label="spin rundown", timeout=90.0)
    await self._home_rotor()


def create_vspin_backend(
  device_id: Optional[str] = None,
  variant: str = "agilent",
  try_runtime_attach_after_startup_failure: bool = False,
) -> CentrifugeBackend:
  """Create a VSpin backend for the selected centrifuge generation.

  ``variant`` accepts ``"agilent"``, ``"velocity11"``, or ``"v11"``. All
  variants use :class:`VSpinBackend`; only firmware-specific command bytes are
  selected differently.
  """
  return VSpinBackend(
    device_id=device_id,
    model=variant,
    try_runtime_attach_after_startup_failure=try_runtime_attach_after_startup_failure,
  )


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class VSpin:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`VSpin` is deprecated. Please use `VSpinBackend` instead. ")
