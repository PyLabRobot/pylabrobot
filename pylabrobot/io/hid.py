import logging
from typing import Optional, cast

from pylabrobot.io.capture import CaptureReader, Command, capturer
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

try:
  import hid  # type: ignore

  USE_HID = True
except ImportError:
  USE_HID = False


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

  async def setup(self):
    if not USE_HID:
      raise RuntimeError("This backend requires the `hid` package to be installed")
    self.device = hid.Device(vid=self.vid, pid=self.pid, serial=self.serial_number)
    logger.log(LOG_LEVEL_IO, "Opened HID device %s", self._unique_id)
    capturer.record(HIDCommand(device_id=self._unique_id, action="open", data=""))

  async def stop(self):
    if self.device is not None:
      self.device.close()
    logger.log(LOG_LEVEL_IO, "Closing HID device %s", self._unique_id)
    capturer.record(HIDCommand(device_id=self._unique_id, action="close", data=""))

  def write(self, data: bytes):
    assert self.device is not None, "forgot to call setup?"
    self.device.write(data)
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._unique_id, data)
    capturer.record(HIDCommand(device_id=self._unique_id, action="write", data=data.decode()))

  def read(self, size: int, timeout: int) -> bytes:
    assert self.device is not None, "forgot to call setup?"
    r = self.device.read(size, timeout=timeout)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._unique_id, r)
    capturer.record(HIDCommand(device_id=self._unique_id, action="read", data=r.decode()))
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

  def write(self, data: bytes):
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

  def read(self, size: int, timeout: int) -> bytes:
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
