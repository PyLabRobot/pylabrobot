from typing import Optional

from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.device import Driver
from pylabrobot.io.serial import Serial


class OpentronsTemperatureModuleUSBDriver(Driver):
  """Driver for the Opentrons Temperature Module v2 via direct USB serial.

  Owns the ``Serial`` connection and its lifecycle.
  """

  def __init__(self, port: str):
    super().__init__()
    self.port = port
    self._serial: Optional[Serial] = None

  @property
  def serial(self) -> Serial:
    if self._serial is None:
      raise RuntimeError("Serial device not initialized. Call setup() first.")
    return self._serial

  async def setup(self):
    self._serial = Serial(
      human_readable_device_name="Opentrons Temperature Module",
      port=self.port,
      baudrate=115200,
      timeout=3,
    )
    await self._serial.setup()

  async def stop(self):
    if self._serial is not None:
      await self._serial.stop()
      self._serial = None

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.port}

  async def send_and_check(self, command: bytes):
    """Send a command and verify the two-line 'ok' acknowledgement."""
    await self.serial.write(command)
    response1 = await self.serial.readline()
    response2 = await self.serial.readline()
    if b"ok" not in response1 or b"ok" not in response2:
      raise RuntimeError(
        f"Unexpected response from device: {response1.decode(encoding='utf-8')} "
        f"{response2.decode(encoding='utf-8')}"
      )

  async def query_temperature(self) -> float:
    """Send M105 and parse the temperature from the response."""
    await self.serial.write(b"M105\r\n")
    response = await self.serial.readline()
    if b"C" not in response:
      raise ValueError(f"Unexpected response from device: {response.decode(encoding='utf-8')}")

    response1 = await self.serial.readline()
    response2 = await self.serial.readline()
    if b"ok" not in response1 or b"ok" not in response2:
      raise RuntimeError(
        f"Unexpected response from device: {response1.decode(encoding='utf-8')} "
        f"{response2.decode(encoding='utf-8')}"
      )
    return float(response.strip().split(b"C:")[-1])


class OpentronsTemperatureModuleUSBTemperatureBackend(TemperatureControllerBackend):
  """Translates ``TemperatureControllerBackend`` into USB serial driver commands."""

  def __init__(self, driver: OpentronsTemperatureModuleUSBDriver):
    self._driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def set_temperature(self, temperature: float):
    tmp_message = f"M104 S{temperature}\r\n"
    await self._driver.send_and_check(tmp_message.encode("utf-8"))

  async def deactivate(self):
    await self._driver.send_and_check(b"M18\r\n")

  async def request_current_temperature(self) -> float:
    return await self._driver.query_temperature()
