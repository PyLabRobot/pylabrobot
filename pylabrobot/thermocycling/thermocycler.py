import asyncio
import time
from typing import Optional

from pylabrobot.machines.machine import Machine

from .backend import ThermocyclerBackend

class Thermocycler(Machine):
  """ Thermocycler. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ThermocyclerBackend,
    category: str = "thermocycler",
    model: Optional[str] = None
  ):
    super().__init__(name, size_x, size_y, size_z, backend, category, model)
    self.backend: ThermocyclerBackend = backend  # fix type
    self.target_temperature: Optional[float] = None

  async def setup(self):
    """ Setup the thermocycler. """
    return await self.backend.setup()

  async def stop(self):
    """ Stop the thermocycler. """
    return await self.backend.stop()

  async def open_lid(self):
    """ Open lid of the thermocycler. """
    return await self.backend.open_lid()

  async def close_lid(self):
    """ Close lid of the thermocycler. """
    return await self.backend.close_lid()

  async def get_lid_status(self):
    """ Get status of lid on the thermocycler (open or closed). """
    return await self.backend.get_lid_status()

  async def set_temperature(self, temperature: float):
    """ Set the temperature of the thermocycler in Celsius.

    Args:
      temperature: Temperature in Celsius.
    """
    self.target_temperature = temperature
    return await self.backend.set_temperature(temperature)

  async def set_lid_temperature(self, temperature: float):
    """ Set the lid temperature of the thermocycler in Celsius.

    Args:
      temperature: Temperature in Celsius.
    """
    self.target_lid_temperature = temperature
    return await self.backend.set_lid_temperature(temperature)

  async def set_block_temperature(self, temperature: float):
    """ Set the block temperature of the thermocycler in Celsius.

    Args:
      temperature: Temperature in Celsius.
    """
    self.target_block_temperature = temperature
    return await self.backend.set_block_temperature(temperature)

  async def wait_for_lid_temperature(self, timeout: float = 300.0, tolerance: float = 0.5):
    """ Wait for the lid temperature to reach the target temperature. The target temperature must be
    set by `set_lid_temperature()`.

    Args:
      timeout: Timeout in seconds.
      tolerance: Tolerance in Celsius.
    """
    if self.target_lid_temperature is None:
      raise RuntimeError("Target temperature is not set.")
    start = time.time()
    while time.time() - start < timeout:
      temperature = await self.get_lid_temperature()
      if abs(temperature - self.target_temperature) < tolerance:
        return
      await asyncio.sleep(1.0)
    raise TimeoutError(f"Temperature did not reach target temperature within {timeout} seconds.")

  async def wait_for_block_temperature(self, timeout: float = 300.0, tolerance: float = 0.5):
    """ Wait for the block temperature to reach the target temperature. The target temperature must be
    set by `set_block_temperature()`.

    Args:
      timeout: Timeout in seconds.
      tolerance: Tolerance in Celsius.
    """
    if self.target_block_temperature is None:
      raise RuntimeError("Target temperature is not set.")
    start = time.time()
    while time.time() - start < timeout:
      temperature = await self.get_lid_temperature()
      if abs(temperature - self.target_temperature) < tolerance:
        return
      await asyncio.sleep(1.0)
    raise TimeoutError(f"Temperature did not reach target temperature within {timeout} seconds.")

  async def wait_for_temperature(self, timeout: float = 300.0, tolerance: float = 0.5):
    """ Wait for the temperature to reach the target temperature. The target temperature must be
      set by `set_temperature()`.

      Args:
        timeout: Timeout in seconds.
        tolerance: Tolerance in Celsius.
      """
    if self.target_temperature is None:
      raise RuntimeError("Target temperature is not set.")
    start = time.time()
    while time.time() - start < timeout:
      temperature = await self.get_temperature()
      if abs(temperature - self.target_temperature) < tolerance:
        return
      await asyncio.sleep(1.0)
    raise TimeoutError(f"Temperature did not reach target temperature within {timeout} seconds.")

  async def get_temperature(self) -> float:
    """ Get the current temperature of the thermocycler in Celsius.

    Returns:
      Temperature in Celsius.
    """
    return await self.backend.get_temperature()

  async def get_lid_temperature(self) -> float:
    """ Get the current lid temperature of the thermocycler in Celsius.

    Returns:
      Temperature in Celsius.
    """
    return await self.backend.get_lid_temperature()

  async def get_block_temperature(self) -> float:
    """ Get the current block temperature of the thermocycler in Celsius.

    Returns:
      Temperature in Celsius.
    """
    return await self.backend.get_block_temperature()

  async def deactivate_lid(self):
    """ Deactivate the lid of the thermocycler (turns off heat). This will stop the heating
    or cooling of the thermocycler lid, and return the temperature to ambient temperature.
    The lid target temperature will be reset to `None`."""
    self.target_lid_temperature = None
    return await self.backend.deactivate_lid()

  async def deactivate_block(self):
    """ Deactivate the block of the thermocycler (turns off heat). This will stop the heating
    or cooling of the thermocycler block, and return the temperature to ambient temperature.
    The block target temperature will be reset to `None`."""
    self.target_block_temperature = None
    return await self.backend.deactivate_block()

  async def deactivate(self):
    """ Deactivate the thermocycler. This will stop the heating or cooling, and return
    the temperature to ambient temperature. The target temperature will be reset to `None`.
    """
    self.target_temperature = None
    self.target_block_temperature = None
    self.target_lid_temperature = None
    return await self.backend.deactivate()

async def run_profile(self, profile: list, block_max_volume: Optional[float]=None):
    """ Run a profile on the thermocycler. A profile is a list of dictionaries with items `temperature` and `duration`
    that specify individual steps inspired from the Opentrons python API:
    https://docs.opentrons.com/v2/modules/thermocycler.html#thermocycler-profiles

    Args:
      profile: A list of steps with keys `temperature` in Celsius and `duration` in seconds. For example:
        profile = [
            {"temperature":95, "duration":30},
            {"temperature":57, "duration":30},
            {"temperature":72, "duration":60}
          ]
      block_max_volume (optional): Amount of liquid in uL of the most-full well in labware loaded onto the thermocycler.
    """
    return await self.backend.run_profile(profile, block_max_volume)