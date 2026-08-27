from __future__ import annotations

import logging
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar, Union

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.boundary import (
  BoundaryCursor,
  BoundaryDiscard,
  BoundaryRead,
  HIDReportTooLargeError,
)
from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import ContinuousConnectionIO
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

try:
  import hid  # type: ignore
  from hid import HIDException

  USE_HID = True
except ImportError as e:
  USE_HID = False
  _HID_IMPORT_ERROR = e


logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class _UnsetTimeout:
  """Distinguish a missing public deadline from an explicit HIDAPI timeout."""


_UNSET_TIMEOUT = _UnsetTimeout()


def maximum_input_report_size(descriptor: bytes) -> int:
  """Calculate the largest HIDAPI-visible Input report from a report descriptor."""
  totals: dict[int, int] = {}
  size: int | None = None
  count: int | None = None
  report_id = 0
  stack: list[tuple[int | None, int | None, int]] = []
  offset = 0
  while offset < len(descriptor):
    prefix = descriptor[offset]
    offset += 1
    if prefix == 0xFE:
      if offset + 2 > len(descriptor):
        raise ValueError("Truncated HID long item")
      length = descriptor[offset]
      offset += 2 + length
      if offset > len(descriptor):
        raise ValueError("Truncated HID long item payload")
      continue
    width = (0, 1, 2, 4)[prefix & 3]
    if offset + width > len(descriptor):
      raise ValueError("Truncated HID item")
    value = int.from_bytes(descriptor[offset : offset + width], "little")
    offset += width
    tag = prefix & 0xFC
    if tag == 0x74:
      size = value
    elif tag == 0x94:
      count = value
    elif tag == 0x84:
      if value == 0:
        raise ValueError("Invalid HID Report ID 0")
      report_id = value
    elif tag == 0xA4:
      stack.append((size, count, report_id))
    elif tag == 0xB4:
      if not stack:
        raise ValueError("Unbalanced HID Pop")
      size, count, report_id = stack.pop()
    elif tag == 0x80:
      if size is None or count is None:
        raise ValueError("HID Input without Report Size and Count")
      totals[report_id] = totals.get(report_id, 0) + size * count
  if not totals or stack:
    raise ValueError("HID descriptor has no complete Input reports")
  return max((bits + 7) // 8 + int(identifier != 0) for identifier, bits in totals.items())


@dataclass
class HIDCommand(Command):
  data: str
  marks: list[dict[str, object]] = field(default_factory=list)
  started_at_boundary: bool | None = None
  ended_at_boundary: bool | None = None
  limit_reached: bool | None = None
  discarded_bytes: int | None = None
  integrity: str | None = None
  integrity_maximum: int | None = None
  max_bytes: int | None = None
  consume_immediate_boundary: bool | None = None

  def __init__(
    self,
    device_id: str,
    action: str,
    data: str,
    module: str = "hid",
    marks: list[dict[str, object]] | None = None,
    started_at_boundary: bool | None = None,
    ended_at_boundary: bool | None = None,
    limit_reached: bool | None = None,
    discarded_bytes: int | None = None,
    integrity: str | None = None,
    integrity_maximum: int | None = None,
    max_bytes: int | None = None,
    consume_immediate_boundary: bool | None = None,
  ):
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data
    self.marks = marks or []
    self.started_at_boundary = started_at_boundary
    self.ended_at_boundary = ended_at_boundary
    self.limit_reached = limit_reached
    self.discarded_bytes = discarded_bytes
    self.integrity = integrity
    self.integrity_maximum = integrity_maximum
    self.max_bytes = max_bytes
    self.consume_immediate_boundary = consume_immediate_boundary


class HID(ContinuousConnectionIO):
  """Own one HID report handle and its affinity worker per lifespan."""

  def __init__(
    self,
    human_readable_device_name: str,
    vid: int,
    pid: int,
    serial_number: Optional[str] = None,
    max_input_report_size: int | None = None,
  ):
    super().__init__()
    if max_input_report_size is not None and max_input_report_size <= 0:
      raise ValueError("max_input_report_size must be positive")
    self.max_input_report_size = max_input_report_size
    self.human_readable_device_name = human_readable_device_name
    self.vid = vid
    self.pid = pid
    self.serial_number = serial_number
    self.device: Optional[hid.Device] = None
    self._unique_id = f"{vid}:{pid}:{serial_number}"
    self._executor: Optional[ThreadPoolExecutor] = None
    self._connection_lost = False
    self._read_lock = anyio.Lock()
    self._write_lock = anyio.Lock()
    self._cursor = BoundaryCursor(frozenset({"report"}))
    self._oversized_report = False
    self._poll_timeout = 20  # milliseconds; HIDAPI calls must stay finite

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new HID object while capture or validation is active")

  def _setup_sync(self) -> None:
    """Enumerate, match and open the device. Runs on the executor that owns all device calls."""
    # --- 1. Enumerate all HID devices ---
    all_devices = hid.enumerate()
    candidates = [
      d
      for d in all_devices
      if (d.get("vendor_id") == self.vid) and (d.get("product_id") == self.pid)
    ]

    # --- 2. No devices found ---
    if len(candidates) == 0:
      formatted_vid = f"0x{self.vid:04X}" if self.vid is not None else "any"
      formatted_pid = f"0x{self.pid:04X}" if self.pid is not None else "any"
      raise RuntimeError(f"No HID devices found for VID={formatted_vid}, PID={formatted_pid}.")

    # --- 3. Serial number specified: must match exactly 1 ---
    if self.serial_number is not None:
      candidates = [d for d in candidates if d.get("serial_number") == self.serial_number]

      if len(candidates) == 0:
        raise RuntimeError(
          f"No HID devices found with VID=0x{self.vid:04X}, PID=0x{self.pid:04X}, "
          f"serial={self.serial_number}."
        )

      if len(candidates) > 1:
        raise RuntimeError(
          f"Multiple HID devices found with identical serial number "
          f"{self.serial_number} for VID/PID {self.vid}:{self.pid}. "
          "Ambiguous; cannot continue."
        )

      chosen = candidates[0]

    # --- 4. Serial number not specified: require exactly one device ---
    else:
      if len(candidates) > 1:
        raise RuntimeError(
          f"Multiple HID devices detected for VID=0x{self.vid:04X}, "
          f"PID=0x{self.pid:04X}.\n"
          f"Serial numbers: {[d.get('serial_number') for d in candidates]}\n"
          "Please specify `serial_number=` explicitly."
        )
      chosen = candidates[0]

    # --- 5. Open the device ---
    self.device = hid.Device(
      path=chosen["path"]  # safer than vid/pid/serial triple
    )
    if self.max_input_report_size is None:
      try:
        descriptor = self.device.get_report_descriptor()
      except AttributeError as exc:
        raise ValueError("HID report descriptor unavailable; supply max_input_report_size") from exc
      self.max_input_report_size = maximum_input_report_size(bytes(descriptor))

  async def _run_on_worker(self, func: Callable[..., _T], *args: Any) -> _T:
    """Run one HIDAPI call on the device's affinity-preserving worker thread."""
    if self._executor is None:
      raise RuntimeError("HID transport is not active")
    future = self._executor.submit(func, *args)
    return await anyio.to_thread.run_sync(future.result)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Open the HID device and register affinity-preserving cleanup."""
    await super()._enter_lifespan(stack)
    if not USE_HID:
      raise RuntimeError(
        "hid is not installed. Install with: pip install pylabrobot[hid]. "
        f"Import error: {_HID_IMPORT_ERROR}"
      )

    self._connection_lost = False
    self._cursor = BoundaryCursor(frozenset({"report"}))
    self._oversized_report = False
    self._executor = ThreadPoolExecutor(max_workers=1)
    stack.push_shielded_async_callback(self._shutdown_executor)
    try:
      with anyio.CancelScope(shield=True):
        await self._run_on_worker(self._setup_sync)
    except BaseException:
      with anyio.CancelScope(shield=True):
        try:
          await self._close(record=False)
        except Exception:
          logger.warning("Failed to close HID device after setup failure", exc_info=True)
      raise

    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint_if_cancelled()
    logger.log(LOG_LEVEL_IO, "Opened HID device %s", self._unique_id)
    capturer.record(HIDCommand(device_id=self._unique_id, action="open", data=""))

  async def _shutdown_executor(self) -> None:
    """Wait for the affinity worker to terminate, then mark the transport inactive."""
    executor = self._executor
    if executor is not None:
      try:
        await anyio.to_thread.run_sync(lambda: executor.shutdown(wait=True, cancel_futures=True))
      finally:
        self._executor = None

  async def _close(self, record: bool = True) -> None:
    """Close the HID device on its affinity worker."""
    device = self.device
    try:
      if device is not None:
        await self._run_on_worker(device.close)
    finally:
      self.device = None
      self._cursor = BoundaryCursor(frozenset({"report"}))
      self._oversized_report = False
    if record:
      logger.log(LOG_LEVEL_IO, "Closing HID device %s", self._unique_id)
      capturer.record(HIDCommand(device_id=self._unique_id, action="close", data=""))

  def _require_connection_usable(self) -> None:
    """Do not issue further reports after an observed hard I/O failure."""
    if self._connection_lost:
      raise ConnectionError("HID connection lost; enter a new lifespan")

  def _record_cursor_operation(
    self,
    action: str,
    data: bytes,
    result: BoundaryRead | BoundaryDiscard | None = None,
    max_bytes: int | None = None,
    consume_immediate_boundary: bool | None = None,
    integrity: str | None = None,
  ) -> None:
    """Capture one returned operation and its observed cursor marks."""
    marks: list[dict[str, object]] = [
      {"offset": mark.offset, "kind": mark.kind, "pending": mark.pending, "provenance": "observed"}
      for mark in self._cursor.last_operation_marks
    ]
    capturer.record(
      HIDCommand(
        device_id=self._unique_id,
        action=action,
        data=data.hex(),
        marks=marks,
        started_at_boundary=result.started_at_boundary if result is not None else None,
        ended_at_boundary=result.ended_at_boundary if result is not None else None,
        limit_reached=result.limit_reached if result is not None else None,
        discarded_bytes=result.discarded_bytes if isinstance(result, BoundaryDiscard) else None,
        max_bytes=max_bytes,
        consume_immediate_boundary=consume_immediate_boundary,
        integrity=integrity,
      )
    )

  async def write(self, data: bytes, report_id: bytes = b"\x00"):
    r"""Writes data to the HID device.

    There is a non-obvious part in the HID API:

    "The first byte of \@p data[] must contain the Report ID. For
    devices which only support a single report, this must be set
    to 0x0. The remaining bytes contain the report data. Since
    the Report ID is mandatory, calls to hid_write() will always
    contain one more byte than the report contains. For example,
    if a hid report is 16 bytes long, 17 bytes must be passed to
    hid_write(), the Report ID (or 0x0, for devices with a
    single report), followed by the report data (16 bytes). In
    this example, the length passed in would be 17.
    "
    https://github.com/libusb/hidapi/blob/9904cbe/hidapi/hidapi.h#L305

    We make this explicit in our API by requiring the `report_id` parameter.

    Args:
      data: The data to write.
      report_id: The report ID to use for the write operation. Defaults to b'\x00'.
    """
    self._require_connection_usable()
    write_data = report_id + data

    def _write():
      if self.device is None:
        raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")
      return self.device.write(write_data)

    async with self._write_lock:
      await anyio.lowlevel.checkpoint()
      try:
        with anyio.CancelScope(shield=True):
          r = await self._run_on_worker(_write)
      except (HIDException, OSError):
        self._connection_lost = True
        raise
      if r != len(write_data):
        self._connection_lost = True
        raise OSError(f"HID short write: wrote {r} of {len(write_data)} bytes")
    logger.log(
      LOG_LEVEL_IO, "[%s] write %s (report_id: %s)", self._unique_id, data, report_id.hex()
    )
    capturer.record(HIDCommand(device_id=self._unique_id, action="write", data=write_data.hex()))
    return r

  async def read(
    self, size: int, timeout: Union[int, None, _UnsetTimeout] = _UNSET_TIMEOUT
  ) -> bytes:
    """Wait for a nonempty report on the affinity worker; timeout is in milliseconds."""
    if size <= 0:
      raise ValueError("size must be positive")
    self._require_connection_usable()
    if isinstance(timeout, _UnsetTimeout):
      public_timeout = None
    else:
      warnings.warn(
        "HID.read(timeout=...) is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
      public_timeout = None if timeout is None else timeout / 1000

    async with self._read_lock:
      with anyio.fail_after(public_timeout):
        while True:
          await anyio.lowlevel.checkpoint_if_cancelled()
          if self._oversized_report:
            capturer.record(
              HIDCommand(
                device_id=self._unique_id,
                action="read",
                data="",
                integrity="oversized",
                integrity_maximum=self.max_input_report_size,
              )
            )
            raise HIDReportTooLargeError(self.max_input_report_size or 0)
          data = self._cursor.read_bytes(size)
          if data:
            logger.log(LOG_LEVEL_IO, "[%s] read %s", self._unique_id, data)
            self._record_cursor_operation("read", data)
            return data
          await self._acquire_report()

  async def _acquire_report(self) -> None:
    """Read one complete native report while shielding its worker completion."""
    maximum = self.max_input_report_size
    if maximum is None:
      raise RuntimeError("HID input report maximum is not known")

    def _read():
      if self.device is None:
        raise RuntimeError(f"Call setup() first for device '{self.human_readable_device_name}'.")
      try:
        return self.device.read(maximum + 1, timeout=self._poll_timeout)
      except HIDException as e:
        if str(e) == "Success":
          return b""
        raise

    try:
      with anyio.CancelScope(shield=True):
        result = await self._run_on_worker(_read)
        if len(result) > maximum:
          self._oversized_report = True
        elif result:
          self._cursor.append(bytes(result), frozenset({"report"}))
      await anyio.lowlevel.checkpoint_if_cancelled()
    except (HIDException, OSError):
      self._connection_lost = True
      raise
    if not result:
      await anyio.lowlevel.checkpoint()

  async def read_to_boundary(
    self, max_bytes: int | None = None, *, consume_immediate_boundary: bool = True
  ) -> BoundaryRead:
    """Read through the next complete HID input report boundary."""
    if max_bytes is not None and max_bytes <= 0:
      raise ValueError("max_bytes must be positive")
    self._require_connection_usable()
    async with self._read_lock:
      while True:
        await anyio.lowlevel.checkpoint_if_cancelled()
        if self._oversized_report:
          capturer.record(
            HIDCommand(
              device_id=self._unique_id,
              action="read_to_boundary",
              data="",
              integrity="oversized",
              integrity_maximum=self.max_input_report_size,
            )
          )
          raise HIDReportTooLargeError(self.max_input_report_size or 0)
        result = self._cursor.take_to_boundary(
          max_bytes,
          boundary="report",
          consume_immediate_boundary=consume_immediate_boundary,
          discard=False,
        )
        if isinstance(result, BoundaryRead):
          self._record_cursor_operation(
            "read_to_boundary", result.data, result, max_bytes, consume_immediate_boundary
          )
          return result
        await self._acquire_report()

  async def discard_to_boundary(
    self, max_bytes: int | None = None, *, consume_immediate_boundary: bool = False
  ) -> BoundaryDiscard:
    """Discard through a report boundary, or acknowledge an oversized report."""
    if max_bytes is not None and max_bytes <= 0:
      raise ValueError("max_bytes must be positive")
    self._require_connection_usable()
    async with self._read_lock:
      await anyio.lowlevel.checkpoint_if_cancelled()
      if self._oversized_report:
        if max_bytes is not None or not consume_immediate_boundary:
          capturer.record(
            HIDCommand(
              device_id=self._unique_id,
              action="discard_to_boundary",
              data="",
              integrity="oversized",
              integrity_maximum=self.max_input_report_size,
            )
          )
          raise HIDReportTooLargeError(self.max_input_report_size or 0)
        self._oversized_report = False
        recovered = BoundaryDiscard(None, True, True, False)
        self._cursor.last_operation_marks = ()
        self._record_cursor_operation(
          "discard_to_boundary",
          b"",
          recovered,
          max_bytes,
          consume_immediate_boundary,
          integrity="recovered",
        )
        return recovered
      while True:
        result = self._cursor.take_to_boundary(
          max_bytes,
          boundary="report",
          consume_immediate_boundary=consume_immediate_boundary,
          discard=True,
        )
        if isinstance(result, BoundaryDiscard):
          self._record_cursor_operation(
            "discard_to_boundary", b"", result, max_bytes, consume_immediate_boundary
          )
          return result
        await self._acquire_report()

  def serialize(self):
    return {
      "human_readable_device_name": self.human_readable_device_name,
      "vid": self.vid,
      "pid": self.pid,
      "serial_number": self.serial_number,
    }


class HIDValidator(HID):
  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    vid: int = 0x03EB,
    pid: int = 0x2023,
    serial_number: Optional[str] = None,
    legacy_report_size: int | None = None,
  ):
    if legacy_report_size is not None and legacy_report_size <= 0:
      raise ValueError("legacy_report_size must be positive")
    super().__init__(
      human_readable_device_name=human_readable_device_name,
      vid=vid,
      pid=pid,
      serial_number=serial_number,
    )
    self.cr = cr
    self.legacy_report_size = legacy_report_size
    self.legacy_inferred_marks: list[dict[str, object]] = []

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Validate open and register validation of close at lifespan exit."""
    self.legacy_inferred_marks.clear()
    next_command = HIDCommand(**self.cr.next_command())
    if not (
      next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "open"
    ):
      raise ValidationError(f"Next line is {next_command}, expected HID open {self._unique_id}")
    stack.push_shielded_async_callback(self._validate_close)

  async def _validate_close(self) -> None:
    """Validate the captured close operation."""
    next_command = HIDCommand(**self.cr.next_command())
    if not (
      next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "close"
    ):
      raise ValidationError(f"Next line is {next_command}, expected HID close {self._unique_id}")

  async def write(self, data: bytes, report_id: bytes = b"\x00"):
    if not self.is_active:
      raise RuntimeError("HID transport is not active")
    await anyio.lowlevel.checkpoint()
    next_command = HIDCommand(**self.cr.next_command())
    if not (
      next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected HID write {self._unique_id}")
    write_data = report_id + data
    if not next_command.data == write_data.hex():
      align_sequences(expected=next_command.data, actual=write_data.hex())
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def read(
    self, size: int, timeout: Union[int, None, _UnsetTimeout] = _UNSET_TIMEOUT
  ) -> bytes:
    if size <= 0:
      raise ValueError("size must be positive")
    if not isinstance(timeout, _UnsetTimeout):
      warnings.warn(
        "HID.read(timeout=...) is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
    await anyio.lowlevel.checkpoint()
    if not self.is_active:
      raise RuntimeError("HID transport is not active")
    next_command = HIDCommand(**self.cr.next_command())
    data = bytes.fromhex(next_command.data)
    if not (
      next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
      and len(data) <= size
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected HID read {self._unique_id}: {size}"
      )
    if next_command.integrity == "oversized":
      raise HIDReportTooLargeError(next_command.integrity_maximum or 0)
    if (
      self.legacy_report_size is not None
      and not next_command.marks
      and len(data) > self.legacy_report_size
    ):
      raise ValidationError("Legacy HID report exceeds assumed report size")
    if not data:
      raise anyio.EndOfStream
    return data

  async def read_to_boundary(
    self, max_bytes: int | None = None, *, consume_immediate_boundary: bool = True
  ) -> BoundaryRead:
    """Replay a captured report with explicit or assumption-labelled boundaries."""
    if max_bytes is not None and max_bytes <= 0:
      raise ValueError("max_bytes must be positive")
    await anyio.lowlevel.checkpoint()
    if not self.is_active:
      raise RuntimeError("HID transport is not active")
    command = HIDCommand(**self.cr.next_command())
    if command.device_id != self._unique_id or command.module != "hid":
      raise ValidationError("HID read device mismatch")
    if command.integrity == "oversized":
      raise HIDReportTooLargeError(command.integrity_maximum or 0)
    data = bytes.fromhex(command.data)
    if command.action == "read" and self.legacy_report_size is not None:
      if len(data) > self.legacy_report_size:
        raise ValidationError("Legacy HID report exceeds assumed report size")
      if max_bytes is not None and len(data) > max_bytes:
        raise ValidationError("Legacy HID report exceeds requested boundary limit")
      self.legacy_inferred_marks.append(
        {
          "offset": len(data),
          "kind": "report",
          "provenance": "legacy_inferred",
          "assumptions": {"legacy_report_size": self.legacy_report_size},
        }
      )
      return BoundaryRead(data, True, True, False)
    if command.action != "read_to_boundary" or command.ended_at_boundary is None:
      raise ValidationError("HID boundary replay requires observed marks or legacy_report_size")
    if (
      command.consume_immediate_boundary is not None
      and command.consume_immediate_boundary != consume_immediate_boundary
    ):
      raise ValidationError("Captured HID immediate-boundary policy mismatch")
    if command.max_bytes is not None and command.max_bytes != max_bytes:
      raise ValidationError("Captured HID boundary limit mismatch")
    if max_bytes is not None and len(data) > max_bytes:
      raise ValidationError("Captured HID boundary read exceeds requested size")
    return BoundaryRead(
      data,
      bool(command.started_at_boundary),
      command.ended_at_boundary,
      bool(command.limit_reached),
    )

  async def discard_to_boundary(
    self, max_bytes: int | None = None, *, consume_immediate_boundary: bool = False
  ) -> BoundaryDiscard:
    """Replay an explicit captured discard operation."""
    if max_bytes is not None and max_bytes <= 0:
      raise ValueError("max_bytes must be positive")
    await anyio.lowlevel.checkpoint()
    if not self.is_active:
      raise RuntimeError("HID transport is not active")
    command = HIDCommand(**self.cr.next_command())
    if command.device_id != self._unique_id or command.action != "discard_to_boundary":
      raise ValidationError("Next command is not an HID boundary discard")
    if command.integrity == "oversized":
      raise HIDReportTooLargeError(command.integrity_maximum or 0)
    if (
      command.consume_immediate_boundary is not None
      and command.consume_immediate_boundary != consume_immediate_boundary
    ):
      raise ValidationError("Captured HID immediate-boundary policy mismatch")
    return BoundaryDiscard(
      command.discarded_bytes,
      bool(command.started_at_boundary),
      bool(command.ended_at_boundary),
      bool(command.limit_reached),
    )
