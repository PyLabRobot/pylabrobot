import unittest
from unittest.mock import MagicMock, patch

from pylabrobot.liquid_handling.channel_positioning import (
  _centers_to_offsets,
  _distribute_channels,
  _get_compartments,
  _position_channels_tight,
  _position_channels_wide,
  _resolve_channel_spacings,
  _space_needed,
  compute_channel_offsets,
  compute_single_container_offsets,
  required_spacing_between,
)
from pylabrobot.liquid_handling.errors import ChannelsDoNotFitError
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


def _make_container(size_y, no_go_zones=None, name="test"):
  return Container(
    name=name,
    size_x=19.0,
    size_y=size_y,
    size_z=50.0,
    no_go_zones=no_go_zones or [],
  )


class TestGetCompartments(unittest.TestCase):
  def test_no_zones(self):
    c = _make_container(100)
    result = _get_compartments(c)
    self.assertEqual(result, [(2.0, 98.0)])

  def test_single_center_zone(self):
    c = _make_container(
      100,
      [
        (Coordinate(0, 48, 0), Coordinate(19, 52, 50)),
      ],
    )
    result = _get_compartments(c)
    self.assertEqual(len(result), 2)
    self.assertAlmostEqual(result[0][0], 2.0)
    self.assertAlmostEqual(result[0][1], 46.0)
    self.assertAlmostEqual(result[1][0], 54.0)
    self.assertAlmostEqual(result[1][1], 98.0)

  def test_zone_at_front(self):
    c = _make_container(
      50,
      [
        (Coordinate(0, 0, 0), Coordinate(19, 10, 50)),
      ],
    )
    result = _get_compartments(c)
    self.assertEqual(len(result), 1)
    self.assertAlmostEqual(result[0][0], 12.0)
    self.assertAlmostEqual(result[0][1], 48.0)

  def test_zone_at_back(self):
    c = _make_container(
      50,
      [
        (Coordinate(0, 40, 0), Coordinate(19, 50, 50)),
      ],
    )
    result = _get_compartments(c)
    self.assertEqual(len(result), 1)
    self.assertAlmostEqual(result[0][0], 2.0)
    self.assertAlmostEqual(result[0][1], 38.0)

  def test_compartment_too_narrow_is_skipped(self):
    # 3mm raw compartment < 2*2mm edge clearance -> skipped
    c = _make_container(
      20,
      [
        (Coordinate(0, 3, 0), Coordinate(19, 17, 50)),
      ],
    )
    result = _get_compartments(c)
    # front compartment [0, 3] -> too narrow (3mm < 4mm)
    # back compartment [17, 20] -> too narrow (3mm < 4mm)
    self.assertEqual(result, [])

  def test_custom_edge_clearance(self):
    c = _make_container(10)
    result = _get_compartments(c, edge_clearance=1.0)
    self.assertEqual(result, [(1.0, 9.0)])

  def test_multiple_zones(self):
    c = _make_container(
      100,
      [
        (Coordinate(0, 30, 0), Coordinate(19, 35, 50)),
        (Coordinate(0, 65, 0), Coordinate(19, 70, 50)),
      ],
    )
    result = _get_compartments(c)
    self.assertEqual(len(result), 3)

  def test_overlapping_zones(self):
    c = _make_container(
      100,
      [
        (Coordinate(0, 30, 0), Coordinate(19, 50, 50)),
        (Coordinate(0, 40, 0), Coordinate(19, 60, 50)),
      ],
    )
    result = _get_compartments(c)
    # Should merge: gap is [0,30] and [60,100]
    self.assertEqual(len(result), 2)
    self.assertAlmostEqual(result[0][0], 2.0)
    self.assertAlmostEqual(result[0][1], 28.0)
    self.assertAlmostEqual(result[1][0], 62.0)
    self.assertAlmostEqual(result[1][1], 98.0)


class TestResolveChannelSpacings(unittest.TestCase):
  def test_none_returns_defaults(self):
    result = _resolve_channel_spacings(4)
    self.assertEqual(result, [9.0, 9.0, 9.0, 9.0])

  def test_explicit_spacings(self):
    result = _resolve_channel_spacings(3, [9.0, 18.0, 9.0])
    self.assertEqual(result, [9.0, 18.0, 9.0])

  def test_wrong_length_raises(self):
    with self.assertRaises(ValueError):
      _resolve_channel_spacings(3, [9.0, 9.0])

  def test_single_channel(self):
    result = _resolve_channel_spacings(1, [18.0, 9.0])
    self.assertEqual(result, [18.0])

  def test_zero_channels(self):
    result = _resolve_channel_spacings(0)
    self.assertEqual(result, [])


class TestRequiredSpacingBetween(unittest.TestCase):
  def test_equal_spacings(self):
    spacings = [9.0, 9.0, 9.0]
    result = required_spacing_between(spacings, 0, 1)
    self.assertAlmostEqual(result, 9.0)

  def test_mixed_spacings(self):
    spacings = [9.0, 18.0]
    result = required_spacing_between(spacings, 0, 1)
    # (9/2 + 18/2) = 13.5, ceil to 0.1 = 13.5
    self.assertAlmostEqual(result, 13.5)

  def test_non_adjacent(self):
    spacings = [9.0, 9.0, 9.0]
    result = required_spacing_between(spacings, 0, 2)
    self.assertAlmostEqual(result, 18.0)

  def test_ceiling_rounding(self):
    # 7.0/2 + 8.0/2 = 7.5 -> ceil(75)/10 = 7.5
    spacings = [7.0, 8.0]
    result = required_spacing_between(spacings, 0, 1)
    self.assertAlmostEqual(result, 7.5)

    # 7.0/2 + 7.1/2 = 7.05 -> ceil(70.5)/10 = 7.1
    spacings = [7.0, 7.1]
    result = required_spacing_between(spacings, 0, 1)
    self.assertAlmostEqual(result, 7.1)


class TestPositionChannelsWide(unittest.TestCase):
  def test_single_channel_centered(self):
    result = _position_channels_wide(100.0, [9.0])
    self.assertAlmostEqual(result[0], 50.0)

  def test_two_channels_equal_spacing(self):
    result = _position_channels_wide(90.0, [9.0, 9.0])
    self.assertAlmostEqual(result[0], 30.0)
    self.assertAlmostEqual(result[1], 60.0)

  def test_too_small_raises(self):
    with self.assertRaises(ValueError):
      _position_channels_wide(5.0, [9.0, 9.0])

  def test_mixed_spacings(self):
    # With mixed spacings, classic gap may not work; should still produce valid result
    result = _position_channels_wide(100.0, [9.0, 18.0, 9.0])
    self.assertEqual(len(result), 3)
    # Channels should be sorted ascending
    self.assertLess(result[0], result[1])
    self.assertLess(result[1], result[2])
    # Gaps should respect minimum required spacings
    gap_01 = result[1] - result[0]
    gap_12 = result[2] - result[1]
    self.assertGreaterEqual(gap_01, required_spacing_between([9.0, 18.0, 9.0], 0, 1) - 0.01)
    self.assertGreaterEqual(gap_12, required_spacing_between([9.0, 18.0, 9.0], 1, 2) - 0.01)


class TestPositionChannelsTight(unittest.TestCase):
  def test_single_channel_centered(self):
    result = _position_channels_tight(100.0, [9.0])
    self.assertAlmostEqual(result[0], 50.0)

  def test_two_channels_centered(self):
    result = _position_channels_tight(100.0, [9.0, 9.0])
    gap = result[1] - result[0]
    self.assertAlmostEqual(gap, 9.0)
    center = (result[0] + result[1]) / 2
    self.assertAlmostEqual(center, 50.0)

  def test_too_small_raises(self):
    with self.assertRaises(ValueError):
      _position_channels_tight(5.0, [9.0, 9.0])

  def test_three_channels(self):
    result = _position_channels_tight(100.0, [9.0, 9.0, 9.0])
    self.assertEqual(len(result), 3)
    self.assertAlmostEqual(result[1] - result[0], 9.0)
    self.assertAlmostEqual(result[2] - result[1], 9.0)


class TestCentersToOffsets(unittest.TestCase):
  def test_sorted_back_to_front(self):
    resource = Resource(name="r", size_x=10, size_y=100, size_z=10)
    centers = [20.0, 50.0, 80.0]
    offsets = _centers_to_offsets(centers, resource)
    # Should be sorted descending by y
    self.assertGreater(offsets[0].y, offsets[1].y)
    self.assertGreater(offsets[1].y, offsets[2].y)

  def test_offset_relative_to_center(self):
    resource = Resource(name="r", size_x=10, size_y=100, size_z=10)
    offsets = _centers_to_offsets([50.0], resource)
    self.assertAlmostEqual(offsets[0].y, 0.0)

  def test_x_and_z_are_zero(self):
    resource = Resource(name="r", size_x=10, size_y=100, size_z=10)
    offsets = _centers_to_offsets([30.0, 70.0], resource)
    for o in offsets:
      self.assertAlmostEqual(o.x, 0.0)
      self.assertAlmostEqual(o.z, 0.0)


class TestSpaceNeeded(unittest.TestCase):
  def test_single_channel(self):
    self.assertAlmostEqual(_space_needed([9.0]), 0.0)

  def test_zero_channels(self):
    self.assertAlmostEqual(_space_needed([]), 0.0)

  def test_two_equal(self):
    self.assertAlmostEqual(_space_needed([9.0, 9.0]), 9.0)

  def test_three_equal(self):
    self.assertAlmostEqual(_space_needed([9.0, 9.0, 9.0]), 18.0)

  def test_mixed(self):
    result = _space_needed([9.0, 18.0])
    expected = required_spacing_between([9.0, 18.0], 0, 1)
    self.assertAlmostEqual(result, expected)


class TestDistributeChannels(unittest.TestCase):
  def test_equal_compartments(self):
    compartments = [(0.0, 40.0), (50.0, 90.0)]
    result = _distribute_channels(compartments, 4, [9.0] * 4)
    self.assertEqual(sum(result), 4)
    self.assertEqual(result, [2, 2])

  def test_unequal_compartments(self):
    compartments = [(0.0, 60.0), (70.0, 90.0)]
    result = _distribute_channels(compartments, 3, [9.0] * 3)
    self.assertEqual(sum(result), 3)
    # Wider compartment should get more
    self.assertGreaterEqual(result[0], result[1])

  def test_single_compartment(self):
    compartments = [(0.0, 100.0)]
    result = _distribute_channels(compartments, 3, [9.0] * 3)
    self.assertEqual(result, [3])

  def test_channels_dont_fit_raises(self):
    # Tiny compartments can't hold many channels
    compartments = [(0.0, 5.0), (10.0, 15.0)]
    with self.assertRaises(ChannelsDoNotFitError):
      _distribute_channels(compartments, 10, [9.0] * 10)

  def test_shifting_needed(self):
    # One narrow + one wide compartment, proportional would overload the narrow one
    compartments = [(0.0, 10.0), (15.0, 100.0)]
    result = _distribute_channels(compartments, 8, [9.0] * 8)
    self.assertEqual(sum(result), 8)
    # Narrow compartment can fit at most 1 channel (10mm width, 9mm spacing)
    self.assertLessEqual(result[0], 1)


class TestComputeChannelOffsets(unittest.TestCase):
  def test_custom_returns_zeros(self):
    resource = Resource(name="r", size_x=10, size_y=100, size_z=10)
    result = compute_channel_offsets(resource, 3, spread="custom")
    for o in result:
      self.assertAlmostEqual(o.x, 0.0)
      self.assertAlmostEqual(o.y, 0.0)
      self.assertAlmostEqual(o.z, 0.0)

  def test_invalid_spread_raises(self):
    resource = Resource(name="r", size_x=10, size_y=100, size_z=10)
    with self.assertRaises(ValueError):
      compute_channel_offsets(resource, 2, spread="invalid")

  def test_wide_plain_resource(self):
    resource = Resource(name="r", size_x=10, size_y=90, size_z=10)
    result = compute_channel_offsets(resource, 2, spread="wide")
    self.assertEqual(len(result), 2)
    # Should be sorted back-to-front (descending y)
    self.assertGreater(result[0].y, result[1].y)

  def test_tight_plain_resource(self):
    resource = Resource(name="r", size_x=10, size_y=90, size_z=10)
    result = compute_channel_offsets(resource, 2, spread="tight")
    self.assertEqual(len(result), 2)
    gap = abs(result[0].y - result[1].y)
    self.assertAlmostEqual(gap, 9.0)

  def test_single_channel(self):
    resource = Resource(name="r", size_x=10, size_y=90, size_z=10)
    result = compute_channel_offsets(resource, 1)
    self.assertEqual(len(result), 1)
    self.assertAlmostEqual(result[0].y, 0.0)

  def test_with_no_go_zones(self):
    c = _make_container(
      100,
      [
        (Coordinate(0, 48, 0), Coordinate(19, 52, 50)),
      ],
    )
    result = compute_channel_offsets(c, 2)
    self.assertEqual(len(result), 2)
    # Channels should be in different compartments (one positive, one negative offset)
    center_y = 50.0
    positions = sorted([center_y + o.y for o in result])
    # First should be in [2, 46], second in [54, 98]
    self.assertLess(positions[0], 48.0)
    self.assertGreater(positions[1], 52.0)

  def test_no_go_zones_too_restrictive_raises(self):
    # Almost entire container is a no-go zone
    c = _make_container(
      20,
      [
        (Coordinate(0, 2, 0), Coordinate(19, 18, 50)),
      ],
    )
    with self.assertRaises(ChannelsDoNotFitError):
      compute_channel_offsets(c, 2)

  def test_channel_spacings(self):
    resource = Resource(name="r", size_x=10, size_y=100, size_z=10)
    result = compute_channel_offsets(resource, 2, channel_spacings=[9.0, 18.0])
    self.assertEqual(len(result), 2)
    gap = abs(result[0].y - result[1].y)
    self.assertGreaterEqual(gap, 13.5 - 0.01)

  def test_many_channels_no_go_zones(self):
    # 3 no-go zones creating 4 compartments
    c = _make_container(
      142.5,
      [
        (Coordinate(0, 39.7, 0), Coordinate(19, 42.2, 50)),
        (Coordinate(0, 73.5, 0), Coordinate(19, 76.0, 50)),
        (Coordinate(0, 107.3, 0), Coordinate(19, 109.8, 50)),
      ],
    )
    for n in [1, 2, 3, 4, 5, 6, 7, 8, 9]:
      result = compute_channel_offsets(c, n)
      self.assertEqual(len(result), n)

  def test_offsets_respect_no_go_zones(self):
    c = _make_container(
      90,
      [
        (Coordinate(0, 43, 0), Coordinate(19, 47, 50)),
      ],
    )
    center_y = 45.0
    for n in range(1, 9):
      result = compute_channel_offsets(c, n)
      positions = [center_y + o.y for o in result]
      for p in positions:
        # No channel center should be inside the no-go zone
        self.assertFalse(
          43.0 <= p <= 47.0, f"Channel at y={p} is inside no-go zone [43, 47] with {n} channels"
        )

  def test_wide_vs_tight_gap_difference(self):
    resource = Resource(name="r", size_x=10, size_y=100, size_z=10)
    wide = compute_channel_offsets(resource, 3, spread="wide")
    tight = compute_channel_offsets(resource, 3, spread="tight")
    wide_gap = abs(wide[0].y - wide[-1].y)
    tight_gap = abs(tight[0].y - tight[-1].y)
    self.assertGreaterEqual(wide_gap, tight_gap - 0.01)


class TestComputeSingleContainerOffsets(unittest.TestCase):
  S = [9.0] * 8

  def _mock_container(self, size_y: float):
    c = MagicMock(spec=["get_absolute_size_y"])
    c.get_absolute_size_y.return_value = size_y
    return c

  @patch("pylabrobot.liquid_handling.channel_positioning.compute_channel_offsets")
  def test_even_span_no_center_offset(self, mock_offsets):
    mock_offsets.return_value = [Coordinate(0, 4.5, 0), Coordinate(0, -4.5, 0)]
    result = compute_single_container_offsets(self._mock_container(50.0), [0, 1], self.S)
    assert result is not None
    self.assertAlmostEqual(result[0].y, 4.5)
    self.assertAlmostEqual(result[1].y, -4.5)

  @patch("pylabrobot.liquid_handling.channel_positioning.compute_channel_offsets")
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

  @patch("pylabrobot.liquid_handling.channel_positioning.compute_channel_offsets")
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

  @patch("pylabrobot.liquid_handling.channel_positioning.compute_channel_offsets")
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
