"""HID framing and response parsing without hardware access."""

import asyncio
import unittest

from pylabrobot.thermo_fisher.kingfisher.hid import encode_reports

from .connection import FlexError, FlexLines, parse_message, wait_for_completion


class FlexCompletionTests(unittest.IsolatedAsyncioTestCase):
  """Keep cancellation distinct from a concurrently completed operation."""

  async def test_completion_cannot_consume_caller_cancellation(self) -> None:
    """Cancel the caller in the same event-loop turn that its child completes."""

    async def complete() -> int:
      caller.cancel()
      return 42

    caller = asyncio.create_task(wait_for_completion(complete(), 1))
    with self.assertRaises(asyncio.CancelledError):
      await caller

  async def test_timeout_and_cancellation_join_pending_operation(self) -> None:
    """The child is stopped before either kind of interruption reaches the caller."""
    for cancel in (False, True):
      with self.subTest(cancel=cancel):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def pending() -> None:
          entered.set()
          try:
            await asyncio.Event().wait()
          finally:
            stopped.set()

        caller = asyncio.create_task(wait_for_completion(pending(), None if cancel else 0.01))
        await entered.wait()
        if cancel:
          caller.cancel()
        with self.assertRaises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
          await caller
        self.assertTrue(stopped.is_set())


class FlexFramingTests(unittest.TestCase):
  """Validate fragmentation, stale padding and response structure."""

  def test_arbitrary_fragmentation_coalescing_and_stale_padding(self) -> None:
    """A line can span any report boundary; padding can contain unrelated old text."""
    payload = b"VER\r\nVER KingFisherFlex 1.01.0 fixture\r\nVER END 0\r\nTEM 24 22\r\n"
    expected = payload.decode().splitlines()
    for chunk_size in range(1, 64):
      parser = FlexLines()
      actual: list[str] = []
      for offset in range(0, len(payload), chunk_size):
        chunk = payload[offset : offset + chunk_size]
        stale = b"PAU END 0\r\n" * 6
        report = bytes([len(chunk)]) + (chunk + stale)[:63]
        actual.extend(parser.feed(report))
      self.assertEqual(actual, expected)
    for invalid in (b"", b"\x40" + b"x" * 63, b"\x02x", b"\x01" + b"x" * 64):
      with self.assertRaises(FlexError):
        FlexLines().feed(invalid)
    self.assertEqual(encode_reports(b"\x1b")[0][:3], b"\x01\x1b\x00")

  def test_response_types_and_invalid_lines(self) -> None:
    """Keep ACK, telemetry, interruption and terminal error codes distinct."""
    self.assertEqual(parse_message("MOH").kind, "ack")
    self.assertEqual(parse_message("TEM 37 22").kind, "report")
    self.assertEqual(parse_message("QIT").kind, "interrupted")
    self.assertEqual(parse_message("MOM END 13").code, 13)
    for line in ("", "MO", "bad"):
      with self.assertRaises(FlexError):
        parse_message(line)
