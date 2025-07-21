"""Tests for the high-level Thermocycler resource and its models."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.thermocycling import (
  Thermocycler,
  ThermocyclerBackend,
  ThermocyclerChatterboxBackend,
  OpentronsThermocyclerModuleV1,
)


@pytest.fixture
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
  mock.get_lid_status = AsyncMock(return_value="closed")
  mock.get_hold_time = AsyncMock(return_value=0.0)
  mock.get_current_cycle_index = AsyncMock(return_value=0)
  mock.get_total_cycle_count = AsyncMock(return_value=0)
  mock.get_current_step_index = AsyncMock(return_value=0)
  mock.get_total_step_count = AsyncMock(return_value=0)
  return mock


@pytest.fixture
def tc_dev(mock_backend: MagicMock) -> Thermocycler:
  """Pytest fixture to create a Thermocycler with the mock backend."""
  return Thermocycler(
    name="test_tc",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=mock_backend,
    child_location=Coordinate(0, 0, 0),
  )


def test_thermocycler_serialization(tc_dev: Thermocycler):
  """Test that the high-level resource serializes and deserializes correctly."""
  tc_dev.backend = ThermocyclerChatterboxBackend()
  serialized = tc_dev.serialize()
  deserialized = Thermocycler.deserialize(serialized)
  assert tc_dev == deserialized


def test_opentrons_v1_serialization():
  """Test that the Opentrons-specific resource model serializes correctly."""
  tc_model = OpentronsThermocyclerModuleV1(
    name="test_v1_tc",
    opentrons_id="test_id",
    child=ItemizedResource(name="plate", size_x=1, size_y=1, size_z=1, ordered_items={}),
  )
  serialized = tc_model.serialize()
  assert "opentrons_id" in serialized
  assert serialized["opentrons_id"] == "test_id"
  deserialized = OpentronsThermocyclerModuleV1.deserialize(serialized)
  assert tc_model == deserialized


@pytest.mark.asyncio
async def test_run_pcr_profile_builds_correct_profile(tc_dev: Thermocycler, monkeypatch):
  """Test that run_pcr_profile correctly builds the flat step list."""

  async def mock_wait_for_lid(*args, **kwargs):
    pass

  monkeypatch.setattr(tc_dev, "wait_for_lid", mock_wait_for_lid)

  await tc_dev.run_pcr_profile(
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

  tc_dev.backend.set_lid_temperature.assert_called_once_with(105.0)  # type: ignore

  expected_profile = [
    {"celsius": 95.0, "holdSeconds": 180.0},
    {"celsius": 98.0, "holdSeconds": 10.0},
    {"celsius": 55.0, "holdSeconds": 30.0},
    {"celsius": 72.0, "holdSeconds": 60.0},
    {"celsius": 98.0, "holdSeconds": 10.0},
    {"celsius": 55.0, "holdSeconds": 30.0},
    {"celsius": 72.0, "holdSeconds": 60.0},
    {"celsius": 72.0, "holdSeconds": 300.0},
    {"celsius": 4.0, "holdSeconds": 600.0},
  ]

  tc_dev.backend.run_profile.assert_called_once_with(expected_profile, 25.0)  # type: ignore


@pytest.mark.asyncio
async def test_wait_for_profile_completion(tc_dev: Thermocycler, monkeypatch):
  """Test that wait_for_profile_completion correctly polls is_profile_running."""
  tc_dev.backend.get_hold_time.side_effect = [10.0, 5.0, 0.0]  # type: ignore

  async def mock_sleep(*args, **kwargs):
    pass

  monkeypatch.setattr("asyncio.sleep", mock_sleep)
  await tc_dev.wait_for_profile_completion(poll_interval=0.01)
  assert tc_dev.backend.get_hold_time.call_count == 3  # type: ignore


@pytest.mark.parametrize(
  "hold, cycle, total_cycles, step, total_steps, expected",
  [
    (10.0, 1, 10, 1, 3, True),
    (0.0, 5, 10, 1, 3, True),
    (0.0, 10, 10, 1, 3, True),
    (0.0, 10, 10, 3, 3, False),
    (0.0, 1, 1, 1, 1, False),
  ],
)
@pytest.mark.asyncio
async def test_is_profile_running_logic(
  tc_dev: Thermocycler, hold, cycle, total_cycles, step, total_steps, expected
):
  """Test that `is_profile_running` returns the correct boolean based on various profile states."""
  tc_dev.backend.get_hold_time.return_value = hold  # type: ignore
  tc_dev.backend.get_current_cycle_index.return_value = cycle  # type: ignore
  tc_dev.backend.get_total_cycle_count.return_value = total_cycles  # type: ignore
  tc_dev.backend.get_current_step_index.return_value = step  # type: ignore
  tc_dev.backend.get_total_step_count.return_value = total_steps  # type: ignore
  assert await tc_dev.is_profile_running() is expected
