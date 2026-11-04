import abc
import enum
import time
from typing import Dict, List, Optional

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.binary import Reader, Writer
from pylabrobot.io.hid import HID
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
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
    super().__init__()
    self.io = HID(
      human_readable_device_name="Byonoy Plate Reader",
      vid=0x16D0,
      pid=pid,
      max_input_report_size=64,
    )
    self._transaction_lock = anyio.Lock()
    self._ping_interval = 1.0  # Send ping every second
    self._sending_pings = False
    self._device_type = device_type

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Own the HID transport and heartbeat worker for one plate reader lifespan."""
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io.lifespan())
    task_group = await stack.enter_task_group()
    stack.callback(task_group.cancel_scope.cancel)
    stack.callback(self._stop_background_pings)
    task_group.start_soon(self._heartbeat_loop)

  def _assemble_command(self, report_id: int, payload: bytes, routing_info: bytes) -> bytes:
    packet = Writer().u16(report_id).raw_bytes(payload).finish()
    packet += b"\x00" * (62 - len(packet)) + routing_info  # pad to 64 bytes
    return packet

  async def _read_report(self) -> bytes:
    """Read a complete HID report before parsing its device protocol."""
    result = await self.io.read_to_boundary()
    if not result.started_at_boundary or not result.ended_at_boundary:
      raise RuntimeError("Incomplete Byonoy HID report")
    return result.data

  async def send_command(
    self,
    report_id: int,
    payload: bytes,
    wait_for_response: bool = True,
    routing_info: bytes = b"\x00\x00",
  ) -> Optional[bytes]:
    async with self._transaction_lock:
      response = await self._send_command_locked(
        report_id=report_id,
        payload=payload,
        wait_for_response=wait_for_response,
        routing_info=routing_info,
      )
    await anyio.lowlevel.checkpoint()
    return response

  async def _send_command_locked(
    self,
    report_id: int,
    payload: bytes,
    wait_for_response: bool,
    routing_info: bytes,
  ) -> Optional[bytes]:
    """Execute one command while the caller owns the transaction lock."""
    command = self._assemble_command(report_id, payload=payload, routing_info=routing_info)

    await self.io.write(command)
    if not wait_for_response:
      return None

    with anyio.CancelScope(shield=True):
      try:
        with anyio.fail_after(120):
          while True:
            response = await self._read_report()
            # if the first 2 bytes do not match, we continue reading
            response_report_id = Reader(response).u16()
            if report_id == response_report_id:
              break
      except TimeoutError:
        raise TimeoutError("Reading luminescence data timed out after 2 minutes.") from None
    return response

  async def _heartbeat_loop(self) -> None:
    """Send heartbeat writes within the owning lifespan task group."""
    while True:
      if self._sending_pings:
        payload = Writer().u8(1).finish()
        async with self._transaction_lock:
          await self.io.write(self._assemble_command(0x0040, payload, b"\x00\x00"))
      await anyio.sleep(self._ping_interval)

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

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Own the transport and complete absorbance initialization before entry."""
    await super()._enter_lifespan(stack)
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

    async with self._transaction_lock:
      # (1) SUPPORTED_REPORTS_IN (0x0010)
      await self._send_command_locked(
        report_id=0x0010,
        payload=b"\x00" * 60,  # seq, seq_len, ids[29]
        wait_for_response=False,
        routing_info=b"\x00\x00",
      )

      with anyio.CancelScope(shield=True):
        # (2) DEVICE_DATA_READ_IN (0x0200)
        payload2 = (
          Writer()
          .u16(7)  # field_index
          .u8(0)  # flags
          .raw_bytes(b"\x00" * 52)  # data
          .finish()
        )
        await self._send_command_locked(
          report_id=0x0200,
          payload=payload2,
          wait_for_response=False,
          routing_info=b"\x00\x00",
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
        await self._send_command_locked(
          report_id=0x0320,
          payload=payload3,
          wait_for_response=False,
          routing_info=b"\x00\x40",
        )

        # (4) Collect chunks (report_id 0x0500)
        rows: List[float] = []
        try:
          with anyio.fail_after(120):
            while True:
              chunk = await self._read_report()
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
        except TimeoutError:
          raise TimeoutError("Measurement timeout.") from None

    await anyio.lowlevel.checkpoint()
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

    assert wavelength in self.available_wavelengths, (
      f"Wavelength {wavelength} nm not in available wavelengths {self.available_wavelengths}."
    )

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

    async with self._transaction_lock:
      # SUPPORTED_REPORTS_IN (0x0010)
      await self._send_command_locked(
        report_id=0x0010,
        payload=b"\x00" * 60,  # seq, seq_len, ids[29]
        wait_for_response=False,
        routing_info=b"\x00\x00",
      )

      with anyio.CancelScope(shield=True):
        # DEVICE_DATA_READ_IN (0x0200)
        payload2 = (
          Writer()
          .u16(7)  # field_index
          .u8(0)  # flags
          .raw_bytes(b"\x00" * 52)  # data
          .finish()
        )
        await self._send_command_locked(
          report_id=0x0200,
          payload=payload2,
          wait_for_response=False,
          routing_info=b"\x00\x00",
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
        await self._send_command_locked(
          report_id=0x0340,
          payload=payload3,
          wait_for_response=False,
          routing_info=b"\x00\x00",
        )

        all_rows: List[float] = []

        try:
          with anyio.fail_after(120):
            while True:
              chunk = await self._read_report()
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
        except TimeoutError:
          raise TimeoutError("Reading luminescence data timed out after 2 minutes.") from None

    await anyio.lowlevel.checkpoint()

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
