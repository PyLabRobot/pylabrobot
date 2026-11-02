"""Fluorescence tests through the device, over the driver's fake USB link with the reader's captured
replies, against the values Voyager exported for the same reads."""

import unittest
from typing import Any, Dict

from pylabrobot.bmg_labtech.vantastar.device_tests import (
  PHOTOCATALYST_WELLS,
  make_device,
  make_device_answering,
  make_recorded_device,
)
from pylabrobot.bmg_labtech.vantastar.driver import master_tests
from pylabrobot.bmg_labtech.vantastar.driver.responses_tests import (
  FLUORESCENCE_LAST_PAGE,
  GAIN_DATA,
  VOYAGER_FLUORESCENCE_ROW_A,
  load_flows,
)


class FluorescenceTests(unittest.IsolatedAsyncioTestCase):
  """Fluorescence reads: an endpoint read, and a recorded emission spectrum of a photocatalyst plate
  (``load_flows``)."""

  flows: Dict[str, Any]

  @classmethod
  def setUpClass(cls):
    """Load the recorded reads once for all the tests."""
    cls.flows = load_flows()

  async def test_fluorescence_results(self):
    """A fluorescence read gives Voyager's counts for row A and the temperature."""
    device = make_device([FLUORESCENCE_LAST_PAGE])
    result = await device.fluorescence.measure(
      365, 400, excitation_bandwidth=8, emission_bandwidth=10, focal_height=5.0, gain=500
    )
    self.assertEqual([result.data[f"A{c}"] for c in range(1, 13)], VOYAGER_FLUORESCENCE_ROW_A)
    self.assertEqual(result.temperature, 26.3)

  async def test_emission_spectrum(self):
    """An emission spectrum of H1, H6 and H12 gives counts whose H6 to H1 ratio matches Voyager's
    export wherever both exceed 1000 counts."""
    device = make_recorded_device("emission_spectrum")
    plate = device.plate_holder.resource
    assert plate is not None
    result = await device.fluorescence.measure_spectrum(
      "emission",
      500,
      700,
      excitation_wavelength=450,
      excitation_bandwidth=16,
      emission_bandwidth=30,
      gain=800,
      focal_height=6.6,
      dichroic_split_wavelength=471.5,
      wells=plate.get_items(["H1", "H6", "H12"]),
    )
    self.assertEqual(list(result.data), ["H1", "H6", "H12"])
    self.assertEqual((result.excitation_wavelength, result.emission_wavelength), (450, None))
    # Voyager exports the counts times its instrument correction for each wavelength; the ratios
    # between wells do not depend on it.
    voyager = self.flows["emission_spectrum"]["voyager_counts"]
    for i, wavelength in enumerate(range(500, 701)):
      h1, h6 = result.data["H1"][wavelength], result.data["H6"][wavelength]
      if h1 > 1000 and h6 > 1000:
        self.assertAlmostEqual(h6 / h1, voyager["H6"][i] / voyager["H1"][i], places=2)


class FluorescenceArgumentTests(unittest.IsolatedAsyncioTestCase):
  """Fluorescence arguments refused before anything is sent."""

  async def test_optics_out_of_the_limits_are_refused(self):
    """Fluorescence optics out of the limits are refused before anything is sent: a bandwidth below
    8 nm, an excitation below 320 nm, an emission overlapping the excitation."""
    device = make_device([])
    for excitation, excitation_bandwidth, emission, emission_bandwidth in (
      (365, 5, 400, 10),  # bandwidth below 8 nm
      (300, 8, 400, 10),  # excitation below 320 nm
      (390, 20, 400, 10),  # emission overlaps excitation
    ):
      with self.assertRaises(ValueError):
        await device.fluorescence.measure(
          excitation,
          emission,
          excitation_bandwidth=excitation_bandwidth,
          emission_bandwidth=emission_bandwidth,
          focal_height=5.0,
          gain=500,
        )
    self.assertEqual(device.driver.io.commands, [])  # type: ignore[attr-defined]

  async def test_fluorescence_spectrum_takes_one_fixed_wavelength(self):
    """A fluorescence spectrum is refused without a fixed wavelength, or with one on both sides."""
    device = make_device([])
    for excitation, emission in ((None, None), (450, 600)):
      with self.assertRaises(ValueError):
        await device.fluorescence.measure_spectrum(
          "emission",
          500,
          700,
          excitation_wavelength=excitation,
          emission_wavelength=emission,
          excitation_bandwidth=16,
          emission_bandwidth=30,
          gain=800,
          focal_height=6.6,
        )
    self.assertEqual(device.driver.io.commands, [])  # type: ignore[attr-defined]

  async def test_fluorescence_spectrum_refuses_an_overlap_at_either_end(self):
    """A fluorescence spectrum whose bands overlap at its start is refused: emission from 470 nm
    against excitation 450/16."""
    device = make_device([])
    with self.assertRaises(ValueError):  # emission from 470 nm overlaps excitation 450/16
      await device.fluorescence.measure_spectrum(
        "emission",
        470,
        700,
        excitation_wavelength=450,
        excitation_bandwidth=16,
        emission_bandwidth=30,
        gain=800,
        focal_height=6.6,
      )
    self.assertEqual(device.driver.io.commands, [])  # type: ignore[attr-defined]


class GainFocusTests(unittest.IsolatedAsyncioTestCase):
  """The gain and focus adjustment."""

  async def test_adjust_something(self):
    """A gain and focus adjustment that adjusts neither is refused before anything is sent."""
    device = make_device([])
    plate = device.plate_holder.resource
    assert plate is not None
    with self.assertRaises(ValueError):
      await device.fluorescence.adjust_gain_and_focus(
        365,
        400,
        excitation_bandwidth=8,
        emission_bandwidth=10,
        wells=plate.get_items(["A1"]),
        adjust_gain=False,
        adjust_focus=False,
      )
    self.assertEqual(device.driver.io.commands, [])  # type: ignore[attr-defined]

  async def test_focus_alone_sends_no_gain_target(self):
    """A focus adjustment alone sends the focus flags and a gain target of 0."""

    def respond(payload: bytes) -> bytes:
      """Answer the gain data request with the gain data, anything else with an idle status."""
      return GAIN_DATA if payload == master_tests.VALUES_GAIN_DATA else master_tests.STATUS_IDLE

    device = make_device_answering(respond)
    plate = device.plate_holder.resource
    assert plate is not None
    await device.fluorescence.adjust_gain_and_focus(
      365,
      400,
      excitation_bandwidth=8,
      emission_bandwidth=10,
      wells=plate.get_items(PHOTOCATALYST_WELLS),
      adjust_gain=False,
    )
    (command,) = [c for c in device.driver.io.commands if c[0] == 0x0C]  # type: ignore[attr-defined]
    self.assertEqual(command[1 + 17 + 48 : 1 + 17 + 48 + 2], bytes([0x20, 0x40]))  # with the focus
    self.assertEqual(command[1 + 17 + 48 + 11 : 1 + 17 + 48 + 13], bytes(2))  # target 0


class FocalHeightTests(unittest.IsolatedAsyncioTestCase):
  """The focal height a read takes when none is given."""

  def test_default_is_a_quarter_of_the_well_depth(self):
    """The default is 25 % of the wells' depth above their floor: 6.2 mm on the 96-well plate
    (cor_96_wellplate_360uL_Fb); a given focal height is kept."""
    device = make_device([])
    plate = device.plate_holder.resource
    assert plate is not None
    self.assertEqual(device.fluorescence._get_focal_height(None, plate, None), 6.2)  # type: ignore[arg-type]
    self.assertEqual(device.fluorescence._get_focal_height(5.0, plate, None), 5.0)  # type: ignore[arg-type]

  async def test_a_read_without_focal_height_sends_the_default(self):
    """A read without a focal height sends the RUN of a read at the default, 6.2 mm."""
    runs = []
    for focal_height in (None, 6.2):
      device = make_device([FLUORESCENCE_LAST_PAGE])
      await device.fluorescence.measure(
        365, 400, excitation_bandwidth=8, emission_bandwidth=10, focal_height=focal_height, gain=500
      )
      runs.append([c for c in device.driver.io.commands if c[0] == 0x04])  # type: ignore[attr-defined]
    self.assertEqual(runs[0], runs[1])

  def test_the_percent_is_checked(self):
    """A default percent outside 0-100 is refused."""
    device = make_device([])
    with self.assertRaises(ValueError):
      device.fluorescence.default_focal_height_percent_of_well_depth = 101


if __name__ == "__main__":
  unittest.main()
