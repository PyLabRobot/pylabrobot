from typing import Optional

from pylabrobot.temperature_controlling.backend import (
  TemperatureControllerBackend,
)

# Import serial for USB communication
from pylabrobot.io.serial import Serial


class OpentronsTemperatureModuleUSBBackend(TemperatureControllerBackend):
  """Opentrons temperature module backend."""

  @property
  def supports_active_cooling(self) -> bool:
    return True

  def __init__(
    self,
    port: Optional[str] = None,
  ):
    """Create a new Opentrons temperature module backend.

    Args:
      port: Serial port for USB communication. Required when USE_OT is False.
    """

    self.port = port
    self.serial: Optional["Serial"] = None

  async def setup(self):
      # Setup serial communication for USB
      if self.port is None:
        raise RuntimeError("Serial port must be specified when USE_OT is False.")
      self.serial = Serial(port=self.port, baudrate=115200, timeout=3)
      await self.serial.setup()

  async def stop(self):
    await self.deactivate()
    if self.serial is not None:
      await self.serial.stop()
      self.serial = None

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.port}

  async def set_temperature(self, temperature: float):
      # Send M104 command over serial to set temperature
      if self.serial is None:
        raise RuntimeError("Serial device not initialized. Call setup() first.")
      # Send M104 SXXX\r\n command
      tmp_message = f"M104 S{temperature}\r\n"
      await self.serial.write(tmp_message.encode('utf-8'))
      # Read response (should be "ok\r\nok\r\n")
      response1 = await self.serial.readline()
      response2 = await self.serial.readline()
      # Verify we got the expected response
      if b"ok" not in response1 or b"ok" not in response2:
        raise RuntimeError(f"Unexpected response from device: {response1} {response2}")

  async def deactivate(self):
      # Send M18 command over serial to stop holding temperature
      if self.serial is None:
        raise RuntimeError("Serial device not initialized. Call setup() first.")
      # Send M18\r\n command
      await self.serial.write(b"M18\r\n")
      # Read response (should be "ok\r\nok\r\n")
      response1 = await self.serial.readline()
      response2 = await self.serial.readline()
      # Verify we got the expected response
      if b"ok" not in response1 or b"ok" not in response2:
        raise RuntimeError(f"Unexpected response from device: {response1} {response2}")

  async def get_current_temperature(self) -> float:
      # Send M105 command over serial to query temperature
      if self.serial is None:
        raise RuntimeError("Serial device not initialized. Call setup() first.")
      # Send M105\r\n command
      await self.serial.write(b"M105\r\n")
      # Read response (should be "T:XX.XXX C:XX.XXX\r\nok\r\nok\r\n")
      response = await self.serial.readline()
      # Verify we got the expected response
      if b"C" in response:
        # Read response (should be "ok\r\nok\r\n")
        response1 = await self.serial.readline()
        response2 = await self.serial.readline()
        # Verify we got the expected response
        if b"ok" not in response1 or b"ok" not in response2:
          raise RuntimeError(f"Unexpected response from device: {response1} {response2}")
        return float(response.strip().split(b"C:")[-1])
      else:
        raise ValueError(f"Unexpected response from device: {response}")
