import asyncio
import contextlib
import enum
import logging
import time
from abc import ABCMeta
from dataclasses import dataclass
from typing import Dict, Iterator, List, Literal, Optional, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.io.binary import Reader, Writer
from pylabrobot.io.hid import HID

logger = logging.getLogger(__name__)


class ByonoyDevice(enum.Enum):
  ABSORBANCE_96 = enum.auto()
  LUMINESCENCE_96 = enum.auto()


class ByonoySlotState(enum.IntEnum):
  UNKNOWN = 0
  EMPTY = 1
  OCCUPIED = 2
  UNDETERMINED = 3


Lum96IntegrationMode = Literal["rapid", "sensitive", "ultra_sensitive", "custom"]


# Preset integration times (matches byonoy_device_library: hidmeasurements.cpp)
LUM96_PRESET_S: Dict[Lum96IntegrationMode, float] = {
  "rapid": 0.1,
  "sensitive": 2.0,
  "ultra_sensitive": 20.0,
}


def encode_well_bitmask(selected: List[bool], n: int = 96) -> bytes:
  """Pack a length-n bool list into a little-endian bitmask, LSB-first within each byte."""
  if len(selected) != n:
    raise ValueError(f"expected {n} bools, got {len(selected)}")
  nbytes = (n + 7) // 8
  out = bytearray(nbytes)
  for i, b in enumerate(selected):
    if b:
      out[i // 8] |= 1 << (i % 8)
  return bytes(out)


@dataclass
class ByonoyStatus:
  is_initialized: bool
  slot_state: ByonoySlotState
  error_code: int
  uptime_s: int
  is_measuring: bool
  boot_completed: bool


@dataclass
class ByonoyEnvironment:
  temperature_c: float
  humidity: float  # 0..1
  acceleration_g: Tuple[float, float, float]


@dataclass
class ByonoyVersions:
  system_version: int
  stm_version: int
  stm_dev_version: int
  esp_version: int
  esp_dev_version: int
  stm_bootloader_version: int

  @property
  def system_version_known(self) -> bool:
    return self.system_version != 0

  @property
  def is_production(self) -> bool:
    return self.stm_dev_version == 0 and self.esp_dev_version == 0


@dataclass
class ByonoyDeviceInfo:
  device_id: str
  device_name: str
  manufacturer: str
  serial_no: str
  firmware_version: str
  ref_number: str


# device_data_field_id (byonoyusbhid.h)
_DD_DEVICE_ID = 0
_DD_DEVICE_NAME = 1
_DD_DEVICE_MANUFACTURER = 2
_DD_SERIAL_NO = 3
_DD_FIRMWARE_VERSION = 4
_DD_REF_NUMBER = 8

# device_data_field_flags (byonoyusbhid.h)
_FLAG_TYPE_MASK = 0x0F
_FLAG_TYPE_STRING = 0x02
_FLAG_TYPE_INTEGER = 0x01
_FLAG_TYPE_FLOAT = 0x04
_FLAG_TYPE_BOOLEAN = 0x03
_FLAG_HAS_MORE_DATA = 0x10


LedEffect = Literal["solid", "progress", "cylon", "rainbow", "blinking", "breathing"]

_LED_EFFECT_CODES: Dict[LedEffect, int] = {
  "solid": 0x00,
  "progress": 0x01,
  "cylon": 0x02,
  "rainbow": 0x03,
  "blinking": 0x04,
  "breathing": 0x05,
}


# --- Firmware error codes (per Byonoy hid-reports source) -------------------
#
# The status_in_t.error_code byte is device-specific. Byonoy's own C library
# defines a Status base class that just stringifies the hex code, with per-
# device subclasses (Abs96Status, Abs1Status) providing named tables. There
# is no documented Lum96 table — Lum96 inherits the generic stringifier.
#
# These mirror the enums in:
#   hid-reports/src/hid/report/request/abs96status.cpp
#   hid-reports/src/hid/report/request/abs1status.cpp


class Abs96StatusError(enum.IntEnum):
  NO_ERROR = 0
  ERROR_CALIB = 1
  ERROR_AMBIENT = 2
  ERROR_USB = 3
  ERROR_HARDWARE = 4
  ERROR_TEMPERATURE = 5
  ERROR_NO_MEASUREMENTUNIT = 6
  ERROR_NO_ACK = 10


class Abs1StatusError(enum.IntFlag):
  """AbsOne errors are a bit-flag set — multiple can be raised at once."""

  NO_ERROR = 0
  AMBIENT_LIGHT = 1
  MIN_LIGHT = 2
  USB = 4
  HARDWARE = 8
  EEPROM = 16
  TIMEOUT = 32
  POWER_CALIBRATION = 64
  NOISE_LIMIT = 128


_GENERIC_ERROR_NAMES: Dict[int, str] = {0: "NO_ERROR"}
ABS96_ERROR_NAMES: Dict[int, str] = {e.value: e.name for e in Abs96StatusError}
ABS1_ERROR_NAMES: Dict[int, str] = {e.value: e.name for e in Abs1StatusError}


_ACCEL_LSB_PER_G = 16384.0  # 14-bit signed @ ±2 g full scale


class ByonoyDriver(Driver, metaclass=ABCMeta):
  """Shared HID communication logic for Byonoy plate readers."""

  # Firmware error-code → name mapping. Default mirrors Byonoy's generic
  # Status::firmwareErrorId (only NO_ERROR is documented). Subclasses for
  # specific devices (e.g. ByonoyAbsorbance96Backend) override with their
  # documented tables. Lum96 has no documented table; inherits the default.
  _ERROR_NAMES: Dict[int, str] = _GENERIC_ERROR_NAMES

  def __init__(self, pid: int, device_type: ByonoyDevice, name: str) -> None:
    super().__init__()
    self.io = HID(human_readable_device_name=name, vid=0x16D0, pid=pid)
    self._device_type = device_type
    self._abort_requested = False
    self._in_flight_trigger: Optional[int] = None
    # Serializes write+response-sniff in send_command. Does NOT cover the
    # measurement read loops in subclasses (which poll io.read directly so
    # cancel() can still send the abort while they run).
    self._io_lock = asyncio.Lock()

  @property
  def name(self) -> str:
    return self.io.human_readable_device_name

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    await self.io.setup()
    logger.info("[%s] connected", self.name)

  async def stop(self) -> None:
    if self._in_flight_trigger is not None:
      await self.cancel()
    await self.io.stop()
    logger.info("[%s] disconnected", self.name)

  def _assemble_command(self, report_id: int, payload: bytes, routing_info: bytes) -> bytes:
    packet = Writer().u16(report_id).raw_bytes(payload).finish()
    packet += b"\x00" * (62 - len(packet)) + routing_info
    return packet

  async def send_command(
    self,
    report_id: int,
    payload: bytes,
    wait_for_response: bool = True,
    routing_info: bytes = b"\x00\x00",
  ) -> Optional[bytes]:
    command = self._assemble_command(report_id, payload=payload, routing_info=routing_info)
    async with self._io_lock:
      await self.io.write(command)
      if not wait_for_response:
        return None

      t0 = time.time()
      while True:
        if time.time() - t0 > 120:
          logger.error(
            "[%s] timeout waiting for response to command 0x%04X after 120s",
            self.name,
            report_id,
          )
          raise TimeoutError("Reading data timed out after 2 minutes.")
        response = await self.io.read(64, timeout=30)
        if len(response) == 0:
          continue
        response_report_id = Reader(response).u16()
        if report_id == response_report_id:
          break
      return response

  async def request_status(self) -> ByonoyStatus:
    """Read REP_STATUS_IN (0x0300): init/slot/error/uptime/measuring/boot."""
    response = await self.send_command(
      report_id=0x0300, payload=b"\x00" * 60, routing_info=b"\x80\x40"
    )
    assert response is not None
    r = Reader(response[2:])
    return ByonoyStatus(
      is_initialized=r.u8() != 0,
      slot_state=ByonoySlotState(r.u8()),
      error_code=r.u8(),
      uptime_s=r.u32(),
      is_measuring=r.u8() != 0,
      boot_completed=r.u8() != 0,
    )

  def _warn_chunk_flags(self, chunk_flags: List[int]) -> None:
    """Log non-zero per-chunk flag bytes from a measurement read loop.

    Vendor bit definitions for the measurement-result `flags` byte aren't
    published, so we can't decode them — only surface that *something* was
    flagged. Subclasses' read loops call this after the loop completes
    (after error_code has been checked and didn't raise).
    """
    if any(f != 0 for f in chunk_flags):
      logger.warning(
        "[%s] non-zero chunk flags during read: %s "
        "(vendor bit definitions not published; data may be unreliable)",
        self.name,
        [f"0x{f:02x}" for f in chunk_flags],
      )

  def describe_error_code(self, code: int) -> str:
    """Return a human-readable name for a firmware error_code byte.

    Looks up `code` in this backend's `_ERROR_NAMES` table. Unknown codes
    fall back to `"errorCode=0xNN"` matching the C library's generic
    Status::firmwareErrorId. The default table only has NO_ERROR (0);
    subclasses for documented devices (Abs96, AbsOne) populate richer
    tables. Lum96 has no documented table — codes other than 0 will
    surface as the hex sentinel, which is the honest answer.
    """
    if code in self._ERROR_NAMES:
      return self._ERROR_NAMES[code]
    return f"errorCode=0x{code:02x}"

  async def request_environment(self) -> ByonoyEnvironment:
    """Read REP_ENVIRONMENT_IN (0x0310): temperature, humidity, acceleration."""
    response = await self.send_command(
      report_id=0x0310, payload=b"\x00" * 60, routing_info=b"\x80\x40"
    )
    assert response is not None
    r = Reader(response[2:])
    temp_c = r.i16() / 100.0
    humidity = r.i16() / 1000.0
    ax, ay, az = r.i16(), r.i16(), r.i16()
    return ByonoyEnvironment(
      temperature_c=temp_c,
      humidity=humidity,
      acceleration_g=(ax / _ACCEL_LSB_PER_G, ay / _ACCEL_LSB_PER_G, az / _ACCEL_LSB_PER_G),
    )

  async def request_api_version(self) -> int:
    """Read REP_API_VERSION_IN (0x0050): a single u32."""
    response = await self.send_command(
      report_id=0x0050, payload=b"\x00" * 60, routing_info=b"\x80\x40"
    )
    assert response is not None
    return Reader(response[2:]).u32()

  async def request_supported_reports(self) -> List[int]:
    """Read REP_SUPPORTED_REPORTS_IN (0x0010): list of report IDs the device supports.

    Reply is delivered in seq/seq_len chunks of up to 29 u16 ids; zero-valued
    entries are padding. Returns the deduplicated, ordered union.
    """
    cmd = self._assemble_command(report_id=0x0010, payload=b"\x00" * 60, routing_info=b"\x80\x40")
    await self.io.write(cmd)

    seen: List[int] = []
    t0 = time.time()
    while True:
      if time.time() - t0 > 30:
        raise TimeoutError("Timed out reading supported reports.")
      chunk = await self.io.read(64, timeout=10)
      if len(chunk) == 0:
        continue
      r = Reader(chunk)
      if r.u16() != 0x0010:
        continue
      seq = r.u8()
      seq_len = r.u8()
      ids = [r.u16() for _ in range(29)]
      seen.extend(i for i in ids if i != 0)
      if seq == seq_len - 1:
        break
    # Preserve order, drop dupes
    out: List[int] = []
    for i in seen:
      if i not in out:
        out.append(i)
    return out

  async def _read_data_field(self, field_index: int) -> object:
    """Read a named device-data field via REP_DEVICE_DATA_READ_IN (0x0200).

    Returns the field's value typed per the response flags
    (str / int / float / bool / bytes). Truncates if HAS_MORE_DATA is set
    (shouldn't happen for the short identity strings; log if it does).

    Private — the only documented caller is `request_device_info`, which knows
    the field types ahead of time. Promote to public if you find a use case
    that needs the polymorphic-by-flag-byte shape.
    """
    payload = Writer().u16(field_index).u8(0).raw_bytes(b"\x00" * 57).finish()
    response = await self.send_command(report_id=0x0200, payload=payload, routing_info=b"\x80\x40")
    assert response is not None
    r = Reader(response[2:])
    _ = r.u16()  # echoed field_index
    flags = r.u8()
    data_type = flags & _FLAG_TYPE_MASK
    if flags & _FLAG_HAS_MORE_DATA:
      logger.warning(
        "[%s] field 0x%04X has more data than fits in one report; truncating",
        self.name,
        field_index,
      )
    raw = r.raw_bytes(52)
    if data_type == _FLAG_TYPE_STRING:
      return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    if data_type == _FLAG_TYPE_INTEGER:
      return int.from_bytes(raw[:4], "little", signed=False)
    if data_type == _FLAG_TYPE_FLOAT:
      return Reader(raw[:4]).f32()
    if data_type == _FLAG_TYPE_BOOLEAN:
      return raw[0] != 0
    return raw  # TypeBytes

  async def request_device_info(self) -> ByonoyDeviceInfo:
    """Read identity strings (matches C lib's byonoy_get_device_information)."""

    async def s(idx: int) -> str:
      v = await self._read_data_field(idx)
      return v if isinstance(v, str) else str(v)

    return ByonoyDeviceInfo(
      device_id=await s(_DD_DEVICE_ID),
      device_name=await s(_DD_DEVICE_NAME),
      manufacturer=await s(_DD_DEVICE_MANUFACTURER),
      serial_no=await s(_DD_SERIAL_NO),
      firmware_version=await s(_DD_FIRMWARE_VERSION),
      ref_number=await s(_DD_REF_NUMBER),
    )

  @contextlib.contextmanager
  def _measurement_in_flight(self, report_id: int) -> Iterator[None]:
    """Mark `report_id` as the in-flight measurement trigger for the duration
    of the `with` block. Subclasses' read methods wrap their trigger + result
    loop in this so `cancel()` can find the right report to abort and so a
    concurrent second read raises instead of corrupting the read buffer.
    """
    if self._in_flight_trigger is not None:
      raise RuntimeError(
        f"Byonoy device busy: report 0x{self._in_flight_trigger:04X} already in "
        f"flight; call cancel() before starting 0x{report_id:04X}."
      )
    # Entry-side reset is load-bearing for correctness; exit-side is hygiene
    # so a between-reads inspection doesn't see stale True from a prior cancel.
    self._in_flight_trigger = report_id
    self._abort_requested = False
    try:
      yield
    finally:
      self._in_flight_trigger = None
      self._abort_requested = False

  async def cancel(self) -> None:
    """Abort the in-flight measurement via REP_ABORT_REPORT_OUT (0x0060).

    Uses the report id tracked by the read method's `_measurement_in_flight`
    context. If no measurement is in flight, this is a no-op.

    Empirically the firmware stops emitting result chunks but sends no closing
    notification, so we also raise `_abort_requested`; subclasses' read loops
    poll the flag and bail out instead of waiting 120 s for the hard timeout.
    """
    report_id = self._in_flight_trigger
    if report_id is None:
      logger.info("[%s] cancel(): no measurement in flight; no-op", self.name)
      return
    self._abort_requested = True
    payload = Writer().u16(report_id).raw_bytes(b"\x00" * 58).finish()
    await self.send_command(report_id=0x0060, payload=payload, wait_for_response=False)
    logger.info("[%s] sent abort for in-flight report 0x%04X", self.name, report_id)

  async def set_led_color(
    self,
    color: Tuple[int, int, int],
    effect: LedEffect = "solid",
    *,
    low_power: bool = False,
    force: bool = False,
    effect_state: int = 0,
    duration_ms: int = 0,
  ) -> None:
    """Set the LED bar to a single color via REP_LED_BAR_EFFECTS_OUT (0x0351).

    Mirrors the vendor's user-facing `set_led_effect(effect, color, modes, ...)`
    in byonoy_device_library. The firmware renders `effect` over `color`:
    "solid" just shows the color; "breathing"/"cylon"/"blinking"/"rainbow"/
    "progress" animate it.

    Packed layout (vendor byonoyusbhid.h led_bar_effects_out_t):
      effect:u8  color:(r,g,b u8)  effect_state:u8  flags:u8  duration_ms:u32

    `force` (FLAG_LED_FORCE=0x10) overrides an unexpired previous
    `duration_ms`. `low_power` (FLAG_LED_LOWPOWER=0x01) reduces brightness.

    `effect_state` is a 0..255 parameter used only by the "progress" effect
    to indicate fill level (0 = empty bar, 255 = full bar). It is ignored
    by every other effect ("solid", "breathing", "blinking", "cylon",
    "rainbow"); leave it at 0 unless you're driving progress.

    The PC routing tag (request_info=0x4000) is required — the firmware
    silently drops LED writes that arrive with the default LEGACY tag.
    """
    flags = (0x01 if low_power else 0) | (0x10 if force else 0)
    r_, g, b = color
    payload = (
      Writer()
      .u8(_LED_EFFECT_CODES[effect])
      .u8(r_ & 0xFF)
      .u8(g & 0xFF)
      .u8(b & 0xFF)
      .u8(effect_state & 0xFF)
      .u8(flags)
      .u32(int(duration_ms))
      .finish()
    )
    await self.send_command(
      report_id=0x0351,
      payload=payload,
      wait_for_response=False,
      routing_info=b"\x00\x40",
    )

  async def set_led_colors(self, colors: List[Tuple[int, int, int]]) -> None:
    """Set each of the 20 LED bar pixels individually via
    REP_LED_BAR_COLOURS_OUT (0x0350). Pads with black if fewer than 20 are
    given; truncates if more. Fast enough for real-time animation (~30+ fps).

    Like `set_led_color`, requires the PC routing tag (request_info=0x4000);
    the firmware silently drops writes with the default LEGACY tag.
    """
    pixels = list(colors[:20]) + [(0, 0, 0)] * max(0, 20 - len(colors))
    w = Writer()
    for r_, g, b in pixels:
      w.u8(r_ & 0xFF).u8(g & 0xFF).u8(b & 0xFF)
    await self.send_command(
      report_id=0x0350,
      payload=w.finish(),
      wait_for_response=False,
      routing_info=b"\x00\x40",
    )

  async def request_versions(self) -> ByonoyVersions:
    """Read REP_VERSIONS_IN (0x0080): system / STM / ESP / bootloader versions."""
    response = await self.send_command(
      report_id=0x0080, payload=b"\x00" * 60, routing_info=b"\x80\x40"
    )
    assert response is not None
    r = Reader(response[2:])
    return ByonoyVersions(
      system_version=r.u32(),
      stm_version=r.u32(),
      stm_dev_version=r.u32(),
      esp_version=r.u32(),
      esp_dev_version=r.u32(),
      stm_bootloader_version=r.u32(),
    )
