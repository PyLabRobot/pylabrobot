"""Tests for thermocycling standard types."""

import math
import unittest

from pylabrobot.capabilities.thermocycling.standard import (
  FULL_SPEED,
  BlockStatus,
  LidStatus,
  Overshoot,
  Protocol,
  Ramp,
  Stage,
  Step,
)


class TestOvershoot(unittest.TestCase):
  def test_construction(self):
    o = Overshoot(target_temp=101.0, hold_seconds=2.0, return_rate=2.2)
    self.assertEqual(o.target_temp, 101.0)
    self.assertEqual(o.hold_seconds, 2.0)
    self.assertEqual(o.return_rate, 2.2)

  def test_frozen(self):
    o = Overshoot(target_temp=101.0, hold_seconds=2.0, return_rate=2.2)
    with self.assertRaises(Exception):
      o.target_temp = 99.0  # type: ignore


class TestRamp(unittest.TestCase):
  def test_default_is_full_speed(self):
    r = Ramp()
    self.assertEqual(r, FULL_SPEED)
    self.assertTrue(math.isinf(r.rate))
    self.assertIsNone(r.overshoot)

  def test_with_rate(self):
    r = Ramp(rate=5.0)
    self.assertEqual(r.rate, 5.0)
    self.assertIsNone(r.overshoot)

  def test_with_overshoot(self):
    o = Overshoot(target_temp=101.0, hold_seconds=2.0, return_rate=2.2)
    r = Ramp(rate=5.0, overshoot=o)
    self.assertEqual(r.overshoot, o)

  def test_frozen(self):
    r = Ramp(rate=5.0)
    with self.assertRaises(Exception):
      r.rate = 3.0  # type: ignore


class TestStep(unittest.TestCase):
  def test_defaults(self):
    s = Step(temperature=95.0, hold_seconds=30.0)
    self.assertEqual(s.temperature, 95.0)
    self.assertEqual(s.hold_seconds, 30.0)
    self.assertEqual(s.ramp, FULL_SPEED)
    self.assertIsNone(s.lid_temperature)
    self.assertIsNone(s.backend_params)

  def test_with_ramp(self):
    r = Ramp(rate=4.4)
    s = Step(temperature=95.0, hold_seconds=30.0, ramp=r)
    self.assertEqual(s.ramp.rate, 4.4)

  def test_with_lid_temperature(self):
    s = Step(temperature=55.0, hold_seconds=30.0, lid_temperature=110.0)
    self.assertEqual(s.lid_temperature, 110.0)

  def test_inf_hold(self):
    s = Step(temperature=4.0, hold_seconds=float("inf"))
    self.assertTrue(math.isinf(s.hold_seconds))

  def test_serialize_deserialize_no_overshoot(self):
    s = Step(temperature=72.0, hold_seconds=60.0, ramp=Ramp(rate=2.2), lid_temperature=105.0)
    data = s.serialize()
    self.assertEqual(data["temperature"], 72.0)
    self.assertEqual(data["ramp"]["rate"], 2.2)
    self.assertIsNone(data["ramp"]["overshoot"])
    self.assertEqual(data["lid_temperature"], 105.0)
    s2 = Step.deserialize(data)
    self.assertEqual(s2.temperature, 72.0)
    self.assertEqual(s2.ramp.rate, 2.2)
    self.assertIsNone(s2.ramp.overshoot)

  def test_serialize_deserialize_with_overshoot(self):
    o = Overshoot(target_temp=101.0, hold_seconds=2.0, return_rate=2.2)
    s = Step(temperature=95.0, hold_seconds=30.0, ramp=Ramp(rate=4.4, overshoot=o))
    data = s.serialize()
    self.assertIsNotNone(data["ramp"]["overshoot"])
    self.assertEqual(data["ramp"]["overshoot"]["target_temp"], 101.0)
    s2 = Step.deserialize(data)
    self.assertIsNotNone(s2.ramp.overshoot)
    self.assertEqual(s2.ramp.overshoot.target_temp, 101.0)
    self.assertEqual(s2.ramp.overshoot.return_rate, 2.2)

  def test_serialize_default_ramp(self):
    s = Step(temperature=95.0, hold_seconds=30.0)
    data = s.serialize()
    s2 = Step.deserialize(data)
    self.assertTrue(math.isinf(s2.ramp.rate))
    self.assertIsNone(s2.ramp.overshoot)


class TestStage(unittest.TestCase):
  def test_defaults(self):
    steps = [Step(temperature=95.0, hold_seconds=30.0)]
    stage = Stage(steps=steps)
    self.assertEqual(stage.repeats, 1)
    self.assertEqual(stage.inner_stages, [])

  def test_with_repeats(self):
    steps = [Step(temperature=95.0, hold_seconds=30.0)]
    stage = Stage(steps=steps, repeats=35)
    self.assertEqual(stage.repeats, 35)

  def test_inner_stages_default_is_empty_list(self):
    stage = Stage(steps=[])
    self.assertIsInstance(stage.inner_stages, list)
    self.assertEqual(len(stage.inner_stages), 0)

  def test_inner_stages_are_independent(self):
    s1 = Stage(steps=[])
    s2 = Stage(steps=[])
    s1.inner_stages.append(Stage(steps=[]))
    self.assertEqual(len(s1.inner_stages), 1)
    self.assertEqual(len(s2.inner_stages), 0)

  def test_serialize_deserialize(self):
    inner = Stage(steps=[Step(temperature=72.0, hold_seconds=60.0)], repeats=5)
    outer = Stage(
      steps=[Step(temperature=95.0, hold_seconds=10.0)],
      repeats=30,
      inner_stages=[inner],
    )
    data = outer.serialize()
    outer2 = Stage.deserialize(data)
    self.assertEqual(outer2.repeats, 30)
    self.assertEqual(len(outer2.inner_stages), 1)
    self.assertEqual(outer2.inner_stages[0].repeats, 5)
    self.assertEqual(outer2.inner_stages[0].steps[0].temperature, 72.0)


class TestProtocol(unittest.TestCase):
  def test_defaults(self):
    p = Protocol(stages=[])
    self.assertEqual(p.name, "")
    self.assertIsNone(p.lid_temperature)

  def test_with_name_and_lid(self):
    p = Protocol(stages=[], name="MyPCR", lid_temperature=105.0)
    self.assertEqual(p.name, "MyPCR")
    self.assertEqual(p.lid_temperature, 105.0)

  def test_serialize_deserialize(self):
    p = Protocol(
      stages=[
        Stage(
          steps=[
            Step(temperature=95.0, hold_seconds=30.0, ramp=Ramp(rate=4.4)),
            Step(temperature=55.0, hold_seconds=30.0),
          ],
          repeats=35,
        )
      ],
      name="TestPCR",
      lid_temperature=110.0,
    )
    data = p.serialize()
    p2 = Protocol.deserialize(data)
    self.assertEqual(p2.name, "TestPCR")
    self.assertEqual(p2.lid_temperature, 110.0)
    self.assertEqual(len(p2.stages), 1)
    self.assertEqual(p2.stages[0].repeats, 35)
    self.assertEqual(p2.stages[0].steps[0].temperature, 95.0)
    self.assertEqual(p2.stages[0].steps[0].ramp.rate, 4.4)
    self.assertEqual(p2.stages[0].steps[1].temperature, 55.0)


class TestEnums(unittest.TestCase):
  def test_lid_status_values(self):
    self.assertEqual(LidStatus.IDLE.value, "idle")
    self.assertEqual(LidStatus.HOLDING_AT_TARGET.value, "holding at target")

  def test_block_status_values(self):
    self.assertEqual(BlockStatus.IDLE.value, "idle")
    self.assertEqual(BlockStatus.HOLDING_AT_TARGET.value, "holding at target")


if __name__ == "__main__":
  unittest.main()
