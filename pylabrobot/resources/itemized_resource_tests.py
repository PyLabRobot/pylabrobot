import sys
import unittest
from typing import List

from pylabrobot.resources import (
  Coordinate,
  ItemizedResource,
  Plate,
  Resource,
  Well,
  create_equally_spaced_2d,
  create_ordered_items_2d,
)

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


class TestItemizedResource(unittest.TestCase):
  def setUp(self) -> None:
    self.plate = Plate(
      "plate",
      size_x=1,
      size_y=1,
      size_z=1,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=12,
        num_items_y=8,
        dx=0,
        dy=0,
        dz=0,
        item_dx=9,
        item_dy=9,
        size_x=9,
        size_y=9,
        size_z=9,
      ),
    )
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
    self.assertEqual(
      self.plate.get_item(0).get_absolute_location(),
      Coordinate(0, 63, 0),
    )
    self.assertEqual(
      self.plate.get_item(7).get_absolute_location(),
      Coordinate(0, 0, 0),
    )
    self.assertEqual(
      self.plate.get_item(8).get_absolute_location(),
      Coordinate(9, 63, 0),
    )
    self.assertEqual(
      self.plate.get_item(17).get_absolute_location(),
      Coordinate(18, 54, 0),
    )
    self.assertEqual(
      self.plate.get_item(95).get_absolute_location(),
      Coordinate(99, 0, 0),
    )

  def test_getitem_int(self):
    self.assertEqual(self.plate[0][0].name, "plate_well_0_0")

  def test_getitem_str(self):
    self.assertEqual(self.plate["A1"][0].name, "plate_well_0_0")
    self.assertEqual(self.plate["B2"][0].name, "plate_well_1_1")

  def test_getitem_slice(self):
    self.assertEqual(
      [w.name for w in self.plate[0:7]],
      [
        "plate_well_0_0",
        "plate_well_0_1",
        "plate_well_0_2",
        "plate_well_0_3",
        "plate_well_0_4",
        "plate_well_0_5",
        "plate_well_0_6",
      ],
    )

  def test_getitem_range(self):
    self.assertEqual(
      [w.name for w in self.plate[range(7)]],
      [
        "plate_well_0_0",
        "plate_well_0_1",
        "plate_well_0_2",
        "plate_well_0_3",
        "plate_well_0_4",
        "plate_well_0_5",
        "plate_well_0_6",
      ],
    )

  def test_getitem_str_range(self):
    self.assertEqual(
      [w.name for w in self.plate["A1:B2"]],
      [
        "plate_well_0_0",
        "plate_well_1_0",
        "plate_well_0_1",
        "plate_well_1_1",
      ],
    )

  def test_getitem_str_error(self):
    with self.assertRaises(IndexError):
      _ = self.plate["A13"]
    with self.assertRaises(IndexError):
      _ = self.plate["T1"]

  def test_getitem_tuple_int(self):
    self.assertEqual(
      [w.name for w in self.plate[0, 4, 1]],
      ["plate_well_0_0", "plate_well_0_4", "plate_well_0_1"],
    )

  def test_getitem_tuple_str(self):
    self.assertEqual(
      [w.name for w in self.plate["A1", "B2", "A2"]],
      ["plate_well_0_0", "plate_well_1_1", "plate_well_1_0"],
    )

  def test_get_row(self):
    self.assertEqual(
      [w.name for w in self.plate.row(0)],
      [
        "plate_well_0_0",
        "plate_well_1_0",
        "plate_well_2_0",
        "plate_well_3_0",
        "plate_well_4_0",
        "plate_well_5_0",
        "plate_well_6_0",
        "plate_well_7_0",
        "plate_well_8_0",
        "plate_well_9_0",
        "plate_well_10_0",
        "plate_well_11_0",
      ],
    )

    self.assertEqual(
      [w.name for w in self.plate.row(3)],
      [
        "plate_well_0_3",
        "plate_well_1_3",
        "plate_well_2_3",
        "plate_well_3_3",
        "plate_well_4_3",
        "plate_well_5_3",
        "plate_well_6_3",
        "plate_well_7_3",
        "plate_well_8_3",
        "plate_well_9_3",
        "plate_well_10_3",
        "plate_well_11_3",
      ],
    )

  def test_get_column(self):
    self.assertEqual(
      [w.name for w in self.plate.column(0)],
      [
        "plate_well_0_0",
        "plate_well_0_1",
        "plate_well_0_2",
        "plate_well_0_3",
        "plate_well_0_4",
        "plate_well_0_5",
        "plate_well_0_6",
        "plate_well_0_7",
      ],
    )

    self.assertEqual(
      [w.name for w in self.plate.column(3)],
      [
        "plate_well_3_0",
        "plate_well_3_1",
        "plate_well_3_2",
        "plate_well_3_3",
        "plate_well_3_4",
        "plate_well_3_5",
        "plate_well_3_6",
        "plate_well_3_7",
      ],
    )


class TestCreateEquallySpaced(unittest.TestCase):
  """Test for create_ordered_items_2d function."""

  def test_create_equally_spaced(self):
    self.maxDiff = None
    equally_spaced = create_equally_spaced_2d(
      Well,
      num_items_x=3,
      num_items_y=2,
      dx=0,
      dy=0,
      dz=0,
      item_dx=9,
      item_dy=9,
      size_x=9,
      size_y=9,
      size_z=9,
    )

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
    correct_items[0][0].location = Coordinate(0, 9, 0)
    correct_items[0][1].location = Coordinate(0, 0, 0)
    correct_items[1][0].location = Coordinate(9, 9, 0)
    correct_items[1][1].location = Coordinate(9, 0, 0)
    correct_items[2][0].location = Coordinate(18, 9, 0)
    correct_items[2][1].location = Coordinate(18, 0, 0)

    self.assertEqual(equally_spaced, correct_items)


class TestItemizedResourceTraversal(unittest.TestCase):
  def setUp(self) -> None:
    def item_id(row, column):
      return "ABC"[row] + str(column + 1)

    items = [
      Resource(name=item_id(row, col), size_x=1, size_y=1, size_z=1).at(Coordinate(col, row, 0))
      for col in range(3)
      for row in range(3)
    ]
    self.ir = ItemizedResource(
      "test_resource",
      ordered_items={item.name: item for item in items},
      size_x=3,
      size_y=3,
      size_z=1,
    )

  def _traverse_test(
    self,
    direction: Literal[
      "up",
      "down",
      "right",
      "left",
      "snake_up",
      "snake_down",
      "snake_left",
      "snake_right",
    ],
    start: Literal["top_left", "bottom_left", "top_right", "bottom_right"],
    pattern: List[str],
  ):
    items: List[Well] = []
    for wells in self.ir.traverse(batch_size=1, direction=direction, start=start, repeat=False):
      items.extend(wells)

    self.assertEqual(len(items), len(pattern))
    for w, identifier in zip(items, pattern):
      self.assertEqual(w.name, w.parent.name + "_" + identifier)

  def test_traverse_down_top_left(self):
    self._traverse_test(
      direction="down",
      start="top_left",
      pattern=["A1", "B1", "C1", "A2", "B2", "C2", "A3", "B3", "C3"],
    )

  def test_traverse_down_top_right(self):
    self._traverse_test(
      direction="down",
      start="top_right",
      pattern=["A3", "B3", "C3", "A2", "B2", "C2", "A1", "B1", "C1"],
    )

  def test_traverse_up_bottom_left(self):
    self._traverse_test(
      direction="up",
      start="bottom_left",
      pattern=["C1", "B1", "A1", "C2", "B2", "A2", "C3", "B3", "A3"],
    )

  def test_traverse_up_bottom_right(self):
    self._traverse_test(
      direction="up",
      start="bottom_right",
      pattern=["C3", "B3", "A3", "C2", "B2", "A2", "C1", "B1", "A1"],
    )

  def test_traverse_right_top_left(self):
    self._traverse_test(
      direction="right",
      start="top_left",
      pattern=["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "C3"],
    )

  def test_traverse_right_bottom_left(self):
    self._traverse_test(
      direction="right",
      start="bottom_left",
      pattern=["C1", "C2", "C3", "B1", "B2", "B3", "A1", "A2", "A3"],
    )

  def test_traverse_left_top_right(self):
    self._traverse_test(
      direction="left",
      start="top_right",
      pattern=["A3", "A2", "A1", "B3", "B2", "B1", "C3", "C2", "C1"],
    )

  def test_traverse_left_bottom_right(self):
    self._traverse_test(
      direction="left",
      start="bottom_right",
      pattern=["C3", "C2", "C1", "B3", "B2", "B1", "A3", "A2", "A1"],
    )

  def test_traverse_snake_down_top_left(self):
    self._traverse_test(
      direction="snake_down",
      start="top_left",
      pattern=["A1", "B1", "C1", "C2", "B2", "A2", "A3", "B3", "C3"],
    )

  def test_traverse_snake_down_top_right(self):
    self._traverse_test(
      direction="snake_down",
      start="top_right",
      pattern=["A3", "B3", "C3", "C2", "B2", "A2", "A1", "B1", "C1"],
    )

  def test_traverse_snake_up_bottom_left(self):
    self._traverse_test(
      direction="snake_up",
      start="bottom_left",
      pattern=["C1", "B1", "A1", "A2", "B2", "C2", "C3", "B3", "A3"],
    )

  def test_traverse_snake_up_bottom_right(self):
    self._traverse_test(
      direction="snake_up",
      start="bottom_right",
      pattern=["C3", "B3", "A3", "A2", "B2", "C2", "C1", "B1", "A1"],
    )

  def test_traverse_snake_right_top_left(self):
    self._traverse_test(
      direction="snake_right",
      start="top_left",
      pattern=["A1", "A2", "A3", "B3", "B2", "B1", "C1", "C2", "C3"],
    )

  def test_traverse_snake_right_bottom_left(self):
    self._traverse_test(
      direction="snake_right",
      start="bottom_left",
      pattern=["C1", "C2", "C3", "B3", "B2", "B1", "A1", "A2", "A3"],
    )

  def test_traverse_snake_left_top_right(self):
    self._traverse_test(
      direction="snake_left",
      start="top_right",
      pattern=["A3", "A2", "A1", "B1", "B2", "B3", "C3", "C2", "C1"],
    )

  def test_traverse_snake_left_bottom_right(self):
    self._traverse_test(
      direction="snake_left",
      start="bottom_right",
      pattern=["C3", "C2", "C1", "B1", "B2", "B3", "A3", "A2", "A1"],
    )

  # def test_traverse_down_repeat(self):
  #   # do a down traversal with batch size 5, which does not divide 96. `ItemizedResource.traverse`
  #   # should continue the batch from the beginning of the resource.
  #   # Go over the resource 10 times in total.

  #   items = []
  #   num_rounds = 10
  #   total_num_batches = 96 * num_rounds // 5  # 10 rounds, batch size 5
  #   num_batches = 0
  #   for wells in self.plate.traverse(batch_size=5, direction="down_right", repeat=True):
  #     if num_batches == total_num_batches:
  #       break
  #     items.extend(wells)
  #     self.assertEqual(len(wells), 5)
  #     num_batches += 1
  #   self.assertEqual(len(items), self.plate.num_items * num_rounds)
