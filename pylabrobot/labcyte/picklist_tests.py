# mypy: disable-error-code="arg-type"
"""Tests for the SDK-free picklist parsing and protocol generation."""

import unittest

from pylabrobot.labcyte.echo525 import Echo525
from pylabrobot.labcyte.echo_mock import EchoMockServer
from pylabrobot.labcyte.picklist import (
  NaiveEchoProtocolGenerator,
  Transfer,
  picklist_from_rows,
)


class TestPicklistParsing(unittest.TestCase):
  def test_reads_standard_echo_cherry_pick_columns(self):
    rows = [
      {"Source Plate Name": "6res", "Source Well": "A2", "Destination Plate Name": "dst",
       "Destination Well": "A1", "Volume": "150", "Source Plate Type": "6RES_AQ_BP2"},
    ]
    [t] = picklist_from_rows(rows)
    self.assertEqual((t.source_well, t.dest_well, t.volume_nl), ("A2", "A1", 150.0))
    self.assertEqual(t.source_plate_type, "6RES_AQ_BP2")

  def test_reads_barcode_header_variant_with_offsets(self):
    rows = [
      {"Source Plate Barcode": "P1", "Source Well": "D5", "Destination Plate Barcode": "D1",
       "Destination Well": "A1", "Transfer Volume": "25", "DestXOffset": "100",
       "DestYOffset": "-50"},
    ]
    [t] = picklist_from_rows(rows)
    self.assertEqual(t.source_plate_name, "P1")
    self.assertEqual((t.dest_x_offset_um, t.dest_y_offset_um), (100, -50))


class TestNaiveGenerator(unittest.TestCase):
  def test_groups_by_source_type_and_keeps_picklist_order(self):
    transfers = [
      Transfer("A2", "A1", 150, "6RES_AQ_BP2"),
      Transfer("B1", "A1", 800, "6RES_AQ_GPSB2"),
      Transfer("A2", "A2", 150, "6RES_AQ_BP2"),
    ]
    plan = NaiveEchoProtocolGenerator().generate(transfers)
    # one DoWellTransfer per source plate type
    self.assertEqual([g.source_plate_type for g in plan], ["6RES_AQ_BP2", "6RES_AQ_GPSB2"])
    bp2 = plan[0].protocol_xml
    self.assertIn("<SourcePlateName>6RES_AQ_BP2</SourcePlateName>", bp2)
    # picklist order preserved (A1 then A2) - no reordering
    self.assertLess(bp2.index('dn="A1"'), bp2.index('dn="A2"'))
    self.assertIn('<wp oid="1" v="150" n="A2" r="0" c="1" dn="A1" dr="0" dc="0"', bp2)

  def test_passes_through_xy_offsets(self):
    plan = NaiveEchoProtocolGenerator().generate(
      [Transfer("A1", "B2", 25, "T", dest_x_offset_um=100, dest_y_offset_um=-50)]
    )
    self.assertIn('dx="100" dy="-50"', plan[0].protocol_xml)


class TestRunPicklistAgainstMock(unittest.IsolatedAsyncioTestCase):
  async def test_run_picklist_drives_one_transfer_per_source_type(self):
    transfers = [
      Transfer("A2", "A1", 150, "6RES_AQ_BP2"),
      Transfer("B1", "A1", 800, "6RES_AQ_GPSB2"),
    ]
    async with EchoMockServer() as srv:
      echo = Echo525(host=srv.host, rpc_port=srv.port, timeout=5.0)
      await echo.setup()
      results = await echo.run_picklist(transfers, survey=False)
    self.assertEqual(len(results), 2)
    self.assertTrue(all(r.succeeded for r in results))
    methods = [m for m, _ in srv.received]
    self.assertEqual(methods[0], "LockInstrument")
    self.assertEqual(methods.count("DoWellTransfer"), 2)
    self.assertEqual(methods[-1], "UnlockInstrument")


if __name__ == "__main__":
  unittest.main()
