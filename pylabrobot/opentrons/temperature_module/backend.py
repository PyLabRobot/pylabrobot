from typing import Optional, cast

from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.device import Driver
from pylabrobot.io.serial import Serial

try:
  import ot_api

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e


class OpentronsTemperatureModuleBackend(TemperatureControllerBackend, Driver):
  """Backend for the Opentrons Temperature Module v2 via the Opentrons HTTP API."""

  @property
  def supports_active_cooling(self) -> bool:
    return False

  def __init__(self, opentrons_id: str):
    """Create a new Opentrons temperature module backend.

    Args:
      opentrons_id: Opentrons ID of the temperature module. Get it from
        ``OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()``.
    """
    self.opentrons_id = opentrons_id

    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
      )

  async def setup(self):
    pass

  async def stop(self):
    await self.deactivate()

  def serialize(self) -> dict:
    return {**super().serialize(), "opentrons_id": self.opentrons_id}

  async def set_temperature(self, temperature: float):
    ot_api.modules.temperature_module_set_temperature(
      celsius=temperature, module_id=self.opentrons_id
    )

  async def deactivate(self):
    ot_api.modules.temperature_module_deactivate(module_id=self.opentrons_id)

  async def get_current_temperature(self) -> float:
    modules = ot_api.modules.list_connected_modules()
    for module in modules:
      if module["id"] == self.opentrons_id:
        return cast(float, module["data"]["currentTemperature"])
    raise RuntimeError(f"Module with id '{self.opentrons_id}' not found")


class OpentronsTemperatureModuleUSBBackend(TemperatureControllerBackend, Driver):
  """Backend for the Opentrons Temperature Module v2 via direct USB serial."""

  @property
  def supports_active_cooling(self) -> bool:
    return True

  def __init__(self, port: str):
    """Create a new Opentrons temperature module USB backend.

    Args:
      port: Serial port for USB communication.
    """
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
    await self.deactivate()
    if self._serial is not None:
      await self._serial.stop()
      self._serial = None

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.port}

  async def set_temperature(self, temperature: float):
    tmp_message = f"M104 S{temperature}\r\n"
    await self.serial.write(tmp_message.encode("utf-8"))
    response1 = await self.serial.readline()
    response2 = await self.serial.readline()
    if b"ok" not in response1 or b"ok" not in response2:
      raise RuntimeError(
        f"Unexpected response from device: {response1.decode(encoding='utf-8')} "
        f"{response2.decode(encoding='utf-8')}"
      )

  async def deactivate(self):
    await self.serial.write(b"M18\r\n")
    response1 = await self.serial.readline()
    response2 = await self.serial.readline()
    if b"ok" not in response1 or b"ok" not in response2:
      raise RuntimeError(
        f"Unexpected response from device: {response1.decode(encoding='utf-8')} "
        f"{response2.decode(encoding='utf-8')}"
      )

  async def get_current_temperature(self) -> float:
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
