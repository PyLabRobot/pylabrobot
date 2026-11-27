import unittest
from typing import Any, Dict, List

from pylabrobot.lib.fluorescence.channels import FluorescenceChannel
from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter


class FluorescenceChannelTests(unittest.TestCase):
  def test_wrong_combinations_and_a_split_not_positive_are_refused(self):
    refused: List[Dict[str, Any]] = [
      dict(excitation_wavelength=485, excitation_filter=OpticalFilter(1)),
      dict(emission_bandwidth=20, emission_filter=OpticalFilter(1)),
      dict(dichroic_split_wavelength=510.0, dichroic_filter=DichroicFilter(1)),
      dict(dichroic_split_wavelength=0.0),
    ]
    for fields in refused:
      with self.subTest(fields), self.assertRaises(ValueError):
        FluorescenceChannel(**fields)

  def test_every_violation_is_listed(self):
    with self.assertRaises(ValueError) as raised:
      FluorescenceChannel(excitation_wavelength=-485, emission_wavelength=528, emission_bandwidth=0)
    self.assertIn("excitation_wavelength", str(raised.exception))
    self.assertIn("emission_bandwidth", str(raised.exception))


if __name__ == "__main__":
  unittest.main()
