from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend

from .driver import ThermoFisherThermocyclerDriver


class ThermoFisherLidBackend(TemperatureControllerBackend):
  """Temperature control backend for a single thermocycler lid (cover heater)."""

  def __init__(self, driver: ThermoFisherThermocyclerDriver, block_id: int):
    self._driver = driver
    self._block_id = block_id

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    res = await self._driver.send_command(
      {"cmd": f"TBC{self._block_id + 1}:CoverRAMP", "params": {}, "args": [temperature]},
      response_timeout=60,
    )
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp cover temperature")

  async def get_current_temperature(self) -> float:
    res = await self._driver.send_command(
      {"cmd": f"TBC{self._block_id + 1}:TBC:CoverTemperatures?"}
    )
    temps = self._driver._parse_scpi_response(res)["args"]
    return float(temps[0])

  async def deactivate(self):
    await self._driver.set_cover_idle_temp(temp=105, control_enabled=False, block_id=self._block_id)

  # ----- Additional public methods -----

  async def set_cover_idle_temp(self, temp: float, control_enabled: bool = True) -> None:
    await self._driver.set_cover_idle_temp(
      temp=temp, block_id=self._block_id, control_enabled=control_enabled
    )
