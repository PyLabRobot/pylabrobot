"""HID framing and response parsing without hardware access."""

import unittest

from .connection import FlexError, FlexLines, parse_message, reports


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
    self.assertEqual(reports(b"\x1b")[0][:3], b"\x01\x1b\x00")

  def test_response_types_and_invalid_lines(self) -> None:
    """Keep ACK, telemetry, interruption and terminal error codes distinct."""
    self.assertEqual(parse_message("MOH").kind, "ack")
    self.assertEqual(parse_message("TEM 37 22").kind, "report")
    self.assertEqual(parse_message("QIT").kind, "interrupted")
    self.assertEqual(parse_message("MOM END 13").code, 13)
    for line in ("", "MO", "bad"):
      with self.assertRaises(FlexError):
        parse_message(line)
