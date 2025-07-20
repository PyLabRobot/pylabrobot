"""A chatterbox backend for a thermocycler."""

from dataclasses import dataclass
from typing import Optional, List, Dict

from pylabrobot.thermocycling.backend import ThermocyclerBackend


@dataclass
class ThermocyclerState:
    """The state of the thermocycler."""

    block_temp: float = 25.0
    lid_temp: float = 25.0
    block_target: Optional[float] = None
    lid_target: Optional[float] = None
    lid_open: bool = True
    profile: Optional[List[Dict[str, float]]] = None
    is_profile_running: bool = False
    current_step_index: int = 0
    total_steps: int = 0


class ThermocyclerChatterboxBackend(ThermocyclerBackend):
    """
    A device-free thermocycler backend that logs operations to stdout with a
    disciplined style and provides an instantaneous simulation of profile
    execution for rapid automated testing.
    """

    _step_length = 8
    _temp_length = 15
    _hold_length = 15

    def __init__(self, name: str = "thermocycler_chatterbox"):
        super().__init__()
        self.name = name
        self._state = ThermocyclerState()

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

    async def set_block_temperature(self, celsius: float):
        print(f"Setting block temperature to {celsius:.1f}°C.")
        self._state.block_target = celsius
        self._state.block_temp = celsius
        if self._state.is_profile_running:
            print("  - A running profile was cancelled.")
            self._state.is_profile_running = False

    async def set_lid_temperature(self, celsius: float):
        print(f"Setting lid temperature to {celsius:.1f}°C.")
        self._state.lid_target = celsius
        self._state.lid_temp = celsius

    async def deactivate_block(self):
        print("Deactivating block.")
        self._state.block_target = None
        if self._state.is_profile_running:
            print("  - A running profile was cancelled.")
            self._state.is_profile_running = False

    async def deactivate_lid(self):
        print("Deactivating lid.")
        self._state.lid_target = None

    async def run_profile(self, profile: list[dict], block_max_volume: float):
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
            celsius_val = step.get("celsius", "N/A")
            celsius_str = (
                f"{celsius_val:.1f}" if isinstance(celsius_val, (int, float)) else "N/A"
            )
            hold_val = step.get("holdSeconds", "N/A")
            hold_str = (
                f"{hold_val:.1f}" if isinstance(hold_val, (int, float)) else "N/A"
            )
            row = (
                f"  {i+1:<{self._step_length}} "
                f"{celsius_str:<{self._temp_length}} "
                f"{hold_str:<{self._hold_length}}"
            )
            print(row)

        self._state.profile = profile
        self._state.total_steps = len(profile)
        self._state.current_step_index = 0
        self._state.is_profile_running = True

        first_step = self._state.profile[0]
        first_temp = first_step.get("celsius")
        if first_temp is not None:
            print(
                f"  - Starting Step 1/{self._state.total_steps}: setting block to {first_temp:.1f}°C."
            )
            self._state.block_target = first_temp
            self._state.block_temp = first_temp

    async def get_hold_time(self) -> float:
        if not self._state.is_profile_running:
            return 0.0

        # Loop through all steps and print the full log instantly.
        for i in range(self._state.total_steps):
            completed_step = self._state.profile[i]
            hold_duration = completed_step.get("holdSeconds", 0)
            print(
                f"  - Step {i + 1}/{self._state.total_steps}: hold for {hold_duration:.1f}s complete."
            )

            if i < self._state.total_steps - 1:
                next_step = self._state.profile[i + 1]
                next_temp = next_step.get("celsius")
                if next_temp is not None:
                    print(
                        f"  - Starting Step {i + 2}/{self._state.total_steps}: "
                        f"setting block to {next_temp:.1f}°C."
                    )

        print("  - Profile finished.")
        self._state.is_profile_running = False
        self._state.current_step_index = self._state.total_steps - 1
        final_temp = self._state.profile[-1].get("celsius")
        if final_temp is not None:
            self._state.block_target = final_temp
            self._state.block_temp = final_temp

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

    async def get_block_current_temperature(self) -> float:
        return self._state.block_temp

    async def get_block_target_temperature(self) -> Optional[float]:
        return self._state.block_target

    async def get_lid_current_temperature(self) -> float:
        return self._state.lid_temp

    async def get_lid_target_temperature(self) -> Optional[float]:
        return self._state.lid_target

    async def get_lid_status(self) -> str:
        return "open" if self._state.lid_open else "closed"
