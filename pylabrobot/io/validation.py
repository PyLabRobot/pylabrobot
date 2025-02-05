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


def validate(log_file: str) -> LogReader:
  lr = LogReader(log_file)
  for machine_backend in MachineBackend.get_all_instances():
    io2v = {
      USB: USBValidator,
      Serial: SerialValidator,
    }

    # replace io with validator
    if machine_backend.io.__class__ in io2v:
      machine_backend.io = io2v[machine_backend.io.__class__](
        **machine_backend.io.serialize(), lr=lr
      )

  return lr
