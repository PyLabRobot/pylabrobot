# mypy: disable-error-code="arg-type,func-returns-value,method-assign,union-attr"
"""Tests for the Labcyte Echo 525 backend.

The golden values below are taken from a real Wireshark capture of an Echo 525
(``Model`` = ``Echo 525``, software ``2.7.3``) executing a HiFi PCR protocol, so these
tests pin the backend to traffic the physical instrument actually produced and accepted.
"""

import gzip
import unittest
from unittest.mock import patch

from pylabrobot.labcyte.echo import (
  Echo,
  EchoDriver,
  EchoDryPlateMode,
  EchoPlateMap,
  EchoSurveyParams,
  MedmanEchoDriver,
  build_echo_transfer_plan,
)
from pylabrobot.labcyte.echo_mock import EchoMockServer
from pylabrobot.labcyte.echo_tests import (
  _FakeReader,
  _FakeWriter,
  _make_plate,
  _soap_response,
)


class TestEcho525Defaults(unittest.IsolatedAsyncioTestCase):
  """Pin the device-specific defaults observed on the wire."""

  def test_driver_uses_captured_525_defaults(self):
    driver = MedmanEchoDriver(model="Echo 525", host="192.168.0.25")
    # GetTransferVolIncrNl / GetTransferVolMinimumNl both returned 25 on the device.
    self.assertEqual(driver.transfer_volume_increment_nl, 25.0)
    # Versions advertised in the instrument's Medman HTTP headers.
    self.assertEqual(driver.protocol_version, "2.6")
    self.assertEqual(driver.client_version, "2.7.3")

  def test_device_wires_up_525_driver(self):
    echo = Echo(model="Echo 525", host="192.168.0.25")
    self.assertEqual(echo.driver.model, "Echo 525")
    self.assertEqual(echo.driver.transfer_volume_increment_nl, 25.0)
    self.assertEqual(echo.deck.model, "Echo 525")

  def test_token_matches_captured_host_header_shape(self):
    # Captured Host header: 192.168.0.25:47500:33224:1780941641:10347
    token = EchoDriver.build_token(
      "192.168.0.25", slot_a=47500, slot_b=33224, epoch=1780941641, pid=10347
    )
    self.assertEqual(token, "192.168.0.25:47500:33224:1780941641:10347")
    fields = token.split(":")
    self.assertEqual(len(fields), 5)
    self.assertEqual(fields[0], "192.168.0.25")
    self.assertTrue(all(f.isdigit() for f in fields[1:]))


class TestEcho525VolumeGranularity(unittest.IsolatedAsyncioTestCase):
  """The 525 dispenses in 25 nL increments; the 650 does 2.5 nL."""

  def setUp(self):
    self.driver = MedmanEchoDriver(model="Echo 525", host="192.168.0.25")
    self.source = _make_plate("source", "6RES_AQ_BP2")
    self.destination = _make_plate("destination", "384PP_AQ_BP2")

  def test_multiple_of_25nl_is_accepted(self):
    plan = self.driver.build_transfer_plan(self.source, self.destination, [("A1", "B1", 150)])
    self.assertIn('<wp n="A1" dn="B1" v="150" />', plan.protocol_xml)

  def test_non_multiple_of_25nl_is_rejected(self):
    # 10 nL is a legal 650 volume (multiple of 2.5) but illegal on the 525.
    with self.assertRaises(ValueError) as ctx:
      self.driver.build_transfer_plan(self.source, self.destination, [("A1", "B1", 10)])
    self.assertIn("multiple of 25", str(ctx.exception))

  def test_25nl_minimum_droplet_is_accepted(self):
    plan = self.driver.build_transfer_plan(self.source, self.destination, [("A1", "B1", 25)])
    self.assertIn('v="25"', plan.protocol_xml)

  def test_650_increment_still_rejected_on_525(self):
    # 2.5 nL is the smallest 650 droplet and must NOT be allowed on a 525.
    with self.assertRaises(ValueError):
      self.driver.build_transfer_plan(self.source, self.destination, [("A1", "B1", 2.5)])


class TestEcho525WireFormat(unittest.IsolatedAsyncioTestCase):
  """Confirm the bytes we put on the wire match what the Echo 525 expects."""

  async def test_rpc_request_matches_captured_medman_framing(self):
    driver = MedmanEchoDriver(model="Echo 525", host="192.168.0.25", timeout=1.0)
    await driver.setup()

    fake_writer = _FakeWriter()
    fake_reader = _FakeReader(
      _soap_response(
        "<GetInstrumentInfoResponse><GetInstrumentInfo>"
        '<SUCCEEDED type="xsd:boolean">True</SUCCEEDED>'
        '<Status type="xsd:string">OK</Status>'
        '<Model type="xsd:string">Echo 525</Model>'
        '<SerialNumber type="xsd:string">E5XX-00000</SerialNumber>'
        "</GetInstrumentInfo></GetInstrumentInfoResponse>"
      )
    )

    async def fake_open_connection(host: str, port: int):
      self.assertEqual(host, "192.168.0.25")
      self.assertEqual(port, 8000)
      return fake_reader, fake_writer

    with patch(
      "pylabrobot.labcyte.echo.asyncio.open_connection",
      side_effect=fake_open_connection,
    ):
      info = await driver.get_instrument_info()

    request = bytes(fake_writer.buffer)
    head, _, body = request.partition(b"\r\n\r\n")
    head_text = head.decode("iso-8859-1")

    # Request line + Medman framing exactly as the real Echo client used.
    self.assertTrue(head_text.startswith("POST /Medman HTTP/1.1"))
    self.assertIn("Protocol: 2.6", head_text)
    self.assertIn("Client: 2.7.3", head_text)
    self.assertIn('SOAPAction: "Some-URI"', head_text)
    self.assertIn("Host: 192.168.0.25:", head_text)  # token-shaped host header

    # Body is gzip-compressed SOAP (the 525 only accepts gzipped Medman bodies).
    decompressed = gzip.decompress(body).decode("utf-8")
    self.assertIn("<GetInstrumentInfo", decompressed)
    self.assertIn("SOAP-ENV:Envelope", decompressed)

    # And the response parsed back into the instrument identity we faked.
    self.assertEqual(info.model, "Echo 525")
    self.assertEqual(info.serial_number, "E5XX-00000")

  def test_reproduces_captured_dowelltransfer_layout(self):
    # The capture's DoWellTransfer dispensed 150 nL from source A2 into every well of a
    # 384-well destination. Reproduce the same source/volume against the 525 builder and
    # confirm the <wp> layout the 525 would emit.
    driver = MedmanEchoDriver(model="Echo 525", host="192.168.0.25")
    source = _make_plate("source", "6RES_AQ_BP2")
    destination = _make_plate("destination", "384PP_AQ_BP2")
    transfers = [("A2", dn, 150) for dn in ("A1", "B1", "A3")]

    plan = driver.build_transfer_plan(source, destination, transfers, protocol_name="hifi_pcr")

    self.assertIn('<Protocol Name="hifi_pcr">', plan.protocol_xml)
    self.assertIn('<wp n="A2" dn="A1" v="150" />', plan.protocol_xml)
    self.assertIn('<wp n="A2" dn="A3" v="150" />', plan.protocol_xml)
    # All transfers draw from a single source well -> sparse, de-duplicated plate map.
    self.assertEqual(plan.plate_map.well_identifiers, ("A2",))


class TestEcho650RegressionGuard(unittest.IsolatedAsyncioTestCase):
  """The 525 changes must not alter Echo 650 (2.5 nL) behaviour."""

  def test_650_default_still_accepts_2_5nl(self):
    source = _make_plate("source", "384PP_DMSO2")
    destination = _make_plate("destination", "1536LDV_Dest")
    # No volume_increment_nl override -> 650 default of 2.5 nL.
    plan = build_echo_transfer_plan(source, destination, [("A1", "B1", 2.5)])
    self.assertIn('<wp n="A1" dn="B1" v="2.5" />', plan.protocol_xml)


class TestEcho525AgainstMockServer(unittest.IsolatedAsyncioTestCase):
  """End-to-end runs against EchoMockServer, which replays real captured 525 responses."""

  async def test_get_instrument_info_returns_captured_525_identity(self):
    async with EchoMockServer() as srv:
      echo = Echo(model="Echo 525", host=srv.host, rpc_port=srv.port, timeout=5.0)
      await echo.setup()
      info = await echo.get_instrument_info()
    self.assertEqual(info.model, "Echo 525")
    self.assertEqual(info.serial_number, "E5XX-00000")
    self.assertEqual(info.software_version, "2.7.3")

  async def test_device_reports_25nl_increment_over_the_wire(self):
    async with EchoMockServer() as srv:
      echo = Echo(model="Echo 525", host=srv.host, rpc_port=srv.port, timeout=5.0)
      await echo.setup()
      increment = await echo.driver.get_transfer_volume_increment_nl("6RES_AQ_BP2")
    self.assertEqual(increment, 25)

  async def test_full_lock_transfer_unlock_flow(self):
    async with EchoMockServer() as srv:
      echo = Echo(model="Echo 525", host=srv.host, rpc_port=srv.port, timeout=5.0)
      await echo.setup()
      await echo.driver.lock()
      xml = (
        '<?xml version="1.0"?><Protocol Name="hifi_pcr"><Name/>'
        '<Layout><wp n="A2" dn="A1" v="150"/></Layout></Protocol>'
      )
      result = await echo.driver.do_well_transfer(xml)
      await echo.driver.unlock()
    self.assertTrue(result.succeeded)
    self.assertEqual(result.status, "OK")
    self.assertGreater(len(result.transfers), 0)  # replayed (trimmed) print-map report
    self.assertEqual(
      [m for m, _ in srv.received],
      ["LockInstrument", "DoWellTransfer", "UnlockInstrument"],
    )

  async def test_expanded_survey_dry_and_transfer_path(self):
    # Exercises the full "expanded survey/transfer path" against responses captured from a real
    # 525: SetPlateMap -> PlateSurvey -> GetSurveyData -> DryPlate -> DoWellTransfer. This is the
    # hardware-free validation of that path (the device's PlateSurvey only acks; survey results
    # come back via GetSurveyData, exactly as the mock replays).
    async with EchoMockServer() as srv:
      echo = Echo(model="Echo 525", host=srv.host, rpc_port=srv.port, timeout=5.0)
      await echo.setup()
      await echo.driver.lock()
      run = await echo.driver.survey_source_plate(
        EchoPlateMap(plate_type="6RES_AQ_BP2", well_identifiers=("A2",)),
        EchoSurveyParams(
          plate_type="6RES_AQ_BP2", start_row=0, start_col=0, num_rows=1, num_cols=1, save=True
        ),
        dry_after=True,
      )
      result = await echo.driver.do_well_transfer(
        '<?xml version="1.0"?><Protocol Name="hifi_pcr"><Name/>'
        '<Layout><wp n="A2" dn="A1" v="150"/></Layout></Protocol>'
      )
      await echo.driver.unlock()
    self.assertIsNotNone(run.saved_data)
    self.assertGreater(len(run.saved_data.wells), 0)  # real survey well from the capture
    self.assertEqual(run.dry_mode, EchoDryPlateMode.TWO_PASS)
    self.assertTrue(result.succeeded)
    self.assertEqual(
      [m for m, _ in srv.received],
      [
        "LockInstrument",
        "SetPlateMap",
        "PlateSurvey",
        "GetSurveyData",
        "DryPlate",
        "DoWellTransfer",
        "UnlockInstrument",
      ],
    )

  async def test_mock_gates_motion_commands_on_the_lock(self):
    # A real Echo rejects motion/transfer unless the caller holds the lock; the mock models it.
    async with EchoMockServer() as srv:
      before = srv.response_for("DoWellTransfer")
      srv._locked = True
      after = srv.response_for("DoWellTransfer")
    self.assertIn("does not own the lock", before)
    self.assertNotIn("does not own the lock", after)


class TestEchoDriverArchitecture(unittest.IsolatedAsyncioTestCase):
  """EchoDriver is an ABC; Medman + Chatterbox are injectable sibling implementations."""

  def test_echodriver_is_abstract(self):
    from pylabrobot.labcyte.echo import EchoChatterboxDriver

    with self.assertRaises(TypeError):
      EchoDriver(host="x")  # cannot instantiate the ABC directly
    self.assertTrue(issubclass(MedmanEchoDriver, EchoDriver))
    self.assertTrue(issubclass(EchoChatterboxDriver, EchoDriver))

  async def test_inject_chatterbox_driver_runs_without_io(self):
    from pylabrobot.labcyte.echo import EchoChatterboxDriver

    echo = Echo(driver=EchoChatterboxDriver())  # no host needed; logs instead of I/O
    await echo.setup()
    await echo.driver.lock()
    result = await echo.driver.do_well_transfer(
      '<?xml version="1.0"?><Protocol Name="t"><Name/>'
      '<Layout><wp n="A1" dn="A1" v="25"/></Layout></Protocol>'
    )
    await echo.driver.unlock()
    self.assertTrue(result.succeeded)


if __name__ == "__main__":
  unittest.main()
