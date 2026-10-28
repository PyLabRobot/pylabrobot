import re
import unittest

from pylabrobot.hamilton.vantage.driver.errors import VantageFirmwareError
from pylabrobot.hamilton.vantage.driver.features.ipg import Ipg, IpgParkConfiguration
from pylabrobot.hamilton.vantage.driver.master import VantageDriver
from pylabrobot.hamilton.vantage.driver.master_tests import ScriptedIO, echo, machine


def without_id(command: str) -> str:
  """A command without the id it was sent with."""
  return re.sub(r"id\d+", "", command, count=1)


class IpgTestCase(unittest.IsolatedAsyncioTestCase):
  """A driver on a machine with an IPG, discovered."""

  async def asyncSetUp(self):
    self.io = ScriptedIO(machine(ipg=True))
    self.driver = VantageDriver(self.io, read_timeout=5)
    self.driver._connected = True
    self.driver._router.start()
    await self.driver.discover()
    self.io.written.clear()
    ipg = self.driver.ipg
    assert ipg is not None
    self.ipg: Ipg = ipg

  async def asyncTearDown(self):
    self.driver._router.stop()

  def sent(self):
    return [without_id(command) for command in self.io.written]


class TestQueries(IpgTestCase):
  """What the IPG is asked."""

  async def test_initialization_status(self):
    self.io.answers = machine(ipg=True, ipg_initialized=True)
    self.assertIs(await self.ipg.request_initialization_status(), True)
    self.io.answers = machine(ipg=True, ipg_initialized=False)
    self.assertIs(await self.ipg.request_initialization_status(), False)
    self.assertEqual(self.sent(), ["A1RMQW", "A1RMQW"])

  async def test_parking_status(self):
    self.io.answers = machine(ipg=True, ipg_parked=True)
    self.assertIs(await self.ipg.request_parking_status(), True)
    self.io.answers = machine(ipg=True, ipg_parked=False)
    self.assertIs(await self.ipg.request_parking_status(), False)
    self.assertEqual(self.sent(), ["A1RMRG", "A1RMRG"])

  async def test_plate_presence(self):
    self.io.answers = machine(ipg=True, ipg_plate=True)
    self.assertIs(await self.ipg.request_plate_presence(), True)
    self.io.answers = machine(ipg=True, ipg_plate=False)
    self.assertIs(await self.ipg.request_plate_presence(), False)
    self.assertEqual(self.sent(), ["A1RMQA", "A1RMQA"])

  async def test_angles(self):
    self.io.answers = lambda command: [echo(command, "rr45 271")]
    self.assertEqual(await self.ipg.request_angles(), [45, 271])
    self.assertEqual(self.sent(), ["A1RMRR"])

  async def test_park_configuration_is_in_mm_and_degrees(self):
    self.io.answers = lambda command: [echo(command, "rs6350 2879 726rr0 272")]
    self.assertEqual(
      await self.ipg.request_park_configuration(),
      IpgParkConfiguration(y=635.0, z=287.9, gripper_width=72.6, arm_angle=0, hand_angle=272),
    )
    self.assertEqual(self.sent(), ["A1RMRS"])


class TestBeforeTheArmIsUp(IpgTestCase):
  """The IPG cannot be initialized or parked until the arm has been initialized."""

  async def test_the_machine_refuses_and_says_why(self):
    self.io.answers = machine(ipg=True, arm_initialized=False)
    for move in (self.ipg.initialize, self.ipg.park):
      with self.assertRaises(VantageFirmwareError) as caught:
        await move()
      self.assertIn("MI", caught.exception.errors["IPG"])

  async def test_it_accepts_them_once_the_arm_is_up(self):
    self.io.answers = machine(ipg=True, arm_initialized=True)
    await self.ipg.initialize()
    await self.ipg.park()


class TestMoves(IpgTestCase):
  """Initializing and parking, which move the IPG."""

  async def test_initialize_sends_di_to_the_ipg(self):
    await self.ipg.initialize()
    self.assertEqual(self.sent(), ["A1RMDI"])

  async def test_park_sends_gp_to_the_ipg(self):
    await self.ipg.park()
    self.assertEqual(self.sent(), ["A1RMGP"])

  async def test_both_are_given_the_time_the_arm_waits_for_a_module(self):
    seen = {}
    send = self.driver._router.send

    async def spying(cmd, id_, write_timeout=None, read_timeout=None, wait=True):
      seen[cmd[:6]] = read_timeout
      return await send(cmd, id_, write_timeout, read_timeout, wait)

    self.driver._router.send = spying  # type: ignore[method-assign]
    await self.ipg.initialize()
    await self.ipg.park()
    self.assertEqual(seen, {"A1RMDI": 300, "A1RMGP": 300})


if __name__ == "__main__":
  unittest.main()
