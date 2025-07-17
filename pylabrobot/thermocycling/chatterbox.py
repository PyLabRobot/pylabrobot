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

    # ─────────────────── Lifecycle ────────────────────────────────────────

    async def setup(self):
        print("⚙ ThermocyclerChatterbox: setup")

    async def stop(self):
        print("⚙ ThermocyclerChatterbox: stop")

    # ─────────────────── Control ─────────────────────────────────────────

    async def open_lid(self):
        print("⚙ open_lid")
        self._lid_open = True

    async def close_lid(self):
        print("⚙ close_lid")
        self._lid_open = False

    async def set_block_temperature(self, celsius: float):
        print(f"⚙ set_block_temperature → {celsius} °C")
        self._block = celsius

    async def set_lid_temperature(self, celsius: float):
        print(f"⚙ set_lid_temperature → {celsius} °C")
        self._lid = celsius

    async def deactivate_block(self):
        print("⚙ deactivate_block")

    async def deactivate_lid(self):
        print("⚙ deactivate_lid")

    async def run_profile(self, profile: list[dict], block_max_volume: float):
        print(f"⚙ run_profile {profile}, max {block_max_volume} µL")
        # simulate status progression
        self._hold = profile[-1].get("holdSeconds", 0)
        self._cycles = len(profile)
        self._steps = len(profile)

    # ─────────────────── Status getters ─────────────────────────────────

    async def get_block_current_temperature(self) -> float:
        print("⚙ get_block_current_temperature")
        return self._block

    async def get_block_target_temperature(self) -> float:
        print("⚙ get_block_target_temperature")
        return self._block

    async def get_lid_current_temperature(self) -> float:
        print("⚙ get_lid_current_temperature")
        return self._lid

    async def get_lid_target_temperature(self) -> float:
        print("⚙ get_lid_target_temperature")
        return self._lid

    async def get_lid_status(self) -> str:
        print("⚙ get_lid_status")
        return "open" if self._lid_open else "closed"

    async def get_hold_time(self) -> float:
        print("⚙ get_hold_time")
        return self._hold

    async def get_current_cycle_index(self) -> int:
        print("⚙ get_current_cycle_index")
        return self._cycle

    async def get_total_cycle_count(self) -> int:
        print("⚙ get_total_cycle_count")
        return self._cycles

    async def get_current_step_index(self) -> int:
        print("⚙ get_current_step_index")
        return self._step

    async def get_total_step_count(self) -> int:
        print("⚙ get_total_step_count")
        return self._steps
