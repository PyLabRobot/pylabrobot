"""The one place PyLabRobot's units meet Biomek's (`units.py`): exact where Biomek's logs are."""

import asyncio
import unittest

from pylabrobot.beckman_coulter.biomek_i7 import units as u
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_driver


class LengthAndFrameTests(unittest.TestCase):
  def test_lengths_and_the_frame_round_trip(self) -> None:
    for mm in (0.0, 1.0, 123.456, -7.5):
      self.assertAlmostEqual(u.to_mm(u.to_cm(mm)), mm)
      self.assertAlmostEqual(u.deck_x(u.biomek_x(mm)), mm)
      self.assertAlmostEqual(u.deck_y(u.biomek_y(mm)), mm)
    self.assertIsNone(u.optional_cm(None))
    point = u.to_biomek(Coordinate(100.0, 700.0, 25.0))
    self.assertEqual(point, u.BiomekPoint(0.0, 0.0, 2.5))  # the frame's origin, 2.5 cm up

  def test_an_offset_turns_with_biomeks_y(self) -> None:
    self.assertEqual(u.offset_to_biomek(10.0, 20.0, Coordinate(5.0, 5.0, 0)), (10.5, 19.5))

  def test_percent_is_biomeks_rounding_or_truncation(self) -> None:
    self.assertEqual(u.percent_to_field(25), 250)
    self.assertEqual(u.percent_to_field(80.1724), 802)
    self.assertEqual(u.percent_to_field(80.1724, truncate=True), 801)  # the logged prime speed
    self.assertEqual(u.percent_to_field(0.01), 1)
    self.assertEqual(u.percent_to_field(150), 1000)

  def test_a_fraction_is_refused_outside_zero_to_one(self) -> None:
    self.assertEqual(u.fraction_to_field(1.0), 1000)
    self.assertEqual(u.fraction_to_field(0.25), 250)
    for bad in (0.0, 1.01, -0.5, float("nan")):
      with self.assertRaises(ValueError):
        u.fraction_to_field(bad)


class RateTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)
    self.driver = generic_driver()
    await self.driver.discover()
    self.addAsyncCleanup(self.driver.stop)

  def axes(self):
    for pod in self.driver.arms:
      assert pod.declared is not None
      for axis in pod.axes:
        if axis.name in pod.declared.conversion and (axis.name[0] in "ZD" or axis.name == "GZ"):
          yield pod, axis.name

  def test_every_field_round_trips_on_every_z_and_pump(self) -> None:
    checked = 0
    for pod, axis in self.axes():
      for field in range(1, 1001):
        rate = u.field_to_rate(pod, axis, field)
        self.assertEqual(u.rate_to_field(pod, axis, rate), field, f"{pod} {axis} {field}")
        checked += 1
    self.assertGreaterEqual(checked, 20 * 1000)  # Z, D, GZ; Z1-Z8, D1-D8, GZ

  def test_a_rate_above_the_maximum_is_refused_naming_it(self) -> None:
    left = self.driver.left_x_arm
    assert left is not None
    top = u.max_rate(left, "Z")
    self.assertAlmostEqual(top, 95.0)  # mm/s: the Multichannel Z, 24320 counts/s at 2560 /cm
    with self.assertRaisesRegex(ValueError, "Z moves at up to 95"):
      u.rate_to_field(left, "Z", 125.0)  # the STAR's default z speed
    with self.assertRaises(ValueError):
      u.rate_to_field(left, "Z", 0.0)
    self.assertEqual(u.rate_to_field(left, "Z", 125.0, clamp=True), 1000)

  def test_the_flow_rate_is_the_old_plunger_arithmetic(self) -> None:
    left = self.driver.left_x_arm
    assert left is not None and left.declared is not None
    for flow in (1.0, 50.0, 90.0, 100.0, 150.0, 400.0, 2000.0):
      per_second = flow * left.declared.conversion["D"].const
      old = max(1, min(1000, round(per_second / left.axis("D").limits.max_velocity * 1000)))
      self.assertEqual(u.rate_to_field(left, "D", flow, clamp=True), old)


if __name__ == "__main__":
  unittest.main()
