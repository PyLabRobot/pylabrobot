import unittest
from contextlib import redirect_stdout
from io import StringIO

from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling import Thermocycler, ThermocyclerChatterboxBackend
from pylabrobot.thermocycling.standard import Step


class TestThermocyclerChatterbox(unittest.IsolatedAsyncioTestCase):
  def __init__(self, methodName="runTest"):
    super().__init__(methodName)
    self.tc = Thermocycler(
      name="tc_test",
      size_x=1,
      size_y=1,
      size_z=1,
      backend=ThermocyclerChatterboxBackend(),
      child_location=Coordinate.zero(),
    )

  async def test_chatterbox_run_profile(self):
    """Test that the chatterbox produces the correct log for a generic profile."""
    profile = [
      Step(temperature=95.0, hold_seconds=10),
      Step(temperature=55.0, hold_seconds=20),
    ]

    log_buffer = StringIO()
    with redirect_stdout(log_buffer):
      await self.tc.run_profile(profile, block_max_volume=25.0)
      await self.tc.wait_for_profile_completion(0.01)
    log = log_buffer.getvalue()

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

  async def test_chatterbox_run_pcr_profile(self):
    """Test that the chatterbox produces the correct log for a PCR profile."""
    log_buffer = StringIO()
    with redirect_stdout(log_buffer):
      await self.tc.run_pcr_profile(
        denaturation_temp=98.0,
        denaturation_time=15.0,
        annealing_temp=60.0,
        annealing_time=15.0,
        extension_temp=72.0,
        extension_time=20.0,
        num_cycles=2,
        block_max_volume=25.0,
        lid_temperature=[105.0],
        storage_temp=4.0,
        storage_time=1.0,
      )
      await self.tc.wait_for_profile_completion(0.01)
    log = log_buffer.getvalue()

    assert "Running profile:" in log
    assert "step#    temp (C)        hold (s)" in log
    assert "7        4.0             1.0" in log  # Check the last step is correct
    assert "- Starting Step 1/7: setting block to 98.0°C." in log
    assert "- Step 1/7: hold for 15.0s complete." in log
    assert "- Starting Step 7/7: setting block to 4.0°C." in log
    assert "- Step 7/7: hold for 1.0s complete." in log
    assert "- Profile finished." in log

  async def test_chatterbox_deactivate_cancels_profile(self):
    """Test that deactivating the block prints a cancellation message."""
    log_buffer = StringIO()
    with redirect_stdout(log_buffer):
      await self.tc.run_profile([Step(temperature=50.0, hold_seconds=10)], 25.0)
      await self.tc.deactivate_block()

    log = log_buffer.getvalue()
    assert "- A running profile was cancelled." in log
