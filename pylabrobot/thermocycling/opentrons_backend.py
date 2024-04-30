import sys
from typing import cast, Optional

from pylabrobot.thermocycling.backend import ThermocyclerBackend

PYTHON_VERSION = sys.version_info[:2]

if PYTHON_VERSION == (3, 10):
  try:
    import ot_api
    USE_OT = True
  except ImportError:
    USE_OT = False
else:
  USE_OT = False


class OpentronsThermocyclerModuleBackend(ThermocyclerBackend):
  """ Opentrons thermocycler module backend. """

  def __init__(self, opentrons_id: str):
    """ Create a new Opentrons thermocycler module backend.

    Args:
      opentrons_id: Opentrons ID of the thermocycler module. Get it from
        `OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()`.
    """
    self.opentrons_id = opentrons_id

    if not USE_OT:
      raise RuntimeError("Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
                         " Only supported on Python 3.10.")

  async def setup(self):
    await super().setup()

  async def stop(self):
    await self.deactivate()
    await super().stop()

  async def open_lid(self):
    ot_api.modules.thermocycler_open_lid(module_id=self.opentrons_id)

  async def close_lid(self):
    ot_api.modules.thermocycler_close_lid(module_id=self.opentrons_id)

  async def get_lid_status(self):
    modules = ot_api.modules.list_connected_modules()
    for module in modules:
      if module["id"] == self.opentrons_id:
        return cast(float, module["data"]["lidStatus"])
    raise RuntimeError(f"Module with id '{self.opentrons_id}' not found")

  async def set_temperature(self, temperature: float):
    """ Acceptable target temperatures are between 37 and 110 °C. """
    ot_api.modules.thermocycler_set_lid_temperature(celsius=temperature,
                                                    module_id=self.opentrons_id)
    ot_api.modules.thermocycler_set_block_temperature(celsius=temperature,
                                                      module_id=self.opentrons_id)

  async  def set_lid_temperature(self, temperature: float):
    """ Acceptable target temperatures are between 37 and 110 °C. """
    ot_api.modules.thermocycler_set_lid_temperature(celsius=temperature,
                                                    module_id=self.opentrons_id)

  async  def set_block_temperature(self, temperature: float):
    """ Acceptable target temperatures are between 37 and 110 °C. """
    ot_api.modules.thermocycler_set_block_temperature(celsius=temperature,
                                                      module_id=self.opentrons_id)

  async  def get_temperature(self):
    modules = ot_api.modules.list_connected_modules()
    for module in modules:
      if module["id"] == self.opentrons_id:
        return cast(float, module["data"]["currentTemperature"])
    raise RuntimeError(f"Module with id '{self.opentrons_id}' not found")

  async  def get_lid_temperature(self):
    modules = ot_api.modules.list_connected_modules()
    for module in modules:
      if module["id"] == self.opentrons_id:
        return cast(float, module["data"]["lidTemperature"])
    raise RuntimeError(f"Module with id '{self.opentrons_id}' not found")

  async  def get_block_temperature(self):
    raise NotImplementedError(f"Block temperature data not available for Opentrons")

  async  def deactivate_lid(self):
    ot_api.modules.thermocycler_deactivate_lid(module_id=self.opentrons_id)

  async  def deactivate_block(self):
    ot_api.modules.thermocycler_deactivate_block(module_id=self.opentrons_id)

  async  def deactivate(self):
    ot_api.modules.thermocycler_deactivate_lid(module_id=self.opentrons_id)
    ot_api.modules.thermocycler_deactivate_block(module_id=self.opentrons_id)

  async def run_profile(self, profile: list, block_max_volume: Optional[float] = None):
    # Transform the profile to use the key names required by the Opentrons HTTP API
    transformed_profile = [
      {"celsius": step["temperature"], "holdSeconds": step["duration"]}
      for step in profile
    ]

    if block_max_volume is None:
      block_max_volume = 25  # Opentrons default block_max_volume is 25uL
      # https://docs.opentrons.com/v2/modules/thermocycler.html#thermocycler-profiles
    ot_api.modules.thermocycler_run_profile(profile=transformed_profile,
                                            block_max_volume=block_max_volume,
                                            module_id=self.opentrons_id)
