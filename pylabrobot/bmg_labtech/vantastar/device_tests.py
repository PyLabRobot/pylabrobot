"""Tests for the VANTAstar device: plate geometry, well order and results, through the driver's fake
USB link with the reader's captured replies."""

import unittest
from collections import deque
from typing import Any, Callable, Deque, Dict, List

from pylabrobot.bmg_labtech.vantastar.device import (
  VANTAstarDevice,
  _get_measured_wells,
  get_plate_geometry,
)
from pylabrobot.bmg_labtech.vantastar.driver import master_tests
from pylabrobot.bmg_labtech.vantastar.driver.responses_tests import (
  ABSORBANCE_FIRST_PAGE,
  ABSORBANCE_LAST_PAGE,
  FLUORESCENCE_LAST_PAGE,
  GAIN_DATA,
  VOYAGER_FLUORESCENCE_ROW_A,
  VOYAGER_OD600,
  load_flows,
)
from pylabrobot.resources import cor_96_wellplate_360uL_Fb

PHOTOCATALYST_WELLS = ["A1", "A6", "A12", "H1", "H6", "H12"]


def make_device_answering(respond: Callable[[bytes], bytes]) -> VANTAstarDevice:
  """A device on the fake link answering with ``respond``, a 96-well plate on its plate holder."""
  device = VANTAstarDevice(io=master_tests.FakeUSB(respond))
  device.driver.default_poll_interval = 0
  device.driver.measurement_poll_interval = 0
  device.plate_holder._drawer_open = True
  device.plate_holder.assign_child_resource(cor_96_wellplate_360uL_Fb(name="plate"))
  device.plate_holder._drawer_open = False
  return device


def make_device(pages: List[bytes]) -> VANTAstarDevice:
  """A device on the fake link: idle status, the RUN reply, the 96-well well list, then a page
  ready for each of ``pages`` but the last, and the block finished; ``pages`` one per data
  request."""
  page_queue: Deque[bytes] = deque(pages)
  block = deque(
    [master_tests.MW_BLOCK_PAGE_READY] * (len(pages) - 1) + [master_tests.MW_BLOCK_LAST_PAGE]
  )

  def respond(payload: bytes) -> bytes:
    if payload[0] == 0x80:
      return master_tests.STATUS_IDLE
    if payload == master_tests.MW_BLOCK_STATUS:
      return block.popleft()
    if payload == master_tests.VALUES_WELL_LIST:
      return master_tests.WELL_LIST_96
    if payload in (master_tests.VALUES_CURRENT_BLOCK, master_tests.VALUES_NEXT_NEW_BLOCK):
      return page_queue.popleft()
    return master_tests.RUN_REPLY

  return make_device_answering(respond)


def make_recorded_device(name: str) -> VANTAstarDevice:
  """A device answering as the reader did in the recorded read ``name`` (``load_flows``)."""
  return make_device_answering(master_tests.RecordedReader(load_flows()[name]))


class PlateTests(unittest.TestCase):
  def test_geometry_from_the_back_left_corner(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    geometry = get_plate_geometry(plate)
    self.assertEqual((geometry.columns, geometry.rows), (12, 8))
    self.assertAlmostEqual(geometry.last_x - geometry.first_x, 99.0, places=1)
    self.assertAlmostEqual(geometry.last_y - geometry.first_y, 63.0, places=1)
    self.assertLess(geometry.first_y, geometry.width / 2)

  def test_measured_wells_are_in_row_order(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    wells = plate.get_items(["B2", "A3", "A1"])
    self.assertEqual(_get_measured_wells(plate, wells), [(0, 0), (0, 2), (1, 1)])


class MeasurementTests(unittest.IsolatedAsyncioTestCase):
  async def test_absorbance_results(self):
    device = make_device([ABSORBANCE_FIRST_PAGE, ABSORBANCE_LAST_PAGE])
    (result,) = await device.measure_absorbance([600])
    self.assertEqual(result.wavelength, 600)
    self.assertEqual(result.temperature, 26.0)
    self.assertEqual(list(result.data)[:13], [f"A{c}" for c in range(1, 13)] + ["B1"])
    self.assertEqual([round(v, 3) for v in result.data.values()], VOYAGER_OD600)

  async def test_fluorescence_results(self):
    device = make_device([FLUORESCENCE_LAST_PAGE])
    result = await device.measure_fluorescence(
      365, 400, excitation_bandwidth=8, emission_bandwidth=10, focal_height=5.0, gain=500
    )
    self.assertEqual([result.data[f"A{c}"] for c in range(1, 13)], VOYAGER_FLUORESCENCE_ROW_A)
    self.assertEqual(result.temperature, 26.3)

  async def test_optics_out_of_the_limits_are_refused(self):
    device = make_device([])
    for excitation, excitation_bandwidth, emission, emission_bandwidth in (
      (365, 5, 400, 10),  # bandwidth below 8 nm
      (300, 8, 400, 10),  # excitation below 320 nm
      (390, 20, 400, 10),  # emission overlaps excitation
    ):
      with self.assertRaises(ValueError):
        await device.measure_fluorescence(
          excitation,
          emission,
          excitation_bandwidth=excitation_bandwidth,
          emission_bandwidth=emission_bandwidth,
          focal_height=5.0,
          gain=500,
        )
    self.assertEqual(device.driver.io.commands, [])  # type: ignore[attr-defined]


class RecordedReadTests(unittest.IsolatedAsyncioTestCase):
  """The device's results from recorded reads of a photocatalyst plate (``load_flows``), against
  the values Voyager exported for the same reads."""

  flows: Dict[str, Any]

  @classmethod
  def setUpClass(cls):
    cls.flows = load_flows()

  async def test_absorbance_spectrum(self):
    device = make_recorded_device("absorbance_spectrum")
    plate = device.plate_holder.resource
    assert plate is not None
    result = await device.measure_absorbance_spectrum(
      220, 1000, wells=plate.get_items(PHOTOCATALYST_WELLS)
    )
    self.assertEqual(list(result.data), PHOTOCATALYST_WELLS)  # row by row
    voyager = self.flows["absorbance_spectrum"]["voyager_optical_densities"]
    for well, spectrum in result.data.items():
      self.assertEqual(list(spectrum), list(range(220, 1001)))
      # Voyager reports optical densities above 3.5 as 3.5.
      self.assertEqual([round(min(v, 3.5), 3) for v in spectrum.values()], voyager[well])

  async def test_absorbance_kinetic(self):
    device = make_recorded_device("absorbance_kinetic")
    plate = device.plate_holder.resource
    assert plate is not None
    cycles = await device.measure_absorbance_kinetic(
      [450], cycles=6, cycle_time=10, wells=plate.get_items(["A1", "A6", "A12"])
    )
    voyager = self.flows["absorbance_kinetic"]["voyager_optical_densities"]
    for k, (result,) in enumerate(cycles):
      self.assertEqual(
        {w: round(v, 3) for w, v in result.data.items()}, {w: voyager[w][k] for w in result.data}
      )
    starts = [result.start_timestamp for (result,) in cycles]
    self.assertEqual([(s - starts[0]).total_seconds() for s in starts], [0, 10, 20, 30, 40, 50])

  async def test_emission_spectrum(self):
    device = make_recorded_device("emission_spectrum")
    plate = device.plate_holder.resource
    assert plate is not None
    result = await device.measure_fluorescence_spectrum(
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

  async def test_absorbance_at_three_wavelengths(self):
    device = make_recorded_device("absorbance_three_wavelengths")
    results = await device.measure_absorbance([300, 450, 600])
    voyager = self.flows["absorbance_three_wavelengths"]["voyager_optical_densities"]
    for mode, result in enumerate(results, start=1):
      self.assertEqual(
        {w: round(min(v, 3.5), 3) for w, v in result.data.items()}, voyager[str(mode)]
      )


class ArgumentTests(unittest.IsolatedAsyncioTestCase):
  async def test_spectrum_step_must_divide_the_range(self):
    device = make_device([])
    with self.assertRaises(ValueError):
      await device.measure_absorbance_spectrum(220, 1000, 3)
    with self.assertRaises(ValueError):
      await device.measure_absorbance_spectrum(600, 500)

  async def test_fluorescence_spectrum_takes_one_fixed_wavelength(self):
    device = make_device([])
    for excitation, emission in ((None, None), (450, 600)):
      with self.assertRaises(ValueError):
        await device.measure_fluorescence_spectrum(
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

  async def test_fluorescence_spectrum_refuses_an_overlap_at_either_end(self):
    device = make_device([])
    with self.assertRaises(ValueError):  # emission from 470 nm overlaps excitation 450/16
      await device.measure_fluorescence_spectrum(
        "emission",
        470,
        700,
        excitation_wavelength=450,
        excitation_bandwidth=16,
        emission_bandwidth=30,
        gain=800,
        focal_height=6.6,
      )

  async def test_kinetic_needs_two_cycles(self):
    device = make_device([])
    with self.assertRaises(ValueError):
      await device.measure_absorbance_kinetic([450], cycles=1, cycle_time=10)

  async def test_adjust_something(self):
    device = make_device([])
    plate = device.plate_holder.resource
    assert plate is not None
    with self.assertRaises(ValueError):
      await device.adjust_gain_and_focus(
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
    def respond(payload: bytes) -> bytes:
      return GAIN_DATA if payload == master_tests.VALUES_GAIN_DATA else master_tests.STATUS_IDLE

    device = make_device_answering(respond)
    plate = device.plate_holder.resource
    assert plate is not None
    await device.adjust_gain_and_focus(
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


if __name__ == "__main__":
  unittest.main()
