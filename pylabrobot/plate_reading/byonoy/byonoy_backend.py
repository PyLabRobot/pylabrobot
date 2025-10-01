import abc
import asyncio
import enum
import struct
import threading
import time
from typing import List, Optional

from pylabrobot.io.hid import HID
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.utils.list import reshape_2d


class _ByonoyDevice(enum.Enum):
  ABSORBANCE_96 = enum.auto()
  LUMINESCENCE_96 = enum.auto()


class _ByonoyBase(PlateReaderBackend, metaclass=abc.ABCMeta):
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

  def _assemble_command(self, report_id: int, payload_fmt: str, payload: list) -> bytes:
    # based on `encode_hid_report` function

    # Encode the payload
    binary_payload = struct.pack(payload_fmt, *payload)

    # Encode the full report (header + payload)
    header_fmt = "<H"
    binary_header = struct.pack(header_fmt, report_id)
    packet = binary_header + binary_payload
    routing_info = b"\x00\x00"
    packet += b"\x00" * (62 - len(packet)) + routing_info  # pad to 64 bytes

    return packet  # first zero byte is HID report ID (different from byonoy report_id)

  async def send_command(
    self, report_id: int, payload_fmt: str, payload: list, wait_for_response: bool = True
  ) -> Optional[bytes]:
    command = self._assemble_command(report_id, payload_fmt=payload_fmt, payload=payload)

    await self.io.write(command)
    if not wait_for_response:
      return None

    response = b""

    t0 = time.time()
    while True:
      if time.time() - t0 > 120:  # read for 2 minutes max. typical is 1m5s.
        raise TimeoutError("Reading luminescence data timed out after 2 minutes.")

      response = await self.io.read(64, timeout=30)
      if len(response) == 0:
        continue

      # if the first 2 bytes do not match, we continue reading
      response_report_id, *_ = struct.unpack("<H", response[:2])
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
        # don't read in background thread, data might get lost here
        # not needed?
        pass

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

  async def read_luminescence(self, plate: Plate, focal_height: float) -> List[List[float]]:
    raise NotImplementedError("Absorbance plate reader does not support luminescence reading.")

  async def get_available_absorbance_wavelengths(self) -> List[float]:
    available_wavelengths_r = await self.send_command(
      report_id=0x0330,
      payload_fmt="<30h",
      payload=[0] * 30,
      wait_for_response=True,
    )
    assert available_wavelengths_r is not None, "Failed to get available wavelengths."
    # cut out the first 2 bytes, then read the next 2 bytes as an integer
    # 64 - 4 = 60. 60/2 = 30 16 bit integers
    available_wavelengths = list(struct.unpack("<30h", available_wavelengths_r[2:62]))
    available_wavelengths = [w for w in available_wavelengths if w != 0]
    return available_wavelengths

  async def read_absorbance(self, plate: Plate, wavelength: int) -> List[List[float]]:
    """Read the absorbance from the plate reader. This should return a list of lists, where the
    outer list is the columns of the plate and the inner list is the rows of the plate."""

    available_wavelengths = await self.get_available_absorbance_wavelengths()
    if wavelength not in available_wavelengths:
      raise ValueError(
        f"Wavelength {wavelength} nm is not supported by this plate reader. "
        f"Available wavelengths: {available_wavelengths}"
      )

    await self.send_command(
      report_id=0x0010,  # SUPPORTED_REPORTS_IN
      payload_fmt="<BB29H",
      # "seq", "seq_len", "ids"
      payload=[0, 0, *([0] * 29)],
      wait_for_response=False,
    )

    await self.send_command(
      report_id=0x0200,  # DEVICE_DATA_READ_IN
      payload_fmt="<HB52s",
      # field_index", "flags", "data"
      payload=[7, 0, b"\x00" * 52],
      wait_for_response=False,
    )

    await self.send_command(
      report_id=0x320,  # ABS_TRIGGER_MEASUREMENT_OUT
      # signal_wavelength_nm, reference_wavelength_nm, is_reference_measurement, flags
      payload_fmt="<hhBB",
      payload=[wavelength, 0, 0, 0],  # 0, 1, 0
      wait_for_response=False,
    )
    self._stop_background_pings()

    t0 = time.time()

    all_rows = []
    while True:
      if time.time() - t0 > 120:  # read for 2 minutes max. typical is 1m5s.
        raise TimeoutError("Reading luminescence data timed out after 2 minutes.")

      chunk = await self.io.read(64, timeout=30)
      if len(chunk) == 0:
        continue

      report_id, *_ = struct.unpack("<H", chunk[:2])

      if report_id == 0x0500:  # REP_LUM96_MEASUREMENT_IN
        (
          seq,
          seq_len,
          signal_wavelength_nm,
          reference_wavelength_nm,
          duration_ms,
          *row,
          flags,
          progress,
        ) = struct.unpack("<BBhhI12fBB", chunk[2:-2])
        all_rows.extend(row)
        _, _, _, _, _ = signal_wavelength_nm, reference_wavelength_nm, duration_ms, flags, progress

        if seq == seq_len - 1:
          break

    return reshape_2d(all_rows, (8, 12))

  async def read_fluorescence(
    self,
    plate: Plate,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[List[float]]:
    raise NotImplementedError("Absorbance plate reader does not support fluorescence reading.")


class ByonoyLuminescence96AutomateBackend(_ByonoyBase):
  def __init__(self) -> None:
    super().__init__(pid=0x119B, device_type=_ByonoyDevice.LUMINESCENCE_96)

  async def read_absorbance(self, plate, wavelength):
    raise NotImplementedError(
      "Luminescence plate reader does not support absorbance reading. Use ByonoyAbsorbance96Automate instead."
    )

  async def read_luminescence(
    self, plate: Plate, focal_height: float, integration_time: float = 2
  ) -> List[List[float]]:
    """integration_time: in seconds, default 2 s"""

    await self.send_command(
      report_id=0x0010,  # SUPPORTED_REPORTS_IN
      payload_fmt="<BB29H",
      # "seq", "seq_len", "ids"
      payload=[0, 0, *([0] * 29)],
      wait_for_response=False,
    )

    await self.send_command(
      report_id=0x0200,  # DEVICE_DATA_READ_IN
      payload_fmt="<HB52s",
      # field_index", "flags", "data"
      payload=[7, 0, b"\x00" * 52],
      wait_for_response=False,
    )

    await self.send_command(
      report_id=0x0340,  # LUM_TRIGGER_MEASUREMENT_OUT
      payload_fmt="<i12sBB",
      # "integration_time_us", "channels_selected", "is_reference_measurement", "flags",
      payload=[
        int(integration_time * 1000 * 1000),
        b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff",
        0,
        0,
      ],
      wait_for_response=False,
    )

    t0 = time.time()
    all_rows = []

    while True:
      if time.time() - t0 > 120:  # read for 2 minutes max. typical is 1m5s.
        raise TimeoutError("Reading luminescence data timed out after 2 minutes.")

      chunk = await self.io.read(64, timeout=30)
      if len(chunk) == 0:
        continue

      report_id, *_ = struct.unpack("<H", chunk[:2])

      if report_id == 0x0600:  # REP_LUM96_MEASUREMENT_IN
        seq, seq_len, integration_time_us, duration_ms, *row, flags, progress = struct.unpack(
          "<BBII12fBB", chunk[2:-2]
        )
        all_rows.extend(row)
        _, _, _, _, _ = integration_time_us, duration_ms, row, flags, progress

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

    return reshape_2d(hybrid_result, (8, 12))

  async def read_fluorescence(
    self,
    plate: Plate,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[List[float]]:
    raise NotImplementedError("Fluorescence plate reader does not support fluorescence reading.")
