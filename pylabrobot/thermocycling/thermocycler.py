"""High-level Thermocycler resource wrapping a backend."""

import asyncio
import time
from typing import List, Optional

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder
from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Step


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
    self.backend: ThermocyclerBackend = backend

  async def open_lid(self, **backend_kwargs):
    return await self.backend.open_lid(**backend_kwargs)

  async def close_lid(self, **backend_kwargs):
    return await self.backend.close_lid(**backend_kwargs)

  async def set_block_temperature(self, temperature: float, **backend_kwargs):
    """Set the block temperature.

    Args:
      temperature: Target temperature in °C.
    """
    return await self.backend.set_block_temperature(temperature, **backend_kwargs)

  async def set_lid_temperature(self, temperature: float, **backend_kwargs):
    """Set the lid temperature.

    Args:
      temperature: Target temperature in °C.
    """
    return await self.backend.set_lid_temperature(temperature, **backend_kwargs)

  async def deactivate_block(self, **backend_kwargs):
    """Turn off the block heater."""
    return await self.backend.deactivate_block(**backend_kwargs)

  async def deactivate_lid(self, **backend_kwargs):
    """Turn off the lid heater."""
    return await self.backend.deactivate_lid(**backend_kwargs)

  async def run_profile(self, profile: List[Step], block_max_volume: float, **backend_kwargs):
    """Enqueue a multi-step temperature profile (fire-and-forget).

    Args:
      profile: List of {"temperature": float, "holdSeconds": float} steps.
      block_max_volume: Maximum block volume (µL) for safety.
    """
    return await self.backend.run_profile(profile, block_max_volume, **backend_kwargs)

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
    **backend_kwargs,
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

    return await self.run_profile(
      profile=profile, block_max_volume=block_max_volume, **backend_kwargs
    )

  async def get_block_current_temperature(self, **backend_kwargs) -> float:
    """Get the current block temperature (°C)."""
    return await self.backend.get_block_current_temperature(**backend_kwargs)

  async def get_block_target_temperature(self, **backend_kwargs) -> float:
    """Get the block's target temperature (°C)."""
    return await self.backend.get_block_target_temperature(**backend_kwargs)

  async def get_lid_current_temperature(self, **backend_kwargs) -> float:
    """Get the current lid temperature (°C)."""
    return await self.backend.get_lid_current_temperature(**backend_kwargs)

  async def get_lid_target_temperature(self, **backend_kwargs) -> float:
    """Get the lid's target temperature (°C), if supported."""
    return await self.backend.get_lid_target_temperature(**backend_kwargs)

  async def get_lid_open(self, **backend_kwargs) -> bool:
    """Return ``True`` if the lid is open."""
    return await self.backend.get_lid_open(**backend_kwargs)

  async def get_lid_status(self, **backend_kwargs) -> LidStatus:
    """Get the lid temperature status."""
    return await self.backend.get_lid_status(**backend_kwargs)

  async def get_block_status(self, **backend_kwargs) -> BlockStatus:
    """Get the block status."""
    return await self.backend.get_block_status(**backend_kwargs)

  async def get_hold_time(self, **backend_kwargs) -> float:
    """Get remaining hold time (s) for the current step."""
    return await self.backend.get_hold_time(**backend_kwargs)

  async def get_current_cycle_index(self, **backend_kwargs) -> int:
    """Get the one-based index of the current cycle."""
    return await self.backend.get_current_cycle_index(**backend_kwargs)

  async def get_total_cycle_count(self, **backend_kwargs) -> int:
    """Get the total number of cycles."""
    return await self.backend.get_total_cycle_count(**backend_kwargs)

  async def get_current_step_index(self, **backend_kwargs) -> int:
    """Get the one-based index of the current step."""
    return await self.backend.get_current_step_index(**backend_kwargs)

  async def get_total_step_count(self, **backend_kwargs) -> int:
    """Get the total number of steps in the current cycle."""
    return await self.backend.get_total_step_count(**backend_kwargs)

  async def wait_for_block(self, timeout: float = 600, tolerance: float = 0.5, **backend_kwargs):
    """Wait until block temp reaches target ± tolerance."""
    target = await self.get_block_target_temperature(**backend_kwargs)
    start = time.time()
    while time.time() - start < timeout:
      if abs((await self.get_block_current_temperature(**backend_kwargs)) - target) < tolerance:
        return
      await asyncio.sleep(1)
    raise TimeoutError("Block temperature timeout.")

  async def wait_for_lid(self, timeout: float = 1200, tolerance: float = 0.5, **backend_kwargs):
    """Wait until the lid temperature reaches target ± ``tolerance`` or the lid temperature status is idle/holding at target."""
    try:
      target = await self.get_lid_target_temperature(**backend_kwargs)
    except RuntimeError:
      target = None
    start = time.time()
    while time.time() - start < timeout:
      if target is not None:
        if abs((await self.get_lid_current_temperature(**backend_kwargs)) - target) < tolerance:
          return
      else:
        # If no target temperature, check status
        status = await self.get_lid_status(**backend_kwargs)
        if status in ["idle", "holding at target"]:
          return
      await asyncio.sleep(1)
    raise TimeoutError("Lid temperature timeout.")

  async def is_profile_running(self, **backend_kwargs) -> bool:
    """Return True if a profile is still in progress."""
    hold = await self.get_hold_time(**backend_kwargs)
    cycle = await self.get_current_cycle_index(**backend_kwargs)
    total_cycles = await self.get_total_cycle_count(**backend_kwargs)
    step = await self.get_current_step_index(**backend_kwargs)
    total_steps = await self.get_total_step_count(**backend_kwargs)

    # if still holding in a step, it's running
    if hold and hold > 0:
      return True
    # if haven't reached last cycle (zero-based indexing)
    if cycle < total_cycles - 1:
      return True
    # last cycle but not last step (zero-based indexing)
    if cycle == total_cycles - 1 and step < total_steps - 1:
      return True
    return False

  async def wait_for_profile_completion(self, poll_interval: float = 60.0, **backend_kwargs):
    """Block until the profile finishes, polling at `poll_interval` seconds."""
    while await self.is_profile_running(**backend_kwargs):
      await asyncio.sleep(poll_interval)

  def serialize(self) -> dict:
    """JSON-serializable representation."""
    return {**Machine.serialize(self), **ResourceHolder.serialize(self)}
