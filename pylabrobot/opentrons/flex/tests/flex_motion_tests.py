"""Tests for the Flex direct-motion surface: head jog + position read
(``_FlexHead.position``/``_FlexHead.move_to``) and gripper motion + jaw
control (``FlexGripper.move_to``/``grip``/``open_jaw``).

Drives ``Flex.setup()`` with an injected ``AsyncMock`` and
asserts the exact wire commands: ``savePosition`` reads, ``moveToCoordinates``
axis merging and ``minimumZHeight``/``speed`` handling, the ``robot/moveTo``
extension-mount params, jaw force validation before any wire command, and the
robot-software version gate on the robot/* command family.
"""

import asyncio
import unittest
from typing import List, Tuple
from unittest.mock import AsyncMock, _Call

from pylabrobot.opentrons.flex.checks import traversal_z
from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.flex.flex import Flex
from pylabrobot.opentrons.flex.flex_gripper import FlexGripper, _require_robot_commands
from pylabrobot.opentrons.flex.flex_head import FlexHead8, _FlexHead
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import cor_96_wellplate_360uL_Fb, set_tip_tracking
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul


def _flex_with_gripper(**api_kwargs) -> Tuple[Flex, AsyncMock]:
  """Build a Flex with fixed API replies and return its mock for assertions."""
  api = make_api(
    pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")],
    gripper=True,
    **api_kwargs,
  )
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  return flex, api


def _head(flex: Flex) -> _FlexHead:
  head = flex.right
  assert head is not None
  return head


def _gripper(flex: Flex) -> FlexGripper:
  gripper = flex.gripper
  assert gripper is not None
  return gripper


def _cmds(api: AsyncMock, command_type: str) -> List[_Call]:
  return [c for c in api.submit_command.await_args_list if c.args[1] == command_type]


def _flex_with_version(api_version: str) -> Tuple[Flex, AsyncMock]:
  """A gripper-equipped Flex whose ``/health`` reports ``api_version``, so a
  test can drive the robot/* version gate."""
  return _flex_with_gripper(api_version=api_version)


class TestHeadPosition(unittest.TestCase):
  """position() reads the head's pose from a savePosition command result."""

  def test_position_reads_save_position_result(self):
    flex, api = _flex_with_gripper(saved_position={"x": 10.0, "y": 20.0, "z": 30.5})
    asyncio.run(flex.setup())
    try:
      head = _head(flex)

      position = asyncio.run(head.position())

      self.assertEqual(position, Coordinate(10.0, 20.0, 30.5))
      save_cmds = _cmds(api, "savePosition")
      self.assertEqual(len(save_cmds), 1)
      self.assertEqual(save_cmds[0].args[2], {"pipetteId": head.pipette_id})
    finally:
      asyncio.run(flex.stop())


class TestHeadMoveTo(unittest.TestCase):
  """move_to fills unspecified axes from the current position and sends ONE
  moveToCoordinates command. No tip is mounted in any of these tests: jogging
  is for teaching/recovery and must not require one.
  """

  def test_partial_axes_merge_saved_with_given(self):
    flex, api = _flex_with_gripper(saved_position={"x": 10.0, "y": 20.0, "z": 30.0})
    asyncio.run(flex.setup())
    try:
      head = _head(flex)

      asyncio.run(head.move_to(x=50.0))

      self.assertEqual(len(_cmds(api, "savePosition")), 1)
      move_cmds = _cmds(api, "moveToCoordinates")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(
        move_cmds[0].args[2],
        {
          "pipetteId": head.pipette_id,
          "coordinates": {"x": 50.0, "y": 20.0, "z": 30.0},
          # Default minimumZHeight is now the computed tip-safe plane (tallest
          # labware top + arc margin), not a hardcoded 120.0 magic number.
          "minimumZHeight": traversal_z(flex.deck),
        },
      )
    finally:
      asyncio.run(flex.stop())

  def test_all_axes_given_skips_position_read(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))

      self.assertEqual(len(_cmds(api, "savePosition")), 0)
      move_cmds = _cmds(api, "moveToCoordinates")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(move_cmds[0].args[2]["coordinates"], {"x": 1.0, "y": 2.0, "z": 3.0})
    finally:
      asyncio.run(flex.stop())

  def test_minimum_z_height_override(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0, minimum_z_height=35.0))

      move_cmds = _cmds(api, "moveToCoordinates")
      self.assertEqual(move_cmds[0].args[2]["minimumZHeight"], 35.0)
    finally:
      asyncio.run(flex.stop())

  def test_speed_passthrough(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0, speed=40.0))

      move_cmds = _cmds(api, "moveToCoordinates")
      self.assertEqual(move_cmds[0].args[2]["speed"], 40.0)
    finally:
      asyncio.run(flex.stop())

  def test_speed_omitted_by_default(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))

      move_cmds = _cmds(api, "moveToCoordinates")
      self.assertNotIn("speed", move_cmds[0].args[2])
    finally:
      asyncio.run(flex.stop())

  def test_no_axes_raises_before_any_wire_command(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(ValueError):
        asyncio.run(_head(flex).move_to())

      self.assertEqual(len(_cmds(api, "savePosition")), 0)
      self.assertEqual(len(_cmds(api, "moveToCoordinates")), 0)
    finally:
      asyncio.run(flex.stop())


class TestGripperMoveTo(unittest.TestCase):
  """Gripper move_to sends robot/moveTo with the extension mount."""

  def test_exact_wire_params(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).move_to(100.0, 50.0, 75.5))

      move_cmds = _cmds(api, "robot/moveTo")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(
        move_cmds[0].args[2],
        {"mount": "extension", "destination": {"x": 100.0, "y": 50.0, "z": 75.5}},
      )
    finally:
      asyncio.run(flex.stop())

  def test_speed_passthrough(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).move_to(1.0, 2.0, 3.0, speed=25.0))

      move_cmds = _cmds(api, "robot/moveTo")
      self.assertEqual(move_cmds[0].args[2]["speed"], 25.0)
    finally:
      asyncio.run(flex.stop())


class TestGripperJaw(unittest.TestCase):
  """grip() validates force before the wire; open_jaw() homes the jaw open."""

  def test_grip_without_force_sends_empty_params(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).grip())

      close_cmds = _cmds(api, "robot/closeGripperJaw")
      self.assertEqual(len(close_cmds), 1)
      self.assertEqual(close_cmds[0].args[2], {})
    finally:
      asyncio.run(flex.stop())

  def test_grip_force_boundaries_accepted(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)

      asyncio.run(gripper.grip(force=2.0))
      asyncio.run(gripper.grip(force=30.0))

      close_cmds = _cmds(api, "robot/closeGripperJaw")
      self.assertEqual(len(close_cmds), 2)
      self.assertEqual(close_cmds[0].args[2], {"force": 2.0})
      self.assertEqual(close_cmds[1].args[2], {"force": 30.0})
    finally:
      asyncio.run(flex.stop())

  def test_grip_force_out_of_range_raises_before_any_wire_command(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)

      for force in (1.9, 30.1, 0.0, -5.0):
        with self.assertRaises(OpentronsError):
          asyncio.run(gripper.grip(force=force))

      self.assertEqual(len(_cmds(api, "robot/closeGripperJaw")), 0)
    finally:
      asyncio.run(flex.stop())

  def test_open_jaw_sends_open_gripper_jaw(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())

      open_cmds = _cmds(api, "robot/openGripperJaw")
      self.assertEqual(len(open_cmds), 1)
      self.assertEqual(open_cmds[0].args[2], {})
    finally:
      asyncio.run(flex.stop())


class TestRobotCommandsVersionGate(unittest.TestCase):
  """robot/* commands require robot software 8.2.0+; dev builds and offline
  stand-ins (non-release version strings) are exempt. Head motion
  (savePosition/moveToCoordinates) is NOT gated -- it predates the robot/*
  family.
  """

  def _assert_no_robot_commands(self, api: AsyncMock) -> None:
    robot_cmds = [c for c in api.submit_command.await_args_list if c.args[1].startswith("robot/")]
    self.assertEqual(len(robot_cmds), 0, "no robot/* wire command may be sent")

  def test_old_release_raises_and_sends_no_robot_commands(self):
    flex, api = _flex_with_version("8.1.0")
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)

      with self.assertRaises(OpentronsError) as ctx:
        asyncio.run(gripper.move_to(1.0, 2.0, 3.0))
      self.assertIn("8.2.0", str(ctx.exception))
      with self.assertRaises(OpentronsError):
        asyncio.run(gripper.grip())
      with self.assertRaises(OpentronsError):
        asyncio.run(gripper.open_jaw())

      self._assert_no_robot_commands(api)
    finally:
      asyncio.run(flex.stop())

  def test_minimum_release_passes(self):
    flex, api = _flex_with_version("8.2.0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_double_digit_major_passes(self):
    # A lexicographic comparison would put "10.0.0" below "8.2.0".
    flex, api = _flex_with_version("10.0.0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_two_part_version_passes(self):
    # "8.2" pads to (8, 2, 0), equal to the minimum, not below it.
    flex, api = _flex_with_version("8.2")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_patch_release_below_minimum_rejected(self):
    flex, api = _flex_with_version("8.1.9")
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(OpentronsError):
        asyncio.run(_gripper(flex).open_jaw())
      self._assert_no_robot_commands(api)
    finally:
      asyncio.run(flex.stop())

  def test_unparseable_version_rejected(self):
    # A version the gate cannot parse must raise, not silently pass.
    flex, api = _flex_with_version("unknown")
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(OpentronsError) as ctx:
        asyncio.run(_gripper(flex).open_jaw())
      self.assertIn("unknown", str(ctx.exception))
      self._assert_no_robot_commands(api)
    finally:
      asyncio.run(flex.stop())

  def test_dev_build_passes(self):
    flex, api = _flex_with_version("0.0.0.dev0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_dev_build_cut_off_a_too_old_tag_is_still_gated(self):
    # An untagged build reports 0.0.0.dev*; a build cut off a release tag
    # reports that tag plus a dev suffix, and is as old as the tag says.
    flex, api = _flex_with_version("8.1.0.dev5")
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(OpentronsError) as ctx:
        asyncio.run(_gripper(flex).open_jaw())
      self.assertIn("8.2.0", str(ctx.exception))
      self._assert_no_robot_commands(api)
    finally:
      asyncio.run(flex.stop())

  def test_dev_build_cut_off_a_new_enough_tag_passes(self):
    flex, api = _flex_with_version("8.2.0.dev3")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_offline_version_sentinel_passes(self):
    flex, api = _flex_with_gripper()  # /health reports "dry-run"
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_head_motion_is_not_gated(self):
    flex, api = _flex_with_version("8.1.0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))
      self.assertEqual(len(_cmds(api, "moveToCoordinates")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_unknown_version_raises(self):
    with self.assertRaises(OpentronsError):
      _require_robot_commands("robot/moveTo", None)


class TestUntestedHardwareWarnings(unittest.TestCase):
  """Hardware-verification coverage is op-scoped per head class: an op in the
  class's verified set never warns; every other op logs the one-time
  untested-hardware notice, naming the op. The gripper carries the same
  mechanism with all five of its ops verified, so an op added later is
  untested by default rather than silently unnoticed."""

  def setUp(self):
    set_tip_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)

  def _flex_head8(self) -> Tuple[Flex, FlexHead8]:
    api = make_api(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")])
    flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
    asyncio.run(flex.setup())
    head = flex.left
    assert isinstance(head, FlexHead8)
    return flex, head

  def test_head8_verified_pickup_does_not_warn(self):
    flex, head = self._flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")
      with self.assertRaises(AssertionError):
        with self.assertLogs("pylabrobot.opentrons.flex.flex_head", level="WARNING"):
          asyncio.run(head.pick_up_tips(rack, column=0))
    finally:
      asyncio.run(flex.stop())

  def test_every_gripper_op_is_in_the_gripper_s_verified_set(self):
    """The gripper's notice can never fire today, and that is the point: the set
    is full rather than the mechanism deleted, so the next op added warns."""
    ops = {"grip", "move_labware", "move_to", "open_jaw", "ungrip"}
    self.assertEqual(FlexGripper._HARDWARE_VERIFIED_OPS, frozenset(ops))

  def test_an_op_outside_the_gripper_s_verified_set_still_warns(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    self.addCleanup(lambda: asyncio.run(flex.stop()))
    gripper = FlexGripper(flex, gripper_model="gripperV1")

    with self.assertLogs("pylabrobot.opentrons.flex.flex_gripper", level="WARNING") as logged:
      gripper._warn_untested_hardware("an_op_added_later")

    self.assertIn("an_op_added_later", logged.output[0])

  def test_each_unverified_head_op_warns_not_just_the_first(self):
    """Unverified operations warn once each, including after verified operations."""
    flex, head = self._flex_head8()
    try:
      with self.assertLogs("pylabrobot.opentrons.flex.flex_head", level="WARNING") as log_ctx:
        head._warn_untested_hardware("another_op_added_later")
        head._warn_untested_hardware("an_op_added_later")
        head._warn_untested_hardware("another_op_added_later")
      self.assertEqual(len(log_ctx.output), 2)
      self.assertTrue(any("FlexHead8.another_op_added_later" in msg for msg in log_ctx.output))
      self.assertTrue(any("FlexHead8.an_op_added_later" in msg for msg in log_ctx.output))
      self.assertTrue(any("not yet verified" in msg.lower() for msg in log_ctx.output))
    finally:
      asyncio.run(flex.stop())

  def test_head8_verified_motion_and_priming_do_not_warn(self):
    """Hardware-tested motion and priming no longer emit the unverified notice."""
    flex, head = self._flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "D1")
      asyncio.run(head.pick_up_tips(rack, column=0))
      with self.assertNoLogs("pylabrobot.opentrons.flex.flex_head", level="WARNING"):
        asyncio.run(head.position())
        asyncio.run(head.move_to(x=100, y=100, z=109))
        asyncio.run(head.move_relative("z", 1))
        asyncio.run(head.prepare_to_aspirate())
        asyncio.run(head.move_to_addressable_area("movableTrashA3", stay_at_max_height=True))
    finally:
      asyncio.run(flex.stop())

  def test_in_place_ops_ran_on_the_eight_head_so_they_stay_quiet(self):
    # These ran on the robot under an 8-nozzle layout.
    flex, head = self._flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")
      asyncio.run(head.pick_up_tips(rack, column=0))
      with self.assertRaises(AssertionError):
        with self.assertLogs("pylabrobot.opentrons.flex.flex_head", level="WARNING"):
          asyncio.run(head.blow_out())
    finally:
      asyncio.run(flex.stop())

  def test_head1_hardware_verified_ops_do_not_warn(self):
    """FlexHead1's ops were confirmed on a p50 single channel, so they stay quiet."""
    api = make_api(pipettes=[("p50_single_flex", 1, 1.0, 50.0, "left")])
    flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
    asyncio.run(flex.setup())
    try:
      head = flex.left
      assert head is not None
      with self.assertRaises(AssertionError):
        with self.assertLogs("pylabrobot.opentrons.flex.flex_head", level="WARNING"):
          asyncio.run(head.position())
    finally:
      asyncio.run(flex.stop())

  def test_head8_tip_presence_read_ran_on_hardware_so_it_stays_quiet(self):
    # The 8-head's tip-presence sensor was read on the robot.
    flex, head = self._flex_head8()
    try:
      with self.assertRaises(AssertionError):
        with self.assertLogs("pylabrobot.opentrons.flex.flex_head", level="WARNING"):
          asyncio.run(head.get_tip_presence())
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()


class TestMoveToWell(unittest.TestCase):
  """move_to_well names the well and lets the robot resolve where that is."""

  def _flex_with_plate(self):
    flex, api = _flex_with_gripper()
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    flex.deck.assign_child_at_slot(plate, "C1")
    return flex, api, plate

  def test_a_tip_spot_is_a_valid_target(self):
    """Looking at where a pickup would descend, before committing to it. The
    robot addresses a tip rack's wells by the same names a plate's use."""
    flex, api = _flex_with_gripper()
    rack = flex_96_tiprack_50ul(name="tips")
    flex.deck.assign_child_at_slot(rack, "C1")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to_well(rack.get_item("A1"), offset=Coordinate(0, 0, 20)))

      (cmd,) = _cmds(api, "moveToWell")
      self.assertEqual(cmd.args[2]["wellName"], "A1")
      self.assertEqual(cmd.args[2]["labwareId"], "labware")
      self.assertEqual(cmd.args[2]["wellLocation"]["offset"]["z"], 20)
    finally:
      asyncio.run(flex.stop())

  def test_names_the_well_and_defaults_to_the_top_origin(self):
    flex, api, plate = self._flex_with_plate()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to_well(plate.get_item("D2")))

      (cmd,) = _cmds(api, "moveToWell")
      self.assertEqual(cmd.args[2]["wellName"], "D2")
      self.assertEqual(
        cmd.args[2]["wellLocation"],
        {"origin": "top", "offset": {"x": 0, "y": 0, "z": 0}},
      )
      self.assertNotIn("coordinates", cmd.args[2])
    finally:
      asyncio.run(flex.stop())

  def test_offset_above_the_well_rides_the_top_origin(self):
    """'10 mm above the D2 well' is an offset from the top, not a coordinate."""
    flex, api, plate = self._flex_with_plate()
    asyncio.run(flex.setup())
    try:
      asyncio.run(
        _head(flex).move_to_well(plate.get_item("D2"), offset=Coordinate(0, 0, 10), speed=50.0)
      )

      (cmd,) = _cmds(api, "moveToWell")
      self.assertEqual(cmd.args[2]["wellLocation"]["origin"], "top")
      self.assertEqual(cmd.args[2]["wellLocation"]["offset"]["z"], 10)
      self.assertEqual(cmd.args[2]["speed"], 50.0)
    finally:
      asyncio.run(flex.stop())

  def test_unknown_origin_is_refused_before_any_wire_command(self):
    flex, api, plate = self._flex_with_plate()
    asyncio.run(flex.setup())
    try:
      with self.assertRaisesRegex(ValueError, "origin must be one of"):
        asyncio.run(_head(flex).move_to_well(plate.get_item("A1"), origin="sideways"))
      self.assertEqual(_cmds(api, "moveToWell"), [])
    finally:
      asyncio.run(flex.stop())

  def test_no_mounted_tip_required(self):
    """Jogging to a well is for teaching and recovery, so it must not need a tip."""
    flex, api, plate = self._flex_with_plate()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to_well(plate.get_item("A1")))
      self.assertEqual(len(_cmds(api, "moveToWell")), 1)
    finally:
      asyncio.run(flex.stop())


class TestMoveRelative(unittest.TestCase):
  """move_relative jogs one axis without reading the position first."""

  def test_sends_axis_and_distance_only(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_relative("z", -5.0))

      (cmd,) = _cmds(api, "moveRelative")
      self.assertEqual(cmd.args[2]["axis"], "z")
      self.assertEqual(cmd.args[2]["distance"], -5.0)
      self.assertEqual(_cmds(api, "savePosition"), [])
    finally:
      asyncio.run(flex.stop())

  def test_unknown_axis_is_refused_before_any_wire_command(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      with self.assertRaisesRegex(ValueError, "axis must be one of"):
        asyncio.run(_head(flex).move_relative("w", 1.0))
      self.assertEqual(_cmds(api, "moveRelative"), [])
    finally:
      asyncio.run(flex.stop())


class TestMoveToAddressableArea(unittest.TestCase):
  """move_to_addressable_area targets a deck fixture by name."""

  def test_names_the_area_and_carries_the_offset(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(
        _head(flex).move_to_addressable_area("movableTrashA3", offset=Coordinate(0, 0, 5))
      )

      (cmd,) = _cmds(api, "moveToAddressableArea")
      self.assertEqual(cmd.args[2]["addressableAreaName"], "movableTrashA3")
      self.assertEqual(cmd.args[2]["offset"], {"x": 0, "y": 0, "z": 5})
      self.assertFalse(cmd.args[2]["stayAtHighestPossibleZ"])
    finally:
      asyncio.run(flex.stop())


class TestSendCommandEscapeHatch(unittest.TestCase):
  """send_command reaches commands the driver wraps no method around."""

  def test_passes_command_type_and_params_through_untouched(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      params = {"moduleId": "abc", "celsius": 37.0}
      asyncio.run(flex.send_command("heaterShaker/setTargetTemperature", params))

      (cmd,) = _cmds(api, "heaterShaker/setTargetTemperature")
      self.assertEqual(cmd.args[2], params)
    finally:
      asyncio.run(flex.stop())

  def test_defaults_params_to_an_empty_payload(self):
    flex, api = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(flex.send_command("unsafe/engageAxes"))
      (cmd,) = _cmds(api, "unsafe/engageAxes")
      self.assertEqual(cmd.args[2], {})
    finally:
      asyncio.run(flex.stop())


class TestSyncTipsToRobot(unittest.TestCase):
  """sync_tips_to_robot pushes PyLabRobot's tip layout onto the robot."""

  def test_splits_present_and_absent_into_one_command_each(self):
    flex, api = _flex_with_gripper()
    rack = flex_96_tiprack_50ul(name="tips")
    flex.deck.assign_child_at_slot(rack, "C1")
    asyncio.run(flex.setup())
    try:
      rack.set_tip_state({spot.get_identifier(): False for spot in rack.get_all_items()})
      rack.set_tip_state({"A1": True, "B1": True})

      asyncio.run(flex.sync_tips_to_robot(rack))

      cmds = _cmds(api, "setTipState")
      by_state = {c.args[2]["tipWellState"]: c.args[2]["wellNames"] for c in cmds}
      self.assertEqual(by_state["clean"], ["A1", "B1"])
      self.assertEqual(len(by_state["empty"]), 94)
    finally:
      asyncio.run(flex.stop())

  def test_a_uniform_rack_sends_only_the_state_it_has(self):
    flex, api = _flex_with_gripper()
    rack = flex_96_tiprack_50ul(name="tips")
    flex.deck.assign_child_at_slot(rack, "C1")
    asyncio.run(flex.setup())
    try:
      asyncio.run(flex.sync_tips_to_robot(rack))

      states = {c.args[2]["tipWellState"] for c in _cmds(api, "setTipState")}
      self.assertEqual(states, {"clean"})
    finally:
      asyncio.run(flex.stop())
