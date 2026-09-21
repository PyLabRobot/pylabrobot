import dataclasses
import unittest

from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import STAR
from pylabrobot.hamilton.star.driver.features.x_arm_tests import RECORDED_DEVICE, declaring
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.hamilton import STARDeck


class TestWiring(unittest.IsolatedAsyncioTestCase):
  async def test_built_on_the_arm_that_carries_pipettes(self):
    star = STAR(simulation=True)
    await star.setup()
    grippers = star.driver.x_arm.core_grippers
    self.assertIsNotNone(grippers)
    self.assertIs(star.driver.core_grippers, grippers)
    self.assertIs(star.core_grippers, grippers)

  async def test_none_on_an_arm_without_pipettes(self):
    both = dataclasses.replace(RECORDED_DEVICE, right_arm=BARE_X_ARM)
    driver = STARSimulationDriver(
      deck=STARDeck(), declared_configuration_json=declaring(device=both)
    )
    await driver.setup()
    assert driver.left_x_arm is not None and driver.right_x_arm is not None
    self.assertIsNotNone(driver.left_x_arm.core_grippers)
    self.assertIsNone(driver.right_x_arm.core_grippers)
    with self.assertRaises(ValueError):
      driver.core_grippers

  async def test_kept_across_a_second_setup(self):
    star = STAR(simulation=True)
    await star.setup()
    grippers = star.core_grippers
    await star.setup()
    self.assertIs(star.core_grippers, grippers)

  async def test_carried_by_the_arms_pipettes(self):
    star = STAR(simulation=True)
    await star.setup()
    assert star.core_grippers is not None
    self.assertIs(star.core_grippers.arm, star.driver.x_arm)
    self.assertIs(star.core_grippers._pipettes, star.driver.x_arm.pipettes)


class TestState(unittest.IsolatedAsyncioTestCase):
  async def test_not_mounted_refuses(self):
    star = STAR(simulation=True)
    await star.setup()
    assert star.core_grippers is not None
    self.assertFalse(star.core_grippers.tools_mounted)
    with self.assertRaises(RuntimeError):
      star.core_grippers._require_mounted()
