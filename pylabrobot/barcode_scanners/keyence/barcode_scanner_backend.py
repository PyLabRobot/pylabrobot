import asyncio
from pylabrobot.barcode_scanners.backend import (
  BarcodeScannerBackend,
  BarcodeScannerError,
)

import serial
import time

from typing import Optional
from pylabrobot.io.serial import Serial

class KeyenceBarcodeScannerBackend(BarcodeScannerBackend):
  default_baudrate = 9600
  serial_messaging_encoding = "ascii"
  init_timeout = 1.0  # seconds
  poll_interval = 0.2  # seconds

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
      await asyncio.sleep(self.poll_interval)
    else:
      raise BarcodeScannerError("Failed to initialize Keyence barcode scanner: " \
      "Timeout waiting for motor to turn on.")

  async def send_command(self, command: str) -> str:
    """Send a command to the barcode scanner and return the response.
    Keyence uses carriage return \r as the line ending by default."""

    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    response = await self.io.read()
    return response.decode(self.serial_messaging_encoding).strip()

  async def send_command_and_stream(
    self,
    command: str,
    on_response: callable,
    timeout: float = 5.0,
    stop_condition: Optional[callable] = None
):
    """Send a command and call on_response for each barcode response."""
    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            response = await asyncio.wait_for(self.io.readline(), timeout=1.0)
            if response:
                decoded = response.decode(self.serial_messaging_encoding).strip()
                print(f"Received from barcode scanner: {decoded}")
                if decoded:
                    try:
                        await on_response(decoded)  # Call the callback
                    except Exception as e:
                        print(f"Error in callback: {e}")
                    if stop_condition and stop_condition(decoded):
                        break
        except asyncio.TimeoutError:
            print("Barcode scanner timeout, continuing...")
            continue
        except Exception as e:
            print(f"Error reading from barcode scanner: {e}")
            continue

  async def stop(self):
    await self.io.stop()

  async def scan_barcode(self) -> str:
    return await self.send_command("LON")
