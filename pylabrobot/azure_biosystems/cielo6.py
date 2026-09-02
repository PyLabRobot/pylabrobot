import asyncio
import enum
import hashlib
import json
import logging
import struct
import time
import zlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, List, Optional

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

VERSION_CHECK_COMMAND = 0x0B01
STATUS_QUERY_COMMAND = 0x0B02
PAUSE_COMMAND = 0x0B04
RESUME_COMMAND = 0x0B05
PROGRAM_GET_COMMAND = 0x0B09
WORKSPACE_SUMMARY_GET_COMMAND = 0x0B0A
WORKSPACE_CREATE_COMMAND = 0x0B0B
WORKSPACE_DELETE_COMMAND = 0x0B0C
PROGRAM_DELETE_COMMAND = 0x0B0D
EXPERIMENT_DATA_SUMMARY_GET_COMMAND = 0x0B10
EXPERIMENT_DATA_FILE_INFO_GET_COMMAND = 0x0B11
EXPERIMENT_DATA_FILE_GET_COMMAND = 0x0B12
RUNNING_EXPERIMENT_INFOS_GET_COMMAND = 0x0B16
RUNNING_EXPERIMENT_DATA_UPLOAD_COMMAND = 0x0B15
SESSION_LOCK_COMMAND = 0x0B17
INITIALIZE_COMMAND = 0x0B00
PROGRAM_UPLOAD_COMMAND = 0x0B08
RESULT_PATH_SET_COMMAND = 0x0B14
RUN_COMMAND = 0x0B03
STOP_COMMAND = 0x0B06
DISCONNECT_COMMAND = 0x0B19
EXEC_SUCCESSFUL = 0x5A01
DISCOVERY_DEVICE_ID = "99999999"
FRAME_HEADER_SIZE = 13
FRAME_OVERHEAD = 17
FRAME_TAIL = b"\x55\xaa"
MAX_PAYLOAD_SIZE = 255
IDENTITY_RESPONSE_SIZE = 57
WORK_STATUS_IDLE = 256
WORK_STATUS_RUNNING = 512
WORK_STATUS_PAUSED = 768
RUNNING_DATA_TYPE_NORMAL = 1
RUNNING_DATA_TYPE_MELTING = 2
RUNNING_DATA_VALUE_COUNT = 16
EXPERIMENT_DATA_SIZE = 2308
EXPERIMENT_CHANNEL_COUNT = 6
EXPERIMENT_WELL_COUNT = 96
MELTING_DATA_SIZE = 388
_COMPLETION_SETTLE_TIMEOUT = 5.0
_COMPLETION_SETTLE_POLL_INTERVAL = 0.25

_STATUS_STRUCT = struct.Struct("<16s2I2IHHHhHHIIIhhHH16h16h")
_PROGRAM_HEADER_STRUCT = struct.Struct("<4s6s6s9H6BHHf6HI")
_PROGRAM_STEP_STRUCT = struct.Struct("<HHIHHhhHHHHHH2H16h")
PROGRAM_SIZE = 2048
PROGRAM_STEP_COUNT = 30
PROGRAM_CHUNK_COUNT = 16
PROGRAM_CHUNK_SIZE = 128


class Cielo6Error(Exception):
  """Report an invalid or unsuccessful Cielo 6 protocol operation."""


class Cielo6RunTimeoutError(Cielo6Error):
  """Report that PLR stopped waiting while the Cielo run can still be active."""

  def __init__(self, latest_status: "Cielo6Status") -> None:
    """Store the last authoritative status that PLR received."""
    self.latest_status = latest_status
    super().__init__(
      "Timed out while waiting for the Cielo run. The run may still be active "
      f"(work_status={latest_status.work_status})."
    )


class Cielo6FirmwareStateError(Cielo6Error):
  """Report an error state returned by the Cielo firmware."""

  def __init__(self, operation: str, status: "Cielo6Status") -> None:
    """Store the operation and authoritative status readback."""
    self.operation = operation
    self.status = status
    super().__init__(
      f"Cielo firmware reported {status.work_state.name} while {operation} "
      f"(work_status={status.work_status})."
    )


class Cielo6WorkState(enum.IntEnum):
  """Firmware-reported Cielo operating state."""

  UNKNOWN = -1
  NONE = 0
  IDLE = WORK_STATUS_IDLE
  RUNNING = WORK_STATUS_RUNNING
  PAUSED = WORK_STATUS_PAUSED
  ERROR_1 = 2561
  ERROR_2 = 2562
  ERROR_3 = 2563
  ERROR_4 = 2564
  PAUSE_ERROR = 2817
  RESUME_ERROR = 2818
  RUN_ERROR = 2819
  STOP_ERROR = 2820
  DOWNLOAD_ERROR = 2821

  @classmethod
  def from_firmware(cls, value: int) -> "Cielo6WorkState":
    """Map a firmware value. Return ``UNKNOWN`` for an unsupported value."""
    try:
      return cls(value)
    except ValueError:
      return cls.UNKNOWN

  @property
  def is_error(self) -> bool:
    """Return true for a firmware-defined error state."""
    return self in {
      self.ERROR_1,
      self.ERROR_2,
      self.ERROR_3,
      self.ERROR_4,
      self.PAUSE_ERROR,
      self.RESUME_ERROR,
      self.RUN_ERROR,
      self.STOP_ERROR,
      self.DOWNLOAD_ERROR,
    }


class _Cielo6RunPhase(enum.Enum):
  """Local progress through the run-dispatch boundary."""

  NONE = enum.auto()
  PREPARING = enum.auto()
  DISPATCHED = enum.auto()


@dataclass(frozen=True)
class Cielo6ExperimentInfo:
  """Identity and timestamps for one experiment stored by the Cielo firmware."""

  workspace: str
  protocol: str
  name: str
  started_at_raw: str = ""
  ended_at_raw: str = ""


def _encode_device_id(device_id: str) -> bytes:
  """Encode and validate an eight-character device identifier."""
  try:
    encoded = device_id.encode("ascii")
  except UnicodeEncodeError as error:
    raise ValueError("device_id must contain exactly 8 ASCII characters") from error
  if len(encoded) != 8:
    raise ValueError("device_id must contain exactly 8 ASCII characters")
  return encoded


def _crc16(data: bytes) -> int:
  """Return the 16-bit value from ``Hats.Tools.CRC.CRC16``.

  The firmware API serializes this value in little-endian order. The wire bytes
  use the standard Modbus CRC byte order.
  """
  crc = 0xFFFF
  for byte in data:
    crc ^= byte
    for _ in range(8):
      crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
  return ((crc & 0xFF) << 8) | (crc >> 8)


@dataclass(frozen=True)
class Cielo6Identity:
  """Identity returned by the Cielo firmware's broadcast version query."""

  device_id: str
  name: str
  transport: str

  @classmethod
  def from_bytes(cls, data: bytes) -> "Cielo6Identity":
    """Decode a firmware identity response."""
    if len(data) != IDENTITY_RESPONSE_SIZE:
      raise Cielo6Error(
        f"Invalid Cielo identity response: expected {IDENTITY_RESPONSE_SIZE} bytes, got {len(data)}"
      )
    if data[:20] != b"Azure QPCR SeriesID:" or data[28:33] != b"Name:":
      raise Cielo6Error("Invalid Cielo identity response header")
    if data[53:] != b"&USB":
      raise Cielo6Error("Invalid Cielo identity response transport")

    try:
      device_id = data[20:28].decode("ascii")
      name = data[33:53].decode("ascii").rstrip()
    except UnicodeDecodeError as error:
      raise Cielo6Error("Cielo identity response is not ASCII") from error
    _encode_device_id(device_id)
    return cls(device_id=device_id, name=name, transport="USB")


@dataclass(frozen=True)
class CieloFrame:
  """One Cielo firmware API frame."""

  device_id: str
  command: int
  payload: bytes = b""

  def __post_init__(self) -> None:
    """Validate the frame fields before serialization."""
    _encode_device_id(self.device_id)
    if not 0 <= self.command <= 0x7FFFFFFF:
      raise ValueError("command must fit in a non-negative signed 32-bit integer")
    if len(self.payload) > MAX_PAYLOAD_SIZE:
      raise ValueError("payload cannot exceed 255 bytes")

  def to_bytes(self) -> bytes:
    """Serialize this frame with its CRC and tail."""
    body = (
      _encode_device_id(self.device_id)
      + self.command.to_bytes(4, byteorder="little", signed=True)
      + bytes([len(self.payload)])
      + self.payload
    )
    return body + _crc16(body).to_bytes(2, byteorder="little") + FRAME_TAIL

  @classmethod
  def from_bytes(cls, data: bytes) -> "CieloFrame":
    """Decode and validate one complete firmware frame."""
    if len(data) < FRAME_OVERHEAD:
      raise Cielo6Error(f"Incomplete Cielo frame: expected at least 17 bytes, got {len(data)}")

    payload_size = data[12]
    expected_size = FRAME_OVERHEAD + payload_size
    if len(data) != expected_size:
      raise Cielo6Error(
        f"Invalid Cielo frame length: header declares {expected_size} bytes, got {len(data)}"
      )
    if data[-2:] != FRAME_TAIL:
      raise Cielo6Error("Invalid Cielo frame tail")

    body = data[:-4]
    received_crc = int.from_bytes(data[-4:-2], byteorder="little")
    expected_crc = _crc16(body)
    if received_crc != expected_crc:
      raise Cielo6Error(
        f"Invalid Cielo frame CRC: expected 0x{expected_crc:04x}, got 0x{received_crc:04x}"
      )

    try:
      device_id = data[:8].decode("ascii")
    except UnicodeDecodeError as error:
      raise Cielo6Error("Cielo frame device ID is not ASCII") from error
    command = int.from_bytes(data[8:12], byteorder="little", signed=True)
    return cls(device_id=device_id, command=command, payload=data[13:-4])


@dataclass(frozen=True)
class Cielo6Status:
  """Contain a decoded payload from firmware command ``0x0B02``.

  The temperature properties apply the firmware scale of 0.01 degrees Celsius.
  The corresponding raw fields preserve each signed firmware integer.
  """

  file_name: str
  run_id: tuple[int, int]
  sample_id: tuple[int, int]
  control_mode: int
  work_status: int
  hot_lid_mode: int
  hot_lid_temperature_raw: int
  current_step: int
  current_cycle: int
  current_time_remaining: int
  program_time_total: int
  program_time_remaining: int
  environment_temperature_raw: int
  radiator_temperature_raw: int
  volume: int
  is_finished: int
  block_temperatures_raw: tuple[int, ...]
  sample_temperatures: tuple[float, ...]

  @property
  def work_state(self) -> Cielo6WorkState:
    """Return the typed firmware work state."""
    return Cielo6WorkState.from_firmware(self.work_status)

  @property
  def is_running(self) -> bool:
    """Return true if the firmware reports an active run."""
    return self.work_state is Cielo6WorkState.RUNNING

  @property
  def is_paused(self) -> bool:
    """Return true if the firmware reports a paused run."""
    return self.work_state is Cielo6WorkState.PAUSED

  @property
  def finished(self) -> bool:
    """Return true if the firmware reports run completion."""
    return self.is_finished == 1

  @property
  def hot_lid_heating(self) -> Optional[bool]:
    """Return heater activity. This value is not the mechanical lid state."""
    if self.hot_lid_mode == 0:
      return False
    if self.hot_lid_mode == 1:
      return True
    return None

  @property
  def hot_lid_temperature(self) -> float:
    """Return the hot-lid temperature in degrees Celsius."""
    return self.hot_lid_temperature_raw / 100

  @property
  def environment_temperature(self) -> float:
    """Return the internal environment temperature in degrees Celsius."""
    return self.environment_temperature_raw / 100

  @property
  def radiator_temperature(self) -> float:
    """Return the radiator temperature in degrees Celsius."""
    return self.radiator_temperature_raw / 100

  @property
  def block_temperatures(self) -> tuple[float, ...]:
    """Return the block temperatures in degrees Celsius."""
    return tuple(value / 100 for value in self.block_temperatures_raw)

  @property
  def progress(self) -> Optional[float]:
    """Return firmware-timed progress in the inclusive range from 0 to 1."""
    if self.program_time_total <= 0:
      return None
    elapsed = self.program_time_total - self.program_time_remaining
    return min(1.0, max(0.0, elapsed / self.program_time_total))

  @classmethod
  def from_payload(cls, payload: bytes) -> "Cielo6Status":
    """Decode one status-command payload."""
    if len(payload) != _STATUS_STRUCT.size:
      raise Cielo6Error(
        f"Invalid Cielo status payload: expected {_STATUS_STRUCT.size} bytes, got {len(payload)}"
      )

    values = _STATUS_STRUCT.unpack(payload)
    try:
      file_name = values[0].split(b"\0", 1)[0].decode("ascii")
    except UnicodeDecodeError as error:
      raise Cielo6Error("Cielo status file name is not ASCII") from error

    return cls(
      file_name=file_name,
      run_id=(values[1], values[2]),
      sample_id=(values[3], values[4]),
      control_mode=values[5],
      work_status=values[6],
      hot_lid_mode=values[7],
      hot_lid_temperature_raw=values[8],
      current_step=values[9],
      current_cycle=values[10],
      current_time_remaining=values[11],
      program_time_total=values[12],
      program_time_remaining=values[13],
      environment_temperature_raw=values[14],
      radiator_temperature_raw=values[15],
      volume=values[16],
      is_finished=values[17],
      block_temperatures_raw=tuple(values[18:34]),
      sample_temperatures=tuple(value / 100 for value in values[34:50]),
    )


@dataclass(frozen=True)
class Cielo6RunningData:
  """Contain one fluorescence upload from firmware command ``0x0B15``.

  The firmware sends one frame for each measurement group. The frame contains
  an index, step, position, channel, cycle, and 16 float values. The structure
  starts at payload byte 5.
  """

  index: int
  step_number: int
  position: int
  channel: int
  cycle: int
  values: tuple[float, ...]

  @classmethod
  def from_payload(cls, payload: bytes) -> "Cielo6RunningData":
    """Decode one live amplification payload."""
    if len(payload) != 5 + 8 + RUNNING_DATA_VALUE_COUNT * 4:
      raise Cielo6Error(
        f"Invalid Cielo running-data payload: expected "
        f"{5 + 8 + RUNNING_DATA_VALUE_COUNT * 4} bytes, got {len(payload)}"
      )
    if payload[4] != RUNNING_DATA_TYPE_NORMAL:
      raise Cielo6Error(
        f"Invalid Cielo running-data type: expected {RUNNING_DATA_TYPE_NORMAL}, got {payload[4]}"
      )
    index = int.from_bytes(payload[:4], byteorder="little", signed=True)
    step_number, position, channel, cycle = struct.unpack_from("<HHHH", payload, 5)
    values = struct.unpack_from(f"<{RUNNING_DATA_VALUE_COUNT}f", payload, 13)
    return cls(
      index=index,
      step_number=step_number,
      position=position,
      channel=channel,
      cycle=cycle,
      values=values,
    )


@dataclass(frozen=True)
class Cielo6MeltingData:
  """One decoded melting-curve upload from firmware command ``0x0B15``."""

  index: int
  position: int
  temperature: int
  cycle: int
  values: tuple[float, ...]

  @classmethod
  def from_payload(cls, payload: bytes) -> "Cielo6MeltingData":
    """Decode one live melting-curve payload."""
    if len(payload) != 5 + 8 + RUNNING_DATA_VALUE_COUNT * 4:
      raise Cielo6Error(
        f"Invalid Cielo melting-data payload: expected "
        f"{5 + 8 + RUNNING_DATA_VALUE_COUNT * 4} bytes, got {len(payload)}"
      )
    if payload[4] != RUNNING_DATA_TYPE_MELTING:
      raise Cielo6Error(
        f"Invalid Cielo melting-data type: expected {RUNNING_DATA_TYPE_MELTING}, got {payload[4]}"
      )
    index = int.from_bytes(payload[:4], byteorder="little", signed=True)
    position = int.from_bytes(payload[5:7], byteorder="little")
    temperature = int.from_bytes(payload[7:11], byteorder="little", signed=True)
    cycle = int.from_bytes(payload[11:13], byteorder="little")
    values = struct.unpack_from(f"<{RUNNING_DATA_VALUE_COUNT}f", payload, 13)
    return cls(
      index=index,
      position=position,
      temperature=temperature,
      cycle=cycle,
      values=values,
    )


@dataclass(frozen=True)
class Cielo6MeltRecord:
  """One temperature point of a melting-curve read from a result file.

  ``values[position]`` holds the fluorescence for the well at that position in
  the same column-major order used by :class:`Cielo6CollectionPoint`.
  ``channel_index`` is the zero-based optical channel index.
  """

  temperature_raw: int
  values: tuple[float, ...]
  channel_index: int

  def __post_init__(self) -> None:
    """Validate the channel index and the number of well values."""
    if not 0 <= self.channel_index < EXPERIMENT_CHANNEL_COUNT:
      raise ValueError(
        f"Cielo melt record channel_index must be between 0 and "
        f"{EXPERIMENT_CHANNEL_COUNT - 1}, got {self.channel_index}"
      )
    if len(self.values) != EXPERIMENT_WELL_COUNT:
      raise ValueError(
        f"Cielo melt record must contain {EXPERIMENT_WELL_COUNT} values, got {len(self.values)}"
      )

  @property
  def temperature(self) -> float:
    """Return the melting temperature in degrees Celsius."""
    return self.temperature_raw / 100


@dataclass(frozen=True)
class Cielo6StoredProgramStep:
  """One decoded 64-byte firmware program step."""

  name: int
  function: int
  hold_time: int
  forever: int
  ramp_rate: int
  delta_temperature: int
  delta_time: int
  to_step: int
  goto_times: int
  pause_before: int
  pause_after: int
  loop_nesting_times: int
  collection_mode: int
  nc: tuple[int, int]
  temperatures_raw: tuple[int, ...]

  def __post_init__(self) -> None:
    """Validate all firmware field widths."""
    self.to_bytes()

  def to_bytes(self) -> bytes:
    """Serialize one 64-byte firmware program step."""
    if len(self.nc) != 2:
      raise ValueError("Cielo program step nc must contain exactly 2 values")
    if len(self.temperatures_raw) != 16:
      raise ValueError("Cielo program step temperatures_raw must contain exactly 16 values")
    try:
      return _PROGRAM_STEP_STRUCT.pack(
        self.name,
        self.function,
        self.hold_time,
        self.forever,
        self.ramp_rate,
        self.delta_temperature,
        self.delta_time,
        self.to_step,
        self.goto_times,
        self.pause_before,
        self.pause_after,
        self.loop_nesting_times,
        self.collection_mode,
        *self.nc,
        *self.temperatures_raw,
      )
    except struct.error as error:
      raise ValueError(
        f"Cielo program step value is outside its firmware field width: {error}"
      ) from error

  @classmethod
  def from_bytes(cls, data: bytes) -> "Cielo6StoredProgramStep":
    """Decode one 64-byte firmware program step."""
    if len(data) != _PROGRAM_STEP_STRUCT.size:
      raise Cielo6Error(
        f"Invalid Cielo program step: expected {_PROGRAM_STEP_STRUCT.size} bytes, got {len(data)}"
      )
    values = _PROGRAM_STEP_STRUCT.unpack(data)
    return cls(
      name=values[0],
      function=values[1],
      hold_time=values[2],
      forever=values[3],
      ramp_rate=values[4],
      delta_temperature=values[5],
      delta_time=values[6],
      to_step=values[7],
      goto_times=values[8],
      pause_before=values[9],
      pause_after=values[10],
      loop_nesting_times=values[11],
      collection_mode=values[12],
      nc=(values[13], values[14]),
      temperatures_raw=tuple(values[15:31]),
    )


def _decode_fixed_ascii(data: bytes, field_name: str) -> str:
  """Decode one fixed-width ASCII program field."""
  try:
    return data.split(b"\0", 1)[0].decode("ascii")
  except UnicodeDecodeError as error:
    raise Cielo6Error(f"Cielo program {field_name} is not ASCII") from error


def _encode_fixed_ascii(value: str, size: int, field_name: str) -> bytes:
  """Encode one fixed-width ASCII program field."""
  try:
    encoded = value.encode("ascii")
  except UnicodeEncodeError as error:
    raise ValueError(f"Cielo program {field_name} must contain only ASCII characters") from error
  if len(encoded) > size:
    raise ValueError(f"Cielo program {field_name} cannot exceed {size} ASCII characters")
  return encoded.ljust(size, b"\0")


def _encode_storage_name(value: str, field_name: str) -> bytes:
  """Encode and validate one firmware storage name."""
  try:
    encoded = value.encode("ascii")
  except UnicodeEncodeError as error:
    raise ValueError(f"{field_name} must contain only ASCII characters") from error
  if not value or "^" in value:
    raise ValueError(f"{field_name} must be non-empty and cannot contain '^'")
  if len(encoded) > 30:
    raise ValueError(f"{field_name} cannot exceed 30 ASCII characters")
  return encoded


@dataclass(frozen=True)
class Cielo6StoredProgram:
  """Decoded 2,048-byte program returned by firmware command ``0x0B09``."""

  identifier: bytes
  channels: tuple[int, ...]
  positions: tuple[int, ...]
  hot_lid_mode: int
  hot_lid_temperature_raw: int
  hot_lid_close_temperature_raw: int
  experiment_mode: int
  sample_volume: int
  test_zone: int
  heater_line: int
  saved_year: int
  saved_month: int
  saved_day: int
  saved_hour: int
  saved_minute: int
  activation_step_number: int
  melting_curve_mode: int
  melting_curve_start_temperature_raw: int
  melting_curve_end_temperature_raw: int
  melting_curve_step_resolution: float
  exposure_times: tuple[int, ...]
  reserved: int
  steps: tuple[Cielo6StoredProgramStep, ...]
  name: str
  workspace: str
  _unused_step_bytes: bytes = field(default=b"", repr=False)

  def __post_init__(self) -> None:
    """Validate the complete stored-program representation."""
    self.to_bytes()

  @property
  def step_count(self) -> int:
    """Return the number of active program steps."""
    return len(self.steps)

  @property
  def thermal_step_count(self) -> int:
    """Return executable temperature steps, excluding firmware loop markers."""
    return sum(step.function != 4 for step in self.steps)

  @property
  def cycle_count(self) -> Optional[int]:
    """Return the cycle count when the program has at most one repeat group."""
    repeat_steps = tuple(step for step in self.steps if step.function == 4)
    if not repeat_steps:
      return 1
    if len(repeat_steps) == 1:
      return repeat_steps[0].goto_times + 1
    return None

  def step_target_temperatures(self, step_index: int) -> tuple[float, ...]:
    """Return target temperatures for a zero-based firmware program step."""
    if not 0 <= step_index < len(self.steps):
      raise IndexError(f"Cielo program step index {step_index} is out of range")
    return tuple(value / 100 for value in self.steps[step_index].temperatures_raw)

  @property
  def crc32(self) -> int:
    """Return the CRC32 of the program's current serialized content."""
    return int.from_bytes(self.to_bytes()[-4:], byteorder="little")

  def to_bytes(self) -> bytes:
    """Serialize the program and calculate the firmware CRC32."""
    if len(self.identifier) != 4:
      raise ValueError("Cielo program identifier must contain exactly 4 bytes")
    if len(self.channels) != 6 or len(self.positions) != 6 or len(self.exposure_times) != 6:
      raise ValueError(
        "Cielo program channels, positions, and exposure_times must each have 6 values"
      )
    if self.step_count > PROGRAM_STEP_COUNT:
      raise ValueError(f"Cielo programs cannot exceed {PROGRAM_STEP_COUNT} steps")

    try:
      header = _PROGRAM_HEADER_STRUCT.pack(
        self.identifier,
        bytes(self.channels),
        bytes(self.positions),
        self.step_count,
        self.hot_lid_mode,
        self.hot_lid_temperature_raw,
        self.hot_lid_close_temperature_raw,
        self.experiment_mode,
        self.sample_volume,
        self.test_zone,
        self.heater_line,
        self.saved_year,
        self.saved_month,
        self.saved_day,
        self.saved_hour,
        self.saved_minute,
        self.activation_step_number,
        self.melting_curve_mode,
        self.melting_curve_start_temperature_raw,
        self.melting_curve_end_temperature_raw,
        self.melting_curve_step_resolution,
        *self.exposure_times,
        self.reserved,
      )
    except (struct.error, ValueError) as error:
      raise ValueError(
        f"Cielo program value is outside its firmware field width: {error}"
      ) from error

    unused_step_size = _PROGRAM_STEP_STRUCT.size * (PROGRAM_STEP_COUNT - self.step_count)
    unused_step_bytes = self._unused_step_bytes or bytes(unused_step_size)
    if len(unused_step_bytes) != unused_step_size:
      raise ValueError(
        f"Cielo program inactive step data must contain exactly {unused_step_size} bytes"
      )
    body = (
      header
      + b"".join(step.to_bytes() for step in self.steps)
      + unused_step_bytes
      + _encode_fixed_ascii(self.name, 30, "name")
      + _encode_fixed_ascii(self.workspace, 30, "workspace")
    )
    assert len(body) == PROGRAM_SIZE - 4
    return body + zlib.crc32(body).to_bytes(4, byteorder="little")

  @classmethod
  def from_bytes(cls, data: bytes) -> "Cielo6StoredProgram":
    """Decode and verify one complete stored program."""
    if len(data) != PROGRAM_SIZE:
      raise Cielo6Error(f"Invalid Cielo program: expected {PROGRAM_SIZE} bytes, got {len(data)}")
    expected_crc = zlib.crc32(data[:-4])
    received_crc = int.from_bytes(data[-4:], byteorder="little")
    if received_crc != expected_crc:
      raise Cielo6Error(
        f"Invalid Cielo program CRC32: expected 0x{expected_crc:08x}, got 0x{received_crc:08x}"
      )

    header = _PROGRAM_HEADER_STRUCT.unpack_from(data)
    step_count = header[3]
    if step_count > PROGRAM_STEP_COUNT:
      raise Cielo6Error(
        f"Invalid Cielo program step count: expected at most {PROGRAM_STEP_COUNT}, got {step_count}"
      )
    step_offset = _PROGRAM_HEADER_STRUCT.size
    all_steps = tuple(
      Cielo6StoredProgramStep.from_bytes(
        data[
          step_offset + index * _PROGRAM_STEP_STRUCT.size : step_offset
          + (index + 1) * _PROGRAM_STEP_STRUCT.size
        ]
      )
      for index in range(PROGRAM_STEP_COUNT)
    )
    name_offset = step_offset + PROGRAM_STEP_COUNT * _PROGRAM_STEP_STRUCT.size
    workspace_offset = name_offset + 30

    return cls(
      identifier=header[0],
      channels=tuple(header[1]),
      positions=tuple(header[2]),
      hot_lid_mode=header[4],
      hot_lid_temperature_raw=header[5],
      hot_lid_close_temperature_raw=header[6],
      experiment_mode=header[7],
      sample_volume=header[8],
      test_zone=header[9],
      heater_line=header[10],
      saved_year=header[11],
      saved_month=header[12],
      saved_day=header[13],
      saved_hour=header[14],
      saved_minute=header[15],
      activation_step_number=header[16],
      melting_curve_mode=header[17],
      melting_curve_start_temperature_raw=header[18],
      melting_curve_end_temperature_raw=header[19],
      melting_curve_step_resolution=header[20],
      exposure_times=tuple(header[21:27]),
      reserved=header[27],
      steps=all_steps[:step_count],
      name=_decode_fixed_ascii(data[name_offset:workspace_offset], "name"),
      workspace=_decode_fixed_ascii(data[workspace_offset : workspace_offset + 30], "workspace"),
      _unused_step_bytes=data[step_offset + step_count * _PROGRAM_STEP_STRUCT.size : name_offset],
    )


@dataclass(frozen=True)
class Cielo6RunState:
  """Contain one run-progress snapshot from the Cielo firmware.

  The status and experiment identity are firmware readback. PLR uses zero-based
  step and cycle indices. Program data supplies totals and target temperatures.
  """

  status: Cielo6Status
  experiment: Optional[Cielo6ExperimentInfo]
  observed_at: datetime
  current_step_index: Optional[int]
  current_cycle_index: Optional[int]
  total_step_count: Optional[int]
  total_cycle_count: Optional[int]
  target_temperatures: Optional[tuple[float, ...]]
  amplification_data: tuple[Cielo6RunningData, ...]
  melting_data: tuple[Cielo6MeltingData, ...]

  @property
  def progress(self) -> Optional[float]:
    """Return the normalized progress reported by the firmware."""
    return self.status.progress

  @property
  def estimated_completion_at(self) -> Optional[datetime]:
    """Return the estimated completion time when the run is active."""
    if (
      not (self.status.is_running or self.status.is_paused)
      or self.status.program_time_remaining <= 0
      or self.status.finished
    ):
      return None
    return self.observed_at + timedelta(seconds=self.status.program_time_remaining)


@dataclass(frozen=True)
class Cielo6ThermalStep:
  """One constant-temperature step in a user-facing Cielo thermal protocol."""

  temperature: float
  hold_time: int
  collect_fluorescence: bool = False

  def __post_init__(self) -> None:
    """Validate the temperature and hold time."""
    if not 4 <= self.temperature <= 100:
      raise ValueError("Cielo step temperature must be between 4 and 100 degrees Celsius")
    if self.hold_time < 1:
      raise ValueError("Cielo step hold_time must be at least 1 second")


@dataclass(frozen=True)
class Cielo6ThermalProtocol:
  """Define a linear Cielo protocol with an optional repeated step group.

  ``repeat_from_step`` is a zero-based index in ``steps``. ``cycles`` includes
  the first execution of the group. Compilation uses a program from the
  instrument as a template. PLR does not create undocumented field values.
  """

  steps: tuple[Cielo6ThermalStep, ...]
  repeat_from_step: Optional[int] = None
  cycles: int = 1
  sample_volume: int = 20

  def __post_init__(self) -> None:
    """Validate the user-facing thermal protocol."""
    if not self.steps:
      raise ValueError("Cielo thermal protocol must contain at least one step")
    if self.cycles < 1:
      raise ValueError("Cielo thermal protocol cycles must be at least 1")
    if not 1 <= self.sample_volume <= 100:
      raise ValueError("Cielo sample_volume must be between 1 and 100 microliters")
    if self.repeat_from_step is None:
      if self.cycles != 1:
        raise ValueError("repeat_from_step is required when cycles is greater than 1")
    elif not 0 <= self.repeat_from_step < len(self.steps):
      raise ValueError("repeat_from_step must identify an existing thermal step")
    compiled_step_count = len(self.steps) + (self.cycles > 1)
    if compiled_step_count > PROGRAM_STEP_COUNT:
      raise ValueError(f"Compiled Cielo protocol cannot exceed {PROGRAM_STEP_COUNT} steps")

  def compile(
    self, template: Cielo6StoredProgram, *, workspace: str, name: str
  ) -> Cielo6StoredProgram:
    """Compile this protocol using hardware-read device settings from ``template``."""
    _encode_storage_name(workspace, "workspace")
    _encode_storage_name(name, "program")
    steps = [self._compile_step(step) for step in self.steps]
    if self.cycles > 1:
      assert self.repeat_from_step is not None
      steps.append(
        Cielo6StoredProgramStep(
          name=0x5AF3,
          function=4,
          hold_time=0,
          forever=0,
          ramp_rate=0,
          delta_temperature=0,
          delta_time=0,
          to_step=self.repeat_from_step + 1,
          goto_times=self.cycles - 1,
          pause_before=0,
          pause_after=0,
          loop_nesting_times=0,
          collection_mode=0,
          nc=(0, 0),
          temperatures_raw=(0,) * 16,
        )
      )
    return replace(
      template,
      sample_volume=self.sample_volume,
      activation_step_number=0,
      melting_curve_mode=0,
      steps=tuple(steps),
      name=name,
      workspace=workspace,
      _unused_step_bytes=b"",
    )

  @staticmethod
  def _compile_step(step: Cielo6ThermalStep) -> Cielo6StoredProgramStep:
    """Compile one user-facing thermal step."""
    temperature_raw = round(step.temperature * 100)
    return Cielo6StoredProgramStep(
      name=0x5AF1,
      function=1,
      hold_time=step.hold_time,
      forever=0,
      ramp_rate=0,
      delta_temperature=0,
      delta_time=0,
      to_step=0,
      goto_times=0,
      pause_before=0,
      pause_after=0,
      loop_nesting_times=0,
      collection_mode=int(step.collect_fluorescence),
      nc=(0, 0),
      temperatures_raw=(temperature_raw,) * 3 + (0,) * 13,
    )


def _well_position(row: int, column: int) -> int:
  """Return the 96-well array position for a well in column-major order.

  The instrument stores fluorescence in the order A1..H1, A2..H2, ..., A12..H12,
  matching the vendor's exported CSV column headers. ``row`` is zero-based
  (0 = A) and ``column`` is one-based (1..12).
  """
  if not 0 <= row <= 7:
    raise ValueError(f"Cielo well row must be between 0 and 7, got {row}")
  if not 1 <= column <= 12:
    raise ValueError(f"Cielo well column must be between 1 and 12, got {column}")
  return (column - 1) * 8 + row


def _row_major_plate_data(values: tuple[float, ...]) -> List[List[Optional[float]]]:
  """Convert one Cielo column-major well array to PLR row-major plate data."""
  if len(values) != EXPERIMENT_WELL_COUNT:
    raise ValueError(
      f"Cielo plate data must contain {EXPERIMENT_WELL_COUNT} values, got {len(values)}"
    )
  return [[values[_well_position(row, column)] for column in range(1, 13)] for row in range(8)]


def _format_float32(value: float) -> str:
  """Format a value as the shortest decimal that preserves its float32 value."""
  single = struct.unpack("<f", struct.pack("<f", value))[0]
  for digits in range(1, 10):
    text = f"{single:.{digits}g}"
    if struct.unpack("<f", struct.pack("<f", float(text)))[0] == single:
      return text
  return f"{single:.9g}"


@dataclass(frozen=True)
class Cielo6CollectionPoint:
  """Fluorescence collected at one protocol step execution.

  ``channels[channel][position]`` holds the raw reading for the well at that
  position; use :meth:`well_value` to address wells by row and column.
  """

  step: int
  cycle: int
  channels: tuple[tuple[float, ...], ...]

  def __post_init__(self) -> None:
    """Validate all channel and well dimensions."""
    if len(self.channels) != EXPERIMENT_CHANNEL_COUNT:
      raise ValueError(
        f"Cielo collection point must have {EXPERIMENT_CHANNEL_COUNT} channels, "
        f"got {len(self.channels)}"
      )
    for channel in self.channels:
      if len(channel) != EXPERIMENT_WELL_COUNT:
        raise ValueError(
          f"Cielo collection point channel must contain {EXPERIMENT_WELL_COUNT} "
          f"values, got {len(channel)}"
        )

  def well_value(self, row: int, column: int, channel: int) -> float:
    """Return the fluorescence for one well and channel (0-based channel)."""
    if not 0 <= channel < EXPERIMENT_CHANNEL_COUNT:
      raise ValueError(f"Cielo channel must be between 0 and 5, got {channel}")
    return self.channels[channel][_well_position(row, column)]


@dataclass
class Cielo6AmplificationResult:
  """Result of one Cielo amplification measurement.

  Attributes:
    data: Plate data indexed by ``[row][column]``. The value is ``None`` for an
      unmeasured well.
    cycle: Cycle number reported in the Cielo result file.
    step: Protocol step number reported in the Cielo result file.
    channel_index: Zero-based optical channel index.
  """

  data: List[List[Optional[float]]]
  cycle: int
  step: int
  channel_index: int


@dataclass
class Cielo6MeltingCurveResult:
  """Result of one Cielo melting-curve measurement.

  Attributes:
    data: Plate data indexed by ``[row][column]``. The value is ``None`` for an
      unmeasured well.
    temperature: Sample temperature in degrees Celsius.
    channel_index: Zero-based optical channel index.
  """

  data: List[List[Optional[float]]]
  temperature: float
  channel_index: int


@dataclass(frozen=True)
class Cielo6ResultFile:
  """Decoded ``.AZE`` experiment data downloaded from the Cielo firmware.

  The file is a length-prefixed binary envelope: magic, the stored program,
  a JSON metadata block, an optional melting section, and separate raw and
  instrument-processed amplification sections. The binary melting section
  contains channel 1. The metadata can contain melting data for channels 2-6.
  The JSON keys retain the vendor's trailing-colon spelling.
  """

  device_id: str
  device_name: str
  software_versions: tuple[str, str, str]
  workspace: str
  program: str
  run_started_at: str
  run_ended_at: str
  gain: int
  exposure_times: tuple[int, ...]
  dyes: tuple[tuple[str, ...], ...]
  dye_crosstalk_coefficients: dict[str, tuple[float, ...]]
  temperature_curves: dict[str, tuple[int, ...]]
  stored_program: Cielo6StoredProgram
  collection_points: tuple[Cielo6CollectionPoint, ...]
  melt_records: tuple[Cielo6MeltRecord, ...] = ()
  processed_collection_points: tuple[Cielo6CollectionPoint, ...] = ()

  def to_amplification_results(self) -> List[Cielo6AmplificationResult]:
    """Return the raw amplification measurements in PLR plate-data format."""
    return self._to_amplification_results(self.collection_points)

  def to_processed_amplification_results(self) -> List[Cielo6AmplificationResult]:
    """Return the instrument-processed amplification measurements."""
    return self._to_amplification_results(self.processed_collection_points)

  def to_melting_curve_results(self) -> List[Cielo6MeltingCurveResult]:
    """Return the melting-curve measurements in PLR plate-data format."""
    return [
      Cielo6MeltingCurveResult(
        data=_row_major_plate_data(record.values),
        temperature=record.temperature,
        channel_index=record.channel_index,
      )
      for record in self.melt_records
    ]

  def _to_amplification_results(
    self, points: tuple[Cielo6CollectionPoint, ...]
  ) -> List[Cielo6AmplificationResult]:
    """Convert Cielo amplification records to PLR plate-data format."""
    return [
      Cielo6AmplificationResult(
        data=_row_major_plate_data(point.channels[channel_index]),
        cycle=point.cycle,
        step=point.step,
        channel_index=channel_index,
      )
      for point in points
      for channel_index in range(EXPERIMENT_CHANNEL_COUNT)
      if self.stored_program.channels[channel_index] > 0
    ]

  @classmethod
  def from_bytes(cls, data: bytes) -> "Cielo6ResultFile":
    """Decode one complete ``.AZE`` experiment file."""

    def section(offset: int, size: int, name: str) -> bytes:
      """Read a bounded section from the experiment file."""
      end = offset + size
      if size < 0 or end > len(data):
        raise Cielo6Error(
          f"Incomplete Cielo experiment file {name}: expected {size} bytes at "
          f"offset {offset}, file has {len(data)} bytes"
        )
      return data[offset:end]

    def length_at(offset: int, name: str, *, signed: bool = False) -> int:
      """Read one big-endian section length."""
      return int.from_bytes(section(offset, 4, f"{name} length"), byteorder="big", signed=signed)

    magic_length = length_at(0, "magic")
    magic = section(4, magic_length, "magic")
    if magic.rstrip(b"\0") != b"Azure Data":
      raise Cielo6Error("Invalid Cielo experiment file magic")

    offset = 4 + magic_length
    program_length = length_at(offset, "program")
    offset += 4
    stored_program = Cielo6StoredProgram.from_bytes(section(offset, program_length, "program"))
    offset += program_length

    datainfo_length = length_at(offset, "metadata")
    offset += 4
    try:
      datainfo = json.loads(section(offset, datainfo_length, "metadata").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
      raise Cielo6Error("Invalid Cielo experiment JSON metadata") from error
    if not isinstance(datainfo, dict):
      raise Cielo6Error("Invalid Cielo experiment JSON metadata: expected an object")
    offset += datainfo_length

    melting_length = length_at(offset, "melting data", signed=True)
    offset += 4
    melt_records: tuple[Cielo6MeltRecord, ...] = ()
    if melting_length < -1:
      raise Cielo6Error(f"Invalid Cielo melting data length: {melting_length}")
    if melting_length > 0:
      if melting_length % MELTING_DATA_SIZE != 0:
        raise Cielo6Error(
          f"Invalid Cielo melting data length: {melting_length} is not a multiple of "
          f"{MELTING_DATA_SIZE}"
        )
      section(offset, melting_length, "melting data")
      melt_records = tuple(
        Cielo6MeltRecord(
          temperature_raw=struct.unpack_from("<i", data, offset + index * MELTING_DATA_SIZE)[0],
          values=struct.unpack_from(
            f"<{EXPERIMENT_WELL_COUNT}f",
            data,
            offset + index * MELTING_DATA_SIZE + 4,
          ),
          channel_index=0,
        )
        for index in range(melting_length // MELTING_DATA_SIZE)
      )
      offset += melting_length

    experiment_length = length_at(offset, "amplification data", signed=True)
    offset += 4
    if experiment_length < -1:
      raise Cielo6Error(f"Invalid Cielo experiment data length: {experiment_length}")
    if experiment_length > 0 and experiment_length % EXPERIMENT_DATA_SIZE != 0:
      raise Cielo6Error(
        f"Invalid Cielo experiment data length: {experiment_length} is not a multiple of "
        f"{EXPERIMENT_DATA_SIZE}"
      )
    if experiment_length > 0:
      section(offset, experiment_length, "amplification data")

    def parse_collection_points(start: int, size: int) -> tuple[Cielo6CollectionPoint, ...]:
      """Decode consecutive amplification records."""
      return tuple(
        Cielo6CollectionPoint(
          step=struct.unpack_from("<H", data, start + index * EXPERIMENT_DATA_SIZE)[0],
          cycle=struct.unpack_from("<H", data, start + index * EXPERIMENT_DATA_SIZE + 2)[0],
          channels=tuple(
            struct.unpack_from(
              f"<{EXPERIMENT_WELL_COUNT}f",
              data,
              start + index * EXPERIMENT_DATA_SIZE + 4 + channel * EXPERIMENT_WELL_COUNT * 4,
            )
            for channel in range(EXPERIMENT_CHANNEL_COUNT)
          ),
        )
        for index in range(size // EXPERIMENT_DATA_SIZE)
      )

    collection_points: tuple[Cielo6CollectionPoint, ...] = ()
    if experiment_length > 0:
      collection_points = parse_collection_points(offset, experiment_length)
      offset += experiment_length

    processed_length = length_at(offset, "processed amplification data", signed=True)
    offset += 4
    processed_collection_points: tuple[Cielo6CollectionPoint, ...] = ()
    if processed_length < -1:
      raise Cielo6Error(f"Invalid Cielo processed amplification data length: {processed_length}")
    if processed_length > 0:
      if processed_length % EXPERIMENT_DATA_SIZE != 0:
        raise Cielo6Error(
          f"Invalid Cielo processed amplification data length: {processed_length} is not a "
          f"multiple of {EXPERIMENT_DATA_SIZE}"
        )
      section(offset, processed_length, "processed amplification data")
      processed_collection_points = parse_collection_points(offset, processed_length)
      offset += processed_length
    if offset != len(data):
      raise Cielo6Error(f"Invalid Cielo experiment file: {len(data) - offset} trailing byte(s)")

    def text(key: str, default: str = "") -> str:
      """Read one scalar metadata value as text."""
      value = datainfo.get(key)
      if value is None:
        return default
      if not isinstance(value, (str, int, float, bool)):
        raise Cielo6Error(f"Invalid Cielo experiment {key!r} value: expected a scalar")
      return str(value)

    def integers(key: str) -> tuple[int, ...]:
      """Read one optional metadata array as integers."""
      value = datainfo.get(key)
      if value is None:
        return ()
      if not isinstance(value, list):
        raise Cielo6Error(f"Invalid Cielo experiment {key!r} value: expected an array")
      result = []
      for item in value:
        try:
          result.append(int(item))
        except (TypeError, ValueError):
          raise Cielo6Error(f"Invalid Cielo experiment {key!r} value: {item!r}") from None
      return tuple(result)

    def floats(key: str) -> tuple[float, ...]:
      """Read one optional metadata array as floats."""
      value = datainfo.get(key)
      if value is None:
        return ()
      if not isinstance(value, list):
        raise Cielo6Error(f"Invalid Cielo experiment {key!r} value: expected an array")
      result = []
      for item in value:
        try:
          result.append(float(item))
        except (TypeError, ValueError):
          raise Cielo6Error(f"Invalid Cielo experiment {key!r} value: {item!r}") from None
      return tuple(result)

    def strings(key: str) -> tuple[str, ...]:
      """Read one optional metadata array as text values."""
      value = datainfo.get(key)
      if value is None:
        return ()
      if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Cielo6Error(f"Invalid Cielo experiment {key!r} value: expected a text array")
      return tuple(value)

    def metadata_melt_records(channel_index: int) -> tuple[Cielo6MeltRecord, ...]:
      """Read one additional optical channel from the flat metadata array."""
      key = f"MeltCurveChannel{channel_index + 1}"
      value = datainfo.get(key)
      if value is None:
        return ()
      if not isinstance(value, list):
        raise Cielo6Error(f"Invalid Cielo experiment {key!r} value: expected an array")

      expected_value_count = len(melt_records) * EXPERIMENT_WELL_COUNT
      if len(value) != expected_value_count:
        raise Cielo6Error(
          f"Invalid Cielo experiment {key!r} value count: expected "
          f"{expected_value_count}, got {len(value)}"
        )
      try:
        values = tuple(float(item) for item in value)
      except (TypeError, ValueError) as error:
        raise Cielo6Error(f"Invalid Cielo experiment {key!r} value") from error
      return tuple(
        Cielo6MeltRecord(
          temperature_raw=record.temperature_raw,
          values=values[index * EXPERIMENT_WELL_COUNT : (index + 1) * EXPERIMENT_WELL_COUNT],
          channel_index=channel_index,
        )
        for index, record in enumerate(melt_records)
      )

    melt_records += tuple(
      record
      for channel_index in range(1, EXPERIMENT_CHANNEL_COUNT)
      for record in metadata_melt_records(channel_index)
    )

    dyes = tuple(strings(f"Channel{channel}") for channel in range(1, EXPERIMENT_CHANNEL_COUNT + 1))
    crosstalk = {
      str(dye): floats(dye)
      for channel_dyes in dyes
      for dye in channel_dyes
      if dye != "default" and datainfo.get(dye) is not None
    }
    temperature_keys = (
      "Block1Temp",
      "Block2Temp",
      "Block3Temp",
      "Sample1Temp",
      "Sample2Temp",
      "Sample3Temp",
      "HotlidTemp",
    )
    try:
      gain = int(datainfo.get("Gain:", 0))
      exposure_times = tuple(
        int(datainfo.get(f"Channel {channel}expose time", 0))
        for channel in range(1, EXPERIMENT_CHANNEL_COUNT + 1)
      )
    except (TypeError, ValueError) as error:
      raise Cielo6Error("Invalid Cielo experiment gain or exposure time") from error

    required = {
      "device_id": text("Device id:"),
      "workspace": text("Workspace:"),
      "program": text("Program:"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
      raise Cielo6Error(f"Cielo experiment metadata is missing {', '.join(missing)}")

    return cls(
      device_id=required["device_id"],
      device_name=text("Device name:"),
      software_versions=(
        text("Instrument software Version:"),
        text("Instrument control module software Version:"),
        text("Instrument heater module software Version:"),
      ),
      workspace=required["workspace"],
      program=required["program"],
      run_started_at=text("Run start time:"),
      run_ended_at=text("Run end time:"),
      gain=gain,
      exposure_times=exposure_times,
      dyes=dyes,
      dye_crosstalk_coefficients=crosstalk,
      temperature_curves={key: integers(key) for key in temperature_keys},
      stored_program=stored_program,
      collection_points=collection_points,
      melt_records=melt_records,
      processed_collection_points=processed_collection_points,
    )

  def to_amplification_csv(self) -> str:
    """Serialize fluorescence in the OEM ``Amplification Values`` CSV shape.

    The OEM export writes a column-major well header, one ``Step{n}Channel{m}``
    label row per enabled channel, then one row per collection point starting
    with the cycle number. Values use a compact float32-preserving decimal
    representation. This output matches the OEM CSV structure and numeric values.
    """
    lines = ["Data,"]
    for column in range(1, 13):
      for row_index in range(8):
        lines.append(f"{chr(ord('A') + row_index)}{column},")
    lines.append("\r\n")

    by_step: dict[int, list[Cielo6CollectionPoint]] = {}
    for point in self.collection_points:
      by_step.setdefault(point.step, []).append(point)

    for step in sorted(by_step):
      points = tuple(by_step[step])
      for channel in range(EXPERIMENT_CHANNEL_COUNT):
        if self.stored_program.channels[channel] <= 0:
          continue
        lines.append(f"Step{step}Channel{channel + 1}\r\n")
        for point in points:
          values = ",".join(_format_float32(value) for value in point.channels[channel])
          lines.append(f"{point.cycle},{values},\r\n")
    return "\ufeff" + "".join(lines)

  def to_melting_csv(self) -> str:
    """Serialize melting records in the OEM ``MeltingCurve`` CSV shape."""
    lines = ["Temperature,"]
    for column in range(1, 13):
      for row_index in range(8):
        lines.append(f"{chr(ord('A') + row_index)}{column},")
    lines.append("\r\n")
    for record in self.melt_records:
      values = ",".join(_format_float32(value) for value in record.values)
      lines.append(f"{record.temperature:g},{values},\r\n")
    return "\ufeff" + "".join(lines)


class Cielo6:
  """Azure BioSystems Cielo 6 qPCR instrument.

  This driver supports read requests, empty-workspace operations, and experiment
  runs. It uses the USB serial interface at 1,500,000 baud. The FT232R VID and
  PID are not unique to this instrument. Therefore, the caller must select a
  port. Setup requests the firmware identity. Setup also verifies a specified
  device ID.

  ``run_experiment`` and ``run_protocol`` control a complete physical run and
  heat the block. Command ``0x0B08`` transfers a program but does not store the
  program. Therefore, PLR does not expose this command as a storage operation.
  """

  def __init__(self, port: str, device_id: Optional[str] = None, timeout: float = 1.0) -> None:
    """Configure the serial transport without opening it."""
    if device_id is not None:
      _encode_device_id(device_id)
    self.device_id = device_id
    self.io = Serial(
      human_readable_device_name="Azure BioSystems Cielo 6",
      port=port,
      baudrate=1_500_000,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=timeout,
      write_timeout=timeout,
    )
    self._request_lock = asyncio.Lock()
    self._operation_lock = asyncio.Lock()
    self._run_transition_lock = asyncio.Lock()
    self._stop_requested = asyncio.Event()
    self._is_setup = False
    self._receive_buffer = bytearray()
    self._active_program: Optional[Cielo6StoredProgram] = None
    self._active_run_identity: Optional[tuple[tuple[int, int], tuple[int, int], str]] = None
    self._run_phase = _Cielo6RunPhase.NONE
    self._running_data: list[Cielo6RunningData] = []
    self._melting_data: list[Cielo6MeltingData] = []
    self.identity: Optional[Cielo6Identity] = None
    self.latest_status: Optional[Cielo6Status] = None

  @property
  def running_data(self) -> tuple[Cielo6RunningData, ...]:
    """Return live amplification frames decoded so far in the current run."""
    return tuple(self._running_data)

  @property
  def melting_data(self) -> tuple[Cielo6MeltingData, ...]:
    """Return live melting frames decoded so far in the current run."""
    return tuple(self._melting_data)

  async def setup(self) -> None:
    """Open the transport and verify the firmware identity."""
    async with self._operation_lock:
      if self._is_setup:
        return
      self._receive_buffer.clear()
      self._running_data.clear()
      self._melting_data.clear()
      self.identity = None
      self.latest_status = None
      try:
        await self.io.setup()
        identity = await self._request_identity(require_setup=False)
        if self.device_id is not None and identity.device_id != self.device_id:
          raise Cielo6Error(
            f"Connected Cielo ID {identity.device_id!r} does not match {self.device_id!r}"
          )
      except BaseException:
        await self._stop_after_setup_failure()
        raise
      self.device_id = identity.device_id
      self.identity = identity
      self._is_setup = True
      logger.info("[%s] connected to %s (%s)", self.io.port, identity.name, identity.device_id)

  async def stop(self) -> None:
    """Close the serial connection."""
    async with self._operation_lock:
      if not self._is_setup:
        return
      async with self._request_lock:
        self._is_setup = False
        try:
          await self.io.stop()
        finally:
          self.identity = None
      logger.info("[%s] disconnected", self.io.port)

  async def request_identity(self) -> Cielo6Identity:
    """Request the firmware identity without a change to the instrument state."""
    return await self._request_identity()

  async def request_status(self) -> Cielo6Status:
    """Read the instrument's current status without changing its state."""
    frame = await self._request(STATUS_QUERY_COMMAND)
    status = Cielo6Status.from_payload(frame.payload)
    self.latest_status = status
    return status

  async def request_run_state(
    self, program: Optional[Cielo6StoredProgram] = None
  ) -> Cielo6RunState:
    """Request one run snapshot and normalize the values for PLR.

    This method always requests status. It requests the experiment identity only
    during an active run. Supply ``program`` for a run that another driver
    instance started. This instance uses its uploaded program automatically.
    """
    status = await self.request_status()
    active = status.is_running or status.is_paused
    experiment = await self.request_running_experiment_info() if active else None
    if (
      program is None
      and active
      and self._active_program is not None
      and self._run_phase is _Cielo6RunPhase.NONE
    ):
      self._clear_active_run_context()
    active_program = program if program is not None else self._active_program
    if program is None and self._run_phase is not _Cielo6RunPhase.DISPATCHED:
      active_program = None
    if not active and not (program is None and self._run_phase is _Cielo6RunPhase.DISPATCHED):
      active_program = None
    if (
      program is None
      and active_program is not None
      and active
      and self._run_phase is _Cielo6RunPhase.DISPATCHED
      and self._active_run_identity is None
    ):
      self._active_run_identity = self._run_identity(status)
    if (
      program is None
      and active_program is not None
      and active
      and self._run_phase is _Cielo6RunPhase.DISPATCHED
      and self._active_run_identity is not None
      and self._run_identity(status) != self._active_run_identity
    ):
      self._clear_active_run_context()
      active_program = None
    current_step_index = status.current_step - 1 if status.current_step > 0 else None
    current_cycle_index = status.current_cycle - 1 if status.current_cycle > 0 else None
    target_temperatures = None
    if (
      active_program is not None
      and current_step_index is not None
      and current_step_index < active_program.step_count
    ):
      target_temperatures = active_program.step_target_temperatures(current_step_index)
    state = Cielo6RunState(
      status=status,
      experiment=experiment,
      observed_at=datetime.now(timezone.utc),
      current_step_index=current_step_index,
      current_cycle_index=current_cycle_index,
      total_step_count=None if active_program is None else active_program.thermal_step_count,
      total_cycle_count=None if active_program is None else active_program.cycle_count,
      target_temperatures=target_temperatures,
      amplification_data=self.running_data,
      melting_data=self.melting_data,
    )
    if (
      status.work_state is Cielo6WorkState.IDLE
      and status.finished
      and self._run_phase is _Cielo6RunPhase.DISPATCHED
      and self._active_run_identity is not None
      and self._run_identity(status) == self._active_run_identity
    ):
      self._clear_active_run_context()
    return state

  async def request_workspace_summary(self) -> dict[str, list[str]]:
    """Return stored workspace names and their program names without modifying storage."""
    summary_frame = await self._request(WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", 0))
    if len(summary_frame.payload) < 8:
      raise Cielo6Error("Invalid Cielo workspace summary header")
    response_index, entry_count = struct.unpack_from("<II", summary_frame.payload)
    if response_index != 0:
      raise Cielo6Error(
        f"Invalid Cielo workspace summary header index: expected 0, got {response_index}"
      )

    result: dict[str, list[str]] = {}
    for request_index in range(1, entry_count + 1):
      entry_frame = await self._request(
        WORKSPACE_SUMMARY_GET_COMMAND, struct.pack("<I", request_index)
      )
      if len(entry_frame.payload) < 4:
        raise Cielo6Error(f"Invalid Cielo workspace summary entry {request_index}")
      response_index = struct.unpack_from("<I", entry_frame.payload)[0]
      if response_index != request_index:
        raise Cielo6Error(
          f"Unexpected Cielo workspace summary index: expected {request_index}, "
          f"got {response_index}"
        )
      try:
        entry = entry_frame.payload[4:].decode("ascii")
      except UnicodeDecodeError as error:
        raise Cielo6Error(f"Cielo workspace summary entry {request_index} is not ASCII") from error
      workspace, separator, program = entry.partition("^")
      if separator == "" or workspace == "":
        raise Cielo6Error(f"Invalid Cielo workspace summary entry {request_index}: {entry!r}")
      programs = result.setdefault(workspace, [])
      if program:
        programs.append(program)
    return result

  async def request_program(self, workspace: str, program: str) -> Cielo6StoredProgram:
    """Retrieve and verify a stored program without modifying instrument storage."""
    self._require_setup()
    payload = (
      _encode_storage_name(workspace, "workspace") + b"^" + _encode_storage_name(program, "program")
    )

    device_id = self.device_id
    if device_id is None:
      raise Cielo6Error("Cielo device ID is unknown. Call setup() before you send commands.")
    request = CieloFrame(device_id=device_id, command=PROGRAM_GET_COMMAND, payload=payload)
    chunks: dict[int, bytes] = {}
    async with self._request_lock:
      self._require_setup()
      await self.io.write(request.to_bytes())
      for _ in range(PROGRAM_CHUNK_COUNT):
        response = await self._read_matching_frame(PROGRAM_GET_COMMAND, device_id)
        if len(response.payload) != PROGRAM_CHUNK_SIZE + 1:
          raise Cielo6Error(
            f"Invalid Cielo program chunk size: expected {PROGRAM_CHUNK_SIZE + 1}, "
            f"got {len(response.payload)}"
          )
        index = response.payload[0]
        if index >= PROGRAM_CHUNK_COUNT:
          raise Cielo6Error(f"Invalid Cielo program chunk index: {index}")
        if index in chunks:
          raise Cielo6Error(f"Duplicate Cielo program chunk index: {index}")
        chunks[index] = response.payload[1:]
    return Cielo6StoredProgram.from_bytes(
      b"".join(chunks[index] for index in range(PROGRAM_CHUNK_COUNT))
    )

  async def request_experiment_summary(self) -> tuple[Cielo6ExperimentInfo, ...]:
    """Return identities and raw timestamps for all stored experiment results."""
    header = await self._request(EXPERIMENT_DATA_SUMMARY_GET_COMMAND, struct.pack("<I", 0))
    if len(header.payload) < 8:
      raise Cielo6Error("Invalid Cielo experiment summary header")
    response_index, entry_count = struct.unpack_from("<II", header.payload)
    if response_index != 0:
      raise Cielo6Error(
        f"Invalid Cielo experiment summary header index: expected 0, got {response_index}"
      )

    results = []
    for request_index in range(1, entry_count + 1):
      frame = await self._request(
        EXPERIMENT_DATA_SUMMARY_GET_COMMAND, struct.pack("<I", request_index)
      )
      if len(frame.payload) < 4:
        raise Cielo6Error(f"Invalid Cielo experiment summary entry {request_index}")
      response_index = struct.unpack_from("<I", frame.payload)[0]
      if response_index != request_index:
        raise Cielo6Error(
          f"Unexpected Cielo experiment summary index: expected {request_index}, "
          f"got {response_index}"
        )
      try:
        fields = frame.payload[4:].decode("ascii").split("^")
      except UnicodeDecodeError as error:
        raise Cielo6Error(f"Cielo experiment summary entry {request_index} is not ASCII") from error
      if len(fields) < 5:
        raise Cielo6Error(
          f"Invalid Cielo experiment summary entry {request_index}: expected 5 fields, "
          f"got {len(fields)}"
        )
      results.append(
        Cielo6ExperimentInfo(
          workspace=fields[0],
          protocol=fields[1],
          name=fields[2].replace("\0", ""),
          started_at_raw=fields[3],
          ended_at_raw=fields[4],
        )
      )
    return tuple(results)

  async def request_running_experiment_info(self) -> Optional[Cielo6ExperimentInfo]:
    """Return the running experiment identity, or ``None`` when none is reported."""
    frame = await self._request(RUNNING_EXPERIMENT_INFOS_GET_COMMAND)
    if not frame.payload:
      return None
    try:
      fields = frame.payload.decode("ascii").split("^")
    except UnicodeDecodeError as error:
      raise Cielo6Error("Cielo running experiment information is not ASCII") from error
    if len(fields) < 3 or not fields[2]:
      return None
    return Cielo6ExperimentInfo(workspace=fields[0], protocol=fields[1], name=fields[2])

  async def request_experiment_data(self, experiment: Cielo6ExperimentInfo) -> bytes:
    """Request one stored ``.AZE`` result and verify its MD5 hash."""
    self._require_setup()
    path = "/".join((experiment.workspace, experiment.protocol, experiment.name))
    try:
      payload = path.encode("ascii")
    except UnicodeEncodeError as error:
      raise ValueError("Cielo experiment path must contain only ASCII characters") from error
    if not all((experiment.workspace, experiment.protocol, experiment.name)):
      raise ValueError("Cielo experiment workspace, protocol, and name must be non-empty")

    info = await self._request(EXPERIMENT_DATA_FILE_INFO_GET_COMMAND, payload)
    if len(info.payload) != 20:
      raise Cielo6Error(
        f"Invalid Cielo experiment file information: expected 20 bytes, got {len(info.payload)}"
      )
    expected_size = int.from_bytes(info.payload[:4], byteorder="little", signed=True)
    if expected_size < 0:
      raise Cielo6Error(f"Invalid Cielo experiment file size: {expected_size}")
    expected_md5 = info.payload[4:]

    device_id = self.device_id
    if device_id is None:
      raise Cielo6Error("Cielo device ID is unknown. Call setup() before you send commands.")
    request = CieloFrame(device_id, EXPERIMENT_DATA_FILE_GET_COMMAND, payload)
    data = bytearray()
    async with self._request_lock:
      self._require_setup()
      await self.io.write(request.to_bytes())
      while len(data) < expected_size:
        frame = await self._read_matching_frame(EXPERIMENT_DATA_FILE_GET_COMMAND, device_id)
        if not frame.payload:
          raise Cielo6Error(
            f"Cielo experiment download stopped after {len(data)} of {expected_size} bytes"
          )
        data.extend(frame.payload)
        if len(data) > expected_size:
          raise Cielo6Error(
            f"Cielo experiment download exceeded declared size {expected_size}: got {len(data)} bytes"
          )

    result = bytes(data)
    # The firmware uses MD5 for protocol-integrity verification. It does not use MD5 for security.
    received_md5 = hashlib.md5(result).digest()  # noqa: S324
    if received_md5 != expected_md5:
      raise Cielo6Error(
        f"Invalid Cielo experiment MD5: expected {expected_md5.hex()}, got {received_md5.hex()}"
      )
    return result

  async def create_workspace(self, workspace: str) -> None:
    """Create an empty workspace if the workspace does not exist."""
    _encode_storage_name(workspace, "workspace")
    async with self._operation_lock:
      self._require_setup()
      await self._create_workspace(workspace)

  async def delete_workspace(self, workspace: str) -> None:
    """Delete an empty workspace if the workspace exists."""
    _encode_storage_name(workspace, "workspace")
    async with self._operation_lock:
      self._require_setup()
      await self._delete_workspace(workspace)

  async def delete_program(self, workspace: str, program: str) -> None:
    """Permanently delete a stored program if the program exists."""
    workspace_payload = _encode_storage_name(workspace, "workspace")
    program_payload = _encode_storage_name(program, "program")
    async with self._operation_lock:
      self._require_setup()
      programs = (await self.request_workspace_summary()).get(workspace)
      if programs is None or program not in programs:
        return
      logger.info("[%s] deleting program %r from workspace %r", self.io.port, program, workspace)
      await self._mutation_request(
        PROGRAM_DELETE_COMMAND, workspace_payload + b"^" + program_payload
      )
      remaining_programs = (await self.request_workspace_summary()).get(workspace, [])
      if program in remaining_programs:
        raise Cielo6Error(
          f"Cielo acknowledged program deletion but {program!r} remained in workspace {workspace!r}"
        )

  async def stop_run(self) -> None:
    """Stop the current run and confirm the non-running state."""
    self._stop_requested.set()
    async with self._run_transition_lock:
      status = await self.request_status()
      self._raise_for_firmware_error(status, "preparing to stop the run")
      if status.is_running or status.is_paused:
        await self._mutation_request(STOP_COMMAND, b"")
        status = await self.request_status()
        self._raise_for_firmware_error(status, "confirming that the run stopped")
      if status.is_running or status.is_paused:
        raise Cielo6Error(
          "The Cielo acknowledged the stop request but still reports an active run."
        )
      if status.work_state is not Cielo6WorkState.IDLE:
        raise Cielo6Error(
          "The Cielo reported an unexpected state after the stop request "
          f"(work_status={status.work_status})."
        )
      self._clear_active_run_context()

  async def pause_run(self) -> None:
    """Pause the active run and confirm the paused state."""
    async with self._run_transition_lock:
      status = await self.request_status()
      self._raise_for_firmware_error(status, "preparing to pause the run")
      if status.is_paused:
        return
      if not status.is_running:
        raise Cielo6Error("Cannot pause the Cielo because no run is active.")
      await self._mutation_request(PAUSE_COMMAND, b"")
      status = await self.request_status()
      self._raise_for_firmware_error(status, "confirming that the run paused")
      if not status.is_paused:
        raise Cielo6Error(
          "The Cielo acknowledged the pause request but did not report a paused run "
          f"(work_status={status.work_status})."
        )

  async def resume_run(self) -> None:
    """Resume the paused run and confirm the running state."""
    async with self._run_transition_lock:
      status = await self.request_status()
      self._raise_for_firmware_error(status, "preparing to resume the run")
      if status.is_running:
        return
      if not status.is_paused:
        raise Cielo6Error("Cannot resume the Cielo because no run is paused.")
      await self._mutation_request(RESUME_COMMAND, b"")
      status = await self.request_status()
      self._raise_for_firmware_error(status, "confirming that the run resumed")
      if not status.is_running:
        raise Cielo6Error(
          "The Cielo acknowledged the resume request but did not report an active run "
          f"(work_status={status.work_status})."
        )

  async def run_protocol(
    self,
    protocol: Cielo6ThermalProtocol,
    *,
    template: Cielo6StoredProgram,
    workspace: str,
    program_name: str,
    result_name: Optional[str] = None,
    poll_interval: float = 2.0,
    timeout: Optional[float] = None,
  ) -> Cielo6ResultFile:
    """Compile a thermal protocol from a hardware-read template and run it.

    The workspace is created when missing so the result has a storage home.
    The method validates all wait settings before it creates the workspace.
    See :meth:`run_experiment` for the run and timeout behavior.
    """
    self._validate_run_wait(poll_interval=poll_interval, timeout=timeout)
    compiled = protocol.compile(template, workspace=workspace, name=program_name)
    resolved_result_name = self._resolve_result_name(result_name)
    async with self._operation_lock:
      self._require_setup()
      return await self._run_experiment(
        compiled,
        workspace=workspace,
        protocol=program_name,
        result_name=resolved_result_name,
        poll_interval=poll_interval,
        timeout=timeout,
        ensure_workspace=True,
      )

  async def run_experiment(
    self,
    program: Cielo6StoredProgram,
    *,
    workspace: str,
    protocol: str,
    result_name: Optional[str] = None,
    poll_interval: float = 2.0,
    timeout: Optional[float] = None,
  ) -> Cielo6ResultFile:
    """Run a compiled program and download its verified result.

    This method starts a physical run and heats the block.
    The method downloads the ``.AZE`` result and verifies its firmware MD5 value.
    The method releases the firmware session after success or failure.

    A timeout stops only the PLR wait operation. It does not stop the physical
    run. :class:`Cielo6RunTimeoutError` contains the last firmware status. The
    backend also keeps the active program for subsequent state requests.
    """
    self._validate_run_wait(poll_interval=poll_interval, timeout=timeout)
    _encode_storage_name(workspace, "workspace")
    _encode_storage_name(protocol, "protocol")
    resolved_result_name = self._resolve_result_name(result_name)
    async with self._operation_lock:
      self._require_setup()
      return await self._run_experiment(
        program,
        workspace=workspace,
        protocol=protocol,
        result_name=resolved_result_name,
        poll_interval=poll_interval,
        timeout=timeout,
      )

  async def _create_workspace(self, workspace: str) -> None:
    """Create a workspace while the operation lock is held."""
    payload = _encode_storage_name(workspace, "workspace")
    if workspace in await self.request_workspace_summary():
      return
    logger.info("[%s] creating workspace %r", self.io.port, workspace)
    await self._mutation_request(WORKSPACE_CREATE_COMMAND, payload)
    if workspace not in await self.request_workspace_summary():
      raise Cielo6Error(
        f"Cielo acknowledged workspace creation but {workspace!r} was not present in readback"
      )

  async def _delete_workspace(self, workspace: str) -> None:
    """Delete an empty workspace while the operation lock is held."""
    payload = _encode_storage_name(workspace, "workspace")
    summary = await self.request_workspace_summary()
    programs = summary.get(workspace)
    if programs is None:
      return
    if programs:
      raise Cielo6Error(
        f"Cannot delete non-empty Cielo workspace {workspace!r}. Delete its programs first."
      )
    logger.info("[%s] deleting empty workspace %r", self.io.port, workspace)
    await self._mutation_request(WORKSPACE_DELETE_COMMAND, payload)
    if workspace in await self.request_workspace_summary():
      raise Cielo6Error(
        f"Cielo acknowledged workspace deletion but {workspace!r} remained in readback"
      )

  async def _run_experiment(
    self,
    program: Cielo6StoredProgram,
    *,
    workspace: str,
    protocol: str,
    result_name: str,
    poll_interval: float,
    timeout: Optional[float],
    ensure_workspace: bool = False,
  ) -> Cielo6ResultFile:
    """Run one experiment while the operation lock is held."""
    self._active_program = program
    self._active_run_identity = None
    self._run_phase = _Cielo6RunPhase.PREPARING
    self._stop_requested.clear()
    self._running_data.clear()
    self._melting_data.clear()
    run_finished = False
    try:
      async with self._firmware_session() as lock_status:
        self._require_idle_for_run(lock_status)
        async with self._run_transition_lock:
          self._raise_if_stop_requested()
          if ensure_workspace:
            await self._create_workspace(workspace)
            self._raise_if_stop_requested()
          await self._initialize()
          self._raise_if_stop_requested()
          await self._upload_program(program)
          self._raise_if_stop_requested()
          await self._set_result_path(workspace, protocol, result_name)
          self._raise_if_stop_requested()
          started_status = await self._start_run()
          self._active_run_identity = self._run_identity(started_status)
        await self._wait_for_completion(poll_interval=poll_interval, timeout=timeout)
        run_finished = True
        experiment = await self._find_experiment(workspace, protocol, result_name)
        return Cielo6ResultFile.from_bytes(await self.request_experiment_data(experiment))
    except BaseException as error:
      if isinstance(error, Cielo6RunTimeoutError) and (
        error.latest_status.is_running or error.latest_status.is_paused
      ):
        self._active_run_identity = self._run_identity(error.latest_status)
      raise
    finally:
      if self._run_phase is _Cielo6RunPhase.PREPARING or run_finished:
        self._clear_active_run_context()

  @asynccontextmanager
  async def _firmware_session(self) -> AsyncIterator[Cielo6Status]:
    """Acquire one firmware session and release it exactly once."""
    status = await self._lock()
    try:
      yield status
    except BaseException:
      try:
        await self._disconnect_session()
      except Exception:
        logger.exception("[%s] failed to release the Cielo session", self.io.port)
      raise
    else:
      await self._disconnect_session()

  @staticmethod
  def _resolve_result_name(result_name: Optional[str]) -> str:
    """Return a validated result name, generating one when necessary."""
    resolved = result_name or f"PLR-{datetime.now():%Y%m%d-%H%M%S-%f}"
    _encode_storage_name(resolved, "result")
    return resolved

  async def _request_identity(self, *, require_setup: bool = True) -> Cielo6Identity:
    """Request identity while setup owns the device lifecycle."""
    request = CieloFrame(device_id=DISCOVERY_DEVICE_ID, command=VERSION_CHECK_COMMAND)
    async with self._request_lock:
      if require_setup:
        self._require_setup()
      await self.io.write(request.to_bytes())
      response = await self._read_exact(IDENTITY_RESPONSE_SIZE)
    return Cielo6Identity.from_bytes(response)

  async def _stop_after_setup_failure(self) -> None:
    """Close a partially initialized transport without masking the setup error."""
    try:
      await self.io.stop()
    except Exception:
      logger.exception("[%s] failed to close the Cielo after setup failure", self.io.port)

  def _require_setup(self) -> None:
    """Require a verified, open Cielo transport."""
    if not self._is_setup:
      raise RuntimeError("Cielo 6 is not set up. Call setup() first.")

  async def _lock(self) -> Cielo6Status:
    """Acquire the firmware session and return its status."""
    frame = await self._request(SESSION_LOCK_COMMAND)
    status = Cielo6Status.from_payload(frame.payload)
    self.latest_status = status
    return status

  async def _initialize(self) -> None:
    """Prepare the instrument for an uploaded experiment."""
    await self._mutation_request(INITIALIZE_COMMAND, b"")

  async def _upload_program(self, program: Cielo6StoredProgram) -> None:
    """Transfer a compiled program without storage on the instrument."""
    self._require_setup()
    device_id = self.device_id
    if device_id is None:
      raise Cielo6Error("Cielo device ID is unknown. Call setup() before you send commands.")
    data = program.to_bytes()
    assert len(data) == PROGRAM_SIZE
    async with self._request_lock:
      self._require_setup()
      for index in range(PROGRAM_CHUNK_COUNT):
        chunk = data[index * PROGRAM_CHUNK_SIZE : (index + 1) * PROGRAM_CHUNK_SIZE]
        request = CieloFrame(device_id, PROGRAM_UPLOAD_COMMAND, bytes([index]) + chunk)
        await self.io.write(request.to_bytes())
        response = await self._read_matching_frame(PROGRAM_UPLOAD_COMMAND, device_id)
        self._validate_mutation_result(response)

  async def _set_result_path(self, workspace: str, protocol: str, result_name: str) -> None:
    """Set the storage path for the completed experiment."""
    payload = b"^".join(
      (
        _encode_storage_name(workspace, "workspace"),
        _encode_storage_name(protocol, "protocol"),
        _encode_storage_name(result_name, "result"),
      )
    )
    await self._mutation_request(RESULT_PATH_SET_COMMAND, payload)

  async def _start_run(self, *, wait: float = 1.0, attempts: int = 120) -> Cielo6Status:
    """Start the uploaded program and confirm the new run state."""
    if wait < 0:
      raise ValueError("Cielo run confirmation wait cannot be negative")
    if attempts < 1:
      raise ValueError("Cielo run confirmation attempts must be at least 1")

    status_before_run = await self.request_status()
    self._require_idle_for_run(status_before_run)
    device_id = self.device_id
    if device_id is None:
      raise Cielo6Error("Cielo device ID is unknown. Call setup() before you send commands.")

    request = CieloFrame(device_id=device_id, command=RUN_COMMAND)
    async with self._request_lock:
      self._raise_if_stop_requested()
      self._run_phase = _Cielo6RunPhase.DISPATCHED
      await self.io.write(request.to_bytes())

    await asyncio.sleep(wait)
    status = status_before_run
    for _ in range(attempts):
      status = await self.request_status()
      self._raise_for_firmware_error(status, "confirming that the run started")
      if status.is_running or status.is_paused:
        return status
      if self._is_new_completed_run(status_before_run, status):
        return status
      await asyncio.sleep(wait)
    raise Cielo6RunTimeoutError(status)

  @staticmethod
  def _is_new_completed_run(previous: Cielo6Status, current: Cielo6Status) -> bool:
    """Return true if status identifies a new run that is complete."""
    if current.work_status != WORK_STATUS_IDLE or not current.finished:
      return False
    previous_identity = (
      previous.run_id,
      previous.sample_id,
      previous.file_name,
      previous.finished,
    )
    current_identity = (current.run_id, current.sample_id, current.file_name, current.finished)
    return current_identity != previous_identity

  async def _disconnect_session(self) -> None:
    """Release the session that :meth:`_lock` acquired."""
    await self._mutation_request(DISCONNECT_COMMAND, b"")

  async def _wait_for_completion(
    self, *, poll_interval: float, timeout: Optional[float]
  ) -> Cielo6Status:
    """Wait for authoritative completion status from the firmware."""
    deadline = None if timeout is None else time.monotonic() + timeout
    completion_settle_deadline: Optional[float] = None
    while True:
      status = await self.request_status()
      self._raise_for_firmware_error(status, "waiting for run completion")
      now = time.monotonic()
      if status.work_state is Cielo6WorkState.IDLE:
        if status.finished:
          return status
        if completion_settle_deadline is None:
          completion_settle_deadline = now + _COMPLETION_SETTLE_TIMEOUT
        elif now >= completion_settle_deadline:
          raise Cielo6Error(
            "The Cielo remained idle without reporting run completion "
            f"(work_status={status.work_status})."
          )
      elif status.is_running or status.is_paused:
        completion_settle_deadline = None
      else:
        raise Cielo6Error(
          "The Cielo reported an unexpected state while waiting for run completion "
          f"(work_status={status.work_status})."
        )
      if deadline is not None and now >= deadline:
        raise Cielo6RunTimeoutError(status)
      await asyncio.sleep(
        poll_interval
        if completion_settle_deadline is None
        else min(poll_interval, _COMPLETION_SETTLE_POLL_INTERVAL)
      )

  @staticmethod
  def _validate_run_wait(*, poll_interval: float, timeout: Optional[float]) -> None:
    """Validate wait settings before PLR sends a command that changes the instrument."""
    if poll_interval <= 0:
      raise ValueError("Cielo poll_interval must be greater than 0 seconds")
    if timeout is not None and timeout <= 0:
      raise ValueError("Cielo timeout must be greater than 0 seconds")

  def _raise_if_stop_requested(self) -> None:
    """Cancel preparation before the run command crosses the hardware boundary."""
    if self._stop_requested.is_set():
      raise Cielo6Error("The Cielo run was stopped before dispatch.")

  @staticmethod
  def _raise_for_firmware_error(status: Cielo6Status, operation: str) -> None:
    """Raise when status contains a firmware-defined error state."""
    if status.work_state.is_error:
      raise Cielo6FirmwareStateError(operation, status)

  @classmethod
  def _require_idle_for_run(cls, status: Cielo6Status) -> None:
    """Require a safe idle state before a run-changing command."""
    cls._raise_for_firmware_error(status, "preparing to start a run")
    if status.work_state is Cielo6WorkState.IDLE:
      return
    if status.is_running or status.is_paused:
      raise Cielo6Error(
        "The Cielo already has an active run. Stop that run before you start another run."
      )
    raise Cielo6Error(
      f"The Cielo is not idle and cannot start a run (work_status={status.work_status})."
    )

  @staticmethod
  def _run_identity(status: Cielo6Status) -> tuple[tuple[int, int], tuple[int, int], str]:
    """Return the firmware fields that identify one run."""
    return status.run_id, status.sample_id, status.file_name

  def _clear_active_run_context(self) -> None:
    """Discard local context after authoritative terminal readback."""
    self._active_program = None
    self._active_run_identity = None
    self._run_phase = _Cielo6RunPhase.NONE

  async def _find_experiment(
    self, workspace: str, protocol: str, result_name: str
  ) -> Cielo6ExperimentInfo:
    """Find one completed result in the firmware summary."""
    for experiment in await self.request_experiment_summary():
      if (
        experiment.workspace == workspace
        and experiment.protocol == protocol
        and experiment.name == result_name
      ):
        return experiment
    raise Cielo6Error(f"Completed run {result_name!r} was not present in the experiment summary")

  async def _request(self, command: int, payload: bytes = b"") -> CieloFrame:
    """Send one command and return its matching response."""
    self._require_setup()
    device_id = self.device_id
    if device_id is None:
      raise Cielo6Error("Cielo device ID is unknown. Call setup() before you send commands.")
    request = CieloFrame(device_id=device_id, command=command, payload=payload)
    async with self._request_lock:
      self._require_setup()
      await self.io.write(request.to_bytes())
      return await self._read_matching_frame(command, device_id)

  async def _mutation_request(self, command: int, payload: bytes) -> None:
    """Send one mutation command and validate its result."""
    self._validate_mutation_result(await self._request(command, payload))

  @staticmethod
  def _validate_mutation_result(response: CieloFrame) -> None:
    """Validate the execution result in a mutation response."""
    if len(response.payload) < 2:
      raise Cielo6Error("Cielo mutation response does not contain an execution result")
    result = int.from_bytes(response.payload[-2:], byteorder="little")
    if result != EXEC_SUCCESSFUL:
      raise Cielo6Error(f"Cielo firmware operation failed with result 0x{result:04x}")

  async def _read_frame(self) -> CieloFrame:
    """Read one frame, recovering from unrelated bytes left in the serial stream."""
    discarded = 0
    while True:
      while len(self._receive_buffer) < FRAME_HEADER_SIZE:
        self._receive_buffer.extend(
          await self._read_exact(FRAME_HEADER_SIZE - len(self._receive_buffer))
        )

      frame_size = FRAME_OVERHEAD + self._receive_buffer[12]
      while len(self._receive_buffer) < frame_size:
        self._receive_buffer.extend(await self._read_exact(frame_size - len(self._receive_buffer)))

      candidate = bytes(self._receive_buffer[:frame_size])
      try:
        frame = CieloFrame.from_bytes(candidate)
      except Cielo6Error:
        del self._receive_buffer[0]
        discarded += 1
        if discarded > FRAME_OVERHEAD + MAX_PAYLOAD_SIZE:
          raise Cielo6Error("Could not find a valid Cielo frame in the received serial data")
        continue

      del self._receive_buffer[:frame_size]
      if discarded:
        logger.warning("[%s] discarded %d byte(s) before a valid frame", self.io.port, discarded)
      return frame

  async def _read_matching_frame(self, command: int, device_id: str) -> CieloFrame:
    """Read until the requested response arrives, retaining unsolicited status."""
    while True:
      response = await self._read_frame()
      if response.device_id != device_id:
        raise Cielo6Error(f"Response device ID {response.device_id!r} does not match {device_id!r}")
      if response.command == command:
        return response
      if response.command == STATUS_QUERY_COMMAND:
        self.latest_status = Cielo6Status.from_payload(response.payload)
        logger.debug("[%s] received unsolicited Cielo status", self.io.port)
        continue
      if response.command == RUNNING_EXPERIMENT_DATA_UPLOAD_COMMAND:
        if len(response.payload) < 5:
          raise Cielo6Error("Cielo running-data payload is too short to contain a data type")
        if response.payload[4] == RUNNING_DATA_TYPE_NORMAL:
          self._running_data.append(Cielo6RunningData.from_payload(response.payload))
        elif response.payload[4] == RUNNING_DATA_TYPE_MELTING:
          self._melting_data.append(Cielo6MeltingData.from_payload(response.payload))
        else:
          raise Cielo6Error(f"Unsupported Cielo running-data type: {response.payload[4]}")
        logger.debug("[%s] retained decoded Cielo running-data frame", self.io.port)
        continue
      if response.command == RUN_COMMAND:
        # The firmware can send this frame after it accepts command 0x0B03.
        # The vendor dispatcher ignores this payload. Status command 0x0B02
        # supplies the authoritative run result.
        logger.debug("[%s] ignored delayed Cielo run response", self.io.port)
        continue
      raise Cielo6Error(
        f"Unexpected Cielo response command while waiting for 0x{command:04x}: "
        f"0x{response.command:04x}"
      )

  async def _read_exact(self, size: int) -> bytes:
    """Read an exact byte count or raise a protocol error."""
    data = bytearray()
    while len(data) < size:
      chunk = await self.io.read(size - len(data))
      if not chunk:
        raise Cielo6Error(f"Incomplete Cielo response: expected {size} bytes, got {len(data)}")
      data.extend(chunk)
    return bytes(data)


__all__ = [
  "Cielo6",
  "Cielo6AmplificationResult",
  "Cielo6CollectionPoint",
  "Cielo6Error",
  "Cielo6ExperimentInfo",
  "Cielo6FirmwareStateError",
  "Cielo6Identity",
  "Cielo6MeltingData",
  "Cielo6MeltingCurveResult",
  "Cielo6MeltRecord",
  "Cielo6ResultFile",
  "Cielo6RunTimeoutError",
  "Cielo6RunState",
  "Cielo6RunningData",
  "Cielo6StoredProgram",
  "Cielo6StoredProgramStep",
  "Cielo6Status",
  "Cielo6ThermalProtocol",
  "Cielo6ThermalStep",
  "Cielo6WorkState",
]
