"""Host-managed loading waits with simulated HID only; no instrument access."""

import asyncio
import unittest
from dataclasses import replace

from .connection import FlexError
from .flex import KingFisherFlex
from .flex_tests import ScriptedHID
from .protocol import FlexPrompt, read_flex_bdz
from .test_helpers import bdz_bytes


class HostConfirmationTests(unittest.IsolatedAsyncioTestCase):
  """Verify confirmations gate every subsequent command and survive no session failure."""

  async def asyncSetUp(self) -> None:
    """Initialize an entirely simulated instrument and a two-prompt script."""
    self.io = ScriptedHID()
    self.flex = KingFisherFlex(timeout=0.2, operation_timeout=0.2)
    self.flex.io = self.io
    self.original = read_flex_bdz(bdz_bytes())
    self.protocol = replace(
      self.original, script='MOR 7\nPAU 1 "tips"\nMOR 6\nPAU 1 "sample"\nTXT "next"\n'
    )
    self.entered: asyncio.Queue[FlexPrompt] = asyncio.Queue()
    self.release = asyncio.Event()
    self.cancelled = asyncio.Event()
    await self.flex.setup()
    await self.flex.initialize()

  async def asyncTearDown(self) -> None:
    """Close simulation and wake pending operations."""
    await self.flex.stop()

  async def confirm(self, prompt: FlexPrompt) -> bool:
    """Wait for this prompt's explicit confirmation and track cancellation."""
    self.entered.put_nowait(prompt)
    try:
      await self.release.wait()
      self.release.clear()
      return True
    finally:
      self.cancelled.set()

  async def test_full_original_preserves_all_non_pause_commands(self) -> None:
    """Host mode changes only PAU execution and reports its confirmations separately."""
    seen = []

    async def confirm(prompt: FlexPrompt) -> bool:
      """Record a simulated operator confirmation."""
      seen.append(prompt)
      return True

    before = len(self.io.commands)
    result = await self.flex.run_protocol(self.original, confirm_prompt=confirm)
    commands = [c.text for c in self.original.preflight() if c.opcode != "PAU"]
    expected = ["EAK", "MOI 0", "MOS 0", "MOM 0", "MOH 0 100", "TEM 0 300"]
    expected += commands + ['TXT "END_OF_RUN"', "TEM 0 300", "TEM 0 300"]
    self.assertEqual(self.io.commands[before:], expected)
    self.assertEqual(tuple(seen), self.original.prompts())
    self.assertEqual(result.completed_commands, len(commands))
    self.assertEqual(result.host_confirmations, len(seen))
    await self.flex.stop()
    events = [e async for e in self.flex.events()]
    self.assertEqual(sum(e.kind == "host_prompt_completed" for e in events), len(seen))
    self.assertFalse(any(e.kind in ("load", "unload", "prompt_completed") for e in events))
    self.assertFalse(any(e.text == "PAU END 0" for e in self.flex.transcript))

  async def test_each_confirmation_gates_motion_and_reader_stays_live(self) -> None:
    """A confirmation releases only its own prompt; telemetry never releases either."""
    task = asyncio.create_task(self.flex.run_protocol(self.protocol, confirm_prompt=self.confirm))
    try:
      first = await asyncio.wait_for(self.entered.get(), 1)
      self.assertEqual((first.processing_slot, first.loading_slot), (7, 2))
      self.assertEqual(self.flex.state, "waiting")
      self.assertNotIn("MOR 6", self.io.commands)
      self.io.emit("TEM 24 23\r\n")
      async for event in self.flex.events():
        if event.kind == "temperature":
          break
      self.assertFalse(task.done())
      with self.assertRaisesRegex(FlexError, "active"):
        await self.flex.initialize()
      self.release.set()
      second = await asyncio.wait_for(self.entered.get(), 1)
      self.assertEqual((second.processing_slot, second.loading_slot), (6, 1))
      self.assertNotIn('TXT "next"', self.io.commands)
      self.assertFalse(any(c.startswith("PAU") for c in self.io.commands))
      self.release.set()
      result = await asyncio.wait_for(task, 1)
      self.assertEqual(result.host_confirmations, 2)
    finally:
      if not task.done():
        task.cancel()
      await asyncio.gather(task, return_exceptions=True)

  async def test_rejected_confirmation_invalidates_without_motion(self) -> None:
    """A negative answer prevents every subsequent command and requires reconnect."""

    async def reject(prompt: FlexPrompt) -> bool:
      """Reject the requested plate action."""
      return False

    with self.assertRaisesRegex(FlexError, "did not confirm"):
      await self.flex.run_protocol(self.protocol, confirm_prompt=reject)
    self.assertEqual(self.io.commands[-1], "MOR 7")
    self.assertEqual(self.flex.state, "uncertain")
    with self.assertRaises(FlexError):
      await self.flex.initialize()
    self.assertFalse(self.io.features)

  async def test_callback_error_invalidates_without_motion(self) -> None:
    """Input/robot failures cannot be treated as a completed plate action."""

    async def fail(prompt: FlexPrompt) -> bool:
      """Simulate an operator interface failure."""
      raise EOFError("operator input closed")

    with self.assertRaises(EOFError):
      await self.flex.run_protocol(self.protocol, confirm_prompt=fail)
    self.assertEqual(self.io.commands[-1], "MOR 7")
    self.assertEqual(self.flex.state, "uncertain")

  async def test_timeout_cancels_callback_without_advancing(self) -> None:
    """The timeout aborts the software run rather than confirming a plate."""
    with self.assertRaises(asyncio.TimeoutError):
      await self.flex.run_protocol(
        self.protocol, confirm_prompt=self.confirm, operator_timeout=0.01
      )
    self.assertTrue(self.cancelled.is_set())
    self.assertEqual(self.io.commands[-1], "MOR 7")
    self.assertFalse(self.io.features)

  async def test_session_failure_wakes_host_wait_and_rejects_late_confirmation(self) -> None:
    """Abort, stop, disconnect, and cancellation release waits without replay."""
    for failure in ("abort", "stop", "disconnect", "cancel"):
      with self.subTest(failure=failure):
        await self.flex.stop()
        await self.flex.setup()
        await self.flex.initialize()
        self.release.clear()
        self.cancelled.clear()
        before = len(self.io.commands)
        task = asyncio.create_task(
          self.flex.run_protocol(self.protocol, confirm_prompt=self.confirm)
        )
        await asyncio.wait_for(self.entered.get(), 1)
        if failure == "abort":
          await self.flex.abort()
        elif failure == "stop":
          await asyncio.wait_for(self.flex.stop(), 1)
        elif failure == "disconnect":
          self.io.incoming.put_nowait(OSError("unplugged"))
        else:
          task.cancel()
        with self.assertRaises((FlexError, asyncio.CancelledError)):
          await asyncio.wait_for(task, 1)
        self.release.set()
        self.assertTrue(self.cancelled.is_set())
        self.assertNotIn("MOR 6", self.io.commands[before:])
        self.assertFalse(any(c.startswith("PAU") for c in self.io.commands[before:]))

  async def test_confirmation_only_after_rotation_completion(self) -> None:
    """An acknowledged but unfinished rotation must not expose a load confirmation."""
    self.io.responses["MOR 7"] = "MOR\r\n"
    task = asyncio.create_task(self.flex.run_protocol(self.protocol, confirm_prompt=self.confirm))
    try:
      await self.io.wait_sent("MOR 7")
      self.assertTrue(self.entered.empty())
      self.io.emit("MOR END 0\r\n")
      await asyncio.wait_for(self.entered.get(), 1)
      self.assertEqual(self.flex.processing_slot, 7)
    finally:
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
  unittest.main()
