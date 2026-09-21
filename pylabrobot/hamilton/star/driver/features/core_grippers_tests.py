import dataclasses
import unittest
from typing import Any, List

from pylabrobot.hamilton.protocol.text.framing import assemble_command
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


class TestToolFirmware(unittest.IsolatedAsyncioTestCase):
  """`C0 ZT` and `C0 ZS` as they go on the wire, with the 5 mL holder's positions."""

  async def asyncSetUp(self):
    star = STAR(simulation=True)
    await star.setup()
    assert star.core_grippers is not None
    self.grippers = star.core_grippers
    self.sent: List[str] = []

    async def recorded(module: str, command: str, **kwargs: Any):
      wire = {k: v for k, v in kwargs.items() if len(k) == 2}
      self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))

    self.grippers._driver.send_command = recorded  # type: ignore[assignment]

  async def test_pick_up(self):
    await self.grippers._unchecked_fw_pick_up_tools(13375, 1250, 1070, 2350, 2250, 2800, 6, 7, 14)
    self.assertEqual(self.sent, ["C0ZTxs13375xd0ya1250yb1070tt14tp2350tz2250th2800pa07pb08"])

  async def test_drop(self):
    await self.grippers._unchecked_fw_drop_tools(13375, 1250, 1070, 2150, 2050, 2800, 2800, 6, 7)
    self.assertEqual(self.sent, ["C0ZSxs13375xd0ya1250yb1070tp2150tz2050th2800te2800pa07pb08"])

  async def test_negative_x_sends_the_direction(self):
    await self.grippers._unchecked_fw_pick_up_tools(-120, 1250, 1070, 2350, 2250, 2800, 4, 5, 14)
    self.assertEqual(self.sent, ["C0ZTxs00120xd1ya1250yb1070tt14tp2350tz2250th2800pa05pb06"])
