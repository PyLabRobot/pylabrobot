import unittest
from typing import cast
from unittest.mock import AsyncMock, call

from pylabrobot.generic import SerialBarcodeScanner
from pylabrobot.io.testing import fake_serial


def make_scanner(incoming: bytes) -> SerialBarcodeScanner:
  """Create a scanner with the supplied bytes waiting on its serial transport."""
  scanner = SerialBarcodeScanner(port="COM_TEST")
  scanner.io = fake_serial(incoming=incoming, port="COM_TEST")  # type: ignore[assignment]
  return scanner


class TestSerialBarcodeScanner(unittest.IsolatedAsyncioTestCase):
  async def test_read_line_carriage_return(self):
    scanner = make_scanner(b"123\r")

    self.assertEqual(await scanner.read_line(timeout=1), "123")

  async def test_read_line_newline(self):
    scanner = make_scanner(b"ABC\n")

    self.assertEqual(await scanner.read_line(timeout=1), "ABC")

  async def test_read_line_timeout_before_data(self):
    scanner = make_scanner(b"")

    self.assertEqual(await scanner.read_line(timeout=0), "")

  async def test_read_line_rejects_negative_timeout(self):
    scanner = make_scanner(b"")

    with self.assertRaises(ValueError):
      await scanner.read_line(timeout=-1)

  async def test_reset_input_buffer(self):
    scanner = make_scanner(b"")

    await scanner.reset_input_buffer()

    reset_input_buffer = cast(AsyncMock, scanner.io.reset_input_buffer)
    reset_input_buffer.assert_awaited_once_with()
    self.assertEqual(reset_input_buffer.call_count, reset_input_buffer.await_count)

  def test_rejects_empty_terminators(self):
    with self.assertRaises(ValueError):
      SerialBarcodeScanner(port="COM_TEST", terminators=[])

  def test_rejects_multi_byte_terminators(self):
    with self.assertRaises(ValueError):
      SerialBarcodeScanner(port="COM_TEST", terminators=[b"\r\n"])

  def test_rejects_non_positive_max_line_length(self):
    with self.assertRaises(ValueError):
      SerialBarcodeScanner(port="COM_TEST", max_line_length=0)

  async def test_scan_barcode(self):
    scanner = make_scanner(b"226\r")

    barcode = await scanner.scan_barcode(
      read_time=1,
      symbology="Code 128 (Subset B and C)",
      position_on_resource="right",
    )

    assert barcode is not None
    self.assertEqual(barcode.data, "226")
    self.assertEqual(barcode.symbology, "Code 128 (Subset B and C)")
    self.assertEqual(barcode.position_on_resource, "right")

  async def test_scan_barcode_returns_none_on_timeout(self):
    scanner = make_scanner(b"")

    self.assertIsNone(await scanner.scan_barcode(read_time=0))

  async def test_scan_barcode_with_trigger_command(self):
    scanner = SerialBarcodeScanner(
      port="COM_TEST",
      trigger_command=b"TRIGGER\r",
      untrigger_command=b"UNTRIGGER\r",
    )
    scanner.io = fake_serial(incoming=b"123\r", port="COM_TEST")  # type: ignore[assignment]

    barcode = await scanner.scan_barcode(read_time=1)

    assert barcode is not None
    self.assertEqual(barcode.data, "123")
    write = cast(AsyncMock, scanner.io.write)
    self.assertEqual(write.await_args_list, [call(b"TRIGGER\r"), call(b"UNTRIGGER\r")])
    self.assertEqual(write.call_count, write.await_count)

  async def test_scan_barcode_rejects_negative_read_time(self):
    scanner = make_scanner(b"")

    with self.assertRaises(ValueError):
      await scanner.scan_barcode(read_time=-1)

  async def test_setup_scan_stop(self):
    scanner = SerialBarcodeScanner(port="COM_TEST")
    fake_io = fake_serial(incoming=b"XYZ\r", port="COM_TEST")
    scanner.io = fake_io  # type: ignore[assignment]

    await scanner.setup()
    barcode = await scanner.scan_barcode(read_time=1, symbology="Code 39")
    await scanner.stop()

    assert barcode is not None
    self.assertEqual(barcode.data, "XYZ")
    self.assertEqual(barcode.symbology, "Code 39")
    self.assertEqual(barcode.position_on_resource, "bottom")
    fake_io.setup.assert_awaited_once_with()
    self.assertEqual(fake_io.setup.call_count, fake_io.setup.await_count)
    fake_io.stop.assert_awaited_once_with()
    self.assertEqual(fake_io.stop.call_count, fake_io.stop.await_count)


if __name__ == "__main__":
  unittest.main()
