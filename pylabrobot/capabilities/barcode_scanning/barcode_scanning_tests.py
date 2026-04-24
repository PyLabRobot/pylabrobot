import unittest

from pylabrobot.capabilities.barcode_scanning.backend import BarcodeScannerBackend
from pylabrobot.capabilities.barcode_scanning.barcode_scanning import BarcodeScanner
from pylabrobot.capabilities.barcode_scanning.chatterbox import BarcodeScannerChatterboxBackend
from pylabrobot.resources.barcode import Barcode


class RecordingBarcodeScannerBackend(BarcodeScannerBackend):
  def __init__(self, barcode: str = "TEST-123"):
    self.barcode = barcode
    self.calls = 0

  async def scan_barcode(self) -> Barcode:
    self.calls += 1
    return Barcode(data=self.barcode, symbology="Data Matrix", position_on_resource="bottom")


class TestBarcodeScanner(unittest.IsolatedAsyncioTestCase):
  async def test_scan_returns_barcode(self):
    backend = RecordingBarcodeScannerBackend()
    scanner = BarcodeScanner(backend=backend)
    await scanner._on_setup()

    barcode = await scanner.scan()

    self.assertEqual(backend.calls, 1)
    self.assertEqual(barcode.data, "TEST-123")
    self.assertEqual(barcode.symbology, "Data Matrix")
    self.assertEqual(barcode.position_on_resource, "bottom")

  async def test_scan_requires_setup(self):
    backend = RecordingBarcodeScannerBackend()
    scanner = BarcodeScanner(backend=backend)

    with self.assertRaises(RuntimeError):
      await scanner.scan()

  async def test_chatterbox_backend(self):
    scanner = BarcodeScanner(backend=BarcodeScannerChatterboxBackend(barcode="CHATTERBOX-XYZ"))
    await scanner._on_setup()

    barcode = await scanner.scan()

    self.assertEqual(barcode.data, "CHATTERBOX-XYZ")


if __name__ == "__main__":
  unittest.main()
