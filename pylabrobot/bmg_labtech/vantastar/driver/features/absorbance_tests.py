"""Absorbance tests through the device, over the driver's fake USB link with the reader's captured
replies, against the values Voyager exported for the same reads."""

import unittest
from typing import Any, Dict
from unittest import mock

from pylabrobot.bmg_labtech.vantastar.device_tests import (
  PHOTOCATALYST_WELLS,
  make_device,
  make_recorded_device,
)
from pylabrobot.bmg_labtech.vantastar.driver import master_tests
from pylabrobot.bmg_labtech.vantastar.driver.features.measurement import (
  MeasurementSettings,
  ShakeSettings,
)
from pylabrobot.bmg_labtech.vantastar.driver.responses_tests import (
  ABSORBANCE_FIRST_PAGE,
  ABSORBANCE_LAST_PAGE,
  VOYAGER_OD600,
  load_flows,
)


class AbsorbanceTests(unittest.IsolatedAsyncioTestCase):
  """Absorbance reads: an endpoint read, and recorded reads of a photocatalyst plate
  (``load_flows``)."""

  flows: Dict[str, Any]

  @classmethod
  def setUpClass(cls):
    """Load the recorded reads once for all the tests."""
    cls.flows = load_flows()

  async def test_absorbance_results(self):
    """An absorbance read at 600 nm gives the temperature and Voyager's optical densities, keyed by
    well, row by row."""
    device = make_device([ABSORBANCE_FIRST_PAGE, ABSORBANCE_LAST_PAGE])
    (result,) = await device.absorbance.measure([600])
    self.assertEqual(result.wavelength, 600)
    self.assertEqual(result.temperature, 26.0)
    self.assertEqual(list(result.data)[:13], [f"A{c}" for c in range(1, 13)] + ["B1"])
    self.assertEqual([round(v, 3) for v in result.data.values()], VOYAGER_OD600)

  async def test_absorbance_at_three_wavelengths(self):
    """An absorbance read at 300, 450 and 600 nm gives the optical densities Voyager exported for
    each wavelength."""
    device = make_recorded_device("absorbance_three_wavelengths")
    results = await device.absorbance.measure([300, 450, 600])
    voyager = self.flows["absorbance_three_wavelengths"]["voyager_optical_densities"]
    for mode, result in enumerate(results, start=1):
      self.assertEqual(
        {w: round(min(v, 3.5), 3) for w, v in result.data.items()}, voyager[str(mode)]
      )

  async def test_absorbance_spectrum(self):
    """An absorbance spectrum gives each well's optical densities from 220 to 1000 nm as Voyager
    exported them, the wells row by row."""
    device = make_recorded_device("absorbance_spectrum")
    plate = device.plate_holder.resource
    assert plate is not None
    result = await device.absorbance.measure_spectrum(
      220, 1000, wells=plate.get_items(PHOTOCATALYST_WELLS)
    )
    self.assertEqual(list(result.data), PHOTOCATALYST_WELLS)  # row by row
    voyager = self.flows["absorbance_spectrum"]["voyager_optical_densities"]
    for well, spectrum in result.data.items():
      self.assertEqual(list(spectrum), list(range(220, 1001)))
      # Voyager reports optical densities above 3.5 as 3.5.
      self.assertEqual([round(min(v, 3.5), 3) for v in spectrum.values()], voyager[well])


class AbsorbanceArgumentTests(unittest.IsolatedAsyncioTestCase):
  """Absorbance arguments refused before anything is sent."""

  async def test_spectrum_step_must_divide_the_range(self):
    """An absorbance spectrum is refused before anything is sent when its step does not divide the
    range or its end is not above its start."""
    device = make_device([])
    with self.assertRaises(ValueError):
      await device.absorbance.measure_spectrum(220, 1000, 7)  # 780 nm is not a multiple of 7
    with self.assertRaises(ValueError):
      await device.absorbance.measure_spectrum(600, 500)
    self.assertEqual(device.driver.io.commands, [])  # type: ignore[attr-defined]

  async def test_limits_the_reader_refuses(self):
    """Settling times outside 0.02-1.0 s and more than 500 flashes, which the reader refuses (quit
    codes 43 and 60), are refused before anything is sent."""
    device = make_device([])
    for settling_time in (0, 1.04):
      with self.assertRaises(ValueError):
        await device.absorbance.measure(
          [450], measurement_settings=MeasurementSettings(settling_time=settling_time)
        )
    with self.assertRaises(ValueError):
      await device.absorbance.measure([450], measurement_settings=MeasurementSettings(flashes=501))
    self.assertEqual(device.driver.io.commands, [])  # type: ignore[attr-defined]


class ReadSettingsTests(unittest.IsolatedAsyncioTestCase):
  """Measurement and shake settings reaching the reader."""

  async def test_shake_settings_shake_right_before_the_read(self):
    """Shake settings send the shaker's command right before the RUN, as Voyager does."""
    device = make_device([ABSORBANCE_FIRST_PAGE, ABSORBANCE_LAST_PAGE])
    await device.absorbance.measure(
      [600], shake_settings=ShakeSettings(pattern="double_orbital", speed=300, duration=10)
    )
    sent = [c for c in master_tests.commands(device.driver) if c[0] in (0x27, 0x04)]
    self.assertEqual([c[0] for c in sent], [0x27, 0x04])
    self.assertEqual(sent[0], master_tests.SHAKE_DOUBLE_ORBITAL_300_10)

  async def test_reading_order_reaches_the_run(self):
    """The reading order sets the RUN's reading-mode byte: column by column from the bottom right
    corner, in one direction."""
    device = make_device([ABSORBANCE_FIRST_PAGE, ABSORBANCE_LAST_PAGE])
    settings = MeasurementSettings(
      corner="bottom_right", reading_direction="column_major", bidirectional=False
    )
    await device.absorbance.measure([600], measurement_settings=settings)
    (run,) = [c for c in master_tests.commands(device.driver) if c[0] == 0x04]
    self.assertEqual(run[67], 0x02 | 0x08 | 0x20 | 0x40 | 0x80)

  async def test_pause_before_reading(self):
    """A pause before reading waits that many seconds before the RUN."""
    device = make_device([ABSORBANCE_FIRST_PAGE, ABSORBANCE_LAST_PAGE])
    with mock.patch(
      "pylabrobot.bmg_labtech.vantastar.driver.features.measurement.asyncio.sleep",
      new_callable=mock.AsyncMock,
    ) as sleep:
      await device.absorbance.measure(
        [600], measurement_settings=MeasurementSettings(pause_before_reading=2)
      )
    sleep.assert_any_await(2)


if __name__ == "__main__":
  unittest.main()
