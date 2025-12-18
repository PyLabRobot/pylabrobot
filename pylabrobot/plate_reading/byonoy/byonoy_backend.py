import abc
import asyncio
import enum
import struct
import threading
import time
from typing import Dict, List, Optional

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

  def _assemble_command(
    self, report_id: int, payload_fmt: str, payload: list, routing_info: bytes
  ) -> bytes:
    # based on `encode_hid_report` function

    # Encode the payload
    binary_payload = struct.pack(payload_fmt, *payload)

    # Encode the full report (header + payload)
    header_fmt = "<H"
    binary_header = struct.pack(header_fmt, report_id)
    packet = binary_header + binary_payload
    packet += b"\x00" * (62 - len(packet)) + routing_info  # pad to 64 bytes

    return packet

  async def send_command(
    self,
    report_id: int,
    payload_fmt: str,
    payload: list,
    wait_for_response: bool = True,
    routing_info: bytes = b"\x00\x00",
  ) -> Optional[bytes]:
    command = self._assemble_command(
      report_id, payload_fmt=payload_fmt, payload=payload, routing_info=routing_info
    )

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

  async def setup(self, verbose: bool = False, **backend_kwargs):
    """Set up the plate reader. This should be called before any other methods."""

    # Call the base setup (opens HID)
    await super().setup(**backend_kwargs)

    # After device is online, run reference initialisation
    await self.initialize_measurements()

    self.available_wavelengths = await self.get_available_absorbance_wavelengths()

    msg = (
      f"Connected to Bynoy {self.io.device_info['product_string']} (via HID with "
      f"VID={self.io.device_info['vendor_id']}:PID={self.io.device_info['product_id']}) "
      f"on {self.io.device_info['path']}\n"
      f"Identified available wavelengths: {self.available_wavelengths} nm"
    )
    if verbose:
      print(msg)

  async def get_available_absorbance_wavelengths(self) -> List[float]:
    available_wavelengths_r = await self.send_command(
      report_id=0x0330,
      payload_fmt="<30h",
      payload=[0] * 30,
      wait_for_response=True,
      routing_info=b"\x80\x40",
    )
    assert available_wavelengths_r is not None, "Failed to get available wavelengths."
    # cut out the first 2 bytes, then read the next 2 bytes as an integer
    # 64 - 4 = 60. 60/2 = 30 16 bit integers
    available_wavelengths = list(struct.unpack("<30h", available_wavelengths_r[2:62]))
    available_wavelengths = [w for w in available_wavelengths if w != 0]
    return available_wavelengths

  async def _run_abs_measurement(self, signal_wl: int, reference_wl: int, is_reference: bool):
    """Perform an absorbance measurement or reference measurement.
    This contains all shared logic between initialization and real measurements."""

    # (1) SUPPORTED_REPORTS_IN   (0x0010)
    await self.send_command(
      report_id=0x0010,
      payload_fmt="<BB29H",
      payload=[0, 0, *([0] * 29)],
      wait_for_response=False,
    )

    # (2) DEVICE_DATA_READ_IN    (0x0200)
    await self.send_command(
      report_id=0x0200,
      payload_fmt="<HB52s",
      payload=[7, 0, b"\x00" * 52],
      wait_for_response=False,
    )

    # (3) ABS_TRIGGER_MEASUREMENT_OUT (0x0320)
    await self.send_command(
      report_id=0x0320,
      payload_fmt="<hhBB",
      payload=[signal_wl, reference_wl, int(is_reference), 0],
      wait_for_response=False,
      routing_info=b"\x00\x40",
    )

    # (4) Collect chunks (report_id 0x0500)
    rows = []
    t0 = time.time()

    while True:
      if time.time() - t0 > 120:
        raise TimeoutError("Measurement timeout.")

      chunk = await self.io.read(64, timeout=30)
      if len(chunk) == 0:
        continue

      report_id = int.from_bytes(chunk[:2], "little")

      # Only handle the measurement packets
      if report_id == 0x0500:
        (
          seq,
          seq_len,
          signal_wl_nm,
          reference_wl_nm,
          duration_ms,
          *row,
          flags,
          progress,
        ) = struct.unpack("<BBhhI12fBB", chunk[2:-2])

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
    num_measurement_replicates: int = 1,
  ) -> List[dict]:
    """
    Measure sample absorbance in each well at the specified wavelength.

    Args:
      wavelength: Signal wavelength in nanometers.
      plate: The plate being read. Included for API uniformity.
      wells, Subset of wells to return. If omitted, all 96 wells are returned.
      num_measurement_replicates: Number of technical replicate reads to acquire.  Replicates are taken sequentially and averaged per well.  (Handled at the backend level for faster acquisition and a simpler interface.)
    """

    assert (
      wavelength in self.available_wavelengths
    ), f"Wavelength {wavelength} nm not in available wavelengths {self.available_wavelengths}."

    # 1. Collect technical replicates
    technical_replicates = []
    for _ in range(num_measurement_replicates):
      rows = await self._run_abs_measurement(
        signal_wl=wavelength,
        reference_wl=0,
        is_reference=False,
      )
      technical_replicates.append(rows)

    # 2. Average the replicates (flat 96-element list)
    if num_measurement_replicates == 1:
      averaged_rows = technical_replicates[0]
    else:
      averaged_rows = [
        sum(rep[i] for rep in technical_replicates) / num_measurement_replicates for i in range(96)
      ]

    # 3. Convert flat -> 8x12 matrix
    matrix = reshape_2d(averaged_rows, (8, 12))

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
