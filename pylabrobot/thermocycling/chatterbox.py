from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol


@dataclass
class ThermocyclerState:
  """The state of the thermocycler."""

  block_temp: List[float]
  lid_temp: List[float]
  block_target: Optional[List[float]]
  lid_target: Optional[List[float]]
  lid_open: bool
  protocol: Optional[Protocol]
  is_profile_running: bool
  current_step_index: int
  total_steps: int

  def __init__(self, num_zones: int):
    """Initialize the thermocycler state with a specified number of zones."""
    self.block_temp = [25.0] * num_zones
    self.lid_temp = [25.0] * num_zones
    self.block_target = None
    self.lid_target = None
    self.lid_open = True
    self.protocol = None
    self.is_profile_running = False
    self.current_step_index = 0
    self.total_steps = 0


class ThermocyclerChatterboxBackend(ThermocyclerBackend):
  """
  A device-free thermocycler backend that logs operations to stdout with a
  disciplined style and provides an instantaneous simulation of profile
  execution for rapid automated testing.
  """

  _step_length = 8
  _temp_length = 15
  _hold_length = 15

  def __init__(self, name: str = "thermocycler_chatterbox", num_zones: int = 1):
    super().__init__()
    self.name = name
    self._state = ThermocyclerState(num_zones=num_zones)
    self.num_zones = num_zones

  async def setup(self):
    print("Setting up thermocycler.")

  async def stop(self):
    print("Stopping thermocycler.")

  async def open_lid(self):
    print("Opening lid.")
    self._state.lid_open = True

  async def close_lid(self):
    print("Closing lid.")
    self._state.lid_open = False

  async def set_block_temperature(self, temperature: List[float]):
    # Ensure we have the right number of temperatures
    if len(temperature) != self.num_zones:
      raise ValueError(f"Expected {self.num_zones} block temperatures, got {len(temperature)}")

    temp_str = ", ".join(f"{t:.1f}" for t in temperature)
    print(f"Setting block temperature(s) to {temp_str}°C.")
    self._state.block_target = list(temperature)
    self._state.block_temp = list(temperature)
    if self._state.is_profile_running:
      print("  - A running profile was cancelled.")
      self._state.is_profile_running = False

  async def set_lid_temperature(self, temperature: List[float]):
    # Ensure we have the right number of temperatures
    if len(temperature) != self.num_zones:
      raise ValueError(f"Expected {self.num_zones} lid temperatures, got {len(temperature)}")

    temp_str = ", ".join(f"{t:.1f}" for t in temperature)
    print(f"Setting lid temperature(s) to {temp_str}°C.")
    self._state.lid_target = list(temperature)
    self._state.lid_temp = list(temperature)

  async def deactivate_block(self):
    print("Deactivating block.")
    self._state.block_target = None
    if self._state.is_profile_running:
      print("  - A running profile was cancelled.")
      self._state.is_profile_running = False

  async def deactivate_lid(self):
    print("Deactivating lid.")
    self._state.lid_target = None

  async def run_protocol(self, protocol: Protocol, block_max_volume: float, **kwargs):
    """Run a protocol with stages and repeats."""
    print("Running protocol:")

    self._state.is_profile_running = True
    self._state.protocol = protocol
    self._state.total_steps = sum(stage.repeats * len(stage.steps) for stage in protocol.stages)
    self._state.current_step_index = 0

    for stage_idx, stage in enumerate(protocol.stages):
      # stage_info.append(f"Stage {stage_idx + 1}: {len(stage.steps)} step(s) x {stage.repeats} repeat(s)")
      print(
        f"- Stage {stage_idx + 1}/{len(protocol.stages)}: {len(stage.steps)} step(s) x {stage.repeats} repeat(s)"
      )
      for repeat_idx in range(stage.repeats):
        print(f"  - Repeat {repeat_idx + 1}/{stage.repeats}:")
        for step_idx, step in enumerate(stage.steps):
          self._state.current_step_index += 1
          self._state.block_target = step.temperature

          temperature_str = ", ".join(f"{t:.1f}" for t in step.temperature)
          hold_str = (
            f"{step.hold_seconds:.1f}" if isinstance(step.hold_seconds, (int, float)) else "N/A"
          )
          # Simulate running the step
          print(
            f"    - Step {step_idx + 1}/{len(stage.steps)} (repeat {repeat_idx + 1}/{stage.repeats}): "
            f"temperature(s) = {temperature_str}°C, hold = {hold_str}s"
          )

    self._state.is_profile_running = False

  async def get_hold_time(self) -> float:
    if not self._state.is_profile_running:
      return 0.0

    # Loop through all steps and print the full log instantly.
    if self._state.protocol is None:
      return 0.0

    self._state.is_profile_running = False
    self._state.current_step_index = self._state.total_steps - 1
    final_temp = self._state.protocol.stages[-1].steps[-1].temperature
    if final_temp is not None:
      # final_temp is now a list, use all temperatures for each zone
      if isinstance(final_temp, list) and len(final_temp) > 0:
        self._state.block_target = list(final_temp)
        self._state.block_temp = list(final_temp)

    return 0.0

  async def get_current_cycle_index(self) -> int:
    return 1

  async def get_total_cycle_count(self) -> int:
    return 1

  async def get_current_step_index(self) -> int:
    # If the profile is "running", it means the simulation hasn't happened yet.
    # The moment get_hold_time is called, it will complete instantly.
    return self._state.total_steps if not self._state.is_profile_running else 1

  async def get_total_step_count(self) -> int:
    return self._state.total_steps

  async def get_block_current_temperature(self) -> List[float]:
    return self._state.block_temp

  async def get_block_target_temperature(self) -> List[float]:
    if self._state.block_target is None:
      raise RuntimeError("Block target temperature is not set. Is a cycle running?")
    return self._state.block_target

  async def get_lid_current_temperature(self) -> List[float]:
    return self._state.lid_temp

  async def get_lid_target_temperature(self) -> List[float]:
    if self._state.lid_target is None:
      raise RuntimeError("Lid target temperature is not set. Is a cycle running?")
    return self._state.lid_target

  async def get_lid_open(self) -> bool:
    return self._state.lid_open

  async def get_lid_status(self) -> LidStatus:
    if self._state.lid_target is not None:
      return LidStatus.HOLDING_AT_TARGET
    return LidStatus.IDLE

  async def get_block_status(self) -> BlockStatus:
    if self._state.block_target is not None:
      return BlockStatus.HOLDING_AT_TARGET
    return BlockStatus.IDLE
