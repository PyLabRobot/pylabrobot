"""Tests for pipette_batch_scheduling module."""

import unittest
from unittest.mock import MagicMock, patch

from pylabrobot.liquid_handling.pipette_batch_scheduling import (
  ChannelBatch,
  _effective_spacing,
  _find_next_y_target,
  _optimize_batch_transitions,
  _min_spacing_between,
  compute_single_container_offsets,
  plan_batches,
)
from pylabrobot.resources.coordinate import Coordinate


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
    batches = plan_batches([0, 1, 2], [100.0] * 3, [270.0, 261.0, 252.0], self.S)
    self.assertEqual(len(batches), 1)
    self.assertAlmostEqual(batches[0].x_position, 100.0)

  def test_two_x_groups(self):
    batches = plan_batches(
      [0, 1, 2, 3], [100.0, 100.0, 200.0, 200.0], [270.0, 261.0, 270.0, 261.0], self.S
    )
    x_positions = [b.x_position for b in batches]
    self.assertAlmostEqual(x_positions[0], 100.0)
    self.assertAlmostEqual(x_positions[-1], 200.0)

  def test_x_groups_sorted_by_ascending_x(self):
    batches = plan_batches([0, 1, 2], [300.0, 100.0, 200.0], [270.0] * 3, self.S)
    x_positions = [b.x_position for b in batches]
    self.assertAlmostEqual(x_positions[0], 100.0)
    self.assertAlmostEqual(x_positions[1], 200.0)
    self.assertAlmostEqual(x_positions[2], 300.0)

  def test_x_positions_within_tolerance_grouped(self):
    batches = plan_batches([0, 1], [100.0, 100.05], [270.0, 261.0], self.S)
    self.assertEqual(len(batches), 1)

  def test_x_positions_outside_tolerance_split(self):
    batches = plan_batches([0, 1], [100.0, 100.2], [270.0, 270.0], self.S)
    self.assertEqual(len(batches), 2)

  # --- Y batching ---

  def test_consecutive_channels_single_batch(self):
    batches = plan_batches([0, 1, 2], [100.0] * 3, [270.0, 261.0, 252.0], self.S)
    self.assertEqual(len(batches), 1)
    self.assertEqual(sorted(batches[0].channels), [0, 1, 2])

  def test_same_y_forces_serialization(self):
    batches = plan_batches([0, 1, 2], [100.0] * 3, [200.0] * 3, self.S)
    self.assertEqual(len(batches), 3)

  def test_barely_fitting_spacing(self):
    batches = plan_batches([0, 1], [100.0] * 2, [209.0, 200.0], self.S)
    self.assertEqual(len(batches), 1)

  def test_barely_insufficient_spacing(self):
    batches = plan_batches([0, 1], [100.0] * 2, [208.9, 200.0], self.S)
    self.assertEqual(len(batches), 2)

  def test_reversed_y_order_splits(self):
    batches = plan_batches([0, 1], [100.0] * 2, [200.0, 220.0], self.S)
    self.assertEqual(len(batches), 2)

  # --- Non-consecutive channels ---

  def test_non_consecutive_channels_fit(self):
    batches = plan_batches(
      [0, 1, 2, 5, 6, 7],
      [100.0] * 6,
      [300.0, 291.0, 282.0, 255.0, 246.0, 237.0],
      self.S,
    )
    self.assertEqual(len(batches), 1)
    self.assertEqual(sorted(batches[0].channels), [0, 1, 2, 5, 6, 7])

  def test_phantom_channels_interpolated(self):
    batches = plan_batches([0, 3], [100.0] * 2, [300.0, 273.0], self.S)
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertAlmostEqual(y[0], 300.0)
    self.assertAlmostEqual(y[1], 291.0)
    self.assertAlmostEqual(y[2], 282.0)
    self.assertAlmostEqual(y[3], 273.0)

  def test_phantoms_only_within_batch(self):
    batches = plan_batches([0, 3], [100.0] * 2, [200.0, 250.0], self.S)
    self.assertEqual(len(batches), 2)
    for batch in batches:
      self.assertEqual(len(batch.y_positions), 1)

  # --- Mixed X and Y ---

  def test_mixed_complexity(self):
    batches = plan_batches(
      [0, 1, 2, 3],
      [100.0, 100.0, 200.0, 200.0],
      [200.0, 200.0, 270.0, 261.0],
      self.S,
    )
    x100 = [b for b in batches if abs(b.x_position - 100.0) < 0.01]
    x200 = [b for b in batches if abs(b.x_position - 200.0) < 0.01]
    self.assertEqual(len(x100), 2)
    self.assertEqual(len(x200), 1)

  # --- Validation ---

  def test_mismatched_lengths(self):
    with self.assertRaises(ValueError):
      plan_batches([0, 1], [100.0], [200.0, 200.0], self.S)

  def test_empty(self):
    with self.assertRaises(ValueError):
      plan_batches([], [], [], self.S)

  # --- Index correctness ---

  def test_indices_map_back_correctly(self):
    use_channels = [3, 7, 0]
    batches = plan_batches(use_channels, [100.0] * 3, [261.0, 237.0, 270.0], self.S)
    all_indices = [idx for b in batches for idx in b.indices]
    self.assertEqual(sorted(all_indices), [0, 1, 2])
    for batch in batches:
      for idx, ch in zip(batch.indices, batch.channels):
        self.assertEqual(use_channels[idx], ch)

  # --- Realistic ---

  def test_8_channels_trough(self):
    batches = plan_batches(list(range(8)), [100.0] * 8, [300.0 - i * 9.0 for i in range(8)], self.S)
    self.assertEqual(len(batches), 1)
    self.assertEqual(len(batches[0].channels), 8)

  def test_8_channels_narrow_well(self):
    batches = plan_batches(list(range(8)), [100.0] * 8, [200.0] * 8, self.S)
    self.assertEqual(len(batches), 8)

  def test_channels_0_1_2_5_6_7_phantoms(self):
    batches = plan_batches(
      [0, 1, 2, 5, 6, 7],
      [100.0] * 6,
      [300.0, 291.0, 282.0, 255.0, 246.0, 237.0],
      self.S,
    )
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertIn(3, y)
    self.assertIn(4, y)
    self.assertAlmostEqual(y[3], 282.0 - 9.0)
    self.assertAlmostEqual(y[4], 282.0 - 18.0)


class TestPlanBatchesMixedSpacing(unittest.TestCase):
  """Tests for mixed-channel instruments (e.g. 1mL + 5mL)."""

  # Channels 0,1 are 1mL (8.98mm), channels 2,3 are 5mL (17.96mm)
  SPACINGS = [8.98, 8.98, 17.96, 17.96]

  def test_two_1ml_channels_fit_at_9mm(self):
    batches = plan_batches([0, 1], [100.0] * 2, [208.98, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 1)

  def test_1ml_and_5ml_need_wider_spacing(self):
    batches = plan_batches([1, 2], [100.0] * 2, [209.0, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 2)

  def test_1ml_and_5ml_fit_at_wide_spacing(self):
    batches = plan_batches([1, 2], [100.0] * 2, [217.96, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 1)

  def test_5ml_channels_fit_at_wide_spacing(self):
    batches = plan_batches([2, 3], [100.0] * 2, [217.96, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 1)

  def test_5ml_channels_too_close(self):
    batches = plan_batches([2, 3], [100.0] * 2, [209.0, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 2)

  def test_span_across_1ml_and_5ml(self):
    # Pairwise sum: max(8.98,8.98) + max(8.98,17.96) + max(17.96,17.96) = 44.9
    batches = plan_batches([0, 3], [100.0] * 2, [244.9, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 1)
    batches = plan_batches([0, 3], [100.0] * 2, [244.0, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 2)

  def test_phantom_channels_use_pairwise_spacing(self):
    # ch0→ch1: max(8.98, 8.98) = 8.98, ch1→ch2: max(8.98, 17.96) = 17.96
    batches = plan_batches([0, 3], [100.0] * 2, [244.9, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertAlmostEqual(y[1], 244.9 - 8.98)
    self.assertAlmostEqual(y[2], 244.9 - 8.98 - 17.96)

  def test_mixed_all_four_channels_spaced_wide(self):
    s = 17.96
    batches = plan_batches(
      [0, 1, 2, 3],
      [100.0] * 4,
      [300.0, 300.0 - s, 300.0 - 2 * s, 300.0 - 3 * s],
      self.SPACINGS,
    )
    self.assertEqual(len(batches), 1)

  def test_pairwise_sum_avoids_unnecessary_split(self):
    # With spacings [8.98, 8.98, 17.96, 17.96], spanning ch0→ch3 requires
    # 8.98 + 17.96 + 17.96 = 44.9mm (pairwise sum), NOT 3 * 17.96 = 53.88mm.
    # A gap of 50mm should fit in one batch (pairwise) even though it's less than 53.88.
    batches = plan_batches([0, 3], [100.0] * 2, [250.0, 200.0], self.SPACINGS)
    self.assertEqual(len(batches), 1)

  def test_mixed_channels_at_1ml_spacing_forces_serialization(self):
    batches = plan_batches(
      [0, 1, 2, 3],
      [100.0] * 4,
      [300.0, 291.0, 282.0, 273.0],
      self.SPACINGS,
    )
    self.assertGreater(len(batches), 1)


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

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.get_wide_single_resource_liquid_op_offsets"
  )
  def test_single_channel_no_center_offset(self, mock_offsets):
    mock_offsets.return_value = [Coordinate(0, 0.0, 0)]
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


class TestFindNextYTarget(unittest.TestCase):
  def _batch(self, y_positions):
    return ChannelBatch(x_position=100.0, indices=[], channels=[], y_positions=y_positions)

  def test_found_in_immediate_next_batch(self):
    batches = [self._batch({0: 400}), self._batch({0: 300, 1: 291})]
    self.assertAlmostEqual(_find_next_y_target(0, 1, batches), 300.0)

  def test_found_in_later_batch(self):
    batches = [
      self._batch({0: 400}),
      self._batch({2: 300}),
      self._batch({0: 200}),
    ]
    # start_batch=1, channel 0 not in batch[1], found in batch[2]
    self.assertAlmostEqual(_find_next_y_target(0, 1, batches), 200.0)

  def test_not_found_returns_none(self):
    batches = [self._batch({0: 400}), self._batch({1: 300})]
    self.assertIsNone(_find_next_y_target(2, 0, batches))

  def test_phantom_position_used_as_target(self):
    # Channel 1 is a phantom in batch 1 (between active 0 and 2)
    batches = [self._batch({0: 400}), self._batch({0: 300, 1: 291, 2: 282})]
    self.assertAlmostEqual(_find_next_y_target(1, 1, batches), 291.0)


class TestForwardPlan(unittest.TestCase):
  S = [9.0] * 8
  N = 8
  MAX_Y = 650.0
  MIN_Y = 6.0

  def _batch(self, y_positions):
    return ChannelBatch(x_position=100.0, indices=[], channels=[], y_positions=dict(y_positions))

  def _optimize_batch_transitions(
    self, batches, spacings=None, num_channels=None, max_y=None, min_y=None
  ):
    _optimize_batch_transitions(
      batches,
      num_channels or self.N,
      spacings or self.S,
      max_y=max_y if max_y is not None else self.MAX_Y,
      min_y=min_y if min_y is not None else self.MIN_Y,
    )

  def _check_spacing(self, positions, spacings, num_channels):
    """Assert all adjacent channels satisfy minimum spacing."""
    for ch in range(num_channels - 1):
      spacing = _min_spacing_between(spacings, ch, ch + 1)
      diff = positions[ch] - positions[ch + 1]
      self.assertGreaterEqual(
        diff + 1e-9, spacing, f"channels {ch}-{ch + 1}: diff={diff:.2f} < spacing={spacing:.2f}"
      )

  def test_single_batch_fills_all_channels(self):
    batches = [self._batch({0: 400, 1: 391})]
    self._optimize_batch_transitions(batches)
    self.assertEqual(set(batches[0].y_positions.keys()), set(range(self.N)))

  def test_idle_channels_move_toward_future_batch(self):
    batches = [
      self._batch({0: 400, 1: 391}),
      self._batch({6: 200, 7: 191}),
    ]
    self._optimize_batch_transitions(batches)
    # Channels 6, 7 should be at or near their batch-1 targets
    self.assertAlmostEqual(batches[0].y_positions[6], 200.0)
    self.assertAlmostEqual(batches[0].y_positions[7], 191.0)

  def test_fixed_channels_not_modified(self):
    batches = [
      self._batch({0: 400, 1: 391}),
      self._batch({6: 200, 7: 191}),
    ]
    self._optimize_batch_transitions(batches)
    self.assertAlmostEqual(batches[0].y_positions[0], 400.0)
    self.assertAlmostEqual(batches[0].y_positions[1], 391.0)
    self.assertAlmostEqual(batches[1].y_positions[6], 200.0)
    self.assertAlmostEqual(batches[1].y_positions[7], 191.0)

  def test_spacing_constraints_satisfied(self):
    batches = [
      self._batch({0: 400, 1: 391}),
      self._batch({6: 200, 7: 191}),
    ]
    self._optimize_batch_transitions(batches)
    for batch in batches:
      self._check_spacing(batch.y_positions, self.S, self.N)

  def test_bounds_respected(self):
    batches = [self._batch({3: 300})]
    self._optimize_batch_transitions(batches)
    self.assertLessEqual(batches[0].y_positions[0], self.MAX_Y)
    self.assertGreaterEqual(batches[0].y_positions[self.N - 1], self.MIN_Y)

  def test_custom_bounds(self):
    batches = [self._batch({3: 300})]
    self._optimize_batch_transitions(batches, max_y=500.0, min_y=50.0)
    self.assertLessEqual(batches[0].y_positions[0], 500.0)
    self.assertGreaterEqual(batches[0].y_positions[self.N - 1], 50.0)

  def test_no_future_use_channels_packed_tightly(self):
    # Only one batch, channels 0,1 active. Channels 2-7 have no future use.
    batches = [self._batch({0: 400, 1: 391})]
    self._optimize_batch_transitions(batches)
    # Channels 2-7 should be packed at minimum spacing below channel 1
    for ch in range(2, self.N):
      spacing = _min_spacing_between(self.S, ch - 1, ch)
      expected = batches[0].y_positions[ch - 1] - spacing
      self.assertAlmostEqual(
        batches[0].y_positions[ch], expected, places=5, msg=f"channel {ch} not tightly packed"
      )

  def test_mixed_spacing(self):
    spacings = [8.98, 8.98, 17.96, 17.96, 9.0, 9.0, 9.0, 9.0]
    batches = [self._batch({0: 500, 1: 491})]
    self._optimize_batch_transitions(batches, spacings=spacings)
    self.assertEqual(set(batches[0].y_positions.keys()), set(range(self.N)))
    self._check_spacing(batches[0].y_positions, spacings, self.N)

  def test_three_batches_progressive_prepositioning(self):
    batches = [
      self._batch({0: 500, 1: 491}),
      self._batch({4: 350, 5: 341}),
      self._batch({6: 200, 7: 191}),
    ]
    self._optimize_batch_transitions(batches)
    # Batch 0: channels 4,5 should target their batch-1 positions
    self.assertAlmostEqual(batches[0].y_positions[4], 350.0)
    self.assertAlmostEqual(batches[0].y_positions[5], 341.0)
    # Batch 0: channels 6,7 should target their batch-2 positions
    self.assertAlmostEqual(batches[0].y_positions[6], 200.0)
    self.assertAlmostEqual(batches[0].y_positions[7], 191.0)
    # All batches satisfy spacing
    for batch in batches:
      self._check_spacing(batch.y_positions, self.S, self.N)

  def test_target_constrained_by_fixed_channels(self):
    # Channel 2 wants to be at 390 (future target), but channel 1 is fixed at 391.
    # Spacing constraint forces channel 2 down to 391 - 9 = 382.
    batches = [
      self._batch({1: 391}),
      self._batch({2: 390}),
    ]
    self._optimize_batch_transitions(batches)
    self.assertAlmostEqual(batches[0].y_positions[1], 391.0)
    self.assertLessEqual(batches[0].y_positions[2], 391.0 - 9.0 + 1e-9)
    self._check_spacing(batches[0].y_positions, self.S, self.N)


if __name__ == "__main__":
  unittest.main()
