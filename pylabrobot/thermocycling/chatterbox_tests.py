"""Tests for the ThermocyclerChatterboxBackend."""

import pytest

from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling import Thermocycler, ThermocyclerChatterboxBackend


@pytest.fixture
def tc_dev() -> Thermocycler:
  """A pytest fixture to provide a standard thermocycler with a chatterbox backend."""
  return Thermocycler(
    name="tc_test",
    size_x=1,
    size_y=1,
    size_z=1,
    backend=ThermocyclerChatterboxBackend(),
    child_location=Coordinate.zero(),
  )


@pytest.mark.asyncio
async def test_chatterbox_run_profile(tc_dev: Thermocycler, capsys):
  """Test that the chatterbox produces the correct log for a generic profile."""
  profile = [
    {"celsius": 95.0, "holdSeconds": 10},
    {"celsius": 55.0, "holdSeconds": 20},
  ]
  await tc_dev.run_profile(profile, block_max_volume=25.0)
  await tc_dev.wait_for_profile_completion(0.01)

  captured = capsys.readouterr()
  log = captured.out

  # Assert that all the key log lines are present and in order
  assert "Running profile:" in log
  assert "step#    temp (C)        hold (s)" in log
  assert "1        95.0            10.0" in log
  assert "2        55.0            20.0" in log
  assert "- Starting Step 1/2: setting block to 95.0°C." in log
  assert "- Step 1/2: hold for 10.0s complete." in log
  assert "- Starting Step 2/2: setting block to 55.0°C." in log
  assert "- Step 2/2: hold for 20.0s complete." in log
  assert "- Profile finished." in log


@pytest.mark.asyncio
async def test_chatterbox_run_pcr_profile(tc_dev: Thermocycler, capsys):
  """Test that the chatterbox produces the correct log for a PCR profile."""
  await tc_dev.run_pcr_profile(
    denaturation_temp=98.0,
    denaturation_time=15.0,
    annealing_temp=60.0,
    annealing_time=15.0,
    extension_temp=72.0,
    extension_time=20.0,
    num_cycles=2,
    block_max_volume=25.0,
    lid_temperature=105.0,
    storage_temp=4.0,
    storage_time=1.0,
  )
  await tc_dev.wait_for_profile_completion(0.01)

  captured = capsys.readouterr()
  log = captured.out

  assert "Running profile:" in log
  assert "step#    temp (C)        hold (s)" in log
  assert "7        4.0             1.0" in log  # Check the last step is correct
  assert "- Starting Step 1/7: setting block to 98.0°C." in log
  assert "- Step 1/7: hold for 15.0s complete." in log
  assert "- Starting Step 7/7: setting block to 4.0°C." in log
  assert "- Step 7/7: hold for 1.0s complete." in log
  assert "- Profile finished." in log


@pytest.mark.asyncio
async def test_chatterbox_deactivate_cancels_profile(tc_dev: Thermocycler, capsys):
  """Test that deactivating the block prints a cancellation message."""
  await tc_dev.run_profile([{"celsius": 50.0, "holdSeconds": 10}], 25.0)
  await tc_dev.deactivate_block()

  captured = capsys.readouterr()
  assert "- A running profile was cancelled." in captured.out
