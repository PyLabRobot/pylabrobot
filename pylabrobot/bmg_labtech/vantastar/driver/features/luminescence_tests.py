"""Luminescence tests through the device, over the driver's fake USB link.  The read is not tested on
a reader with luminescence."""

import unittest

from pylabrobot.bmg_labtech.vantastar.device_tests import make_device
from pylabrobot.bmg_labtech.vantastar.driver.features.measurement import MeasurementSettings


class LuminescenceArgumentTests(unittest.IsolatedAsyncioTestCase):
  """Luminescence arguments refused before anything is sent."""

  async def test_flashes_and_well_scans_are_refused(self):
    """Measurement settings with flashes or a well scan are refused: a luminescence read integrates
    at the centre."""
    device = make_device([])
    for settings in (MeasurementSettings(flashes=5), MeasurementSettings(well_scan="orbital")):
      with self.subTest(settings=settings), self.assertRaises(ValueError):
        await device.luminescence.measure(measurement_settings=settings)
    self.assertEqual(device.driver.io.commands, [])  # type: ignore[attr-defined]


if __name__ == "__main__":
  unittest.main()
