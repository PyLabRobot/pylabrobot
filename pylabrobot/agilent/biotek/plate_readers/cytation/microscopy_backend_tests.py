import unittest
import unittest.mock

import pytest

pytest.importorskip("numpy")

import numpy as np

from pylabrobot.agilent.biotek.plate_readers.cytation.microscopy_backend import (
  COLOR_BRIGHTFIELD_LED_CODES,
  CytationMicroscopyBackend,
)
from pylabrobot.capabilities.microscopy.standard import ImagingMode


def _make_backend(version: str = "2.07"):
  """A microscopy backend with a fully mocked serial driver (no hardware)."""
  driver = unittest.mock.MagicMock()
  driver.version = version
  driver.send_command = unittest.mock.AsyncMock()
  backend = CytationMicroscopyBackend(driver=driver, use_cam=False)
  return backend, driver


class TestColorBrightfield(unittest.IsolatedAsyncioTestCase):
  """Color brightfield illuminates with three LEDs (codes 6/7/8) and stacks the frames."""

  def test_imaging_mode_code_uses_brightfield_light_path(self):
    backend, _ = _make_backend()
    # Color brightfield shares the brightfield filter cube (5); colors come from the LEDs.
    self.assertEqual(backend._imaging_mode_code(ImagingMode.COLOR_BRIGHTFIELD), 5)

  async def test_acquire_cycles_three_leds_and_stacks_rgb(self):
    backend, driver = _make_backend()
    frame = np.zeros((4, 5), dtype=np.uint8)
    backend.acquire_image = unittest.mock.AsyncMock(return_value=frame)

    image = await backend._acquire_color_brightfield_image(led_intensity=10)

    # One mono frame per color, stacked into an (H, W, 3) RGB image.
    self.assertEqual(image.shape, (4, 5, 3))
    self.assertEqual(backend.acquire_image.await_count, 3)

    # Per color, in cycle order: LED on (L0{code}10) then strobe (l{code}).
    expected = []
    for code in COLOR_BRIGHTFIELD_LED_CODES:
      expected.append(unittest.mock.call("i", f"L0{code}10"))
      expected.append(unittest.mock.call("i", f"l{code}"))
    self.assertEqual(driver.send_command.await_args_list, expected)

  async def test_set_imaging_mode_matches_brightfield_setup_without_static_led(self):
    backend, driver = _make_backend(version="2.07")
    backend._imaging_mode = None

    await backend.set_imaging_mode(ImagingMode.COLOR_BRIGHTFIELD, led_intensity=10)

    sent = [call.args for call in driver.send_command.await_args_list]
    # Same optics setup as plain brightfield (filter cube 5).
    for cmd in (("Y", "P1101"), ("Y", "P0d05"), ("Y", "P1002")):
      self.assertIn(cmd, sent)
    # No single LED is enabled (colors are cycled during acquisition): only led_off (L0001).
    self.assertEqual([p for (ch, p) in sent if ch == "i"], ["L0001"])
    self.assertEqual(backend._imaging_mode, ImagingMode.COLOR_BRIGHTFIELD)

  async def test_color_brightfield_not_supported_on_cytation1(self):
    backend, _ = _make_backend(version="1.05")
    backend._imaging_mode = None
    with self.assertRaises(NotImplementedError):
      await backend.set_imaging_mode(ImagingMode.COLOR_BRIGHTFIELD, led_intensity=10)


if __name__ == "__main__":
  unittest.main()
