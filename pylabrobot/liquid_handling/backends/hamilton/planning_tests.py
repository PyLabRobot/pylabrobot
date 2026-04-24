import unittest

from pylabrobot.resources import Coordinate

from .planning import group_by_x_batch_by_xy


class TestGroupByXBatchByXY(unittest.TestCase):
  """Tests for group_by_x_batch_by_xy."""

  def test_single_location(self):
    locations = [Coordinate(100.0, 200.0, 0)]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0])
    self.assertEqual(result, {100.0: [[0]]})

  def test_same_x_different_y_fits_in_one_batch(self):
    locations = [
      Coordinate(100.0, 200.0, 0),
      Coordinate(100.0, 180.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 1])
    # y diff = 20 >= 9*1, fits in one batch
    self.assertEqual(result, {100.0: [[0, 1]]})

  def test_same_x_too_close_y_splits_batches(self):
    locations = [
      Coordinate(100.0, 200.0, 0),
      Coordinate(100.0, 195.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 1])
    # y diff = 5 < 9*1, separate batches
    self.assertEqual(result, {100.0: [[0], [1]]})

  def test_different_x_groups(self):
    locations = [
      Coordinate(100.0, 200.0, 0),
      Coordinate(200.0, 200.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 1])
    self.assertEqual(result, {100.0: [[0]], 200.0: [[1]]})

  def test_x_rounding(self):
    locations = [
      Coordinate(100.04, 200.0, 0),
      Coordinate(100.02, 180.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 1])
    # Both round to 100.0
    self.assertEqual(result, {100.0: [[0, 1]]})

  def test_two_channels_same_x(self):
    locations = [Coordinate(100.0, 200.0, 0), Coordinate(100.0, 180.0, 0)]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 1])
    self.assertEqual(result, {100.0: [[0, 1]]})

  def test_empty_use_channels_raises(self):
    with self.assertRaises(ValueError):
      group_by_x_batch_by_xy(
        locations=[Coordinate(100.0, 200.0, 0)],
        use_channels=[],
      )

  def test_non_adjacent_channels(self):
    locations = [
      Coordinate(100.0, 300.0, 0),
      Coordinate(100.0, 200.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 5])
    # y diff = 100 >= 9*(5-0) = 45, fits in one batch
    self.assertEqual(result, {100.0: [[0, 1]]})

  def test_non_adjacent_channels_too_close(self):
    locations = [
      Coordinate(100.0, 240.0, 0),
      Coordinate(100.0, 200.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 5])
    # y diff = 40 < 9*(5-0) = 45, separate batches
    self.assertEqual(result, {100.0: [[0], [1]]})

  def test_sorted_by_x(self):
    locations = [
      Coordinate(300.0, 200.0, 0),
      Coordinate(100.0, 200.0, 0),
      Coordinate(200.0, 200.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 1, 2])
    self.assertEqual(result, {100.0: [[1]], 200.0: [[2]], 300.0: [[0]]})

  def test_multiple_batches_in_one_x_group(self):
    locations = [
      Coordinate(100.0, 200.0, 0),
      Coordinate(100.0, 197.0, 0),
      Coordinate(100.0, 194.0, 0),
      Coordinate(100.0, 191.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 1, 2, 3])
    # Each consecutive pair has y diff = 3 < 9, so each in its own batch
    self.assertEqual(result, {100.0: [[0], [1], [2], [3]]})

  def test_duplicate_channels_split_into_separate_batches(self):
    locations = [
      Coordinate(100.0, 200.0, 0),
      Coordinate(100.0, 180.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 0])
    self.assertEqual(result, {100.0: [[0], [1]]})

  def test_duplicate_channels_three_ops(self):
    locations = [
      Coordinate(100.0, 200.0, 0),
      Coordinate(100.0, 180.0, 0),
      Coordinate(100.0, 160.0, 0),
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[0, 0, 0])
    self.assertEqual(result, {100.0: [[0], [1], [2]]})

  def test_channels_sorted_by_channel_index_within_x_group(self):
    locations = [
      Coordinate(100.0, 180.0, 0),  # channel 2
      Coordinate(100.0, 200.0, 0),  # channel 0
    ]
    result = group_by_x_batch_by_xy(locations=locations, use_channels=[2, 0])
    # Channel 0 (index 1) sorted before channel 2 (index 0)
    self.assertEqual(result, {100.0: [[1, 0]]})
