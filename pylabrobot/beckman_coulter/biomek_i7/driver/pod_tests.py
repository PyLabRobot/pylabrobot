"""A pod's drive commands, checked on the wire against a fake board.

Where a command has a counterpart in the Master Controller's log, the expected bytes are that
logged message (sequence number aside).
"""

import asyncio
import unittest
from typing import List

from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.board_tests import (
  FakeAceUSB,
  accepted,
  done,
  recorder,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import InterlockTrippedError
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod
from pylabrobot.beckman_coulter.biomek_i7.driver.framing import split
from pylabrobot.beckman_coulter.biomek_i7.driver.motors import AxisMove
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_configuration

TABLES = generic_configuration().motor_tables


def without_sequence(message: bytes) -> bytes:
  return message[:3] + b"\x00" + message[4:]


def logged(hex_message: str) -> bytes:
  return without_sequence(bytes.fromhex(hex_message))


def answer_like_a_board(message: bytes) -> List[bytes]:
  if message[4] in (0x50, 0x51):
    return [accepted(message), done(message)]
  return [done(message)]


class PodDriveCommandTests(unittest.IsolatedAsyncioTestCase):
  async def make_pod(self, module: str, side: str) -> Pod:
    self.usb = FakeAceUSB(answer_like_a_board)
    board = AceBoard(io=self.usb, default_timeout=1)
    await board.setup()
    self.addAsyncCleanup(board.stop)
    return Pod(board, side, module, TABLES)  # type: ignore[arg-type]

  def sent(self) -> List[bytes]:
    return [without_sequence(m) for t in self.usb.transfers for m in split(t)]

  async def test_move_parameters_default_to_the_axis_maximum(self) -> None:
    pod = await self.make_pod("multichannel", "left")
    self.assertEqual(await pod._unchecked_fw_set_move_parameters([AxisMove("X", 35840)]), [True])
    # Set X Move Parameters Position:35840, logged in the 2026-10-24 cold start.
    self.assertEqual(
      self.sent(), [logged("110100A340807D020000FC03000088130000000000008C00000000")]
    )

  async def test_move_parameters_with_force_and_explicit_speeds(self) -> None:
    """A grip at force 5.000 and a tip-loading Z move at 787.500, both logged in the 2026-10-08
    method run."""
    span8 = await self.make_pod("span8", "right")
    await span8._unchecked_fw_set_move_parameters([AxisMove("GG", 34873, force=5000)])
    self.assertEqual(
      self.sent(), [logged("111300F54019660100F9F91B00BAC3170188130000398800000000")]
    )

    left = await self.make_pod("multichannel", "left")
    await left._unchecked_fw_set_move_parameters(
      [AxisMove("Z", -35182, velocity=2432, acceleration=12800, jerk=69869, force=787500)]
    )
    self.assertEqual(
      self.sent(), [logged("1103005F408009000000320000ED1001002C040C009276FFFF0000")]
    )

  async def test_targets_outside_the_limits_send_nothing(self) -> None:
    pod = await self.make_pod("multichannel", "left")
    with self.assertRaisesRegex(ValueError, "outside"):
      await pod._unchecked_fw_set_move_parameters([AxisMove("Y", 0), AxisMove("X", 350721)])
    self.assertEqual(self.usb.transfers, [])

  async def test_a_pump_whose_valve_is_not_set_is_refused(self) -> None:
    pod = await self.make_pod("span8", "right")
    with self.assertRaisesRegex(RuntimeError, "valve"):
      await pod._unchecked_fw_set_move_parameters([AxisMove("D1", 0)])
    self.assertEqual(self.usb.transfers, [])

  async def test_not_homed_is_an_answer_and_other_refusals_raise(self) -> None:
    pod = await self.make_pod("span8", "right")
    self.usb.handler = lambda m: [m[:5] + b"\x00\x32"] if m[1] == 0x0A else [done(m)]
    accepted_ = await pod._unchecked_fw_set_move_parameters(
      [AxisMove("Z1", 9647), AxisMove("Z2", 9647)]
    )
    self.assertEqual(accepted_, [False, True])
    self.usb.handler = lambda m: [m[:5] + b"\x00\x0f"]
    with self.assertRaises(InterlockTrippedError):
      await pod._unchecked_fw_set_move_parameters([AxisMove("X", -2560)])

  async def test_a_batch_travels_together(self) -> None:
    pod = await self.make_pod("span8", "right")
    await pod._unchecked_fw_set_move_parameters([AxisMove(f"Z{i}", 9647) for i in range(1, 9)])
    self.assertEqual(len(self.usb.transfers), 1)
    self.assertEqual([m[1] for m in self.sent()], list(range(0x0A, 0x12)))

  async def test_move_and_move_all(self) -> None:
    pod = await self.make_pod("multichannel", "left")
    await pod._unchecked_fw_move("GG")
    await pod._unchecked_fw_move_all()
    # Move GG Timeout:30000 and Move All Timeout:5000, as logged.
    self.assertEqual(self.sent(), [logged("1106007F5030750000"), logged("1000002E5088130000")])

  async def test_home_waits_for_every_axis(self) -> None:
    pod = await self.make_pod("span8", "right")
    held: List[bytes] = []
    self.usb.handler = recorder(held, lambda m: [accepted(m)])
    task = asyncio.create_task(pod._unchecked_fw_home(["Z1", "Z5"]))
    while len(held) < 2:
      await asyncio.sleep(0)
    self.assertEqual(len(self.usb.transfers), 1)  # both homes start together
    self.usb.push(done(held[0]))
    await asyncio.sleep(0.05)
    self.assertFalse(task.done())
    self.usb.push(done(held[1]))
    await task
    self.assertEqual(
      [without_sequence(m) for m in held], [logged("110A001051"), logged("110E001151")]
    )

  async def test_enable_addresses_pumps_and_motors(self) -> None:
    pod = await self.make_pod("span8", "right")
    await pod._unchecked_fw_enable(["D8", "GY"])
    await pod._unchecked_fw_stop_motion()
    self.assertEqual(
      self.sent(),
      [logged("1208006B1200"), logged("1112006C1200"), logged("100000670F")],
    )


if __name__ == "__main__":
  unittest.main()
