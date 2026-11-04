"""Luminescence tests through the device, over the driver's fake USB link.  The read is not tested on
a reader with luminescence."""

import unittest

import pytest

from pylabrobot.bmg_labtech.vantastar.device_tests import make_device
from pylabrobot.bmg_labtech.vantastar.driver.features.measurement import MeasurementSettings
from pylabrobot.testing.concurrency import AnyioTestBase


class TestLuminescenceArguments(AnyioTestBase):
  """Luminescence arguments refused before anything is sent."""

  async def test_flashes_and_well_scans_are_refused(self):
    """Measurement settings with flashes or a well scan are refused: a luminescence read integrates
    at the centre."""
    device = make_device([])
    for settings in (MeasurementSettings(flashes=5), MeasurementSettings(well_scan="orbital")):
      with pytest.raises(ValueError):
        await device.luminescence.measure(measurement_settings=settings)
    assert device.driver.io.commands == []  # type: ignore[attr-defined]


if __name__ == "__main__":
  unittest.main()
