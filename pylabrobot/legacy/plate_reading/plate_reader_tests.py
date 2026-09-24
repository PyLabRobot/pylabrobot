import unittest

from pylabrobot.legacy.plate_reading import PlateReader
from pylabrobot.legacy.plate_reading.backend import ImagerBackend
from pylabrobot.legacy.plate_reading.chatterbox import PlateReaderChatterboxBackend
from pylabrobot.legacy.plate_reading.imager import Imager
from pylabrobot.legacy.plate_reading.standard import ImagingMode, ImagingResult, Objective
from pylabrobot.resources import Coordinate, Plate
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb


class TestPlateReaderResource(unittest.TestCase):
  """Test plate reader as a resource."""

  def setUp(self) -> None:
    super().setUp()
    self.pr = PlateReader(
      name="pr",
      backend=PlateReaderChatterboxBackend(),
      size_x=1,
      size_y=1,
      size_z=1,
    )

  def test_add_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    self.pr.assign_child_resource(plate)

  def test_add_plate_full(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    self.pr.assign_child_resource(plate)

    another_plate = Plate("another_plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    with self.assertRaises(ValueError):
      self.pr.assign_child_resource(another_plate)

  def test_get_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    self.pr.assign_child_resource(plate)

    self.assertEqual(self.pr.get_plate(), plate)


class _RecordingImagerBackend(ImagerBackend):
  """Records the (row, column) each capture is dispatched to."""

  last_capture = None

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def capture(self, row, column, mode, objective, exposure_time, focal_height, gain, plate):
    self.last_capture = (row, column)
    return ImagingResult(images=[], exposure_time=10, focal_height=0)


class TestImagerWellIndexing(unittest.IsolatedAsyncioTestCase):
  """A Well object must resolve to the same (row, column) as the equivalent tuple."""

  async def test_well_object_resolves_to_row_column(self):
    backend = _RecordingImagerBackend()
    imager = Imager(name="imager", size_x=1, size_y=1, size_z=1, backend=backend)
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    imager.assign_child_resource(plate, location=Coordinate.zero())
    await imager.setup()

    # plate items are ordered column-major (A1, B1, ..., H1, A2, ...), so e.g. B2 has
    # index 9, which must decode to row 1, column 1
    for name, expected in [("A1", (0, 0)), ("B2", (1, 1)), ("H1", (7, 0)), ("A12", (0, 11))]:
      await imager.capture(
        well=plate.get_well(name),
        mode=ImagingMode.BRIGHTFIELD,
        objective=Objective.O_4X_PL_FL,
        exposure_time=10,
        focal_height=1,
        gain=1,
      )
      self.assertEqual(backend.last_capture, expected)
