"""KingFisher report boundary and padding regressions without hardware."""

import unittest

from pylabrobot.thermo_fisher.kingfisher.hid import decode_report, encode_reports


class HIDFramingTests(unittest.TestCase):
  """Check byte preservation and rejection of impossible report lengths."""

  def test_boundaries_and_binary_payloads(self) -> None:
    """Retain every byte across full and partial reports, including ESC and NUL."""
    for size in (1, 62, 63, 64, 126, 127, 256):
      with self.subTest(size=size):
        payload = bytes(range(256))[:size]
        reports = encode_reports(payload)
        self.assertTrue(all(len(report) == 64 for report in reports))
        self.assertEqual(b"".join(decode_report(report) for report in reports), payload)
    with self.assertRaises(ValueError):
      encode_reports(b"")

  def test_padding_short_and_invalid_reports(self) -> None:
    """Ignore stale padding, accept bounded short reads, and reject overlong reads."""
    self.assertEqual(decode_report(b"\x01x" + b"old" * 20), b"x")
    self.assertEqual(decode_report(b"\x01x"), b"x")
    self.assertEqual(decode_report(b"\x00"), b"")
    for report in (b"", b"\x02x", b"\x40" + b"x" * 63, b"\x01" + b"x" * 64):
      with self.subTest(report=report), self.assertRaises(ValueError):
        decode_report(report)
