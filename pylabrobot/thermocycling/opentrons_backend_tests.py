"""Tests for the OpentronsThermocyclerBackend."""

from typing import Generator
from unittest.mock import patch

import pytest

from pylabrobot.thermocycling.opentrons_backend import OpentronsThermocyclerBackend


@pytest.fixture
def thermocycler_backend() -> Generator[OpentronsThermocyclerBackend, None, None]:
  """Fixture for OpentronsThermocyclerBackend."""
  with patch("pylabrobot.thermocycling.opentrons_backend.USE_OT", True):
    yield OpentronsThermocyclerBackend(opentrons_id="test_id")


@patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
@pytest.mark.asyncio
async def test_find_module_raises_error_if_not_found(
  mock_list_connected_modules, thermocycler_backend
):
  mock_list_connected_modules.return_value = [{"id": "some_other_id", "data": {}}]
  with pytest.raises(RuntimeError, match="Module 'test_id' not found"):
    await thermocycler_backend.get_lid_status()


@patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_open_lid")
@pytest.mark.asyncio
async def test_open_lid(mock_open_lid, thermocycler_backend):
  """Test for `open_lid`"""
  await thermocycler_backend.open_lid()
  mock_open_lid.assert_called_once_with(module_id="test_id")


@patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_close_lid")
@pytest.mark.asyncio
async def test_close_lid(mock_close_lid, thermocycler_backend):
  """Test for `close_lid`"""
  await thermocycler_backend.close_lid()
  mock_close_lid.assert_called_once_with(module_id="test_id")


@patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_set_block_temperature")
@pytest.mark.asyncio
async def test_set_block_temperature(mock_set_block_temp, thermocycler_backend):
  """Test for `set_block_temperature`"""
  await thermocycler_backend.set_block_temperature(95.0)
  mock_set_block_temp.assert_called_once_with(celsius=95.0, module_id="test_id")


@patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_set_lid_temperature")
@pytest.mark.asyncio
async def test_set_lid_temperature(mock_set_lid_temp, thermocycler_backend):
  """Test for `set_lid_temperature`"""
  await thermocycler_backend.set_lid_temperature(105.0)
  mock_set_lid_temp.assert_called_once_with(celsius=105.0, module_id="test_id")


@patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_deactivate_block")
@pytest.mark.asyncio
async def test_deactivate_block(mock_deactivate_block, thermocycler_backend):
  """Test for `deactivate_block`"""
  await thermocycler_backend.deactivate_block()
  mock_deactivate_block.assert_called_once_with(module_id="test_id")


@patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_deactivate_lid")
@pytest.mark.asyncio
async def test_deactivate_lid(mock_deactivate_lid, thermocycler_backend):
  """Test for `deactivate_lid`"""
  await thermocycler_backend.deactivate_lid()
  mock_deactivate_lid.assert_called_once_with(module_id="test_id")


@patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_run_profile_no_wait")
@pytest.mark.asyncio
async def test_run_profile(mock_run_profile, thermocycler_backend):
  """Test for `run_profile`"""
  profile = [{"celsius": 95, "holdSeconds": 10}]
  await thermocycler_backend.run_profile(profile, 50.0)
  mock_run_profile.assert_called_once_with(
    profile=profile, block_max_volume=50.0, module_id="test_id"
  )


@patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
@pytest.mark.asyncio
async def test_getters_return_correct_data(mock_list_connected_modules, thermocycler_backend):
  """Test for getters returning correct data"""
  mock_data = {
    "id": "test_id",
    "data": {
      "currentTemperature": 25.5,
      "targetTemperature": 95.0,
      "lidTemperature": 37.1,
      "lidTargetTemperature": 105.0,
      "lidStatus": "open",
      "holdTime": 12.0,
      "currentCycleIndex": 2,
      "totalCycleCount": 10,
      "currentStepIndex": 1,
      "totalStepCount": 3,
    },
  }
  mock_list_connected_modules.return_value = [mock_data]

  assert await thermocycler_backend.get_block_current_temperature() == 25.5
  assert await thermocycler_backend.get_block_target_temperature() == 95.0
  assert await thermocycler_backend.get_lid_current_temperature() == 37.1
  assert await thermocycler_backend.get_lid_target_temperature() == 105.0
  assert await thermocycler_backend.get_lid_status() == "open"
  assert await thermocycler_backend.get_hold_time() == 12.0
  assert await thermocycler_backend.get_current_cycle_index() == 2
  assert await thermocycler_backend.get_total_cycle_count() == 10
  assert await thermocycler_backend.get_current_step_index() == 1
  assert await thermocycler_backend.get_total_step_count() == 3
