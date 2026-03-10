"""Tests for Hamilton Prep backend logic and command generation.

Verifies PrepBackend method behavior: how operations are transformed into
commands, geometry computed, command variants dispatched, and state managed.
All tests mock client.send_command — no real TCP connection required.
"""

import math
import unittest
import unittest.mock

from pylabrobot.liquid_handling.backends.hamilton.prep_backend import (
  PrepBackend,
  _absolute_z_from_well,
  _build_container_segments,
  _effective_radius,
)
from pylabrobot.liquid_handling.backends.hamilton.prep_commands import (
  ChannelIndex,
  DeckBounds,
  InstrumentConfig,
  LldParameters,
  MphDropTips,
  MphPickupTips,
  MonitoringMode,
  PrepAspirateNoLldMonitoringV2,
  PrepAspirateWithLldTadmV2,
  PrepAspirateWithLldV2,
  PrepAspirateTadmV2,
  PrepDispenseNoLldV2,
  PrepDispenseWithLldV2,
  PrepDropPlate,
  PrepDropTips,
  PrepDropTool,
  PrepMethodBegin,
  PrepMethodEnd,
  PrepMethodAbort,
  PrepMovePlate,
  PrepMoveToPosition,
  PrepMoveToPositionViaLane,
  PrepMoveZUpToSafe,
  PrepPark,
  PrepPickUpPlate,
  PrepPickUpTips,
  PrepPickUpTool,
  PrepSetDeckLight,
  PrepSpread,
  SegmentDescriptor,
  TipDropType,
)
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
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb
from pylabrobot.resources.hamilton import HamiltonTip, TipPickupMethod, TipSize
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton.hamilton_decks import PrepDeck
from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_300uL_filter
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.well import CrossSectionType, Well


# =============================================================================
# Setup helpers
# =============================================================================

_MLPREP_ADDR = Address(1, 1, 0x0015)
_PIPETTOR_ADDR = Address(1, 1, 0x00E0)
_COORD_ADDR = Address(1, 1, 0x00C0)
_DECK_CONFIG_ADDR = Address(1, 1, 0x00D0)
_MPH_ADDR = Address(1, 1, 0x00F0)
_SERVICE_ADDR = Address(1, 1, 0x0017)

_TRAVERSE_HEIGHT = 96.97


def _setup_backend(num_channels: int = 2, has_mph: bool = False) -> PrepBackend:
  """PrepBackend with pre-resolved interfaces, bypassing TCP."""
  backend = PrepBackend(host="192.168.100.102", port=2000)
  backend._num_channels = num_channels
  backend._has_mph = has_mph
  backend._user_traverse_height = _TRAVERSE_HEIGHT
  backend._config = InstrumentConfig(
    deck_bounds=DeckBounds(0.0, 300.0, 0.0, 320.0, 0.0, 100.0),
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
  return [
    call.args[0]
    for call in mock_send.call_args_list
    if isinstance(call.args[0], cmd_type)
  ]


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
    expected_area = math.pi * (3.0 ** 2)
    self.assertIsInstance(segs[0], SegmentDescriptor)
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
    area = math.pi * 3.0 ** 2
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
    well = self._make_circular_well(diameter=6.0, height=10.0)
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
    backend._config = InstrumentConfig(
      deck_bounds=None, has_enclosure=False, safe_speeds_enabled=False,
      deck_sites=(), waste_sites=(), default_traverse_height=75.0,
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
      name="t", has_filter=False, total_tip_length=59.9, maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME, pickup_method=TipPickupMethod.OUT_OF_RACK,
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
      name="t", has_filter=False, total_tip_length=95.0, maximal_volume=5000.0,
      tip_size=TipSize.XL, pickup_method=TipPickupMethod.OUT_OF_RACK,
    )
    self.assertFalse(backend.can_pick_up_tip(0, tip))

  def test_can_pick_up_tip_channel_out_of_range(self):
    backend = _setup_backend(num_channels=2)
    tip = HamiltonTip(
      name="t", has_filter=False, total_tip_length=59.9, maximal_volume=300.0,
      tip_size=TipSize.STANDARD_VOLUME, pickup_method=TipPickupMethod.OUT_OF_RACK,
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
    cmds = _get_commands(self.mock_send, PrepPickUpTips)
    self.assertEqual(len(cmds), 1)
    cmd = cmds[0]
    self.assertEqual(cmd.dest, _PIPETTOR_ADDR)
    self.assertEqual(len(cmd.tip_positions), 1)
    tp = cmd.tip_positions[0]
    self.assertEqual(tp.channel, ChannelIndex.RearChannel)

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
    cmd = _get_commands(self.mock_send, PrepPickUpTips)[0]
    self.assertEqual(len(cmd.tip_positions), 2)
    channels = [tp.channel for tp in cmd.tip_positions]
    self.assertIn(ChannelIndex.RearChannel, channels)
    self.assertIn(ChannelIndex.FrontChannel, channels)

  async def test_pick_up_tips_custom_final_z(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
      final_z=55.0,
    )
    cmd = _get_commands(self.mock_send, PrepPickUpTips)[0]
    self.assertAlmostEqual(cmd.final_z, 55.0)

  async def test_pick_up_tips_default_final_z_from_traverse(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
    )
    cmd = _get_commands(self.mock_send, PrepPickUpTips)[0]
    self.assertAlmostEqual(cmd.final_z, _TRAVERSE_HEIGHT)

  async def test_pick_up_tips_z_seek_offset(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
      z_seek_offset=3.0,
    )
    cmd = _get_commands(self.mock_send, PrepPickUpTips)[0]
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
    cmd = _get_commands(self.mock_send, PrepDropTips)[0]
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
    cmd = _get_commands(self.mock_send, PrepDropTips)[0]
    dp = cmd.tip_positions[0]
    loc = waste.get_absolute_location("c", "c", "t")
    # Waste: same as tip spots — z_position so tip bottom lands at surface; z_seek for approach
    expected_z = loc.z + (tip.total_tip_length - tip.fitting_depth)
    expected_z_seek = loc.z + tip.total_tip_length + 10.0
    self.assertAlmostEqual(dp.z_position, expected_z, places=3)
    self.assertAlmostEqual(dp.z_seek, expected_z_seek, places=3)
    # Default roll-off when all Trash; use Stall so pipette detects contact before release
    self.assertAlmostEqual(cmd.tip_roll_off_distance, 3.0)
    self.assertEqual(dp.drop_type, TipDropType.Stall)

  async def test_drop_tips_stall_type(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
      drop_type=TipDropType.Stall,
    )
    cmd = _get_commands(self.mock_send, PrepDropTips)[0]
    self.assertEqual(cmd.tip_positions[0].drop_type, TipDropType.Stall)

  async def test_drop_tips_roll_off_distance(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.backend.drop_tips(
      [Drop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)],
      use_channels=[0],
      tip_roll_off_distance=2.5,
    )
    cmd = _get_commands(self.mock_send, PrepDropTips)[0]
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
    cmd = _get_commands(self.mock_send, PrepDropTips)[0]
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
    backend.client.send_command = unittest.mock.AsyncMock(return_value=None)
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

  def _make_asp(self, well_name="A1", volume=100.0, flow_rate=None,
                liquid_height=5.0, blow_out_air_volume=0.0):
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
    self.assertEqual(len(_get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)), 1)

  async def test_aspirate_tadm_mode(self):
    await self.backend.aspirate(
      [self._make_asp()], use_channels=[0],
      monitoring_mode=MonitoringMode.TADM,
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepAspirateTadmV2)), 1)

  async def test_aspirate_lld_mode(self):
    await self.backend.aspirate([self._make_asp()], use_channels=[0], use_lld=True)
    self.assertEqual(len(_get_commands(self.mock_send, PrepAspirateWithLldV2)), 1)

  async def test_aspirate_lld_tadm_mode(self):
    await self.backend.aspirate(
      [self._make_asp()], use_channels=[0],
      use_lld=True, monitoring_mode=MonitoringMode.TADM,
    )
    self.assertEqual(len(_get_commands(self.mock_send, PrepAspirateWithLldTadmV2)), 1)

  async def test_aspirate_implicit_lld_via_lld_param(self):
    """Passing lld= activates LLD path without use_lld=True."""
    custom_lld = LldParameters(
      default_values=False, z_seek=90.0, z_seek_speed=5.0, z_submerge=2.0, z_out_of_liquid=1.0,
    )
    await self.backend.aspirate(
      [self._make_asp()], use_channels=[0], lld=custom_lld,
    )
    cmds = _get_commands(self.mock_send, PrepAspirateWithLldV2)
    self.assertEqual(len(cmds), 1)
    # Verify the provided LLD parameters are used (not auto-derived)
    lld = cmds[0].aspirate_parameters[0].lld
    self.assertAlmostEqual(lld.z_seek, 90.0)

  async def test_aspirate_lld_auto_seek_z(self):
    """Auto-derived LLD z_seek equals the top-of-well Z."""
    well = self.plate.get_item("A1")
    op = self._make_asp()
    _, _, top_of_well_z, _ = _absolute_z_from_well(op)
    await self.backend.aspirate([op], use_channels=[0], use_lld=True)
    cmd = _get_commands(self.mock_send, PrepAspirateWithLldV2)[0]
    lld = cmd.aspirate_parameters[0].lld
    self.assertAlmostEqual(lld.z_seek, top_of_well_z, places=3)

  # --- Channel mapping ---

  async def test_aspirate_channel_0_is_rear(self):
    await self.backend.aspirate([self._make_asp()], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    self.assertEqual(cmd.aspirate_parameters[0].channel, ChannelIndex.RearChannel)

  async def test_aspirate_channel_1_is_front(self):
    await self.backend.aspirate([self._make_asp()], use_channels=[1])
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    self.assertEqual(cmd.aspirate_parameters[0].channel, ChannelIndex.FrontChannel)

  async def test_aspirate_two_channels(self):
    ops = [self._make_asp("A1", volume=100.0, flow_rate=50.0),
           self._make_asp("B1", volume=150.0, flow_rate=75.0)]
    await self.backend.aspirate(ops, use_channels=[0, 1])
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    self.assertEqual(len(cmd.aspirate_parameters), 2)
    channels = {p.channel for p in cmd.aspirate_parameters}
    self.assertIn(ChannelIndex.RearChannel, channels)
    self.assertIn(ChannelIndex.FrontChannel, channels)

  # --- Volume and flow rate ---

  async def test_aspirate_volume_corrected_by_hlc(self):
    """HLC-corrected volume is sent, not raw op.volume."""
    op = self._make_asp(volume=100.0)
    hlc = get_star_liquid_class(
      tip_volume=self.tip.maximal_volume, is_core=False, is_tip=True,
      has_filter=self.tip.has_filter, liquid=Liquid.WATER, jet=False, blow_out=False,
    )
    if hlc is not None:
      expected_vol = hlc.compute_corrected_volume(100.0)
    else:
      expected_vol = 100.0
    await self.backend.aspirate([op], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    actual_vol = cmd.aspirate_parameters[0].common.liquid_volume
    self.assertAlmostEqual(actual_vol, expected_vol, places=2)

  async def test_aspirate_disable_volume_correction(self):
    """Raw volume used when disable_volume_correction=True."""
    raw_volume = 100.0
    await self.backend.aspirate(
      [self._make_asp(volume=raw_volume)], use_channels=[0],
      disable_volume_correction=[True],
    )
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    actual_vol = cmd.aspirate_parameters[0].common.liquid_volume
    self.assertAlmostEqual(actual_vol, raw_volume, places=2)

  async def test_aspirate_explicit_flow_rate(self):
    await self.backend.aspirate([self._make_asp(flow_rate=60.0)], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    self.assertAlmostEqual(cmd.aspirate_parameters[0].common.liquid_speed, 60.0)

  async def test_aspirate_flow_rate_from_hlc_default(self):
    """flow_rate=None -> uses HLC aspiration_flow_rate."""
    hlc = get_star_liquid_class(
      tip_volume=self.tip.maximal_volume, is_core=False, is_tip=True,
      has_filter=self.tip.has_filter, liquid=Liquid.WATER, jet=False, blow_out=False,
    )
    await self.backend.aspirate([self._make_asp(flow_rate=None)], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    expected = hlc.aspiration_flow_rate if hlc is not None else 100.0
    self.assertAlmostEqual(cmd.aspirate_parameters[0].common.liquid_speed, expected, places=2)

  async def test_aspirate_explicit_settling_time_override(self):
    await self.backend.aspirate(
      [self._make_asp()], use_channels=[0],
      settling_time=[2.0],
    )
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    self.assertAlmostEqual(cmd.aspirate_parameters[0].common.settling_time, 2.0)

  async def test_aspirate_hlc_settling_time_default(self):
    """Settling time from HLC when not explicitly passed."""
    hlc = get_star_liquid_class(
      tip_volume=self.tip.maximal_volume, is_core=False, is_tip=True,
      has_filter=self.tip.has_filter, liquid=Liquid.WATER, jet=False, blow_out=False,
    )
    await self.backend.aspirate([self._make_asp()], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    expected = hlc.aspiration_settling_time if hlc is not None else 1.0
    self.assertAlmostEqual(cmd.aspirate_parameters[0].common.settling_time, expected, places=3)

  async def test_aspirate_auto_container_geometry(self):
    """auto_container_geometry=True produces non-empty container_description."""
    await self.backend.aspirate(
      [self._make_asp()], use_channels=[0],
      auto_container_geometry=True,
    )
    cmd = _get_commands(self.mock_send, PrepAspirateNoLldMonitoringV2)[0]
    segs = cmd.aspirate_parameters[0].container_description
    self.assertGreater(len(segs), 0)
    self.assertIsInstance(segs[0], SegmentDescriptor)


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

  def _make_disp(self, well_name="A1", volume=100.0, flow_rate=None,
                 liquid_height=5.0, blow_out_air_volume=0.0):
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
    self.assertEqual(len(_get_commands(self.mock_send, PrepDispenseNoLldV2)), 1)

  async def test_dispense_lld_mode(self):
    await self.backend.dispense([self._make_disp()], use_channels=[0], use_lld=True)
    self.assertEqual(len(_get_commands(self.mock_send, PrepDispenseWithLldV2)), 1)

  async def test_dispense_volume_corrected(self):
    hlc = get_star_liquid_class(
      tip_volume=self.tip.maximal_volume, is_core=False, is_tip=True,
      has_filter=self.tip.has_filter, liquid=Liquid.WATER, jet=False, blow_out=False,
    )
    raw = 100.0
    expected = hlc.compute_corrected_volume(raw) if hlc else raw
    await self.backend.dispense([self._make_disp(volume=raw)], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepDispenseNoLldV2)[0]
    self.assertAlmostEqual(cmd.dispense_parameters[0].common.liquid_volume, expected, places=2)

  async def test_dispense_explicit_stop_back_volume(self):
    await self.backend.dispense(
      [self._make_disp()], use_channels=[0],
      stop_back_volume=[3.0],
    )
    cmd = _get_commands(self.mock_send, PrepDispenseNoLldV2)[0]
    self.assertAlmostEqual(cmd.dispense_parameters[0].dispense.stop_back_volume, 3.0)

  async def test_dispense_explicit_cutoff_speed(self):
    await self.backend.dispense(
      [self._make_disp()], use_channels=[0],
      cutoff_speed=[75.0],
    )
    cmd = _get_commands(self.mock_send, PrepDispenseNoLldV2)[0]
    self.assertAlmostEqual(cmd.dispense_parameters[0].dispense.cutoff_speed, 75.0)

  async def test_dispense_two_channels(self):
    ops = [self._make_disp("A1", volume=100.0), self._make_disp("B1", volume=200.0)]
    await self.backend.dispense(ops, use_channels=[0, 1])
    cmd = _get_commands(self.mock_send, PrepDispenseNoLldV2)[0]
    self.assertEqual(len(cmd.dispense_parameters), 2)

  async def test_dispense_z_minimum_from_well_bottom(self):
    op = self._make_disp()
    loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
    await self.backend.dispense([op], use_channels=[0])
    cmd = _get_commands(self.mock_send, PrepDispenseNoLldV2)[0]
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
    self.assertEqual(len(_get_commands(self.mock_send, MphPickupTips)), 1)
    # Must not send single-channel PrepPickUpTips
    self.assertEqual(len(_get_commands(self.mock_send, PrepPickUpTips)), 0)

  async def test_mph_pickup_default_tip_mask(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.pick_up_tips_mph(tip_spot)
    cmd = _get_commands(self.mock_send, MphPickupTips)[0]
    self.assertEqual(cmd.tip_mask, 0xFF)

  async def test_mph_pickup_custom_tip_mask(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.pick_up_tips_mph(tip_spot, tip_mask=0x0F)
    cmd = _get_commands(self.mock_send, MphPickupTips)[0]
    self.assertEqual(cmd.tip_mask, 0x0F)

  async def test_mph_drop_sends_mph_command(self):
    tip_spot = self.tip_rack.get_item("A1")
    await self.backend.drop_tips_mph(tip_spot)
    self.assertEqual(len(_get_commands(self.mock_send, MphDropTips)), 1)

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
    """When _gripper_tool_on=False, PrepPickUpTool is sent before PrepPickUpPlate."""
    self.assertFalse(self.backend._gripper_tool_on)
    await self.backend.pick_up_resource(self._make_pickup(self.plate))
    tool_cmds = _get_commands(self.mock_send, PrepPickUpTool)
    plate_cmds = _get_commands(self.mock_send, PrepPickUpPlate)
    self.assertEqual(len(tool_cmds), 1)
    self.assertEqual(len(plate_cmds), 1)
    # Tool must be picked up before plate
    all_calls = [c.args[0] for c in self.mock_send.call_args_list]
    self.assertLess(all_calls.index(tool_cmds[0]), all_calls.index(plate_cmds[0]))
    self.assertTrue(self.backend._gripper_tool_on)

  async def test_skip_tool_pickup_when_already_holding(self):
    self.backend._gripper_tool_on = True
    await self.backend.pick_up_resource(self._make_pickup(self.plate))
    self.assertEqual(len(_get_commands(self.mock_send, PrepPickUpTool)), 0)
    self.assertEqual(len(_get_commands(self.mock_send, PrepPickUpPlate)), 1)

  async def test_plate_dimensions_from_resource(self):
    await self.backend.pick_up_resource(self._make_pickup(self.plate))
    cmd = _get_commands(self.mock_send, PrepPickUpPlate)[0]
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
    cmd = _get_commands(self.mock_send, PrepPickUpPlate)[0]
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
    drop_plate_cmds = _get_commands(self.mock_send, PrepDropPlate)
    drop_tool_cmds = _get_commands(self.mock_send, PrepDropTool)
    self.assertEqual(len(drop_plate_cmds), 1)
    self.assertEqual(len(drop_tool_cmds), 1)
    self.assertFalse(self.backend._gripper_tool_on)

  async def test_drop_resource_without_return_gripper(self):
    self.backend._gripper_tool_on = True
    plate_loc = self.plate.get_absolute_location()
    drop = self._make_drop(self.plate, destination=plate_loc)
    await self.backend.drop_resource(drop, return_gripper=False)
    self.assertEqual(len(_get_commands(self.mock_send, PrepDropTool)), 0)
    self.assertEqual(len(_get_commands(self.mock_send, PrepDropPlate)), 1)

  async def test_move_picked_up_resource(self):
    self.backend._gripper_tool_on = True
    dest = Coordinate(100.0, 50.0, 10.0)
    move = self._make_move(self.plate, location=dest)
    await self.backend.move_picked_up_resource(move)
    cmds = _get_commands(self.mock_send, PrepMovePlate)
    self.assertEqual(len(cmds), 1)


# =============================================================================
# 8. Convenience methods
# =============================================================================


class TestPrepBackendConvenience(unittest.IsolatedAsyncioTestCase):
  """Convenience methods: correct command type to correct interface address."""

  async def asyncSetUp(self):
    self.backend = _setup_backend()
    self.mock_send = unittest.mock.AsyncMock(return_value=None)
    self.backend.client.send_command = self.mock_send  # type: ignore[method-assign]

  async def test_park(self):
    await self.backend.park()
    cmds = _get_commands(self.mock_send, PrepPark)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].dest, _MLPREP_ADDR)

  async def test_spread(self):
    await self.backend.spread()
    cmds = _get_commands(self.mock_send, PrepSpread)
    self.assertEqual(len(cmds), 1)
    self.assertEqual(cmds[0].dest, _MLPREP_ADDR)

  async def test_method_begin_automatic_pause(self):
    await self.backend.method_begin(automatic_pause=True)
    cmds = _get_commands(self.mock_send, PrepMethodBegin)
    self.assertEqual(len(cmds), 1)
    self.assertTrue(cmds[0].automatic_pause)

  async def test_method_begin_no_automatic_pause(self):
    await self.backend.method_begin(automatic_pause=False)
    cmds = _get_commands(self.mock_send, PrepMethodBegin)
    self.assertFalse(cmds[0].automatic_pause)

  async def test_method_end(self):
    await self.backend.method_end()
    self.assertEqual(len(_get_commands(self.mock_send, PrepMethodEnd)), 1)

  async def test_method_abort(self):
    await self.backend.method_abort()
    self.assertEqual(len(_get_commands(self.mock_send, PrepMethodAbort)), 1)

  async def test_move_to_position(self):
    await self.backend.move_to_position(x=100.0, y=50.0, z=20.0, use_channels=[0])
    cmds = _get_commands(self.mock_send, PrepMoveToPosition)
    self.assertEqual(len(cmds), 1)
    cmd = cmds[0]
    self.assertAlmostEqual(cmd.move_parameters.gantry_x_position, 100.0)
    self.assertEqual(len(cmd.move_parameters.axis_parameters), 1)
    self.assertEqual(cmd.move_parameters.axis_parameters[0].channel, ChannelIndex.RearChannel)
    self.assertAlmostEqual(cmd.move_parameters.axis_parameters[0].y_position, 50.0)
    self.assertAlmostEqual(cmd.move_parameters.axis_parameters[0].z_position, 20.0)

  async def test_move_to_position_via_lane(self):
    await self.backend.move_to_position(x=100.0, y=50.0, z=20.0, use_channels=[0], via_lane=True)
    self.assertEqual(len(_get_commands(self.mock_send, PrepMoveToPositionViaLane)), 1)
    self.assertEqual(len(_get_commands(self.mock_send, PrepMoveToPosition)), 0)

  async def test_move_channels_to_safe_z_all(self):
    await self.backend.move_channels_to_safe_z()
    cmds = _get_commands(self.mock_send, PrepMoveZUpToSafe)
    self.assertEqual(len(cmds), 1)
    channels = cmds[0].channels
    self.assertIn(ChannelIndex.RearChannel, channels)
    self.assertIn(ChannelIndex.FrontChannel, channels)

  async def test_set_deck_light(self):
    await self.backend.set_deck_light(white=100, red=50, green=25, blue=200)
    cmds = _get_commands(self.mock_send, PrepSetDeckLight)
    self.assertEqual(len(cmds), 1)
    cmd = cmds[0]
    self.assertEqual(cmd.white, 100)
    self.assertEqual(cmd.red, 50)
    self.assertEqual(cmd.green, 25)
    self.assertEqual(cmd.blue, 200)
    self.assertEqual(cmd.dest, _MLPREP_ADDR)

  async def test_not_implemented_96_ops(self):
    from pylabrobot.liquid_handling.standard import (
      DropTipRack, MultiHeadAspirationPlate, MultiHeadDispensePlate, PickupTipRack,
    )
    with self.assertRaises(NotImplementedError):
      await self.backend.pick_up_tips96(None)  # type: ignore[arg-type]
    with self.assertRaises(NotImplementedError):
      await self.backend.drop_tips96(None)  # type: ignore[arg-type]
    with self.assertRaises(NotImplementedError):
      await self.backend.aspirate96(None)  # type: ignore[arg-type]
    with self.assertRaises(NotImplementedError):
      await self.backend.dispense96(None)  # type: ignore[arg-type]


if __name__ == "__main__":
  unittest.main()
