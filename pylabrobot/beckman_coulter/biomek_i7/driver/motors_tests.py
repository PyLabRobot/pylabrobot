"""Motor tables: each axis's Configure parameters and the limits read from them, and the axes a pod
gets from them. On the package's generic configuration."""

import asyncio
import unittest

from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_configuration,
  generic_driver,
  sealed,
)


class MotorTableTests(unittest.TestCase):
  def setUp(self) -> None:
    self.config = generic_configuration()
    self.tables = self.config.motor_tables

  def test_limits_from_configure_parameters_match_the_logged_ones(self) -> None:
    """The limits parsed from each axis's Configure bytes are the ones the Master Controller
    logged for it."""
    for module, axes in self.config.modules.items():
      for axis, logged in zip(self.tables.axes(module), axes):
        for name, value in logged["limits"].items():
          self.assertEqual(getattr(axis.limits, name), value, (module, axis.name, name))

  def test_position_check(self) -> None:
    x = self.tables.axes("multichannel")[0]
    x.check_position(350720)
    with self.assertRaisesRegex(ValueError, "outside"):
      x.check_position(350721)

  def test_an_unknown_module_has_no_axes(self) -> None:
    with self.assertRaisesRegex(ValueError, "no motor tables recorded for a robot arm"):
      self.tables.axes("robot")


class PodAxesTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)

  async def test_each_pod_gets_its_types_axes_and_declaration(self) -> None:
    driver = generic_driver()
    self.addAsyncCleanup(driver.stop)
    await driver.discover()
    left, right = driver.left_x_arm, driver.right_x_arm
    assert left is not None and right is not None
    self.assertEqual(
      [a.name for a in left.axes], [a.name for a in driver.motor_tables.axes("multichannel")]
    )
    self.assertIs(left.declared, driver.declared.pods["left"])
    self.assertEqual(right.axis("Z8").name, "Z8")
    with self.assertRaisesRegex(ValueError, "has no axis 'Z8'"):
      left.axis("Z8")

  async def test_the_traverse_defaults_follow_biomeks_always_max_z(self) -> None:
    driver = generic_driver()  # `AlwaysMaxZ` False on both pods, as on the instrument it came from
    self.addAsyncCleanup(driver.stop)
    await driver.discover()
    for pod in (driver.left_x_arm, driver.right_x_arm):
      assert pod is not None
      self.assertEqual(pod.traverse_defaults, {"channels": "safe", "gripper": "safe"})
    config = generic_configuration()
    for side in ("left", "right"):
      config.pods[side].settings["AlwaysMaxZ"] = True
    driver = generic_driver(configuration=sealed(config))
    self.addAsyncCleanup(driver.stop)
    await driver.discover()
    assert driver.left_x_arm is not None and driver.right_x_arm is not None
    for pod in (driver.left_x_arm, driver.right_x_arm):
      self.assertEqual(pod.traverse_defaults, {"channels": "max", "gripper": "max"})


if __name__ == "__main__":
  unittest.main()
