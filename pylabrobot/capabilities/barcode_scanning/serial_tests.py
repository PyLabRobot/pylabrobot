import unittest
from typing import List

from pylabrobot.capabilities.barcode_scanning.serial import (
  SerialBarcodeScanner,
  SerialBarcodeScannerBackend,
  SerialBarcodeScannerDriver,
)


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


def make_driver(chunks: List[bytes]) -> SerialBarcodeScannerDriver:
  driver = SerialBarcodeScannerDriver(port="COM_TEST")
  driver.io = FakeSerialIO(chunks)  # type: ignore[assignment]
  return driver


class TestSerialBarcodeScannerDriver(unittest.IsolatedAsyncioTestCase):
  async def test_read_line_carriage_return(self):
    driver = make_driver([b"1", b"2", b"3", b"\r"])

    self.assertEqual(await driver.read_line(timeout=1), "123")

  async def test_read_line_newline(self):
    driver = make_driver([b"A", b"B", b"C", b"\n"])

    self.assertEqual(await driver.read_line(timeout=1), "ABC")

  async def test_read_line_timeout_before_data(self):
    driver = make_driver([])

    self.assertEqual(await driver.read_line(timeout=0), "")

  async def test_reset_input_buffer(self):
    driver = make_driver([])

    await driver.reset_input_buffer()

    fake_io = driver.io
    assert isinstance(fake_io, FakeSerialIO)
    self.assertTrue(fake_io.reset_input_buffer_called)

  def test_rejects_empty_terminators(self):
    with self.assertRaises(ValueError):
      SerialBarcodeScannerDriver(port="COM_TEST", terminators=[])

  def test_rejects_multi_byte_terminators(self):
    with self.assertRaises(ValueError):
      SerialBarcodeScannerDriver(port="COM_TEST", terminators=[b"\r\n"])


class TestSerialBarcodeScannerBackend(unittest.IsolatedAsyncioTestCase):
  async def test_scan_barcode(self):
    driver = make_driver([b"2", b"2", b"6", b"\r"])
    backend = SerialBarcodeScannerBackend(
      driver=driver, symbology="Code 128 (Subset B and C)", position_on_resource="right"
    )

    barcode = await backend.scan_barcode(read_time=1)

    assert barcode is not None
    self.assertEqual(barcode.data, "226")
    self.assertEqual(barcode.symbology, "Code 128 (Subset B and C)")
    self.assertEqual(barcode.position_on_resource, "right")

  async def test_scan_barcode_returns_none_on_timeout(self):
    driver = make_driver([])
    backend = SerialBarcodeScannerBackend(driver=driver)

    self.assertIsNone(await backend.scan_barcode(read_time=0))

  async def test_scan_barcode_with_trigger_command(self):
    driver = make_driver([b"1", b"2", b"3", b"\r"])
    backend = SerialBarcodeScannerBackend(
      driver=driver,
      trigger_command=b"TRIGGER\r",
      untrigger_command=b"UNTRIGGER\r",
    )

    barcode = await backend.scan_barcode(read_time=1)

    assert barcode is not None
    self.assertEqual(barcode.data, "123")
    fake_io = driver.io
    assert isinstance(fake_io, FakeSerialIO)
    self.assertEqual(fake_io.writes, [b"TRIGGER\r", b"UNTRIGGER\r"])

  async def test_scan_barcode_rejects_negative_read_time(self):
    driver = make_driver([])
    backend = SerialBarcodeScannerBackend(driver=driver)

    with self.assertRaises(ValueError):
      await backend.scan_barcode(read_time=-1)


class TestSerialBarcodeScannerDevice(unittest.IsolatedAsyncioTestCase):
  async def test_device_setup_scan_stop(self):
    scanner = SerialBarcodeScanner(port="COM_TEST")
    fake_io = FakeSerialIO([b"X", b"Y", b"Z", b"\r"])
    scanner.driver.io = fake_io  # type: ignore[assignment]

    await scanner.setup()
    barcode = await scanner.barcode_scanning.scan(read_time=1)
    await scanner.stop()

    assert barcode is not None
    self.assertEqual(barcode.data, "XYZ")
    self.assertTrue(fake_io.setup_called)
    self.assertTrue(fake_io.stop_called)


if __name__ == "__main__":
  unittest.main()
