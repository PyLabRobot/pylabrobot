"""Tests for OpentronsFlex device shell + head composition (Task 2).

Drives ``OpentronsFlex.setup()`` with an injected ``ChatterboxTransport`` (no
network) reporting a configurable mounted pipette, and asserts discovery
composes the matching head onto the right attribute (``left``/``right``/
``head96``).
"""

import asyncio
import unittest
from typing import List, Tuple

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_head import FlexHead1, FlexHead8, FlexHead96
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import ChatterboxTransport
from pylabrobot.resources import cor_96_wellplate_360uL_Fb, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.errors import TooLittleLiquidError
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul


def _flex(pipette: Tuple[str, int, float, float], mount: str = "right") -> OpentronsFlex:
  transport = ChatterboxTransport(pipette=pipette, mount=mount)
  return OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)


def _flex_with_transport(
  pipettes: List[Tuple[str, int, float, float, str]],
  **transport_kwargs,
) -> Tuple[OpentronsFlex, ChatterboxTransport]:
  """Like ``_flex`` but simulates multiple mounted pipettes and returns the
  transport too, so a test can inspect recorded commands.

  ``transport_kwargs`` are forwarded to ``ChatterboxTransport`` (e.g.
  ``simulate_failed_pickup=True``/``simulate_stuck_tip=True`` to drive the
  hardware tip-presence sensor model).
  """
  transport = ChatterboxTransport(pipettes=pipettes, **transport_kwargs)
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  return flex, transport


class TestLifecycleHalves(unittest.TestCase):
  """``setup``/``stop`` are the two halves run together; each half is callable alone.

  The split exists because taking the robot and moving it are separate acts. An
  operator reclaiming the touchscreen wants the session ended and nothing else;
  homing an arm over a deck someone is reaching into is the thing to avoid.
  """

  def test_connect_opens_the_link_but_starts_no_run(self):
    """The run is what the robot holds against its touchscreen, so taking the
    robot is create_run's doing, not connect's."""
    flex, transport = _flex_with_transport([("p50_multi_flex", 8, 1.0, 50.0, "left")])

    asyncio.run(flex.connect())
    try:
      self.assertIsNone(flex.run_id)
      self.assertEqual([c for c in transport.commands if c["commandType"] == "home"], [])
    finally:
      asyncio.run(flex.disconnect())

  def test_initialize_discovers_without_moving(self):
    """Asking what is mounted must not require moving the robot to find out."""
    flex, transport = _flex_with_transport([("p50_multi_flex", 8, 1.0, 50.0, "left")])
    asyncio.run(flex.connect())
    asyncio.run(flex.create_run())

    asyncio.run(flex.initialize())
    try:
      self.assertIsInstance(flex.left, FlexHead8)
      self.assertEqual([c for c in transport.commands if c["commandType"] == "home"], [])
    finally:
      asyncio.run(flex.disconnect())

  def test_cancel_run_frees_local_control_without_dropping_the_link(self):
    """The operator's 'give me the robot back' action: end the session, stay connected."""
    flex, transport = _flex_with_transport([("p50_multi_flex", 8, 1.0, 50.0, "left")])
    asyncio.run(flex.setup())
    homes_from_setup = len([c for c in transport.commands if c["commandType"] == "home"])

    asyncio.run(flex.cancel_run())
    try:
      self.assertIsNone(flex.run_id)
      self.assertEqual(
        len([c for c in transport.commands if c["commandType"] == "home"]), homes_from_setup
      )
    finally:
      asyncio.run(flex.disconnect())

  def test_disconnect_ends_the_run_without_homing(self):
    flex, transport = _flex_with_transport([("p50_multi_flex", 8, 1.0, 50.0, "left")])
    asyncio.run(flex.setup())
    homes_from_setup = len([c for c in transport.commands if c["commandType"] == "home"])

    asyncio.run(flex.disconnect())

    self.assertIsNone(flex.run_id)
    self.assertEqual(
      len([c for c in transport.commands if c["commandType"] == "home"]), homes_from_setup
    )

  def test_stop_still_homes_before_releasing(self):
    """The one-call teardown keeps parking the gantry; only disconnect skips it."""
    flex, transport = _flex_with_transport([("p50_multi_flex", 8, 1.0, 50.0, "left")])
    asyncio.run(flex.setup())
    homes_from_setup = len([c for c in transport.commands if c["commandType"] == "home"])

    asyncio.run(flex.stop())

    self.assertEqual(
      len([c for c in transport.commands if c["commandType"] == "home"]), homes_from_setup + 1
    )
    self.assertIsNone(flex.run_id)


class TestHeadDiscovery(unittest.TestCase):
  """setup() discovers mounted pipettes and composes the matching head per mount."""

  def test_eight_channel_left_mount_becomes_flex_head8(self):
    flex = _flex(("p50_multi_flex", 8, 1.0, 50.0), mount="left")
    asyncio.run(flex.setup())
    try:
      self.assertIsInstance(flex.left, FlexHead8)
      self.assertIsNone(flex.right)
      self.assertIsNone(flex.head96)
    finally:
      asyncio.run(flex.stop())

  def test_eight_channel_right_mount_becomes_flex_head8(self):
    flex = _flex(("p50_multi_flex", 8, 1.0, 50.0), mount="right")
    asyncio.run(flex.setup())
    try:
      self.assertIsInstance(flex.right, FlexHead8)
      self.assertIsNone(flex.left)
      self.assertIsNone(flex.head96)
    finally:
      asyncio.run(flex.stop())

  def test_single_channel_becomes_flex_head1(self):
    flex = _flex(("p1000_single_flex", 1, 1.0, 1000.0), mount="right")
    asyncio.run(flex.setup())
    try:
      self.assertIsInstance(flex.right, FlexHead1)
      self.assertIsNone(flex.left)
      self.assertIsNone(flex.head96)
    finally:
      asyncio.run(flex.stop())

  def test_ninety_six_channel_becomes_head96_leaves_mounts_none(self):
    flex = _flex(("p1000_96", 96, 1.0, 1000.0), mount="left")
    asyncio.run(flex.setup())
    try:
      self.assertIsInstance(flex.head96, FlexHead96)
      self.assertIsNone(flex.left)
      self.assertIsNone(flex.right)
    finally:
      asyncio.run(flex.stop())

  def test_unsupported_channel_count_raises_opentrons_error(self):
    flex = _flex(("weird_pipette", 4, 1.0, 100.0), mount="right")
    with self.assertRaises(OpentronsError):
      asyncio.run(flex.setup())


class TestNoDoubleLoad(unittest.TestCase):
  """Regression for the double-``loadPipette`` bug (base ``setup()`` used to
  discover+load the first mount, then ``_model_setup()`` loaded it again).
  """

  def test_single_pipette_is_loaded_exactly_once(self):
    flex, transport = _flex_with_transport([("p50_multi_flex", 8, 1.0, 50.0, "left")])
    asyncio.run(flex.setup())
    try:
      self.assertEqual(len(transport.load_pipette_commands), 1)
    finally:
      asyncio.run(flex.stop())


class TestDualMount(unittest.TestCase):
  """setup() discovers and composes BOTH mounts when two pipettes are present."""

  def test_left_and_right_mounts_become_distinct_heads(self):
    flex, transport = _flex_with_transport(
      [
        ("p50_multi_flex", 8, 1.0, 50.0, "left"),
        ("p1000_single_flex", 1, 1.0, 1000.0, "right"),
      ]
    )
    asyncio.run(flex.setup())
    try:
      self.assertIsInstance(flex.left, FlexHead8)
      self.assertIsInstance(flex.right, FlexHead1)
      assert flex.left is not None and flex.right is not None
      self.assertNotEqual(flex.left.pipette_id, flex.right.pipette_id)
      self.assertEqual(len(transport.load_pipette_commands), 2)
    finally:
      asyncio.run(flex.stop())


class TestNoPipetteMounted(unittest.TestCase):
  """setup() raises OpentronsError when no pipette is mounted at all."""

  def test_empty_pipette_list_raises_opentrons_error(self):
    flex, _transport = _flex_with_transport([])
    with self.assertRaises(OpentronsError):
      asyncio.run(flex.setup())


class TestImpossibleHead96PlusMountCombo(unittest.TestCase):
  """A 96-channel head cannot physically coexist with a mount pipette."""

  def test_head96_plus_mount_pipette_raises_opentrons_error(self):
    flex, _transport = _flex_with_transport(
      [
        ("p1000_96", 96, 1.0, 1000.0, "left"),
        ("p1000_single_flex", 1, 1.0, 1000.0, "right"),
      ]
    )
    with self.assertRaises(OpentronsError):
      asyncio.run(flex.setup())


class TestGetMountedTips(unittest.TestCase):
  """get_mounted_tips() returns a list sized to the head's channel count, and a copy."""

  def test_eight_channel_head_reports_eight_slots(self):
    flex = _flex(("p50_multi_flex", 8, 1.0, 50.0), mount="left")
    asyncio.run(flex.setup())
    try:
      head = flex.left
      assert head is not None
      tips = head.get_mounted_tips()
      self.assertEqual(len(tips), 8)
      self.assertTrue(all(tip is None for tip in tips))
    finally:
      asyncio.run(flex.stop())

  def test_returned_list_is_a_copy(self):
    flex = _flex(("p1000_single_flex", 1, 1.0, 1000.0), mount="right")
    asyncio.run(flex.setup())
    try:
      head = flex.right
      assert head is not None
      tips = head.get_mounted_tips()
      tips.append(None)  # mutate the returned list; must not affect head state
      self.assertEqual(len(head.get_mounted_tips()), 1)
    finally:
      asyncio.run(flex.stop())

  def test_ninety_six_channel_head_reports_ninety_six_slots(self):
    flex = _flex(("p1000_96", 96, 1.0, 1000.0), mount="left")
    asyncio.run(flex.setup())
    try:
      head = flex.head96
      assert head is not None
      self.assertEqual(len(head.get_mounted_tips()), 96)
    finally:
      asyncio.run(flex.stop())


def _flex_head8(**transport_kwargs) -> Tuple[OpentronsFlex, ChatterboxTransport, FlexHead8]:
  """An ``OpentronsFlex`` with an 8-channel head on the left mount, plus the
  transport (for command inspection) and the head itself.

  ``transport_kwargs`` are forwarded to ``ChatterboxTransport``.
  """
  flex, transport = _flex_with_transport(
    [("p50_multi_flex", 8, 1.0, 50.0, "left")], **transport_kwargs
  )
  asyncio.run(flex.setup())
  head = flex.left
  assert isinstance(head, FlexHead8)
  return flex, transport, head


class TestFlexHead8ColumnOps(unittest.TestCase):
  """Task 3: column ops send exactly ONE wire command anchored at the
  column's A-row well; the hardware fans it out to all 8 physical channels;
  trackers commit only for wells/spots the head actually actuated.
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_pick_up_tips_emits_one_command_and_fans_to_all_8_channels(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0))

      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(pickup_cmds), 1)
      self.assertEqual(pickup_cmds[0]["params"]["wellName"], "A1")

      column_spots = rack.get_all_items()[0:8]
      for spot in column_spots:
        self.assertFalse(spot.has_tip())

      tips = head.get_mounted_tips()
      self.assertEqual(sum(1 for t in tips if t is not None), 8)
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_emits_one_command_and_only_column_wells_change(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      # Pre-load every well with 100uL so aspirating 50uL is valid, and so a
      # baseline exists to prove non-column wells are untouched.
      wells = plate.get_all_items()
      for well in wells:
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate(plate, column=2, volume=50))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      self.assertEqual(aspirate_cmds[0]["params"]["wellName"], "A3")

      column_2 = set(wells[16:24])
      for well in wells:
        expected = 50.0 if well in column_2 else 100.0
        self.assertAlmostEqual(well.tracker.volume, expected, msg=well.name)
    finally:
      asyncio.run(flex.stop())

  def test_pick_up_single_tip_configures_nozzle_then_picks_named_well(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_single_tip(rack, well="H2"))

      cmd_types = [c["commandType"] for c in transport.commands]
      configure_idx = cmd_types.index("configureNozzleLayout")
      pickup_idx = cmd_types.index("pickUpTip")
      self.assertLess(configure_idx, pickup_idx)
      self.assertEqual(transport.commands[pickup_idx]["params"]["wellName"], "H2")

      tips = head.get_mounted_tips()
      self.assertIsNotNone(tips[7])
      for i in range(7):
        self.assertIsNone(tips[i], msg=f"channel {i}")
    finally:
      asyncio.run(flex.stop())

  def test_dispense_emits_one_command_and_only_column_wells_change(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.dispense(plate, column=5, volume=30))

      dispense_cmds = [c for c in transport.commands if c["commandType"] == "dispense"]
      self.assertEqual(len(dispense_cmds), 1)
      self.assertEqual(dispense_cmds[0]["params"]["wellName"], "A6")

      wells = plate.get_all_items()
      column_5 = set(wells[40:48])
      for well in wells:
        expected = 30.0 if well in column_5 else 0.0
        self.assertAlmostEqual(well.tracker.volume, expected, msg=well.name)
    finally:
      asyncio.run(flex.stop())

  def test_drop_tips_to_rack_returns_tips_and_clears_channels(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.drop_tips(rack, column=0))  # return to the column it came from

      drop_cmds = [c for c in transport.commands if c["commandType"] == "dropTip"]
      self.assertEqual(len(drop_cmds), 1)
      self.assertEqual(drop_cmds[0]["params"]["wellName"], "A1")

      column_0_spots = rack.get_all_items()[0:8]
      for spot in column_0_spots:
        self.assertTrue(spot.has_tip())

      self.assertTrue(all(t is None for t in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_discard_tips_uses_addressable_area_trash_sequence(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")
      trash = flex.deck.get_trash_area()

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.discard_tips(trash))

      cmd_types = [c["commandType"] for c in transport.commands]
      self.assertIn("moveToAddressableAreaForDropTip", cmd_types)
      self.assertIn("dropTipInPlace", cmd_types)
      move_cmd = next(
        c for c in transport.commands if c["commandType"] == "moveToAddressableAreaForDropTip"
      )
      self.assertEqual(move_cmd["params"]["addressableAreaName"], "movableTrashA3")
      self.assertTrue(all(t is None for t in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_single_tip_aspirate_dispense_and_drop_round_trip(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      trash = flex.deck.get_trash_area()

      asyncio.run(head.pick_up_single_tip(rack, well="A1"))
      self.assertIsNotNone(head.get_mounted_tips()[0])

      asyncio.run(head.dispense_single(plate, well="B3", volume=20))
      target = plate.get_item("B3")
      self.assertAlmostEqual(target.tracker.volume, 20.0)

      # Every other well is untouched (single-tip is a strict None-skip case).
      for well in plate.get_all_items():
        if well is target:
          continue
        self.assertAlmostEqual(well.tracker.volume, 0.0, msg=well.name)

      target.tracker.set_volume(20.0)  # aspirate needs liquid present
      asyncio.run(head.aspirate_single(plate, well="B3", volume=20))
      self.assertAlmostEqual(target.tracker.volume, 0.0)

      asyncio.run(head.drop_single_tip(trash))
      self.assertTrue(all(t is None for t in head.get_mounted_tips()))

      # Nozzle layout is restored to ALL, so a subsequent column op needs no
      # extra reset command beyond the ones already issued.
      asyncio.run(head.pick_up_tips(rack, column=1))
      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(pickup_cmds[-1]["params"]["wellName"], "A2")
    finally:
      asyncio.run(flex.stop())


class TestFlexHead8PrepareToAspirate(unittest.TestCase):
  """Task 3 fix #1: `prepareToAspirate` must fire once, immediately before the
  FIRST aspirate after a tip pickup, and NOT before subsequent aspirates."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_prepare_to_aspirate_sent_before_first_aspirate_only(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      for well in plate.get_all_items():
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate(plate, column=0, volume=10))
      asyncio.run(head.aspirate(plate, column=1, volume=10))

      cmd_types = [c["commandType"] for c in transport.commands]
      prepare_indices = [i for i, t in enumerate(cmd_types) if t == "prepareToAspirate"]
      aspirate_indices = [i for i, t in enumerate(cmd_types) if t == "aspirate"]

      self.assertEqual(len(prepare_indices), 1, "prepareToAspirate must fire exactly once")
      self.assertEqual(len(aspirate_indices), 2)
      self.assertEqual(prepare_indices[0], aspirate_indices[0] - 1)

      prepare_cmd = transport.commands[prepare_indices[0]]
      self.assertEqual(prepare_cmd["params"], {"pipetteId": head.pipette_id})
    finally:
      asyncio.run(flex.stop())

  def test_prepare_to_aspirate_refires_after_a_new_pickup(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      for well in plate.get_all_items():
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate(plate, column=0, volume=10))
      asyncio.run(head.drop_tips(rack, column=0))
      asyncio.run(head.pick_up_tips(rack, column=1))
      asyncio.run(head.aspirate(plate, column=1, volume=10))

      cmd_types = [c["commandType"] for c in transport.commands]
      prepare_indices = [i for i, t in enumerate(cmd_types) if t == "prepareToAspirate"]
      self.assertEqual(len(prepare_indices), 2, "a new pickup must require a new prepare")
    finally:
      asyncio.run(flex.stop())


class TestFlexHead8PickupOrigin(unittest.TestCase):
  """Task 3 fix #2: tip-pickup offsets must use wellLocation.origin == 'top',
  not the 'bottom' origin used for aspirate/dispense."""

  def test_pick_up_tips_offset_uses_top_origin(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0, offset=Coordinate(x=0, y=0, z=1)))

      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(pickup_cmds), 1)
      self.assertEqual(pickup_cmds[0]["params"]["wellLocation"]["origin"], "top")
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_offset_still_uses_bottom_origin(self):
    set_tip_tracking(True)
    set_volume_tracking(True)
    try:
      flex, transport, head = _flex_head8()
      try:
        rack = flex_96_tiprack_50ul(name="rack")
        plate = cor_96_wellplate_360uL_Fb(name="plate")
        plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
        flex.deck.assign_child_at_slot(rack, "C1")
        flex.deck.assign_child_at_slot(plate, "C2")
        for well in plate.get_all_items():
          well.tracker.set_volume(100.0)

        asyncio.run(head.pick_up_tips(rack, column=0))
        asyncio.run(head.aspirate(plate, column=0, volume=10, offset=Coordinate(x=0, y=0, z=1)))

        aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
        self.assertEqual(aspirate_cmds[0]["params"]["wellLocation"]["origin"], "bottom")
      finally:
        asyncio.run(flex.stop())
    finally:
      set_tip_tracking(False)
      set_volume_tracking(False)


class TestFlexHead8TransactionalTrackers(unittest.TestCase):
  """Task 3 fix #3: infeasible tracker operations must raise BEFORE any wire
  command is sent, and must not leave trackers mutated."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_infeasible_aspirate_raises_before_wire_command_and_leaves_trackers_unchanged(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      # Column 0 wells are left at 0uL -- aspirating 50uL is infeasible.
      asyncio.run(head.pick_up_tips(rack, column=0))

      with self.assertRaises(TooLittleLiquidError):
        asyncio.run(head.aspirate(plate, column=0, volume=50))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 0, "no aspirate wire command may be sent")

      for well in plate.get_all_items()[0:8]:
        self.assertAlmostEqual(well.tracker.volume, 0.0)
        self.assertAlmostEqual(well.tracker.get_used_volume(), 0.0)
    finally:
      asyncio.run(flex.stop())


class TestFlexHead8DoublePickupGuard(unittest.TestCase):
  """Task 3 fix #4: picking up onto an already-occupied channel must raise
  OpentronsError rather than silently overwrite head state."""

  def setUp(self):
    set_tip_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)

  def test_pick_up_tips_onto_occupied_channels_raises(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0))
      with self.assertRaises(OpentronsError):
        asyncio.run(head.pick_up_tips(rack, column=1))

      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(pickup_cmds), 1, "the second (invalid) pickup must not reach the wire")
    finally:
      asyncio.run(flex.stop())

  def test_pick_up_single_tip_onto_occupied_channel_raises(self):
    set_tip_tracking(True)
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_single_tip(rack, well="A1"))
      with self.assertRaises(OpentronsError):
        asyncio.run(head.pick_up_single_tip(rack, well="A2"))  # same channel (row A)
    finally:
      asyncio.run(flex.stop())


class TestFlexHead8EnsureAllModeReset(unittest.TestCase):
  """Task 3 fix #5: a column op directly after a single-tip pickup (no
  intervening drop) must emit a configureNozzleLayout(ALL) reset first."""

  def test_column_op_after_single_pickup_resets_nozzle_layout(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_single_tip(rack, well="A1"))
      # Simulate the mounted single tip having been cleared through a path
      # not under test here, so the column op's occupied-channel guard
      # (fix #4) doesn't fire -- isolating the nozzle-layout reset (fix #5).
      head._channel_tips = [None] * head.channels
      self.assertEqual(head._nozzle_layout, "SINGLE")

      asyncio.run(head.pick_up_tips(rack, column=1))

      cmd_types = [c["commandType"] for c in transport.commands]
      configure_indices = [i for i, t in enumerate(cmd_types) if t == "configureNozzleLayout"]
      pickup_indices = [i for i, t in enumerate(cmd_types) if t == "pickUpTip"]
      # The reset configureNozzleLayout (the one before the column pickUpTip)
      # must come before that pickUpTip.
      self.assertGreater(len(configure_indices), 1)
      self.assertLess(configure_indices[-1], pickup_indices[-1])
      self.assertEqual(
        transport.commands[configure_indices[-1]]["params"]["configurationParams"]["style"],
        "ALL",
      )
    finally:
      asyncio.run(flex.stop())


class TestFlexHead8SingleOpFlowRateAndNoneSkip(unittest.TestCase):
  """Task 3 fix #7: aspirate_single/dispense_single accept a flow_rate
  override, and a partially-filled column pickup leaves missing-tip
  channels' wells untouched (None-skip) on a later column aspirate."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_aspirate_single_and_dispense_single_accept_flow_rate_override(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_single_tip(rack, well="A1"))
      asyncio.run(head.dispense_single(plate, well="A1", volume=20, flow_rate=99.0))
      asyncio.run(head.aspirate_single(plate, well="A1", volume=20, flow_rate=88.0))

      dispense_cmd = next(c for c in transport.commands if c["commandType"] == "dispense")
      aspirate_cmd = next(c for c in transport.commands if c["commandType"] == "aspirate")
      self.assertEqual(dispense_cmd["params"]["flowRate"], 99.0)
      self.assertEqual(aspirate_cmd["params"]["flowRate"], 88.0)
    finally:
      asyncio.run(flex.stop())

  def test_partially_filled_column_pickup_skips_missing_tip_wells_on_aspirate(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      # Empty out channels B (index 1) and G (index 6) of column 0 before pickup.
      column_0_spots = rack.get_all_items()[0:8]
      column_0_spots[1].tracker.remove_tip(commit=True)
      column_0_spots[6].tracker.remove_tip(commit=True)

      for well in plate.get_all_items():
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      tips = head.get_mounted_tips()
      self.assertIsNone(tips[1])
      self.assertIsNone(tips[6])

      asyncio.run(head.aspirate(plate, column=0, volume=20))

      wells = plate.get_all_items()[0:8]
      for i, well in enumerate(wells):
        expected = 100.0 if i in (1, 6) else 80.0
        self.assertAlmostEqual(well.tracker.volume, expected, msg=f"channel {i}")
    finally:
      asyncio.run(flex.stop())


class TestFlexHead8HardwareTipPresence(unittest.TestCase):
  """Task 5: the Flex hardware tip-presence sensor (one bool per pipette,
  via /instruments -> state.tipDetected) is the aggregate authority used to
  verify a pickup seated a tip and to confirm a drop cleared it.
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_has_tip_on_hardware_true_after_successful_pickup(self):
    flex, _transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0))

      self.assertTrue(asyncio.run(head.has_tip_on_hardware()))
    finally:
      asyncio.run(flex.stop())

  def test_simulated_failed_pickup_raises_and_leaves_no_tracker_mutation(self):
    flex, transport, head = _flex_head8(simulate_failed_pickup=True)
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      with self.assertRaises(OpentronsError):
        asyncio.run(head.pick_up_tips(rack, column=0))

      # The pickUpTip wire command WAS sent (the sensor is what caught the
      # failure, not a pre-wire guard) -- but nothing downstream persisted.
      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(pickup_cmds), 1)

      column_0_spots = rack.get_all_items()[0:8]
      for spot in column_0_spots:
        self.assertTrue(spot.has_tip(), msg=f"{spot.name} tracker must not have been committed")

      self.assertTrue(all(t is None for t in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_simulated_failed_pickup_single_tip_raises_and_leaves_no_tracker_mutation(self):
    flex, transport, head = _flex_head8(simulate_failed_pickup=True)
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      with self.assertRaises(OpentronsError):
        asyncio.run(head.pick_up_single_tip(rack, well="A1"))

      spot = rack.get_item("A1")
      self.assertTrue(spot.has_tip())
      self.assertTrue(all(t is None for t in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_has_tip_on_hardware_false_after_drop_tips(self):
    flex, _transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.drop_tips(rack, column=0))

      self.assertFalse(asyncio.run(head.has_tip_on_hardware()))
    finally:
      asyncio.run(flex.stop())

  def test_has_tip_on_hardware_false_after_discard_tips(self):
    flex, _transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")
      trash = flex.deck.get_trash_area()

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.discard_tips(trash))

      self.assertFalse(asyncio.run(head.has_tip_on_hardware()))
    finally:
      asyncio.run(flex.stop())

  def test_simulated_stuck_tip_after_drop_logs_warning(self):
    flex, _transport, head = _flex_head8(simulate_stuck_tip=True)
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0))
      with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING") as log_ctx:
        asyncio.run(head.drop_tips(rack, column=0))

      self.assertTrue(
        any("stuck" in msg.lower() or "clear" in msg.lower() for msg in log_ctx.output)
      )
      # Trackers still commit -- the confirm step only warns, never raises.
      column_0_spots = rack.get_all_items()[0:8]
      for spot in column_0_spots:
        self.assertTrue(spot.has_tip())
      self.assertTrue(all(t is None for t in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())


def _flex_head1(**transport_kwargs) -> Tuple[OpentronsFlex, ChatterboxTransport, FlexHead1]:
  """An ``OpentronsFlex`` with a single-channel head on the right mount, plus
  the transport (for command inspection) and the head itself.

  ``transport_kwargs`` are forwarded to ``ChatterboxTransport``.
  """
  flex, transport = _flex_with_transport(
    [("p1000_single_flex", 1, 1.0, 1000.0, "right")], **transport_kwargs
  )
  asyncio.run(flex.setup())
  head = flex.right
  assert isinstance(head, FlexHead1)
  return flex, transport, head


def _flex_head96(**transport_kwargs) -> Tuple[OpentronsFlex, ChatterboxTransport, FlexHead96]:
  """An ``OpentronsFlex`` with a 96-channel head, plus the transport (for
  command inspection) and the head itself.

  ``transport_kwargs`` are forwarded to ``ChatterboxTransport``.
  """
  flex, transport = _flex_with_transport(
    [("p1000_96", 96, 1.0, 1000.0, "left")], **transport_kwargs
  )
  asyncio.run(flex.setup())
  head = flex.head96
  assert isinstance(head, FlexHead96)
  return flex, transport, head


class TestFlexHead1Ops(unittest.TestCase):
  """Task 5: FlexHead1 (single-channel, well-addressed) reuses the FlexHead8
  transactional stage -> wire -> verify -> commit/rollback flow and hardware
  tip-presence machinery, addressing exactly one well/tip spot per command
  instead of a whole column.
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_pick_up_tips_and_aspirate_emit_one_command_each_and_warn_untested(self):
    flex, transport, head = _flex_head1()
    try:
      rack = flex_96_tiprack_50ul(name="rack1")
      plate = cor_96_wellplate_360uL_Fb(name="plate1")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      target_well = plate.get_item("B3")
      target_well.tracker.set_volume(100.0)

      with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING") as log_ctx:
        asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      self.assertTrue(any("not yet verified" in msg.lower() for msg in log_ctx.output))

      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(pickup_cmds), 1)
      self.assertEqual(pickup_cmds[0]["params"]["wellName"], "A1")
      self.assertIsNotNone(head.get_mounted_tips()[0])
      self.assertEqual(len(head.get_mounted_tips()), 1)

      # A 2nd warning call must be a no-op (only the FIRST op logs).
      with self.assertRaises(AssertionError):
        with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING"):
          asyncio.run(head.aspirate(target_well, volume=10))

      cmd_types = [c["commandType"] for c in transport.commands]
      prepare_indices = [i for i, t in enumerate(cmd_types) if t == "prepareToAspirate"]
      aspirate_indices = [i for i, t in enumerate(cmd_types) if t == "aspirate"]
      self.assertEqual(len(prepare_indices), 1, "prepareToAspirate must fire exactly once")
      self.assertEqual(len(aspirate_indices), 1)
      self.assertEqual(prepare_indices[0], aspirate_indices[0] - 1)
      self.assertEqual(transport.commands[aspirate_indices[0]]["params"]["wellName"], "B3")

      # Exactly 1 Well tracked -- every other well on the plate is untouched.
      for well in plate.get_all_items():
        expected = 90.0 if well is target_well else 0.0
        self.assertAlmostEqual(well.tracker.volume, expected, msg=well.name)
    finally:
      asyncio.run(flex.stop())

  def test_double_pickup_onto_occupied_channel_raises(self):
    flex, transport, head = _flex_head1()
    try:
      rack = flex_96_tiprack_50ul(name="rack1")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      with self.assertRaises(OpentronsError):
        asyncio.run(head.pick_up_tips(rack.get_item("A2")))

      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(pickup_cmds), 1, "the second (invalid) pickup must not reach the wire")
    finally:
      asyncio.run(flex.stop())

  def test_simulated_failed_pickup_raises_and_leaves_no_tracker_mutation(self):
    flex, transport, head = _flex_head1(simulate_failed_pickup=True)
    try:
      rack = flex_96_tiprack_50ul(name="rack1")
      flex.deck.assign_child_at_slot(rack, "C1")

      with self.assertRaises(OpentronsError):
        asyncio.run(head.pick_up_tips(rack.get_item("A1")))

      # The pickUpTip wire command WAS sent (the sensor is what caught the
      # failure, not a pre-wire guard) -- but nothing downstream persisted.
      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(pickup_cmds), 1)
      self.assertTrue(rack.get_item("A1").has_tip(), "tracker must not have been committed")
      self.assertTrue(all(t is None for t in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_drop_tips_to_rack_and_discard_to_trash(self):
    flex, transport, head = _flex_head1()
    try:
      rack = flex_96_tiprack_50ul(name="rack1")
      flex.deck.assign_child_at_slot(rack, "C1")
      trash = flex.deck.get_trash_area()

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      asyncio.run(head.drop_tips(rack.get_item("A1")))
      self.assertTrue(rack.get_item("A1").has_tip())
      self.assertIsNone(head.get_mounted_tips()[0])

      asyncio.run(head.pick_up_tips(rack.get_item("A2")))
      asyncio.run(head.discard_tips(trash))
      cmd_types = [c["commandType"] for c in transport.commands]
      self.assertIn("moveToAddressableAreaForDropTip", cmd_types)
      self.assertIn("dropTipInPlace", cmd_types)
      self.assertIsNone(head.get_mounted_tips()[0])
    finally:
      asyncio.run(flex.stop())

  def test_docstring_does_not_claim_hardware_validation(self):
    doc = FlexHead1.__doc__ or ""
    self.assertNotIn("Validated on real", doc)


class TestFlexHead96Ops(unittest.TestCase):
  """Task 5: FlexHead96 (96 fixed nozzles, whole-plate-addressed) reuses the
  FlexHead8 transactional stage -> wire -> verify -> commit/rollback flow and
  hardware tip-presence machinery, fanning ONE command out to all 96
  channels anchored at well "A1".
  """

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_pick_up_tips_configures_all_nozzles_and_picks_at_a1(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack96")
      flex.deck.assign_child_at_slot(rack, "C1")

      with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING") as log_ctx:
        asyncio.run(head.pick_up_tips(rack))
      self.assertTrue(any("not yet verified" in msg.lower() for msg in log_ctx.output))

      cmd_types = [c["commandType"] for c in transport.commands]
      configure_cmds = [
        c for c in transport.commands if c["commandType"] == "configureNozzleLayout"
      ]
      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(configure_cmds), 1)
      self.assertEqual(configure_cmds[0]["params"]["configurationParams"]["style"], "ALL")
      self.assertEqual(len(pickup_cmds), 1)
      self.assertEqual(pickup_cmds[0]["params"]["wellName"], "A1")
      self.assertLess(cmd_types.index("configureNozzleLayout"), cmd_types.index("pickUpTip"))

      tips = head.get_mounted_tips()
      self.assertEqual(len(tips), 96)
      self.assertTrue(all(t is not None for t in tips))
      for spot in rack.get_all_items():
        self.assertFalse(spot.has_tip())
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_emits_one_command_and_tracks_all_96_wells(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack96")
      plate = cor_96_wellplate_360uL_Fb(name="plate96")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      for well in plate.get_all_items():
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.aspirate(plate, volume=50))

      cmd_types = [c["commandType"] for c in transport.commands]
      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      prepare_indices = [i for i, t in enumerate(cmd_types) if t == "prepareToAspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      self.assertEqual(aspirate_cmds[0]["params"]["wellName"], "A1")
      self.assertEqual(len(prepare_indices), 1, "prepareToAspirate must fire before the aspirate")

      wells = plate.get_all_items()
      self.assertEqual(len(wells), 96)
      for well in wells:
        self.assertAlmostEqual(well.tracker.volume, 50.0, msg=well.name)
    finally:
      asyncio.run(flex.stop())

  def test_dispense_and_drop_tips_round_trip(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack96")
      plate = cor_96_wellplate_360uL_Fb(name="plate96")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.dispense(plate, volume=30))

      dispense_cmds = [c for c in transport.commands if c["commandType"] == "dispense"]
      self.assertEqual(len(dispense_cmds), 1)
      self.assertEqual(dispense_cmds[0]["params"]["wellName"], "A1")
      for well in plate.get_all_items():
        self.assertAlmostEqual(well.tracker.volume, 30.0, msg=well.name)

      asyncio.run(head.drop_tips(rack))
      drop_cmds = [c for c in transport.commands if c["commandType"] == "dropTip"]
      self.assertEqual(len(drop_cmds), 1)
      self.assertEqual(drop_cmds[0]["params"]["wellName"], "A1")
      for spot in rack.get_all_items():
        self.assertTrue(spot.has_tip())
      self.assertTrue(all(t is None for t in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_simulated_failed_pickup_raises_and_leaves_no_tracker_mutation(self):
    flex, transport, head = _flex_head96(simulate_failed_pickup=True)
    try:
      rack = flex_96_tiprack_50ul(name="rack96")
      flex.deck.assign_child_at_slot(rack, "C1")

      with self.assertRaises(OpentronsError):
        asyncio.run(head.pick_up_tips(rack))

      # The pickUpTip wire command WAS sent (the sensor is what caught the
      # failure, not a pre-wire guard) -- but nothing downstream persisted.
      pickup_cmds = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(len(pickup_cmds), 1)
      for spot in rack.get_all_items():
        self.assertTrue(spot.has_tip(), msg=f"{spot.name} tracker must not have been committed")
      self.assertTrue(all(t is None for t in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_docstring_does_not_claim_hardware_validation(self):
    doc = FlexHead96.__doc__ or ""
    self.assertNotIn("Validated on real", doc)


if __name__ == "__main__":
  unittest.main()
