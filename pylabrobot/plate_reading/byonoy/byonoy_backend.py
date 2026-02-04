import abc
import asyncio
import enum
import threading
import time
from typing import Dict, List, Optional

from pylabrobot.io.binary import Reader, Writer
from pylabrobot.io.hid import HID
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate, Well
from pylabrobot.utils.list import reshape_2d


class _ByonoyDevice(enum.Enum):
  ABSORBANCE_96 = enum.auto()
  LUMINESCENCE_96 = enum.auto()


class _ByonoyBase(PlateReaderBackend, metaclass=abc.ABCMeta):
  """Base backend for Byonoy plate readers using HID communication.
  Provides common functionality for different Byonoy machine types.
  """

  def __init__(self, pid: int, device_type: _ByonoyDevice) -> None:
    self.io = HID(vid=0x16D0, pid=pid)
    self._background_thread: Optional[threading.Thread] = None
    self._stop_background = threading.Event()
    self._ping_interval = 1.0  # Send ping every second
    self._sending_pings = False  # Whether to actively send pings
    self._device_type = device_type

  async def setup(self) -> None:
    """Set up the plate reader. This should be called before any other methods."""

    await self.io.setup()

    # Start background keep alive messages
    self._stop_background.clear()
    self._background_thread = threading.Thread(target=self._background_ping_worker, daemon=True)
    self._background_thread.start()

  async def stop(self) -> None:
    """Close all connections to the plate reader and make sure setup() can be called again."""

    # Stop background keep alive messages
    self._stop_background.set()
    if self._background_thread and self._background_thread.is_alive():
      self._background_thread.join(timeout=2.0)

    await self.io.stop()

  def _assemble_command(self, report_id: int, payload: bytes, routing_info: bytes) -> bytes:
    packet = Writer().u16(report_id).raw_bytes(payload).finish()
    packet += b"\x00" * (62 - len(packet)) + routing_info  # pad to 64 bytes
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
      if time.time() - t0 > 120:  # read for 2 minutes max. typical is 1m5s.
        raise TimeoutError("Reading luminescence data timed out after 2 minutes.")

      response = await self.io.read(64, timeout=30)
      if len(response) == 0:
        continue

      # if the first 2 bytes do not match, we continue reading
      response_report_id = Reader(response).u16()
      if report_id == response_report_id:
        break
    return response

  def _background_ping_worker(self) -> None:
    """Background worker that sends periodic ping commands."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
      loop.run_until_complete(self._ping_loop())
    finally:
      loop.close()

  async def _ping_loop(self) -> None:
    """Main ping loop that runs in the background thread."""
    while not self._stop_background.is_set():
      if self._sending_pings:
        # don't read in background thread, data might get lost here. don't use send_command
        payload = Writer().u8(1).finish()
        cmd = self._assemble_command(
          report_id=0x0040,  # command id: HEARTBEAT_IN
          payload=payload,
          routing_info=b"\x00\x00",
        )
        await self.io.write(cmd)

      self._stop_background.wait(self._ping_interval)

  def _start_background_pings(self) -> None:
    self._sending_pings = True

  def _stop_background_pings(self) -> None:
    self._sending_pings = False

  async def open(self) -> None:
    raise NotImplementedError(
      "byonoy cannot open by itself. you need to move the top module using a robot arm."
    )

  async def close(self, plate: Optional[Plate]) -> None:
    raise NotImplementedError(
      "byonoy cannot close by itself. you need to move the top module using a robot arm."
    )


class ByonoyAbsorbance96AutomateBackend(_ByonoyBase):
  def __init__(self) -> None:
    super().__init__(pid=0x1199, device_type=_ByonoyDevice.ABSORBANCE_96)

  async def setup(self, verbose: bool = False, **backend_kwargs):
    """Set up the plate reader. This should be called before any other methods."""

    # Call the base setup (opens HID)
    await super().setup(**backend_kwargs)

    # After device is online, run reference initialisation
    await self.initialize_measurements()

    self.available_wavelengths = await self.get_available_absorbance_wavelengths()

  async def get_available_absorbance_wavelengths(self) -> List[float]:
    response = await self.send_command(
      report_id=0x0330,
      payload=b"\x00" * 60,  # 30 x i16
      wait_for_response=True,
      routing_info=b"\x80\x40",
    )
    assert response is not None, "Failed to get available wavelengths."

    # Skip the first 2 bytes (report_id), then read 30 signed 16-bit integers
    reader = Reader(response[2:])
    available_wavelengths = [reader.i16() for _ in range(30)]
    return [w for w in available_wavelengths if w != 0]

  async def _run_abs_measurement(self, signal_wl: int, reference_wl: int, is_reference: bool):
    """Perform an absorbance measurement or reference measurement.
    This contains all shared logic between initialization and real measurements."""

    # (1) SUPPORTED_REPORTS_IN (0x0010)
    await self.send_command(
      report_id=0x0010,
      payload=b"\x00" * 60,  # seq, seq_len, ids[29]
      wait_for_response=False,
    )

    # (2) DEVICE_DATA_READ_IN (0x0200)
    payload2 = (
      Writer()
      .u16(7)  # field_index
      .u8(0)  # flags
      .raw_bytes(b"\x00" * 52)  # data
      .finish()
    )
    await self.send_command(
      report_id=0x0200,
      payload=payload2,
      wait_for_response=False,
    )

    # (3) ABS_TRIGGER_MEASUREMENT_OUT (0x0320)
    payload3 = (
      Writer()
      .i16(signal_wl)
      .i16(reference_wl)
      .u8(int(is_reference))
      .u8(0)  # flags
      .finish()
    )
    await self.send_command(
      report_id=0x0320,
      payload=payload3,
      wait_for_response=False,
      routing_info=b"\x00\x40",
    )

    # (4) Collect chunks (report_id 0x0500)
    rows: List[float] = []
    t0 = time.time()

    while True:
      if time.time() - t0 > 120:
        raise TimeoutError("Measurement timeout.")

      chunk = await self.io.read(64, timeout=30)
      if len(chunk) == 0:
        continue

      reader = Reader(chunk)
      report_id = reader.u16()

      # Only handle the measurement packets
      if report_id == 0x0500:
        seq = reader.u8()
        seq_len = reader.u8()
        _ = reader.i16()  # signal_wl_nm
        _ = reader.i16()  # reference_wl_nm
        _ = reader.u32()  # duration_ms
        row = [reader.f32() for _ in range(12)]
        _ = reader.u8()  # flags
        _ = reader.u8()  # progress

        rows.extend(row)

        if seq == seq_len - 1:
          break

    return rows

  async def initialize_measurements(self):
    """Perform the reference ABS measurement required by the firmware."""

    # Standard reference wavelength used by Byonoy app
    # required startup protocol to initialize the photodiode reference
    REFERENCE_WL = 0
    SIGNAL_WL = 660

    await self._run_abs_measurement(
      signal_wl=SIGNAL_WL,
      reference_wl=REFERENCE_WL,
      is_reference=True,
    )

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
  ) -> List[dict]:
    """
    Measure sample absorbance in each well at the specified wavelength.

    Args:
      wavelength: Signal wavelength in nanometers.
      plate: The plate being read. Included for API uniformity.
      wells: Subset of wells to return. If omitted, all 96 wells are returned.
    """

    assert (
      wavelength in self.available_wavelengths
    ), f"Wavelength {wavelength} nm not in available wavelengths {self.available_wavelengths}."

    rows = await self._run_abs_measurement(
      signal_wl=wavelength,
      reference_wl=0,
      is_reference=False,
    )

    matrix = reshape_2d(rows, (8, 12))

    # dictionary output for filtered wells
    return [
      {
        "wavelength": wavelength,
        "time": time.time(),
        "temperature": None,
        "data": matrix,
      }
    ]

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float
  ) -> List[dict]:
    raise NotImplementedError("Absorbance plate reader does not support luminescence reading.")

  async def read_fluorescence(
    self,
    plate: Plate,
    wells,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[dict]:
    raise NotImplementedError("Absorbance plate reader does not support fluorescence reading.")


class ByonoyLuminescence96AutomateBackend(_ByonoyBase):
  def __init__(self) -> None:
    super().__init__(pid=0x119B, device_type=_ByonoyDevice.LUMINESCENCE_96)

  async def read_absorbance(self, plate, wells, wavelength) -> List[Dict]:
    raise NotImplementedError(
      "Luminescence plate reader does not support absorbance reading. Use ByonoyAbsorbance96Automate instead."
    )

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float, integration_time: float = 2
  ) -> List[Dict]:
    """integration_time: in seconds, default 2 s"""

    # SUPPORTED_REPORTS_IN (0x0010)
    await self.send_command(
      report_id=0x0010,
      payload=b"\x00" * 60,  # seq, seq_len, ids[29]
      wait_for_response=False,
    )

    # DEVICE_DATA_READ_IN (0x0200)
    payload2 = (
      Writer()
      .u16(7)  # field_index
      .u8(0)  # flags
      .raw_bytes(b"\x00" * 52)  # data
      .finish()
    )
    await self.send_command(
      report_id=0x0200,
      payload=payload2,
      wait_for_response=False,
    )

    # LUM_TRIGGER_MEASUREMENT_OUT (0x0340)
    payload3 = (
      Writer()
      .i32(int(integration_time * 1000 * 1000))  # integration_time_us
      .raw_bytes(b"\xff" * 12)  # channels_selected
      .u8(0)  # is_reference_measurement
      .u8(0)  # flags
      .finish()
    )
    await self.send_command(
      report_id=0x0340,
      payload=payload3,
      wait_for_response=False,
    )

    t0 = time.time()
    all_rows: List[float] = []

    while True:
      if time.time() - t0 > 120:  # read for 2 minutes max. typical is 1m5s.
        raise TimeoutError("Reading luminescence data timed out after 2 minutes.")

      chunk = await self.io.read(64, timeout=30)
      if len(chunk) == 0:
        continue

      reader = Reader(chunk)
      report_id = reader.u16()

      if report_id == 0x0600:  # REP_LUM96_MEASUREMENT_IN
        seq = reader.u8()
        seq_len = reader.u8()
        _ = reader.u32()  # integration_time_us
        _ = reader.u32()  # duration_ms
        row = [reader.f32() for _ in range(12)]
        _ = reader.u8()  # flags
        _ = reader.u8()  # progress

        all_rows.extend(row)

        if seq == seq_len - 1:
          break

    hybrid_result = all_rows[96 * 0 : 96 * 1]
    _ = all_rows[96 * 1 : 96 * 2]  # counting_result
    _ = all_rows[96 * 2 : 96 * 3]  # sampling_result
    _ = all_rows[96 * 3 : 96 * 4]  # micro_counting_result
    _ = all_rows[96 * 4 : 96 * 5]  # micro_integration_result
    _ = all_rows[96 * 5 : 96 * 6]  # repetition_count
    _ = all_rows[96 * 6 : 96 * 7]  # integration_times
    _ = all_rows[96 * 7 : 96 * 8]  # below_breakdown_measurement

    return [
      {
        "time": time.time(),
        "temperature": None,
        "data": reshape_2d(hybrid_result, (8, 12)),
      }
    ]

  async def read_fluorescence(
    self,
    plate: Plate,
    wells,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[Dict]:
    raise NotImplementedError("Fluorescence plate reader does not support fluorescence reading.")
