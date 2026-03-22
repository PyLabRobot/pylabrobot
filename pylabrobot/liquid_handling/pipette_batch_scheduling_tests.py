"""Tests for pipette_batch_scheduling module."""

import unittest
from typing import List
from unittest.mock import MagicMock, patch

from pylabrobot.liquid_handling.pipette_batch_scheduling import (
  _effective_spacing,
  _min_spacing_between,
  compute_single_container_offsets,
  plan_batches,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


def _coords(x_pos: List[float], y_pos: List[float]) -> List[Coordinate]:
  """Build Coordinate targets from parallel x/y lists."""
  return [Coordinate(x, y, 0) for x, y in zip(x_pos, y_pos)]


class TestEffectiveSpacing(unittest.TestCase):
  def test_uniform(self):
    self.assertAlmostEqual(_effective_spacing([9.0, 9.0, 9.0, 9.0], 0, 3), 9.0)

  def test_mixed_takes_max(self):
    spacings = [9.0, 9.0, 18.0, 18.0]
    self.assertAlmostEqual(_effective_spacing(spacings, 0, 3), 18.0)
    self.assertAlmostEqual(_effective_spacing(spacings, 0, 1), 9.0)
    self.assertAlmostEqual(_effective_spacing(spacings, 1, 2), 18.0)

  def test_single_channel(self):
    self.assertAlmostEqual(_effective_spacing([9.0, 18.0], 0, 0), 9.0)
    self.assertAlmostEqual(_effective_spacing([9.0, 18.0], 1, 1), 18.0)


class TestPlanBatchesUniformSpacing(unittest.TestCase):
  S = 9.0

  # --- X grouping ---

  def test_single_x_group(self):
    batches = plan_batches(
      [0, 1, 2], _coords([100.0] * 3, [270.0, 261.0, 252.0]), self.S, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)
    self.assertAlmostEqual(batches[0].x_position, 100.0)
    self.assertEqual(sorted(batches[0].channels), [0, 1, 2])

  def test_two_x_groups(self):
    batches = plan_batches(
      [0, 1, 2, 3],
      _coords([100.0, 100.0, 200.0, 200.0], [270.0, 261.0, 270.0, 261.0]),
      self.S,
      x_tolerance=0.1,
    )
    x_positions = [b.x_position for b in batches]
    self.assertAlmostEqual(x_positions[0], 100.0)
    self.assertAlmostEqual(x_positions[-1], 200.0)

  def test_x_groups_sorted_by_ascending_x(self):
    batches = plan_batches(
      [0, 1, 2], _coords([300.0, 100.0, 200.0], [270.0] * 3), self.S, x_tolerance=0.1
    )
    x_positions = [b.x_position for b in batches]
    self.assertAlmostEqual(x_positions[0], 100.0)
    self.assertAlmostEqual(x_positions[1], 200.0)
    self.assertAlmostEqual(x_positions[2], 300.0)

  def test_x_positions_within_tolerance_grouped(self):
    batches = plan_batches(
      [0, 1], _coords([100.0, 100.05], [270.0, 261.0]), self.S, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)

  def test_x_positions_outside_tolerance_split(self):
    batches = plan_batches([0, 1], _coords([100.0, 100.2], [270.0, 270.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)

  # --- Y batching ---

  def test_same_y_forces_serialization(self):
    batches = plan_batches([0, 1, 2], _coords([100.0] * 3, [200.0] * 3), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 3)

  def test_barely_fitting_spacing(self):
    batches = plan_batches([0, 1], _coords([100.0] * 2, [209.0, 200.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 1)

  def test_barely_insufficient_spacing(self):
    batches = plan_batches([0, 1], _coords([100.0] * 2, [208.9, 200.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)

  def test_reversed_y_order_splits(self):
    batches = plan_batches([0, 1], _coords([100.0] * 2, [200.0, 220.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)

  # --- Non-consecutive channels ---

  def test_non_consecutive_channels_with_phantoms(self):
    batches = plan_batches(
      [0, 1, 2, 5, 6, 7],
      _coords([100.0] * 6, [300.0, 291.0, 282.0, 255.0, 246.0, 237.0]),
      self.S,
      x_tolerance=0.1,
    )
    self.assertEqual(len(batches), 1)
    self.assertEqual(sorted(batches[0].channels), [0, 1, 2, 5, 6, 7])
    y = batches[0].y_positions
    self.assertIn(3, y)
    self.assertIn(4, y)
    self.assertAlmostEqual(y[3], 282.0 - 9.0)
    self.assertAlmostEqual(y[4], 282.0 - 18.0)

  def test_phantom_channels_interpolated(self):
    batches = plan_batches([0, 3], _coords([100.0] * 2, [300.0, 273.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertAlmostEqual(y[0], 300.0)
    self.assertAlmostEqual(y[1], 291.0)
    self.assertAlmostEqual(y[2], 282.0)
    self.assertAlmostEqual(y[3], 273.0)

  def test_phantoms_only_within_batch(self):
    batches = plan_batches([0, 3], _coords([100.0] * 2, [200.0, 250.0]), self.S, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)
    for batch in batches:
      self.assertEqual(len(batch.y_positions), 1)

  # --- Mixed X and Y ---

  def test_mixed_complexity(self):
    batches = plan_batches(
      [0, 1, 2, 3],
      _coords([100.0, 100.0, 200.0, 200.0], [200.0, 200.0, 270.0, 261.0]),
      self.S,
      x_tolerance=0.1,
    )
    x100 = [b for b in batches if abs(b.x_position - 100.0) < 0.01]
    x200 = [b for b in batches if abs(b.x_position - 200.0) < 0.01]
    self.assertEqual(len(x100), 2)
    self.assertEqual(len(x200), 1)

  # --- Validation ---

  def test_mismatched_lengths(self):
    with self.assertRaises(ValueError):
      plan_batches([0, 1], _coords([100.0], [200.0]), self.S, x_tolerance=0.1)

  def test_empty(self):
    with self.assertRaises(ValueError):
      plan_batches([], [], self.S, x_tolerance=0.1)

  # --- Index correctness ---

  def test_indices_map_back_correctly(self):
    use_channels = [3, 7, 0]
    batches = plan_batches(
      use_channels, _coords([100.0] * 3, [261.0, 237.0, 270.0]), self.S, x_tolerance=0.1
    )
    all_indices = [idx for b in batches for idx in b.indices]
    self.assertEqual(sorted(all_indices), [0, 1, 2])
    for batch in batches:
      for idx, ch in zip(batch.indices, batch.channels):
        self.assertEqual(use_channels[idx], ch)

  # --- Realistic ---

  def test_8_channels_trough(self):
    batches = plan_batches(
      list(range(8)),
      _coords([100.0] * 8, [300.0 - i * 9.0 for i in range(8)]),
      self.S,
      x_tolerance=0.1,
    )
    self.assertEqual(len(batches), 1)
    self.assertEqual(len(batches[0].channels), 8)

  def test_8_channels_narrow_well(self):
    batches = plan_batches(
      list(range(8)), _coords([100.0] * 8, [200.0] * 8), self.S, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 8)


class TestPlanBatchesMixedSpacing(unittest.TestCase):
  """Tests for mixed-channel instruments (e.g. 1mL + 5mL)."""

  # Channels 0,1 are 1mL (8.98mm), channels 2,3 are 5mL (17.96mm)
  SPACINGS = [8.98, 8.98, 17.96, 17.96]

  def test_two_1ml_channels_fit_at_9mm(self):
    # ceil(8.98 * 10) / 10 = 9.0mm effective spacing
    batches = plan_batches(
      [0, 1], _coords([100.0] * 2, [209.0, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)

  def test_1ml_and_5ml_need_wider_spacing(self):
    # ceil(17.96 * 10) / 10 = 18.0mm effective spacing between ch1 and ch2
    batches = plan_batches(
      [1, 2], _coords([100.0] * 2, [217.9, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)

  def test_1ml_and_5ml_fit_at_wide_spacing(self):
    batches = plan_batches(
      [1, 2], _coords([100.0] * 2, [218.0, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)

  def test_5ml_channels_too_close(self):
    batches = plan_batches(
      [2, 3], _coords([100.0] * 2, [217.9, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)

  def test_span_across_1ml_and_5ml_boundary(self):
    # Rounded pairwise sum: 9.0 + 18.0 + 18.0 = 45.0mm
    batches = plan_batches(
      [0, 3], _coords([100.0] * 2, [245.0, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)
    # Also check phantom positions
    y = batches[0].y_positions
    self.assertAlmostEqual(y[1], 245.0 - 9.0)
    self.assertAlmostEqual(y[2], 245.0 - 9.0 - 18.0)
    # 0.1mm less doesn't fit
    batches = plan_batches(
      [0, 3], _coords([100.0] * 2, [244.9, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)

  def test_pairwise_sum_avoids_unnecessary_split(self):
    # Rounded pairwise sum ch0→ch3: 9.0 + 18.0 + 18.0 = 45.0mm, NOT 3 * 18.0 = 54.0mm.
    # 50mm gap fits with pairwise even though < 54.0
    batches = plan_batches(
      [0, 3], _coords([100.0] * 2, [250.0, 200.0]), self.SPACINGS, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)

  def test_mixed_all_four_channels_spaced_wide(self):
    batches = plan_batches(
      [0, 1, 2, 3],
      _coords([100.0] * 4, [300.0, 291.0, 273.0, 255.0]),
      self.SPACINGS,
      x_tolerance=0.1,
    )
    self.assertEqual(len(batches), 1)

  def test_mixed_channels_at_1ml_spacing_forces_serialization(self):
    batches = plan_batches(
      [0, 1, 2, 3],
      _coords([100.0] * 4, [300.0, 291.0, 282.0, 273.0]),
      self.SPACINGS,
      x_tolerance=0.1,
    )
    self.assertGreater(len(batches), 1)


class TestPlanBatchesWithContainers(unittest.TestCase):
  """Tests for the Container path with auto-spreading."""

  S = 9.0

  def _mock_container(self, cx: float, cy: float, size_y: float = 10.0, name: str = "well"):
    c = MagicMock(spec=Container)
    c.get_absolute_size_y.return_value = size_y
    c.name = name
    c.get_location_wrt = MagicMock(return_value=Coordinate(cx, cy, 0))
    return c

  def _mock_deck(self):
    return MagicMock(spec=Resource)

  def test_single_container_no_spreading(self):
    """One channel per container — no spreading needed."""
    c1 = self._mock_container(100.0, 270.0)
    c2 = self._mock_container(100.0, 261.0)
    deck = self._mock_deck()
    batches = plan_batches([0, 1], [c1, c2], self.S, x_tolerance=0.1, wrt_resource=deck)
    self.assertEqual(len(batches), 1)
    self.assertEqual(sorted(batches[0].channels), [0, 1])

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.get_wide_single_resource_liquid_op_offsets"
  )
  def test_same_container_auto_spreads(self, mock_offsets):
    """Two channels targeting the same wide container get spread offsets."""
    mock_offsets.return_value = [Coordinate(0, 4.5, 0), Coordinate(0, -4.5, 0)]
    trough = self._mock_container(100.0, 200.0, size_y=50.0, name="trough")
    deck = self._mock_deck()
    batches = plan_batches([0, 1], [trough, trough], self.S, x_tolerance=0.1, wrt_resource=deck)
    # With spreading, ch0 at 204.5 and ch1 at 195.5 — 9mm apart, fits in one batch
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertAlmostEqual(y[0], 200.0 + 4.5)
    self.assertAlmostEqual(y[1], 200.0 - 4.5)

  def test_same_narrow_container_serialized(self):
    """Two channels targeting the same narrow container can't spread — serialized."""
    well = self._mock_container(100.0, 200.0, size_y=5.0, name="narrow_well")
    deck = self._mock_deck()
    batches = plan_batches([0, 1], [well, well], self.S, x_tolerance=0.1, wrt_resource=deck)
    # Can't spread in 5mm container, both at y=200 — must serialize
    self.assertEqual(len(batches), 2)

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.get_wide_single_resource_liquid_op_offsets"
  )
  def test_resource_offsets_skips_auto_spreading(self, mock_offsets):
    """User-provided offsets disable auto-spreading."""
    trough = self._mock_container(100.0, 200.0, size_y=50.0, name="trough")
    deck = self._mock_deck()
    user_offsets = [Coordinate(0, 10.0, 0), Coordinate(0, -10.0, 0)]
    batches = plan_batches(
      [0, 1],
      [trough, trough],
      self.S,
      x_tolerance=0.1,
      wrt_resource=deck,
      resource_offsets=user_offsets,
    )
    # Should use user offsets (210, 190) not auto-spread
    mock_offsets.assert_not_called()
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertAlmostEqual(y[0], 210.0)
    self.assertAlmostEqual(y[1], 190.0)


class TestComputeSingleContainerOffsets(unittest.TestCase):
  S = 9.0

  def _mock_container(self, size_y: float):
    c = MagicMock(spec=["get_absolute_size_y"])
    c.get_absolute_size_y.return_value = size_y
    return c

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.get_wide_single_resource_liquid_op_offsets"
  )
  def test_even_span_no_center_offset(self, mock_offsets):
    mock_offsets.return_value = [Coordinate(0, 4.5, 0), Coordinate(0, -4.5, 0)]
    result = compute_single_container_offsets(self._mock_container(50.0), [0, 1], self.S)
    self.assertAlmostEqual(result[0].y, 4.5)
    self.assertAlmostEqual(result[1].y, -4.5)

  def test_single_channel_returns_zero(self):
    result = compute_single_container_offsets(self._mock_container(50.0), [0], self.S)
    self.assertAlmostEqual(result[0].y, 0.0)

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.get_wide_single_resource_liquid_op_offsets"
  )
  def test_odd_span_applies_center_offset(self, mock_offsets):
    mock_offsets.return_value = [
      Coordinate(0, 9.0, 0),
      Coordinate(0, 0.0, 0),
      Coordinate(0, -9.0, 0),
    ]
    result = compute_single_container_offsets(self._mock_container(50.0), [0, 1, 2], self.S)
    self.assertAlmostEqual(result[0].y, 9.0 + 5.5)
    self.assertAlmostEqual(result[1].y, 0.0 + 5.5)
    self.assertAlmostEqual(result[2].y, -9.0 + 5.5)

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.get_wide_single_resource_liquid_op_offsets"
  )
  def test_non_consecutive_selects_correct_offsets(self, mock_offsets):
    mock_offsets.return_value = [
      Coordinate(0, 10.0, 0),
      Coordinate(0, 0.0, 0),
      Coordinate(0, -10.0, 0),
    ]
    result = compute_single_container_offsets(self._mock_container(50.0), [0, 2], self.S)
    self.assertEqual(len(result), 2)
    mock_offsets.assert_called_once_with(
      resource=unittest.mock.ANY, num_channels=3, min_spacing=self.S
    )

  def test_container_too_small_returns_none(self):
    self.assertIsNone(compute_single_container_offsets(self._mock_container(10.0), [0, 1], self.S))

  def test_empty_channels(self):
    self.assertEqual(compute_single_container_offsets(self._mock_container(50.0), [], self.S), [])

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.get_wide_single_resource_liquid_op_offsets"
  )
  def test_mixed_spacing_uses_effective(self, mock_offsets):
    mock_offsets.return_value = [
      Coordinate(0, 18.0, 0),
      Coordinate(0, 0.0, 0),
      Coordinate(0, -18.0, 0),
    ]
    spacings = [9.0, 9.0, 18.0]
    result = compute_single_container_offsets(self._mock_container(100.0), [0, 2], spacings)
    self.assertIsNotNone(result)
    mock_offsets.assert_called_once_with(
      resource=unittest.mock.ANY, num_channels=3, min_spacing=18.0
    )


class TestPairwiseMinSpacing(unittest.TestCase):
  def test_uniform_spacing(self):
    spacings = [9.0] * 8
    self.assertAlmostEqual(_min_spacing_between(spacings, 0, 1), 9.0)
    self.assertAlmostEqual(_min_spacing_between(spacings, 5, 6), 9.0)

  def test_mixed_spacing(self):
    spacings = [8.98, 8.98, 17.96, 17.96]
    # max(8.98, 8.98) = 8.98 → ceil(89.8)/10 = 9.0
    self.assertAlmostEqual(_min_spacing_between(spacings, 0, 1), 9.0)
    # max(8.98, 17.96) = 17.96 → ceil(179.6)/10 = 18.0
    self.assertAlmostEqual(_min_spacing_between(spacings, 1, 2), 18.0)
    # max(17.96, 17.96) = 17.96 → ceil(179.6)/10 = 18.0
    self.assertAlmostEqual(_min_spacing_between(spacings, 2, 3), 18.0)


if __name__ == "__main__":
  unittest.main()
