"""Liquid class table tests: its keys name chip types the RFID reader decodes."""

import unittest

from pylabrobot.formulatrix.mantis.driver.features.rfid import RFID_CHIP_TYPE_CODES
from pylabrobot.formulatrix.mantis.liquid_classes import mantis_mapping


class MappingTests(unittest.TestCase):
  def test_every_chip_type_is_one_the_rfid_reader_decodes(self):
    self.assertLessEqual(
      {chip_type for _, chip_type in mantis_mapping}, set(RFID_CHIP_TYPE_CODES.values())
    )

  def test_every_band_holds_water_like_and_viscous_classes(self):
    self.assertEqual(
      {band for band, _ in mantis_mapping}, {"1 cP", "2-5 cP", "6-10 cP", "11-20 cP", "21-25 cP"}
    )

  def test_recovery_delays_are_their_own_values(self):
    high_volume = mantis_mapping[("1 cP", "high_volume")]
    self.assertEqual((high_volume.large_fill_delay, high_volume.large_empty_delay), (136, 84))
    self.assertEqual((high_volume.recovery_fill_delay, high_volume.recovery_empty_delay), (60, 30))


if __name__ == "__main__":
  unittest.main()
