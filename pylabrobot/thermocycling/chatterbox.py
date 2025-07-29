from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Step


@dataclass
class ThermocyclerState:
  """The state of the thermocycler."""

  block_temp: List[float]
  lid_temp: List[float]
  block_target: Optional[List[float]]
  lid_target: Optional[List[float]]
  lid_open: bool
  profile: Optional[List[Step]]
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
    self.profile = None
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

  async def run_profile(self, profile: list[Step], block_max_volume: float):
    print("Running profile:")

    if not profile:
      print("  (Profile is empty, no action taken)")
      self._state.is_profile_running = False
      return

    header = (
      f"{'step#':<{self._step_length}} "
      f"{'temp (C)':<{self._temp_length}} "
      f"{'hold (s)':<{self._hold_length}}"
    )
    print(f"  {header}")

    for i, step in enumerate(profile):
      temperature_val = step.temperature
      # Handle temperature as a list - display all temperatures
      if isinstance(temperature_val, list) and len(temperature_val) > 0:
        temperature_str = ", ".join(f"{t:.1f}" for t in temperature_val)
      elif isinstance(temperature_val, (int, float)):
        temperature_str = f"{temperature_val:.1f}"
      else:
        temperature_str = "N/A"
      hold_val = step.hold_seconds
      hold_str = f"{hold_val:.1f}" if isinstance(hold_val, (int, float)) else "N/A"
      row = (
        f"  {i + 1:<{self._step_length}} "
        f"{temperature_str:<{self._temp_length}} "
        f"{hold_str:<{self._hold_length}}"
      )
      print(row)

    self._state.profile = profile
    self._state.total_steps = len(profile)
    self._state.current_step_index = 0
    self._state.is_profile_running = True

    first_step = self._state.profile[0]
    first_temp = first_step.temperature
    if first_temp is not None:
      # first_temp is now a list, display all temperatures
      if isinstance(first_temp, list) and len(first_temp) > 0:
        temp_str = ", ".join(f"{t:.1f}" for t in first_temp)
        print(f"  - Starting Step 1/{self._state.total_steps}: setting block to {temp_str}°C.")
        # Set block target to the temperatures for each zone
        self._state.block_target = list(first_temp)
        self._state.block_temp = list(first_temp)

  async def get_hold_time(self) -> float:
    if not self._state.is_profile_running:
      return 0.0

    # Loop through all steps and print the full log instantly.
    if self._state.profile is None:
      return 0.0

    for i in range(self._state.total_steps):
      completed_step = self._state.profile[i]
      hold_duration = completed_step.hold_seconds
      print(f"  - Step {i + 1}/{self._state.total_steps}: hold for {hold_duration:.1f}s complete.")

      if i < self._state.total_steps - 1:
        next_step = self._state.profile[i + 1]
        next_temp = next_step.temperature
        if next_temp is not None:
          # next_temp is now a list, display all temperatures
          if isinstance(next_temp, list) and len(next_temp) > 0:
            temp_str = ", ".join(f"{t:.1f}" for t in next_temp)
            print(
              f"  - Starting Step {i + 2}/{self._state.total_steps}: "
              f"setting block to {temp_str}°C."
            )

    print("  - Profile finished.")
    self._state.is_profile_running = False
    self._state.current_step_index = self._state.total_steps - 1
    final_temp = self._state.profile[-1].temperature
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
