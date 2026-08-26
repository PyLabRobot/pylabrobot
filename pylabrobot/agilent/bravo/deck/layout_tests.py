import unittest

from pylabrobot.agilent.bravo.deck.layout import DeckLayout

# Every 1-based deck location mapped to its expected 0-based (row, col).
_EXPECTED_ROW_COL = {
  1: (0, 0),
  2: (0, 1),
  3: (0, 2),
  4: (1, 0),
  5: (1, 1),
  6: (1, 2),
  7: (2, 0),
  8: (2, 1),
  9: (2, 2),
}


class GetRowColTests(unittest.TestCase):
  def test_every_location_maps_to_the_expected_row_col(self):
    for location, expected in _EXPECTED_ROW_COL.items():
      with self.subTest(location=location):
        self.assertEqual(DeckLayout.get_row_col(location), expected)

  def test_out_of_range_location_raises(self):
    with self.assertRaises(ValueError):
      DeckLayout.get_row_col(0)
    with self.assertRaises(ValueError):
      DeckLayout.get_row_col(10)


class GetLocationTests(unittest.TestCase):
  def test_every_row_col_maps_back_to_the_expected_location(self):
    for location, (row, col) in _EXPECTED_ROW_COL.items():
      with self.subTest(location=location):
        self.assertEqual(DeckLayout.get_location(row, col), location)

  def test_round_trips_with_get_row_col(self):
    for location in range(1, 10):
      row, col = DeckLayout.get_row_col(location)
      self.assertEqual(DeckLayout.get_location(row, col), location)


class GetAdjacentLocationsTests(unittest.TestCase):
  def test_center_has_all_eight_neighbours(self):
    self.assertEqual(
      sorted(DeckLayout.get_adjacent_locations(5)),
      [1, 2, 3, 4, 6, 7, 8, 9],
    )

  def test_corner_has_three_neighbours(self):
    self.assertEqual(sorted(DeckLayout.get_adjacent_locations(1)), [2, 4, 5])

  def test_edge_has_five_neighbours(self):
    self.assertEqual(sorted(DeckLayout.get_adjacent_locations(2)), [1, 3, 4, 5, 6])

  def test_every_location_has_the_expected_neighbour_count(self):
    expected_counts = {1: 3, 2: 5, 3: 3, 4: 5, 5: 8, 6: 5, 7: 3, 8: 5, 9: 3}
    for location, count in expected_counts.items():
      with self.subTest(location=location):
        self.assertEqual(len(DeckLayout.get_adjacent_locations(location)), count)

  def test_a_location_is_never_its_own_neighbour(self):
    for location in range(1, 10):
      self.assertNotIn(location, DeckLayout.get_adjacent_locations(location))


class GetRegionTests(unittest.TestCase):
  def test_empty_inputs_return_empty_region(self):
    self.assertEqual(DeckLayout.get_region([], []), set())

  def test_single_location_region_is_itself(self):
    self.assertEqual(DeckLayout.get_region([5], []), {5})

  def test_bounding_rectangle_spans_full_grid(self):
    self.assertEqual(DeckLayout.get_region([1], [9]), set(range(1, 10)))

  def test_bounding_rectangle_top_row_only(self):
    self.assertEqual(DeckLayout.get_region([1], [3]), {1, 2, 3})

  def test_bounding_rectangle_combines_both_lists(self):
    # start=[1] end=[6] -> rows 0-1, cols 0-2 -> {1,2,3,4,5,6}
    self.assertEqual(DeckLayout.get_region([1], [6]), {1, 2, 3, 4, 5, 6})


class GetDistanceTests(unittest.TestCase):
  def test_same_location_is_zero(self):
    self.assertEqual(DeckLayout.get_distance(5, 5), 0)

  def test_horizontally_adjacent_is_one(self):
    self.assertEqual(DeckLayout.get_distance(4, 5), 1)

  def test_vertically_adjacent_is_two(self):
    self.assertEqual(DeckLayout.get_distance(2, 5), 2)

  def test_diagonally_adjacent_is_three(self):
    self.assertEqual(DeckLayout.get_distance(1, 5), 3)

  def test_two_steps_same_row_is_four(self):
    self.assertEqual(DeckLayout.get_distance(1, 3), 4)

  def test_two_steps_same_col_is_four(self):
    self.assertEqual(DeckLayout.get_distance(1, 7), 4)

  def test_knight_move_is_five(self):
    self.assertEqual(DeckLayout.get_distance(1, 6), 5)
    self.assertEqual(DeckLayout.get_distance(1, 8), 5)

  def test_opposite_corners_is_six(self):
    self.assertEqual(DeckLayout.get_distance(1, 9), 6)
    self.assertEqual(DeckLayout.get_distance(3, 7), 6)

  def test_distance_is_symmetric(self):
    for a in range(1, 10):
      for b in range(1, 10):
        self.assertEqual(DeckLayout.get_distance(a, b), DeckLayout.get_distance(b, a))


if __name__ == "__main__":
  unittest.main()
