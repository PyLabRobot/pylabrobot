import asyncio
import unittest
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.thermocycling import (
  Thermocycler,
  ThermocyclerBackend,
  ThermocyclerChatterboxBackend,
)
from pylabrobot.legacy.thermocycling.standard import (
  BlockStatus,
  LidStatus,
  Protocol,
  Stage,
  Step,
)
from pylabrobot.resources import Coordinate
from pylabrobot.testing import AnyioTestBase


def mock_backend() -> MagicMock:
  """Creates a fully compliant mock of the ThermocyclerBackend using a spec."""
  mock = MagicMock(spec=ThermocyclerBackend)
  mock.setup = AsyncMock()
  mock.stop = AsyncMock()
  mock.open_lid = AsyncMock()
  mock.close_lid = AsyncMock()
  mock.set_block_temperature = AsyncMock()
  mock.set_lid_temperature = AsyncMock()
  mock.deactivate_block = AsyncMock()
  mock.deactivate_lid = AsyncMock()
  mock.run_protocol = AsyncMock()
  mock.get_block_current_temperature = AsyncMock(return_value=[25.0])
  mock.get_block_target_temperature = AsyncMock(return_value=None)
  mock.get_lid_current_temperature = AsyncMock(return_value=[25.0])
  mock.get_lid_target_temperature = AsyncMock(return_value=None)
  mock.get_lid_open = AsyncMock(return_value=False)
  mock.get_lid_temperature_status = AsyncMock(return_value="idle")
  mock.get_block_status = AsyncMock(return_value="idle")
  mock.get_hold_time = AsyncMock(return_value=0.0)
  mock.get_current_cycle_index = AsyncMock(return_value=0)
  mock.get_total_cycle_count = AsyncMock(return_value=0)
  mock.get_current_step_index = AsyncMock(return_value=0)
  mock.get_total_step_count = AsyncMock(return_value=0)
  return mock


class ThermocyclerTests(unittest.IsolatedAsyncioTestCase):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.tc = Thermocycler(
      name="test_tc",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=mock_backend(),
      child_location=Coordinate(0, 0, 0),
    )

  def test_thermocycler_serialization(self):
    """Test that the high-level resource serializes and deserializes correctly."""
    self.tc.backend = ThermocyclerChatterboxBackend()
    serialized = self.tc.serialize()
    deserialized = Thermocycler.deserialize(serialized)
    assert self.tc == deserialized

  async def test_run_pcr_profile_builds_correct_profile(self):
    """Test that run_pcr_profile correctly builds the flat step list."""

    async def mock_wait_for_lid(*args, **kwargs):
      pass

    self.tc.wait_for_lid = mock_wait_for_lid  # type: ignore

    await self.tc.run_pcr_profile(
      denaturation_temp=[98.0],
      denaturation_time=10.0,
      annealing_temp=[55.0],
      annealing_time=30.0,
      extension_temp=[72.0],
      extension_time=60.0,
      num_cycles=2,
      block_max_volume=25.0,
      lid_temperature=[105.0],
      pre_denaturation_temp=[95.0],
      pre_denaturation_time=180.0,
      final_extension_temp=[72.0],
      final_extension_time=300.0,
      storage_temp=[4.0],
      storage_time=600.0,
    )

    self.tc.backend.set_lid_temperature.assert_called_once_with([105.0])  # type: ignore

    expected_protocol = Protocol(
      stages=[
        Stage(steps=[Step(temperature=[95.0], hold_seconds=180.0, rate=None)], repeats=1),
        Stage(
          steps=[
            Step(temperature=[98.0], hold_seconds=10.0, rate=None),
            Step(temperature=[55.0], hold_seconds=30.0, rate=None),
            Step(temperature=[72.0], hold_seconds=60.0, rate=None),
          ],
          repeats=2,
        ),
        Stage(steps=[Step(temperature=[72.0], hold_seconds=300.0, rate=None)], repeats=1),
        Stage(steps=[Step(temperature=[4.0], hold_seconds=600.0, rate=None)], repeats=1),
      ]
    )

    self.tc.backend.run_protocol.assert_called_once_with(expected_protocol, 25.0)  # type: ignore

  async def test_wait_for_profile_completion(self):
    """Test that wait_for_profile_completion correctly polls is_profile_running."""
    self.tc.backend.get_hold_time.side_effect = [10.0, 5.0, 0.0]  # type: ignore

    # Patch asyncio.sleep to a no-op for the test.
    original_sleep = asyncio.sleep

    async def mock_sleep(*args, **kwargs):
      pass

    asyncio.sleep = mock_sleep
    try:
      await self.tc.wait_for_profile_completion(poll_interval=0.01)
      assert self.tc.backend.get_hold_time.call_count == 3  # type: ignore
    finally:
      asyncio.sleep = original_sleep

  async def test_is_profile_running_logic(self):
    """Test that `is_profile_running` returns the correct boolean based on various profile states."""
    test_cases = [
      (10.0, 1, 10, 1, 3, True),
      (0.0, 5, 10, 1, 3, True),
      (0.0, 9, 10, 1, 3, True),
      (0.0, 9, 10, 3, 3, False),
      (0.0, 1, 1, 1, 1, False),
    ]
    for hold, cycle, total_cycles, step, total_steps, expected in test_cases:
      self.tc.backend.get_hold_time.return_value = hold  # type: ignore
      self.tc.backend.get_current_cycle_index.return_value = cycle  # type: ignore
      self.tc.backend.get_total_cycle_count.return_value = total_cycles  # type: ignore
      self.tc.backend.get_current_step_index.return_value = step  # type: ignore
      self.tc.backend.get_total_step_count.return_value = total_steps  # type: ignore
      print(f"Testing with hold={hold}, cycle={cycle}, total_cycles={total_cycles}, ")
      assert await self.tc.is_profile_running() is expected

  async def test_is_profile_running_raises_if_no_profile_running(self):
    """RuntimeError from get_hold_time should propagate."""
    self.tc.backend.get_hold_time.side_effect = RuntimeError("No profile is running")  # type: ignore

    with self.assertRaises(RuntimeError):
      await self.tc.is_profile_running()

  async def test_is_profile_running_preserves_not_implemented_error(self):
    """Unsupported backends should still surface NotImplementedError."""
    self.tc.backend.get_hold_time.side_effect = NotImplementedError  # type: ignore

    with self.assertRaises(NotImplementedError):
      await self.tc.is_profile_running()


class RecordingThermocyclerBackend(ThermocyclerBackend):
  """Thermocycler backend double recording every query and command it receives.

  Each query awaits a checkpoint before recording itself, so a query that is cancelled
  before it runs leaves no trace and a test can assert that polling stopped.
  """

  def __init__(
    self,
    block_current: Optional[List[float]] = None,
    block_target: Optional[List[float]] = None,
    lid_current: Optional[List[float]] = None,
    lid_target: Optional[List[float]] = None,
    lid_status: LidStatus = LidStatus.IDLE,
    hold_time: float = 0.0,
  ) -> None:
    super().__init__()
    self.block_current = [25.0] if block_current is None else block_current
    self.block_target = block_target
    self.lid_current = [25.0] if lid_current is None else lid_current
    self.lid_target = lid_target
    self.lid_status = lid_status
    self.hold_time = hold_time
    self.polls: List[str] = []
    self.commands: List[str] = []
    self.iteration_polled = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("thermocycler backend double does not accept lifespan options")

  async def open_lid(self) -> None:
    self.commands.append("open_lid")

  async def close_lid(self) -> None:
    self.commands.append("close_lid")

  async def set_block_temperature(self, temperature: List[float]) -> None:
    self.commands.append(f"set_block:{temperature}")

  async def set_lid_temperature(self, temperature: List[float]) -> None:
    self.commands.append(f"set_lid:{temperature}")

  async def deactivate_block(self) -> None:
    self.commands.append("deactivate_block")

  async def deactivate_lid(self) -> None:
    self.commands.append("deactivate_lid")

  async def run_protocol(self, protocol: Protocol, block_max_volume: float) -> None:
    self.commands.append(f"run_protocol:{block_max_volume}")

  async def get_block_current_temperature(self) -> List[float]:
    await anyio.sleep(0)
    self.polls.append("block_current")
    return self.block_current

  async def get_block_target_temperature(self) -> List[float]:
    await anyio.sleep(0)
    self.polls.append("block_target")
    if self.block_target is None:
      raise RuntimeError("No block target temperature is set.")
    return self.block_target

  async def get_lid_current_temperature(self) -> List[float]:
    await anyio.sleep(0)
    self.polls.append("lid_current")
    return self.lid_current

  async def get_lid_target_temperature(self) -> List[float]:
    await anyio.sleep(0)
    self.polls.append("lid_target")
    if self.lid_target is None:
      raise RuntimeError("No lid target temperature is set.")
    return self.lid_target

  async def get_lid_open(self) -> bool:
    await anyio.sleep(0)
    self.polls.append("lid_open")
    return False

  async def get_lid_status(self) -> LidStatus:
    await anyio.sleep(0)
    self.polls.append("lid_status")
    return self.lid_status

  async def get_block_status(self) -> BlockStatus:
    await anyio.sleep(0)
    self.polls.append("block_status")
    return BlockStatus.IDLE

  async def get_hold_time(self) -> float:
    await anyio.sleep(0)
    self.polls.append("hold_time")
    return self.hold_time

  async def get_current_cycle_index(self) -> int:
    await anyio.sleep(0)
    self.polls.append("cycle_index")
    return 0

  async def get_total_cycle_count(self) -> int:
    await anyio.sleep(0)
    self.polls.append("total_cycles")
    return 0

  async def get_current_step_index(self) -> int:
    await anyio.sleep(0)
    self.polls.append("step_index")
    return 0

  async def get_total_step_count(self) -> int:
    await anyio.sleep(0)
    self.polls.append("total_steps")
    return 0


class LidPollingThermocyclerBackend(RecordingThermocyclerBackend):
  """Recording backend signalling the test after each complete lid polling iteration."""

  async def get_lid_current_temperature(self) -> List[float]:
    currents = await super().get_lid_current_temperature()
    self.iteration_polled.set()
    return currents


class ProfilePollingThermocyclerBackend(RecordingThermocyclerBackend):
  """Recording backend signalling the test after each complete profile polling iteration."""

  async def get_total_step_count(self) -> int:
    total = await super().get_total_step_count()
    self.iteration_polled.set()
    return total


def make_thermocycler(backend: ThermocyclerBackend) -> Thermocycler:
  """Build a thermocycler frontend over a software backend double."""
  return Thermocycler(
    name="wait_thermocycler",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate(0, 0, 0),
  )


class TestThermocyclerWaits(AnyioTestBase):
  async def test_wait_for_block_uses_one_deadline(self) -> None:
    backend = RecordingThermocyclerBackend(block_current=[25.0], block_target=[95.0])
    thermocycler = make_thermocycler(backend)

    start = anyio.current_time()
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="Block temperature timeout."):
        await thermocycler.wait_for_block(timeout=0.01)
    elapsed = anyio.current_time() - start

    assert backend.polls == ["block_target", "block_current"]
    assert elapsed < 0.5

  async def test_wait_for_block_rejects_none_timeout(self) -> None:
    backend = RecordingThermocyclerBackend(block_current=[25.0], block_target=[95.0])
    thermocycler = make_thermocycler(backend)

    with anyio.fail_after(0.2):
      with pytest.raises(TypeError, match="timeout"):
        await thermocycler.wait_for_block(timeout=None)  # type: ignore[arg-type]

    assert backend.polls == []
    assert backend.commands == []

  async def test_wait_for_lid_uses_one_deadline(self) -> None:
    backend = RecordingThermocyclerBackend(lid_current=[25.0], lid_target=[105.0])
    thermocycler = make_thermocycler(backend)

    start = anyio.current_time()
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="Lid temperature timeout."):
        await thermocycler.wait_for_lid(timeout=0.01)
    elapsed = anyio.current_time() - start

    assert backend.polls == ["lid_target", "lid_current"]
    assert elapsed < 0.5

  async def test_wait_for_lid_rejects_none_timeout(self) -> None:
    backend = RecordingThermocyclerBackend(lid_current=[25.0], lid_target=[105.0])
    thermocycler = make_thermocycler(backend)

    with anyio.fail_after(0.2):
      with pytest.raises(TypeError, match="timeout"):
        await thermocycler.wait_for_lid(timeout=None)  # type: ignore[arg-type]

    assert backend.polls == []
    assert backend.commands == []

  async def test_wait_for_lid_status_uses_enum_values(self) -> None:
    for status in (LidStatus.IDLE, LidStatus.HOLDING_AT_TARGET):
      backend = RecordingThermocyclerBackend(lid_status=status)
      thermocycler = make_thermocycler(backend)

      with anyio.fail_after(1):
        await thermocycler.wait_for_lid()

      assert backend.polls == ["lid_target", "lid_status"]

  async def test_wait_for_lid_cancels_during_poll_sleep(self) -> None:
    backend = LidPollingThermocyclerBackend(lid_current=[25.0], lid_target=[105.0])
    thermocycler = make_thermocycler(backend)

    start = anyio.current_time()
    with anyio.fail_after(1):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(thermocycler.wait_for_lid)
        await backend.iteration_polled.wait()
        task_group.cancel_scope.cancel()
    elapsed = anyio.current_time() - start

    assert backend.polls == ["lid_target", "lid_current"]
    assert elapsed < 0.5

  async def test_wait_for_profile_completion_cancels_during_poll_sleep(self) -> None:
    backend = ProfilePollingThermocyclerBackend(hold_time=10.0)
    thermocycler = make_thermocycler(backend)

    start = anyio.current_time()
    with anyio.fail_after(1):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(thermocycler.wait_for_profile_completion)
        await backend.iteration_polled.wait()
        task_group.cancel_scope.cancel()
    elapsed = anyio.current_time() - start

    assert backend.polls == [
      "hold_time",
      "cycle_index",
      "total_cycles",
      "step_index",
      "total_steps",
    ]
    assert elapsed < 0.5
