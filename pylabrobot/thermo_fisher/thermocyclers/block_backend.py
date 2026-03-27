from typing import List

from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend

from .driver import ThermoFisherThermocyclerDriver


class ThermoFisherBlockBackend(TemperatureControllerBackend):
  """Temperature control backend for a single thermocycler block."""

  def __init__(self, driver: ThermoFisherThermocyclerDriver, block_id: int):
    self._driver = driver
    self._block_id = block_id

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def set_temperature(self, temperature: float):
    temps = [temperature] * self._driver.num_temp_zones
    await self._driver.send_command(
      {"cmd": f"TBC{self._block_id + 1}:RAMP", "params": {"rate": 100}, "args": temps},
      response_timeout=60,
    )

  async def get_current_temperature(self) -> float:
    res = await self._driver.send_command(
      {"cmd": f"TBC{self._block_id + 1}:TBC:BlockTemperatures?"}
    )
    temps = self._driver._parse_scpi_response(res)["args"]
    return float(temps[0])

  async def deactivate(self):
    await self._driver.set_block_idle_temp(temp=25, control_enabled=False, block_id=self._block_id)

  # ----- Additional public methods -----

  async def set_block_idle_temp(self, temp: float, control_enabled: bool = True) -> None:
    await self._driver.set_block_idle_temp(
      temp=temp, block_id=self._block_id, control_enabled=control_enabled
    )

  async def get_sample_temps(self) -> List[float]:
    res = await self._driver.send_command(
      {"cmd": f"TBC{self._block_id + 1}:TBC:SampleTemperatures?"}
    )
    from typing import cast

    return cast(List[float], self._driver._parse_scpi_response(res)["args"])

  async def block_ramp_single_temp(self, target_temp: float, rate: float = 100):
    """Set a single temperature for the block with a ramp rate.

    It might be better to use ``set_temperature`` to set individual temperatures for each
    zone.
    """
    if self._block_id not in self._driver.available_blocks:
      raise ValueError(f"Block {self._block_id} not available")
    res = await self._driver.send_command(
      {
        "cmd": f"TBC{self._block_id + 1}:BlockRAMP",
        "params": {"rate": rate},
        "args": [target_temp],
      },
      response_timeout=60,
    )
    if self._driver._parse_scpi_response(res)["status"] != "OK":
      raise ValueError("Failed to ramp block temperature")
