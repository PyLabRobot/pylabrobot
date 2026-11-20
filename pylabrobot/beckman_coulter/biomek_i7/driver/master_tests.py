"""Discovery on a replayed instrument: each board placed by what it says it is, then read."""

import asyncio
import itertools
import unittest
from typing import Any, Dict, List

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.features.chassis import Chassis
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import ReplayAceUSB
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_boards,
  generic_driver,
  generic_recording,
)

# The commands that only read: discovery sends nothing else.
READS = {name for name in p.COMMANDS.values() if name.startswith(("Get ", "Read ", "List "))}


def answer(recording: Dict[str, Any], module: str, key: str, reply: str) -> None:
  """Have `module` answer the request `key` (its bytes less the sequence) with `reply`."""
  entries: List[List[Any]] = recording["boards"][module]
  for entry in entries:
    if entry[0] == key:
      entry[1] = [reply]
      return
  entries.append([key, [reply]])


def module_id(recording: Dict[str, Any], module: str, type_code: int, side_code: int) -> None:
  answer(recording, module, "00000000", f"00000000000200{type_code:02X}{side_code:02X}")


class ReplayedTestCase(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)  # the test loop's debug mode makes replays slow


class PlacementTests(ReplayedTestCase):
  async def test_boards_are_placed_by_what_they_say_in_any_order(self) -> None:
    for order in itertools.permutations(["chassis", "multichannel", "span8"]):
      with self.subTest(order=order):
        driver = generic_driver(generic_boards(modules=order))
        await driver.discover()
        try:
          self.assertIsNotNone(driver.chassis)
          assert driver.chassis is not None and driver.left_x_arm and driver.right_x_arm
          self.assertEqual(driver.chassis.board.name, "replayed chassis")
          self.assertEqual(driver.left_x_arm.board.name, "replayed multichannel")
          self.assertEqual(driver.right_x_arm.board.name, "replayed span8")
        finally:
          await driver.stop()
    left, right = driver.left_x_arm, driver.right_x_arm
    assert left is not None and right is not None
    self.assertEqual(str(left), "left pod (Pod1, multichannel)")
    self.assertEqual((left.component, left.pod_number), ("MC_1", 1))
    self.assertEqual((right.component, right.pod_number), ("SPAN8_2", 2))
    self.assertEqual(driver.arms, [left, right])

  async def test_two_boards_in_one_place_are_refused(self) -> None:
    recording = generic_recording()
    module_id(recording, "span8", 0xFB, 0x01)  # a second left pod
    driver = generic_driver(generic_boards(recording))
    with self.assertRaisesRegex(RuntimeError, "replayed span8 and replayed multichannel both"):
      await driver.discover()
    await driver.stop()

  async def test_no_chassis_is_refused(self) -> None:
    driver = generic_driver(generic_boards(modules=["multichannel", "span8"]))
    with self.assertRaisesRegex(RuntimeError, "no chassis"):
      await driver.discover()
    await driver.stop()

  async def test_an_unknown_module_type_is_refused(self) -> None:
    recording = generic_recording()
    module_id(recording, "span8", 0x42, 0x02)
    driver = generic_driver(generic_boards(recording))
    with self.assertRaisesRegex(RuntimeError, "module type 0x42"):
      await driver.discover()
    await driver.stop()

  async def test_an_unknown_side_is_refused(self) -> None:
    recording = generic_recording()
    module_id(recording, "span8", 0xFB, 0x07)
    driver = generic_driver(generic_boards(recording))
    with self.assertRaisesRegex(RuntimeError, "side 0x07"):
      await driver.discover()
    await driver.stop()

  async def test_setup_releases_the_boards_when_discovery_fails(self) -> None:
    boards = generic_boards(modules=["multichannel", "span8"])
    with self.assertRaisesRegex(RuntimeError, "no chassis"):
      await generic_driver(boards).setup()
    self.assertFalse(any(board.connected for board in boards))


class PodNameTests(ReplayedTestCase):
  async def test_one_pod(self) -> None:
    driver = generic_driver(generic_boards(modules=["chassis", "span8"]))
    with self.assertRaisesRegex(RuntimeError, "discover"):
      driver.x_arm
    await driver.discover()
    self.assertIsNone(driver.left_x_arm)
    self.assertIs(driver.x_arm, driver.right_x_arm)
    await driver.stop()

  async def test_two_pods(self) -> None:
    driver = generic_driver()
    await driver.discover()
    with self.assertRaisesRegex(ValueError, "left_x_arm"):
      driver.x_arm
    for name in ("left", 1, "Pod1", "MC_1"):
      self.assertIs(driver.arm(name), driver.left_x_arm)
    for name in ("right", 2, "Pod2", "SPAN8_2"):
      self.assertIs(driver.arm(name), driver.right_x_arm)
    with self.assertRaises(KeyError):
      driver.arm("MC_2")
    await driver.stop()


class ReadTests(ReplayedTestCase):
  async def asyncSetUp(self) -> None:
    await super().asyncSetUp()
    self.boards = generic_boards()
    self.driver = generic_driver(self.boards)

  async def asyncTearDown(self) -> None:
    await self.driver.stop()

  async def test_discovery_on_its_own_opens_the_boards_and_only_reads(self) -> None:
    await self.driver.discover()
    self.assertTrue(all(board.connected for board in self.boards))
    for board in self.boards:
      assert isinstance(board.io, ReplayAceUSB)
      self.assertEqual(board.io.unanswered, [], board.name)
      sent = {p.COMMANDS[(m[0], m[4])] for m in board.io.requests}
      self.assertLessEqual(sent, READS, board.name)

  async def test_serial_numbers(self) -> None:
    with self.assertRaisesRegex(RuntimeError, "discover"):
      await self.driver.request_serial_numbers()
    await self.driver.discover()
    serials = await self.driver.request_serial_numbers()
    self.assertEqual(set(serials), {"chassis", "left", "right"})
    for serial in serials.values():
      assert serial is not None
      self.assertTrue(serial.startswith("SIMULATED"), serial)
    self.assertEqual(len(set(serials.values())), 3)

  async def test_each_modules_boards_and_inputs(self) -> None:
    await self.driver.discover()
    chassis, left, right = self.driver.chassis, self.driver.left_x_arm, self.driver.right_x_arm
    assert chassis and chassis.configuration and left and left.configuration
    assert right and right.configuration
    self.assertEqual(chassis.configuration.module_id.type, "chassis")
    self.assertEqual(chassis.configuration.digital_inputs[1], "Door Sensor")
    self.assertEqual(chassis.configuration.analog_units[9], "deg. C")
    self.assertEqual(
      [b.index for b in left.configuration.boards],
      list(range(len(left.configuration.boards), 0, -1)),  # highest index first
    )
    self.assertTrue(any(b.hardware.name == "AccuFrame" for b in left.configuration.boards))
    self.assertEqual(right.configuration.untested_firmware(), [])

  async def test_sensor_reads(self) -> None:
    await self.driver.discover()
    chassis, left, right = self.driver.chassis, self.driver.left_x_arm, self.driver.right_x_arm
    assert chassis and left and right
    analog = await chassis.request_analog_inputs()
    self.assertIn("Temperature", analog)
    self.assertEqual(set(await chassis.request_digital_inputs()), {"Door Sensor"})
    self.assertEqual(len(await chassis.request_digital_outputs()), 1)
    self.assertIsInstance(await chassis.request_legacy_alp_devices(), list)
    for pod in (left, right):
      # The recording answers as the instrument did when it was made, with the interlock armed.
      self.assertGreater(await pod.request_drive_supply(), 30.0)
      self.assertIn("+36V Interlock", await pod.request_analog_inputs())
    self.assertEqual(len(await left.request_accuframe_offsets()), 3)

  async def test_untested_firmware_is_warned_about(self) -> None:
    with self.assertNoLogs("pylabrobot.beckman_coulter.biomek_i7.driver.master", "WARNING"):
      await self.driver.discover()
    recording = generic_recording()
    answer(recording, "span8", "00000012", "000000001202" + "00EFBEADDE")
    driver = generic_driver(generic_boards(recording))
    with self.assertLogs("pylabrobot.beckman_coulter.biomek_i7.driver.master", "WARNING") as logs:
      await driver.discover()
    await driver.stop()
    self.assertIn("module image CRC 0xdeadbeef", "\n".join(logs.output))

  async def test_stop_releases_the_boards_and_discovery_runs_again(self) -> None:
    await self.driver.discover()
    await self.driver.stop()
    self.assertFalse(any(board.connected for board in self.boards))
    await self.driver.discover()
    self.assertTrue(all(board.connected for board in self.boards))


class InterlockStateTests(unittest.IsolatedAsyncioTestCase):
  """Get Interlock Status, which the Master Controller never sends, answers the interlock's byte
  (read on the instrument 2026-11-09: `FF` tripped, `00` armed)."""

  async def test_states(self) -> None:
    for byte, state in (("FF", "tripped"), ("00", "armed")):
      board = AceBoard(
        io=ReplayAceUSB({"01010010": ["0101000010" + "0200" + byte]}), default_timeout=0.3
      )
      await board.setup()
      try:
        self.assertEqual(await Chassis(board).request_interlock_state(), state)
      finally:
        await board.stop()


if __name__ == "__main__":
  unittest.main()
