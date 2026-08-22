import unittest

import pytest

from pylabrobot.opentrons.pipette_defaults import (
  _DEFAULTS,
  flow_rates,
  supported_tip_volumes,
)


class PipetteDefaultsTests(unittest.TestCase):
  """Flow-rate defaults come from Opentrons' own shipped pipette data."""

  def test_rates_differ_by_pipette_and_by_tip(self):
    # The whole reason a single constant cannot serve: these span two orders of
    # magnitude across pipettes the same robot can mount.
    self.assertEqual(flow_rates("p50_multi_v3.5", 50), (35.0, 57.0, 57.0))
    self.assertEqual(flow_rates("p1000_multi_v3.5", 1000), (716.0, 716.0, 716.0))
    self.assertEqual(flow_rates("p1000_multi_v3.5", 50), (478.0, 478.0, 478.0))
    self.assertEqual(flow_rates("p200_96_v3.3", 20), (6.5, 6.5, 10.0))

  def test_rates_differ_between_versions_of_one_pipette(self):
    # Same pipette, same tip, different hardware revision: v3.0 runs a 50uL tip
    # at 8 uL/s where v3.5 runs it at 35.
    self.assertEqual(flow_rates("p50_multi_v3.0", 50).aspirate, 8.0)
    self.assertEqual(flow_rates("p50_multi_v3.5", 50).aspirate, 35.0)

  def test_unsupported_tip_is_refused_and_names_what_is_supported(self):
    with self.assertRaises(ValueError) as caught:
      flow_rates("p50_multi_v3.5", 300)
    self.assertIn("300", str(caught.exception))
    self.assertIn("t50", str(caught.exception))

  def test_empty_model_is_refused_rather_than_resolving_to_a_p1000(self):
    # Opentrons' own parser maps "" to a p1000 single-channel instead of raising,
    # which would blow a p50 out at up to 716 uL/s.
    with self.assertRaises(ValueError):
      flow_rates("", 50)

  def test_supported_tip_volumes_are_ascending(self):
    self.assertEqual(supported_tip_volumes("p50_multi_v3.5"), (20.0, 50.0))
    self.assertEqual(supported_tip_volumes("p1000_multi_v3.5"), (50.0, 200.0, 1000.0))

  def test_an_unrecorded_model_is_refused_rather_than_run_at_a_neighbour_s_rate(self):
    with self.assertRaises(ValueError) as caught:
      flow_rates("p50_single_v9.9", 50)
    self.assertIn("p50_single_v9.9", str(caught.exception))
    self.assertIn("flow_rate", str(caught.exception))


class PipetteDefaultsMatchOpentronsTests(unittest.TestCase):
  """The vendored table is a copy, so prove it still matches what it was copied from.

  Skipped unless ``opentrons-shared-data`` is installed; PyLabRobot does not
  depend on it.
  """

  def test_every_recorded_model_matches_opentrons_own_definition(self):
    pytest.importorskip("opentrons_shared_data")
    from opentrons_shared_data.pipette.load_data import load_liquid_model
    from opentrons_shared_data.pipette.pipette_load_name_conversions import (
      convert_pipette_model,
    )
    from opentrons_shared_data.pipette.types import PipetteModel, PipetteOEMType

    for model, recorded in _DEFAULTS.items():
      with self.subTest(model=model):
        version = convert_pipette_model(PipetteModel(model))
        liquid_model = load_liquid_model(
          version.pipette_type,
          version.pipette_channels,
          version.pipette_version,
          PipetteOEMType.OT,
        )
        theirs = {
          tip_type.name: (
            tip.default_aspirate_flowrate.default,
            tip.default_dispense_flowrate.default,
            tip.default_blowout_flowrate.default,
          )
          for tip_type, tip in liquid_model["default"].supported_tips.items()
        }
        self.assertEqual({name: tuple(r) for name, r in recorded.items()}, theirs)


if __name__ == "__main__":
  unittest.main()
