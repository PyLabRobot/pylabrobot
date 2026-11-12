"""Biomek techniques as PyLabRobot arguments: the 96-head steps of a method Biomek's logs ran
(2026-10-20, a dilution), as records exported from Biomek's project database, give the arguments
`driver/liquid_tests.py` checks against the logged moves."""

import unittest
from dataclasses import asdict
from typing import Any, Dict

from pylabrobot.beckman_coulter.biomek_i7 import units
from pylabrobot.lib.liquid_handling.mix import Mix
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.beckman_coulter.biomek_i7.techniques import (
  BiomekLiquidType,
  BiomekTechnique,
  Conditioning,
  MixHeights,
  TipTouch,
  aspirate_arguments,
  dispense_arguments,
  for_channels,
  method_step_arguments,
)
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_pods

SECTION = {"c__mix": False, "c__tiptouch": False, "c__blowout": False, "c__trailingairgap": False}
GENERAL = {
  "c__aspiratedelay": 100.0, "c__aspiratespeed": 100.0, "c__dispensedelay": 0.0,
  "c__dispensespeed": 100.0, "c__blowoutvolume": 10.0, "c__blowoutdelay": 0.0,
  "C__CalibrationSlope": 1.0, "C__CalibrationOffset": 0.0, "C__ConditioningExcess": 0.0,
  "C__ConditioningSectionCount": 1.0, "C__ConditioningSectionVolume": 10.0,
  "C__MinimumHeight": 0.2, "C__Prewet": False, "c__cutoffvelocity": 150.0,
  "C__Conductivity": "3500", "c__overrideliquidtypesettings": True,
  "c__tiptouchheight": -2.0, "c__tiptouchheightfrom": 0.0, "c__tiptouchspeed": 100.0,
  "c__tiptouchtheta": 90.0, "c__tiptouchdelay": 0.0,
}  # fmt: skip
# The method's aspirate step (from the reservoir) and dispense step, sections as a method embeds.
ASPIRATE: Dict[str, Any] = {
  "name": "[Custom]",
  "template": "MC Pipetting Reservoir with Asp Offset",
  "aspirate": {**SECTION, "C__Height": 2.0, "C__HeightFrom": 1, "C__Speed": 50.0,
               "C__FollowLiquid": False},
  "general": GENERAL,
}  # fmt: skip
DISPENSE: Dict[str, Any] = {
  "name": "[Custom]",
  "template": "MC Pipetting",
  "dispense": {**SECTION, "C__Height": 1.0, "C__HeightFrom": 1, "C__Speed": 25.0,
               "C__FollowLiquid": True, "C__TipTouch": True},
  "general": {**GENERAL, "c__dispensespeed": 125.0},
}  # fmt: skip
# The Span-8 dispense step that mixes (the same method): once, 25 uL at 25 uL/s,
# drawing and pushing 2 mm from the bottom, following the liquid as its dispense does.
MIXING_DISPENSE: Dict[str, Any] = {
  "name": "[Custom]",
  "template": "S8 Pipetting",
  "dispense": {**SECTION, "C__Height": 2.0, "C__HeightFrom": 1, "C__Speed": 30.0,
               "C__FollowLiquid": True, "c__blowout": True, "c__mix": True, "C__MixCount": 1.0,
               "C__MixVolume": 25.0, "C__MixAspirateFrom": 1, "C__MixAspirateHeight": 2.0,
               "C__MixAspirateSpeed": 25.0, "C__MixDispenseFrom": 1, "C__MixDispenseHeight": 2.0,
               "C__MixDispenseSpeed": 25.0},
  "general": GENERAL,
}  # fmt: skip
WATER = {"name": "Water", "context": {"c__aspiratespeed": 90.0, "c__dispensespeed": 90.0}}


# The generic instrument's pods: the 96-head's and the Span-8's (the techniques' units are theirs).
MC, S8 = generic_pods()


class TechniqueTests(unittest.TestCase):
  def test_the_logged_aspirate(self) -> None:
    args = aspirate_arguments(BiomekTechnique.from_record(ASPIRATE), arm=MC)
    self.assertEqual(args["liquid_height"], 2.0)
    self.assertEqual(args["height_from"], "bottom")
    self.assertFalse(args["follow_liquid"])
    self.assertEqual(args["minimum_height"], 0.2)  # mm, as the log shows
    self.assertAlmostEqual(args["z_speed"], 47.5)  # mm/s: 50 % of the Multichannel Z's 95
    self.assertEqual(units.rate_to_field(MC, "Z", args["z_speed"]), 500)  # Biomek's field, exactly
    self.assertEqual(args["flow_rate"], 100.0)
    self.assertAlmostEqual(args["settling_time"], 0.1)
    self.assertEqual(args["blow_out_air_volume"], 0.0)  # off in the aspirate section
    self.assertEqual(args["conditioning"], Conditioning(1, 10.0, 0.0, 100.0, 0.0))
    self.assertIsNone(args["tip_touch"])

  def test_a_method_step(self) -> None:
    # The reservoir template moves the aspirate 2 mm left; a step's own height overrides.
    step = {
      "Operation": "Aspirate",
      "Span8": False,
      "technique": asdict(BiomekTechnique.from_record(ASPIRATE)),
      "OverrideHeight": True,
      "Height": 3.0,
      "HeightFrom": 2,
    }
    args = method_step_arguments(step, arm=MC)
    self.assertEqual(args["offset"], Coordinate(-2.0, 0.0, 0))
    self.assertEqual((args["liquid_height"], args["height_from"]), (3.0, "top"))
    with self.assertRaises(ValueError):
      method_step_arguments({**step, "Operation": "Mix"}, arm=MC)
    with self.assertRaisesRegex(ValueError, "96-head"):
      method_step_arguments(step, arm=S8)  # a 96-head step on the Span-8

  def test_for_channels(self) -> None:
    args = for_channels({"liquid_height": 1.0, "flow_rate": 20.0, "z_speed": 30.0}, 3)
    self.assertEqual(args, {"liquid_heights": [1.0] * 3, "flow_rates": [20.0] * 3, "z_speed": 30.0})

  def test_the_logged_dispense(self) -> None:
    args = dispense_arguments(BiomekTechnique.from_record(DISPENSE), arm=MC)
    self.assertTrue(args["follow_liquid"])
    self.assertEqual(args["flow_rate"], 125.0)
    self.assertEqual(args["tip_touch"], TipTouch(height=-2.0, height_from="liquid", theta=90.0))
    self.assertFalse(args["empty"])

  def test_the_span8_adds_its_own(self) -> None:
    args = aspirate_arguments(BiomekTechnique.from_record(ASPIRATE), arm=S8)
    self.assertEqual((args["cutoff_velocity"], args["lls_sensitivity"]), (150.0, 3500))

  def test_the_liquid_type_unless_the_technique_overrides_it(self) -> None:
    water = BiomekLiquidType.from_record(WATER)
    general = {**GENERAL, "c__overrideliquidtypesettings": False}
    technique = BiomekTechnique.from_record({**ASPIRATE, "general": general})
    self.assertEqual(aspirate_arguments(technique, water, arm=MC)["flow_rate"], 90.0)
    overriding = BiomekTechnique.from_record(ASPIRATE)
    self.assertEqual(aspirate_arguments(overriding, water, arm=MC)["flow_rate"], 100.0)

  def test_the_logged_mixing_dispense(self) -> None:
    args = dispense_arguments(BiomekTechnique.from_record(MIXING_DISPENSE), arm=S8)
    self.assertEqual(args["post_mix"], Mix(25.0, 1, 25.0, auto_surface_following=True))
    self.assertEqual(args["mix_heights"], MixHeights(2.0, "bottom", 2.0, "bottom"))
    self.assertNotIn("pre_mix", args)
    span8 = for_channels(args, 8)
    self.assertEqual(span8["post_mixes"], [args["post_mix"]] * 8)
    self.assertNotIn("post_mix", span8)
    # An aspirate section that mixes gives `pre_mix`; one that does not, no mix arguments.
    section = {**MIXING_DISPENSE["dispense"], "C__FollowLiquid": False, "C__MixAspirateFrom": 0,
               "C__MixAspirateHeight": -1.0}  # fmt: skip
    mixing = {**ASPIRATE, "aspirate": section}
    args = aspirate_arguments(BiomekTechnique.from_record(mixing), arm=MC)
    self.assertEqual(args["pre_mix"], Mix(25.0, 1, 25.0))
    self.assertEqual(args["mix_heights"], MixHeights(-1.0, "liquid", 2.0, "bottom"))
    self.assertNotIn(
      "mix_heights", aspirate_arguments(BiomekTechnique.from_record(ASPIRATE), arm=MC)
    )

  def test_a_mix_the_driver_cannot_take_is_refused(self) -> None:
    def dispense(**changes: Any) -> None:
      record = {**MIXING_DISPENSE, "dispense": {**MIXING_DISPENSE["dispense"], **changes}}
      dispense_arguments(BiomekTechnique.from_record(record), arm=S8)

    with self.assertRaisesRegex(NotImplementedError, "speeds differ"):
      dispense(C__MixDispenseSpeed=100.0)  # PyLabRobot's Mix has one flow rate
    with self.assertRaisesRegex(NotImplementedError, "DilutionVolume"):
      dispense(C__MixVolume="=DilutionVolume")  # a method's variable

  def test_what_is_not_built_is_refused(self) -> None:
    prewetting = {**ASPIRATE, "general": {**GENERAL, "C__Prewet": True}}
    with self.assertRaisesRegex(NotImplementedError, "prewet"):
      aspirate_arguments(BiomekTechnique.from_record(prewetting), arm=MC)
    calibrated = {**ASPIRATE, "general": {**GENERAL, "C__CalibrationSlope": 1.02}}
    with self.assertRaisesRegex(NotImplementedError, "calibration"):
      aspirate_arguments(BiomekTechnique.from_record(calibrated), arm=MC)
