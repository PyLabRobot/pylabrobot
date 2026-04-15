"""Tests for pipette_batch_scheduling module."""

import unittest
from typing import Iterable, List
from unittest.mock import MagicMock, patch

from pylabrobot.liquid_handling.pipette_batch_scheduling import (
  ChannelBatch,
  _span_required,
  enumerate_valid_batches,
  is_valid_batch,
  minimum_exact_cover,
  plan_batches,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


def _mock_container(
  cx: float, cy: float, size_y: float = 3.0, name: str = "well", no_go_zones: Iterable = ()
) -> Container:
  """Build a mock Container at (cx, cy).

  Default ``size_y=3.0`` mm is below ``2 * MIN_SPACING_EDGE = 4mm`` so
  ``compute_nonconsecutive_channel_offsets`` returns ``None`` without reaching
  ``compute_channel_offsets`` (which reads real Container internals). Tests
  that need multi-channel spread pass a larger ``size_y`` and ``@patch`` the
  offsets function.
  """
  c = MagicMock(spec=Container)
  c.name = name
  c.get_absolute_size_y.return_value = size_y
  c.get_location_wrt = MagicMock(return_value=Coordinate(cx, cy, 0))
  c.no_go_zones = list(no_go_zones)
  return c


def _mock_deck() -> Resource:
  return MagicMock(spec=Resource)


def _containers(xs: List[float], ys: List[float]) -> List[Container]:
  return [_mock_container(x, y) for x, y in zip(xs, ys)]


def _stub_batch(indices: Iterable[int]) -> ChannelBatch:
  """Build a ChannelBatch with only indices populated (cover-algorithm tests)."""
  return ChannelBatch(
    x_position=0.0,
    indices=list(indices),
    channels=list(indices),
    y_positions={},
  )


def _index_sets(batches: List[ChannelBatch]) -> List[frozenset]:
  return [frozenset(b.indices) for b in batches]


DECK = _mock_deck()


class TestSpanRequired(unittest.TestCase):
  """Rounded pairwise spacing sums."""

  SPACINGS = [8.98, 8.98, 17.96, 17.96]

  def test_uniform(self):
    # max(8.98, 8.98) = 8.98 -> ceil(89.8)/10 = 9.0
    self.assertAlmostEqual(_span_required(self.SPACINGS, 0, 1), 9.0)

  def test_mixed(self):
    # max(8.98, 17.96) = 17.96 -> ceil(179.6)/10 = 18.0
    self.assertAlmostEqual(_span_required(self.SPACINGS, 1, 2), 18.0)


class TestIsValidBatch(unittest.TestCase):
  """Container-aware batch-level validity predicate."""

  S = [9.0] * 8

  def test_empty_and_singleton_valid(self):
    c = _mock_container(100.0, 200.0)
    self.assertEqual(is_valid_batch([], [0], [c], self.S, DECK, x_tolerance=0.1), {})
    result = is_valid_batch([0], [0], [c], self.S, DECK, x_tolerance=0.1)
    assert result is not None
    self.assertAlmostEqual(result[0].x, 100.0)
    self.assertAlmostEqual(result[0].y, 200.0)

  def test_duplicate_channels_invalid(self):
    cs = _containers([100.0, 100.0], [200.0, 180.0])
    self.assertIsNone(is_valid_batch([0, 1], [3, 3], cs, self.S, DECK, x_tolerance=0.1))

  def test_x_tolerance_respected(self):
    cs = _containers([100.0, 100.08], [209.0, 200.0])
    self.assertIsNotNone(is_valid_batch([0, 1], [0, 1], cs, self.S, DECK, x_tolerance=0.1))
    cs = _containers([100.0, 100.2], [209.0, 200.0])
    self.assertIsNone(is_valid_batch([0, 1], [0, 1], cs, self.S, DECK, x_tolerance=0.1))

  def test_spacing_boundary(self):
    cs = _containers([100.0, 100.0], [209.0, 200.0])
    self.assertIsNotNone(is_valid_batch([0, 1], [0, 1], cs, self.S, DECK, x_tolerance=0.1))
    cs = _containers([100.0, 100.0], [208.9, 200.0])
    self.assertIsNone(is_valid_batch([0, 1], [0, 1], cs, self.S, DECK, x_tolerance=0.1))

  def test_monotone_y_enforced(self):
    # Higher channel must be at lower Y.
    cs = _containers([100.0, 100.0], [200.0, 209.0])
    self.assertIsNone(is_valid_batch([0, 1], [0, 1], cs, self.S, DECK, x_tolerance=0.1))

  def test_pairwise_sum_not_uniform_product(self):
    # ch0→ch3 with mixed [8.98, 8.98, 17.96, 17.96]: 9+18+18 = 45mm pairwise.
    sp = [8.98, 8.98, 17.96, 17.96]
    cs = _containers([100.0, 100.0], [245.0, 200.0])  # 45mm apart
    self.assertIsNotNone(is_valid_batch([0, 1], [0, 3], cs, sp, DECK, x_tolerance=0.1))
    cs = _containers([100.0, 100.0], [244.9, 200.0])  # 44.9mm — short
    self.assertIsNone(is_valid_batch([0, 1], [0, 3], cs, sp, DECK, x_tolerance=0.1))

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.compute_nonconsecutive_channel_offsets"
  )
  def test_container_fit_failure_rejects_batch(self, mock_offsets):
    mock_offsets.return_value = None
    c = _mock_container(100.0, 200.0, size_y=3.0)
    self.assertIsNone(is_valid_batch([0, 1], [0, 1], [c, c], self.S, DECK, x_tolerance=0.1))

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.compute_nonconsecutive_channel_offsets"
  )
  def test_container_fit_sets_spread_positions(self, mock_offsets):
    mock_offsets.return_value = [Coordinate(0, 4.5, 0), Coordinate(0, -4.5, 0)]
    c = _mock_container(100.0, 200.0, size_y=50.0)
    resolved = is_valid_batch([0, 1], [0, 1], [c, c], self.S, DECK, x_tolerance=0.1)
    assert resolved is not None
    self.assertAlmostEqual(resolved[0].y, 204.5)
    self.assertAlmostEqual(resolved[1].y, 195.5)

  def test_resource_offsets_override_auto_spread(self):
    # When explicit offsets are provided, compute_nonconsecutive_channel_offsets is not called.
    c = _mock_container(100.0, 200.0)
    offsets = [Coordinate(0, 10.0, 0), Coordinate(0, -10.0, 0)]
    resolved = is_valid_batch(
      [0, 1],
      [0, 1],
      [c, c],
      self.S,
      DECK,
      x_tolerance=0.1,
      resource_offsets=offsets,
    )
    assert resolved is not None
    self.assertAlmostEqual(resolved[0].y, 210.0)
    self.assertAlmostEqual(resolved[1].y, 190.0)


class TestEnumerateValidBatches(unittest.TestCase):
  """Backtracking enumeration."""

  S = [9.0] * 8

  def test_singletons_always_present(self):
    cs = _containers([100.0, 200.0, 300.0], [200.0, 200.0, 200.0])
    batches = enumerate_valid_batches([0, 1, 2], cs, self.S, DECK, x_tolerance=0.1)
    keys = _index_sets(batches)
    for i in range(3):
      self.assertIn(frozenset([i]), keys)

  def test_enumerates_all_compatible_subsets(self):
    # 3 channels, all pairwise compatible at 9mm spacing.
    cs = _containers([100.0] * 3, [218.0, 209.0, 200.0])
    batches = enumerate_valid_batches([0, 1, 2], cs, self.S, DECK, x_tolerance=0.1)
    expected = {
      frozenset([0]),
      frozenset([1]),
      frozenset([2]),
      frozenset([0, 1]),
      frozenset([1, 2]),
      frozenset([0, 2]),
      frozenset([0, 1, 2]),
    }
    self.assertEqual(set(_index_sets(batches)), expected)

  def test_excludes_invalid_pair(self):
    cs = _containers([100.0] * 3, [210.0, 205.0, 200.0])  # 0↔1 gap only 5mm
    batches = enumerate_valid_batches([0, 1, 2], cs, self.S, DECK, x_tolerance=0.1)
    keys = set(_index_sets(batches))
    self.assertNotIn(frozenset([0, 1]), keys)
    self.assertNotIn(frozenset([0, 1, 2]), keys)

  def test_different_x_never_share(self):
    cs = _containers([100.0, 200.0], [209.0, 200.0])
    batches = enumerate_valid_batches([0, 1], cs, self.S, DECK, x_tolerance=0.1)
    self.assertNotIn(frozenset([0, 1]), _index_sets(batches))


class TestMinimumExactCover(unittest.TestCase):
  """Branch-and-bound minimum partition."""

  def test_single_job(self):
    cover = minimum_exact_cover(1, [_stub_batch([0])])
    self.assertEqual(_index_sets(cover), [frozenset([0])])

  def test_prefers_larger_batch(self):
    batches = [_stub_batch(ix) for ix in ([0], [1], [2], [0, 1], [0, 1, 2])]
    cover = minimum_exact_cover(3, batches)
    self.assertEqual(_index_sets(cover), [frozenset([0, 1, 2])])

  def test_forces_two_when_no_triple_valid(self):
    batches = [_stub_batch(ix) for ix in ([0], [1], [2], [0, 1], [1, 2])]
    cover = minimum_exact_cover(3, batches)
    self.assertEqual(len(cover), 2)
    self.assertEqual(frozenset().union(*_index_sets(cover)), frozenset([0, 1, 2]))

  def test_falls_back_to_singletons(self):
    batches = [_stub_batch([0]), _stub_batch([1])]
    self.assertEqual(len(minimum_exact_cover(2, batches)), 2)

  def test_greedy_largest_first_beaten_by_branch_and_bound(self):
    # Greedy takes {0,1,2,3} first, then {4},{5} → 3 batches.
    # Optimum is {0,1,2} + {3,4,5} = 2 batches.
    batches = [
      _stub_batch(ix) for ix in ([0, 1, 2, 3], [0, 1, 2], [3, 4, 5], [0], [1], [2], [3], [4], [5])
    ]
    cover = minimum_exact_cover(6, batches)
    self.assertEqual(len(cover), 2)
    self.assertEqual(frozenset().union(*_index_sets(cover)), frozenset(range(6)))


class TestPlanBatches(unittest.TestCase):
  """End-to-end planning: X grouping, Y batching, phantoms, errors."""

  S = [9.0] * 8

  def test_spacing_boundary(self):
    batches = plan_batches(
      [0, 1], _containers([100.0] * 2, [209.0, 200.0]), self.S, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)
    batches = plan_batches(
      [0, 1], _containers([100.0] * 2, [208.9, 200.0]), self.S, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)

  def test_same_y_serializes(self):
    batches = plan_batches(
      [0, 1, 2], _containers([100.0] * 3, [200.0] * 3), self.S, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 3)

  def test_x_tolerance_boundary(self):
    batches = plan_batches(
      [0, 1], _containers([100.0, 100.05], [270.0, 261.0]), self.S, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)
    batches = plan_batches(
      [0, 1], _containers([100.0, 100.2], [270.0, 270.0]), self.S, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)

  def test_x_groups_sorted_ascending(self):
    batches = plan_batches(
      [0, 1, 2], _containers([300.0, 100.0, 200.0], [270.0] * 3), self.S, DECK, x_tolerance=0.1
    )
    xs = [b.x_position for b in batches]
    self.assertEqual(xs, sorted(xs))

  def test_empty_raises(self):
    with self.assertRaises(ValueError):
      plan_batches([], [], self.S, DECK, x_tolerance=0.1)

  def test_mismatched_lengths_raises(self):
    with self.assertRaises(ValueError):
      plan_batches([0, 1], _containers([100.0], [200.0]), self.S, DECK, x_tolerance=0.1)

  def test_duplicate_channels_serialized(self):
    batches = plan_batches(
      [0, 0], _containers([100.0] * 2, [200.0] * 2), self.S, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)

  def test_duplicate_channels_three_ops(self):
    batches = plan_batches(
      [0, 0, 0], _containers([100.0] * 3, [200.0] * 3), self.S, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 3)

  def test_phantoms_interpolated_at_spacing(self):
    # 6 channels in one batch with non-consecutive channel indices.
    batches = plan_batches(
      [0, 1, 2, 5, 6, 7],
      _containers([100.0] * 6, [300.0, 291.0, 282.0, 255.0, 246.0, 237.0]),
      [9.0] * 8,
      DECK,
      x_tolerance=0.1,
    )
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertIn(3, y)
    self.assertIn(4, y)
    self.assertAlmostEqual(y[3], 282.0 - 9.0)
    self.assertAlmostEqual(y[4], 282.0 - 18.0)

  def test_phantoms_only_within_batch(self):
    # Two separate batches — no phantoms bridging them.
    batches = plan_batches(
      [0, 3], _containers([100.0] * 2, [200.0, 250.0]), [9.0] * 4, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)
    for batch in batches:
      self.assertEqual(len(batch.y_positions), 1)

  def test_indices_map_back_correctly(self):
    use_channels = [3, 7, 0]
    batches = plan_batches(
      use_channels,
      _containers([100.0] * 3, [261.0, 237.0, 270.0]),
      [9.0] * 8,
      DECK,
      x_tolerance=0.1,
    )
    all_indices = [idx for b in batches for idx in b.indices]
    self.assertEqual(sorted(all_indices), [0, 1, 2])
    for batch in batches:
      for idx, ch in zip(batch.indices, batch.channels):
        self.assertEqual(use_channels[idx], ch)

  def test_mixed_spacing_boundary(self):
    # 1mL (ch0,1) + 5mL (ch2,3): 18mm required between ch1 and ch2.
    sp = [8.98, 8.98, 17.96, 17.96]
    batches = plan_batches(
      [1, 2], _containers([100.0] * 2, [217.9, 200.0]), sp, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 2)
    batches = plan_batches(
      [1, 2], _containers([100.0] * 2, [218.0, 200.0]), sp, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)

  def test_mixed_phantoms_use_pairwise_spacing(self):
    sp = [8.98, 8.98, 17.96, 17.96]
    batches = plan_batches(
      [0, 3], _containers([100.0] * 2, [245.0, 200.0]), sp, DECK, x_tolerance=0.1
    )
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    # ch0→ch1: 9.0, ch1→ch2: 18.0
    self.assertAlmostEqual(y[1], 245.0 - 9.0)
    self.assertAlmostEqual(y[2], 245.0 - 9.0 - 18.0)

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.compute_nonconsecutive_channel_offsets"
  )
  def test_auto_spread_same_container(self, mock_offsets):
    mock_offsets.return_value = [Coordinate(0, 4.5, 0), Coordinate(0, -4.5, 0)]
    trough = _mock_container(100.0, 200.0, size_y=50.0, name="trough")
    batches = plan_batches([0, 1], [trough, trough], self.S, DECK, x_tolerance=0.1)
    self.assertEqual(len(batches), 1)
    y = batches[0].y_positions
    self.assertAlmostEqual(y[0], 204.5)
    self.assertAlmostEqual(y[1], 195.5)

  def test_narrow_container_serializes(self):
    # size_y=3 means compute_nonconsecutive_channel_offsets returns None → singletons only.
    well = _mock_container(100.0, 200.0, size_y=3.0, name="narrow")
    batches = plan_batches([0, 1], [well, well], self.S, DECK, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)


class TestPlanBatchesNoGoZones(unittest.TestCase):
  """Per-batch container-fit decisions let plan_batches batch subsets that a
  fixed up-front spread cannot (the motivating case for no-go zones)."""

  S = [9.0] * 8

  @patch(
    "pylabrobot.liquid_handling.pipette_batch_scheduling.compute_nonconsecutive_channel_offsets"
  )
  def test_fits_pair_but_not_triple_in_container(self, mock_offsets):
    # Container fits any adjacent pair but not three channels at once.
    def fake_offsets(container, channels, spacings):
      if len(channels) <= 2 and channels == sorted(channels) and channels[-1] - channels[0] <= 1:
        return [Coordinate(0, 4.5, 0), Coordinate(0, -4.5, 0)][: len(channels)]
      return None

    mock_offsets.side_effect = fake_offsets
    c = _mock_container(100.0, 200.0, size_y=12.0, name="small")
    batches = plan_batches([0, 1, 2], [c, c, c], self.S, DECK, x_tolerance=0.1)
    self.assertEqual(len(batches), 2)
    self.assertEqual(sorted(len(b.channels) for b in batches), [1, 2])


if __name__ == "__main__":
  unittest.main()
