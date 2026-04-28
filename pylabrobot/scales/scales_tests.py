"""Tests for generic scale behavior via the scale physics simulation."""

import unittest

from pylabrobot.scales.scale import Scale
from pylabrobot.scales.simulator import ScaleSimulator


class ScaleSimulatorTests(unittest.IsolatedAsyncioTestCase):
  """Tests for the physics simulation via the Scale frontend."""

  async def asyncSetUp(self):
    self.backend = ScaleSimulator()
    self.scale = Scale(
      name="test_scale",
      backend=self.backend,
      size_x=0,
      size_y=0,
      size_z=0,
    )
    await self.scale.setup()

  async def asyncTearDown(self):
    await self.scale.stop()

  async def test_zero_then_read_returns_zero(self):
    self.backend.platform_weight = 5.0
    await self.scale.zero()
    weight = await self.scale.read_weight()
    self.assertEqual(weight, 0.0)

  async def test_tare_workflow(self):
    self.backend.platform_weight = 50.0  # container
    await self.scale.tare()
    self.backend.sample_weight = 0.0106  # ~10 uL liquid
    weight = await self.scale.read_weight()
    self.assertEqual(weight, 0.0106)

  async def test_zero_and_tare_compose(self):
    # preload on platform
    self.backend.platform_weight = 2.0
    await self.scale.zero()

    # place container
    self.backend.platform_weight = 52.0  # 2g preload + 50g container
    await self.scale.tare()

    # add sample
    self.backend.sample_weight = 1.06
    weight = await self.scale.read_weight()
    self.assertEqual(weight, 1.06)

  async def test_request_tare_weight_accuracy(self):
    self.backend.platform_weight = 45.0
    await self.scale.tare()
    tare = await self.scale.request_tare_weight()
    self.assertEqual(tare, 45.0)

  async def test_re_tare_resets(self):
    # first tare with 50g container
    self.backend.platform_weight = 50.0
    await self.scale.tare()

    # re-tare with 30g container
    self.backend.platform_weight = 30.0
    await self.scale.tare()

    # add sample
    self.backend.sample_weight = 5.0
    weight = await self.scale.read_weight()
    self.assertEqual(weight, 5.0)

    tare = await self.scale.request_tare_weight()
    self.assertEqual(tare, 30.0)


if __name__ == "__main__":
  unittest.main()
