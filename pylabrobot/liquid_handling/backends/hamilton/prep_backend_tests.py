"""Tests for Hamilton Prep backend logic and command generation.

Verifies PrepBackend method behavior: how operations are transformed into
commands, geometry computed, command variants dispatched, and state managed.
All tests mock client.send_command — no real TCP connection required.
"""

import asyncio
import math
import unittest
import unittest.mock
from types import SimpleNamespace

from pylabrobot.liquid_handling.backends.hamilton import prep_commands as PrepCmd
from pylabrobot.liquid_handling.backends.hamilton.prep_backend import (
  CalibrationCommandReport,
  PrepBackend,
  PrepCalibrationSession,
  _absolute_z_from_well,
  _build_container_segments,
  _effective_radius,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import ObjectInfo
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.liquid_classes.hamilton import get_star_liquid_class
from pylabrobot.liquid_handling.standard import (
  Drop,
  GripDirection,
  Pickup,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import (
  Coordinate,
  Cor_96_wellplate_360ul_Fb,
  CrossSectionType,
  Deck,
  HamiltonTip,
  Liquid,
  Plate,
  PrepDeck,
  Rotation,
  TipPickupMethod,
  TipSize,
  Trash,
  Well,
  hamilton_96_tiprack_300uL_filter,
)

# =============================================================================
# Setup helpers
# =============================================================================

_MLPREP_ADDR = Address(1, 1, 0x0015)
_PIPETTOR_ADDR = Address(1, 1, 0x00E0)
_COORD_ADDR = Address(1, 1, 0x00C0)
_DECK_CONFIG_ADDR = Address(1, 1, 0x00D0)
_MPH_ADDR = Address(1, 1, 0x00F0)
_SERVICE_ADDR = Address(1, 1, 0x0017)
_CALIBRATION_ADDR = Address(1, 1, 0x00E2)

_TRAVERSE_HEIGHT = 96.97


def _setup_backend(num_channels: int = 2, has_mph: bool = False) -> PrepBackend:
  """PrepBackend with pre-resolved interfaces, bypassing TCP."""
  backend = PrepBackend(host="192.168.100.102", port=2000)
  backend._num_channels = num_channels
  backend._has_mph = has_mph
  backend._user_traverse_height = _TRAVERSE_HEIGHT
  backend._config = PrepCmd.InstrumentConfig(
    deck_bounds=PrepCmd.DeckBounds(0.0, 300.0, 0.0, 320.0, 0.0, 100.0),
    has_enclosure=False,
    safe_speeds_enabled=False,
    deck_sites=(),
    waste_sites=(),
    default_traverse_height=_TRAVERSE_HEIGHT,
    num_channels=num_channels,
    has_mph=has_mph,
  )
  backend._resolver._resolved["mlprep"] = _MLPREP_ADDR
  backend._resolver._resolved["pipettor"] = _PIPETTOR_ADDR
  backend._resolver._resolved["coordinator"] = _COORD_ADDR
  backend._resolver._resolved["deck_config"] = _DECK_CONFIG_ADDR
  backend._resolver._resolved["mph"] = _MPH_ADDR if has_mph else None
  backend._resolver._resolved["mlprep_service"] = _SERVICE_ADDR
  backend._resolver._resolved["calibration"] = _CALIBRATION_ADDR
  backend._supports_v2_pipetting = True
  backend.setup_finished = True
  return backend


def _setup_backend_with_deck(
  num_channels: int = 2,
  has_mph: bool = False,
  with_core_grippers: bool = False,
) -> tuple:
  """Returns (backend, deck, tip_rack, plate)."""
  backend = _setup_backend(num_channels=num_channels, has_mph=has_mph)
  deck = PrepDeck(with_core_grippers=with_core_grippers)
  backend._deck = deck

  tip_rack = hamilton_96_tiprack_300uL_filter("tip_rack")
  deck[0] = tip_rack

  plate = Cor_96_wellplate_360ul_Fb("plate")
  deck[1] = plate

  return backend, deck, tip_rack, plate


def _get_commands(mock_send, cmd_type):
  """Extract sent commands of a specific type from mock call list."""
  return [call.args[0] for call in mock_send.call_args_list if isinstance(call.args[0], cmd_type)]


# =============================================================================
# 1. Helper function logic
# =============================================================================


class TestPrepHelperFunctions(unittest.TestCase):
  """Tests for pure geometry helper functions — no mocking."""

  def _make_circular_well(self, diameter: float, height: float) -> Well:
    return Well(
      name="w",
      size_x=diameter,
      size_y=diameter,
      size_z=height,
      cross_section_type=CrossSectionType.CIRCLE,
    )

  def _make_rect_well(self, x: float, y: float, height: float) -> Well:
    return Well(
      name="w",
      size_x=x,
      size_y=y,
      size_z=height,
      cross_section_type=CrossSectionType.RECTANGLE,
    )

  # --- _effective_radius ---

  def test_effective_radius_circular(self):
    well = self._make_circular_well(diameter=8.0, height=10.0)
    self.assertAlmostEqual(_effective_radius(well), 4.0)

  def test_effective_radius_rectangular(self):
    well = self._make_rect_well(x=6.0, y=4.0, height=10.0)
    expected = math.sqrt(6.0 * 4.0 / math.pi)
    self.assertAlmostEqual(_effective_radius(well), expected)

  def test_effective_radius_non_well_uses_size_x(self):
    # For non-Well objects the function falls back to size_x / 2
    from pylabrobot.resources import Resource

    resource = Resource(name="r", size_x=10.0, size_y=10.0, size_z=5.0)
    self.assertAlmostEqual(_effective_radius(resource), 5.0)

  # --- _build_container_segments ---

  def test_build_container_segments_non_well(self):
    from pylabrobot.resources import Resource

    resource = Resource(name="r", size_x=10.0, size_y=10.0, size_z=5.0)
    segs = _build_container_segments(resource)
    self.assertEqual(segs, [])

  def test_build_container_segments_simple_circular(self):
    well = self._make_circular_well(diameter=6.0, height=10.0)
    segs = _build_container_segments(well)
    self.assertEqual(len(segs), 1)
    expected_area = math.pi * (3.0**2)
    self.assertIsInstance(segs[0], PrepCmd.SegmentDescriptor)
    self.assertAlmostEqual(segs[0].area_top, expected_area, places=4)
    self.assertAlmostEqual(segs[0].area_bottom, expected_area, places=4)
    self.assertAlmostEqual(segs[0].height, 10.0, places=4)

  def test_build_container_segments_simple_rect(self):
    well = self._make_rect_well(x=6.0, y=4.0, height=10.0)
    segs = _build_container_segments(well)
    self.assertEqual(len(segs), 1)
    expected_area = 6.0 * 4.0
    self.assertAlmostEqual(segs[0].area_top, expected_area, places=4)
    self.assertAlmostEqual(segs[0].height, 10.0, places=4)

  def test_build_container_segments_heights_sum_to_size_z(self):
    """Wells with compute_height_volume should produce 10 segments summing to size_z."""
    area = math.pi * 3.0**2
    well = Well(
      name="w",
      size_x=6.0,
      size_y=6.0,
      size_z=10.0,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=lambda h: area * h,
      compute_height_from_volume=lambda v: v / area,
    )
    segs = _build_container_segments(well)
    self.assertEqual(len(segs), 10)
    total_height = sum(s.height for s in segs)
    self.assertAlmostEqual(total_height, well.get_size_z(), places=4)

  # --- _absolute_z_from_well ---

  def test_absolute_z_from_well_geometry(self):
    self._make_circular_well(diameter=6.0, height=10.0)
    deck = PrepDeck()
    deck[0] = Cor_96_wellplate_360ul_Fb("p")
    plate = deck[0].resource
    assert plate is not None and isinstance(plate, Plate)
    # Use a plate well with known absolute location
    from pylabrobot.liquid_handling.standard import SingleChannelAspiration

    tip = hamilton_96_tiprack_300uL_filter("tr").get_item("A1").get_tip()
    op = SingleChannelAspiration(
      resource=plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=tip,
      volume=10.0,
      flow_rate=None,
      liquid_height=0.0,
      blow_out_air_volume=None,
      mix=None,
    )
    well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z = _absolute_z_from_well(op)
    size_z = op.resource.get_size_z()
    # liquid_surface_z = well_bottom_z + liquid_height
    self.assertAlmostEqual(liquid_surface_z - well_bottom_z, op.liquid_height or 0.0, places=4)
    # top_of_well_z = well_bottom_z + size_z (cavity location is at cavity_bottom)
    loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
    self.assertAlmostEqual(top_of_well_z, loc.z + size_z, places=4)
    # z_air_z = top_of_well_z + 2mm margin
    self.assertAlmostEqual(z_air_z, top_of_well_z + 2.0, places=4)

  def test_absolute_z_from_well_liquid_height_offset(self):
    plate = Cor_96_wellplate_360ul_Fb("p")
    deck = PrepDeck()
    deck[0] = plate
    tip = hamilton_96_tiprack_300uL_filter("tr").get_item("A1").get_tip()
    well = plate.get_item("A1")

    def make_op(liquid_height):
      return SingleChannelAspiration(
        resource=well,
        offset=Coordinate.zero(),
        tip=tip,
        volume=10.0,
        flow_rate=None,
        liquid_height=liquid_height,
        blow_out_air_volume=None,
        mix=None,
      )

    _, ls_0, _, _ = _absolute_z_from_well(make_op(0.0))
    _, ls_5, _, _ = _absolute_z_from_well(make_op(5.0))
    self.assertAlmostEqual(ls_5 - ls_0, 5.0, places=4)


# =============================================================================
# 2. Backend unit tests (properties, state, validation)
# =============================================================================


class TestPrepBackendUnit(unittest.TestCase):
  """Backend construction, properties, and traverse height resolution."""

  def setUp(self):
    super().setUp()
    try:
      asyncio.get_running_loop()
    except RuntimeError:
      asyncio.set_event_loop(asyncio.new_event_loop())

  def test_num_channels_raises_before_setup(self):
    backend = PrepBackend(host="localhost", port=2000)
    with self.assertRaises(RuntimeError):
      _ = backend.num_channels

  def test_has_mph_false_before_setup(self):
    backend = PrepBackend(host="localhost", port=2000)
    self.assertFalse(backend.has_mph)

  def test_num_arms_no_deck(self):
    # Backend without _deck set (never assigned)
    backend = PrepBackend(host="localhost", port=2000)
    backend._num_channels = 2
    self.assertEqual(backend.num_arms, 0)

  def test_num_arms_with_core_grippers(self):
    backend, _, _, _ = _setup_backend_with_deck(with_core_grippers=True)
    self.assertEqual(backend.num_arms, 1)

  def test_num_arms_without_core_grippers(self):
    backend, _, _, _ = _setup_backend_with_deck(with_core_grippers=False)
    self.assertEqual(backend.num_arms, 0)

  def test_resolve_traverse_height_explicit(self):
    backend = _setup_backend()
    self.assertAlmostEqual(backend._resolve_traverse_height(50.0), 50.0)

  def test_resolve_traverse_height_user_set(self):
    backend = PrepBackend(host="localhost", port=2000)
    backend._user_traverse_height = 80.0
    self.assertAlmostEqual(backend._resolve_traverse_height(None), 80.0)

  def test_resolve_traverse_height_probed(self):
    backend = PrepBackend(host="localhost", port=2000)
    backend._config = PrepCmd.InstrumentConfig(
      deck_bounds=None,
      has_enclosure=False,
      safe_speeds_enabled=False,
      deck_sites=(),
      waste_sites=(),
      default_traverse_height=75.0,
    )
    self.assertAlmostEqual(backend._resolve_traverse_height(None), 75.0)

  def test_resolve_traverse_height_nothing_raises(self):
    backend = PrepBackend(host="localhost", port=2000)
    with self.assertRaises(RuntimeError):
      backend._resolve_traverse_height(None)

  def test_set_default_traverse_height(self):
    backend = PrepBackend(host="localhost", port=2000)
    backend.set_default_traverse_height(88.0)
    self.assertAlmostEqual(backend._resolve_traverse_height(None), 88.0)

  def test_resolve_traverse_height_explicit_beats_user(self):
    backend = _setup_backend()
    backend._user_traverse_height = 80.0
    self.assertAlmostEqual(backend._resolve_traverse_height(50.0), 50.0)

  def test_can_pick_up_tip_hamilton_tip(self):
    backend = _setup_backend()
    tip = HamiltonTip(
      name="t",
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertTrue(backend.can_pick_up_tip(0, tip))

  def test_can_pick_up_tip_non_hamilton(self):
    from pylabrobot.resources import Tip

    backend = _setup_backend()
    tip = Tip(
      name="generic_tip",
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      fitting_depth=8.0,
    )
    self.assertFalse(backend.can_pick_up_tip(0, tip))

  def test_can_pick_up_tip_xl_rejected(self):
    backend = _setup_backend()
    tip = HamiltonTip(
      name="t",
      has_filter=False,
      total_tip_length=95.0,
      maximal_volume=5000.0,
      tip_size=TipSize.XL,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertFalse(backend.can_pick_up_tip(0, tip))

  def test_can_pick_up_tip_channel_out_of_range(self):
    backend = _setup_backend(num_channels=2)
    tip = HamiltonTip(
      name="t",
      has_filter=False,
      total_tip_length=59.9,
      maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertFalse(backend.can_pick_up_tip(2, tip))

  def test_not_implemented_96_head_methods(self):
    import asyncio

    backend = _setup_backend()
    with self.assertRaises(NotImplementedError):
      asyncio.run(backend.pick_up_tips96(None))  # type: ignore[arg-type]


# =============================================================================
# 3. Tip pick-up and drop
# =============================================================================


class TestPrepBackendTipOps(unittest.IsolatedAsyncioTestCase):
  """Tip pickup/drop: channel mapping, Z geometry, waste handling."""

  async def asyncSetUp(self):
    self.backend, self.deck, self.tip_rack, self.plate = _setup_backend_with_deck()
    self.mock_send = unittest.mock.AsyncMock(return_value=None)
    self.backend.client.send_command = self.mock_send

  # --- pick_up_tips ---

  async def test_pick_up_tips_single_channel_ch0(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    cmds = _get_commands(self.mock_send, PrepCmd.PrepPickUpTips)
    self.assertEqual(len(cmds), 1)
    cmd = cmds[0]
    self.assertEqual(cmd.dest, _PIPETTOR_ADDR)
    self.assertEqual(len(cmd.tip_positions), 1)
    tp = cmd.tip_positions[0]
    self.assertEqual(tp.channel, PrepCmd.ChannelIndex.RearChannel)

    # Verify Z geometry
    loc = tip_spot.get_absolute_location("c", "c", "t")
    expected_z = loc.z + tip.total_tip_length - tip.fitting_depth
    expected_z_seek = expected_z + tip.fitting_depth + 5.0
    self.assertAlmostEqual(tp.z_position, expected_z, places=3)
    self.assertAlmostEqual(tp.z_seek, expected_z_seek, places=3)
    self.assertAlmostEqual(tp.x_position, loc.x, places=3)
    self.assertAlmostEqual(tp.y_position, loc.y, places=3)

  async def test_pick_up_tips_two_channels(self):
    spot_a = self.tip_rack.get_item("A1")
    spot_b = self.tip_rack.get_item("B1")
    await self.backend.pick_up_tips(
      [
        Pickup(resource=spot_a, offset=Coordinate.zero(), tip=spot_a.get_tip()),
        Pickup(resource=spot_b, offset=Coordinate.zero(), tip=spot_b.get_tip()),
      ],
      use_channels=[0, 1],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepPickUpTips)[0]
    self.assertEqual(len(cmd.tip_positions), 2)
    channels = [tp.channel for tp in cmd.tip_positions]
    self.assertIn(PrepCmd.ChannelIndex.RearChannel, channels)
    self.assertIn(PrepCmd.ChannelIndex.FrontChannel, channels)

  async def test_pick_up_tips_custom_final_z(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
      final_z=55.0,
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepPickUpTips)[0]
    self.assertAlmostEqual(cmd.final_z, 55.0)

  async def test_pick_up_tips_default_final_z_from_traverse(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepPickUpTips)[0]
    self.assertAlmostEqual(cmd.final_z, _TRAVERSE_HEIGHT)

  async def test_pick_up_tips_z_seek_offset(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
      z_seek_offset=3.0,
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepPickUpTips)[0]
    tp = cmd.tip_positions[0]
    loc = tip_spot.get_absolute_location("c", "c", "t")
    base_z = loc.z + tip.total_tip_length - tip.fitting_depth
    expected_z_seek = base_z + tip.fitting_depth + 5.0 + 3.0
    self.assertAlmostEqual(tp.z_seek, expected_z_seek, places=3)

  async def test_pick_up_tips_channel_out_of_range(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    with self.assertRaises(AssertionError):
      await self.backend.pick_up_tips(
        [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
        use_channels=[2],  # num_channels=2, valid range 0-1
      )

  # --- drop_tips ---

  async def test_drop_tips_to_rack_uses_tip_geometry(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDropTips)[0]
    self.assertEqual(len(cmd.tip_positions), 1)
    dp = cmd.tip_positions[0]
    loc = tip_spot.get_absolute_location("c", "c", "t")
    expected_z = loc.z + (tip.total_tip_length - tip.fitting_depth)
    expected_z_seek = loc.z + tip.total_tip_length + 10.0
    self.assertAlmostEqual(dp.z_position, expected_z, places=3)
    self.assertAlmostEqual(dp.z_seek, expected_z_seek, places=3)

  async def test_drop_tips_to_waste_position(self):
    waste = self.deck.get_resource("waste_rear")
    tip = self.tip_rack.get_item("A1").get_tip()
    await self.backend.drop_tips(
      [Drop(resource=waste, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDropTips)[0]
    dp = cmd.tip_positions[0]
    loc = waste.get_absolute_location("c", "c", "t")
    # Waste: same as tip spots — z_position so tip bottom lands at surface; z_seek for approach
    expected_z = loc.z + (tip.total_tip_length - tip.fitting_depth)
    expected_z_seek = loc.z + tip.total_tip_length + 10.0
    self.assertAlmostEqual(dp.z_position, expected_z, places=3)
    self.assertAlmostEqual(dp.z_seek, expected_z_seek, places=3)
    # Default roll-off when all Trash; use Stall so pipette detects contact before release
    self.assertAlmostEqual(cmd.tip_roll_off_distance, 3.0)
    self.assertEqual(dp.drop_type, PrepCmd.TipDropType.Stall)

  async def test_drop_tips_stall_type(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
      drop_type=PrepCmd.TipDropType.Stall,
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDropTips)[0]
    self.assertEqual(cmd.tip_positions[0].drop_type, PrepCmd.TipDropType.Stall)

  async def test_drop_tips_roll_off_distance(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
      tip_roll_off_distance=2.5,
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDropTips)[0]
    self.assertAlmostEqual(cmd.tip_roll_off_distance, 2.5)

  async def test_drop_tips_all_trash_resolves_to_deck_waste_and_default_roll(self):
    """When all ops are Trash (e.g. discard_tips), resolve to waste_rear/waste_front; default roll 3mm."""
    trash = self.deck.get_trash_area()
    tip = self.tip_rack.get_item("A1").get_tip()
    tip2 = self.tip_rack.get_item("B1").get_tip()
    await self.backend.drop_tips(
      [
        Drop(resource=trash, offset=Coordinate.zero(), tip=tip),
        Drop(resource=trash, offset=Coordinate.zero(), tip=tip2),
      ],
      use_channels=[0, 1],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDropTips)[0]
    self.assertEqual(len(cmd.tip_positions), 2)
    waste_rear = self.deck.get_resource("waste_rear")
    waste_front = self.deck.get_resource("waste_front")
    loc_rear = waste_rear.get_absolute_location("c", "c", "t")
    loc_front = waste_front.get_absolute_location("c", "c", "t")
    dp0, dp1 = cmd.tip_positions[0], cmd.tip_positions[1]
    expected_z_rear = loc_rear.z + (tip.total_tip_length - tip.fitting_depth)
    expected_z_seek_rear = loc_rear.z + tip.total_tip_length + 10.0
    expected_z_front = loc_front.z + (tip2.total_tip_length - tip2.fitting_depth)
    expected_z_seek_front = loc_front.z + tip2.total_tip_length + 10.0
    self.assertAlmostEqual(dp0.z_position, expected_z_rear, places=3)
    self.assertAlmostEqual(dp0.z_seek, expected_z_seek_rear, places=3)
    self.assertAlmostEqual(dp1.z_position, expected_z_front, places=3)
    self.assertAlmostEqual(dp1.z_seek, expected_z_seek_front, places=3)
    self.assertAlmostEqual(cmd.tip_roll_off_distance, 3.0)

  async def test_drop_tips_all_trash_deck_missing_waste_raises(self):
    """When all ops are Trash but deck has no waste_rear/waste_front, raise ValueError."""
    backend = _setup_backend()
    deck = Deck(size_x=300.0, size_y=320.0, size_z=0.0)
    trash = Trash(name="trash", size_x=0.0, size_y=0.0, size_z=0.0)
    deck.assign_child_resource(trash, location=Coordinate(287.0, 0.0, 0.0))
    backend._deck = deck
    backend.client.send_command = unittest.mock.AsyncMock(return_value=None)  # type: ignore[assignment]
    tip = hamilton_96_tiprack_300uL_filter("_tmp").get_item("A1").get_tip()
    with self.assertRaises(ValueError) as ctx:
      await backend.drop_tips(
        [Drop(resource=trash, offset=Coordinate.zero(), tip=tip)],
        use_channels=[0],
      )
    self.assertIn("waste_rear", str(ctx.exception))
    self.assertIn("deck has no waste position", str(ctx.exception))

  async def test_drop_tips_mixed_trash_and_tip_spot_raises(self):
    """Mixing Trash and TipSpot in one drop_tips call raises ValueError."""
    trash = self.deck.get_trash_area()
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    tip2 = self.tip_rack.get_item("B1").get_tip()
    with self.assertRaises(ValueError) as ctx:
      await self.backend.drop_tips(
        [
          Drop(resource=trash, offset=Coordinate.zero(), tip=tip),
          Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip2),
        ],
        use_channels=[0, 1],
      )
    self.assertIn("Cannot mix waste", str(ctx.exception))


# =============================================================================
# 4. Aspirate dispatch and parameter logic
# =============================================================================


class TestPrepBackendAspirate(unittest.IsolatedAsyncioTestCase):
  """Aspirate: 4-way dispatch, liquid class defaults, volume correction, Z geometry."""

  async def asyncSetUp(self):
    self.backend, self.deck, self.tip_rack, self.plate = _setup_backend_with_deck()
    self.mock_send = unittest.mock.AsyncMock(return_value=None)
    self.backend.client.send_command = self.mock_send
    self.tip = self.tip_rack.get_item("A1").get_tip()

  def _make_asp(
    self, well_name="A1", volume=100.0, flow_rate=None, liquid_height=5.0, blow_out_air_volume=0.0
  ):
    return SingleChannelAspiration(
      resource=self.plate.get_item(well_name),
      offset=Coordinate.zero(),
      tip=self.tip,
      volume=volume,
      flow_rate=flow_rate,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      mix=None,
    )

  # --- Dispatch ---

  async def test_aspirate_default_sends_nolld_monitoring(self):
    await self.backend.aspirate([self._make_asp()], use_channels=[0])
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)), 1)

  async def test_aspirate_tadm_mode(self):
    await self.backend.aspirate(
      [self._make_asp()],
      use_channels=[0],
      tadm=PrepCmd.TadmParameters.default(),
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateTadmV2)), 1)

  async def test_aspirate_lld_mode(self):
    await self.backend.aspirate(
      [self._make_asp()], use_channels=[0], lld_mode=[PrepBackend.LLDMode.CAPACITIVE]
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateWithLldV2)), 1)

  async def test_aspirate_lld_tadm_mode(self):
    await self.backend.aspirate(
      [self._make_asp()],
      use_channels=[0],
      lld_mode=[PrepBackend.LLDMode.CAPACITIVE],
      tadm=PrepCmd.TadmParameters.default(),
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateWithLldTadmV2)), 1)

  async def test_aspirate_implicit_lld_via_lld_param(self):
    """Passing lld= activates LLD path without explicit lld_mode."""
    custom_lld = PrepCmd.LldParameters(
      default_values=False,
      search_start_position=90.0,
      channel_speed=5.0,
      z_submerge=2.0,
      z_out_of_liquid=1.0,
    )
    await self.backend.aspirate(
      [self._make_asp()],
      use_channels=[0],
      lld=custom_lld,
    )
    cmds = _get_commands(self.mock_send, PrepCmd.PrepAspirateWithLldV2)
    self.assertEqual(len(cmds), 1)
    # Verify the provided LLD parameters are used (not auto-derived)
    lld = cmds[0].aspirate_parameters[0].lld
    self.assertAlmostEqual(lld.search_start_position, 90.0)

  async def test_aspirate_lld_auto_seek_z(self):
    """Auto-derived LLD search_start_position equals the top-of-well Z."""
    self.plate.get_item("A1")
    op = self._make_asp()
    _, _, top_of_well_z, _ = _absolute_z_from_well(op)
    await self.backend.aspirate([op], use_channels=[0], lld_mode=[PrepBackend.LLDMode.CAPACITIVE])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateWithLldV2)[0]
    lld = cmd.aspirate_parameters[0].lld
    self.assertAlmostEqual(lld.search_start_position, top_of_well_z, places=3)

  # --- Channel mapping ---

  async def test_aspirate_channel_0_is_rear(self):
    await self.backend.aspirate([self._make_asp()], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    self.assertEqual(cmd.aspirate_parameters[0].channel, PrepCmd.ChannelIndex.RearChannel)

  async def test_aspirate_channel_1_is_front(self):
    await self.backend.aspirate([self._make_asp()], use_channels=[1])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    self.assertEqual(cmd.aspirate_parameters[0].channel, PrepCmd.ChannelIndex.FrontChannel)

  async def test_aspirate_two_channels(self):
    ops = [
      self._make_asp("A1", volume=100.0, flow_rate=50.0),
      self._make_asp("B1", volume=150.0, flow_rate=75.0),
    ]
    await self.backend.aspirate(ops, use_channels=[0, 1])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    self.assertEqual(len(cmd.aspirate_parameters), 2)
    channels = {p.channel for p in cmd.aspirate_parameters}
    self.assertIn(PrepCmd.ChannelIndex.RearChannel, channels)
    self.assertIn(PrepCmd.ChannelIndex.FrontChannel, channels)

  # --- Volume and flow rate ---

  async def test_aspirate_volume_corrected_by_hlc(self):
    """HLC-corrected volume is sent, not raw op.volume."""
    op = self._make_asp(volume=100.0)
    hlc = get_star_liquid_class(
      tip_volume=self.tip.maximal_volume,
      is_core=False,
      is_tip=True,
      has_filter=self.tip.has_filter,
      liquid=Liquid.WATER,
      jet=False,
      blow_out=False,
    )
    if hlc is not None:
      expected_vol = hlc.compute_corrected_volume(100.0)
    else:
      expected_vol = 100.0
    await self.backend.aspirate([op], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    actual_vol = cmd.aspirate_parameters[0].common.liquid_volume
    self.assertAlmostEqual(actual_vol, expected_vol, places=2)

  async def test_aspirate_disable_volume_correction(self):
    """Raw volume used when disable_volume_correction=True."""
    raw_volume = 100.0
    await self.backend.aspirate(
      [self._make_asp(volume=raw_volume)],
      use_channels=[0],
      disable_volume_correction=[True],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    actual_vol = cmd.aspirate_parameters[0].common.liquid_volume
    self.assertAlmostEqual(actual_vol, raw_volume, places=2)

  async def test_aspirate_explicit_flow_rate(self):
    await self.backend.aspirate([self._make_asp(flow_rate=60.0)], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    self.assertAlmostEqual(cmd.aspirate_parameters[0].common.liquid_speed, 60.0)

  async def test_aspirate_flow_rate_from_hlc_default(self):
    """flow_rate=None -> uses HLC aspiration_flow_rate."""
    hlc = get_star_liquid_class(
      tip_volume=self.tip.maximal_volume,
      is_core=False,
      is_tip=True,
      has_filter=self.tip.has_filter,
      liquid=Liquid.WATER,
      jet=False,
      blow_out=False,
    )
    await self.backend.aspirate([self._make_asp(flow_rate=None)], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    expected = hlc.aspiration_flow_rate if hlc is not None else 100.0
    self.assertAlmostEqual(cmd.aspirate_parameters[0].common.liquid_speed, expected, places=2)

  async def test_aspirate_explicit_settling_time_override(self):
    await self.backend.aspirate(
      [self._make_asp()],
      use_channels=[0],
      settling_time=[2.0],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    self.assertAlmostEqual(cmd.aspirate_parameters[0].common.settling_time, 2.0)

  async def test_aspirate_hlc_settling_time_default(self):
    """Settling time from HLC when not explicitly passed."""
    hlc = get_star_liquid_class(
      tip_volume=self.tip.maximal_volume,
      is_core=False,
      is_tip=True,
      has_filter=self.tip.has_filter,
      liquid=Liquid.WATER,
      jet=False,
      blow_out=False,
    )
    await self.backend.aspirate([self._make_asp()], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    expected = hlc.aspiration_settling_time if hlc is not None else 1.0
    self.assertAlmostEqual(cmd.aspirate_parameters[0].common.settling_time, expected, places=3)

  async def test_aspirate_auto_container_geometry(self):
    """auto_container_geometry=True produces non-empty container_description."""
    await self.backend.aspirate(
      [self._make_asp()],
      use_channels=[0],
      auto_container_geometry=True,
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)[0]
    segs = cmd.aspirate_parameters[0].container_description
    self.assertGreater(len(segs), 0)
    self.assertIsInstance(segs[0], PrepCmd.SegmentDescriptor)


# =============================================================================
# 5. Dispense dispatch and parameter logic
# =============================================================================


class TestPrepBackendDispense(unittest.IsolatedAsyncioTestCase):
  """Dispense: 2-way dispatch, volume correction, HLC defaults."""

  async def asyncSetUp(self):
    self.backend, self.deck, self.tip_rack, self.plate = _setup_backend_with_deck()
    self.mock_send = unittest.mock.AsyncMock(return_value=None)
    self.backend.client.send_command = self.mock_send
    self.tip = self.tip_rack.get_item("A1").get_tip()

  def _make_disp(
    self, well_name="A1", volume=100.0, flow_rate=None, liquid_height=5.0, blow_out_air_volume=0.0
  ):
    return SingleChannelDispense(
      resource=self.plate.get_item(well_name),
      offset=Coordinate.zero(),
      tip=self.tip,
      volume=volume,
      flow_rate=flow_rate,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      mix=None,
    )

  async def test_dispense_default_sends_nolld(self):
    await self.backend.dispense([self._make_disp()], use_channels=[0])
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepDispenseNoLldV2)), 1)

  async def test_dispense_lld_mode(self):
    await self.backend.dispense(
      [self._make_disp()], use_channels=[0], lld_mode=[PrepBackend.LLDMode.CAPACITIVE]
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepDispenseWithLldV2)), 1)

  async def test_dispense_volume_corrected(self):
    hlc = get_star_liquid_class(
      tip_volume=self.tip.maximal_volume,
      is_core=False,
      is_tip=True,
      has_filter=self.tip.has_filter,
      liquid=Liquid.WATER,
      jet=False,
      blow_out=False,
    )
    raw = 100.0
    expected = hlc.compute_corrected_volume(raw) if hlc else raw
    await self.backend.dispense([self._make_disp(volume=raw)], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDispenseNoLldV2)[0]
    self.assertAlmostEqual(cmd.dispense_parameters[0].common.liquid_volume, expected, places=2)

  async def test_dispense_explicit_stop_back_volume(self):
    await self.backend.dispense(
      [self._make_disp()],
      use_channels=[0],
      stop_back_volume=[3.0],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDispenseNoLldV2)[0]
    self.assertAlmostEqual(cmd.dispense_parameters[0].dispense.stop_back_volume, 3.0)

  async def test_dispense_explicit_cutoff_speed(self):
    await self.backend.dispense(
      [self._make_disp()],
      use_channels=[0],
      cutoff_speed=[75.0],
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDispenseNoLldV2)[0]
    self.assertAlmostEqual(cmd.dispense_parameters[0].dispense.cutoff_speed, 75.0)

  async def test_dispense_two_channels(self):
    ops = [self._make_disp("A1", volume=100.0), self._make_disp("B1", volume=200.0)]
    await self.backend.dispense(ops, use_channels=[0, 1])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDispenseNoLldV2)[0]
    self.assertEqual(len(cmd.dispense_parameters), 2)

  async def test_dispense_z_minimum_from_well_bottom(self):
    op = self._make_disp()
    loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
    await self.backend.dispense([op], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepCmd.PrepDispenseNoLldV2)[0]
    self.assertAlmostEqual(cmd.dispense_parameters[0].common.z_minimum, loc.z, places=3)


# =============================================================================
# 6. MPH head
# =============================================================================


class TestPrepBackendMPH(unittest.IsolatedAsyncioTestCase):
  """MPH head: single Struct (not StructArray), tip_mask, guard checks."""

  async def asyncSetUp(self):
    self.backend, self.deck, self.tip_rack, self.plate = _setup_backend_with_deck(has_mph=True)
    self.mock_send = unittest.mock.AsyncMock(return_value=None)
    self.backend.client.send_command = self.mock_send

  async def test_mph_pickup_sends_mph_command(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.pick_up_tips_mph(tip_spot)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.MphPickupTips)), 1)
    # Must not send single-channel PrepCmd.PrepPickUpTips
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepPickUpTips)), 0)

  async def test_mph_pickup_default_tip_mask(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.pick_up_tips_mph(tip_spot)
    cmd = _get_commands(self.mock_send, PrepCmd.MphPickupTips)[0]
    self.assertEqual(cmd.tip_mask, 0xFF)

  async def test_mph_pickup_custom_tip_mask(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.pick_up_tips_mph(tip_spot, tip_mask=0x0F)
    cmd = _get_commands(self.mock_send, PrepCmd.MphPickupTips)[0]
    self.assertEqual(cmd.tip_mask, 0x0F)

  async def test_mph_drop_sends_mph_command(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.drop_tips_mph(tip_spot)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.MphDropTips)), 1)

  async def test_mph_pickup_raises_when_no_mph(self):
    backend = _setup_backend(has_mph=False)
    with self.assertRaises(RuntimeError):
      await backend.pick_up_tips_mph(self.tip_rack.get_item("A1"))

  async def test_mph_pickup_raises_empty_list(self):
    with self.assertRaises(ValueError):
      await self.backend.pick_up_tips_mph([])


# =============================================================================
# 7. CORE gripper
# =============================================================================


class TestPrepBackendGripper(unittest.IsolatedAsyncioTestCase):
  """CORE gripper: tool lifecycle, plate geometry, drop with/without return."""

  async def asyncSetUp(self):
    self.backend, self.deck, self.tip_rack, self.plate = _setup_backend_with_deck(
      with_core_grippers=True
    )
    self.mock_send = unittest.mock.AsyncMock(return_value=None)
    self.backend.client.send_command = self.mock_send

  def _make_pickup(self, resource, pickup_distance_from_top=5.0):
    return ResourcePickup(
      resource=resource,
      offset=Coordinate.zero(),
      pickup_distance_from_top=pickup_distance_from_top,
      direction=GripDirection.FRONT,
    )

  def _make_drop(self, resource, destination: Coordinate, pickup_distance_from_top=5.0):
    return ResourceDrop(
      resource=resource,
      destination=destination,
      destination_absolute_rotation=Rotation(),
      offset=Coordinate.zero(),
      pickup_distance_from_top=pickup_distance_from_top,
      pickup_direction=GripDirection.FRONT,
      direction=GripDirection.FRONT,
      rotation=0.0,
    )

  def _make_move(self, resource, location: Coordinate, pickup_distance_from_top=5.0):
    return ResourceMove(
      resource=resource,
      location=location,
      gripped_direction=GripDirection.FRONT,
      pickup_distance_from_top=pickup_distance_from_top,
      offset=Coordinate.zero(),
    )

  async def test_auto_picks_up_tool_before_plate(self):
    """When _gripper_tool_on=False, PrepCmd.PrepPickUpTool is sent before PrepCmd.PrepPickUpPlate."""
    self.assertFalse(self.backend._gripper_tool_on)
    await self.backend.pick_up_resource(self._make_pickup(self.plate))
    tool_cmds = _get_commands(self.mock_send, PrepCmd.PrepPickUpTool)
    plate_cmds = _get_commands(self.mock_send, PrepCmd.PrepPickUpPlate)
    self.assertEqual(len(tool_cmds), 1)
    self.assertEqual(len(plate_cmds), 1)
    # Tool must be picked up before plate
    all_calls = [c.args[0] for c in self.mock_send.call_args_list]
    self.assertLess(all_calls.index(tool_cmds[0]), all_calls.index(plate_cmds[0]))
    self.assertTrue(self.backend._gripper_tool_on)

  async def test_skip_tool_pickup_when_already_holding(self):
    self.backend._gripper_tool_on = True
    await self.backend.pick_up_resource(self._make_pickup(self.plate))
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepPickUpTool)), 0)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepPickUpPlate)), 1)

  async def test_plate_dimensions_from_resource(self):
    await self.backend.pick_up_resource(self._make_pickup(self.plate))
    cmd = _get_commands(self.mock_send, PrepCmd.PrepPickUpPlate)[0]
    self.assertAlmostEqual(cmd.plate.length, self.plate.get_absolute_size_x(), places=3)
    self.assertAlmostEqual(cmd.plate.width, self.plate.get_absolute_size_y(), places=3)
    self.assertAlmostEqual(cmd.plate.height, self.plate.get_absolute_size_z(), places=3)

  async def test_grip_distance_is_clearance_plus_squeeze(self):
    clearance_y = 2.5
    squeeze_mm = 2.0
    await self.backend.pick_up_resource(
      self._make_pickup(self.plate),
      clearance_y=clearance_y,
      squeeze_mm=squeeze_mm,
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepPickUpPlate)[0]
    self.assertAlmostEqual(cmd.grip_distance, clearance_y + squeeze_mm)

  async def test_grip_direction_not_front_raises(self):
    pickup = ResourcePickup(
      resource=self.plate,
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      direction=GripDirection.LEFT,
    )
    with self.assertRaises(NotImplementedError):
      await self.backend.pick_up_resource(pickup)

  async def test_drop_resource_with_return_gripper(self):
    self.backend._gripper_tool_on = True
    plate_loc = self.plate.get_absolute_location()
    drop = self._make_drop(self.plate, destination=plate_loc)
    await self.backend.drop_resource(drop, return_gripper=True)
    drop_plate_cmds = _get_commands(self.mock_send, PrepCmd.PrepDropPlate)
    drop_tool_cmds = _get_commands(self.mock_send, PrepCmd.PrepDropTool)
    self.assertEqual(len(drop_plate_cmds), 1)
    self.assertEqual(len(drop_tool_cmds), 1)
    self.assertFalse(self.backend._gripper_tool_on)

  async def test_drop_resource_without_return_gripper(self):
    self.backend._gripper_tool_on = True
    plate_loc = self.plate.get_absolute_location()
    drop = self._make_drop(self.plate, destination=plate_loc)
    await self.backend.drop_resource(drop, return_gripper=False)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepDropTool)), 0)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepDropPlate)), 1)

  async def test_move_picked_up_resource(self):
    self.backend._gripper_tool_on = True
    dest = Coordinate(100.0, 50.0, 10.0)
    move = self._make_move(self.plate, location=dest)
    await self.backend.move_picked_up_resource(move)
    cmds = _get_commands(self.mock_send, PrepCmd.PrepMovePlate)
    self.assertEqual(len(cmds), 1)


# =============================================================================
# 8. Convenience methods
# =============================================================================


class TestPrepBackendConvenience(unittest.IsolatedAsyncioTestCase):
  """Convenience methods: correct command type to correct interface address."""

  async def asyncSetUp(self):
    self.backend = _setup_backend()
    self.mock_send = unittest.mock.AsyncMock(return_value=None)
    self.backend.client.send_command = self.mock_send  # type: ignore[assignment]

  async def test_park(self):
    await self.backend.park()
    cmds = _get_commands(self.mock_send, PrepCmd.PrepPark)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].dest, _MLPREP_ADDR)

  async def test_spread(self):
    await self.backend.spread()
    cmds = _get_commands(self.mock_send, PrepCmd.PrepSpread)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].dest, _MLPREP_ADDR)

  async def test_method_begin_automatic_pause(self):
    await self.backend.method_begin(automatic_pause=True)
    cmds = _get_commands(self.mock_send, PrepCmd.PrepMethodBegin)
    self.assertEqual(len(cmds), 1)
    self.assertTrue(cmds[0].automatic_pause)

  async def test_method_begin_no_automatic_pause(self):
    await self.backend.method_begin(automatic_pause=False)
    cmds = _get_commands(self.mock_send, PrepCmd.PrepMethodBegin)
    self.assertFalse(cmds[0].automatic_pause)

  async def test_method_end(self):
    await self.backend.method_end()
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepMethodEnd)), 1)

  async def test_method_abort(self):
    await self.backend.method_abort()
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepMethodAbort)), 1)

  async def test_move_to_position(self):
    await self.backend.move_to_position(x=100.0, y=50.0, z=20.0, use_channels=[0])
    cmds = _get_commands(self.mock_send, PrepCmd.PrepMoveToPosition)
    self.assertEqual(len(cmds), 1)
    cmd = cmds[0]
    self.assertAlmostEqual(cmd.move_parameters.gantry_x_position, 100.0)
    self.assertEqual(len(cmd.move_parameters.axis_parameters), 1)
    self.assertEqual(
      cmd.move_parameters.axis_parameters[0].channel, PrepCmd.ChannelIndex.RearChannel
    )
    self.assertAlmostEqual(cmd.move_parameters.axis_parameters[0].y_position, 50.0)
    self.assertAlmostEqual(cmd.move_parameters.axis_parameters[0].z_position, 20.0)

  async def test_move_to_position_via_lane(self):
    await self.backend.move_to_position(x=100.0, y=50.0, z=20.0, use_channels=[0], via_lane=True)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepMoveToPositionViaLane)), 1)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepMoveToPosition)), 0)

  async def test_move_channels_to_safe_z_all(self):
    await self.backend.move_channels_to_safe_z()
    cmds = _get_commands(self.mock_send, PrepCmd.PrepMoveZUpToSafe)
    self.assertEqual(len(cmds), 1)
    channels = cmds[0].channels
    self.assertIn(PrepCmd.ChannelIndex.RearChannel, channels)
    self.assertIn(PrepCmd.ChannelIndex.FrontChannel, channels)

  async def test_set_deck_light(self):
    await self.backend.set_deck_light(white=100, red=50, green=25, blue=200)
    cmds = _get_commands(self.mock_send, PrepCmd.PrepSetDeckLight)
    self.assertEqual(len(cmds), 1)
    cmd = cmds[0]
    self.assertEqual(cmd.white, 100)
    self.assertEqual(cmd.red, 50)
    self.assertEqual(cmd.green, 25)
    self.assertEqual(cmd.blue, 200)
    self.assertEqual(cmd.dest, _MLPREP_ADDR)

  async def test_not_implemented_96_ops(self):
    with self.assertRaises(NotImplementedError):
      await self.backend.pick_up_tips96(None)  # type: ignore[arg-type]
    with self.assertRaises(NotImplementedError):
      await self.backend.drop_tips96(None)  # type: ignore[arg-type]
    with self.assertRaises(NotImplementedError):
      await self.backend.aspirate96(None)  # type: ignore[arg-type]
    with self.assertRaises(NotImplementedError):
      await self.backend.dispense96(None)  # type: ignore[arg-type]


class TestPrepCalibrationModelUtilities(unittest.TestCase):
  def _make_values(self, z_offset: float = 10.0) -> PrepCmd.CalibrationValues:
    return PrepCmd.CalibrationValues(
      independent_offset_x=1.0,
      mph_offset_x=2.0,
      channel_values=(
        PrepCmd.ChannelCalibrationValuesInfo(
          index=2,
          y_offset=20.0,
          z_offset=z_offset,
          squeeze_position=100,
          z_touchoff=5,
          pressure_shift=7,
          pressure_monitoring_shift=9,
          dispenser_return_distance=1.5,
          z_tip_height=2.5,
          core_ii=False,
        ),
      ),
    )

  def test_calibration_values_to_pretty_string(self):
    values = self._make_values()
    text = values.to_pretty_string()
    self.assertIn("Independent offset X: 1.0", text)
    self.assertIn("MPH offset X: 2.0", text)
    self.assertIn("index=2", text)

  def test_diff_calibration_values_uses_tolerance(self):
    old = self._make_values(z_offset=10.0)
    new = self._make_values(z_offset=10.0000001)
    diff = PrepCmd.diff_calibration_values(old, new, float_tol=1e-6)
    self.assertFalse(diff.has_changes)

    changed = self._make_values(z_offset=10.01)
    diff_changed = PrepCmd.diff_calibration_values(old, changed, float_tol=1e-6)
    self.assertTrue(diff_changed.has_changes)
    formatted = PrepCmd.format_calibration_diff(diff_changed)
    self.assertIn("index=2", formatted)
    self.assertIn("z_offset", formatted)


class TestPrepCalibrationReadOnly(unittest.IsolatedAsyncioTestCase):
  async def test_read_calibration_values_outside_session(self):
    backend = _setup_backend()
    backend.client.send_command = unittest.mock.AsyncMock(  # type: ignore[assignment]
      return_value=SimpleNamespace(
        independent_offset_x=1.25,
        mph_offset_x=2.5,
        channel_values=[
          SimpleNamespace(
            index=2,
            y_offset=20.0,
            z_offset=10.0,
            squeeze_position=100,
            z_touchoff=5,
            pressure_shift=7,
            pressure_monitoring_shift=9,
            dispenser_return_distance=1.5,
            z_tip_height=2.5,
            core_ii=False,
          )
        ],
      )
    )

    values = await backend.read_calibration_values()
    self.assertAlmostEqual(values.independent_offset_x, 1.25)
    self.assertAlmostEqual(values.mph_offset_x, 2.5)
    self.assertEqual(len(values.channel_values), 1)
    self.assertEqual(values.channel_values[0].index, 2)


class TestPrepCalibrationSession(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.backend = _setup_backend(num_channels=2, has_mph=True)
    self.send_command_mock = unittest.mock.AsyncMock(side_effect=self._dispatch)
    self.backend.client.send_command = self.send_command_mock  # type: ignore[assignment]
    self._z_offset = 10.0

  def _calibration_result(self):
    return SimpleNamespace(
      independent_offset_x=1.0,
      mph_offset_x=2.0,
      channel_values=[
        SimpleNamespace(
          index=2,
          y_offset=20.0,
          z_offset=self._z_offset,
          squeeze_position=100,
          z_touchoff=5,
          pressure_shift=7,
          pressure_monitoring_shift=9,
          dispenser_return_distance=1.5,
          z_tip_height=2.5,
          core_ii=False,
        )
      ],
    )

  async def _dispatch(self, command, **kwargs):
    if isinstance(command, PrepCmd.PrepCalibrateZAxis):
      self._z_offset = 11.0
      return SimpleNamespace(offset=self._z_offset)
    if isinstance(command, PrepCmd.PrepGetCalibrationValues):
      return self._calibration_result()
    if isinstance(
      command,
      (
        PrepCmd.PrepBeginCalibration,
        PrepCmd.PrepCalibrationInitialize,
        PrepCmd.PrepCancelCalibration,
        PrepCmd.PrepEndCalibration,
      ),
    ):
      return None
    if isinstance(command, PrepCmd.PrepCalibrateSqueezeTips):
      return SimpleNamespace(positions=[111, 222])
    return None

  async def test_session_reports_changes_by_default(self):
    async with PrepCalibrationSession(self.backend) as session:
      result = await session.calibrate_z_axis(
        site_index=0,
        channel=PrepCmd.ChannelIndex.RearChannel,
      )
      self.assertIsInstance(result, CalibrationCommandReport)
      assert isinstance(result, CalibrationCommandReport)
      self.assertEqual(len(session.history), 1)
      self.assertTrue(result.diff.has_changes)
      self.assertEqual(result.before.channel_values[0].index, int(PrepCmd.ChannelIndex.RearChannel))
      await session.rollback()

  async def test_session_commit_skips_auto_rollback(self):
    async with PrepCalibrationSession(self.backend) as session:
      await session.commit()
    sent = [c.args[0] for c in self.send_command_mock.call_args_list]
    self.assertEqual(sum(isinstance(c, PrepCmd.PrepEndCalibration) for c in sent), 1)
    self.assertEqual(sum(isinstance(c, PrepCmd.PrepCancelCalibration) for c in sent), 0)

  async def test_session_start_end_without_save(self):
    session = self.backend.calibration_session(report_after_command=False)
    await session.start()
    await session.end(save=False)
    sent = [c.args[0] for c in self.send_command_mock.call_args_list]
    self.assertEqual(sum(isinstance(c, PrepCmd.PrepBeginCalibration) for c in sent), 1)
    self.assertEqual(sum(isinstance(c, PrepCmd.PrepCalibrationInitialize) for c in sent), 1)
    self.assertEqual(sum(isinstance(c, PrepCmd.PrepCancelCalibration) for c in sent), 1)

  async def test_calibrate_squeeze_tips_mph_uses_mph_channel(self):
    backend, _, tip_rack, _ = _setup_backend_with_deck(has_mph=True)

    async def _dispatch(command, **kwargs):
      if isinstance(command, PrepCmd.PrepGetCalibrationValues):
        return SimpleNamespace(
          independent_offset_x=0.0,
          mph_offset_x=0.0,
          channel_values=[],
        )
      if isinstance(command, PrepCmd.PrepCalibrateSqueezeTips):
        return SimpleNamespace(positions=[777])
      return None

    mock_send = unittest.mock.AsyncMock(side_effect=_dispatch)
    backend.client.send_command = mock_send  # type: ignore[assignment]
    spot = tip_rack.get_item("A1")
    async with backend.calibration_session(report_after_command=False) as session:
      positions = await session.calibrate_squeeze_tips_mph(spot)
    self.assertEqual(positions, (777,))
    cmd = _get_commands(mock_send, PrepCmd.PrepCalibrateSqueezeTips)[0]
    self.assertEqual(len(cmd.channels), 1)
    self.assertEqual(cmd.channels[0].channel, PrepCmd.ChannelIndex.MPHChannel)

  async def test_calibrate_squeeze_tips_mph_requires_hardware(self):
    backend, _, tip_rack, _ = _setup_backend_with_deck(has_mph=False)

    async def _dispatch(command, **kwargs):
      if isinstance(command, PrepCmd.PrepGetCalibrationValues):
        return SimpleNamespace(
          independent_offset_x=0.0,
          mph_offset_x=0.0,
          channel_values=[],
        )
      return None

    backend.client.send_command = unittest.mock.AsyncMock(side_effect=_dispatch)  # type: ignore[assignment]
    with self.assertRaises(RuntimeError):
      async with backend.calibration_session(report_after_command=False) as session:
        await session.calibrate_squeeze_tips_mph(tip_rack.get_item("A1"))

  def test_calibration_methods_are_session_owned(self):
    backend = _setup_backend()
    self.assertFalse(hasattr(backend, "calibrate_z_axis"))
    self.assertFalse(hasattr(backend, "calibrate_squeeze_tips_mph"))


# =============================================================================
# V1/V2 aspirate/dispense fallback
# =============================================================================


class TestSegmentsToConeGeometry(unittest.TestCase):
  """Unit tests for _segments_to_cone_geometry conversion."""

  def test_empty_segments_returns_fallback(self):
    from pylabrobot.liquid_handling.backends.hamilton.prep_backend import _segments_to_cone_geometry

    r, ch, cbr = _segments_to_cone_geometry([], fallback_radius=4.0)
    self.assertAlmostEqual(r, 4.0)
    self.assertAlmostEqual(ch, 0.0)
    self.assertAlmostEqual(cbr, 0.0)

  def test_single_cylinder_segment(self):
    from pylabrobot.liquid_handling.backends.hamilton.prep_backend import _segments_to_cone_geometry

    area = math.pi * 4.0**2  # radius 4
    seg = PrepCmd.SegmentDescriptor(area_top=area, area_bottom=area, height=10.0)
    r, ch, cbr = _segments_to_cone_geometry([seg], fallback_radius=0.0)
    self.assertAlmostEqual(r, 4.0, places=5)
    self.assertAlmostEqual(ch, 0.0)
    self.assertAlmostEqual(cbr, 0.0)

  def test_tapered_bottom_segment(self):
    from pylabrobot.liquid_handling.backends.hamilton.prep_backend import _segments_to_cone_geometry

    area_small = math.pi * 2.0**2
    area_large = math.pi * 4.0**2
    seg = PrepCmd.SegmentDescriptor(area_top=area_large, area_bottom=area_small, height=5.0)
    r, ch, cbr = _segments_to_cone_geometry([seg], fallback_radius=0.0)
    # Volume-weighted avg area = (area_large + area_small) / 2
    avg_area = (area_large + area_small) / 2.0
    expected_r = math.sqrt(avg_area / math.pi)
    self.assertAlmostEqual(r, expected_r, places=5)
    self.assertAlmostEqual(ch, 5.0)
    self.assertAlmostEqual(cbr, 2.0, places=5)

  def test_multi_segment_cylinder_plus_cone(self):
    from pylabrobot.liquid_handling.backends.hamilton.prep_backend import _segments_to_cone_geometry

    area_small = math.pi * 1.0**2
    area_large = math.pi * 3.0**2
    cone_seg = PrepCmd.SegmentDescriptor(area_top=area_large, area_bottom=area_small, height=2.0)
    cyl_seg = PrepCmd.SegmentDescriptor(area_top=area_large, area_bottom=area_large, height=8.0)
    r, ch, cbr = _segments_to_cone_geometry([cone_seg, cyl_seg], fallback_radius=0.0)
    # Bottom segment tapers, so cone_height=2.0, cone_bottom_radius=1.0
    self.assertAlmostEqual(ch, 2.0)
    self.assertAlmostEqual(cbr, 1.0, places=5)
    # tube_radius is volume-weighted average across both segments
    total_h = 10.0
    weighted = 2.0 * (area_large + area_small) / 2.0 + 8.0 * area_large
    expected_r = math.sqrt((weighted / total_h) / math.pi)
    self.assertAlmostEqual(r, expected_r, places=5)


class TestV1CommandDispatch(unittest.IsolatedAsyncioTestCase):
  """V1 command_version dispatch: verify v1 command classes are sent."""

  async def asyncSetUp(self):
    self.backend, self.deck, self.tip_rack, self.plate = _setup_backend_with_deck()
    self.backend._supports_v2_pipetting = True  # default: v2 available
    self.mock_send = unittest.mock.AsyncMock(return_value=None)
    self.backend.client.send_command = self.mock_send
    self.tip = self.tip_rack.get_item("A1").get_tip()

  def _make_asp(self, well_name="A1", volume=100.0, liquid_height=5.0):
    return SingleChannelAspiration(
      resource=self.plate.get_item(well_name),
      offset=Coordinate.zero(),
      tip=self.tip,
      volume=volume,
      flow_rate=None,
      liquid_height=liquid_height,
      blow_out_air_volume=0.0,
      mix=None,
    )

  def _make_disp(self, well_name="A1", volume=100.0, liquid_height=5.0):
    return SingleChannelDispense(
      resource=self.plate.get_item(well_name),
      offset=Coordinate.zero(),
      tip=self.tip,
      volume=volume,
      flow_rate=None,
      liquid_height=liquid_height,
      blow_out_air_volume=0.0,
      mix=None,
    )

  # --- Aspirate v1 dispatch ---

  async def test_aspirate_v1_nolld_monitoring(self):
    await self.backend.aspirate([self._make_asp()], use_channels=[0], command_version="v1")
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoring)), 1)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)), 0)

  async def test_aspirate_v1_tadm(self):
    await self.backend.aspirate(
      [self._make_asp()],
      use_channels=[0],
      tadm=PrepCmd.TadmParameters.default(),
      command_version="v1",
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateTadm)), 1)

  async def test_aspirate_v1_with_lld(self):
    await self.backend.aspirate(
      [self._make_asp()], use_channels=[0],
      lld_mode=[PrepBackend.LLDMode.CAPACITIVE], command_version="v1"
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateWithLld)), 1)

  async def test_aspirate_v1_with_lld_tadm(self):
    await self.backend.aspirate(
      [self._make_asp()],
      use_channels=[0],
      lld_mode=[PrepBackend.LLDMode.CAPACITIVE],
      tadm=PrepCmd.TadmParameters.default(),
      command_version="v1",
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateWithLldTadm)), 1)

  # --- Dispense v1 dispatch ---

  async def test_dispense_v1_nolld(self):
    await self.backend.dispense([self._make_disp()], use_channels=[0], command_version="v1")
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepDispenseNoLld)), 1)
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepDispenseNoLldV2)), 0)

  async def test_dispense_v1_with_lld(self):
    await self.backend.dispense(
      [self._make_disp()], use_channels=[0],
      lld_mode=[PrepBackend.LLDMode.CAPACITIVE], command_version="v1"
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepDispenseWithLld)), 1)

  # --- V1 cone geometry patching ---

  async def test_v1_aspirate_patches_cone_geometry_from_segments(self):
    """When using v1 with auto_container_geometry, CommonParameters gets cone fields patched."""
    await self.backend.aspirate(
      [self._make_asp()],
      use_channels=[0],
      auto_container_geometry=True,
      command_version="v1",
    )
    cmd = _get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoring)[0]
    common = cmd.aspirate_parameters[0].common
    # auto_container_geometry builds segments from the well; v1 conversion should
    # populate tube_radius from those segments (not leave cone_height=0).
    # For a Cor_96 plate with uniform cross-section, tube_radius should match
    # _effective_radius, and cone should stay 0 (constant area).
    self.assertGreater(common.tube_radius, 0.0)

  # --- Default auto-detection ---

  async def test_default_uses_v2_when_supported(self):
    self.backend._supports_v2_pipetting = True
    await self.backend.aspirate([self._make_asp()], use_channels=[0])
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoringV2)), 1)

  async def test_default_uses_v1_when_unsupported(self):
    self.backend._supports_v2_pipetting = False
    await self.backend.aspirate([self._make_asp()], use_channels=[0])
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoring)), 1)

  async def test_use_v1_aspirate_dispense_forces_v1(self):
    backend = _setup_backend()
    backend._use_v1_aspirate_dispense = True
    backend._supports_v2_pipetting = False
    mock = unittest.mock.AsyncMock(return_value=None)
    backend.client.send_command = mock  # type: ignore[assignment]
    backend._deck = self.deck
    tip = self.tip_rack.get_item("A1").get_tip()
    op = SingleChannelAspiration(
      resource=self.plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=tip,
      volume=100.0,
      flow_rate=None,
      liquid_height=5.0,
      blow_out_air_volume=0.0,
      mix=None,
    )
    await backend.aspirate([op], use_channels=[0])
    self.assertEqual(len(_get_commands(mock, PrepCmd.PrepAspirateNoLldMonitoring)), 1)

  async def test_per_call_v1_override_uses_v1(self):
    """Per-call command_version='v1' forces v1 even when v2 is supported."""
    self.backend._supports_v2_pipetting = True
    await self.backend.aspirate([self._make_asp()], use_channels=[0], command_version="v1")
    self.assertEqual(len(_get_commands(self.mock_send, PrepCmd.PrepAspirateNoLldMonitoring)), 1)

  async def test_per_call_v2_override_raises_when_unsupported(self):
    self.backend._supports_v2_pipetting = False
    with self.assertRaises(ValueError):
      await self.backend.aspirate([self._make_asp()], use_channels=[0], command_version="v2")


class TestPrepSharedTraversal(unittest.IsolatedAsyncioTestCase):
  async def test_discover_channel_drives_uses_shared_tree(self):
    backend = _setup_backend(num_channels=2)
    nodes = [
      (
        "MLPrepRoot",
        Address(1, 1, 10),
        ObjectInfo("MLPrepRoot", "1.0", 0, 3, Address(1, 1, 10)),
      ),
      (
        "MLPrepRoot.MLPrepCpu",
        Address(1, 1, 11),
        ObjectInfo("MLPrepCpu", "1.0", 0, 0, Address(1, 1, 11)),
      ),
      (
        "MLPrepRoot.PipettorRoot.ModuleInformation",
        Address(1, 1, 12),
        ObjectInfo("ModuleInformation", "1.0", 0, 0, Address(1, 1, 12)),
      ),
      (
        "MLPrepRoot.Channel Root",
        Address(1, 2, 20),
        ObjectInfo("Channel Root", "1.0", 0, 0, Address(1, 2, 20)),
      ),
      (
        "MLPrepRoot.Channel Root.Channel",
        Address(1, 2, 21),
        ObjectInfo("Channel", "1.0", 0, 0, Address(1, 2, 21)),
      ),
      (
        "MLPrepRoot.Channel Root.Channel.Squeeze.SDrive",
        Address(1, 2, 22),
        ObjectInfo("SDrive", "1.0", 0, 0, Address(1, 2, 22)),
      ),
      (
        "MLPrepRoot.Channel Root.Channel.ZAxis.ZDrive",
        Address(1, 2, 23),
        ObjectInfo("ZDrive", "1.0", 0, 0, Address(1, 2, 23)),
      ),
      (
        "MLPrepRoot.Channel Root.NodeInformation",
        Address(1, 2, 24),
        ObjectInfo("NodeInformation", "1.0", 0, 0, Address(1, 2, 24)),
      ),
    ]
    backend.client.build_firmware_tree = unittest.mock.AsyncMock(return_value=nodes)  # type: ignore[assignment]

    await backend._discover_channel_drives()

    self.assertEqual(backend._mlprep_cpu_addr, Address(1, 1, 11))
    self.assertEqual(backend._module_info_addr, Address(1, 1, 12))
    self.assertEqual(backend._channel_sleeve_sensor_addrs, [Address(1, 2, 22)])
    self.assertEqual(backend._channel_zdrive_addrs, [Address(1, 2, 23)])
    self.assertEqual(backend._channel_node_info_addrs, [Address(1, 2, 24)])
    backend.client.build_firmware_tree.assert_awaited_once()  # type: ignore[attr-defined]

  async def test_print_firmware_tree_uses_shared_tree(self):
    backend = _setup_backend(num_channels=2)
    nodes = [
      (
        "MLPrepRoot",
        Address(1, 1, 10),
        ObjectInfo("MLPrepRoot", "1.0", 2, 1, Address(1, 1, 10)),
      ),
      (
        "MLPrepRoot.Child",
        Address(1, 1, 11),
        ObjectInfo("Child", "1.0", 0, 0, Address(1, 1, 11)),
      ),
    ]
    backend.client.build_firmware_tree = unittest.mock.AsyncMock(return_value=nodes)  # type: ignore[assignment]

    with unittest.mock.patch("builtins.print") as print_mock:
      await backend.print_firmware_tree()

    backend.client.build_firmware_tree.assert_awaited_once()  # type: ignore[attr-defined]
    printed = print_mock.call_args.args[0]
    self.assertIn("MLPrepRoot @ 1:1:10", printed)
    self.assertIn("Child @ 1:1:11", printed)


if __name__ == "__main__":
  unittest.main()
