"""Backend that drives an Opentrons Thermocycler via the HTTP API."""

import sys
from typing import cast

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Step

# Only supported on Python 3.10 with the OT-API HTTP client installed
PYTHON_VERSION = sys.version_info[:2]

if PYTHON_VERSION == (3, 10):
  try:
    from ot_api.modules import (
      list_connected_modules,
      thermocycler_close_lid,
      thermocycler_deactivate_block,
      thermocycler_deactivate_lid,
      thermocycler_open_lid,
      thermocycler_run_profile_no_wait,
      thermocycler_set_block_temperature,
      thermocycler_set_lid_temperature,
    )

    USE_OT = True
  except ImportError as e:
    USE_OT = False
    _OT_IMPORT_ERROR = e
else:
  USE_OT = False


class OpentronsThermocyclerBackend(ThermocyclerBackend):
  """HTTP-API backend for the Opentrons GEN-1/GEN-2 Thermocycler.

  All core functions are supported. run_profile() is fire-and-forget,
  since PCR runs can outlive the decorator's default timeout.
  """

  def __init__(self, opentrons_id: str):
    """Create a new backend bound to a specific thermocycler.

    Args:
      opentrons_id: The OT-API module "id" for your thermocycler.
    """
    super().__init__()  # Call parent constructor
    if not USE_OT:
      raise RuntimeError(
        "Opentrons is not installed. Please run pip install pylabrobot[opentrons]."
        f" Import error: {_OT_IMPORT_ERROR}."
        " Only supported on Python 3.10 and below."
      )
    self.opentrons_id = opentrons_id

  async def setup(self):
    """No extra setup needed for HTTP-API thermocycler."""

  async def stop(self):
    """Gracefully deactivate both heaters."""
    await self.deactivate_block()
    await self.deactivate_lid()

  def serialize(self) -> dict:
    """Include the Opentrons module ID in serialized state."""
    return {**super().serialize(), "opentrons_id": self.opentrons_id}

  async def open_lid(self):
    """Open the thermocycler lid."""
    return thermocycler_open_lid(module_id=self.opentrons_id)

  async def close_lid(self):
    """Close the thermocycler lid."""
    return thermocycler_close_lid(module_id=self.opentrons_id)

  async def set_block_temperature(self, temperature: float):
    """Set block temperature in °C."""
    return thermocycler_set_block_temperature(celsius=temperature, module_id=self.opentrons_id)

  async def set_lid_temperature(self, temperature: float):
    """Set lid temperature in °C."""
    return thermocycler_set_lid_temperature(celsius=temperature, module_id=self.opentrons_id)

  async def deactivate_block(self):
    """Deactivate the block heater."""
    return thermocycler_deactivate_block(module_id=self.opentrons_id)

  async def deactivate_lid(self):
    """Deactivate the lid heater."""
    return thermocycler_deactivate_lid(module_id=self.opentrons_id)

  async def run_profile(self, profile: list[Step], block_max_volume: float):
    """Enqueue and return immediately (no wait) the PCR profile command."""
    # in opentrons, the "celsius" key is used instead of "temperature"
    ot_profile = [
      {"celsius": step.temperature, "holdSeconds": step.hold_seconds} for step in profile
    ]
    return thermocycler_run_profile_no_wait(
      profile=ot_profile,
      block_max_volume=block_max_volume,
      module_id=self.opentrons_id,
    )

  def _find_module(self) -> dict:
    """Helper to locate this module's live-data dict."""
    for m in list_connected_modules():
      if m["id"] == self.opentrons_id:
        return cast(dict, m["data"])
    raise RuntimeError(f"Module '{self.opentrons_id}' not found")

  async def get_block_current_temperature(self) -> float:
    return cast(float, self._find_module()["currentTemperature"])

  async def get_block_target_temperature(self) -> float:
    target_temp = self._find_module().get("targetTemperature")
    if target_temp is None:
      raise RuntimeError("Block target temperature is not set. is a cycle running?")
    return cast(float, target_temp)

  async def get_lid_current_temperature(self) -> float:
    return cast(float, self._find_module()["lidTemperature"])

  async def get_lid_target_temperature(self) -> float:
    """Get the lid target temperature in °C. Raises RuntimeError if no target is active."""
    target_temp = self._find_module().get("lidTargetTemperature")
    if target_temp is None:
      raise RuntimeError("Lid target temperature is not set. is a cycle running?")
    return cast(float, target_temp)

  async def get_lid_open(self) -> bool:
    return cast(str, self._find_module()["lidStatus"]) == "open"

  async def get_lid_status(self) -> LidStatus:
    status = cast(str, self._find_module()["lidTemperatureStatus"])
    # Map Opentrons status strings to our enum
    if status == "holding at target":
      return LidStatus.HOLDING_AT_TARGET
    else:
      return LidStatus.IDLE

  async def get_block_status(self) -> BlockStatus:
    status = cast(str, self._find_module()["status"])
    # Map Opentrons status strings to our enum
    if status == "holding at target":
      return BlockStatus.HOLDING_AT_TARGET
    else:
      return BlockStatus.IDLE

  async def get_hold_time(self) -> float:
    return cast(float, self._find_module().get("holdTime", 0.0))

  async def get_current_cycle_index(self) -> int:
    """Get the zero-based index of the current cycle from the Opentrons API."""
    # Opentrons API returns one-based, convert to zero-based
    cycle_index = self._find_module().get("currentCycleIndex")
    if cycle_index is None:
      raise RuntimeError("Current cycle index is not available. Is a profile running?")
    return cast(int, cycle_index) - 1

  async def get_total_cycle_count(self) -> int:
    total_cycles = self._find_module().get("totalCycleCount")
    if total_cycles is None:
      raise RuntimeError("Total cycle count is not available. Is a profile running?")
    return cast(int, total_cycles)

  async def get_current_step_index(self) -> int:
    """Get the zero-based index of the current step from the Opentrons API."""
    # Opentrons API returns one-based, convert to zero-based
    step_index = self._find_module().get("currentStepIndex")
    if step_index is None:
      raise RuntimeError("Current step index is not available. Is a profile running?")
    return cast(int, step_index) - 1

  async def get_total_step_count(self) -> int:
    total_steps = self._find_module().get("totalStepCount")
    if total_steps is None:
      raise RuntimeError("Total step count is not available. Is a profile running?")
    return cast(int, total_steps)
