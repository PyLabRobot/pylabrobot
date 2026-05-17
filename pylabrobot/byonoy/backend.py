import asyncio
import enum
import logging
import threading
import time
from abc import ABCMeta
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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


class Lum96IntegrationMode(enum.Enum):
  RAPID = "rapid"
  SENSITIVE = "sensitive"
  ULTRA_SENSITIVE = "ultra_sensitive"
  CUSTOM = "custom"


# Preset integration times (matches byonoy_device_library: hidmeasurements.cpp)
LUM96_PRESET_S = {
  Lum96IntegrationMode.RAPID: 0.1,
  Lum96IntegrationMode.SENSITIVE: 2.0,
  Lum96IntegrationMode.ULTRA_SENSITIVE: 20.0,
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
class ByonoyApiVersion:
  version_no: int


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


class LedEffect(enum.IntEnum):
  SOLID = 0x00
  PROGRESS = 0x01
  CYLON = 0x02
  RAINBOW = 0x03
  BLINKING = 0x04
  BREATHING = 0x05


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

  def __init__(self, pid: int, device_type: ByonoyDevice) -> None:
    super().__init__()
    self.io = HID(human_readable_device_name="Byonoy Plate Reader", vid=0x16D0, pid=pid)
    self._background_thread: Optional[threading.Thread] = None
    self._stop_background = threading.Event()
    self._ping_interval = 1.0
    self._sending_pings = False
    self._device_type = device_type
    self._abort_requested = False

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    await self.io.setup()
    logger.info("[Byonoy %s pid=0x%04X] connected", self._device_type.name, self.io.pid)
    self._stop_background.clear()
    self._background_thread = threading.Thread(target=self._background_ping_worker, daemon=True)
    self._background_thread.start()

  async def stop(self) -> None:
    self._stop_background.set()
    if self._background_thread and self._background_thread.is_alive():
      self._background_thread.join(timeout=2.0)
    await self.io.stop()
    logger.info("[Byonoy %s pid=0x%04X] disconnected", self._device_type.name, self.io.pid)

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
    await self.io.write(command)
    if not wait_for_response:
      return None

    t0 = time.time()
    while True:
      if time.time() - t0 > 120:
        logger.error(
          "[Byonoy %s pid=0x%04X] timeout waiting for response to command 0x%04X after 120s",
          self._device_type.name,
          self.io.pid,
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

  def _background_ping_worker(self) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
      loop.run_until_complete(self._ping_loop())
    except Exception:
      logger.error("Background ping worker crashed", exc_info=True)
    finally:
      loop.close()

  async def _ping_loop(self) -> None:
    while not self._stop_background.is_set():
      if self._sending_pings:
        payload = Writer().u8(1).finish()
        cmd = self._assemble_command(
          report_id=0x0040,
          payload=payload,
          routing_info=b"\x00\x00",
        )
        await self.io.write(cmd)
      self._stop_background.wait(self._ping_interval)

  def _start_background_pings(self) -> None:
    self._sending_pings = True

  def _stop_background_pings(self) -> None:
    self._sending_pings = False

  async def get_status(self) -> ByonoyStatus:
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

  async def get_environment(self) -> ByonoyEnvironment:
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

  async def get_api_version(self) -> ByonoyApiVersion:
    """Read REP_API_VERSION_IN (0x0050): a single u32."""
    response = await self.send_command(
      report_id=0x0050, payload=b"\x00" * 60, routing_info=b"\x80\x40"
    )
    assert response is not None
    r = Reader(response[2:])
    return ByonoyApiVersion(version_no=r.u32())

  async def get_supported_reports(self) -> List[int]:
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

  async def read_data_field(self, field_index: int) -> object:
    """Read a named device-data field via REP_DEVICE_DATA_READ_IN (0x0200).

    Returns the field's value typed per the response flags
    (str / int / float / bool / bytes). Truncates if HAS_MORE_DATA is set
    (shouldn't happen for the short identity strings; log if it does).
    """
    payload = Writer().u16(field_index).u8(0).raw_bytes(b"\x00" * 57).finish()
    response = await self.send_command(
      report_id=0x0200, payload=payload, routing_info=b"\x80\x40"
    )
    assert response is not None
    r = Reader(response[2:])
    _ = r.u16()  # echoed field_index
    flags = r.u8()
    data_type = flags & _FLAG_TYPE_MASK
    if flags & _FLAG_HAS_MORE_DATA:
      logger.warning(
        "[Byonoy] field 0x%04X has more data than fits in one report; truncating",
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

  async def get_device_info(self) -> ByonoyDeviceInfo:
    """Read identity strings (matches C lib's byonoy_get_device_information)."""

    async def s(idx: int) -> str:
      v = await self.read_data_field(idx)
      return v if isinstance(v, str) else str(v)

    return ByonoyDeviceInfo(
      device_id=await s(_DD_DEVICE_ID),
      device_name=await s(_DD_DEVICE_NAME),
      manufacturer=await s(_DD_DEVICE_MANUFACTURER),
      serial_no=await s(_DD_SERIAL_NO),
      firmware_version=await s(_DD_FIRMWARE_VERSION),
      ref_number=await s(_DD_REF_NUMBER),
    )

  async def cancel(self, report_id: int = 0x0340) -> None:
    """Abort an in-progress measurement via REP_ABORT_REPORT_OUT (0x0060).

    Empirically the firmware stops emitting result chunks but does not send
    any closing notification, so we also raise an `_abort_requested` flag
    that subclasses' read loops poll to bail out instead of waiting 120 s
    for the hard timeout.

    `report_id` is the trigger report whose execution should be aborted.
    Defaults to the lum96 trigger (0x0340).
    """
    self._abort_requested = True
    payload = Writer().u16(report_id).raw_bytes(b"\x00" * 58).finish()
    await self.send_command(report_id=0x0060, payload=payload, wait_for_response=False)
    logger.info("[Byonoy] sent abort for report 0x%04X", report_id)

  async def set_led_color(
    self,
    color: Tuple[int, int, int],
    effect: LedEffect = LedEffect.SOLID,
    *,
    low_power: bool = False,
    force: bool = False,
    effect_state: int = 0,
    duration_ms: int = 0,
  ) -> None:
    """Set the LED bar to a single color via REP_LED_BAR_EFFECTS_OUT (0x0351).

    Mirrors the vendor's user-facing `set_led_effect(effect, color, modes, ...)`
    in byonoy_device_library. The firmware renders `effect` over `color`:
    SOLID just shows the color; BREATHING/CYLON/BLINKING/RAINBOW/PROGRESS
    animate it.

    Packed layout (vendor byonoyusbhid.h led_bar_effects_out_t):
      effect:u8  color:(r,g,b u8)  effect_state:u8  flags:u8  duration_ms:u32

    `force` (FLAG_LED_FORCE=0x10) overrides an unexpired previous
    `duration_ms`. `low_power` (FLAG_LED_LOWPOWER=0x01) reduces brightness.

    The PC routing tag (request_info=0x4000) is required — the firmware
    silently drops LED writes that arrive with the default LEGACY tag.
    """
    flags = (0x01 if low_power else 0) | (0x10 if force else 0)
    r_, g, b = color
    payload = (
      Writer()
      .u8(int(effect))
      .u8(r_ & 0xFF).u8(g & 0xFF).u8(b & 0xFF)
      .u8(effect_state & 0xFF)
      .u8(flags)
      .u32(int(duration_ms))
      .finish()
    )
    await self.send_command(
      report_id=0x0351, payload=payload, wait_for_response=False,
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
      report_id=0x0350, payload=w.finish(), wait_for_response=False,
      routing_info=b"\x00\x40",
    )

  async def get_versions(self) -> ByonoyVersions:
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
