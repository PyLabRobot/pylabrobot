"""Dummy backend for testing: prints every operation and returns fixed data."""

from pylabrobot.thermocycling.backend import ThermocyclerBackend


class ThermocyclerChatterboxBackend(ThermocyclerBackend):
  """Device-free thermocycler backend that logs to stdout."""

  def __init__(self, dummy_block: float = 25.0, dummy_lid: float = 25.0):
    """Create a chatterbox backend with initial dummy temperatures."""
    self._block = dummy_block
    self._lid = dummy_lid
    self._hold = 0.0
    self._cycle = 0
    self._cycles = 1
    self._step = 0
    self._steps = 1
    self._lid_open = False
    self._profile_poll_count = 0

  async def setup(self):
    print("Setting up the thermocycler.")

  async def stop(self):
    print("Stopping the thermocycler.")

  async def open_lid(self):
    print("Opening the lid.")
    self._lid_open = True

  async def close_lid(self):
    print("Closing the lid.")
    self._lid_open = False

  async def set_block_temperature(self, celsius: float):
    print(f"Setting the block temperature to {celsius} °C.")
    self._block = celsius

  async def set_lid_temperature(self, celsius: float):
    print(f"Setting the lid temperature to {celsius} °C.")
    self._lid = celsius

  async def deactivate_block(self):
    print("Deactivating the block.")

  async def deactivate_lid(self):
    print("Deactivating the lid.")

  async def run_profile(self, profile: list[dict], block_max_volume: float):
    print(f"Running profile {profile}, max {block_max_volume} µL.")
    # simulate status progression
    self._hold = profile[-1].get("holdSeconds", 0)
    self._cycles = len(profile)
    self._steps = len(profile)
    self._profile_poll_count = 5  # Simulate 5 polls before completion

  async def get_block_current_temperature(self) -> float:
    print("Getting the block current temperature.")
    return self._block

  async def get_block_target_temperature(self) -> float:
    print("Getting the block target temperature.")
    return self._block

  async def get_lid_current_temperature(self) -> float:
    print("Getting the lid current temperature.")
    return self._lid

  async def get_lid_target_temperature(self) -> float:
    print("Getting the lid target temperature.")
    return self._lid

  async def get_lid_status(self) -> str:
    print("Getting the lid status.")
    return "open" if self._lid_open else "closed"

  async def get_hold_time(self) -> float:
    print("Getting the hold time.")
    if self._profile_poll_count > 0:
      self._profile_poll_count -= 1
      return self._hold
    return 0.0

  async def get_current_cycle_index(self) -> int:
    print("Getting the current cycle index.")
    if self._profile_poll_count > 0:
      return self._cycle
    return self._cycles - 1

  async def get_total_cycle_count(self) -> int:
    print("Getting the total cycle count.")
    return self._cycles

  async def get_current_step_index(self) -> int:
    print("Getting the current step index.")
    if self._profile_poll_count > 0:
      return self._step
    return self._steps - 1

  async def get_total_step_count(self) -> int:
    print("Getting the total step count.")
    return self._steps
