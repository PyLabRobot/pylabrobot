from typing import Optional

from pylabrobot.io.capture import CaptureReader, capturer
from pylabrobot.io.ftdi import FTDI, FTDIValidator
from pylabrobot.io.hid import HID, HIDValidator
from pylabrobot.io.serial import Serial, SerialValidator
from pylabrobot.io.tcp import TCP, TCPValidator
from pylabrobot.io.usb import USB, USBValidator
from pylabrobot.machines.backend import MachineBackend

cr: Optional[CaptureReader] = None


def validate(capture_file: str):
  """Start validation against a capture file.

  Args:
    capture_file: path to the capture file. Generate with start_capture.
  """

  if capturer.capture_active:
    raise RuntimeError("Cannot validate while capture is active")

  global cr
  cr = CaptureReader(path=capture_file)

  def _replace_io(obj):
    io2v = {
      USB: USBValidator,
      Serial: SerialValidator,
      FTDI: FTDIValidator,
      HID: HIDValidator,
      TCP: TCPValidator,
    }
    if not hasattr(obj, "io"):
      return False
    if obj.io.__class__ in io2v:
      obj.io = io2v[obj.io.__class__](**obj.io.serialize(), cr=cr)
    elif obj.io.__class__ in io2v.values():
      obj.io.cr = cr
    else:
      return False
    return True

  for machine_backend in MachineBackend.get_all_instances():
    if not (
      (hasattr(machine_backend, "io") and _replace_io(machine_backend))
      or (hasattr(machine_backend, "interface") and _replace_io(machine_backend.interface))
    ):
      raise RuntimeError(f"Backend {machine_backend} not supported for validation")

  cr.start()


def end_validation():
  if cr is None:
    raise RuntimeError("Validation not started")
  cr.done()
