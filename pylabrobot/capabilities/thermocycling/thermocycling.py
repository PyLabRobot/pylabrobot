import asyncio
from typing import List, Optional

from pylabrobot.capabilities.capability import Capability
from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability
from pylabrobot.serializer import SerializableMixin

from .backend import ThermocyclingBackend
from .standard import Protocol, Stage, Step


class ThermocyclingCapability(Capability):
  """Thermocycling capability.

  Owns protocol execution (delegated to the backend) and lid control.
  Block and lid temperature control for ad-hoc use (outside of protocol runs)
  is accessed via the `block` and `lid` TemperatureControlCapability instances.
  """

  def __init__(
    self,
    backend: ThermocyclingBackend,
    block: TemperatureControlCapability,
    lid: TemperatureControlCapability,
  ):
    super().__init__(backend=backend)
    self.backend: ThermocyclingBackend = backend
    self.block = block
    self.lid = lid

  async def open_lid(self) -> None:
    await self.backend.open_lid()

  async def close_lid(self) -> None:
    await self.backend.close_lid()

  async def get_lid_open(self) -> bool:
    return await self.backend.get_lid_open()

  async def run_protocol(self, protocol: Protocol, block_max_volume: float, backend_params: Optional[SerializableMixin] = None) -> None:
    """Execute a thermocycler protocol.

    Args:
      protocol: Protocol containing stages with steps and repeats.
      block_max_volume: Maximum block volume in uL.
      backend_params: Backend-specific parameters.
    """
    num_zones = len(protocol.stages[0].steps[0].temperature)
    for stage in protocol.stages:
      for i, step in enumerate(stage.steps):
        if len(step.temperature) != num_zones:
          raise ValueError(
            f"All steps must have the same number of temperatures. "
            f"Expected {num_zones}, got {len(step.temperature)} in step {i}."
          )

    await self.backend.run_protocol(protocol, block_max_volume, backend_params=backend_params)

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
    lid_temperature: float,
    pre_denaturation_temp: Optional[List[float]] = None,
    pre_denaturation_time: Optional[float] = None,
    final_extension_temp: Optional[List[float]] = None,
    final_extension_time: Optional[float] = None,
    storage_temp: Optional[List[float]] = None,
    storage_time: Optional[float] = None,
  ) -> None:
    """Build and run a standard PCR profile.

    Sets the lid temperature first, waits for it, then runs the protocol.
    """
    await self.lid.set_temperature(lid_temperature)
    await self.lid.wait_for_temperature()

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

    await self.run_protocol(protocol=Protocol(stages=stages), block_max_volume=block_max_volume)

  async def get_hold_time(self) -> float:
    return await self.backend.get_hold_time()

  async def get_current_cycle_index(self) -> int:
    return await self.backend.get_current_cycle_index()

  async def get_total_cycle_count(self) -> int:
    return await self.backend.get_total_cycle_count()

  async def get_current_step_index(self) -> int:
    return await self.backend.get_current_step_index()

  async def get_total_step_count(self) -> int:
    return await self.backend.get_total_step_count()

  async def is_profile_running(self) -> bool:
    """Return True if a protocol is still in progress."""
    hold = await self.backend.get_hold_time()
    cycle = await self.backend.get_current_cycle_index()
    total_cycles = await self.backend.get_total_cycle_count()
    step = await self.backend.get_current_step_index()
    total_steps = await self.backend.get_total_step_count()

    if hold > 0:
      return True
    if cycle < total_cycles - 1:
      return True
    if cycle == total_cycles - 1 and step < total_steps - 1:
      return True
    return False

  async def wait_for_profile_completion(self, poll_interval: float = 60.0) -> None:
    """Block until the protocol finishes."""
    while await self.is_profile_running():
      await asyncio.sleep(poll_interval)
