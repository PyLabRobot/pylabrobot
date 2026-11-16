"""Native XML fixtures and compiler regressions.

firmware_manual fixtures use examples from TD_SILA_FWCommandSet v1.06, sections 6.1
and 9.1.5: https://github.com/user-attachments/files/26010799/TD_SILA_FWCommandSet.25210.pdf
The other fixtures exercise flat/nested loops and independent overshoot ramp values.
"""

import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import Any

from pylabrobot.inheco.odtc import (
  ODTCPID,
  ODTCMethodSet,
  ODTCOvershoot,
  ODTCProtocol,
  ODTCStage,
  ODTCStep,
  compile_protocol,
  method_set_to_xml,
  parse_method_set,
)
from pylabrobot.inheco.odtc.protocol import (
  ODTCVariant,
  calculate_overshoot,
  volume_to_fluid_quantity,
)
from pylabrobot.inheco.odtc.xml import parse_progress, parse_sensor_values

FIXTURES = Path(__file__).with_name("fixtures")


class ProtocolTests(unittest.TestCase):
  def test_native_roundtrip_retains_every_field(self):
    for name in ("flat_loop_xml", "nested_loop_xml", "overshoot_xml", "firmware_manual_method_set"):
      with self.subTest(name=name):
        original = parse_method_set((FIXTURES / f"{name}.xml").read_text())
        self.assertEqual(parse_method_set(method_set_to_xml(original)), original)

  def test_manual_method_and_loop(self):
    methods = parse_method_set((FIXTURES / "firmware_manual_method_set.xml").read_text())
    self.assertEqual(len(methods.methods[0].steps), 5)
    self.assertEqual(methods.methods[0].steps[3].goto, 2)
    self.assertEqual(methods.methods[0].steps[3].loops, 5)
    self.assertEqual(methods.premethods[0].temperature, 20)

  def test_compile_cycles_and_variant(self):
    variants: tuple[tuple[ODTCVariant, float], ...] = ((96, 4.4), (384, 5.0))
    for variant, rate in variants:
      with self.subTest(variant=variant):
        profile = ODTCProtocol(
          [ODTCStage([ODTCStep(95, 30), ODTCStep(55, 20), ODTCStep(72, 60)], 30)]
        )
        method = compile_protocol(profile, variant=variant, volume=29.5, lid_temperature=105)
        self.assertEqual(method.start_temperature, 95)
        self.assertEqual(method.start_lid_temperature, 105)
        self.assertEqual(method.fluid_quantity, 0)
        self.assertEqual(method.steps[2].ramp_rate, rate)
        self.assertEqual(method.steps[2].goto, 1)
        self.assertEqual(method.steps[2].loops, 29)
        self.assertEqual(method.steps[1].ramp_rate, 2.2)
        self.assertEqual(
          parse_method_set(method_set_to_xml(ODTCMethodSet([method]))).methods[0], method
        )

  def test_nested_loop_order(self):
    profile = ODTCProtocol(
      [
        ODTCStage(
          [ODTCStep(95, 10), ODTCStage([ODTCStep(55, 10), ODTCStep(72, 10)], 5), ODTCStep(50, 20)],
          30,
        )
      ]
    )
    method = compile_protocol(profile, variant=96, volume=50, lid_temperature=110)
    self.assertEqual(
      [(s.temperature, s.goto, s.loops) for s in method.steps],
      [(95, 0, 0), (55, 0, 0), (72, 2, 4), (50, 1, 29)],
    )

  def test_coincident_loop_endpoints_preserve_repetitions(self):
    profile = ODTCProtocol([ODTCStage([ODTCStage([ODTCStep(95, 1), ODTCStep(55, 1)], 3)], 2)])
    method = compile_protocol(profile, variant=96, volume=50, lid_temperature=110)
    self.assertEqual(
      [(s.number, s.goto, s.loops) for s in method.steps],
      [(1, 0, 0), (2, 1, 2), (3, 0, 0), (4, 3, 2)],
    )

  def test_explicit_overshoot_lid_and_pid_survive_compilation(self):
    overshoot = ODTCOvershoot(3.5, duration=2, approach_rate=1, return_rate=0.5)
    profile = ODTCProtocol(
      [ODTCStage([ODTCStep(95, 30, lid_temperature=100, overshoot=overshoot, pid_number=2)])]
    )
    method = compile_protocol(
      profile, variant=96, volume=25, lid_temperature=105, pid_set=[ODTCPID(number=2)]
    )
    self.assertEqual(method.steps[0].overshoot, overshoot)
    self.assertEqual(method.start_lid_temperature, 100)
    self.assertEqual(
      parse_method_set(method_set_to_xml(ODTCMethodSet([method]))).methods[0], method
    )

  def test_invalid_profiles_fail_without_io(self):
    for step in (
      ODTCStep(float("nan"), 5),
      ODTCStep(100, 5),
      ODTCStep(95, -1),
      ODTCStep(95, float("inf")),
      ODTCStep(95, 5, ramp_rate=100),
      ODTCStep(95, 5, pid_number=9),
    ):
      with self.subTest(step=step), self.assertRaises(ValueError):
        compile_protocol(
          ODTCProtocol([ODTCStage([step])]), variant=96, volume=25, lid_temperature=110
        )
    invalid_repeats: list[Any] = [0, -1, 1.5, True]
    for repeats in invalid_repeats:
      with self.subTest(repeats=repeats), self.assertRaises(ValueError):
        compile_protocol(
          ODTCProtocol([ODTCStage([ODTCStep(95, 5)], repeats)]),
          variant=96,
          volume=25,
          lid_temperature=110,
        )

  def test_volume_boundaries(self):
    for volume, code in ((1, 0), (29.9, 0), (30, 1), (74.9, 1), (75, 2), (100, 2)):
      self.assertEqual(volume_to_fluid_quantity(volume), code)
    for volume in (0, -1, 101, float("nan"), float("inf")):
      with self.assertRaises(ValueError):
        volume_to_fluid_quantity(volume)

  def test_automatic_overshoot_is_opt_in(self):
    profile = ODTCProtocol([ODTCStage([ODTCStep(50, 10), ODTCStep(95, 30)])])
    method = compile_protocol(profile, variant=96, volume=25, lid_temperature=110)
    self.assertEqual(method.steps[1].overshoot.temperature, 0)
    with self.assertLogs("pylabrobot.inheco.odtc.protocol", level="WARNING"):
      method = compile_protocol(
        profile, variant=96, volume=25, lid_temperature=110, apply_overshoot=True
      )
    self.assertGreater(method.steps[1].overshoot.temperature, 0)
    self.assertLessEqual(99 + calculate_overshoot(99, 40, 4.4, 30, 2).temperature, 102)

  def test_no_implicit_delete_and_unique_names(self):
    original = parse_method_set((FIXTURES / "flat_loop_xml.xml").read_text())
    self.assertNotIn("DeleteAllMethods", method_set_to_xml(original))
    with self.assertRaises(ValueError):
      method_set_to_xml(ODTCMethodSet([original.methods[0], original.methods[0]]))

  def test_missing_xml_fields_are_errors(self):
    xml = (FIXTURES / "flat_loop_xml.xml").read_text().replace("<PlateauTime>10</PlateauTime>", "")
    with self.assertRaisesRegex(ValueError, "PlateauTime"):
      parse_method_set(xml)

  def test_invalid_native_loop_is_rejected(self):
    method = parse_method_set((FIXTURES / "flat_loop_xml.xml").read_text()).methods[0]
    method = replace(method, steps=[replace(method.steps[0], goto=5, loops=1), method.steps[1]])
    with self.assertRaisesRegex(ValueError, "loop"):
      method_set_to_xml(ODTCMethodSet([method]))

  def test_manual_progress_units(self):
    sample = parse_progress((FIXTURES / "firmware_manual_data_event.xml").read_text())
    self.assertEqual(
      (
        sample.elapsed,
        sample.target_temperature,
        sample.current_temperature,
        sample.lid_temperature,
      ),
      (4.355, 15.66, 16.56, 24.63),
    )

  def test_malformed_progress_is_not_reported_as_zero(self):
    xml = (FIXTURES / "firmware_manual_data_event.xml").read_text()
    with self.assertRaisesRegex(ValueError, "units"):
      parse_progress(xml.replace("1/100°C", "C"))
    root = ET.fromstring(xml)
    any_data = root.find("AnyData")
    assert any_data is not None and any_data.text is not None
    data = ET.fromstring(any_data.text)
    step = data.find("experimentStep")
    assert step is not None
    step.remove(step[-1])
    any_data.text = ET.tostring(data, encoding="unicode")
    with self.assertRaisesRegex(ValueError, "missing series"):
      parse_progress(ET.tostring(root, encoding="unicode"))

  def test_sensor_values_from_manual(self):
    xml = (
      '<SensorValues timestamp="2014-01-01T12:00:00Z"><Mount>2463</Mount>'
      "<Mount_Monitor>2642</Mount_Monitor><Lid>2575</Lid><Lid_Monitor>2627</Lid_Monitor>"
      "<Ambient>2450</Ambient><PCB>3308</PCB><Heatsink>2596</Heatsink><Heatsink_TEC>2487</Heatsink_TEC></SensorValues>"
    )
    values = parse_sensor_values(xml)
    self.assertEqual(values.mount, 24.63)
    self.assertEqual(values.lid, 25.75)
    with self.assertRaises(ValueError):
      parse_sensor_values(xml.replace("<Mount>2463</Mount>", ""))

  def test_loop_entry_rate_accounts_for_return_transition(self):
    profile = ODTCProtocol([ODTCStage([ODTCStep(50, 5), ODTCStep(95, 5)], repeats=2)])
    method = compile_protocol(profile, variant=96, volume=25, lid_temperature=105)
    self.assertEqual(method.steps[0].ramp_rate, 2.2)
    with self.assertRaisesRegex(ValueError, "ambiguous"):
      compile_protocol(profile, variant=96, volume=25, lid_temperature=105, apply_overshoot=True)

  def test_working_premethod_name_cannot_overwrite_main_method(self):
    with self.assertRaisesRegex(ValueError, "reserved"):
      compile_protocol(
        ODTCProtocol([ODTCStage([ODTCStep(95, 10)])]),
        variant=96,
        volume=25,
        lid_temperature=105,
        name="plr_currentPreMethod",
      )
