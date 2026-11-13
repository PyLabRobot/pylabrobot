"""Onboard inventory parsing and documented BGN lifecycle, without hardware."""

import asyncio
import unittest

from .connection import FlexError
from .flex import KingFisherFlex
from .flex_tests import ScriptedHID


class FlexStoredTests(unittest.IsolatedAsyncioTestCase):
  """Exercise inventory and launch with simulated replies."""

  async def asyncSetUp(self) -> None:
    """Construct a driver with an in-memory transport only."""
    self.io = ScriptedHID()
    self.flex = KingFisherFlex(timeout=0.1)
    self.flex.io = self.io
    self.io.responses["REP 4"] = 'REP\r\nREP 4 1 1 "KF_Start_Test" 200\r\nREP END 0\r\n'

  async def asyncTearDown(self) -> None:
    """Close the reader even after intentionally invalidating it."""
    await self.flex.stop()

  async def test_inventory_parses_names_and_summary(self) -> None:
    """Return entry names without mistaking numeric summary fields for entries."""
    self.io.responses["REP 4"] = (
      'REP\r\nREP 4 1 1 "one" 100\r\nREP 4 2 1 "two" 200\r\nREP 4 1 2 3 4 5\r\nREP END 0\r\n'
    )
    await self.flex.setup()
    self.assertEqual(await self.flex.list_protocols(), ("one", "two"))
    self.assertEqual(self.io.commands, ["VER", "REP 4"])

  async def test_invalid_names_and_deadlines_send_nothing(self) -> None:
    """Empty names, control characters and invalid deadlines never reach USB."""
    for name in ("", "a\nBGN b", "é", " "):
      with self.subTest(name=name), self.assertRaises(ValueError):
        await self.flex.start_stored_protocol(name)
    with self.assertRaises(ValueError):
      await self.flex.start_stored_protocol("KF_Start_Test", completion_timeout=0)
    self.assertEqual(self.io.opened, 0)
    self.assertEqual(self.io.commands, [])

  async def test_long_inventory_names_are_transmitted_exactly(self) -> None:
    """Stored names survive quoting and HID fragmentation without truncation."""
    await self.flex.setup()
    for name in ("Long stored protocol name", 'Long "quoted" protocol ' + "x" * 70):
      with self.subTest(name=name):
        quoted = name.replace('"', '""')
        self.io.responses["REP 4"] = f'REP\r\nREP 4 1 1 "{quoted}" 200\r\nREP END 0\r\n'
        await self.flex.start_stored_protocol(name)
        self.assertEqual(self.io.commands[-1], f'BGN "{quoted}"')

  async def test_missing_duplicate_and_malformed_inventory_cannot_launch(self) -> None:
    """Reject ambiguous or unknown inventory instead of guessing a stored entry."""
    await self.flex.setup()
    for data in (
      "",
      'REP 4 1 1 "KF_Start_Test" 1\r\n' * 2,
      "REP 4 unknown\r\n",
    ):
      self.io.responses["REP 4"] = f"REP\r\n{data}REP END 0\r\n"
      with self.assertRaises(FlexError):
        await self.flex.start_stored_protocol("KF_Start_Test")
    self.assertEqual(self.io.commands, ["VER", "REP 4", "REP 4", "REP 4"])

  async def test_ack_does_not_finish_launch_and_abort_bypasses_wait(self) -> None:
    """BGN hides internal PAU; abort must work without a BGN END response."""
    await self.flex.setup()
    command = 'BGN "KF_Start_Test"'
    self.io.responses[command] = "BGN\r\n"
    task = asyncio.create_task(self.flex.start_stored_protocol("KF_Start_Test"))
    await self.io.wait_sent(command)
    self.assertFalse(task.done())
    with self.assertRaisesRegex(FlexError, "Another Flex operation"):
      await self.flex.list_protocols()
    await self.flex.abort()
    with self.assertRaises(FlexError):
      await task
    self.assertEqual(self.io.commands, ["VER", "REP 4", command])
    self.assertEqual(self.io.features, [b"\x00\x01\x00"])
    self.assertEqual(self.flex.state, "uncertain")

  async def test_terminal_success_and_quoted_name(self) -> None:
    """Only BGN END 0 finishes, with position and initialization still unknown."""
    self.io.responses["REP 4"] = 'REP\r\nREP 4 1 1 "Test ""A""" 200\r\nREP END 0\r\n'
    await self.flex.setup()
    await self.flex.start_stored_protocol('Test "A"')
    self.assertEqual(self.io.commands[-1], 'BGN "Test ""A"""')
    self.assertEqual(self.flex.state, "idle")
    self.assertIsNone(self.flex.processing_slot)

  async def test_errors_timeout_and_qit_never_retry(self) -> None:
    """A failed stored launch stays uncertain and is never resent."""
    for reply in ("BGN\r\nBGN END 8\r\n", "BGN\r\nQIT\r\n", "BGN\r\n"):
      with self.subTest(reply=reply):
        await self.flex.stop()
        await self.flex.setup()
        command = 'BGN "KF_Start_Test"'
        self.io.responses[command] = reply
        before = len(self.io.commands)
        with self.assertRaises((FlexError, TimeoutError)):
          await self.flex.start_stored_protocol("KF_Start_Test", completion_timeout=0.02)
        self.assertEqual(self.io.commands[before:], ["REP 4", command])
        self.assertEqual(self.flex.state, "uncertain")
