import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.backend import (
  MultidropCombiBackend,
)


class BackendSerializationTests(unittest.TestCase):
  def test_serialize(self):
    backend = MultidropCombiBackend(port="COM3", timeout=15.0)
    data = backend.serialize()
    self.assertEqual(data["type"], "MultidropCombiBackend")
    self.assertEqual(data["port"], "COM3")
    self.assertEqual(data["timeout"], 15.0)

  def test_serialize_defaults(self):
    backend = MultidropCombiBackend()
    data = backend.serialize()
    self.assertIsNone(data["port"])
    self.assertEqual(data["timeout"], 30.0)


class BackendLifecycleTests(unittest.IsolatedAsyncioTestCase):
  @patch("pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.backend.Serial")
  async def test_setup_and_stop(self, MockSerial):
    mock_serial = MagicMock()
    mock_serial.setup = AsyncMock()
    mock_serial.stop = AsyncMock()
    mock_serial.write = AsyncMock()
    mock_serial.readline = AsyncMock()
    mock_serial.reset_input_buffer = AsyncMock()
    mock_serial.reset_output_buffer = AsyncMock()
    mock_serial._ser = MagicMock()
    mock_serial._ser.timeout = 30.0
    MockSerial.return_value = mock_serial

    # Setup readline responses: drain (empty), VER, EAK
    mock_serial.readline.side_effect = [
      b"",                                       # drain - empty
      b"VER\r\n",                                 # VER echo
      b"MultidropCombi 2.00.29 836-4191\r\n",    # VER data
      b"VER END 0\r\n",                           # VER end
      b"EAK\r\n",                                 # EAK echo
      b"EAK END 0\r\n",                           # EAK end
    ]

    backend = MultidropCombiBackend(port="COM3")
    await backend.setup()

    self.assertEqual(backend._instrument_name, "MultidropCombi")
    self.assertEqual(backend._firmware_version, "2.00.29")
    self.assertEqual(backend._serial_number, "836-4191")
    self.assertIsNotNone(backend.io)

    # Reset readline for QIT during stop
    mock_serial.readline.side_effect = [
      b"QIT\r\n",
      b"QIT END 0\r\n",
    ]
    await backend.stop()
    self.assertIsNone(backend.io)
    mock_serial.stop.assert_awaited_once()
