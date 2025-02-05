from pylabrobot.io.io import IOBase


class Serial(IOBase):
  def __init__(self, port: str, baudrate: int) -> None:
    self.port = port
    self.baudrate = baudrate

  def write(self, data: bytes, timeout=None) -> bytes:
    pass

  def read(self, timeout=None) -> bytes:
    pass


class SerialValidator(Serial):
  pass
