"""High-level Thermocycler resource wrapping a backend."""

import asyncio
import time
from typing import List, Optional, cast

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder
from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import Step


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

  async def open_lid(self):
    """Open the thermocycler lid."""
    return await self.backend.open_lid()

  async def close_lid(self):
    """Close the thermocycler lid."""
    return await self.backend.close_lid()

  async def set_block_temperature(self, temperature: float):
    """Set the block temperature.

    Args:
      temperature: Target temperature in °C.
    """
    return await self.backend.set_block_temperature(temperature)

  async def set_lid_temperature(self, temperature: float):
    """Set the lid temperature.

    Args:
      temperature: Target temperature in °C.
    """
    return await self.backend.set_lid_temperature(temperature)

  async def deactivate_block(self):
    """Turn off the block heater."""
    return await self.backend.deactivate_block()

  async def deactivate_lid(self):
    """Turn off the lid heater."""
    return await self.backend.deactivate_lid()

  async def run_profile(self, profile: List[Step], block_max_volume: float):
    """Enqueue a multi-step temperature profile (fire-and-forget).

    Args:
      profile: List of {"temperature": float, "holdSeconds": float} steps.
      block_max_volume: Maximum block volume (µL) for safety.
    """
    return await self.backend.run_profile(profile, block_max_volume)

  async def run_pcr_profile(
    self,
    denaturation_temp: float,
    denaturation_time: float,
    annealing_temp: float,
    annealing_time: float,
    extension_temp: float,
    extension_time: float,
    num_cycles: int,
    block_max_volume: float,
    lid_temperature: float,
    pre_denaturation_temp: Optional[float] = None,
    pre_denaturation_time: Optional[float] = None,
    final_extension_temp: Optional[float] = None,
    final_extension_time: Optional[float] = None,
    storage_temp: Optional[float] = None,
    storage_time: Optional[float] = None,
  ):
    """Run a PCR profile with specified parameters.

    Args:
      denaturation_temp: Denaturation temperature in °C.
      denaturation_time: Denaturation time in seconds.
      annealing_temp: Annealing temperature in °C.
      annealing_time: Annealing time in seconds.
      extension_temp: Extension temperature in °C.
      extension_time: Extension time in seconds.
      num_cycles: Number of PCR cycles.
      block_max_volume: Maximum block volume (µL) for safety.
      lid_temperature: Lid temperature to set during the profile.
      pre_denaturation_temp: Optional pre-denaturation temperature in °C.
      pre_denaturation_time: Optional pre-denaturation time in seconds.
      final_extension_temp: Optional final extension temperature in °C.
      final_extension_time: Optional final extension time in seconds.
      storage_temp: Optional storage temperature in °C.
      storage_time: Optional storage time in seconds.
    """

    await self.set_lid_temperature(lid_temperature)
    await self.wait_for_lid()

    profile: List[Step] = []

    if pre_denaturation_temp is not None and pre_denaturation_time is not None:
      profile.append(Step(temperature=pre_denaturation_temp, hold_seconds=pre_denaturation_time))

    # Main PCR cycles
    pcr_step = [
      Step(temperature=denaturation_temp, hold_seconds=denaturation_time),
      Step(temperature=annealing_temp, hold_seconds=annealing_time),
      Step(temperature=extension_temp, hold_seconds=extension_time),
    ]
    for _ in range(num_cycles):
      profile.extend(pcr_step)

    if final_extension_temp is not None and final_extension_time is not None:
      profile.append(Step(temperature=final_extension_temp, hold_seconds=final_extension_time))

    if storage_temp is not None and storage_time is not None:
      profile.append(Step(temperature=storage_temp, hold_seconds=storage_time))

    return await self.run_profile(profile=profile, block_max_volume=block_max_volume)

  async def get_block_current_temperature(self) -> float:
    """Get the current block temperature (°C)."""
    return await self.backend.get_block_current_temperature()

  async def get_block_target_temperature(self) -> Optional[float]:
    """Get the block’s target temperature (°C)."""
    return cast(Optional[float], await self.backend.get_block_target_temperature())

  async def get_lid_current_temperature(self) -> float:
    """Get the current lid temperature (°C)."""
    return await self.backend.get_lid_current_temperature()

  async def get_lid_target_temperature(self) -> Optional[float]:
    """Get the lid’s target temperature (°C), if supported."""
    return await self.backend.get_lid_target_temperature()

  async def get_lid_status(self) -> str:
    """Get whether the lid is “open” or “closed”."""
    return cast(str, await self.backend.get_lid_status())

  async def get_hold_time(self) -> float:
    """Get remaining hold time (s) for the current step."""
    return await self.backend.get_hold_time()

  async def get_current_cycle_index(self) -> int:
    """Get the one-based index of the current cycle."""
    return await self.backend.get_current_cycle_index()

  async def get_total_cycle_count(self) -> int:
    """Get the total number of cycles."""
    return await self.backend.get_total_cycle_count()

  async def get_current_step_index(self) -> int:
    """Get the one-based index of the current step."""
    return await self.backend.get_current_step_index()

  async def get_total_step_count(self) -> int:
    """Get the total number of steps in the current cycle."""
    return await self.backend.get_total_step_count()

  async def wait_for_block(self, timeout: float = 600, tolerance: float = 0.5):
    """Wait until block temp reaches target ± tolerance."""
    target = await self.get_block_target_temperature()
    if target is None:
      return  # No target temperature to wait for
    start = time.time()
    while time.time() - start < timeout:
      if abs((await self.get_block_current_temperature()) - target) < tolerance:
        return
      await asyncio.sleep(1)
    raise TimeoutError("Block temperature timeout.")

  async def wait_for_lid(self, timeout: float = 1200, tolerance: float = 0.5):
    """Wait until lid temp reaches target ± tolerance, or status is idle/holding at target."""
    target = await self.get_lid_target_temperature()
    start = time.time()
    while time.time() - start < timeout:
      if target is not None:
        if abs((await self.get_lid_current_temperature()) - target) < tolerance:
          return
      else:
        # If no target temperature, check status
        status = await self.get_lid_status()
        if status in ["idle", "holding at target"]:
          return
      await asyncio.sleep(1)
    raise TimeoutError("Lid temperature timeout.")

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
    if cycle < total_cycles:
      return True
    # last cycle but not last step
    if cycle == total_cycles and step < total_steps:
      return True
    return False

  async def wait_for_profile_completion(self, poll_interval: float = 60.0):
    """Block until the profile finishes, polling at `poll_interval` seconds."""
    while await self.is_profile_running():
      await asyncio.sleep(poll_interval)

  def serialize(self) -> dict:
    """JSON-serializable representation."""
    return {**Machine.serialize(self), **ResourceHolder.serialize(self)}
