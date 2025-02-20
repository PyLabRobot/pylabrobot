from typing import Optional

from pylabrobot.io.capture import CaptureReader, capturer
from pylabrobot.io.ftdi import FTDI, FTDIValidator
from pylabrobot.io.hid import HID, HIDValidator
from pylabrobot.io.serial import Serial, SerialValidator
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
  for machine_backend in MachineBackend.get_all_instances():
    io2v = {
      USB: USBValidator,
      Serial: SerialValidator,
      FTDI: FTDIValidator,
      HID: HIDValidator,
    }

    # replace `io` with validator variant
    if machine_backend.io.__class__ in io2v:
      machine_backend.io = io2v[machine_backend.io.__class__](
        **machine_backend.io.serialize(), cr=cr
      )
    elif machine_backend.io.__class__ in io2v.values():
      machine_backend.io.cr = cr
    else:
      raise RuntimeError(f"Backend {machine_backend} not supported for validation")


def end_validation():
  if cr is None:
    raise RuntimeError("Validation not started")
  cr.done()
