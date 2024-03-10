import sys
from typing import List
import unittest

from pylabrobot.resources import (
  Coordinate,
  Plate,
  Well,
  create_equally_spaced
)

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


class TestItemizedResource(unittest.TestCase):
  """ Tests for ItemizedResource """

  def setUp(self) -> None:
    self.plate = Plate("plate", size_x=1, size_y=1, size_z=1, lid_height=10,
      items=create_equally_spaced(Well,
      num_items_x=12, num_items_y=8,
      dx=0, dy=0, dz=0,
      item_dx=9, item_dy=9,
      size_x=9, size_y=9, size_z=9))
    self.plate.location = Coordinate.zero()
    return super().setUp()

  def test_initialize_with_wells(self):
    self.assertEqual(len(self.plate.children), 96)
    self.assertEqual(self.plate.children[0].name, "plate_well_0_0")
    self.assertEqual(self.plate.children[95].name, "plate_well_11_7")

  def test_get_item_int(self):
    self.assertEqual(self.plate.get_item(0).name, "plate_well_0_0")
    self.assertEqual(self.plate.get_item(95).name, "plate_well_11_7")

  def test_get_item_str(self):
    self.assertEqual(self.plate.get_item("A1").name, "plate_well_0_0")
    self.assertEqual(self.plate.get_item("B1").name, "plate_well_0_1")
    self.assertEqual(self.plate.get_item("A2").name, "plate_well_1_0")

  def test_get_item_tuple(self):
    self.assertEqual(self.plate.get_item((0, 0)).name, "plate_well_0_0")
    self.assertEqual(self.plate.get_item((7, 11)).name, "plate_well_11_7")

  def test_well_get_absolute_location(self):
    self.assertEqual(self.plate.get_item(0).get_absolute_location(),
      Coordinate(0, 63, 0))
    self.assertEqual(self.plate.get_item(7).get_absolute_location(),
      Coordinate(0, 0, 0))
    self.assertEqual(self.plate.get_item(8).get_absolute_location(),
      Coordinate(9, 63, 0))
    self.assertEqual(self.plate.get_item(17).get_absolute_location(),
      Coordinate(18, 54, 0))
    self.assertEqual(self.plate.get_item(95).get_absolute_location(),
      Coordinate(99, 0, 0))

  def test_getitem_int(self):
    self.assertEqual(self.plate[0][0].name, "plate_well_0_0")

  def test_getitem_str(self):
    self.assertEqual(self.plate["A1"][0].name, "plate_well_0_0")
    self.assertEqual(self.plate["B2"][0].name, "plate_well_1_1")

  def test_getitem_slice(self):
    self.assertEqual([w.name for w in self.plate[0:7]],
      ["plate_well_0_0", "plate_well_0_1", "plate_well_0_2", "plate_well_0_3",
        "plate_well_0_4", "plate_well_0_5", "plate_well_0_6"])

  def test_getitem_range(self):
    self.assertEqual([w.name for w in self.plate[range(7)]],
      ["plate_well_0_0", "plate_well_0_1", "plate_well_0_2", "plate_well_0_3",
        "plate_well_0_4", "plate_well_0_5", "plate_well_0_6"])

  def test_getitem_str_range(self):
    self.assertEqual([w.name for w in self.plate["A1:B2"]],
      ["plate_well_0_0", "plate_well_1_0", "plate_well_0_1", "plate_well_1_1"])

  def test_getitem_str_error(self):
    with self.assertRaises(IndexError):
      _ = self.plate["A13"]
    with self.assertRaises(IndexError):
      _ = self.plate["T1"]

  def test_getitem_tuple_int(self):
    self.assertEqual([w.name for w in self.plate[0, 4, 1]],
      ["plate_well_0_0", "plate_well_0_4", "plate_well_0_1"])

  def test_getitem_tuple_str(self):
    self.assertEqual([w.name for w in self.plate["A1", "B2", "A2"]],
      ["plate_well_0_0", "plate_well_1_1", "plate_well_1_0"])

  def _traverse_test(
    self,
    direction: Literal["up", "down", "right", "left",
                       "snake_up", "snake_down", "snake_left", "snake_right"],
    pattern: List[int]):
    items: List[Well] = []
    for wells in self.plate.traverse(batch_size=2, direction=direction, repeat=False):
      self.assertEqual(len(wells), 2)
      items.extend(wells)

    self.assertEqual(len(items), len(pattern))
    for w, idx in zip(items, pattern):
      self.assertEqual(w, self.plate.get_well(idx))

  def test_traverse_down(self):
    pattern = list(range(self.plate.num_items))
    self._traverse_test("down", pattern)

  def test_traverse_up(self):
    pattern = [7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8, 23, 22, 21, 20, 19, 18, 17, 16,
               31, 30, 29, 28, 27, 26, 25, 24, 39, 38, 37, 36, 35, 34, 33, 32, 47, 46, 45, 44, 43,
               42, 41, 40, 55, 54, 53, 52, 51, 50, 49, 48, 63, 62, 61, 60, 59, 58, 57, 56, 71, 70,
               69, 68, 67, 66, 65, 64, 79, 78, 77, 76, 75, 74, 73, 72, 87, 86, 85, 84, 83, 82, 81,
               80, 95, 94, 93, 92, 91, 90, 89, 88]
    self._traverse_test("up", pattern)

  def test_traverse_left(self):
    pattern = [88, 80, 72, 64, 56, 48, 40, 32, 24, 16, 8, 0, 89, 81, 73, 65, 57, 49, 41, 33, 25, 17,
               9, 1, 90, 82, 74, 66, 58, 50, 42, 34, 26, 18, 10, 2, 91, 83, 75, 67, 59, 51, 43, 35,
               27, 19, 11, 3, 92, 84, 76, 68, 60, 52, 44, 36, 28, 20, 12, 4, 93, 85, 77, 69, 61, 53,
               45, 37, 29, 21, 13, 5, 94, 86, 78, 70, 62, 54, 46, 38, 30, 22, 14, 6, 95, 87, 79, 71,
               63, 55, 47, 39, 31, 23, 15, 7]
    self._traverse_test("left", pattern)

  def test_traverse_right(self):
    pattern = [0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 1, 9, 17, 25, 33, 41, 49, 57, 65, 73,
               81, 89, 2, 10, 18, 26, 34, 42, 50, 58, 66, 74, 82, 90, 3, 11, 19, 27, 35, 43, 51, 59,
               67, 75, 83, 91, 4, 12, 20, 28, 36, 44, 52, 60, 68, 76, 84, 92, 5, 13, 21, 29, 37, 45,
               53, 61, 69, 77, 85, 93, 6, 14, 22, 30, 38, 46, 54, 62, 70, 78, 86, 94, 7, 15, 23, 31,
               39, 47, 55, 63, 71, 79, 87, 95]
    self._traverse_test("right", pattern)

  def test_traverse_snake_right(self):
    pattern = [0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 89, 81, 73, 65, 57, 49, 41, 33, 25, 17,
               9, 1, 2, 10, 18, 26, 34, 42, 50, 58, 66, 74, 82, 90, 91, 83, 75, 67, 59, 51, 43, 35,
               27, 19, 11, 3, 4, 12, 20, 28, 36, 44, 52, 60, 68, 76, 84, 92, 93, 85, 77, 69, 61, 53,
               45, 37, 29, 21, 13, 5, 6, 14, 22, 30, 38, 46, 54, 62, 70, 78, 86, 94, 95, 87, 79, 71,
               63, 55, 47, 39, 31, 23, 15, 7]
    self._traverse_test("snake_right", pattern)

  def test_traverse_snake_down(self):
    pattern = [0, 1, 2, 3, 4, 5, 6, 7, 15, 14, 13, 12, 11, 10, 9, 8, 16, 17, 18, 19, 20, 21, 22, 23,
               31, 30, 29, 28, 27, 26, 25, 24, 32, 33, 34, 35, 36, 37, 38, 39, 47, 46, 45, 44, 43,
               42, 41, 40, 48, 49, 50, 51, 52, 53, 54, 55, 63, 62, 61, 60, 59, 58, 57, 56, 64, 65,
               66, 67, 68, 69, 70, 71, 79, 78, 77, 76, 75, 74, 73, 72, 80, 81, 82, 83, 84, 85, 86,
               87, 95, 94, 93, 92, 91, 90, 89, 88]
    self._traverse_test("snake_down", pattern)

  def test_traverse_snake_left(self):
    pattern = [88, 80, 72, 64, 56, 48, 40, 32, 24, 16, 8, 0, 1, 9, 17, 25, 33, 41, 49, 57, 65, 73,
               81, 89, 90, 82, 74, 66, 58, 50, 42, 34, 26, 18, 10, 2, 3, 11, 19, 27, 35, 43, 51, 59,
               67, 75, 83, 91, 92, 84, 76, 68, 60, 52, 44, 36, 28, 20, 12, 4, 5, 13, 21, 29, 37, 45,
               53, 61, 69, 77, 85, 93, 94, 86, 78, 70, 62, 54, 46, 38, 30, 22, 14, 6, 7, 15, 23, 31,
               39, 47, 55, 63, 71, 79, 87, 95]
    self._traverse_test("snake_left", pattern)

  def test_traverse_snake_up(self):
    pattern = [7, 6, 5, 4, 3, 2, 1, 0, 8, 9, 10, 11, 12, 13, 14, 15, 23, 22, 21, 20, 19, 18, 17, 16,
               24, 25, 26, 27, 28, 29, 30, 31, 39, 38, 37, 36, 35, 34, 33, 32, 40, 41, 42, 43, 44,
               45, 46, 47, 55, 54, 53, 52, 51, 50, 49, 48, 56, 57, 58, 59, 60, 61, 62, 63, 71, 70,
               69, 68, 67, 66, 65, 64, 72, 73, 74, 75, 76, 77, 78, 79, 87, 86, 85, 84, 83, 82, 81,
               80, 88, 89, 90, 91, 92, 93, 94, 95]
    self._traverse_test("snake_up", pattern)

  def test_travserse_down_repeat(self):
    # do a down traversal with batch size 5, which does not divide 96. `ItemizedResource.traverse`
    # should continue the batch from the beginning of the resource.
    # Go over the resource 10 times in total.

    items = []
    num_rounds = 10
    total_num_batches = 96*num_rounds // 5 # 10 rounds, batch size 5
    num_batches = 0
    for wells in self.plate.traverse(batch_size=5, direction="down", repeat=True):
      if num_batches == total_num_batches:
        break
      items.extend(wells)
      self.assertEqual(len(wells), 5)
      num_batches += 1
    self.assertEqual(len(items), self.plate.num_items*num_rounds)


class TestCreateEquallySpaced(unittest.TestCase):
  """ Test for create_equally_spaced function. """

  def test_create_equally_spaced(self):
    self.maxDiff = None
    equally_spaced = create_equally_spaced(Well,
      num_items_x=3, num_items_y=2,
      dx=0, dy=0, dz=0,
      item_dx=9, item_dy=9,
      size_x=9, size_y=9, size_z=9)

    # assert that ids of items are correct
    ids = [id(item) for item in equally_spaced]
    self.assertEqual(len(ids), len(set(ids)))

    self.assertEqual(len(equally_spaced), 3)
    self.assertEqual(len(equally_spaced[0]), 2)
    self.assertEqual(len(equally_spaced[1]), 2)
    self.assertEqual(len(equally_spaced[2]), 2)

    correct_items = [
      [
        Well("well_0_0", size_x=9, size_y=9, size_z=9),
        Well("well_0_1", size_x=9, size_y=9, size_z=9),
      ],
      [
        Well("well_1_0", size_x=9, size_y=9, size_z=9),
        Well("well_1_1", size_x=9, size_y=9, size_z=9),
      ],
      [
        Well("well_2_0", size_x=9, size_y=9, size_z=9),
        Well("well_2_1", size_x=9, size_y=9, size_z=9),
      ],
    ]
    correct_items[0][0].location = Coordinate( 0, 9, 0)
    correct_items[0][1].location = Coordinate( 0, 0, 0)
    correct_items[1][0].location = Coordinate( 9, 9, 0)
    correct_items[1][1].location = Coordinate( 9, 0, 0)
    correct_items[2][0].location = Coordinate(18, 9, 0)
    correct_items[2][1].location = Coordinate(18, 0, 0)

    self.assertEqual(equally_spaced, correct_items)
