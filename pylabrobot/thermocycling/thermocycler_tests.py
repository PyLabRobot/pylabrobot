import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling import (
  Thermocycler,
  ThermocyclerBackend,
  ThermocyclerChatterboxBackend,
)
from pylabrobot.thermocycling.standard import Step


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
  mock.run_profile = AsyncMock()
  mock.get_block_current_temperature = AsyncMock(return_value=25.0)
  mock.get_block_target_temperature = AsyncMock(return_value=None)
  mock.get_lid_current_temperature = AsyncMock(return_value=25.0)
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
      denaturation_temp=98.0,
      denaturation_time=10.0,
      annealing_temp=55.0,
      annealing_time=30.0,
      extension_temp=72.0,
      extension_time=60.0,
      num_cycles=2,
      block_max_volume=25.0,
      lid_temperature=105.0,
      pre_denaturation_temp=95.0,
      pre_denaturation_time=180.0,
      final_extension_temp=72.0,
      final_extension_time=300.0,
      storage_temp=4.0,
      storage_time=600.0,
    )

    self.tc.backend.set_lid_temperature.assert_called_once_with(105.0)  # type: ignore

    expected_profile = [
      Step(temperature=95.0, hold_seconds=180.0),
      Step(temperature=98.0, hold_seconds=10.0),
      Step(temperature=55.0, hold_seconds=30.0),
      Step(temperature=72.0, hold_seconds=60.0),
      Step(temperature=98.0, hold_seconds=10.0),
      Step(temperature=55.0, hold_seconds=30.0),
      Step(temperature=72.0, hold_seconds=60.0),
      Step(temperature=72.0, hold_seconds=300.0),
      Step(temperature=4.0, hold_seconds=600.0),
    ]

    self.tc.backend.run_profile.assert_called_once_with(expected_profile, 25.0)  # type: ignore

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
