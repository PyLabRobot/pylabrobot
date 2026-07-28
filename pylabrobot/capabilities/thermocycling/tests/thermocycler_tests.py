"""Tests for Thermocycler capability and chatterbox backend."""

import unittest

from pylabrobot.capabilities.thermocycling.chatterbox import ThermocyclerChatterboxBackend
from pylabrobot.capabilities.thermocycling.standard import (
  Protocol,
  Ramp,
  Stage,
  Step,
)
from pylabrobot.capabilities.thermocycling.thermocycler import Thermocycler


def _make_thermocycler() -> Thermocycler:
  backend = ThermocyclerChatterboxBackend()
  return Thermocycler(backend=backend)


def _simple_protocol() -> Protocol:
  return Protocol(
    stages=[
      Stage(steps=[Step(temperature=95.0, hold_seconds=30.0)], repeats=1),
      Stage(
        steps=[
          Step(temperature=95.0, hold_seconds=10.0, ramp=Ramp(rate=4.4)),
          Step(temperature=55.0, hold_seconds=30.0),
          Step(temperature=72.0, hold_seconds=60.0),
        ],
        repeats=35,
      ),
    ],
    name="TestPCR",
  )


class TestThermocyclerCapability(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.tc = _make_thermocycler()
    await self.tc._on_setup()

  async def asyncTearDown(self):
    await self.tc._on_stop()

  async def test_run_protocol_stores_current_protocol(self):
    protocol = _simple_protocol()
    await self.tc.run_protocol(protocol)
    self.assertIs(self.tc._current_protocol, protocol)

  async def test_set_block_temperature(self):
    await self.tc.set_block_temperature(37.0)
    self.assertAlmostEqual(self.tc.backend._block_temperature, 37.0)

  async def test_request_block_temperature(self):
    await self.tc.set_block_temperature(72.0)
    temp = await self.tc.request_block_temperature()
    self.assertAlmostEqual(temp, 72.0)

  async def test_request_lid_temperature(self):
    temp = await self.tc.request_lid_temperature()
    self.assertAlmostEqual(temp, 25.0)

  async def test_deactivate_block_clears_protocol(self):
    await self.tc.run_protocol(_simple_protocol())
    await self.tc.deactivate_block()
    progress = await self.tc.request_progress()
    self.assertIsNone(progress)

  async def test_request_progress_none_when_idle(self):
    progress = await self.tc.request_progress()
    self.assertIsNone(progress)

  async def test_request_progress_after_run(self):
    await self.tc.run_protocol(_simple_protocol())
    progress = await self.tc.request_progress()
    self.assertIsNotNone(progress)
    self.assertEqual(progress["protocol_name"], "TestPCR")

  async def test_stop_protocol(self):
    await self.tc.run_protocol(_simple_protocol())
    await self.tc.stop_protocol()
    self.assertIsNone(self.tc._current_protocol)

  async def test_on_stop_deactivates(self):
    await self.tc.run_protocol(_simple_protocol())
    await self.tc._on_stop()
    self.assertIsNone(self.tc._current_protocol)
    self.assertFalse(self.tc._setup_finished)

  async def test_wait_for_first_progress(self):
    await self.tc.run_protocol(_simple_protocol())
    progress = await self.tc.wait_for_first_progress(timeout=1.0)
    self.assertIsNotNone(progress)

  async def test_wait_for_first_progress_timeout(self):
    with self.assertRaises(TimeoutError):
      await self.tc.wait_for_first_progress(timeout=0.1)


class TestNeedCapabilityReady(unittest.IsolatedAsyncioTestCase):
  async def test_guard_fires_before_setup(self):
    tc = _make_thermocycler()
    with self.assertRaises(RuntimeError):
      await tc.run_protocol(_simple_protocol())
    with self.assertRaises(RuntimeError):
      await tc.set_block_temperature(37.0)
    with self.assertRaises(RuntimeError):
      await tc.request_block_temperature()

  async def test_guard_passes_after_setup(self):
    tc = _make_thermocycler()
    await tc._on_setup()
    await tc.set_block_temperature(37.0)
    await tc._on_stop()


if __name__ == "__main__":
  unittest.main()
