"""Tests for ODTC model — ODTCProtocol, constraints, helpers."""

import unittest

from pylabrobot.capabilities.thermocycling.standard import Protocol, Ramp, Stage, Step
from pylabrobot.inheco.odtc.model import (
  FluidQuantity,
  ODTCPID,
  ODTCMethodSet,
  ODTCProtocol,
  get_constraints,
  normalize_variant,
  volume_to_fluid_quantity,
)


def _make_method(**kwargs) -> ODTCProtocol:
  defaults = dict(
    stages=[Stage(steps=[Step(temperature=95.0, hold_seconds=30.0)])],
    name="TestMethod",
    variant=96,
    plate_type=0,
    fluid_quantity=FluidQuantity.UL_30_TO_74,
    post_heating=True,
    start_block_temperature=25.0,
    start_lid_temperature=110.0,
    pid_set=[ODTCPID(number=1)],
    kind="method",
  )
  defaults.update(kwargs)
  return ODTCProtocol(**defaults)


class TestODTCProtocolIsProtocol(unittest.TestCase):
  def test_isinstance_protocol(self):
    m = _make_method()
    self.assertIsInstance(m, Protocol)

  def test_name_from_protocol(self):
    m = _make_method(name="PCR_96")
    self.assertEqual(m.name, "PCR_96")

  def test_lid_temperature_from_protocol(self):
    m = _make_method(lid_temperature=105.0)
    self.assertEqual(m.lid_temperature, 105.0)

  def test_stages_from_protocol(self):
    stages = [Stage(steps=[Step(temperature=95.0, hold_seconds=30.0)], repeats=35)]
    m = _make_method(stages=stages)
    self.assertEqual(len(m.stages), 1)
    self.assertEqual(m.stages[0].repeats, 35)

  def test_defaults(self):
    m = _make_method()
    self.assertEqual(m.kind, "method")
    self.assertTrue(m.is_scratch)
    self.assertIsNone(m.creator)
    self.assertIsNone(m.description)
    self.assertIsNotNone(m.datetime)
    self.assertEqual(m.target_block_temperature, 0.0)
    self.assertEqual(m.target_lid_temperature, 0.0)


class TestODTCProtocolValidation(unittest.TestCase):
  def test_invalid_fluid_quantity_96(self):
    with self.assertRaises(ValueError) as ctx:
      _make_method(fluid_quantity=99)
    self.assertIn("fluid_quantity", str(ctx.exception))

  def test_invalid_plate_type_96(self):
    with self.assertRaises(ValueError) as ctx:
      _make_method(plate_type=2, variant=96)
    self.assertIn("plate_type", str(ctx.exception))

  def test_valid_plate_type_384(self):
    m = _make_method(variant=384, plate_type=2, pid_set=[ODTCPID(number=1)])
    self.assertEqual(m.plate_type, 2)

  def test_invalid_plate_type_384(self):
    with self.assertRaises(ValueError):
      _make_method(variant=384, plate_type=99)

  def test_invalid_lid_temperature(self):
    with self.assertRaises(ValueError) as ctx:
      _make_method(lid_temperature=200.0)
    self.assertIn("lid_temperature", str(ctx.exception))

  def test_valid_lid_none(self):
    m = _make_method(lid_temperature=None)
    self.assertIsNone(m.lid_temperature)


class TestPremethod(unittest.TestCase):
  def test_premethod_kind(self):
    m = ODTCProtocol(
      stages=[],
      name="PreHeat",
      variant=96,
      plate_type=0,
      fluid_quantity=1,
      post_heating=False,
      start_block_temperature=37.0,
      start_lid_temperature=110.0,
      pid_set=[ODTCPID(number=1)],
      kind="premethod",
      target_block_temperature=37.0,
      target_lid_temperature=110.0,
    )
    self.assertEqual(m.kind, "premethod")
    self.assertAlmostEqual(m.target_block_temperature, 37.0)


class TestNormalizeVariant(unittest.TestCase):
  def test_96(self):
    self.assertEqual(normalize_variant(96), 96)
    self.assertEqual(normalize_variant(960000), 96)

  def test_384(self):
    self.assertEqual(normalize_variant(384), 384)
    self.assertEqual(normalize_variant(384000), 384)
    self.assertEqual(normalize_variant(3840000), 384)

  def test_invalid(self):
    with self.assertRaises(ValueError):
      normalize_variant(100)


class TestGetConstraints(unittest.TestCase):
  def test_96_constraints(self):
    c = get_constraints(96)
    self.assertAlmostEqual(c.max_heating_slope, 4.4)
    self.assertAlmostEqual(c.max_lid_temp, 110.0)

  def test_384_constraints(self):
    c = get_constraints(384)
    self.assertAlmostEqual(c.max_heating_slope, 5.0)
    self.assertAlmostEqual(c.max_lid_temp, 115.0)
    self.assertIn(2, c.valid_plate_types)


class TestVolumeToFluidQuantity(unittest.TestCase):
  def test_small(self):
    self.assertEqual(volume_to_fluid_quantity(20.0), 0)

  def test_medium(self):
    self.assertEqual(volume_to_fluid_quantity(50.0), 1)

  def test_large(self):
    self.assertEqual(volume_to_fluid_quantity(80.0), 2)

  def test_too_large(self):
    with self.assertRaises(ValueError):
      volume_to_fluid_quantity(101.0)


class TestODTCMethodSet(unittest.TestCase):
  def test_get_by_name(self):
    m = _make_method(name="PCR1")
    ms = ODTCMethodSet(methods=[m])
    self.assertIs(ms.get("PCR1"), m)
    self.assertIsNone(ms.get("Missing"))

  def test_get_premethod(self):
    pm = ODTCProtocol(
      stages=[],
      name="PreHeat",
      variant=96,
      plate_type=0,
      fluid_quantity=1,
      post_heating=False,
      start_block_temperature=37.0,
      start_lid_temperature=110.0,
      pid_set=[ODTCPID(number=1)],
      kind="premethod",
    )
    ms = ODTCMethodSet(premethods=[pm])
    self.assertIs(ms.get("PreHeat"), pm)


class TestFluidQuantity(unittest.TestCase):
  def test_int_enum_equality(self):
    """FluidQuantity is an IntEnum — integer comparisons still work."""
    self.assertEqual(FluidQuantity.UL_10_TO_29, 0)
    self.assertEqual(FluidQuantity.UL_30_TO_74, 1)
    self.assertEqual(FluidQuantity.UL_75_TO_100, 2)
    self.assertEqual(FluidQuantity.VERIFICATION_TOOL, -1)

  def test_volume_to_fluid_quantity_returns_fluid_quantity(self):
    result = volume_to_fluid_quantity(20.0)
    self.assertIsInstance(result, FluidQuantity)
    self.assertEqual(result, FluidQuantity.UL_10_TO_29)

  def test_volume_to_fluid_quantity_ranges(self):
    self.assertEqual(volume_to_fluid_quantity(10.0), FluidQuantity.UL_10_TO_29)
    self.assertEqual(volume_to_fluid_quantity(29.0), FluidQuantity.UL_10_TO_29)
    self.assertEqual(volume_to_fluid_quantity(30.0), FluidQuantity.UL_30_TO_74)
    self.assertEqual(volume_to_fluid_quantity(74.0), FluidQuantity.UL_30_TO_74)
    self.assertEqual(volume_to_fluid_quantity(75.0), FluidQuantity.UL_75_TO_100)
    self.assertEqual(volume_to_fluid_quantity(100.0), FluidQuantity.UL_75_TO_100)

  def test_volume_to_fluid_quantity_too_large(self):
    with self.assertRaises(ValueError):
      volume_to_fluid_quantity(101.0)

  def test_fluid_quantity_used_as_int_in_xml_validation(self):
    """FluidQuantity can be used wherever int is expected."""
    m = _make_method(fluid_quantity=FluidQuantity.UL_30_TO_74)
    self.assertEqual(m.fluid_quantity, 1)
    self.assertIsInstance(m.fluid_quantity, FluidQuantity)


if __name__ == "__main__":
  unittest.main()
