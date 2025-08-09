import unittest
from contextlib import redirect_stdout
from io import StringIO

from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling import Thermocycler, ThermocyclerChatterboxBackend
from pylabrobot.thermocycling.standard import Protocol, Stage, Step


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
    protocol = Protocol(
      stages=[
        Stage(
          steps=[
            Step(temperature=[95.0], hold_seconds=10),
            Step(temperature=[55.0], hold_seconds=20),
          ],
          repeats=1,
        )
      ]
    )

    log_buffer = StringIO()
    with redirect_stdout(log_buffer):
      await self.tc.run_protocol(protocol, block_max_volume=25.0)
      await self.tc.wait_for_profile_completion(0.01)
    log = log_buffer.getvalue()

    print(log)
    assert "Running protocol:" in log
    assert "- Stage 1/1: 2 step(s) x 1 repeat(s)" in log
    assert "  - Repeat 1/1:" in log
    assert "    - Step 1/2 (repeat 1/1): temperature(s) = 95.0°C, hold = 10.0s" in log
    assert "    - Step 2/2 (repeat 1/1): temperature(s) = 55.0°C, hold = 20.0s" in log

  async def test_chatterbox_run_pcr_profile(self):
    """Test that the chatterbox produces the correct log for a PCR profile."""
    log_buffer = StringIO()
    with redirect_stdout(log_buffer):
      await self.tc.run_pcr_profile(
        denaturation_temp=[98.0],
        denaturation_time=15.0,
        annealing_temp=[60.0],
        annealing_time=15.0,
        extension_temp=[72.0],
        extension_time=20.0,
        num_cycles=2,
        block_max_volume=25.0,
        lid_temperature=[105.0],
        storage_temp=[4.0],
        storage_time=1.0,
      )
      await self.tc.wait_for_profile_completion(0.01)
    log = log_buffer.getvalue()

    assert "Setting lid temperature(s) to 105.0°C." in log
    assert "Running protocol:" in log
    assert "- Stage 1/2: 3 step(s) x 2 repeat(s)" in log
    assert "  - Repeat 1/2:" in log
    assert "    - Step 1/3 (repeat 1/2): temperature(s) = 98.0°C, hold = 15.0s" in log
    assert "    - Step 2/3 (repeat 1/2): temperature(s) = 60.0°C, hold = 15.0s" in log
    assert "    - Step 3/3 (repeat 1/2): temperature(s) = 72.0°C, hold = 20.0s" in log
    assert "  - Repeat 2/2:" in log
    assert "    - Step 1/3 (repeat 2/2): temperature(s) = 98.0°C, hold = 15.0s" in log
    assert "    - Step 2/3 (repeat 2/2): temperature(s) = 60.0°C, hold = 15.0s" in log
    assert "    - Step 3/3 (repeat 2/2): temperature(s) = 72.0°C, hold = 20.0s" in log
    assert "- Stage 2/2: 1 step(s) x 1 repeat(s)" in log
    assert "  - Repeat 1/1:" in log
    assert "    - Step 1/1 (repeat 1/1): temperature(s) = 4.0°C, hold = 1.0s" in log
