import logging
import unittest
from unittest import mock

from pylabrobot.io import ftdi as ftdi_module
from pylabrobot.io.ftdi import FTDI, HAS_PYLIBFTDI, HAS_PYUSB
from pylabrobot.io.validation_utils import LOG_LEVEL_IO


class _RecordingHandler(logging.Handler):
  """Captures every record emitted to a logger, whatever method produced it - so the
  test cannot be fooled by swapping logger.log for logger.debug/info."""

  def __init__(self) -> None:
    super().__init__(level=LOG_LEVEL_IO)
    self.records: list[logging.LogRecord] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.records.append(record)


@unittest.skipUnless(HAS_PYLIBFTDI and HAS_PYUSB, "pylibftdi/pyusb not installed")
class FTDIEmptyReadTests(unittest.IsolatedAsyncioTestCase):
  """Empty reads must be neither logged nor captured (mirrors io.serial). Polling a quiet
  device reads b'' repeatedly; logging/capturing each one floods the log and capture.
  Locks the `if len(data) != 0:` guard on read()/readline() - asserting on real log
  records (not a mocked method) so re-routing the log call can't fake a pass."""

  def setUp(self) -> None:
    self._handler = _RecordingHandler()
    self._logger = logging.getLogger("pylabrobot.io.ftdi")
    self._prev_level = self._logger.level
    self._logger.setLevel(LOG_LEVEL_IO)
    self._logger.addHandler(self._handler)
    self.addCleanup(self._logger.setLevel, self._prev_level)
    self.addCleanup(self._logger.removeHandler, self._handler)

  def _ftdi(self, return_value: bytes) -> FTDI:
    # Bypass setup() (no hardware) by driving the underlying device read directly.
    dev = FTDI(human_readable_device_name="test", device_id="test")
    dev._dev = mock.Mock()
    dev._dev.read.return_value = return_value
    dev._dev.readline.return_value = return_value
    return dev

  async def test_empty_read_is_not_logged_or_captured(self) -> None:
    dev = self._ftdi(b"")
    with mock.patch.object(ftdi_module.capturer, "record") as mock_record:
      self.assertEqual(await dev.read(4), b"")
    self.assertEqual(self._handler.records, [])
    mock_record.assert_not_called()

  async def test_empty_readline_is_not_logged_or_captured(self) -> None:
    dev = self._ftdi(b"")
    with mock.patch.object(ftdi_module.capturer, "record") as mock_record:
      self.assertEqual(await dev.readline(), b"")
    self.assertEqual(self._handler.records, [])
    mock_record.assert_not_called()

  async def test_nonempty_read_is_logged_and_captured(self) -> None:
    dev = self._ftdi(b"\x01\x02")
    with mock.patch.object(ftdi_module.capturer, "record") as mock_record:
      self.assertEqual(await dev.read(2), b"\x01\x02")
    self.assertEqual(len(self._handler.records), 1)
    self.assertIn(str(b"\x01\x02"), self._handler.records[0].getMessage())
    mock_record.assert_called_once()


if __name__ == "__main__":
  unittest.main()
