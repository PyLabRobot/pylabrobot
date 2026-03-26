"""Legacy. Use pylabrobot.capabilities.barcode_scanning instead."""

from pylabrobot.capabilities.barcode_scanning import BarcodeScannerBackend, BarcodeScannerError
from pylabrobot.legacy.barcode_scanners.keyence import KeyenceBarcodeScannerBackend

from .barcode_scanner import BarcodeScanner
