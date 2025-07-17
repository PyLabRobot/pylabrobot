"""High-level Thermocycler resource wrapping a backend."""

import asyncio
import time
from typing import Optional, List, Dict

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder
from pylabrobot.thermocycling.backend import ThermocyclerBackend


class Thermocycler(ResourceHolder, Machine):
    """Generic Thermocycler: block + lid + profile + status queries."""

    def __init__(
        self,
        name: str,
        size_x: float,
        size_y: float,
        size_z: float,
        backend: ThermocyclerBackend,
        child_location: Coordinate,
        category: str = "thermocycler",
        model: Optional[str] = None,
    ):
        """Initialize a Thermocycler resource.

        Args:
          name: Human-readable name.
          size_x: Footprint in the X dimension (mm).
          size_y: Footprint in the Y dimension (mm).
          size_z: Height in the Z dimension (mm).
          backend: A ThermocyclerBackend instance.
          child_location: Where a plate sits on the block.
          category: Resource category (default: "thermocycler").
          model: Module model string (e.g. "thermocyclerModuleV1").
        """
        ResourceHolder.__init__(
            self,
            name=name,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            child_location=child_location,
            category=category,
            model=model,
        )
        Machine.__init__(self, backend=backend)
        # exactly like TemperatureController does
        self.backend: ThermocyclerBackend = backend

    # ─── Control ──────────────────────────────────────────────────────────────

    async def open_lid(self):
        """Open the thermocycler lid."""
        return await self.backend.open_lid()

    async def close_lid(self):
        """Close the thermocycler lid."""
        return await self.backend.close_lid()

    async def set_block_temperature(self, celsius: float):
        """Set the block temperature.

        Args:
          celsius: Target temperature in °C.
        """
        return await self.backend.set_block_temperature(celsius)

    async def set_lid_temperature(self, celsius: float):
        """Set the lid temperature.

        Args:
          celsius: Target temperature in °C.
        """
        return await self.backend.set_lid_temperature(celsius)

    async def deactivate_block(self):
        """Turn off the block heater."""
        return await self.backend.deactivate_block()

    async def deactivate_lid(self):
        """Turn off the lid heater."""
        return await self.backend.deactivate_lid()

    async def run_profile(self, profile: List[Dict[str, float]], block_max_volume: float):
        """Enqueue a multi-step temperature profile (fire-and-forget).

        Args:
          profile: List of {"celsius": float, "holdSeconds": float} steps.
          block_max_volume: Maximum block volume (µL) for safety.
        """
        return await self.backend.run_profile(profile, block_max_volume)

    # ─── Status queries ───────────────────────────────────────────────────────

    async def get_block_current_temperature(self) -> float:
        """Get the current block temperature (°C)."""
        return await self.backend.get_block_current_temperature()

    async def get_block_target_temperature(self) -> float:
        """Get the block’s target temperature (°C)."""
        return await self.backend.get_block_target_temperature()

    async def get_lid_current_temperature(self) -> float:
        """Get the current lid temperature (°C)."""
        return await self.backend.get_lid_current_temperature()

    async def get_lid_target_temperature(self) -> Optional[float]:
        """Get the lid’s target temperature (°C), if supported."""
        return await self.backend.get_lid_target_temperature()

    async def get_lid_status(self) -> str:
        """Get whether the lid is “open” or “closed”."""
        return await self.backend.get_lid_status()

    async def get_hold_time(self) -> float:
        """Get remaining hold time (s) for the current step."""
        return await self.backend.get_hold_time()

    async def get_current_cycle_index(self) -> int:
        """Get the zero-based index of the current cycle."""
        return await self.backend.get_current_cycle_index()

    async def get_total_cycle_count(self) -> int:
        """Get the total number of cycles."""
        return await self.backend.get_total_cycle_count()

    async def get_current_step_index(self) -> int:
        """Get the zero-based index of the current step."""
        return await self.backend.get_current_step_index()

    async def get_total_step_count(self) -> int:
        """Get the total number of steps in the current cycle."""
        return await self.backend.get_total_step_count()

    # ─── Optional wait helpers ────────────────────────────────────────────────

    async def wait_for_block(self, timeout: float = 600, tolerance: float = 0.5):
        """Wait until block temp reaches target ± tolerance."""
        target = await self.get_block_target_temperature()
        start = time.time()
        while time.time() - start < timeout:
            if abs((await self.get_block_current_temperature()) - target) < tolerance:
                return
            await asyncio.sleep(1)
        raise TimeoutError("Block temperature timeout.")

    async def wait_for_lid(self, timeout: float = 600, tolerance: float = 0.5):
        """Wait until lid temp reaches target ± tolerance."""
        target = await self.get_lid_target_temperature()
        start = time.time()
        while time.time() - start < timeout:
            if abs((await self.get_lid_current_temperature()) - target) < tolerance:
                return
            await asyncio.sleep(1)
        raise TimeoutError("Lid temperature timeout.")

    # ─── Profile status helpers ──────────────────────────────────────────────

    async def is_profile_running(self) -> bool:
        """Return True if a profile is still in progress."""
        hold = await self.get_hold_time()
        cycle = await self.get_current_cycle_index()
        total_cycles = await self.get_total_cycle_count()
        step = await self.get_current_step_index()
        total_steps = await self.get_total_step_count()

        # if still holding in a step, it’s running
        if hold and hold > 0:
            return True
        # if haven’t reached last cycle
        if cycle < total_cycles - 1:
            return True
        # last cycle but not last step
        if cycle == total_cycles - 1 and step < total_steps - 1:
            return True
        return False

    async def wait_for_profile_completion(self, poll_interval: float = 60.0):
        """Block until the profile finishes, polling at `poll_interval` seconds."""
        while await self.is_profile_running():
            await asyncio.sleep(poll_interval)

    def serialize(self) -> dict:
        """JSON-serializable representation."""
        return {**Machine.serialize(self), **ResourceHolder.serialize(self)}