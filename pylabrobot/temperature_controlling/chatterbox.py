from pylabrobot.temperature_controlling.backend import TemperatureControllerBackend


class TemperatureControllerChatterboxBackend(TemperatureControllerBackend):
  """ Chatter box backend for device-free testing. Prints out all operations. """

  def __init__(self, dummy_temperature: float = 0.0) -> None:
    self._dummy_temperature = dummy_temperature

  async def setup(self):
    print("Setting up the temperature controller.")

  async def stop(self):
    print("Stopping the temperature controller.")

  async def set_temperature(self, temperature: float):
    print(f"Setting the temperature to {temperature}.")
    self._dummy_temperature = temperature

  async def get_current_temperature(self) -> float:
    print("Getting the current temperature.")
    return self._dummy_temperature

  async def deactivate(self):
    print("Deactivating the temperature controller.")
