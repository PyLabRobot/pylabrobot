from typing import List, Optional

from pylabrobot.io.ftdi import FTDI, FTDIValidator
from pylabrobot.io.hid import HID, HIDValidator
from pylabrobot.io.serial import Serial, SerialValidator
from pylabrobot.io.usb import USB, USBValidator
from pylabrobot.io.validation_utils import ValidationError
from pylabrobot.machines.backends.machine import MachineBackend


class LogReader:
  def __init__(self, path: str):
    self.path = path
    self.file = open(path, "r")

  def next_line(self) -> str:
    module = ""
    level = ""
    while not (module.startswith("pylabrobot.io") and level == "IO"):
      line = self.file.readline().strip()
      if line == "":
        raise StopIteration
      if line.count(" - ") < 3:
        continue
      _, module, level, data = line.split(" - ", 3)  # first is datetime
    return data

  def done(self):
    n = 0
    first_line = None
    while not self.file.readline().strip() == "":
      n += 1
      if n == 1:
        first_line = self.file.readline().strip()
    if n > 0:
      raise ValidationError(f"Log file not fully read, {n} lines left. First line: {first_line}")
    self.file.close()
    print("Validation successful!")

  def reset(self):
    if self.file.closed:
      self.file = open(self.path, "r")
    self.file.seek(0)


def validate(log_file: str, backends: Optional[List[MachineBackend]] = None) -> LogReader:
  """Start

  Args:
    log_file: path to log file
    backends: list of backends to validate. If None, all backends will be validated.
  """

  lr = LogReader(log_file)
  for machine_backend in MachineBackend.get_all_instances():
    io2v = {
      USB: USBValidator,
      Serial: SerialValidator,
      FTDI: FTDIValidator,
      HID: HIDValidator,
    }

    # replace io with validator
    if machine_backend.io.__class__ in io2v and (
      backends is not None and machine_backend in backends
    ):
      machine_backend.io = io2v[machine_backend.io.__class__](
        **machine_backend.io.serialize(), lr=lr
      )

  return lr


#  - note that it is not slower, use has full control over what they want to log
#  - start validation
#  - check backends that will be tested
