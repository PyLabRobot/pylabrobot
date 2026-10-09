from functools import partial
from typing import cast

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.temperature_controlling.backend import (
  TemperatureControllerBackend,
)

try:
  import ot_api

  USE_OT = True
except ImportError as e:
  USE_OT = False
  _OT_IMPORT_ERROR = e


class OpentronsTemperatureModuleBackend(TemperatureControllerBackend):
  """Opentrons temperature module backend."""

  @property
  def supports_active_cooling(self) -> bool:
    return False

  def __init__(self, opentrons_id: str):
    """Create a new Opentrons temperature module backend.

    Args:
      opentrons_id: Opentrons ID of the temperature module. Get it from
        `OpentronsBackend(host="x.x.x.x", port=31950).list_connected_modules()`.
    """
    super().__init__()
    self.opentrons_id = opentrons_id

    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
      )

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    stack.push_shielded_async_callback(self.deactivate)

  def serialize(self) -> dict:
    return {**super().serialize(), "opentrons_id": self.opentrons_id}

  async def set_temperature(self, temperature: float):
    celsius = temperature
    module_id = self.opentrons_id
    set_temperature = partial(
      ot_api.modules.temperature_module_set_temperature,
      celsius=celsius,
      module_id=module_id,
    )
    await anyio.to_thread.run_sync(set_temperature, abandon_on_cancel=False)

  async def deactivate(self):
    module_id = self.opentrons_id
    deactivate = partial(ot_api.modules.temperature_module_deactivate, module_id=module_id)
    await anyio.to_thread.run_sync(deactivate, abandon_on_cancel=False)

  async def get_current_temperature(self) -> float:
    list_connected_modules = ot_api.modules.list_connected_modules
    modules = await anyio.to_thread.run_sync(list_connected_modules, abandon_on_cancel=False)
    for module in modules:
      if module["id"] == self.opentrons_id:
        return cast(float, module["data"]["currentTemperature"])
    raise RuntimeError(f"Module with id '{self.opentrons_id}' not found")
