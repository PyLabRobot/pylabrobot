"""Vendor calibration units, identity validation, and atomic configuration loading."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ._inco_udp import IncoUDP
from .calibration import ValveCalibration
from .device import CertusFlex
from .geometry_tests import registration
from .plans import DispenseStep

VENDOR_XML = """<calibrations><calibration-map id="example" ambient-temperature="20 C">
<metadata comment="synthetic algorithm fixture, not a measured valve" />
<fluid id="water-id" name="Water" density="1 g/ml" />
<valve valve-type="example-type" valve-serial-number="TEST-01" />
<fluid-supply type="Syringe" dispense-pressure="200 mbar" />
<calibration-points>
<calibration-point volume="2000 [nl]" valve-open-duration="2 [ms]" used-shots="100" />
<calibration-point volume="1 µl" valve-open-duration="1000 µs" />
<calibration-point volume="1.5 ul" valve-open-duration="0.001 s" enable="false" />
</calibration-points></calibration-map></calibrations>"""


class CalibrationImportTests(unittest.TestCase):
  """Import exact native field names with explicit quantity-unit conversions."""

  def test_units_disabled_points_and_round_trip(self) -> None:
    calibration = ValveCalibration.from_vendor_xml(VENDOR_XML)
    self.assertEqual(calibration.points, ((1, 0.001), (2, 0.002)))
    self.assertEqual(calibration.pressure, 0.2)
    self.assertEqual(calibration.valve_serial, "TEST-01")
    calibration.validate_for(fluid="water", valve_serial="test-01")
    self.assertIn('enable="false"', calibration.to_vendor_xml())
    self.assertEqual(ValveCalibration.from_vendor_xml(calibration.to_vendor_xml()), calibration)
    self.assertEqual(ValveCalibration.from_dict(calibration.to_dict()), calibration)

  def test_ambiguous_missing_and_mismatched_metadata(self) -> None:
    calibration = ValveCalibration.from_vendor_xml(VENDOR_XML)
    for fluid, serial in (("DMSO", "TEST-01"), ("Water", "TEST-02")):
      with self.assertRaises(ValueError):
        calibration.validate_for(fluid=fluid, valve_serial=serial)
    for xml in (
      VENDOR_XML.replace("200 mbar", "200"),
      VENDOR_XML.replace("<calibrations>", "<!DOCTYPE a><calibrations>"),
      VENDOR_XML.replace("</calibrations>", calibration.to_vendor_xml() + "</calibrations>"),
    ):
      with self.assertRaises(ValueError):
        ValveCalibration.from_vendor_xml(xml)


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):
  """Reusable configuration binds to the controller and is applied only after all checks."""

  async def test_configuration_round_trip_and_factor_inheritance(self) -> None:
    device = CertusFlex("example.invalid", head="flex8_angled")
    device.serial_number = "C196069"
    device._inco = MagicMock(spec=IncoUDP)
    device._inco.get_number.return_value = 4.0
    calibration = ValveCalibration.from_vendor_xml(VENDOR_XML)
    await device.configure_channel(
      1, calibration=calibration, fluid="Water", valve_serial="TEST-01", correction_factor=1.25
    )
    reg = registration()
    plan = device.plan_dispense(reg, [DispenseStep(reg.plate.get_item("B2"), 1, volume=1)])
    self.assertAlmostEqual(plan.groups[0][0].duration, 0.00125)
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "configuration.json"
      device.save_configuration(path)
      original = path.read_text()
      device.valves[1].calibration = None
      await device.load_configuration(path)
      self.assertEqual(device.valves[1].calibration, calibration)
      self.assertEqual(device.valves[1].correction_factor, 1.25)
      broken = json.loads(original)
      broken["channels"].append({**broken["channels"][0], "channel": 2, "fluid": "wrong"})
      path.write_text(json.dumps(broken))
      with self.assertRaises(ValueError):
        await device.load_configuration(path)
      self.assertIsNone(device.valves[2].calibration)
      self.assertEqual(device.valves[1].calibration, calibration)
      broken = json.loads(original)
      broken["serial_number"] = "another-machine"
      path.write_text(json.dumps(broken))
      with self.assertRaises(ValueError):
        await device.load_configuration(path)
    device._inco.call.assert_not_awaited()
