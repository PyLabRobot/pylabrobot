import unittest
from unittest.mock import AsyncMock, MagicMock, call

from pylabrobot.barcode_scanners.backend import BarcodeScannerError
from pylabrobot.barcode_scanners.keyence.keyence_backend import (
  HAS_SERIAL,
  KeyenceBarcodeScannerBackend,
)
from pylabrobot.resources.barcode import Barcode


@unittest.skipUnless(HAS_SERIAL, "pyserial is not installed")
class TestKeyenceSendCommand(unittest.IsolatedAsyncioTestCase):
  """send_command must read a full \\r-terminated reply, not a single byte."""

  def setUp(self):
    self.backend = KeyenceBarcodeScannerBackend(port="COM1")
    self.backend.io = MagicMock()
    self.backend.io.write = AsyncMock()
    self.backend.io.read = AsyncMock()

  async def test_writes_carriage_return_terminated_command(self):
    self.backend.io.read.side_effect = [b"\r"]
    await self.backend.send_command("RMOTOR")
    self.backend.io.write.assert_awaited_once_with(b"RMOTOR\r")

  async def test_accumulates_reply_until_carriage_return(self):
    # The bug: a single io.read() returned one byte and truncated the reply.
    self.backend.io.read.side_effect = [b"M", b"O", b"T", b"O", b"R", b"O", b"N", b"\r"]
    response = await self.backend.send_command("RMOTOR")
    self.assertEqual(response, "MOTORON")

  async def test_stops_reading_at_terminator(self):
    # Once \r lands the reply is complete; reading again would block on the next command.
    self.backend.io.read.side_effect = [b"O", b"K", b"\r"]
    await self.backend.send_command("LOFF")
    self.assertEqual(self.backend.io.read.await_count, 3)

  async def test_byteless_read_is_an_empty_reply(self):
    # A byte-less read means the port timeout elapsed with no reply (e.g. no barcode).
    self.backend.io.read.side_effect = [b""]
    response = await self.backend.send_command("LON")
    self.assertEqual(response, "")


@unittest.skipUnless(HAS_SERIAL, "pyserial is not installed")
class TestKeyenceScanBarcode(unittest.IsolatedAsyncioTestCase):
  """scan_barcode must release the LON-latched read beam with LOFF, success or failure."""

  def setUp(self):
    self.backend = KeyenceBarcodeScannerBackend(port="COM1")
    self.replies = {}
    self.raise_on = set()

    def _send(command: str) -> str:
      if command in self.raise_on:
        raise RuntimeError(f"simulated failure on {command}")
      return self.replies.get(command, "")

    self.backend.send_command = AsyncMock(side_effect=_send)

  async def test_success_returns_barcode_then_releases_beam(self):
    self.replies = {"LON": "ABC123", "LOFF": ""}
    barcode = await self.backend.scan_barcode()
    self.assertIsInstance(barcode, Barcode)
    self.assertEqual(barcode.data, "ABC123")
    self.backend.send_command.assert_has_calls([call("LON"), call("LOFF")])

  async def test_releases_beam_when_reader_off(self):
    self.replies = {"LON": "NG", "LOFF": ""}
    with self.assertRaises(BarcodeScannerError):
      await self.backend.scan_barcode()
    self.backend.send_command.assert_has_calls([call("LON"), call("LOFF")])

  async def test_releases_beam_on_error_response(self):
    self.replies = {"LON": "ERR99", "LOFF": ""}
    with self.assertRaises(BarcodeScannerError):
      await self.backend.scan_barcode()
    self.backend.send_command.assert_has_calls([call("LON"), call("LOFF")])

  async def test_loff_failure_does_not_mask_successful_scan(self):
    # A failing LOFF is logged, never propagated, so the scan result survives.
    self.replies = {"LON": "ABC123"}
    self.raise_on = {"LOFF"}
    barcode = await self.backend.scan_barcode()
    self.assertEqual(barcode.data, "ABC123")

  async def test_loff_failure_does_not_mask_scan_error(self):
    # The reader error must propagate, not the swallowed LOFF failure.
    self.replies = {"LON": "NG"}
    self.raise_on = {"LOFF"}
    with self.assertRaises(BarcodeScannerError):
      await self.backend.scan_barcode()


if __name__ == "__main__":
  unittest.main()
