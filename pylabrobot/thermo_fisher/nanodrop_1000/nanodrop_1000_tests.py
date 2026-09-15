import math
import unittest

from pylabrobot.thermo_fisher.nanodrop_1000.nanodrop_1000 import ThermoFisherNanoDrop1000


def _flat(value: float, n: int = 2048):
  return [value] * n


class ThermoFisherNanoDrop1000PathLengthTests(unittest.TestCase):
  """Path-length bookkeeping. None of this touches the USB transport."""

  def setUp(self):
    self.nd = ThermoFisherNanoDrop1000()
    # 2048 pixels spanning roughly the instrument's real range, so the auto-range window
    # (235-750 nm) selects a sensible slice.
    self.wavelengths = [180.0 + i * 0.35 for i in range(2048)]

  def test_to_path_length_scales_linearly(self):
    # Beer-Lambert is linear in path length: x10 from the long path, x50 from the short.
    self.assertEqual(ThermoFisherNanoDrop1000.to_path_length([0.5], from_mm=1.0), [5.0])
    self.assertEqual(ThermoFisherNanoDrop1000.to_path_length([0.5], from_mm=0.2), [25.0])

  def test_to_path_length_explicit_target(self):
    self.assertEqual(ThermoFisherNanoDrop1000.to_path_length([1.0], from_mm=1.0, to_mm=0.2), [0.2])

  def test_select_path_prefers_the_longest_in_range(self):
    # A longer path resolves small differences better, so prefer it while it is usable.
    spectra = {1.0: _flat(0.4), 0.2: _flat(0.08)}
    self.assertEqual(self.nd.select_path(self.wavelengths, spectra), 1.0)

  def test_select_path_falls_back_when_the_long_path_saturates(self):
    spectra = {1.0: _flat(2.0), 0.2: _flat(0.4)}  # long path over the 1.2 AU cutoff
    self.assertEqual(self.nd.select_path(self.wavelengths, spectra), 0.2)

  def test_select_path_returns_shortest_when_nothing_is_in_range(self):
    # Too concentrated for either path. The caller is warned; the shortest is least bad.
    spectra = {1.0: _flat(3.0), 0.2: _flat(2.0)}
    with self.assertLogs("pylabrobot.thermo_fisher.nanodrop_1000.nanodrop_1000", "WARNING"):
      self.assertEqual(self.nd.select_path(self.wavelengths, spectra), 0.2)

  def test_peak_absorbance_ignores_the_photon_starved_deep_uv(self):
    # Below ~235 nm the detector is photon starved and the absorbance there is noise, so
    # a spike outside the window must not drive the auto-range decision.
    absorbance = _flat(0.1)
    for i, wavelength in enumerate(self.wavelengths):
      if wavelength < 235.0:
        absorbance[i] = 99.0
    self.assertAlmostEqual(self.nd._peak_absorbance(self.wavelengths, absorbance), 0.1)

  def test_legacy_spectrum_properties_map_to_the_short_path(self):
    # Before path selection existed every acquisition ran with the magnet energised, i.e.
    # on the short path, so these must land there to preserve their original meaning.
    self.nd.dark_spectrum = [1.0, 2.0]
    self.nd.blank_spectrum = [3.0, 4.0]
    self.assertEqual(self.nd.baselines[ThermoFisherNanoDrop1000.PATH_SHORT_MM]["dark"], [1.0, 2.0])
    self.assertEqual(self.nd.dark_spectrum, [1.0, 2.0])
    self.assertEqual(self.nd.blank_spectrum, [3.0, 4.0])


class ThermoFisherNanoDrop1000AbsorbanceTests(unittest.TestCase):
  def setUp(self):
    self.nd = ThermoFisherNanoDrop1000()

  def test_absorbance_against_the_matching_baseline(self):
    self.nd.baselines[1.0] = {"dark": [100.0] * 4, "blank": [1100.0] * 4}
    # Half the blank's lift through the sample -> absorbance of log10(2).
    absorbance = self.nd._absorbance([600.0] * 4, 1.0)
    for value in absorbance:
      self.assertAlmostEqual(value, math.log10(2.0), places=6)

  def test_absorbance_without_a_blank_for_that_path_raises(self):
    self.nd.baselines[0.2] = {"dark": [100.0] * 4, "blank": [1100.0] * 4}
    # A sample measured on one path cannot be divided by a blank taken on another.
    with self.assertRaises(ValueError):
      self.nd._absorbance([600.0] * 4, 1.0)


class ThermoFisherNanoDrop1000ArgumentTests(unittest.IsolatedAsyncioTestCase):
  async def test_measure_absorbance_rejects_an_unknown_path(self):
    nd = ThermoFisherNanoDrop1000()
    with self.assertRaises(ValueError):
      await nd.measure_absorbance(path="sideways")

  async def test_select_path_rejects_a_length_the_solenoid_cannot_reach(self):
    nd = ThermoFisherNanoDrop1000()
    with self.assertRaises(ValueError):
      await nd._select_path(0.5)

  async def test_read_averaged_rejects_a_non_positive_count(self):
    nd = ThermoFisherNanoDrop1000()
    with self.assertRaises(ValueError):
      await nd._read_averaged(0)


if __name__ == "__main__":
  unittest.main()
