import contextlib
import logging
from typing import Optional, cast

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

try:
  import hid  # type: ignore
  from hid import HIDException

  USE_HID = True
except ImportError as e:
  USE_HID = False
  _HID_IMPORT_ERROR = e


logger = logging.getLogger(__name__)


class HIDCommand(Command):
  data: str

  def __init__(self, device_id: str, action: str, data: str):
    super().__init__(module="hid", device_id=device_id, action=action)


class HID(IOBase):
  def __init__(
    self, human_readable_device_name: str, vid: int, pid: int, serial_number: Optional[str] = None
  ):
    self._human_readable_device_name = human_readable_device_name
    self.vid = vid
    self.pid = pid
    self.serial_number = serial_number
    self.device: Optional[hid.Device] = None
    self._unique_id = f"{vid}:{pid}:{serial_number}"
    self._lock = anyio.Lock()

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new HID object while capture or validation is active")

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    """
    Sets up the HID device by enumerating connected devices, matching the specified
    VID, PID, and optional serial number, and opening a connection to the device.
    """
    if not USE_HID:
      raise RuntimeError(
        "hid is not installed. Install with: pip install pylabrobot[hid]. "
        f"Import error: {_HID_IMPORT_ERROR}"
      )

    # --- 1. Enumerate all HID devices ---
    all_devices = await anyio.to_thread.run_sync(hid.enumerate)
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
    self.device = await anyio.to_thread.run_sync(lambda: hid.Device(path=chosen["path"]))

    async def _cleanup():
      if self.device is not None:
        await anyio.to_thread.run_sync(self.device.close)
      logger.log(LOG_LEVEL_IO, "Closing HID device %s", self._unique_id)
      capturer.record(HIDCommand(device_id=self._unique_id, action="close", data=""))

    stack.push_shielded_async_callback(_cleanup)

    logger.log(LOG_LEVEL_IO, "Opened HID device %s", self._unique_id)
    capturer.record(HIDCommand(device_id=self._unique_id, action="open", data=""))

  async def _dev_call(self, func, *args):
    async with self._lock:
      return await anyio.to_thread.run_sync(func, *args)

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
    write_data = report_id + data

    def _write():
      if self.device is None:
        raise RuntimeError(f"Call setup() first for device '{self._human_readable_device_name}'.")
      return self.device.write(write_data)

    r = await self._dev_call(_write)
    logger.log(
      LOG_LEVEL_IO, "[%s] write %s (report_id: %s)", self._unique_id, data, report_id.hex()
    )
    capturer.record(HIDCommand(device_id=self._unique_id, action="write", data=write_data.hex()))
    return r

  async def read(self, size: int, timeout: int) -> bytes:
    def _read():
      if self.device is None:
        raise RuntimeError(f"Call setup() first for device '{self._human_readable_device_name}'.")
      try:
        return self.device.read(size, timeout=int(timeout))
      except HIDException as e:
        if str(e) == "Success":
          return b""
        raise

    r = await self._dev_call(_read)
    if len(r) > 0:
      logger.log(LOG_LEVEL_IO, "[%s] read %s", self._unique_id, r)
      capturer.record(HIDCommand(device_id=self._unique_id, action="read", data=r.hex()))
    return cast(bytes, r)

  def serialize(self):
    return {
      "human_readable_device_name": self._human_readable_device_name,
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
  ):
    super().__init__(
      human_readable_device_name=human_readable_device_name,
      vid=vid,
      pid=pid,
      serial_number=serial_number,
    )
    self.cr = cr

  async def _enter_lifespan(self, stack: contextlib.AsyncExitStack):
    next_command = HIDCommand(**self.cr.next_command())
    if (
      not next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "open"
    ):
      raise ValidationError(f"Next line is {next_command}, expected HID open {self._unique_id}")

    def _cleanup():
      next_command = HIDCommand(**self.cr.next_command())
      if (
        not next_command.module == "hid"
        and next_command.device_id == self._unique_id
        and next_command.action == "close"
      ):
        raise ValidationError(f"Next line is {next_command}, expected HID close {self._unique_id}")

    stack.callback(_cleanup)

  async def write(self, data: bytes, report_id: bytes = b"\x00"):
    next_command = HIDCommand(**self.cr.next_command())
    if (
      not next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected HID write {self._unique_id}")
    write_data = report_id + data
    if not next_command.data == write_data.hex():
      align_sequences(expected=next_command.data, actual=write_data.hex())
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def read(self, size: int, timeout: int) -> bytes:
    next_command = HIDCommand(**self.cr.next_command())
    if (
      not next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
      and len(next_command.data) == size
    ):
      raise ValidationError(
        f"Next line is {next_command}, expected HID read {self._unique_id}: {size}"
      )
    return next_command.data.encode()
