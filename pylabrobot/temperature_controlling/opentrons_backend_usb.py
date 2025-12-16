from typing import Optional

from pylabrobot.io.serial import Serial
from pylabrobot.temperature_controlling.backend import (
  TemperatureControllerBackend,
)


class OpentronsTemperatureModuleUSBBackend(TemperatureControllerBackend):
  """Opentrons temperature module backend."""

  @property
  def supports_active_cooling(self) -> bool:
    return True

  def __init__(self, port: str):
    """Create a new Opentrons temperature module backend.

    Args:
      port: Serial port for USB communication.
    """

    self.port = port
    self._serial: Optional["Serial"] = None

  @property
  def serial(self) -> "Serial":
    if self._serial is None:
      raise RuntimeError("Serial device not initialized. Call setup() first.")
    return self._serial

  async def setup(self):
    # Setup serial communication for USB
    self._serial = Serial(port=self.port, baudrate=115200, timeout=3)
    await self._serial.setup()

  async def stop(self):
    await self.deactivate()
    if self._serial is not None:
      await self._serial.stop()
      self._serial = None

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.port}

  async def set_temperature(self, temperature: float):
    tmp_message = f"M104 S{temperature}\r\n"
    await self.serial.write(tmp_message.encode("utf-8"))
    # Read response (should be "ok\r\nok\r\n")
    response1 = await self.serial.readline()
    response2 = await self.serial.readline()
    # Verify we got the expected response
    if b"ok" not in response1 or b"ok" not in response2:
      raise RuntimeError(
        f"Unexpected response from device: {response1.decode(encoding='utf-8')} {response2.decode(encoding='utf-8')}"
      )

  async def deactivate(self):
    await self.serial.write(b"M18\r\n")
    # Read response (should be "ok\r\nok\r\n")
    response1 = await self.serial.readline()
    response2 = await self.serial.readline()
    # Verify we got the expected response
    if b"ok" not in response1 or b"ok" not in response2:
      raise RuntimeError(
        f"Unexpected response from device: {response1.decode(encoding='utf-8')} {response2.decode(encoding='utf-8')}"
      )

  async def get_current_temperature(self) -> float:
    await self.serial.write(b"M105\r\n")
    # Read response (should be "T:XX.XXX C:XX.XXX\r\nok\r\nok\r\n")
    response = await self.serial.readline()
    # Verify we got the expected response
    # Read response (should be "ok\r\nok\r\n")
    if b"C" not in response:
      raise ValueError(f"Unexpected response from device: {response.decode(encoding='utf-8')}")

    response1 = await self.serial.readline()
    response2 = await self.serial.readline()
    if b"ok" not in response1 or b"ok" not in response2:
      raise RuntimeError(
        f"Unexpected response from device: {response1.decode(encoding='utf-8')} {response2.decode(encoding='utf-8')}"
      )
    return float(response.strip().split(b"C:")[-1])
