import dataclasses
import unittest
from typing import Any, List

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.conftest import BARE_X_ARM
from pylabrobot.hamilton.star.device import STAR
from pylabrobot.hamilton.star.driver.features.x_arm_tests import RECORDED_DEVICE, declaring
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.errors import HasTipError
from pylabrobot.resources.hamilton import STARDeck, hamilton_tip_300uL


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
  """`C0 ZT` and `C0 ZS` as they go on the wire, byte for byte as legacy sends them."""

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
    self.assertEqual(self.sent, ["C0ZTxs13375xd0ya1250yb1070pa07pb08tp2350tz2250th2800tt14"])

  async def test_drop(self):
    await self.grippers._unchecked_fw_drop_tools(13375, 1250, 1070, 2150, 2050, 2800, 2800)
    self.assertEqual(self.sent, ["C0ZSxs13375xd0ya1250yb1070tp2150tz2050th2800te2800"])

  async def test_negative_x_sends_the_direction(self):
    await self.grippers._unchecked_fw_pick_up_tools(-120, 1250, 1070, 2350, 2250, 2800, 4, 5, 14)
    self.assertEqual(self.sent, ["C0ZTxs00120xd1ya1250yb1070pa05pb06tp2350tz2250th2800tt14"])


class TestPickUpAndDropTools(unittest.IsolatedAsyncioTestCase):
  """The checked tool pick-up and drop: what they refuse, send and leave behind."""

  async def asyncSetUp(self):
    self.star = STAR(simulation=True)
    await self.star.setup()
    assert self.star.core_grippers is not None
    self.grippers = self.star.core_grippers
    self.sent: List[str] = []
    answer = self.grippers._driver.send_command

    async def recorded(module: str, command: str, **kwargs: Any):
      wire = {k: v for k, v in kwargs.items() if len(k) == 2 and not isinstance(v, list)}
      self.sent.append(assemble_command(module=module, command=command, id_=None, **wire))
      return await answer(module=module, command=command, **kwargs)

    self.grippers._driver.send_command = recorded  # type: ignore[assignment]

  def tool_commands(self) -> List[str]:
    return [c for c in self.sent if c[2:4] in ("ZT", "ZS")]

  def safe_z_moves(self) -> List[str]:
    return [c[:4] for c in self.sent if c[:4] == "C0ZA" or (c[0] == "P" and c[2:4] == "ZA")]

  async def test_default_pair_sends_what_legacy_sends(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(
      self.tool_commands(), ["C0ZTxs13375xd0ya1250yb1070pa07pb08tp2350tz2250th2800tt14"]
    )
    self.assertEqual(self.safe_z_moves(), ["C0ZA"])

  async def test_a_named_pair(self):
    await self.grippers.pick_up_tools_at_location(
      1337.5, 225.0, 107.0, 125.0, back_channel=4, front_channel=5
    )
    self.assertIn("pa05pb06", self.tool_commands()[0])

  async def test_refused_before_anything_is_sent(self):
    for kwargs in ({"back_channel": 3, "front_channel": 5}, {"front_channel": 8}):
      with self.assertRaises(ValueError):
        await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0, **kwargs)
    with self.assertRaises(ValueError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 125.0, 107.0)
    with self.assertRaises(ValueError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0, tool_seek=220.0)
    self.assertEqual(self.tool_commands(), [])

  async def test_a_channel_carrying_something_is_refused(self):
    shaft = self.grippers._pipettes.shaft(7)
    assert shaft is not None
    shaft.mount_tip(hamilton_tip_300uL(name="on_channel_7"))
    with self.assertRaises(HasTipError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(self.tool_commands(), [])

  async def test_iswap_not_parked_is_refused(self):
    iswap = self.star.driver.x_arm.iswap
    assert iswap is not None

    async def not_parked(*args: Any, **kwargs: Any) -> bool:
      return False

    iswap.request_parked = not_parked  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(self.tool_commands(), [])

  async def test_drop_goes_back_20_mm_lower_as_legacy(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.sent.clear()
    await self.grippers.drop_tools()
    self.assertEqual(self.tool_commands(), ["C0ZSxs13375xd0ya1250yb1070tp2150tz2050th2800te2800"])
    self.assertEqual(self.safe_z_moves(), ["C0ZA"])
    with self.assertRaises(RuntimeError):
      await self.grippers.drop_tools()

  async def test_a_second_pick_up_is_refused(self):
    await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)

  async def test_a_failed_pick_up_still_goes_to_safe_z(self):
    async def refused(*args: Any, **kwargs: Any):
      raise RuntimeError("the device")

    self.grippers._unchecked_fw_pick_up_tools = refused  # type: ignore[method-assign]
    with self.assertRaises(RuntimeError):
      await self.grippers.pick_up_tools_at_location(1337.5, 225.0, 107.0, 125.0)
    self.assertEqual(self.safe_z_moves(), ["C0ZA"])
    self.assertIsNone(self.grippers._tools_taken_from)
