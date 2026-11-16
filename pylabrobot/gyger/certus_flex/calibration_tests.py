"""Vendor calibration units, identity validation, and atomic configuration loading."""

import json
import tempfile
from pathlib import Path

import pytest

from pylabrobot.testing.concurrency import AnyioTestBase

from .calibration import ValveCalibration
from .device import CertusFlex
from .geometry_tests import registration
from .plans import DispenseStep
from .testing_utils import FakeInco

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


class TestCalibrationImport:
  """Import exact native field names with explicit quantity-unit conversions."""

  def test_units_disabled_points_and_round_trip(self) -> None:
    calibration = ValveCalibration.from_vendor_xml(VENDOR_XML)
    assert calibration.points == ((1, 0.001), (2, 0.002))
    assert calibration.pressure == 0.2
    assert calibration.valve_serial == "TEST-01"
    calibration.validate_for(fluid="water", valve_serial="test-01")
    assert 'enable="false"' in calibration.to_vendor_xml()
    assert ValveCalibration.from_vendor_xml(calibration.to_vendor_xml()) == calibration
    assert ValveCalibration.from_dict(calibration.to_dict()) == calibration

  def test_ambiguous_missing_and_mismatched_metadata(self) -> None:
    calibration = ValveCalibration.from_vendor_xml(VENDOR_XML)
    for fluid, serial in (("DMSO", "TEST-01"), ("Water", "TEST-02")):
      with pytest.raises(ValueError):
        calibration.validate_for(fluid=fluid, valve_serial=serial)
    for xml in (
      VENDOR_XML.replace("200 mbar", "200"),
      VENDOR_XML.replace("<calibrations>", "<!DOCTYPE a><calibrations>"),
      VENDOR_XML.replace("</calibrations>", calibration.to_vendor_xml() + "</calibrations>"),
    ):
      with pytest.raises(ValueError):
        ValveCalibration.from_vendor_xml(xml)


class TestConfigurationRoundTrip(AnyioTestBase):
  """Reusable configuration binds to the controller and is applied only after all checks."""

  async def test_configuration_round_trip_and_factor_inheritance(self) -> None:
    device = CertusFlex("example.invalid", head="flex8_angled")
    inco = FakeInco(strings=["C196069", "1.0.7.378-trunk"], default_number=4.0)
    device._inco = inco  # type: ignore[assignment]
    calibration = ValveCalibration.from_vendor_xml(VENDOR_XML)
    async with device:
      await device.configure_channel(
        1, calibration=calibration, fluid="Water", valve_serial="TEST-01", correction_factor=1.25
      )
      reg = registration()
      plan = device.plan_dispense(reg, [DispenseStep(reg.plate.get_item("B2"), 1, volume=1)])
      assert plan.groups[0][0].duration == pytest.approx(0.00125)
      with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "configuration.json"
        device.save_configuration(path)
        original = path.read_text()
        device.valves[1].calibration = None
        await device.load_configuration(path)
        assert device.valves[1].calibration == calibration
        assert device.valves[1].correction_factor == 1.25
        broken = json.loads(original)
        broken["channels"].append({**broken["channels"][0], "channel": 2, "fluid": "wrong"})
        path.write_text(json.dumps(broken))
        with pytest.raises(ValueError):
          await device.load_configuration(path)
        assert device.valves[2].calibration is None
        assert device.valves[1].calibration == calibration
        broken = json.loads(original)
        broken["serial_number"] = "another-machine"
        path.write_text(json.dumps(broken))
        with pytest.raises(ValueError):
          await device.load_configuration(path)
    assert inco.proc_calls == []
