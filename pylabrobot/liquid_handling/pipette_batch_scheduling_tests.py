"""Tests for pipette_batch_scheduling module.

Tests cover: mixed channel spacing, phantom interpolation, coordinate batching,
container-to-coordinate resolution (resolve_container_targets), auto-spreading,
and compute_single_container_offsets.
"""

import unittest
from typing import List
from unittest.mock import MagicMock, patch

from pylabrobot.liquid_handling.pipette_batch_scheduling import (
  _min_spacing_between,
  compute_single_container_offsets,
  plan_batches,
  resolve_container_targets,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


def _coords(x_pos: List[float], y_pos: List[float]) -> List[Coordinate]:
  """Build Coordinate targets from parallel x/y lists."""
  return [Coordinate(x, y, 0) for x, y in zip(x_pos, y_pos)]


class TestMixedChannelSpacing(unittest.TestCase):
  """Pairwise spacing with non-uniform channel sizes (e.g. 1mL + 5mL)."""

  SPACINGS = [8.98, 8.98, 17.96, 17.96]

  def test_pairwise_rounding(self):
    # max(8.98, 17.96) = 17.96 -> ceil(179.6)/10 = 18.0
    self.assertAlmostEqual(_min_spacing_between(self.SPACINGS, 1, 2), 18.0)
    # max(8.98, 8.98) = 8.98 -> ceil(89.8)/10 = 9.0
    self.assertAlmostEqual(_min_spacing_between(self.SPACINGS, 0, 1), 9.0)

  def test_mixed_spacing_boundary(self):
    # 18.0mm needed between ch1 (1mL) and ch2 (5mL)
    batches = plan_batches(
      [1, 2], _coords([100.0] * 2, [217.9, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)
    batches = plan_batches(
      [1, 2], _coords([100.0] * 2, [218.0, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)

  def test_pairwise_sum_not_uniform_product(self):
    # ch0->ch3: 9.0 + 18.0 + 18.0 = 45.0mm pairwise, NOT 3 * 18.0 = 54.0mm uniform
    batches = plan_batches(
      [0, 3], _coords([100.0] * 2, [250.0, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)

  def test_mixed_phantoms_use_pairwise_spacing(self):
    batches = plan_batches(
      [0, 3], _coords([100.0] * 2, [245.0, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    # ch0->ch1: 9.0mm, ch1->ch2: 18.0mm
    self.assertAlmostEqual(y[1], 245.0 - 9.0)
    self.assertAlmostEqual(y[2], 245.0 - 9.0 - 18.0)


class TestCoreBatching(unittest.TestCase):
  """Fundamental X grouping, Y batching, and validation."""

  S = [9.0] * 8

  def test_spacing_boundary(self):
    # Exactly 9mm -> one batch
    batches = plan_batches([0, 1], _coords([100.0] * 2, [209.0, 200.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 1)
    # 0.1mm short -> two batches
    batches = plan_batches([0, 1], _coords([100.0] * 2, [208.9, 200.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)

  def test_same_y_serializes(self):
    batches = plan_batches([0, 1, 2], _coords([100.0] * 3, [200.0] * 3), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 3)

  def test_x_tolerance_boundary(self):
    # Within tolerance -> one group
    batches = plan_batches(
      [0, 1], _coords([100.0, 100.05], [270.0, 261.0]), self.S, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)
    # Outside tolerance -> two groups
    batches = plan_batches([0, 1], _coords([100.0, 100.2], [270.0, 270.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)

  def test_x_groups_sorted_ascending(self):
    batches = plan_batches(
      [0, 1, 2], _coords([300.0, 100.0, 200.0], [270.0] * 3), self.S, x_tolerance=0.1
    )
    xs = [b.x_position for b in batches]
    self.assertEqual(xs, sorted(xs))

  def test_empty_raises(self):
    with self.assertRaises(ValueError):
      plan_batches([], [], self.S, x_tolerance=0.1)

  def test_mismatched_lengths_raises(self):
    with self.assertRaises(ValueError):
      plan_batches([0, 1], _coords([100.0], [200.0]), self.S, x_tolerance=0.1)

  def test_duplicate_channels_serialized(self):
    batches = plan_batches([0, 0], _coords([100.0] * 2, [200.0] * 2), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)

  def test_duplicate_channels_three_ops(self):
    batches = plan_batches([0, 0, 0], _coords([100.0] * 3, [200.0] * 3), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 3)


class TestPhantomInterpolation(unittest.TestCase):
  """Phantom channels between non-consecutive batch members."""

  def test_phantoms_interpolated_at_spacing(self):
    batches = plan_batches(
      [0, 1, 2, 5, 6, 7],
      _coords([100.0] * 6, [300.0, 291.0, 282.0, 255.0, 246.0, 237.0]),
      [9.0] * 8,
      x_tolerance=0.1,
    )
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertIn(3, y)
    self.assertIn(4, y)
    self.assertAlmostEqual(y[3], 282.0 - 9.0)
    self.assertAlmostEqual(y[4], 282.0 - 18.0)

  def test_phantoms_only_within_batch(self):
    # Split into 2 batches — no phantoms across batches
    batches = plan_batches([0, 3], _coords([100.0] * 2, [200.0, 250.0]), [9.0] * 4, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)
    for batch in batches:
      self.assertEqual(len(batch.y_positions), 1)


class TestCoordinateTargets(unittest.TestCase):
  """plan_batches with Coordinate targets (no containers)."""

  def test_coordinate_x_grouping_and_y_batching(self):
    batches = plan_batches(
      [0, 1, 2, 3],
      _coords([100.0, 100.0, 200.0, 200.0], [200.0, 200.0, 270.0, 261.0]),
      [9.0] * 4,
      x_tolerance=0.1,
    )
    x100 = [b for b in batches if abs(b.x_position - 100.0) < 0.01]
    x200 = [b for b in batches if abs(b.x_position - 200.0) < 0.01]
    self.assertEqual(len(x100), 2)  # same Y -> serialized
    self.assertEqual(len(x200), 1)  # 9mm apart -> parallel

  def test_indices_map_back_correctly(self):
    use_channels = [3, 7, 0]
    batches = plan_batches(
      use_channels, _coords([100.0] * 3, [261.0, 237.0, 270.0]), [9.0] * 8, x_tolerance=0.1
    )
    all_indices = [idx for b in batches for idx in b.indices]
    self.assertEqual(sorted(all_indices), [0, 1, 2])
    for batch in batches:
      for idx, ch in zip(batch.indices, batch.channels):
        self.assertEqual(use_channels[idx], ch)


class TestContainerTargets(unittest.TestCase):
  """resolve_container_targets + plan_batches with Container auto-spreading."""

  S = [9.0] * 8

  def _mock_container(self, cx: float, cy: float, size_y: float = 10.0, name: str = "well"):
    c = MagicMock(spec=Container)
    c.get_absolute_size_y.return_value = size_y
    c.name = name
    c.get_location_wrt = MagicMock(return_value=Coordinate(cx, cy, 0))
    return c

  def _mock_deck(self):
    return MagicMock(spec=Resource)

  @patch("pylabrobot.liquid_handling.pipette_batch_scheduling.compute_channel_offsets")
  def test_same_container_auto_spreads(self, mock_offsets):
    mock_offsets.return_value = [Coordinate(0, 4.5, 0), Coordinate(0, -4.5, 0)]
    trough = self._mock_container(100.0, 200.0, size_y=50.0, name="trough")
    deck = self._mock_deck()
    targets = resolve_container_targets([trough, trough], [0, 1], self.S, deck)
    batches = plan_batches([0, 1], targets, self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertAlmostEqual(y[0], 200.0 + 4.5)
    self.assertAlmostEqual(y[1], 200.0 - 4.5)

  def test_same_narrow_container_serialized(self):
    well = self._mock_container(100.0, 200.0, size_y=5.0, name="narrow_well")
    deck = self._mock_deck()
    targets = resolve_container_targets([well, well], [0, 1], self.S, deck)
    batches = plan_batches([0, 1], targets, self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)

  @patch("pylabrobot.liquid_handling.pipette_batch_scheduling.compute_channel_offsets")
  def test_resource_offsets_skips_auto_spreading(self, mock_offsets):
    trough = self._mock_container(100.0, 200.0, size_y=50.0, name="trough")
    deck = self._mock_deck()
    user_offsets = [Coordinate(0, 10.0, 0), Coordinate(0, -10.0, 0)]
    targets = resolve_container_targets(
      [trough, trough], [0, 1], self.S, deck, resource_offsets=user_offsets
    )
    mock_offsets.assert_not_called()
    batches = plan_batches([0, 1], targets, self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertAlmostEqual(y[0], 210.0)
    self.assertAlmostEqual(y[1], 190.0)


class TestComputeSingleContainerOffsets(unittest.TestCase):
  S = [9.0] * 8

  def _mock_container(self, size_y: float):
    c = MagicMock(spec=["get_absolute_size_y"])
    c.get_absolute_size_y.return_value = size_y
    return c

  @patch("pylabrobot.liquid_handling.pipette_batch_scheduling.compute_channel_offsets")
  def test_even_span_no_center_offset(self, mock_offsets):
    mock_offsets.return_value = [Coordinate(0, 4.5, 0), Coordinate(0, -4.5, 0)]
    result = compute_single_container_offsets(self._mock_container(50.0), [0, 1], self.S)
    assert result is not None
    self.assertAlmostEqual(result[0].y, 4.5)
    self.assertAlmostEqual(result[1].y, -4.5)

  @patch("pylabrobot.liquid_handling.pipette_batch_scheduling.compute_channel_offsets")
  def test_odd_span_passes_through_offsets(self, mock_offsets):
    mock_offsets.return_value = [
      Coordinate(0, 9.0, 0),
      Coordinate(0, 0.0, 0),
      Coordinate(0, -9.0, 0),
    ]
    # No additional shift; compute_channel_offsets handles no-go zones directly
    result = compute_single_container_offsets(self._mock_container(50.0), [0, 1, 2], self.S)
    assert result is not None
    self.assertAlmostEqual(result[0].y, 9.0)

  def test_container_too_small_returns_none(self):
    self.assertIsNone(compute_single_container_offsets(self._mock_container(10.0), [0, 1], self.S))

  @patch("pylabrobot.liquid_handling.pipette_batch_scheduling.compute_channel_offsets")
  def test_non_consecutive_uses_full_physical_span(self, mock_offsets):
    mock_offsets.return_value = [
      Coordinate(0, 10.0, 0),
      Coordinate(0, 0.0, 0),
      Coordinate(0, -10.0, 0),
    ]
    result = compute_single_container_offsets(self._mock_container(50.0), [0, 2], self.S)
    assert result is not None
    self.assertEqual(len(result), 2)
    mock_offsets.assert_called_once_with(
      resource=unittest.mock.ANY, num_channels=3, spread="wide", channel_spacings=[9.0] * 3
    )

  @patch("pylabrobot.liquid_handling.pipette_batch_scheduling.compute_channel_offsets")
  def test_mixed_spacing_uses_effective(self, mock_offsets):
    mock_offsets.return_value = [
      Coordinate(0, 18.0, 0),
      Coordinate(0, 0.0, 0),
      Coordinate(0, -18.0, 0),
    ]
    result = compute_single_container_offsets(self._mock_container(100.0), [0, 2], [9.0, 9.0, 18.0])
    self.assertIsNotNone(result)
    mock_offsets.assert_called_once_with(
      resource=unittest.mock.ANY, num_channels=3, spread="wide", channel_spacings=[18.0] * 3
    )


if __name__ == "__main__":
  unittest.main()
