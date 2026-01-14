from pylabrobot.barcode_scanners.backend import (
  BarcodeScannerBackend,
  BarcodeScannerError,
)

import serial
import time

from pylabrobot.io.serial import Serial

class KeyenceBarcodeScannerBackend(BarcodeScannerBackend):
  default_baudrate = 9600
  serial_messaging_encoding = "ascii"
  init_timeout = 1.0  # seconds

  def __init__(self, serial_port: str,):
    super().__init__()

    # BL-1300 Barcode reader factory default serial communication settings
    # should be the same factory default for the BL-600HA and BL-1300 models
    self.io = Serial(
      port=serial_port,
      baudrate=self.default_baudrate,
      bytesize=serial.SEVENBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=1,
      timeout=1,
      rtscts=False,
    )

  async def setup(self):
    await self.io.setup()
    await self.initialize_scanner()

  async def initialize_scanner(self):
    """Initialize the Keyence barcode scanner."""

    response = await self.send_command("RMOTOR")

    deadline = time.time() + self.init_timeout
    while time.time() < deadline:
      response = await self.send_command("RMOTOR")
      if response.strip() == "MOTORON":
        print("Barcode scanner motor is ON.")
        break
      elif response.strip() == "MOTOROFF":
        raise BarcodeScannerError("Failed to initialize Keyence barcode scanner: Motor is off.")
    else:
      raise BarcodeScannerError("Failed to initialize Keyence barcode scanner: " \
      "Timeout waiting for motor to turn on.")

  async def send_command(self, command: str) -> str:
    """Send a command to the barcode scanner and return the response.
    Keyence uses carriage return \r as the line ending by default."""

    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    response = await self.io.readline()
    return response.decode(self.serial_messaging_encoding).strip()

  async def stop(self):
    await self.io.stop()

  async def scan_barcode(self) -> str:
    return await self.send_command("LON")
