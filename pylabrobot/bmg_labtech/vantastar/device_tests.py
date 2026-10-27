"""Tests for the VANTAstar device: plate geometry, well order and results, through the driver's fake
USB link with the reader's captured replies."""

import unittest
from collections import deque
from typing import Deque, List

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
  VOYAGER_FLUORESCENCE_ROW_A,
  VOYAGER_OD600,
)
from pylabrobot.resources import cor_96_wellplate_360uL_Fb


def make_device(pages: List[bytes]) -> VANTAstarDevice:
  """A device on the fake link: idle status, the RUN reply, then ``pages`` one per data request."""
  page_queue: Deque[bytes] = deque(pages)
  block = deque([master_tests.MW_BLOCK_PAGE_READY] * len(pages))

  def respond(payload: bytes) -> bytes:
    if payload[0] == 0x80:
      return master_tests.STATUS_IDLE
    if payload == master_tests.MW_BLOCK_STATUS:
      return block.popleft()
    if payload == master_tests.VALUES_CURRENT_BLOCK:
      return page_queue.popleft()
    return master_tests.RUN_REPLY

  device = VANTAstarDevice(io=master_tests.FakeUSB(respond))
  device.driver.default_poll_interval = 0
  device.driver.measurement_poll_interval = 0
  device.plate_holder._drawer_open = True
  device.plate_holder.assign_child_resource(cor_96_wellplate_360uL_Fb(name="plate"))
  device.plate_holder._drawer_open = False
  return device


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


if __name__ == "__main__":
  unittest.main()
