import unittest

from pylabrobot.opentrons.pipette_defaults import flow_rates, supported_tip_volumes


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


if __name__ == "__main__":
  unittest.main()
