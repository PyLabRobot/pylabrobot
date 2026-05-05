"""Tests for ODTC XML parsing and serialization — roundtrip fidelity."""

import unittest
import xml.etree.ElementTree as ET

from pylabrobot.capabilities.thermocycling.standard import Overshoot, Ramp, Stage, Step
from pylabrobot.inheco.odtc.model import ODTCPID, ODTCMethodSet, ODTCProtocol
from pylabrobot.inheco.odtc.xml import (
  build_stages_from_parsed_steps,
  method_set_to_xml,
  parse_method_set,
  parse_method_set_file,
  _flatten_stages_for_xml,
  _parse_step_element,
  _ParsedStep,
)


def _flat_loop_xml() -> str:
  return """<?xml version="1.0" encoding="utf-8"?>
<MethodSet>
  <DeleteAllMethods>false</DeleteAllMethods>
  <Method methodName="FlatLoop" creator="test" dateTime="2025-01-01T00:00:00">
    <Variant>960000</Variant><PlateType>0</PlateType><FluidQuantity>0</FluidQuantity>
    <PostHeating>false</PostHeating>
    <StartBlockTemperature>25</StartBlockTemperature>
    <StartLidTemperature>110</StartLidTemperature>
    <Step><Number>1</Number><Slope>4.4</Slope><PlateauTemperature>95</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>2</Number><Slope>2.2</Slope><PlateauTemperature>55</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>1</GotoNumber><LoopNumber>2</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <PIDSet><PID number="1"><PHeating>60</PHeating><PCooling>80</PCooling><IHeating>250</IHeating><ICooling>100</ICooling><DHeating>10</DHeating><DCooling>10</DCooling><PLid>100</PLid><ILid>70</ILid></PID></PIDSet>
  </Method>
</MethodSet>"""


def _nested_loop_xml() -> str:
  return """<?xml version="1.0" encoding="utf-8"?>
<MethodSet>
  <DeleteAllMethods>false</DeleteAllMethods>
  <Method methodName="NestedLoops" creator="test" dateTime="2025-01-01T00:00:00">
    <Variant>960000</Variant><PlateType>0</PlateType><FluidQuantity>0</FluidQuantity>
    <PostHeating>false</PostHeating>
    <StartBlockTemperature>25</StartBlockTemperature>
    <StartLidTemperature>110</StartLidTemperature>
    <Step><Number>1</Number><Slope>4.4</Slope><PlateauTemperature>95</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>2</Number><Slope>2.2</Slope><PlateauTemperature>55</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>3</Number><Slope>4.4</Slope><PlateauTemperature>72</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>4</Number><Slope>4.4</Slope><PlateauTemperature>95</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>2</GotoNumber><LoopNumber>4</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>5</Number><Slope>2.2</Slope><PlateauTemperature>50</PlateauTemperature><PlateauTime>20</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>1</GotoNumber><LoopNumber>29</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <PIDSet><PID number="1"><PHeating>60</PHeating><PCooling>80</PCooling><IHeating>250</IHeating><ICooling>100</ICooling><DHeating>10</DHeating><DCooling>10</DCooling><PLid>100</PLid><ILid>70</ILid></PID></PIDSet>
  </Method>
</MethodSet>"""


def _overshoot_xml() -> str:
  return """<?xml version="1.0" encoding="utf-8"?>
<MethodSet>
  <DeleteAllMethods>false</DeleteAllMethods>
  <Method methodName="WithOvershoot" creator="test" dateTime="2025-01-01T00:00:00">
    <Variant>960000</Variant><PlateType>0</PlateType><FluidQuantity>1</FluidQuantity>
    <PostHeating>true</PostHeating>
    <StartBlockTemperature>25</StartBlockTemperature>
    <StartLidTemperature>110</StartLidTemperature>
    <Step><Number>1</Number><Slope>4.4</Slope><PlateauTemperature>95</PlateauTemperature><PlateauTime>30</PlateauTime><OverShootSlope1>4.4</OverShootSlope1><OverShootTemperature>5.2</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>2.2</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <PIDSet><PID number="1"><PHeating>60</PHeating><PCooling>80</PCooling><IHeating>250</IHeating><ICooling>100</ICooling><DHeating>10</DHeating><DCooling>10</DCooling><PLid>100</PLid><ILid>70</ILid></PID></PIDSet>
  </Method>
</MethodSet>"""


class TestParsedStepToStep(unittest.TestCase):
  def test_no_overshoot(self):
    ps = _ParsedStep(
      number=1, slope=4.4, plateau_temperature=95.0, plateau_time=30.0,
      overshoot_slope1=4.4, overshoot_temperature=0.0, overshoot_time=0.0,
      overshoot_slope2=2.2, goto_number=0, loop_number=0, lid_temp=110.0,
    )
    step = ps.to_step()
    self.assertEqual(step.temperature, 95.0)
    self.assertEqual(step.hold_seconds, 30.0)
    self.assertAlmostEqual(step.ramp.rate, 4.4)
    self.assertIsNone(step.ramp.overshoot)
    self.assertAlmostEqual(step.lid_temperature, 110.0)

  def test_with_overshoot(self):
    ps = _ParsedStep(
      number=1, slope=4.4, plateau_temperature=95.0, plateau_time=30.0,
      overshoot_slope1=4.4, overshoot_temperature=5.2, overshoot_time=0.0,
      overshoot_slope2=2.2, goto_number=0, loop_number=0, lid_temp=110.0,
    )
    step = ps.to_step()
    self.assertIsNotNone(step.ramp.overshoot)
    self.assertAlmostEqual(step.ramp.overshoot.target_temp, 5.2)
    self.assertAlmostEqual(step.ramp.overshoot.return_rate, 2.2)


class TestBuildStagesFromParsedSteps(unittest.TestCase):
  def test_flat_no_loop(self):
    steps = [
      _ParsedStep(1, 4.4, 95.0, 30.0, 4.4, 0, 0, 2.2, 0, 0, 110.0),
      _ParsedStep(2, 2.2, 55.0, 30.0, 2.2, 0, 0, 2.2, 0, 0, 110.0),
    ]
    stages = build_stages_from_parsed_steps(steps)
    self.assertEqual(len(stages), 1)
    self.assertEqual(stages[0].repeats, 1)
    self.assertEqual(len(stages[0].steps), 2)
    self.assertEqual(stages[0].inner_stages, [])

  def test_flat_loop(self):
    # step 2 has goto=1, loop=2 → 3 total repeats
    steps = [
      _ParsedStep(1, 4.4, 95.0, 10.0, 4.4, 0, 0, 2.2, 0, 0, 110.0),
      _ParsedStep(2, 2.2, 55.0, 10.0, 2.2, 0, 0, 2.2, 1, 2, 110.0),
    ]
    stages = build_stages_from_parsed_steps(steps)
    self.assertEqual(len(stages), 1)
    self.assertEqual(stages[0].repeats, 3)
    self.assertEqual(len(stages[0].steps), 2)

  def test_nested_loop(self):
    # Steps 1-5: inner 2-4 x 5, outer 1-5 x 30
    steps = [
      _ParsedStep(1, 4.4, 95.0, 10.0, 4.4, 0, 0, 2.2, 0, 0, 110.0),
      _ParsedStep(2, 2.2, 55.0, 10.0, 2.2, 0, 0, 2.2, 0, 0, 110.0),
      _ParsedStep(3, 4.4, 72.0, 10.0, 4.4, 0, 0, 2.2, 0, 0, 110.0),
      _ParsedStep(4, 4.4, 95.0, 10.0, 4.4, 0, 0, 2.2, 2, 4, 110.0),
      _ParsedStep(5, 2.2, 50.0, 20.0, 2.2, 0, 0, 2.2, 1, 29, 110.0),
    ]
    stages = build_stages_from_parsed_steps(steps)
    outer = next((s for s in stages if s.repeats == 30), None)
    self.assertIsNotNone(outer)
    self.assertEqual(len(outer.inner_stages), 1)
    self.assertEqual(outer.inner_stages[0].repeats, 5)


class TestFlattenStagesForXml(unittest.TestCase):
  def test_flat_no_repeat(self):
    stage = Stage(
      steps=[Step(95.0, 30.0), Step(55.0, 30.0)],
      repeats=1,
    )
    flat = _flatten_stages_for_xml([stage])
    self.assertEqual(len(flat), 2)
    self.assertEqual(flat[0][1], 1)  # number
    self.assertEqual(flat[0][2], 0)  # goto
    self.assertEqual(flat[1][1], 2)
    self.assertEqual(flat[1][2], 0)

  def test_stage_with_repeats(self):
    stage = Stage(
      steps=[Step(95.0, 10.0), Step(55.0, 10.0)],
      repeats=35,
    )
    flat = _flatten_stages_for_xml([stage])
    self.assertEqual(len(flat), 2)
    # last step should have goto=1, loop=34
    last_step, num, goto, loop = flat[-1]
    self.assertEqual(goto, 1)
    self.assertEqual(loop, 34)

  def test_sequential_stages(self):
    s1 = Stage(steps=[Step(95.0, 10.0)], repeats=1)
    s2 = Stage(steps=[Step(55.0, 30.0), Step(72.0, 60.0)], repeats=35)
    flat = _flatten_stages_for_xml([s1, s2])
    self.assertEqual(len(flat), 3)
    # s1 step: number=1, no goto
    self.assertEqual(flat[0][1], 1)
    self.assertEqual(flat[0][2], 0)
    # s2 last step: number=3, goto=2
    _, num, goto, loop = flat[2]
    self.assertEqual(num, 3)
    self.assertEqual(goto, 2)
    self.assertEqual(loop, 34)


class TestXmlRoundtrip(unittest.TestCase):
  def test_flat_loop_roundtrip(self):
    ms = parse_method_set(_flat_loop_xml())
    self.assertEqual(len(ms.methods), 1)
    odtc = ms.methods[0]
    self.assertEqual(odtc.name, "FlatLoop")
    # Should produce 1 stage with 2 steps and repeats=3
    self.assertEqual(len(odtc.stages), 1)
    self.assertEqual(odtc.stages[0].repeats, 3)
    self.assertEqual(len(odtc.stages[0].steps), 2)
    # Roundtrip
    xml_out = method_set_to_xml(ODTCMethodSet(methods=[odtc]))
    ms2 = parse_method_set(xml_out)
    odtc2 = ms2.methods[0]
    self.assertEqual(len(odtc2.stages), 1)
    self.assertEqual(odtc2.stages[0].repeats, 3)

  def test_nested_loop_roundtrip(self):
    ms = parse_method_set(_nested_loop_xml())
    odtc = ms.methods[0]
    # Parse re-serializes and parses again; stage structure preserved
    xml_out = method_set_to_xml(ODTCMethodSet(methods=[odtc]))
    ms2 = parse_method_set(xml_out)
    odtc2 = ms2.methods[0]
    # Find outer stage with repeats=30 and inner stage with repeats=5
    outer = next((s for s in odtc2.stages if s.repeats == 30), None)
    self.assertIsNotNone(outer, "Expected stage with repeats=30")
    self.assertEqual(len(outer.inner_stages), 1)
    self.assertEqual(outer.inner_stages[0].repeats, 5)

  def test_overshoot_roundtrip(self):
    ms = parse_method_set(_overshoot_xml())
    odtc = ms.methods[0]
    step = odtc.stages[0].steps[0]
    self.assertIsNotNone(step.ramp.overshoot)
    self.assertAlmostEqual(step.ramp.overshoot.target_temp, 5.2, places=1)
    # Roundtrip
    xml_out = method_set_to_xml(ODTCMethodSet(methods=[odtc]))
    ms2 = parse_method_set(xml_out)
    step2 = ms2.methods[0].stages[0].steps[0]
    self.assertIsNotNone(step2.ramp.overshoot)
    self.assertAlmostEqual(step2.ramp.overshoot.target_temp, 5.2, places=1)
    self.assertAlmostEqual(step2.ramp.overshoot.return_rate, 2.2, places=1)

  def test_premethod_roundtrip(self):
    xml = """<?xml version="1.0" encoding="utf-8"?>
<MethodSet><DeleteAllMethods>false</DeleteAllMethods>
  <PreMethod methodName="PreHeat37" dateTime="2025-01-01T00:00:00">
    <TargetBlockTemperature>37</TargetBlockTemperature>
    <TargetLidTemp>110</TargetLidTemp>
  </PreMethod>
</MethodSet>"""
    ms = parse_method_set(xml)
    self.assertEqual(len(ms.premethods), 1)
    pm = ms.premethods[0]
    self.assertEqual(pm.name, "PreHeat37")
    self.assertAlmostEqual(pm.target_block_temperature, 37.0)
    # Roundtrip
    xml_out = method_set_to_xml(ODTCMethodSet(premethods=[pm]))
    ms2 = parse_method_set(xml_out)
    pm2 = ms2.premethods[0]
    self.assertEqual(pm2.name, "PreHeat37")
    self.assertAlmostEqual(pm2.target_block_temperature, 37.0)

  def test_step_temperatures_preserved(self):
    xml = _flat_loop_xml()
    ms = parse_method_set(xml)
    steps = ms.methods[0].stages[0].steps
    self.assertAlmostEqual(steps[0].temperature, 95.0)
    self.assertAlmostEqual(steps[1].temperature, 55.0)
    self.assertAlmostEqual(steps[0].ramp.rate, 4.4)
    self.assertAlmostEqual(steps[1].ramp.rate, 2.2)
    self.assertAlmostEqual(steps[0].lid_temperature, 110.0)

  def test_pid_set_preserved(self):
    ms = parse_method_set(_flat_loop_xml())
    odtc = ms.methods[0]
    self.assertEqual(len(odtc.pid_set), 1)
    self.assertEqual(odtc.pid_set[0].number, 1)
    self.assertEqual(odtc.pid_set[0].p_heating, 60.0)

  def test_method_metadata_preserved(self):
    ms = parse_method_set(_flat_loop_xml())
    odtc = ms.methods[0]
    self.assertEqual(odtc.creator, "test")
    self.assertEqual(odtc.datetime, "2025-01-01T00:00:00")
    self.assertFalse(odtc.post_heating)
    self.assertEqual(odtc.variant, 96)
    self.assertEqual(odtc.plate_type, 0)
    self.assertEqual(odtc.fluid_quantity, 0)


if __name__ == "__main__":
  unittest.main()
