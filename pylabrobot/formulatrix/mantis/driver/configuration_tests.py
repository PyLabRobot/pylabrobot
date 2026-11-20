"""Configuration tests."""

import unittest

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.configuration import (
  compute_generation,
  configuration_from_json,
  read_configuration,
)


class ComputeGenerationTests(unittest.TestCase):
  def test_board_0_version_decides_nothing(self):
    recorded = configuration_from_json(
      fields=read_configuration(path=RECORDING_MANTIS_V4)["device"]
    ).firmware_versions
    firmware_versions = {**recorded, 0: "3.0.0"}
    self.assertEqual(compute_generation(firmware_versions=firmware_versions), "v4")


if __name__ == "__main__":
  unittest.main()
