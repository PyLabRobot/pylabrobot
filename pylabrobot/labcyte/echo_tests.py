import asyncio
import gzip
import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.capabilities.plate_access import PlateAccessState
from pylabrobot.labcyte.echo import Echo, EchoCommandError, EchoDriver


def _soap_response(inner_xml: str) -> bytes:
  body = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<SOAP-ENV:Envelope '
    'xmlns:SOAPSDK1="http://www.w3.org/2001/XMLSchema" '
    'xmlns:SOAPSDK2="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:SOAPSDK3="http://schemas.xmlsoap.org/soap/encoding/" '
    'xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
    'SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<SOAP-ENV:Body SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    f"{inner_xml}"
    "</SOAP-ENV:Body>"
    "</SOAP-ENV:Envelope>"
  ).encode("utf-8")
  gz_body = gzip.compress(body)
  headers = (
    "HTTP/1.1 200 OK\r\n"
    "Server: Echo® Liquid Handler-3.1.1\r\n"
    "Protocol: 3.1\r\n"
    'Content-Type: text/xml; charset="utf-8"\r\n'
    f"Content-Length: {len(gz_body)}\r\n"
    "\r\n"
  ).encode("iso-8859-1")
  return headers + gz_body


class _FakeReader:
  def __init__(self, payload: bytes):
    self.payload = payload

  async def read(self, num_bytes: int) -> bytes:
    chunk = self.payload[:num_bytes]
    self.payload = self.payload[num_bytes:]
    return chunk


class _FakeWriter:
  def __init__(self):
    self.buffer = bytearray()
    self.closed = False

  def write(self, data: bytes) -> None:
    self.buffer.extend(data)

  async def drain(self) -> None:
    return None

  def close(self) -> None:
    self.closed = True

  async def wait_closed(self) -> None:
    return None


class TestEchoDriver(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.driver = EchoDriver(host="echo.local", timeout=1.0)

  async def test_setup_builds_token_from_resolved_ip(self):
    with patch("pylabrobot.labcyte.echo.socket.gethostbyname", return_value="192.168.0.25"), \
         patch("pylabrobot.labcyte.echo.time.time", return_value=1775092000), \
         patch("pylabrobot.labcyte.echo.os.getpid", return_value=4242):
      await self.driver.setup()

    self.assertEqual(self.driver.token, "192.168.0.25:15588:8240:1775092000:4242")

  async def test_get_instrument_info_parses_response_and_preserves_framing(self):
    await self.driver.setup()
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<GetInstrumentInfoResponse>"
        "<GetInstrumentInfo>"
        "<SUCCEEDED>True</SUCCEEDED>"
        "<Status>OK</Status>"
        "<SerialNumber>E6XX-20044</SerialNumber>"
        "<InstrumentName>E6XX-20044</InstrumentName>"
        "<IPAddress>192.168.0.25</IPAddress>"
        "<SoftwareVersion>3.1.1</SoftwareVersion>"
        "<BootTime>2026-03-31_16-06-34</BootTime>"
        "<InstrumentStatus>Normal</InstrumentStatus>"
        "<Model>Echo 650</Model>"
        "</GetInstrumentInfo>"
        "</GetInstrumentInfoResponse>"
      )
    )

    async def fake_open_connection(host: str, port: int):
      self.assertEqual(host, "echo.local")
      self.assertEqual(port, 8000)
      return fake_reader, fake_writer

    with patch("pylabrobot.labcyte.echo.asyncio.open_connection", side_effect=fake_open_connection):
      info = await self.driver.get_instrument_info()

    self.assertEqual(info.model, "Echo 650")
    self.assertEqual(info.serial_number, "E6XX-20044")
    self.assertEqual(info.software_version, "3.1.1")
    request = bytes(fake_writer.buffer)
    self.assertIn(b"POST /Medman HTTP/1.1\nHost: ", request)
    self.assertIn(b'Content-Type: text/xml; charset="utf-8"\n', request)
    self.assertIn(b'SOAPAction: "Some-URI"\r\n\r\n', request)

  async def test_get_access_state_parses_known_signals(self):
    await self.driver.setup()
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<GetDIOEx2Response>"
        "<DIOEx2T>"
        "<DPP>0</DPP>"
        "<SPP>-1</SPP>"
        "<DFO>True</DFO>"
        "<DFC>False</DFC>"
        "<LSO>True</LSO>"
        "<LSI>False</LSI>"
        "</DIOEx2T>"
        "</GetDIOEx2Response>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      state = await self.driver.get_access_state()

    self.assertEqual(state.source_plate_position, -1)
    self.assertTrue(state.source_access_open)
    self.assertFalse(state.source_access_closed)
    self.assertTrue(state.door_open)
    self.assertFalse(state.door_closed)

  async def test_lock_and_unlock_toggle_driver_state(self):
    await self.driver.setup()
    responses = [
      _soap_response(
        "<LockInstrumentResponse><LockInstrument>"
        "<SUCCEEDED>True</SUCCEEDED><Status>Session is locked. Lock count: 1</Status>"
        "<LockID>1775092000</LockID>"
        "</LockInstrument></LockInstrumentResponse>"
      ),
      _soap_response(
        "<UnlockInstrumentResponse><UnlockInstrument>"
        "<SUCCEEDED>True</SUCCEEDED><Status>OK</Status>"
        "</UnlockInstrument></UnlockInstrumentResponse>"
      ),
    ]

    async def fake_open_connection(*_args, **_kwargs):
      return _FakeReader(responses.pop(0)), _FakeWriter()

    with patch("pylabrobot.labcyte.echo.asyncio.open_connection", side_effect=fake_open_connection):
      await self.driver.lock()
      self.assertTrue(self.driver._lock_held)
      await self.driver.unlock()
      self.assertFalse(self.driver._lock_held)

  async def test_motion_requires_lock(self):
    await self.driver.setup()
    with self.assertRaises(EchoCommandError):
      await self.driver.open_source_plate()

  async def test_close_source_plate_uses_empty_retract_defaults(self):
    await self.driver.setup()
    self.driver._lock_held = True
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<RetractSrcPlateGripperResponse><RetractSrcPlateGripper>"
        "<SUCCEEDED>True</SUCCEEDED><Status>OK</Status>"
        "</RetractSrcPlateGripper></RetractSrcPlateGripperResponse>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      await self.driver.close_source_plate()

    request = bytes(fake_writer.buffer)
    payload = gzip.decompress(request.split(b"\r\n\r\n", 1)[1]).decode("utf-8")
    self.assertIn("<PlateType", payload)
    self.assertIn(">None</PlateType>", payload)
    self.assertIn("<BarCodeLocation", payload)
    self.assertIn(">None</BarCodeLocation>", payload)
    self.assertIn("<BarCode", payload)


class TestEchoDevice(unittest.IsolatedAsyncioTestCase):
  async def test_device_delegates_to_driver_and_capability(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    echo.driver.get_instrument_info = AsyncMock()
    echo.plate_access.open_source_plate = AsyncMock()
    echo.plate_access.get_access_state = AsyncMock(return_value=PlateAccessState())

    await echo.get_instrument_info()
    await echo.open_source_plate()
    state = await echo.get_access_state()

    echo.driver.get_instrument_info.assert_awaited_once()
    echo.plate_access.open_source_plate.assert_awaited_once()
    self.assertIsInstance(state, PlateAccessState)

  async def test_stop_unlocks_held_lock(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    echo.driver._lock_held = True
    echo.driver.unlock = AsyncMock()

    await echo.stop()

    echo.driver.unlock.assert_awaited_once()


if __name__ == "__main__":
  unittest.main()
