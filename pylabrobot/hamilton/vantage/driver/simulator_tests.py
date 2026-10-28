import unittest
from typing import List

from pylabrobot.events import EventBus, PLREvent
from pylabrobot.events.bus import use_event_bus
from pylabrobot.hamilton.vantage.driver.errors import VantageFirmwareError
from pylabrobot.hamilton.vantage.driver.simulator import VantageSimulationDriver


def kinds(driver: VantageSimulationDriver) -> List[str]:
  """The module and command of everything the driver sent."""
  return [command[:6] for command in driver.commands]


class TestSetup(unittest.IsolatedAsyncioTestCase):
  """`setup` against a machine that has just been switched on."""

  async def test_a_machine_that_was_just_switched_on_is_brought_up_in_order(self):
    driver = VantageSimulationDriver()
    await driver.setup()
    self.assertEqual(
      kinds(driver),
      [
        "A1PMQA",
        "A1AMQU",
        "A1AMQN",
        "A1RMQA",
        "A1AMQW",
        "A1AMMI",
        "A1RMQW",
        "A1RMDI",
        "A1RMRG",
        "A1RMGP",
        "A1PMQW",
        "A1PMDI",
      ],
    )
    self.assertTrue(
      (driver.arm_up, driver.ipg_up, driver.ipg_parked, driver.channels_up) == (True,) * 4
    )
    self.assertEqual(driver.num_channels, 8)
    self.assertTrue(driver.configuration.ipg_installed)  # type: ignore[union-attr]

  async def test_a_machine_that_is_up_is_only_read(self):
    driver = VantageSimulationDriver(initialized=True)
    await driver.setup()
    self.assertEqual(
      kinds(driver),
      ["A1PMQA", "A1AMQU", "A1AMQN", "A1RMQA", "A1AMQW", "A1RMQW", "A1RMRG", "A1PMQW"],
    )

  async def test_setup_twice_does_not_move_it_again(self):
    driver = VantageSimulationDriver()
    await driver.setup()
    driver.commands.clear()
    await driver.setup()
    self.assertNotIn("A1AMMI", kinds(driver))
    self.assertNotIn("A1RMDI", kinds(driver))
    self.assertNotIn("A1PMDI", kinds(driver))

  async def test_the_tips_on_the_channels_are_discarded(self):
    driver = VantageSimulationDriver(initialized=True, tips_mounted=[True] + [False] * 7)
    await driver.setup()
    self.assertIn("A1PMDI", kinds(driver))
    self.assertEqual(driver.tips, [False] * 8)

  async def test_a_machine_without_an_ipg_is_never_asked_about_one(self):
    driver = VantageSimulationDriver(ipg_installed=False)
    await driver.setup()
    self.assertIsNone(driver.ipg)
    self.assertEqual(driver.configuration.arm_type, 13)  # type: ignore[union-attr]
    self.assertFalse([kind for kind in kinds(driver) if kind.startswith("A1RM")])

  async def test_an_ipg_that_holds_a_plate_stops_the_setup_before_anything_moves(self):
    driver = VantageSimulationDriver(ipg_plate=True)
    with self.assertRaises(RuntimeError):
      await driver.setup()
    self.assertEqual(kinds(driver), ["A1PMQA", "A1AMQU", "A1AMQN", "A1RMQA"])
    self.assertFalse(driver.arm_up)
    self.assertFalse(driver.connected)

  async def test_discover_moves_nothing(self):
    driver = VantageSimulationDriver(tips_mounted=[False, True] + [False] * 6)
    await driver.connect()
    self.assertEqual(await driver.discover(), [False, True] + [False] * 6)
    self.assertEqual((driver.arm_up, driver.ipg_up, driver.channels_up), (False,) * 3)
    await driver.stop()


class TestTheMachineAnswersLikeOne(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.driver = VantageSimulationDriver()
    await self.driver.connect()

  async def asyncTearDown(self):
    await self.driver.stop()

  async def test_the_ipg_cannot_be_initialized_or_parked_before_the_arm(self):
    await self.driver.discover()
    assert self.driver.ipg is not None
    for move in (self.driver.ipg.initialize, self.driver.ipg.park):
      with self.assertRaises(VantageFirmwareError) as caught:
        await move()
      self.assertIn("MI", caught.exception.errors["IPG"])
    self.assertFalse(self.driver.ipg_up)
    self.assertFalse(self.driver.ipg_parked)

  async def test_the_channels_cannot_be_initialized_before_the_arm(self):
    await self.driver.discover()
    assert self.driver.pipettes is not None
    with self.assertRaises(VantageFirmwareError):
      await self.driver.pipettes.initialize()
    self.assertFalse(self.driver.channels_up)

  async def test_an_unknown_command_is_reported_as_one(self):
    with self.assertRaises(VantageFirmwareError) as caught:
      await self.driver.send_command("A1AM", "ZZ")
    self.assertEqual(caught.exception.errors, {"Arm": "Command: Unknown command"})

  async def test_an_ipg_that_is_not_there_does_not_answer(self):
    driver = VantageSimulationDriver(ipg_installed=False)
    await driver.connect()
    with self.assertRaises(VantageFirmwareError):
      await driver.send_command("A1RM", "QW", fmt={"qw": "int"})
    await driver.stop()

  async def test_the_ipg_reports_where_it_stands(self):
    angles = await self.driver.send_command("A1RM", "RR", fmt={"rr": "[int]"})
    self.assertEqual(angles["rr"], [45, 271])
    await self.driver.send_command("A1AM", "MI")
    await self.driver.send_command("A1RM", "DI")
    await self.driver.send_command("A1RM", "GP")
    angles = await self.driver.send_command("A1RM", "RR", fmt={"rr": "[int]"})
    self.assertEqual(angles["rr"], [0, 272])

  async def test_a_reply_carries_the_id_of_its_command(self):
    reply = await self.driver.send_command("A1AM", "QW")
    self.assertRegex(reply, r"^A1AMQWid1er0qw0$")
    raw = await self.driver.send_raw_command("A1AMQWid0042")
    self.assertEqual(raw, "A1AMQWid42er0qw0")

  async def test_a_raw_command_without_an_id_is_answered_with_id_0(self):
    self.assertEqual(await self.driver.send_raw_command("A1AMQW"), "A1AMQWid0er0qw0")

  async def test_a_command_that_is_not_waited_for_gets_no_answer_and_still_acts(self):
    self.assertIsNone(await self.driver.send_raw_command("A1AMMIid0001", wait=False))
    self.assertTrue(self.driver.arm_up)

  async def test_the_firmware_reply_is_a_firmware_string(self):
    reply = await self.driver.send_command("A1AM", "RF", fmt={"rf": "str"})
    self.assertEqual(reply["rf"], "simulated")


class TestConstruction(unittest.TestCase):
  def test_the_number_of_tips_must_match_the_number_of_channels(self):
    with self.assertRaises(ValueError):
      VantageSimulationDriver(num_channels=8, tips_mounted=[False] * 7)

  def test_the_tips_are_copied_from_the_list_given(self):
    tips = [True] + [False] * 7
    driver = VantageSimulationDriver(tips_mounted=tips)
    tips[0] = False
    self.assertEqual(driver.tips, [True] + [False] * 7)

  def test_a_machine_has_a_channel(self):
    with self.assertRaises(ValueError):
      VantageSimulationDriver(num_channels=0)

  def test_a_machine_without_an_ipg_cannot_hold_a_plate_in_it(self):
    with self.assertRaises(ValueError):
      VantageSimulationDriver(ipg_installed=False, ipg_plate=True)


class TestEvents(unittest.IsolatedAsyncioTestCase):
  async def test_a_command_emits_the_events_a_real_one_does(self):
    driver = VantageSimulationDriver()
    events: List[PLREvent] = []
    bus = EventBus()
    bus.subscribe(events.append)
    await driver.connect()
    with use_event_bus(bus):
      await driver.send_command("A1AM", "QW")
      with self.assertRaises(VantageFirmwareError):
        await driver.send_command("A1AM", "ZZ")
    await driver.stop()
    self.assertEqual(
      [event.name for event in events],
      [
        "firmware.command.started",
        "firmware.command.completed",
        "firmware.command.started",
        "firmware.command.failed",
      ],
    )
    self.assertEqual(events[0].data["driver"], "VantageSimulationDriver")


if __name__ == "__main__":
  unittest.main()
