import gzip
import html
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, call, patch

from pylabrobot.capabilities.plate_access import PlateAccessState
from pylabrobot.labcyte.echo import (
  DEFAULT_DRY_TIMEOUT,
  DEFAULT_HOME_TIMEOUT,
  DEFAULT_LOADED_RETRACT_TIMEOUT,
  DEFAULT_SURVEY_TIMEOUT,
  Echo,
  EchoCommandError,
  EchoDriver,
  EchoDryPlateMode,
  EchoDryPlateParams,
  EchoEvent,
  EchoFluidInfo,
  EchoPlateAccessBackend,
  EchoPlateMap,
  EchoPlateWorkflowResult,
  EchoProtocolError,
  EchoSurveyData,
  EchoSurveyParams,
  EchoSurveyRunResult,
  EchoTransferredWell,
  _HttpMessage,
  _RpcResult,
  EchoTransferPrintOptions,
  EchoTransferResult,
  build_echo_transfer_plan,
)
from pylabrobot.resources import Plate, Well, create_ordered_items_2d, set_volume_tracking


def _soap_response(
  inner_xml: str,
  *,
  content_length_override: int | None = None,
  include_content_length: bool = True,
) -> bytes:
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
  content_length = len(gz_body) if content_length_override is None else content_length_override
  header_lines = [
    "HTTP/1.1 200 OK",
    "Server: Echo® Liquid Handler-3.1.1",
    "Protocol: 3.1",
    'Content-Type: text/xml; charset="utf-8"',
  ]
  if include_content_length:
    header_lines.append(f"Content-Length: {content_length}")
  headers = ("\r\n".join(header_lines) + "\r\n\r\n").encode("iso-8859-1")
  return headers + gz_body


def _soap_request(inner_xml: str) -> bytes:
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
    "POST /Medman HTTP/1.1\r\n"
    "Host: event-stream\r\n"
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


class _StubWell:
  def __init__(self, identifier: str):
    self._identifier = identifier

  def get_identifier(self) -> str:
    return self._identifier


class _StubPlate:
  def __init__(self, identifiers: list[str]):
    self._wells = {identifier: _StubWell(identifier) for identifier in identifiers}

  def get_all_items(self):
    return list(self._wells.values())

  def get_well(self, identifier: str):
    return self._wells[identifier]


def _make_plate(name: str, model: str) -> Plate:
  return Plate(
    name=name,
    size_x=12,
    size_y=8,
    size_z=4,
    model=model,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=3,
      num_items_y=2,
      dx=0,
      dy=0,
      dz=0,
      item_dx=1,
      item_dy=1,
      size_x=1,
      size_y=1,
      size_z=1,
      max_volume=100,
    ),
  )


class TestEchoDriver(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.driver = EchoDriver(host="echo.local", timeout=1.0)

  def tearDown(self):
    set_volume_tracking(False)

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

  async def test_get_echo_configuration_returns_raw_config(self):
    await self.driver.setup()
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<GetEchoConfigurationResponse><GetEchoConfiguration>"
        "<SUCCEEDED>True</SUCCEEDED><Status>OK</Status>"
        "<xmlEchoConfig>"
        '&lt;Configuration internal=&quot;true&quot;&gt;&lt;/Configuration&gt;'
        "</xmlEchoConfig>"
        "</GetEchoConfiguration></GetEchoConfigurationResponse>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      config = await self.driver.get_echo_configuration()

    payload = gzip.decompress(bytes(fake_writer.buffer).split(b"\r\n\r\n", 1)[1]).decode("utf-8")
    self.assertIn("<GetEchoConfiguration", payload)
    self.assertIn('&lt;Configuration internal="true"&gt;', payload)
    self.assertEqual(config, '<Configuration internal="true"></Configuration>')

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
    self.assertEqual(state.destination_plate_position, 0)
    self.assertTrue(state.source_access_open)
    self.assertFalse(state.source_access_closed)
    self.assertFalse(state.destination_access_open)
    self.assertTrue(state.destination_access_closed)
    self.assertTrue(state.door_open)
    self.assertFalse(state.door_closed)

  async def test_get_access_state_infers_destination_from_position(self):
    await self.driver.setup()
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<GetDIOEx2Response>"
        "<DIOEx2T>"
        "<DPP>-1</DPP>"
        "<SPP>0</SPP>"
        "<DFO>1</DFO>"
        "<DFC>0</DFC>"
        "</DIOEx2T>"
        "</GetDIOEx2Response>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      state = await self.driver.get_access_state()

    self.assertFalse(state.source_access_open)
    self.assertTrue(state.source_access_closed)
    self.assertTrue(state.destination_access_open)
    self.assertFalse(state.destination_access_closed)
    self.assertTrue(state.door_open)
    self.assertFalse(state.door_closed)

  async def test_read_events_parses_handle_event_payloads(self):
    await self.driver.setup()
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_request(
        "<handleEvent><Event>"
        "<id>7</id>"
        "<source>Logger</source>"
        "<payload>DoWellTransfer() START</payload>"
        "<timestamp>2026-04-13T17:02:12</timestamp>"
        "</Event></handleEvent>"
      )
    )

    async def fake_open_connection(host: str, port: int):
      self.assertEqual(host, "echo.local")
      self.assertEqual(port, 8010)
      return fake_reader, fake_writer

    with patch("pylabrobot.labcyte.echo.asyncio.open_connection", side_effect=fake_open_connection):
      events = await self.driver.read_events(max_events=1, timeout=1.0)

    self.assertEqual(
      events,
      [
        EchoEvent(
          event_id="7",
          source="Logger",
          payload="DoWellTransfer() START",
          timestamp="2026-04-13T17:02:12",
          raw={
            "id": 7,
            "source": "Logger",
            "payload": "DoWellTransfer() START",
            "timestamp": "2026-04-13T17:02:12",
          },
        )
      ],
    )
    registration = bytes(fake_writer.buffer)
    self.assertIn(b"POST /Medman HTTP/1.1\nHost: ", registration)
    self.assertIn(b"add", gzip.decompress(registration.split(b"\r\n\r\n", 1)[1]))

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

  async def test_unlock_tolerates_stale_local_lock_state(self):
    await self.driver.setup()
    self.driver._lock_held = True
    self.driver._rpc = AsyncMock(return_value=_RpcResult(
      method="UnlockInstrument",
      values={"SUCCEEDED": False, "Status": "Caller does not own the lock"},
      succeeded=False,
      status="Caller does not own the lock",
    ))

    await self.driver.unlock()

    self.driver._rpc.assert_awaited_once_with(
      "UnlockInstrument",
      (("LockID", "string", self.driver.token),),
    )
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

  async def test_close_source_plate_with_plate_uses_loaded_timeout(self):
    await self.driver.setup()
    self.driver._lock_held = True
    self.driver._rpc = AsyncMock(return_value=_RpcResult(
      method="RetractSrcPlateGripper",
      values={"SUCCEEDED": True, "Status": "OK"},
      succeeded=True,
      status="OK",
    ))

    await self.driver.close_source_plate(plate_type="384PP_DMSO2")

    self.driver._rpc.assert_awaited_once_with(
      "RetractSrcPlateGripper",
      (
        ("PlateType", "string", "384PP_DMSO2"),
        ("BarCodeLocation", "string", "None"),
        ("BarCode", "string", ""),
      ),
      timeout=DEFAULT_LOADED_RETRACT_TIMEOUT,
    )

  async def test_set_plate_map_serializes_selected_wells(self):
    await self.driver.setup()
    self.driver._lock_held = True
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<SetPlateMapResponse><SetPlateMap>"
        "<SUCCEEDED>True</SUCCEEDED><Status>OK</Status>"
        "</SetPlateMap></SetPlateMapResponse>"
      )
    )
    plate_map = EchoPlateMap(plate_type="384PP_DMSO2", well_identifiers=("A1", "B2"))

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      await self.driver.set_plate_map(plate_map)

    payload = gzip.decompress(bytes(fake_writer.buffer).split(b"\r\n\r\n", 1)[1]).decode("utf-8")
    self.assertIn('&lt;PlateMap p="384PP_DMSO2"&gt;', payload)
    self.assertIn('&lt;Well n="A1" r="0" c="0"', payload)
    self.assertIn('&lt;Well n="B2" r="1" c="1"', payload)

  async def test_survey_plate_uses_default_survey_timeout(self):
    await self.driver.setup()
    self.driver._lock_held = True
    params = EchoSurveyParams(plate_type="384PP_DMSO2", num_rows=16, num_cols=24)
    self.driver._rpc = AsyncMock(return_value=_RpcResult(
      method="PlateSurvey",
      values={"SUCCEEDED": True, "Status": "OK"},
      succeeded=True,
      status="OK",
    ))

    result = await self.driver.survey_plate(params)

    self.assertIsNone(result)
    self.driver._rpc.assert_awaited_once_with(
      "PlateSurvey",
      (
        ("PlateType", "string", "384PP_DMSO2"),
        ("StartRow", "int", "0"),
        ("StartCol", "int", "0"),
        ("NumRows", "int", "16"),
        ("NumCols", "int", "24"),
        ("Save", "boolean", "True"),
        ("CheckSrc", "boolean", "False"),
      ),
      timeout=DEFAULT_SURVEY_TIMEOUT,
    )

  async def test_survey_plate_parses_nested_survey_xml(self):
    await self.driver.setup()
    self.driver._lock_held = True
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<PlateSurveyResponse><PlateSurvey>"
        "<SUCCEEDED>True</SUCCEEDED>"
        "<Status>OK</Status>"
        "<PlateSurveyData>"
        '<platesurvey p="384PP_DMSO2">'
        '<Well n="A1" r="0" c="0" comp="1.83" />'
        '<Well n="B2" r="1" c="1" comp="1.75" />'
        "</platesurvey>"
        "</PlateSurveyData>"
        "</PlateSurvey></PlateSurveyResponse>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      data = await self.driver.survey_plate(
        EchoSurveyParams(plate_type="384PP_DMSO2", num_rows=16, num_cols=24)
      )

    assert data is not None
    self.assertEqual(data.plate_type, "384PP_DMSO2")
    self.assertEqual([well.identifier for well in data.wells], ["A1", "B2"])
    self.assertEqual(data.wells[0].raw_attributes["comp"], "1.83")
    self.assertIn("<platesurvey", data.raw_xml)

  async def test_survey_plate_handles_gzip_body_longer_than_advertised_length(self):
    await self.driver.setup()
    self.driver._lock_held = True
    inner_xml = (
      "<PlateSurveyResponse><PlateSurvey>"
      "<SUCCEEDED>True</SUCCEEDED>"
      "<Status>OK</Status>"
      "<PlateSurveyData>"
      '<platesurvey p="384PP_DMSO2">'
      '<Well n="A1" r="0" c="0" comp="1.83" />'
      '<Well n="B2" r="1" c="1" comp="1.75" />'
      "</platesurvey>"
      "</PlateSurveyData>"
      "</PlateSurvey></PlateSurveyResponse>"
    )
    response = _soap_response(inner_xml)
    _, gz_body = response.split(b"\r\n\r\n", 1)
    advertised_length = max(1, len(gz_body) - 8)
    response = _soap_response(inner_xml, content_length_override=advertised_length)
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(response)

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      data = await self.driver.survey_plate(
        EchoSurveyParams(plate_type="384PP_DMSO2", num_rows=16, num_cols=24)
      )

    assert data is not None
    self.assertEqual(data.plate_type, "384PP_DMSO2")
    self.assertEqual([well.identifier for well in data.wells], ["A1", "B2"])

  async def test_survey_plate_reads_missing_content_length_until_gzip_complete(self):
    await self.driver.setup()
    self.driver._lock_held = True
    wells = "".join(
      f'<Well n="{chr(ord("A") + row)}{column + 1}" r="{row}" c="{column}" '
      f'comp="{row}.{column}" vl="{1000 + row * 24 + column}" '
      f'cvl="{900 + row * 24 + column}" extra="{row * 1234567 + column}" />'
      for row in range(16)
      for column in range(24)
    )
    inner_xml = (
      "<PlateSurveyResponse><PlateSurvey>"
      "<SUCCEEDED>True</SUCCEEDED>"
      "<Status>OK</Status>"
      "<PlateSurveyData>"
      f'<platesurvey p="384PP_DMSO2">{wells}</platesurvey>'
      "</PlateSurveyData>"
      "</PlateSurvey></PlateSurveyResponse>"
    )
    response = _soap_response(inner_xml, include_content_length=False)
    self.assertGreater(len(response), 4096)
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(response)

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      data = await self.driver.survey_plate(
        EchoSurveyParams(plate_type="384PP_DMSO2", num_rows=16, num_cols=24)
      )

    assert data is not None
    self.assertEqual(data.plate_type, "384PP_DMSO2")
    self.assertEqual(len(data.wells), 384)

  def test_decoded_body_rejects_incomplete_gzip(self):
    compressed = gzip.compress(b"<ok />")
    message = _HttpMessage(
      start_line="HTTP/1.1 200 OK",
      headers={"content-length": str(len(compressed) - 8)},
      body=compressed[:-8],
    )

    with self.assertRaisesRegex(EchoProtocolError, "Incomplete gzip-compressed Echo HTTP body"):
      message.decoded_body()

  async def test_get_survey_data_parses_escaped_survey_xml(self):
    await self.driver.setup()
    survey_xml = (
      '<platesurvey p="384PP_DMSO2">'
      '<Well n="A1" r="0" c="0" comp="1.83" />'
      "</platesurvey>"
    )
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<GetSurveyDataResponse><GetSurveyData>"
        "<SUCCEEDED>True</SUCCEEDED>"
        "<Status>OK</Status>"
        f"<PlateSurveyData>{html.escape(survey_xml)}</PlateSurveyData>"
        "</GetSurveyData></GetSurveyDataResponse>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      data = await self.driver.get_survey_data()

    self.assertEqual(data.plate_type, "384PP_DMSO2")
    self.assertEqual(len(data.wells), 1)
    self.assertEqual(data.wells[0].identifier, "A1")
    self.assertEqual(data.wells[0].row, 0)
    self.assertEqual(data.wells[0].column, 0)

  async def test_dry_plate_uses_selected_mode_and_timeout(self):
    await self.driver.setup()
    self.driver._lock_held = True
    params = EchoDryPlateParams(mode=EchoDryPlateMode.TWO_PASS, timeout=45.0)
    self.driver._rpc = AsyncMock(return_value=_RpcResult(
      method="DryPlate",
      values={"SUCCEEDED": True, "Status": "OK"},
      succeeded=True,
      status="OK",
    ))

    await self.driver.dry_plate(params)

    self.driver._rpc.assert_awaited_once_with(
      "DryPlate",
      (("Type", "string", "TWO_PASS"),),
      timeout=45.0,
    )

  async def test_dry_plate_uses_default_timeout(self):
    await self.driver.setup()
    self.driver._lock_held = True
    self.driver._rpc = AsyncMock(return_value=_RpcResult(
      method="DryPlate",
      values={"SUCCEEDED": True, "Status": "OK"},
      succeeded=True,
      status="OK",
    ))

    await self.driver.dry_plate()

    self.driver._rpc.assert_awaited_once_with(
      "DryPlate",
      (("Type", "string", "TWO_PASS"),),
      timeout=DEFAULT_DRY_TIMEOUT,
    )

  async def test_low_level_actuator_commands_serialize_expected_rpc(self):
    await self.driver.setup()
    self.driver._lock_held = True
    self.driver._rpc = AsyncMock(return_value=_RpcResult(
      method="AnyCommand",
      values={"SUCCEEDED": True, "Status": "OK"},
      succeeded=True,
      status="OK",
    ))

    await self.driver.home_axes()
    await self.driver.open_door(timeout=2.0)
    await self.driver.set_pump_direction(False, timeout=3.0)
    await self.driver.enable_bubbler_pump(True)
    await self.driver.actuate_bubbler_nozzle(False, timeout=4.0)
    await self.driver.raise_coupling_fluid(timeout=5.0)
    await self.driver.lower_coupling_fluid()
    await self.driver.enable_vacuum_nozzle(True)
    await self.driver.actuate_vacuum_nozzle(False)
    await self.driver.actuate_ionizer(True, timeout=6.0)

    self.driver._rpc.assert_has_awaits([
      call("HomeAxes", timeout=DEFAULT_HOME_TIMEOUT),
      call("OpenDoor", timeout=2.0),
      call("SetPumpDir", (("Value", "boolean", "False"),), timeout=3.0),
      call("EnableBubblerPump", (("Value", "boolean", "True"),), timeout=None),
      call("ActuateBubblerNozzle", (("Value", "boolean", "False"),), timeout=4.0),
      call("ActuateBubblerNozzle", (("Value", "boolean", "True"),), timeout=5.0),
      call("ActuateBubblerNozzle", (("Value", "boolean", "False"),), timeout=None),
      call("EnableVacuumNozzle", (("Value", "boolean", "True"),), timeout=None),
      call("ActuateVacuumNozzle", (("Value", "boolean", "False"),), timeout=None),
      call("ActuateIonizer", (("Value", "boolean", "True"),), timeout=6.0),
    ])

  async def test_soap_fault_raises_command_error_with_fault_string(self):
    await self.driver.setup()
    self.driver._lock_held = True
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<SOAP-ENV:Fault>"
        "<faultcode>Server</faultcode>"
        "<faultstring>MM1302001: Unknown Source Plate, inset</faultstring>"
        "</SOAP-ENV:Fault>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      with self.assertRaises(EchoCommandError) as ctx:
        await self.driver.actuate_vacuum_nozzle(True)

    self.assertIn("ActuateVacuumNozzle failed", str(ctx.exception))
    self.assertIn("MM1302001", str(ctx.exception))

  async def test_get_current_plate_type_helpers_return_strings(self):
    await self.driver.setup()
    self.driver._rpc = AsyncMock(side_effect=[
      _RpcResult(
        method="GetCurrentSrcPlateType",
        values={"SUCCEEDED": True, "Status": "OK", "GetCurrentSrcPlateType": "384PP_DMSO2"},
        succeeded=True,
        status="OK",
      ),
      _RpcResult(
        method="GetCurrentDstPlateType",
        values={"SUCCEEDED": True, "Status": "OK", "GetCurrentDstPlateType": "1536LDV_Dest"},
        succeeded=True,
        status="OK",
      ),
    ])

    source = await self.driver.get_current_source_plate_type()
    destination = await self.driver.get_current_destination_plate_type()

    self.assertEqual(source, "384PP_DMSO2")
    self.assertEqual(destination, "1536LDV_Dest")

  async def test_plate_presence_helpers_treat_none_as_empty(self):
    await self.driver.setup()
    self.driver._rpc = AsyncMock(side_effect=[
      _RpcResult(
        method="GetCurrentSrcPlateType",
        values={"SUCCEEDED": True, "Status": "OK", "PlateType": "None"},
        succeeded=True,
        status="OK",
      ),
      _RpcResult(
        method="GetCurrentDstPlateType",
        values={"SUCCEEDED": True, "Status": "OK", "PlateType": "1536LDV_Dest"},
        succeeded=True,
        status="OK",
      ),
    ])

    self.assertFalse(await self.driver.is_source_plate_present())
    self.assertTrue(await self.driver.is_destination_plate_present())

  async def test_get_all_destination_plate_names_parses_nested_xml(self):
    await self.driver.setup()
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<GetAllDestPlateNamesResponse><GetAllDestPlateNames>"
        "<SUCCEEDED>True</SUCCEEDED>"
        "<Status>OK</Status>"
        "<Names><Name>1536LDV_Dest</Name><Name>Corning 3764 Black Clear Bottom</Name></Names>"
        "</GetAllDestPlateNames></GetAllDestPlateNamesResponse>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      names = await self.driver.get_all_destination_plate_names()

    self.assertEqual(names, ["1536LDV_Dest", "Corning 3764 Black Clear Bottom"])

  async def test_get_all_protocol_names_preserves_duplicate_tags(self):
    await self.driver.setup()
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<GetAllProtocolNamesResponse><GetAllProtocolNames>"
        "<SUCCEEDED>True</SUCCEEDED>"
        "<Status>OK</Status>"
        "<ProtocolName>baseline</ProtocolName>"
        "<ProtocolName>dose-response</ProtocolName>"
        "</GetAllProtocolNames></GetAllProtocolNamesResponse>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      names = await self.driver.get_all_protocol_names()

    self.assertEqual(names, ["baseline", "dose-response"])

  async def test_get_protocol_and_power_calibration_return_raw_payloads(self):
    await self.driver.setup()
    self.driver._rpc = AsyncMock(side_effect=[
      _RpcResult(
        method="GetProtocol",
        values={"SUCCEEDED": True, "Status": "OK", "Protocol": "<Protocol />"},
        succeeded=True,
        status="OK",
      ),
      _RpcResult(
        method="GetPwrCal",
        values={"SUCCEEDED": True, "Status": "OK", "PwrCal": "current"},
        succeeded=True,
        status="OK",
      ),
    ])

    protocol = await self.driver.get_protocol("baseline")
    power_calibration = await self.driver.get_power_calibration()

    self.driver._rpc.assert_has_awaits([
      call("GetProtocol", (("ProtocolName", "string", "baseline"),)),
      call("GetPwrCal"),
    ])
    self.assertEqual(protocol["Protocol"], "<Protocol />")
    self.assertEqual(power_calibration["PwrCal"], "current")

  async def test_get_fluid_info_parses_response(self):
    await self.driver.setup()
    self.driver._rpc = AsyncMock(return_value=_RpcResult(
      method="GetFluidInfo",
      values={
        "SUCCEEDED": True,
        "Status": "OK",
        "FluidName": "DMSO",
        "Description": "Dimethyl sulfoxide",
        "FCMin": "0.0",
        "FCMax": "100.0",
        "FCUnits": "%",
      },
      succeeded=True,
      status="OK",
    ))

    info = await self.driver.get_fluid_info("DMSO")

    self.driver._rpc.assert_awaited_once_with(
      "GetFluidInfo",
      (("FluidType", "string", "DMSO"),),
    )
    self.assertEqual(
      info,
      EchoFluidInfo(
        name="DMSO",
        description="Dimethyl sulfoxide",
        fc_min=0.0,
        fc_max=100.0,
        fc_units="%",
        raw={
          "SUCCEEDED": True,
          "Status": "OK",
          "FluidName": "DMSO",
          "Description": "Dimethyl sulfoxide",
          "FCMin": "0.0",
          "FCMax": "100.0",
          "FCUnits": "%",
        },
      ),
    )

  async def test_build_echo_transfer_plan_generates_protocol_and_sparse_plate_map(self):
    source_plate = _make_plate("source", "384PP_DMSO2")
    destination_plate = _make_plate("destination", "1536LDV_Dest")

    plan = build_echo_transfer_plan(
      source_plate,
      destination_plate,
      [
        ("A1", "B2", 2.5),
        (source_plate.get_well("A2"), destination_plate.get_well("A3"), 5.0),
      ],
      protocol_name="dose",
    )

    self.assertEqual(plan.source_plate_type, "384PP_DMSO2")
    self.assertEqual(plan.destination_plate_type, "1536LDV_Dest")
    self.assertEqual(plan.plate_map.well_identifiers, ("A1", "A2"))
    self.assertIn('<Protocol Name="dose">', plan.protocol_xml)
    self.assertIn('<wp n="A1" dn="B2" v="2.5" />', plan.protocol_xml)
    self.assertIn('<wp n="A2" dn="A3" v="5" />', plan.protocol_xml)

  async def test_build_echo_transfer_plan_accepts_ul_volumes(self):
    source_plate = _make_plate("source", "384PP_DMSO2")
    destination_plate = _make_plate("destination", "1536LDV_Dest")

    plan = self.driver.build_transfer_plan(
      source_plate,
      destination_plate,
      [("A1", "B1", 0.005)],
      volume_unit="uL",
    )

    self.assertEqual(plan.transfers[0].volume_nl, 5.0)
    self.assertIn('<wp n="A1" dn="B1" v="5" />', plan.protocol_xml)

  async def test_store_parameter_serializes_scalar_type(self):
    await self.driver.setup()
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<StoreParameterResponse><StoreParameter>"
        "<SUCCEEDED>True</SUCCEEDED><Status>OK</Status>"
        "</StoreParameter></StoreParameterResponse>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      await self.driver.store_parameter("Access", 1775092000)

    payload = gzip.decompress(bytes(fake_writer.buffer).split(b"\r\n\r\n", 1)[1]).decode("utf-8")
    self.assertIn("<Param", payload)
    self.assertIn(">Access</Param>", payload)
    self.assertIn('type="xsd:int"', payload)
    self.assertIn(">1775092000</Value>", payload)

  async def test_get_instrument_lock_state_returns_raw_payload(self):
    await self.driver.setup()
    self.driver._rpc = AsyncMock(return_value=_RpcResult(
      method="GetInstrumentLockState",
      values={"SUCCEEDED": False, "Status": "Instrument is not locked.", "UnlockInstrument": ""},
      succeeded=False,
      status="Instrument is not locked.",
    ))

    values = await self.driver.get_instrument_lock_state(lock_id=self.driver.token)

    self.driver._rpc.assert_awaited_once_with(
      "GetInstrumentLockState",
      (("LockID", "string", self.driver.token),),
    )
    self.assertEqual(values["Status"], "Instrument is not locked.")

  async def test_do_well_transfer_uses_nested_print_options_and_parses_report(self):
    await self.driver.setup()
    self.driver._lock_held = True
    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<DoWellTransferResponse><DoWellTransfer>"
        "<SUCCEEDED>True</SUCCEEDED><Status>OK</Status>"
        "<Value>"
        "&lt;transfer date=&quot;2026-04-21&quot; serial_number=&quot;E6XX-20044&quot;&gt;"
        "&lt;plateInfo&gt;&lt;plate name=&quot;384PP_DMSO2&quot; /&gt;"
        "&lt;plate name=&quot;1536LDV_Dest&quot; /&gt;&lt;/plateInfo&gt;"
        "&lt;printmap&gt;"
        "&lt;w n=&quot;A1&quot; r=&quot;0&quot; c=&quot;0&quot; dn=&quot;B1&quot; dr=&quot;1&quot; dc=&quot;0&quot; "
        "vt=&quot;2.5&quot; avt=&quot;2.5&quot; cvl=&quot;997.5&quot; vl=&quot;1000&quot; "
        "fld=&quot;DMSO&quot; fldu=&quot;%&quot; fc=&quot;100&quot; /&gt;"
        "&lt;/printmap&gt;"
        "&lt;skippedwells&gt;"
        "&lt;w n=&quot;A2&quot; r=&quot;0&quot; c=&quot;1&quot; dn=&quot;B2&quot; dr=&quot;1&quot; dc=&quot;1&quot; "
        "vt=&quot;5&quot; reason=&quot;empty&quot; /&gt;"
        "&lt;/skippedwells&gt;"
        "&lt;/transfer&gt;"
        "</Value>"
        "</DoWellTransfer></DoWellTransferResponse>"
      )
    )

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      return_value=(fake_reader, fake_writer),
    ):
      result = await self.driver.do_well_transfer(
        "<Protocol><Name></Name></Protocol>",
        EchoTransferPrintOptions(do_plate_survey=True, plate_map=True),
      )

    payload = gzip.decompress(bytes(fake_writer.buffer).split(b"\r\n\r\n", 1)[1]).decode("utf-8")
    ET.fromstring(payload)
    self.assertIn("<DoWellTransfer", payload)
    self.assertIn("&lt;Protocol&gt;&lt;Name&gt;&lt;/Name&gt;&lt;/Protocol&gt;", payload)
    self.assertEqual(payload.count('xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'), 1)
    self.assertIn(
      '<PrintOptions SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">',
      payload,
    )
    self.assertIn(
      '<DoPlateSurvey SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
      'type="xsd:boolean">True</DoPlateSurvey>',
      payload,
    )
    self.assertIn(
      '<PlateMap SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
      'type="xsd:boolean">True</PlateMap>',
      payload,
    )
    self.assertNotIn("&lt;PrintOptions&gt;", payload)
    self.assertIsNotNone(result.report_xml)
    self.assertIn("<transfer", result.report_xml)
    self.assertEqual(result.source_plate_type, "384PP_DMSO2")
    self.assertEqual(result.destination_plate_type, "1536LDV_Dest")
    self.assertEqual(len(result.transfers), 1)
    self.assertEqual(result.transfers[0].source_identifier, "A1")
    self.assertEqual(result.transfers[0].destination_identifier, "B1")
    self.assertEqual(result.transfers[0].actual_volume_nl, 2.5)
    self.assertEqual(result.transfers[0].fluid, "DMSO")
    self.assertEqual(len(result.skipped), 1)
    self.assertEqual(result.skipped[0].reason, "empty")

  async def test_transfer_wells_updates_volume_trackers_after_successful_report(self):
    await self.driver.setup()
    self.driver._lock_held = True
    source_plate = _make_plate("source", "384PP_DMSO2")
    destination_plate = _make_plate("destination", "1536LDV_Dest")
    source_plate.get_well("A1").set_volume(1.0)
    destination_plate.get_well("B1").set_volume(0.0)
    set_volume_tracking(True)

    self.driver.get_current_source_plate_type = AsyncMock(return_value="384PP_DMSO2")
    self.driver.get_current_destination_plate_type = AsyncMock(return_value="1536LDV_Dest")
    self.driver.retrieve_parameter = AsyncMock(return_value=False)
    self.driver.set_plate_map = AsyncMock()
    self.driver.get_plate_info = AsyncMock(return_value={})
    self.driver.get_dio_ex2 = AsyncMock(return_value={})
    self.driver.get_dio = AsyncMock(return_value={})
    self.driver.do_well_transfer = AsyncMock(return_value=EchoTransferResult(
      report_xml=None,
      raw={},
      succeeded=True,
      status="OK",
      transfers=[
        EchoTransferredWell(
          source_identifier="A1",
          source_row=0,
          source_column=0,
          destination_identifier="B1",
          destination_row=1,
          destination_column=0,
          actual_volume_nl=5.0,
        )
      ],
    ))

    result = await self.driver.transfer_wells(
      source_plate,
      destination_plate,
      [("A1", "B1", 5.0)],
      do_survey=False,
      close_door_before_transfer=False,
    )

    self.assertEqual(result.status, "OK")
    self.assertAlmostEqual(source_plate.get_well("A1").tracker.get_used_volume(), 0.995)
    self.assertAlmostEqual(destination_plate.get_well("B1").tracker.get_used_volume(), 0.005)
    self.driver.set_plate_map.assert_awaited_once()
    plate_map = self.driver.set_plate_map.await_args.args[0]
    self.assertEqual(plate_map.well_identifiers, ("A1",))
    self.driver.do_well_transfer.assert_awaited_once()
    protocol_xml = self.driver.do_well_transfer.await_args.args[0]
    self.assertIn('<wp n="A1" dn="B1" v="5" />', protocol_xml)

  async def test_survey_source_plate_can_update_source_plate_volumes(self):
    await self.driver.setup()
    self.driver._lock_held = True
    source_plate = _make_plate("source", "384PP_DMSO2")
    set_volume_tracking(True)
    survey_data = EchoSurveyData.from_xml(
      '<platesurvey p="384PP_DMSO2"><w n="A1" r="0" c="0" vl="1250" cvl="1000" /></platesurvey>'
    )
    self.driver.set_plate_map = AsyncMock()
    self.driver.survey_plate = AsyncMock(return_value=survey_data)
    self.driver.get_survey_data = AsyncMock(return_value=survey_data)
    self.driver.dry_plate = AsyncMock()

    result = await self.driver.survey_source_plate(
      EchoPlateMap(plate_type="384PP_DMSO2", well_identifiers=("A1",)),
      EchoSurveyParams(plate_type="384PP_DMSO2", num_rows=1, num_cols=1),
      source_plate=source_plate,
    )

    self.assertEqual(result.saved_data, survey_data)
    self.assertEqual(source_plate.get_well("A1").tracker.get_used_volume(), 1.0)

  async def test_load_source_plate_sequences_operator_pause_and_barcode(self):
    await self.driver.setup()
    self.driver._lock_held = True
    calls: list[str] = []

    async def operator_pause(message: str):
      calls.append(message)

    self.driver.open_door = AsyncMock(side_effect=lambda *_args, **_kwargs: calls.append("door"))
    self.driver.open_source_plate = AsyncMock(
      side_effect=lambda *_args, **_kwargs: calls.append("present")
    )
    self.driver.get_power_calibration = AsyncMock()
    self.driver.get_plate_info = AsyncMock()
    self.driver.get_current_source_plate_type = AsyncMock(side_effect=["None", "384PP_DMSO2"])
    self.driver.close_source_plate = AsyncMock(return_value="BC123")
    self.driver.retrieve_parameter = AsyncMock()
    self.driver.get_dio_ex2 = AsyncMock(return_value={"SPP": -1})

    result = await self.driver.load_source_plate(
      "384PP_DMSO2",
      operator_pause=operator_pause,
      retract_timeout=45.0,
    )

    self.assertEqual(calls, ["door", "present", "source plate presented"])
    self.assertTrue(result.plate_present)
    self.assertEqual(result.barcode, "BC123")
    self.driver.close_source_plate.assert_awaited_once_with(
      plate_type="384PP_DMSO2",
      barcode_location="Right-Side",
      barcode="",
      timeout=45.0,
    )

  async def test_eject_all_plates_ejects_source_before_destination(self):
    await self.driver.setup()
    self.driver._lock_held = True
    calls: list[str] = []
    self.driver.eject_source_plate = AsyncMock(
      side_effect=lambda **_kwargs: calls.append("source")
      or EchoPlateWorkflowResult(side="source", plate_type=None, plate_present=False)
    )
    self.driver.eject_destination_plate = AsyncMock(
      side_effect=lambda **_kwargs: calls.append("destination")
      or EchoPlateWorkflowResult(side="destination", plate_type=None, plate_present=False)
    )
    self.driver.close_door = AsyncMock(side_effect=lambda *_args, **_kwargs: calls.append("door"))

    source_result, destination_result = await self.driver.eject_all_plates()

    self.assertEqual(calls, ["source", "destination", "door"])
    self.assertFalse(source_result.plate_present)
    self.assertFalse(destination_result.plate_present)


class TestEchoPlateAccessBackend(unittest.IsolatedAsyncioTestCase):
  async def test_close_door_rejects_when_access_is_open(self):
    driver = EchoDriver(host="192.168.0.25")
    backend = EchoPlateAccessBackend(driver)
    driver.get_access_state = AsyncMock(
      return_value=PlateAccessState(source_access_open=True, source_access_closed=False)
    )
    driver.close_door = AsyncMock()

    with self.assertRaises(EchoCommandError):
      await backend.close_door()

    driver.close_door.assert_not_awaited()


class TestEchoPlateMap(unittest.TestCase):
  def test_from_plate_uses_canonical_identifiers(self):
    plate = _StubPlate(["A1", "B2", "C3"])

    plate_map = EchoPlateMap.from_plate(
      plate,
      plate_type="384PP_DMSO2",
      wells=["B2", "A1"],
    )

    self.assertEqual(plate_map.plate_type, "384PP_DMSO2")
    self.assertEqual(plate_map.well_identifiers, ("B2", "A1"))


class TestEchoDevice(unittest.IsolatedAsyncioTestCase):
  async def test_device_delegates_to_driver_and_capability(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    echo.driver.get_instrument_info = AsyncMock()
    opened_state = PlateAccessState(source_access_open=True, source_access_closed=False)
    echo.plate_access.open_source_plate = AsyncMock(return_value=opened_state)
    echo.plate_access.get_access_state = AsyncMock(return_value=PlateAccessState())

    await echo.get_instrument_info()
    returned_opened_state = await echo.open_source_plate(timeout=1.0)
    state = await echo.get_access_state()

    echo.driver.get_instrument_info.assert_awaited_once()
    echo.plate_access.open_source_plate.assert_awaited_once_with(
      timeout=1.0,
      poll_interval=0.1,
    )
    self.assertIsInstance(state, PlateAccessState)
    self.assertTrue(returned_opened_state.source_access_open)

  async def test_device_delegates_low_level_methods_to_driver(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    echo.driver.get_fluid_info = AsyncMock(return_value=EchoFluidInfo(
      name="DMSO",
      description="",
      fc_min=None,
      fc_max=None,
      fc_units="",
    ))
    echo.driver.get_echo_configuration = AsyncMock(return_value="<Configuration />")
    echo.driver.get_all_protocol_names = AsyncMock(return_value=["baseline"])
    echo.driver.open_door = AsyncMock()
    echo.driver.home_axes = AsyncMock()
    echo.driver.actuate_ionizer = AsyncMock()

    fluid = await echo.get_fluid_info("DMSO")
    config = await echo.get_echo_configuration()
    protocols = await echo.get_all_protocol_names()
    await echo.open_door(timeout=1.0)
    await echo.home_axes(timeout=2.0)
    await echo.actuate_ionizer(True, timeout=3.0)

    self.assertEqual(fluid.name, "DMSO")
    self.assertEqual(config, "<Configuration />")
    self.assertEqual(protocols, ["baseline"])
    echo.driver.get_fluid_info.assert_awaited_once_with("DMSO")
    echo.driver.get_echo_configuration.assert_awaited_once()
    echo.driver.get_all_protocol_names.assert_awaited_once()
    echo.driver.open_door.assert_awaited_once_with(timeout=1.0)
    echo.driver.home_axes.assert_awaited_once_with(timeout=2.0)
    echo.driver.actuate_ionizer.assert_awaited_once_with(enabled=True, timeout=3.0)

  async def test_device_survey_helper_delegates_to_driver(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    plate_map = EchoPlateMap(plate_type="384PP_DMSO2", well_identifiers=("A1",))
    survey = EchoSurveyParams(plate_type="384PP_DMSO2", num_rows=16, num_cols=24)
    response_data = EchoSurveyData.from_xml(
      '<platesurvey p="384PP_DMSO2"><Well n="A1" r="0" c="0" /></platesurvey>'
    )
    saved_data = EchoSurveyData.from_xml(
      '<platesurvey p="384PP_DMSO2"><Well n="A1" r="0" c="0" comp="1.83" /></platesurvey>'
    )
    expected = EchoSurveyRunResult(
      response_data=response_data,
      saved_data=saved_data,
      dry_mode=EchoDryPlateMode.TWO_PASS,
    )
    echo.driver.survey_source_plate = AsyncMock(return_value=expected)

    result = await echo.survey_source_plate(
      plate_map,
      survey,
      fetch_saved_data=True,
      dry_after=True,
    )

    self.assertEqual(result, expected)
    echo.driver.survey_source_plate.assert_awaited_once_with(
      plate_map,
      survey,
      fetch_saved_data=True,
      dry_after=True,
      dry=None,
      source_plate=None,
      update_volume_trackers=True,
    )

  async def test_device_transfer_and_plate_workflows_delegate_to_driver(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    source_plate = _make_plate("source", "384PP_DMSO2")
    destination_plate = _make_plate("destination", "1536LDV_Dest")
    transfer_result = EchoTransferResult(report_xml=None, raw={}, status="OK")
    load_result = EchoPlateWorkflowResult(
      side="source",
      plate_type="384PP_DMSO2",
      plate_present=True,
    )
    echo.driver.transfer_wells = AsyncMock(return_value=transfer_result)
    echo.driver.load_source_plate = AsyncMock(return_value=load_result)

    returned_transfer = await echo.transfer_wells(
      source_plate,
      destination_plate,
      [("A1", "B1", 2.5)],
      do_survey=False,
    )
    returned_load = await echo.load_source_plate("384PP_DMSO2")

    self.assertEqual(returned_transfer, transfer_result)
    self.assertEqual(returned_load, load_result)
    echo.driver.transfer_wells.assert_awaited_once()
    echo.driver.load_source_plate.assert_awaited_once_with(
      "384PP_DMSO2",
      barcode_location="Right-Side",
      barcode="",
      operator_pause=None,
      open_door_first=True,
      present_timeout=None,
      retract_timeout=None,
    )

  async def test_stop_unlocks_held_lock(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    echo.driver._lock_held = True
    echo.driver.unlock = AsyncMock()

    await echo.stop()

    echo.driver.unlock.assert_awaited_once()


if __name__ == "__main__":
  unittest.main()
