"""The Span-8's pumps must be homed before it aspirates or dispenses: after a power-up they are not
(Home All does not home them), and the board would refuse the pump move halfway through an
operation. The driver knows once it homes them, or asks each pump once, and refuses first."""

from pylabrobot.beckman_coulter.biomek_i7.driver import liquid_tests
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import NotHomedError
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import biomek_tip


class PumpHomingTests(PodMoveTest):
  setup_deck = liquid_tests.LiquidTests.setup_deck
  record = liquid_tests.LiquidTests.record

  async def test_pumps_not_homed_refuse_before_anything_moves(self) -> None:
    driver, lh = await self.setup_deck()  # type: ignore[misc]
    self.usb(driver, "span8").axis_model("D1").homed = False
    pipettes = lh.pipettes
    pipettes._pumps_homed = None  # a session that has not homed them yet
    pipettes._set_tips({n: biomek_tip("T190F", "t") for n in range(1, 3)})
    reservoir = lh.deck.get_resource("Reservoir").get_item(0)
    moves = self.record(driver, "right")  # type: ignore[misc]
    with self.assertRaisesRegex(NotHomedError, "prime the Span-8"):
      await pipettes.aspirate([reservoir] * 2, [30, 30])
    self.assertEqual(moves, [])
    await pipettes.home_pumps()
    await pipettes.aspirate([reservoir] * 2, [30, 30])
    self.assertTrue(moves)

  async def test_pumps_homed_in_this_session_are_not_asked_again(self) -> None:
    driver, lh = await self.setup_deck()  # type: ignore[misc]
    pipettes = lh.pipettes
    await pipettes.home_pumps()
    self.mark(driver)
    pipettes._set_tips({n: biomek_tip("T190F", "t") for n in range(1, 3)})
    reservoir = lh.deck.get_resource("Reservoir").get_item(0)
    await pipettes.aspirate([reservoir] * 2, [30, 30])
    asked = [name for name, _ in self.sent(driver, "span8") if name == "Get Homed State"]
    self.assertEqual(asked, [])  # the traffic stays the Master Controller's
