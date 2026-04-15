import asyncio
import gzip
import html
import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.capabilities.plate_access import PlateAccessState
from pylabrobot.labcyte.echo import (
  DEFAULT_DRY_TIMEOUT,
  DEFAULT_LOADED_RETRACT_TIMEOUT,
  DEFAULT_SURVEY_TIMEOUT,
  Echo,
  EchoCommandError,
  EchoDriver,
  EchoDryPlateMode,
  EchoDryPlateParams,
  EchoEvent,
  EchoPlateAccessBackend,
  EchoPlateMap,
  EchoSurveyData,
  EchoSurveyParams,
  EchoSurveyRunResult,
  _RpcResult,
  EchoTransferPrintOptions,
)


def _soap_response(inner_xml: str, *, content_length_override: int | None = None) -> bytes:
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
  headers = (
    "HTTP/1.1 200 OK\r\n"
    "Server: Echo® Liquid Handler-3.1.1\r\n"
    "Protocol: 3.1\r\n"
    'Content-Type: text/xml; charset="utf-8"\r\n'
    f"Content-Length: {content_length}\r\n"
    "\r\n"
  ).encode("iso-8859-1")
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
        "<Value>&lt;transfer job=&quot;1&quot;&gt;&lt;summary total=&quot;1&quot; /&gt;&lt;/transfer&gt;</Value>"
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
    self.assertIn("<DoWellTransfer", payload)
    self.assertIn("&lt;Protocol&gt;&lt;Name&gt;&lt;/Name&gt;&lt;/Protocol&gt;", payload)
    self.assertIn("<PrintOptions><DoPlateSurvey>True</DoPlateSurvey>", payload)
    self.assertIn("<PlateMap>True</PlateMap>", payload)
    self.assertNotIn("&lt;PrintOptions&gt;", payload)
    self.assertIsNotNone(result.report_xml)
    self.assertIn("<transfer", result.report_xml)


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

  async def test_device_survey_helper_sequences_calls(self):
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
    calls: list[str] = []

    echo.set_plate_map = AsyncMock(side_effect=lambda *_args, **_kwargs: calls.append("map"))
    echo.survey_plate = AsyncMock(
      side_effect=lambda *_args, **_kwargs: calls.append("survey") or response_data
    )
    echo.get_survey_data = AsyncMock(
      side_effect=lambda *_args, **_kwargs: calls.append("get") or saved_data
    )
    echo.dry_plate = AsyncMock(side_effect=lambda *_args, **_kwargs: calls.append("dry"))

    result = await echo.survey_source_plate(
      plate_map,
      survey,
      fetch_saved_data=True,
      dry_after=True,
    )

    self.assertEqual(calls, ["map", "survey", "get", "dry"])
    self.assertEqual(
      result,
      EchoSurveyRunResult(
        response_data=response_data,
        saved_data=saved_data,
        dry_mode=EchoDryPlateMode.TWO_PASS,
      ),
    )

  async def test_device_survey_helper_skips_saved_fetch_when_not_saved(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    plate_map = EchoPlateMap(plate_type="384PP_DMSO2", well_identifiers=("A1",))
    survey = EchoSurveyParams(
      plate_type="384PP_DMSO2",
      num_rows=16,
      num_cols=24,
      save=False,
    )
    response_data = EchoSurveyData.from_xml(
      '<platesurvey p="384PP_DMSO2"><Well n="A1" r="0" c="0" /></platesurvey>'
    )

    echo.set_plate_map = AsyncMock()
    echo.survey_plate = AsyncMock(return_value=response_data)
    echo.get_survey_data = AsyncMock()
    echo.dry_plate = AsyncMock()

    result = await echo.survey_source_plate(plate_map, survey, fetch_saved_data=True)

    echo.get_survey_data.assert_not_awaited()
    echo.dry_plate.assert_not_awaited()
    self.assertEqual(result.response_data, response_data)
    self.assertIsNone(result.saved_data)
    self.assertIsNone(result.dry_mode)

  async def test_stop_unlocks_held_lock(self):
    echo = Echo(host="192.168.0.25")
    echo._setup_finished = True
    echo.driver._lock_held = True
    echo.driver.unlock = AsyncMock()

    await echo.stop()

    echo.driver.unlock.assert_awaited_once()


if __name__ == "__main__":
  unittest.main()
