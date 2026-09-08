import unittest
from typing import List

from pylabrobot.generic import SerialBarcodeScanner


class FakeSerialIO:
  def __init__(self, chunks: List[bytes]):
    self.chunks = chunks
    self.writes: List[bytes] = []
    self.port = "COM_TEST"
    self.timeout: float = 1
    self.setup_called = False
    self.stop_called = False
    self.reset_input_buffer_called = False

  async def setup(self):
    self.setup_called = True

  async def stop(self):
    self.stop_called = True

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    if len(self.chunks) == 0:
      return b""
    return self.chunks.pop(0)

  async def write(self, data: bytes):
    self.writes.append(data)

  async def reset_input_buffer(self):
    self.reset_input_buffer_called = True

  def get_read_timeout(self) -> float:
    return self.timeout

  def set_read_timeout(self, timeout: float) -> None:
    self.timeout = timeout

  def temporary_timeout(self, timeout: float):
    fake = self

    class TemporaryTimeout:
      def __enter__(self):
        self.original_timeout = fake.timeout
        fake.timeout = timeout

      def __exit__(self, exc_type, exc_value, traceback):
        fake.timeout = self.original_timeout

    return TemporaryTimeout()


def make_scanner(chunks: List[bytes]) -> SerialBarcodeScanner:
  scanner = SerialBarcodeScanner(port="COM_TEST")
  scanner.io = FakeSerialIO(chunks)  # type: ignore[assignment]
  return scanner


class TestSerialBarcodeScanner(unittest.IsolatedAsyncioTestCase):
  async def test_read_line_carriage_return(self):
    scanner = make_scanner([b"1", b"2", b"3", b"\r"])

    self.assertEqual(await scanner.read_line(timeout=1), "123")

  async def test_read_line_newline(self):
    scanner = make_scanner([b"A", b"B", b"C", b"\n"])

    self.assertEqual(await scanner.read_line(timeout=1), "ABC")

  async def test_read_line_timeout_before_data(self):
    scanner = make_scanner([])

    self.assertEqual(await scanner.read_line(timeout=0), "")

  async def test_read_line_rejects_negative_timeout(self):
    scanner = make_scanner([])

    with self.assertRaises(ValueError):
      await scanner.read_line(timeout=-1)

  async def test_reset_input_buffer(self):
    scanner = make_scanner([])

    await scanner.reset_input_buffer()

    fake_io = scanner.io
    assert isinstance(fake_io, FakeSerialIO)
    self.assertTrue(fake_io.reset_input_buffer_called)

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
    scanner = SerialBarcodeScanner(port="COM_TEST")
    scanner.io = FakeSerialIO([b"2", b"2", b"6", b"\r"])  # type: ignore[assignment]

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
    scanner = make_scanner([])

    self.assertIsNone(await scanner.scan_barcode(read_time=0))

  async def test_scan_barcode_with_trigger_command(self):
    scanner = SerialBarcodeScanner(
      port="COM_TEST",
      trigger_command=b"TRIGGER\r",
      untrigger_command=b"UNTRIGGER\r",
    )
    scanner.io = FakeSerialIO([b"1", b"2", b"3", b"\r"])  # type: ignore[assignment]

    barcode = await scanner.scan_barcode(read_time=1)

    assert barcode is not None
    self.assertEqual(barcode.data, "123")
    fake_io = scanner.io
    assert isinstance(fake_io, FakeSerialIO)
    self.assertEqual(fake_io.writes, [b"TRIGGER\r", b"UNTRIGGER\r"])

  async def test_scan_barcode_rejects_negative_read_time(self):
    scanner = make_scanner([])

    with self.assertRaises(ValueError):
      await scanner.scan_barcode(read_time=-1)

  async def test_setup_scan_stop(self):
    scanner = SerialBarcodeScanner(port="COM_TEST")
    fake_io = FakeSerialIO([b"X", b"Y", b"Z", b"\r"])
    scanner.io = fake_io  # type: ignore[assignment]

    await scanner.setup()
    barcode = await scanner.scan_barcode(read_time=1, symbology="Code 39")
    await scanner.stop()

    assert barcode is not None
    self.assertEqual(barcode.data, "XYZ")
    self.assertEqual(barcode.symbology, "Code 39")
    self.assertEqual(barcode.position_on_resource, "bottom")
    self.assertTrue(fake_io.setup_called)
    self.assertTrue(fake_io.stop_called)


if __name__ == "__main__":
  unittest.main()
