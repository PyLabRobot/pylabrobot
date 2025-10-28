import sys
from typing import Optional, cast

from pylabrobot.temperature_controlling.backend import (
  TemperatureControllerBackend,
)

PYTHON_VERSION = sys.version_info[:2]

if PYTHON_VERSION == (3, 10):
  try:
    import ot_api

    USE_OT = True
  except ImportError as e:
    USE_OT = False
    _OT_IMPORT_ERROR = e
else:
  USE_OT = False

# Import serial for USB communication when USE_OT is False
try:
  from pylabrobot.io.serial import Serial

  HAS_SERIAL = True
except ImportError:
  HAS_SERIAL = False


class OpentronsTemperatureModuleBackend(TemperatureControllerBackend):
  """Opentrons temperature module backend."""

  @property
  def supports_active_cooling(self) -> bool:
    return True

  def __init__(
    self,
    opentrons_id: str,
    port: Optional[str] = None,
  ):
    """Create a new Opentrons temperature module backend.

    Args:
      opentrons_id: Opentrons ID of the temperature module. Get it from
        `OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()`.
        If USE_OT is False, this can be any identifier.
      port: Serial port for USB communication. Required when USE_OT is False.
    """
    self.opentrons_id = opentrons_id
    self.port = port
    self.serial: Optional["Serial"] = None

    if not USE_OT and not HAS_SERIAL:
      raise RuntimeError(
        "Neither Opentrons API nor pyserial is installed. "
        "Please run pip install pylabrobot[opentrons] or pip install pyserial."
      )

  async def setup(self):
    if USE_OT:
      # No setup needed for opentrons API
      pass
    else:
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
    return {**super().serialize(), "opentrons_id": self.opentrons_id, "port": self.port}

  async def set_temperature(self, temperature: float):
    if USE_OT:
      ot_api.modules.temperature_module_set_temperature(
        celsius=temperature, module_id=self.opentrons_id
      )
    else:
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
    if USE_OT:
      ot_api.modules.temperature_module_deactivate(module_id=self.opentrons_id)
    else:
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
    if USE_OT:
      modules = ot_api.modules.list_connected_modules()
      for module in modules:
        if module["id"] == self.opentrons_id:
          return cast(float, module["data"]["currentTemperature"])
      raise RuntimeError(f"Module with id '{self.opentrons_id}' not found")
    else:
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
