"""Backend that drives an Opentrons Thermocycler via the HTTP API."""

from typing import List, Optional, cast

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol

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
      )
    self.opentrons_id = opentrons_id
    self._current_protocol: Optional[Protocol] = None

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

  async def set_block_temperature(self, temperature: List[float]):
    """Set block temperature in °C. Only single unique temperature supported."""
    if len(set(temperature)) != 1:
      raise ValueError(
        f"Opentrons thermocycler only supports a single unique block temperature, got {set(temperature)}"
      )
    temp_value = temperature[0]
    return thermocycler_set_block_temperature(celsius=temp_value, module_id=self.opentrons_id)

  async def set_lid_temperature(self, temperature: List[float]):
    """Set lid temperature in °C. Only single unique temperature supported."""
    if len(set(temperature)) != 1:
      raise ValueError(
        f"Opentrons thermocycler only supports a single unique lid temperature, got {set(temperature)}"
      )
    temp_value = temperature[0]
    return thermocycler_set_lid_temperature(celsius=temp_value, module_id=self.opentrons_id)

  async def deactivate_block(self):
    """Deactivate the block heater."""
    return thermocycler_deactivate_block(module_id=self.opentrons_id)

  async def deactivate_lid(self):
    """Deactivate the lid heater."""
    return thermocycler_deactivate_lid(module_id=self.opentrons_id)

  async def run_protocol(self, protocol: Protocol, block_max_volume: float):
    """Enqueue and return immediately (no wait) the PCR profile command."""

    # flatten the protocol to a list of Steps
    # in opentrons, the "celsius" key is used instead of "temperature"
    # step.temperature is now a list, but Opentrons only supports single temperature
    ot_profile = []
    for stage in protocol.stages:
      for step in stage.steps:
        for _ in range(stage.repeats):
          if len(set(step.temperature)) != 1:
            raise ValueError(
              f"Opentrons thermocycler only supports a single unique temperature per step, got {set(step.temperature)}"
            )
          celsius = step.temperature[0]
          ot_profile.append({"celsius": celsius, "holdSeconds": step.hold_seconds})

    self._current_protocol = protocol

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

  async def get_block_current_temperature(self) -> List[float]:
    return [cast(float, self._find_module()["currentTemperature"])]

  async def get_block_target_temperature(self) -> List[float]:
    target_temp = self._find_module().get("targetTemperature")
    if target_temp is None:
      raise RuntimeError("Block target temperature is not set. is a cycle running?")
    return [cast(float, target_temp)]

  async def get_lid_current_temperature(self) -> List[float]:
    return [cast(float, self._find_module()["lidTemperature"])]

  async def get_lid_target_temperature(self) -> List[float]:
    """Get the lid target temperature in °C. Raises RuntimeError if no target is active."""
    target_temp = self._find_module().get("lidTargetTemperature")
    if target_temp is None:
      raise RuntimeError("Lid target temperature is not set. is a cycle running?")
    return [cast(float, target_temp)]

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

    # https://github.com/PyLabRobot/pylabrobot/issues/632
    raise NotImplementedError('Opentrons "cycle" concept is not understood currently.')

    # Since we send a flattened list of steps, we have to recover the cycle index based
    # on the current step index and total step count.
    seen_steps = 0
    current_step = self.get_current_step_index()
    for stage in self._current_protocol.stages:
      for _ in stage.steps:
        if seen_steps == current_step:
          return  # TODO: what is a cycle in OT?
        seen_steps += 1

    raise RuntimeError("Current cycle index is not available. Is a profile running?")

  async def get_total_cycle_count(self) -> int:
    # https://github.com/PyLabRobot/pylabrobot/issues/632
    raise NotImplementedError('Opentrons "cycle" concept is not understood currently.')

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
