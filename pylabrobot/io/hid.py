import logging
from typing import TYPE_CHECKING, Optional

from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, ValidationError

try:
  import hid  # type: ignore

  USE_HID = True
except ImportError:
  USE_HID = False

if TYPE_CHECKING:
  from pylabrobot.io.validation import LogReader


logger = logging.getLogger(__name__)


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

  async def stop(self):
    self.device.close()
    logger.log(LOG_LEVEL_IO, "Closing HID device %s", self._unique_id)

  def write(self, data: bytes):
    self.device.write(data)
    logger.log(LOG_LEVEL_IO, "[%s] write %s", self._unique_id, data)

  def read(self, size: int, timeout: int) -> bytes:
    r = self.device.read(size, timeout=timeout)
    logger.log(LOG_LEVEL_IO, "[%s] read %s", self._unique_id, r)
    return r

  def serialize(self):
    return {
      "vid": self.vid,
      "pid": self.pid,
      "serial_number": self.serial_number,
    }


class HIDValidator(HID):
  def __init__(
    self, lr: "LogReader", vid: int = 0x03EB, pid: int = 0x2023, serial_number: Optional[str] = None
  ):
    super().__init__(vid=vid, pid=pid, serial_number=serial_number)
    self.lr = lr

  async def setup(self):
    next_line = self.lr.next_line()
    expected = f"Opening HID device {self._unique_id}"
    if not next_line == expected:
      raise ValidationError(f"Next line is {next_line}, expected {expected}")

  async def stop(self):
    next_line = self.lr.next_line()
    expected = f"Closing HID device {self._unique_id}"
    if not next_line == expected:
      raise ValidationError(f"Next line is {next_line}, expected {expected}")

  def write(self, data: bytes):
    next_line = self.lr.next_line()
    expected = f"[{self._unique_id}] write {data}"
    if not next_line == expected:
      raise ValidationError(f"Next line is {next_line}, expected {expected}")

  def read(self, size: int, timeout: int) -> bytes:
    next_line = self.lr.next_line()
    _, _, data = next_line.split(" ", 2)
    if not next_line.startswith(f"[{self._unique_id}] read"):
      raise ValidationError(f"Next line is {next_line}, expected {self._unique_id} read")
    if not len(data) == size:
      raise ValidationError(f"Read data has length {len(data)}, expected {size}")
    return data
