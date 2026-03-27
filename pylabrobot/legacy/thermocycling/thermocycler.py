"""High-level Thermocycler resource wrapping a backend.

Internally delegates to ThermocyclingCapability and TemperatureControlCapability
via adapters. The legacy public API is unchanged.
"""

import asyncio
import time
from typing import List, Optional

from pylabrobot.capabilities.temperature_controlling import (
  TemperatureControlCapability,
  TemperatureControllerBackend as _NewTempBackend,
)
from pylabrobot.capabilities.thermocycling import (
  ThermocyclingBackend as _NewThermocyclingBackend,
  ThermocyclingCapability,
)
from pylabrobot.capabilities.thermocycling import standard as _new_std
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.legacy.thermocycling.backend import ThermocyclerBackend
from pylabrobot.legacy.thermocycling.standard import (
  BlockStatus,
  LidStatus,
  Protocol,
  Stage,
  Step,
  protocol_from_new,
  protocol_to_new,
)
from pylabrobot.resources import Coordinate, ResourceHolder


# ---------------------------------------------------------------------------
# Adapters: wrap a legacy ThermocyclerBackend for the new capability interfaces
# ---------------------------------------------------------------------------


class _BlockTempAdapter(_NewTempBackend):
  """Adapts the block side of a legacy ThermocyclerBackend to TemperatureControllerBackend."""

  def __init__(self, legacy: ThermocyclerBackend):
    self._legacy = legacy

  async def setup(self):
    pass

  async def stop(self):
    pass

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def set_temperature(self, temperature: float):
    await self._legacy.set_block_temperature([temperature])

  async def get_current_temperature(self) -> float:
    temps = await self._legacy.get_block_current_temperature()
    return temps[0]

  async def deactivate(self):
    await self._legacy.deactivate_block()


class _LidTempAdapter(_NewTempBackend):
  """Adapts the lid side of a legacy ThermocyclerBackend to TemperatureControllerBackend."""

  def __init__(self, legacy: ThermocyclerBackend):
    self._legacy = legacy

  async def setup(self):
    pass

  async def stop(self):
    pass

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    await self._legacy.set_lid_temperature([temperature])

  async def get_current_temperature(self) -> float:
    temps = await self._legacy.get_lid_current_temperature()
    return temps[0]

  async def deactivate(self):
    await self._legacy.deactivate_lid()


class _ThermocyclingAdapter(_NewThermocyclingBackend):
  """Adapts a legacy ThermocyclerBackend to the new ThermocyclingBackend."""

  def __init__(self, legacy: ThermocyclerBackend):
    self._legacy = legacy

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def open_lid(self) -> None:
    await self._legacy.open_lid()

  async def close_lid(self) -> None:
    await self._legacy.close_lid()

  async def get_lid_open(self) -> bool:
    return await self._legacy.get_lid_open()

  async def run_protocol(self, protocol: _new_std.Protocol, block_max_volume: float, backend_params=None) -> None:
    await self._legacy.run_protocol(protocol_from_new(protocol), block_max_volume)

  async def get_hold_time(self) -> float:
    return await self._legacy.get_hold_time()

  async def get_current_cycle_index(self) -> int:
    return await self._legacy.get_current_cycle_index()

  async def get_total_cycle_count(self) -> int:
    return await self._legacy.get_total_cycle_count()

  async def get_current_step_index(self) -> int:
    return await self._legacy.get_current_step_index()

  async def get_total_step_count(self) -> int:
    return await self._legacy.get_total_step_count()


# ---------------------------------------------------------------------------
# Legacy frontend
# ---------------------------------------------------------------------------


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

    # Wire up capabilities via adapters
    block_cap = TemperatureControlCapability(backend=_BlockTempAdapter(backend))
    lid_cap = TemperatureControlCapability(backend=_LidTempAdapter(backend))
    self._thermocycling = ThermocyclingCapability(
      backend=_ThermocyclingAdapter(backend), block=block_cap, lid=lid_cap
    )

  # --- delegate to capabilities ---

  async def open_lid(self, **backend_kwargs):
    return await self._thermocycling.open_lid()

  async def close_lid(self, **backend_kwargs):
    return await self._thermocycling.close_lid()

  async def set_block_temperature(self, temperature: List[float], **backend_kwargs):
    return await self.backend.set_block_temperature(temperature, **backend_kwargs)

  async def set_lid_temperature(self, temperature: List[float], **backend_kwargs):
    return await self.backend.set_lid_temperature(temperature, **backend_kwargs)

  async def deactivate_block(self, **backend_kwargs):
    return await self.backend.deactivate_block(**backend_kwargs)

  async def deactivate_lid(self, **backend_kwargs):
    return await self.backend.deactivate_lid(**backend_kwargs)

  async def run_protocol(self, protocol: Protocol, block_max_volume: float, **backend_kwargs):
    await self._thermocycling.run_protocol(protocol_to_new(protocol), block_max_volume)

  async def run_pcr_profile(
    self,
    denaturation_temp: List[float],
    denaturation_time: float,
    annealing_temp: List[float],
    annealing_time: float,
    extension_temp: List[float],
    extension_time: float,
    num_cycles: int,
    block_max_volume: float,
    lid_temperature: List[float],
    pre_denaturation_temp: Optional[List[float]] = None,
    pre_denaturation_time: Optional[float] = None,
    final_extension_temp: Optional[List[float]] = None,
    final_extension_time: Optional[float] = None,
    storage_temp: Optional[List[float]] = None,
    storage_time: Optional[float] = None,
    **backend_kwargs,
  ):
    await self.set_lid_temperature(lid_temperature)
    await self.wait_for_lid()

    stages: List[Stage] = []

    if pre_denaturation_temp is not None and pre_denaturation_time is not None:
      stages.append(
        Stage(
          steps=[Step(temperature=pre_denaturation_temp, hold_seconds=pre_denaturation_time)],
          repeats=1,
        )
      )

    stages.append(
      Stage(
        steps=[
          Step(temperature=denaturation_temp, hold_seconds=denaturation_time),
          Step(temperature=annealing_temp, hold_seconds=annealing_time),
          Step(temperature=extension_temp, hold_seconds=extension_time),
        ],
        repeats=num_cycles,
      )
    )

    if final_extension_temp is not None and final_extension_time is not None:
      stages.append(
        Stage(
          steps=[Step(temperature=final_extension_temp, hold_seconds=final_extension_time)],
          repeats=1,
        )
      )

    if storage_temp is not None and storage_time is not None:
      stages.append(
        Stage(steps=[Step(temperature=storage_temp, hold_seconds=storage_time)], repeats=1)
      )

    protocol = Protocol(stages=stages)
    return await self.run_protocol(
      protocol=protocol, block_max_volume=block_max_volume, **backend_kwargs
    )

  async def get_block_current_temperature(self, **backend_kwargs) -> List[float]:
    return await self.backend.get_block_current_temperature(**backend_kwargs)

  async def get_block_target_temperature(self, **backend_kwargs) -> List[float]:
    return await self.backend.get_block_target_temperature(**backend_kwargs)

  async def get_lid_current_temperature(self, **backend_kwargs) -> List[float]:
    return await self.backend.get_lid_current_temperature(**backend_kwargs)

  async def get_lid_target_temperature(self, **backend_kwargs) -> List[float]:
    return await self.backend.get_lid_target_temperature(**backend_kwargs)

  async def get_lid_open(self, **backend_kwargs) -> bool:
    return await self._thermocycling.get_lid_open()

  async def get_lid_status(self, **backend_kwargs) -> LidStatus:
    return await self.backend.get_lid_status(**backend_kwargs)

  async def get_block_status(self, **backend_kwargs) -> BlockStatus:
    return await self.backend.get_block_status(**backend_kwargs)

  async def get_hold_time(self, **backend_kwargs) -> float:
    return await self._thermocycling.get_hold_time()

  async def get_current_cycle_index(self, **backend_kwargs) -> int:
    return await self._thermocycling.get_current_cycle_index()

  async def get_total_cycle_count(self, **backend_kwargs) -> int:
    return await self._thermocycling.get_total_cycle_count()

  async def get_current_step_index(self, **backend_kwargs) -> int:
    return await self._thermocycling.get_current_step_index()

  async def get_total_step_count(self, **backend_kwargs) -> int:
    return await self._thermocycling.get_total_step_count()

  async def wait_for_block(self, timeout: float = 600, tolerance: float = 0.5, **backend_kwargs):
    targets = await self.get_block_target_temperature(**backend_kwargs)
    start = time.time()
    while time.time() - start < timeout:
      currents = await self.get_block_current_temperature(**backend_kwargs)
      if all(abs(current - target) < tolerance for current, target in zip(currents, targets)):
        return
      await asyncio.sleep(1)
    raise TimeoutError("Block temperature timeout.")

  async def wait_for_lid(self, timeout: float = 1200, tolerance: float = 0.5, **backend_kwargs):
    try:
      targets = await self.get_lid_target_temperature(**backend_kwargs)
    except (RuntimeError, NotImplementedError):
      targets = None
    start = time.time()
    while time.time() - start < timeout:
      if targets is not None:
        currents = await self.get_lid_current_temperature(**backend_kwargs)
        if all(abs(current - target) < tolerance for current, target in zip(currents, targets)):
          return
      else:
        status = await self.get_lid_status(**backend_kwargs)
        if status in (LidStatus.IDLE, LidStatus.HOLDING_AT_TARGET):
          return
      await asyncio.sleep(1)
    raise TimeoutError("Lid temperature timeout.")

  async def is_profile_running(self, **backend_kwargs) -> bool:
    return await self._thermocycling.is_profile_running()

  async def wait_for_profile_completion(self, poll_interval: float = 60.0, **backend_kwargs):
    await self._thermocycling.wait_for_profile_completion(poll_interval=poll_interval)

  def serialize(self) -> dict:
    return {**Machine.serialize(self), **ResourceHolder.serialize(self)}
