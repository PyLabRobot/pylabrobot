import asyncio
import enum
import logging
import threading
import time
from abc import ABCMeta
from dataclasses import dataclass
from typing import List, Optional, Tuple

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


_ACCEL_LSB_PER_G = 16384.0  # 14-bit signed @ ±2 g full scale


class ByonoyBase(Driver, metaclass=ABCMeta):
  """Shared HID communication logic for Byonoy plate readers."""

  def __init__(self, pid: int, device_type: ByonoyDevice) -> None:
    super().__init__()
    self.io = HID(human_readable_device_name="Byonoy Plate Reader", vid=0x16D0, pid=pid)
    self._background_thread: Optional[threading.Thread] = None
    self._stop_background = threading.Event()
    self._ping_interval = 1.0
    self._sending_pings = False
    self._device_type = device_type

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
