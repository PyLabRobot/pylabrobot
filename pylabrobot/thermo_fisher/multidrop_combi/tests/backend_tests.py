import contextlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.thermo_fisher.multidrop_combi.driver import MultidropCombiDriver


class DriverSerializationTests(unittest.TestCase):
  def test_serialize(self):
    driver = MultidropCombiDriver(port="COM3", timeout=15.0)
    data = driver.serialize()
    self.assertEqual(data["type"], "MultidropCombiDriver")
    self.assertEqual(data["port"], "COM3")
    self.assertEqual(data["timeout"], 15.0)

  def test_serialize_defaults(self):
    driver = MultidropCombiDriver(port="/dev/ttyUSB0")
    data = driver.serialize()
    self.assertEqual(data["port"], "/dev/ttyUSB0")
    self.assertEqual(data["timeout"], 30.0)


class DriverLifecycleTests(unittest.IsolatedAsyncioTestCase):
  @patch("pylabrobot.thermo_fisher.multidrop_combi.driver.Serial")
  async def test_setup_and_stop(self, MockSerial):
    mock_serial = MagicMock()
    mock_serial.setup = AsyncMock()
    mock_serial.stop = AsyncMock()
    mock_serial.write = AsyncMock()
    mock_serial.readline = AsyncMock()
    mock_serial.reset_input_buffer = AsyncMock()
    mock_serial.reset_output_buffer = AsyncMock()
    MockSerial.return_value = mock_serial

    # Mock timeout API
    _timeout = 30.0

    def get_read_timeout():
      return _timeout

    def set_read_timeout(t):
      nonlocal _timeout
      _timeout = t

    @contextlib.contextmanager
    def temporary_timeout(t):
      original = get_read_timeout()
      set_read_timeout(t)
      try:
        yield
      finally:
        set_read_timeout(original)

    mock_serial.get_read_timeout = get_read_timeout
    mock_serial.set_read_timeout = set_read_timeout
    mock_serial.temporary_timeout = temporary_timeout

    # Setup readline responses: drain (empty), VER
    mock_serial.readline.side_effect = [
      b"",  # drain - empty
      b"VER\r\n",  # VER echo
      b"MultidropCombi 2.00.29 836-4191\r\n",  # VER data
      b"VER END 0\r\n",  # VER end
    ]

    driver = MultidropCombiDriver(port="COM3")
    await driver.setup()

    self.assertEqual(driver._instrument_name, "MultidropCombi")
    self.assertEqual(driver._firmware_version, "2.00.29")
    self.assertEqual(driver._serial_number, "836-4191")

    # Reset readline for QIT during stop
    mock_serial.readline.side_effect = [
      b"QIT\r\n",
      b"QIT END 0\r\n",
    ]
    await driver.stop()
    mock_serial.stop.assert_awaited_once()
