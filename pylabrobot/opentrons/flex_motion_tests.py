"""Tests for the Flex direct-motion surface: head jog + position read
(``_FlexHead.position``/``_FlexHead.move_to``) and gripper motion + jaw
control (``FlexGripper.move_to``/``grip``/``open_jaw``).

Drives ``OpentronsFlex.setup()`` with an injected ``ChatterboxTransport`` and
asserts the exact wire commands: ``savePosition`` reads, ``moveToCoordinates``
axis merging and ``minimumZHeight``/``speed`` handling, the ``robot/moveTo``
extension-mount params, jaw force validation before any wire command, and the
robot-software version gate on the robot/* command family.
"""

import asyncio
import unittest
from typing import Any, Dict, List, Tuple

from pylabrobot.opentrons.checks import traversal_z
from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_gripper import FlexGripper, _require_robot_commands
from pylabrobot.opentrons.flex_head import FlexHead8, _FlexHead
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import ChatterboxTransport
from pylabrobot.resources import set_tip_tracking
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_plates import corning_96_wellplate_360ul_flat
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul


def _flex_with_gripper(**transport_kwargs) -> Tuple[OpentronsFlex, ChatterboxTransport]:
  """An ``OpentronsFlex`` with a single-channel right-mount pipette and a
  gripper, returning the transport too so a test can inspect recorded
  commands. ``transport_kwargs`` are forwarded to ``ChatterboxTransport``.
  """
  transport = ChatterboxTransport(
    pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")],
    gripper=True,
    **transport_kwargs,
  )
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  return flex, transport


def _head(flex: OpentronsFlex) -> _FlexHead:
  head = flex.right
  assert head is not None
  return head


def _gripper(flex: OpentronsFlex) -> FlexGripper:
  gripper = flex.gripper
  assert gripper is not None
  return gripper


def _cmds(transport: ChatterboxTransport, command_type: str) -> List[Dict[str, Any]]:
  return [c for c in transport.commands if c["commandType"] == command_type]


def _flex_with_version(api_version: str) -> Tuple[OpentronsFlex, ChatterboxTransport]:
  """A gripper-equipped Flex whose ``/health`` reports ``api_version``, so a
  test can drive the robot/* version gate."""
  return _flex_with_gripper(api_version=api_version)


class TestHeadPosition(unittest.TestCase):
  """position() reads the head's pose from a savePosition command result."""

  def test_position_reads_save_position_result(self):
    flex, transport = _flex_with_gripper(saved_position={"x": 10.0, "y": 20.0, "z": 30.5})
    asyncio.run(flex.setup())
    try:
      head = _head(flex)

      position = asyncio.run(head.position())

      self.assertEqual(position, Coordinate(10.0, 20.0, 30.5))
      save_cmds = _cmds(transport, "savePosition")
      self.assertEqual(len(save_cmds), 1)
      self.assertEqual(save_cmds[0]["params"], {"pipetteId": head.pipette_id})
    finally:
      asyncio.run(flex.stop())


class TestHeadMoveTo(unittest.TestCase):
  """move_to fills unspecified axes from the current position and sends ONE
  moveToCoordinates command. No tip is mounted in any of these tests: jogging
  is for teaching/recovery and must not require one.
  """

  def test_partial_axes_merge_saved_with_given(self):
    flex, transport = _flex_with_gripper(saved_position={"x": 10.0, "y": 20.0, "z": 30.0})
    asyncio.run(flex.setup())
    try:
      head = _head(flex)

      asyncio.run(head.move_to(x=50.0))

      self.assertEqual(len(_cmds(transport, "savePosition")), 1)
      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(
        move_cmds[0]["params"],
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
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))

      self.assertEqual(len(_cmds(transport, "savePosition")), 0)
      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(move_cmds[0]["params"]["coordinates"], {"x": 1.0, "y": 2.0, "z": 3.0})
    finally:
      asyncio.run(flex.stop())

  def test_minimum_z_height_override(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0, minimum_z_height=35.0))

      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertEqual(move_cmds[0]["params"]["minimumZHeight"], 35.0)
    finally:
      asyncio.run(flex.stop())

  def test_speed_passthrough(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0, speed=40.0))

      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertEqual(move_cmds[0]["params"]["speed"], 40.0)
    finally:
      asyncio.run(flex.stop())

  def test_speed_omitted_by_default(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))

      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertNotIn("speed", move_cmds[0]["params"])
    finally:
      asyncio.run(flex.stop())

  def test_no_axes_raises_before_any_wire_command(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(ValueError):
        asyncio.run(_head(flex).move_to())

      self.assertEqual(len(_cmds(transport, "savePosition")), 0)
      self.assertEqual(len(_cmds(transport, "moveToCoordinates")), 0)
    finally:
      asyncio.run(flex.stop())


class TestGripperMoveTo(unittest.TestCase):
  """Gripper move_to sends robot/moveTo with the extension mount."""

  def test_exact_wire_params(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).move_to(100.0, 50.0, 75.5))

      move_cmds = _cmds(transport, "robot/moveTo")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(
        move_cmds[0]["params"],
        {"mount": "extension", "destination": {"x": 100.0, "y": 50.0, "z": 75.5}},
      )
    finally:
      asyncio.run(flex.stop())

  def test_speed_passthrough(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).move_to(1.0, 2.0, 3.0, speed=25.0))

      move_cmds = _cmds(transport, "robot/moveTo")
      self.assertEqual(move_cmds[0]["params"]["speed"], 25.0)
    finally:
      asyncio.run(flex.stop())


class TestGripperJaw(unittest.TestCase):
  """grip() validates force before the wire; open_jaw() homes the jaw open."""

  def test_grip_without_force_sends_empty_params(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).grip())

      close_cmds = _cmds(transport, "robot/closeGripperJaw")
      self.assertEqual(len(close_cmds), 1)
      self.assertEqual(close_cmds[0]["params"], {})
    finally:
      asyncio.run(flex.stop())

  def test_grip_force_boundaries_accepted(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)

      asyncio.run(gripper.grip(force=2.0))
      asyncio.run(gripper.grip(force=30.0))

      close_cmds = _cmds(transport, "robot/closeGripperJaw")
      self.assertEqual(len(close_cmds), 2)
      self.assertEqual(close_cmds[0]["params"], {"force": 2.0})
      self.assertEqual(close_cmds[1]["params"], {"force": 30.0})
    finally:
      asyncio.run(flex.stop())

  def test_grip_force_out_of_range_raises_before_any_wire_command(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)

      for force in (1.9, 30.1, 0.0, -5.0):
        with self.assertRaises(OpentronsError):
          asyncio.run(gripper.grip(force=force))

      self.assertEqual(len(_cmds(transport, "robot/closeGripperJaw")), 0)
    finally:
      asyncio.run(flex.stop())

  def test_open_jaw_sends_open_gripper_jaw(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())

      open_cmds = _cmds(transport, "robot/openGripperJaw")
      self.assertEqual(len(open_cmds), 1)
      self.assertEqual(open_cmds[0]["params"], {})
    finally:
      asyncio.run(flex.stop())


class TestRobotCommandsVersionGate(unittest.TestCase):
  """robot/* commands require robot software 8.2.0+; dev builds and offline
  stand-ins (non-release version strings) are exempt. Head motion
  (savePosition/moveToCoordinates) is NOT gated -- it predates the robot/*
  family.
  """

  def _assert_no_robot_commands(self, transport: ChatterboxTransport) -> None:
    robot_cmds = [c for c in transport.commands if c["commandType"].startswith("robot/")]
    self.assertEqual(len(robot_cmds), 0, "no robot/* wire command may be sent")

  def test_old_release_raises_and_sends_no_robot_commands(self):
    flex, transport = _flex_with_version("8.1.0")
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

      self._assert_no_robot_commands(transport)
    finally:
      asyncio.run(flex.stop())

  def test_minimum_release_passes(self):
    flex, transport = _flex_with_version("8.2.0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_double_digit_major_passes(self):
    # A lexicographic comparison would put "10.0.0" below "8.2.0".
    flex, transport = _flex_with_version("10.0.0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_two_part_version_passes(self):
    # "8.2" pads to (8, 2, 0), equal to the minimum, not below it.
    flex, transport = _flex_with_version("8.2")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_patch_release_below_minimum_rejected(self):
    flex, transport = _flex_with_version("8.1.9")
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(OpentronsError):
        asyncio.run(_gripper(flex).open_jaw())
      self._assert_no_robot_commands(transport)
    finally:
      asyncio.run(flex.stop())

  def test_unparseable_version_rejected(self):
    # A version the gate cannot parse must raise, not silently pass.
    flex, transport = _flex_with_version("unknown")
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(OpentronsError) as ctx:
        asyncio.run(_gripper(flex).open_jaw())
      self.assertIn("unknown", str(ctx.exception))
      self._assert_no_robot_commands(transport)
    finally:
      asyncio.run(flex.stop())

  def test_dev_build_passes(self):
    flex, transport = _flex_with_version("0.0.0.dev0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_dev_build_cut_off_a_too_old_tag_is_still_gated(self):
    # An untagged build reports 0.0.0.dev*; a build cut off a release tag
    # reports that tag plus a dev suffix, and is as old as the tag says.
    flex, transport = _flex_with_version("8.1.0.dev5")
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(OpentronsError) as ctx:
        asyncio.run(_gripper(flex).open_jaw())
      self.assertIn("8.2.0", str(ctx.exception))
      self._assert_no_robot_commands(transport)
    finally:
      asyncio.run(flex.stop())

  def test_dev_build_cut_off_a_new_enough_tag_passes(self):
    flex, transport = _flex_with_version("8.2.0.dev3")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_default_chatterbox_passes(self):
    flex, transport = _flex_with_gripper()  # /health reports "dry-run"
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_head_motion_is_not_gated(self):
    flex, transport = _flex_with_version("8.1.0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))
      self.assertEqual(len(_cmds(transport, "moveToCoordinates")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_unknown_version_raises(self):
    with self.assertRaises(OpentronsError):
      _require_robot_commands("robot/moveTo", None)


class TestUntestedHardwareWarnings(unittest.TestCase):
  """Hardware-verification coverage is op-scoped: FlexHead8's verified
  column-pickup lineage never warns; every other head or gripper op logs the
  one-time untested-hardware notice, naming the op."""

  def setUp(self):
    set_tip_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)

  def _flex_head8(self) -> Tuple[OpentronsFlex, FlexHead8]:
    transport = ChatterboxTransport(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")])
    flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
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
        with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING"):
          asyncio.run(head.pick_up_tips(rack, column=0))
    finally:
      asyncio.run(flex.stop())

  def test_head8_op_outside_verified_lineage_warns_once(self):
    flex, head = self._flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")
      asyncio.run(head.pick_up_tips(rack, column=0))

      with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING") as log_ctx:
        asyncio.run(head.blow_out())
      self.assertTrue(any("FlexHead8.blow_out" in msg for msg in log_ctx.output))
      self.assertTrue(any("not yet verified" in msg.lower() for msg in log_ctx.output))

      # Only the FIRST unverified op on an instance logs.
      with self.assertRaises(AssertionError):
        with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING"):
          asyncio.run(head.blow_out())
    finally:
      asyncio.run(flex.stop())

  def test_base_motion_ops_warn_on_unverified_heads(self):
    """A base-class op warns unless THIS head's verified set names it.

    Uses FlexHead8, whose verified lineage is column pickup only, so a motion
    op it never covered still warns.
    """
    flex, head = self._flex_head8()
    try:
      with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING") as log_ctx:
        asyncio.run(head.position())
      self.assertTrue(any("FlexHead8.position" in msg for msg in log_ctx.output))
    finally:
      asyncio.run(flex.stop())

  def test_head1_hardware_verified_ops_do_not_warn(self):
    """FlexHead1's ops were confirmed on a p50 single channel, so they stay quiet."""
    transport = ChatterboxTransport(pipettes=[("p50_single_flex", 1, 1.0, 50.0, "left")])
    flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
    asyncio.run(flex.setup())
    try:
      head = flex.left
      assert head is not None
      with self.assertRaises(AssertionError):
        with self.assertLogs("pylabrobot.opentrons.flex_head", level="WARNING"):
          asyncio.run(head.position())
    finally:
      asyncio.run(flex.stop())

  def test_gripper_ops_warn_once(self):
    flex, _transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)
      with self.assertLogs("pylabrobot.opentrons.flex_gripper", level="WARNING") as log_ctx:
        asyncio.run(gripper.move_to(1.0, 2.0, 3.0))
      self.assertTrue(any("FlexGripper.move_to" in msg for msg in log_ctx.output))

      with self.assertRaises(AssertionError):
        with self.assertLogs("pylabrobot.opentrons.flex_gripper", level="WARNING"):
          asyncio.run(gripper.open_jaw())
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()


class TestMoveToWell(unittest.TestCase):
  """move_to_well names the well and lets the robot resolve where that is."""

  def _flex_with_plate(self):
    flex, transport = _flex_with_gripper()
    plate = corning_96_wellplate_360ul_flat(name="plate")
    flex.deck.assign_child_at_slot(plate, "C1")
    return flex, transport, plate

  def test_a_tip_spot_is_a_valid_target(self):
    """Looking at where a pickup would descend, before committing to it. The
    robot addresses a tip rack's wells by the same names a plate's use."""
    flex, transport = _flex_with_gripper()
    rack = flex_96_tiprack_50ul(name="tips")
    flex.deck.assign_child_at_slot(rack, "C1")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to_well(rack.get_item("A1"), offset=Coordinate(0, 0, 20)))

      (cmd,) = _cmds(transport, "moveToWell")
      self.assertEqual(cmd["params"]["wellName"], "A1")
      self.assertEqual(cmd["params"]["labwareId"], transport.labware_ids["tips"])
      self.assertEqual(cmd["params"]["wellLocation"]["offset"]["z"], 20)
    finally:
      asyncio.run(flex.stop())

  def test_names_the_well_and_defaults_to_the_top_origin(self):
    flex, transport, plate = self._flex_with_plate()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to_well(plate.get_item("D2")))

      (cmd,) = _cmds(transport, "moveToWell")
      self.assertEqual(cmd["params"]["wellName"], "D2")
      self.assertEqual(
        cmd["params"]["wellLocation"],
        {"origin": "top", "offset": {"x": 0, "y": 0, "z": 0}},
      )
      self.assertNotIn("coordinates", cmd["params"])
    finally:
      asyncio.run(flex.stop())

  def test_offset_above_the_well_rides_the_top_origin(self):
    """'10 mm above the D2 well' is an offset from the top, not a coordinate."""
    flex, transport, plate = self._flex_with_plate()
    asyncio.run(flex.setup())
    try:
      asyncio.run(
        _head(flex).move_to_well(plate.get_item("D2"), offset=Coordinate(0, 0, 10), speed=50.0)
      )

      (cmd,) = _cmds(transport, "moveToWell")
      self.assertEqual(cmd["params"]["wellLocation"]["origin"], "top")
      self.assertEqual(cmd["params"]["wellLocation"]["offset"]["z"], 10)
      self.assertEqual(cmd["params"]["speed"], 50.0)
    finally:
      asyncio.run(flex.stop())

  def test_unknown_origin_is_refused_before_any_wire_command(self):
    flex, transport, plate = self._flex_with_plate()
    asyncio.run(flex.setup())
    try:
      with self.assertRaisesRegex(ValueError, "origin must be one of"):
        asyncio.run(_head(flex).move_to_well(plate.get_item("A1"), origin="sideways"))
      self.assertEqual(_cmds(transport, "moveToWell"), [])
    finally:
      asyncio.run(flex.stop())

  def test_no_mounted_tip_required(self):
    """Jogging to a well is for teaching and recovery, so it must not need a tip."""
    flex, transport, plate = self._flex_with_plate()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to_well(plate.get_item("A1")))
      self.assertEqual(len(_cmds(transport, "moveToWell")), 1)
    finally:
      asyncio.run(flex.stop())


class TestMoveRelative(unittest.TestCase):
  """move_relative jogs one axis without reading the position first."""

  def test_sends_axis_and_distance_only(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_relative("z", -5.0))

      (cmd,) = _cmds(transport, "moveRelative")
      self.assertEqual(cmd["params"]["axis"], "z")
      self.assertEqual(cmd["params"]["distance"], -5.0)
      self.assertEqual(_cmds(transport, "savePosition"), [])
    finally:
      asyncio.run(flex.stop())

  def test_unknown_axis_is_refused_before_any_wire_command(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      with self.assertRaisesRegex(ValueError, "axis must be one of"):
        asyncio.run(_head(flex).move_relative("w", 1.0))
      self.assertEqual(_cmds(transport, "moveRelative"), [])
    finally:
      asyncio.run(flex.stop())


class TestMoveToAddressableArea(unittest.TestCase):
  """move_to_addressable_area targets a deck fixture by name."""

  def test_names_the_area_and_carries_the_offset(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(
        _head(flex).move_to_addressable_area("movableTrashA3", offset=Coordinate(0, 0, 5))
      )

      (cmd,) = _cmds(transport, "moveToAddressableArea")
      self.assertEqual(cmd["params"]["addressableAreaName"], "movableTrashA3")
      self.assertEqual(cmd["params"]["offset"], {"x": 0, "y": 0, "z": 5})
      self.assertFalse(cmd["params"]["stayAtHighestPossibleZ"])
    finally:
      asyncio.run(flex.stop())


class TestSendCommandEscapeHatch(unittest.TestCase):
  """send_command reaches commands the driver wraps no method around."""

  def test_passes_command_type_and_params_through_untouched(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      params = {"moduleId": "abc", "celsius": 37.0}
      asyncio.run(flex.send_command("heaterShaker/setTargetTemperature", params))

      (cmd,) = _cmds(transport, "heaterShaker/setTargetTemperature")
      self.assertEqual(cmd["params"], params)
    finally:
      asyncio.run(flex.stop())

  def test_defaults_params_to_an_empty_payload(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(flex.send_command("unsafe/engageAxes"))
      (cmd,) = _cmds(transport, "unsafe/engageAxes")
      self.assertEqual(cmd["params"], {})
    finally:
      asyncio.run(flex.stop())


class TestSyncTipsToRobot(unittest.TestCase):
  """sync_tips_to_robot pushes PyLabRobot's tip layout onto the robot."""

  def test_splits_present_and_absent_into_one_command_each(self):
    flex, transport = _flex_with_gripper()
    rack = flex_96_tiprack_50ul(name="tips")
    flex.deck.assign_child_at_slot(rack, "C1")
    asyncio.run(flex.setup())
    try:
      rack.set_tip_state({spot.get_identifier(): False for spot in rack.get_all_items()})
      rack.set_tip_state({"A1": True, "B1": True})

      asyncio.run(flex.sync_tips_to_robot(rack))

      cmds = _cmds(transport, "setTipState")
      by_state = {c["params"]["tipWellState"]: c["params"]["wellNames"] for c in cmds}
      self.assertEqual(by_state["clean"], ["A1", "B1"])
      self.assertEqual(len(by_state["empty"]), 94)
    finally:
      asyncio.run(flex.stop())

  def test_a_uniform_rack_sends_only_the_state_it_has(self):
    flex, transport = _flex_with_gripper()
    rack = flex_96_tiprack_50ul(name="tips")
    flex.deck.assign_child_at_slot(rack, "C1")
    asyncio.run(flex.setup())
    try:
      asyncio.run(flex.sync_tips_to_robot(rack))

      states = {c["params"]["tipWellState"] for c in _cmds(transport, "setTipState")}
      self.assertEqual(states, {"clean"})
    finally:
      asyncio.run(flex.stop())
