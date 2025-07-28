import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, cast

from pylabrobot.io.capture import CaptureReader, Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

try:
  import hid  # type: ignore

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
  def __init__(self, vid=0x03EB, pid=0x2023, serial_number: Optional[str] = None):
    self.vid = vid
    self.pid = pid
    self.serial_number = serial_number
    self.device: Optional[hid.Device] = None
    self._unique_id = f"{vid}:{pid}:{serial_number}"
    self._executor: Optional[ThreadPoolExecutor] = None

    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a new HID object while capture or validation is active")

  async def setup(self):
    if not USE_HID:
      raise RuntimeError(
        f"This backend requires the `hid` package to be installed. Import error: {_HID_IMPORT_ERROR}"
      )
    self.device = hid.Device(vid=self.vid, pid=self.pid, serial=self.serial_number)
    self._executor = ThreadPoolExecutor(max_workers=1)
    logger.log(LOG_LEVEL_IO, "Opened HID device %s", self._unique_id)
    capturer.record(HIDCommand(device_id=self._unique_id, action="open", data=""))

  async def stop(self):
    if self.device is not None:
      self.device.close()
    logger.log(LOG_LEVEL_IO, "Closing HID device %s", self._unique_id)
    capturer.record(HIDCommand(device_id=self._unique_id, action="close", data=""))
    if self._executor is not None:
      self._executor.shutdown(wait=True)
      self._executor = None

  async def write(self, data: bytes):
    loop = asyncio.get_running_loop()

    def _write():
      assert self.device is not None, "forgot to call setup?"
      return self.device.write(data)

    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    r = await loop.run_in_executor(self._executor, _write)
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._unique_id, data)
    capturer.record(HIDCommand(device_id=self._unique_id, action="write", data=data.hex()))
    return r

  async def read(self, size: int, timeout: int) -> bytes:
    loop = asyncio.get_running_loop()

    def _read():
      assert self.device is not None, "forgot to call setup?"
      return self.device.read(size, timeout=timeout)

    if self._executor is None:
      raise RuntimeError("Call setup() first.")
    r = await loop.run_in_executor(self._executor, _read)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._unique_id, r)
    capturer.record(HIDCommand(device_id=self._unique_id, action="read", data=r.hex()))
    return cast(bytes, r)

  def serialize(self):
    return {
      "vid": self.vid,
      "pid": self.pid,
      "serial_number": self.serial_number,
    }


class HIDValidator(HID):
  def __init__(
    self,
    cr: "CaptureReader",
    vid: int = 0x03EB,
    pid: int = 0x2023,
    serial_number: Optional[str] = None,
  ):
    super().__init__(vid=vid, pid=pid, serial_number=serial_number)
    self.cr = cr

  async def setup(self):
    next_command = HIDCommand(**self.cr.next_command())
    if (
      not next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "open"
    ):
      raise ValidationError(f"Next line is {next_command}, expected HID open {self._unique_id}")

  async def stop(self):
    next_command = HIDCommand(**self.cr.next_command())
    if (
      not next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "close"
    ):
      raise ValidationError(f"Next line is {next_command}, expected HID close {self._unique_id}")

  async def write(self, data: bytes):
    next_command = HIDCommand(**self.cr.next_command())
    if (
      not next_command.module == "hid"
      and next_command.device_id == self._unique_id
      and next_command.action == "write"
    ):
      raise ValidationError(f"Next line is {next_command}, expected HID write {self._unique_id}")
    if not next_command.data == data.decode():
      align_sequences(expected=next_command.data, actual=data.decode())
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
