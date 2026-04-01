from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.device import Driver


class HamiltonHeaterCoolerDriver(Driver):
  """Serial driver for Hamilton Heater Cooler (HHC) via STAR TCC port.

  The HHC connects to the STAR liquid handler via RS-232 through TCC ports.
  Communication uses the STAR firmware command protocol with module addressing.

  TODO: implement TCC serial communication. See legacy STAR_backend.py methods
  (initialize_hhc, start_temperature_control_at_hhc, get_temperature_at_hhc,
  stop_temperature_control_at_hhc) for the command protocol.
  """

  def __init__(self, device_number: int):
    super().__init__()
    self.device_number = device_number

  async def setup(self):
    raise NotImplementedError("HamiltonHeaterCoolerDriver is not yet implemented.")

  async def stop(self):
    raise NotImplementedError("HamiltonHeaterCoolerDriver is not yet implemented.")


class HamiltonHeaterCoolerTemperatureBackend(TemperatureControllerBackend):
  """Translates TemperatureControllerBackend calls into HHC serial commands."""

  def __init__(self, driver: HamiltonHeaterCoolerDriver):
    self.driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def set_temperature(self, temperature: float):
    raise NotImplementedError("HamiltonHeaterCoolerTemperatureBackend is not yet implemented.")

  async def request_current_temperature(self) -> float:
    raise NotImplementedError("HamiltonHeaterCoolerTemperatureBackend is not yet implemented.")

  async def deactivate(self):
    raise NotImplementedError("HamiltonHeaterCoolerTemperatureBackend is not yet implemented.")
