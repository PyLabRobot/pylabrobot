"""Tests for the Flex direct-motion surface: head jog + position read
(``_FlexHead.position``/``_FlexHead.move_to``) and gripper motion + jaw
control (``FlexGripper.move_to``/``grip``/``open_jaw``).

Drives ``Flex.setup()`` with an injected ``AsyncMock`` and
asserts the exact wire commands: ``savePosition`` reads, ``moveToCoordinates``
axis merging and ``minimumZHeight``/``speed`` handling, the ``robot/moveTo``
extension-mount params, jaw force validation before any wire command, and the
robot-software version gate on the robot/* command family.
"""

import unittest
from typing import List, Tuple
from unittest.mock import AsyncMock, _Call

from pylabrobot.opentrons.flex.checks import traversal_z
from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.flex.flex import Flex
from pylabrobot.opentrons.flex.flex_gripper import FlexGripper, _require_robot_commands
from pylabrobot.opentrons.flex.flex_head import _FlexHead
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul


def _flex_with_gripper(
  test_case: unittest.IsolatedAsyncioTestCase, **api_kwargs
) -> Tuple[Flex, AsyncMock]:
  """Build a Flex with fixed API replies and return its mock for assertions."""
  api = make_api(
    pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")],
    gripper=True,
    **api_kwargs,
  )
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  test_case.addAsyncCleanup(flex.disconnect)
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


def _flex_with_version(
  test_case: unittest.IsolatedAsyncioTestCase, api_version: str
) -> Tuple[Flex, AsyncMock]:
  """A gripper-equipped Flex whose ``/health`` reports ``api_version``, so a
  test can drive the robot/* version gate."""
  return _flex_with_gripper(test_case, api_version=api_version)


class TestHeadPosition(unittest.IsolatedAsyncioTestCase):
  """position() reads the head's pose from a savePosition command result."""

  async def test_position_reads_save_position_result(self):
    flex, api = _flex_with_gripper(self, saved_position={"x": 10.0, "y": 20.0, "z": 30.5})
    await flex.setup()
    head = _head(flex)

    position = await head.position()

    self.assertEqual(position, Coordinate(10.0, 20.0, 30.5))
    save_cmds = _cmds(api, "savePosition")
    self.assertEqual(len(save_cmds), 1)
    self.assertEqual(save_cmds[0].args[2], {"pipetteId": head.pipette_id})


class TestHeadMoveTo(unittest.IsolatedAsyncioTestCase):
  """move_to fills unspecified axes from the current position and sends ONE
  moveToCoordinates command. No tip is mounted in any of these tests: jogging
  is for teaching/recovery and must not require one.
  """

  async def test_partial_axes_merge_saved_with_given(self):
    flex, api = _flex_with_gripper(self, saved_position={"x": 10.0, "y": 20.0, "z": 30.0})
    await flex.setup()
    head = _head(flex)

    await head.move_to(x=50.0)

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

  async def test_all_axes_given_skips_position_read(self):
    flex, api = _flex_with_gripper(self)
    await flex.setup()
    await _head(flex).move_to(x=1.0, y=2.0, z=3.0)

    self.assertEqual(len(_cmds(api, "savePosition")), 0)
    move_cmds = _cmds(api, "moveToCoordinates")
    self.assertEqual(len(move_cmds), 1)
    self.assertEqual(move_cmds[0].args[2]["coordinates"], {"x": 1.0, "y": 2.0, "z": 3.0})

  async def test_minimum_z_height_override(self):
    flex, api = _flex_with_gripper(self)
    await flex.setup()
    await _head(flex).move_to(x=1.0, y=2.0, z=3.0, minimum_z_height=35.0)

    move_cmds = _cmds(api, "moveToCoordinates")
    self.assertEqual(move_cmds[0].args[2]["minimumZHeight"], 35.0)

  async def test_speed_passthrough(self):
    flex, api = _flex_with_gripper(self)
    await flex.setup()
    await _head(flex).move_to(x=1.0, y=2.0, z=3.0, speed=40.0)

    move_cmds = _cmds(api, "moveToCoordinates")
    self.assertEqual(move_cmds[0].args[2]["speed"], 40.0)

  async def test_speed_omitted_by_default(self):
    flex, api = _flex_with_gripper(self)
    await flex.setup()
    await _head(flex).move_to(x=1.0, y=2.0, z=3.0)

    move_cmds = _cmds(api, "moveToCoordinates")
    self.assertNotIn("speed", move_cmds[0].args[2])

  async def test_no_axes_raises_before_any_wire_command(self):
    flex, api = _flex_with_gripper(self)
    await flex.setup()
    with self.assertRaises(ValueError):
      await _head(flex).move_to()

    self.assertEqual(len(_cmds(api, "savePosition")), 0)
    self.assertEqual(len(_cmds(api, "moveToCoordinates")), 0)


class TestGripperMoveTo(unittest.IsolatedAsyncioTestCase):
  """Gripper move_to sends robot/moveTo with the extension mount."""

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)
    await self.flex.setup()

  async def test_exact_wire_params(self):
    await _gripper(self.flex).move_to(100.0, 50.0, 75.5)

    move_cmds = _cmds(self.api, "robot/moveTo")
    self.assertEqual(len(move_cmds), 1)
    self.assertEqual(
      move_cmds[0].args[2],
      {"mount": "extension", "destination": {"x": 100.0, "y": 50.0, "z": 75.5}},
    )

  async def test_speed_passthrough(self):
    await _gripper(self.flex).move_to(1.0, 2.0, 3.0, speed=25.0)

    move_cmds = _cmds(self.api, "robot/moveTo")
    self.assertEqual(move_cmds[0].args[2]["speed"], 25.0)


class TestGripperJaw(unittest.IsolatedAsyncioTestCase):
  """grip() validates force before the wire; open_jaw() homes the jaw open."""

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)
    await self.flex.setup()

  async def test_grip_without_force_sends_empty_params(self):
    await _gripper(self.flex).grip()

    close_cmds = _cmds(self.api, "robot/closeGripperJaw")
    self.assertEqual(len(close_cmds), 1)
    self.assertEqual(close_cmds[0].args[2], {})

  async def test_grip_force_boundaries_accepted(self):
    gripper = _gripper(self.flex)

    await gripper.grip(force=2.0)
    await gripper.grip(force=30.0)

    close_cmds = _cmds(self.api, "robot/closeGripperJaw")
    self.assertEqual(len(close_cmds), 2)
    self.assertEqual(close_cmds[0].args[2], {"force": 2.0})
    self.assertEqual(close_cmds[1].args[2], {"force": 30.0})

  async def test_grip_force_out_of_range_raises_before_any_wire_command(self):
    gripper = _gripper(self.flex)

    for force in (1.9, 30.1, 0.0, -5.0):
      with self.assertRaises(OpentronsError):
        await gripper.grip(force=force)

    self.assertEqual(len(_cmds(self.api, "robot/closeGripperJaw")), 0)

  async def test_open_jaw_sends_open_gripper_jaw(self):
    await _gripper(self.flex).open_jaw()

    open_cmds = _cmds(self.api, "robot/openGripperJaw")
    self.assertEqual(len(open_cmds), 1)
    self.assertEqual(open_cmds[0].args[2], {})


class TestRobotCommandsVersionGate(unittest.IsolatedAsyncioTestCase):
  """robot/* commands require robot software 8.2.0+; dev builds and offline
  stand-ins (non-release version strings) are exempt. Head motion
  (savePosition/moveToCoordinates) is NOT gated -- it predates the robot/*
  family.
  """

  def _assert_no_robot_commands(self, api: AsyncMock) -> None:
    robot_cmds = [c for c in api.submit_command.await_args_list if c.args[1].startswith("robot/")]
    self.assertEqual(len(robot_cmds), 0, "no robot/* wire command may be sent")

  async def test_old_release_raises_and_sends_no_robot_commands(self):
    flex, api = _flex_with_version(self, "8.1.0")
    await flex.setup()
    gripper = _gripper(flex)

    with self.assertRaises(OpentronsError) as ctx:
      await gripper.move_to(1.0, 2.0, 3.0)
    self.assertIn("8.2.0", str(ctx.exception))
    with self.assertRaises(OpentronsError):
      await gripper.grip()
    with self.assertRaises(OpentronsError):
      await gripper.open_jaw()

    self._assert_no_robot_commands(api)

  async def test_minimum_release_passes(self):
    flex, api = _flex_with_version(self, "8.2.0")
    await flex.setup()
    await _gripper(flex).open_jaw()
    self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)

  async def test_double_digit_major_passes(self):
    # A lexicographic comparison would put "10.0.0" below "8.2.0".
    flex, api = _flex_with_version(self, "10.0.0")
    await flex.setup()
    await _gripper(flex).open_jaw()
    self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)

  async def test_two_part_version_passes(self):
    # "8.2" pads to (8, 2, 0), equal to the minimum, not below it.
    flex, api = _flex_with_version(self, "8.2")
    await flex.setup()
    await _gripper(flex).open_jaw()
    self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)

  async def test_patch_release_below_minimum_rejected(self):
    flex, api = _flex_with_version(self, "8.1.9")
    await flex.setup()
    with self.assertRaises(OpentronsError):
      await _gripper(flex).open_jaw()
    self._assert_no_robot_commands(api)

  async def test_unparseable_version_rejected(self):
    # A version the gate cannot parse must raise, not silently pass.
    flex, api = _flex_with_version(self, "unknown")
    await flex.setup()
    with self.assertRaises(OpentronsError) as ctx:
      await _gripper(flex).open_jaw()
    self.assertIn("unknown", str(ctx.exception))
    self._assert_no_robot_commands(api)

  async def test_dev_build_passes(self):
    flex, api = _flex_with_version(self, "0.0.0.dev0")
    await flex.setup()
    await _gripper(flex).open_jaw()
    self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)

  async def test_dev_build_cut_off_a_too_old_tag_is_still_gated(self):
    # An untagged build reports 0.0.0.dev*; a build cut off a release tag
    # reports that tag plus a dev suffix, and is as old as the tag says.
    flex, api = _flex_with_version(self, "8.1.0.dev5")
    await flex.setup()
    with self.assertRaises(OpentronsError) as ctx:
      await _gripper(flex).open_jaw()
    self.assertIn("8.2.0", str(ctx.exception))
    self._assert_no_robot_commands(api)

  async def test_dev_build_cut_off_a_new_enough_tag_passes(self):
    flex, api = _flex_with_version(self, "8.2.0.dev3")
    await flex.setup()
    await _gripper(flex).open_jaw()
    self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)

  async def test_offline_version_sentinel_passes(self):
    flex, api = _flex_with_gripper(self)  # /health reports "dry-run"
    await flex.setup()
    await _gripper(flex).open_jaw()
    self.assertEqual(len(_cmds(api, "robot/openGripperJaw")), 1)

  async def test_head_motion_is_not_gated(self):
    flex, api = _flex_with_version(self, "8.1.0")
    await flex.setup()
    await _head(flex).move_to(x=1.0, y=2.0, z=3.0)
    self.assertEqual(len(_cmds(api, "moveToCoordinates")), 1)

  def test_unknown_version_raises(self):
    with self.assertRaises(OpentronsError):
      _require_robot_commands("robot/moveTo", None)


if __name__ == "__main__":
  unittest.main()


class TestMoveToWell(unittest.IsolatedAsyncioTestCase):
  """move_to_well names the well and lets the robot resolve where that is."""

  def _flex_with_plate(self):
    flex, api = _flex_with_gripper(self)
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    flex.deck.assign_child_at_slot(plate, "C1")
    return flex, api, plate

  async def test_a_tip_spot_is_a_valid_target(self):
    """Looking at where a pickup would descend, before committing to it. The
    robot addresses a tip rack's wells by the same names a plate's use."""
    flex, api = _flex_with_gripper(self)
    rack = flex_96_tiprack_50ul(name="tips")
    flex.deck.assign_child_at_slot(rack, "C1")
    await flex.setup()
    await _head(flex).move_to_well(rack.get_item("A1"), offset=Coordinate(0, 0, 20))

    (cmd,) = _cmds(api, "moveToWell")
    self.assertEqual(cmd.args[2]["wellName"], "A1")
    self.assertEqual(cmd.args[2]["labwareId"], "labware")
    self.assertEqual(cmd.args[2]["wellLocation"]["offset"]["z"], 20)

  async def test_names_the_well_and_defaults_to_the_top_origin(self):
    flex, api, plate = self._flex_with_plate()
    await flex.setup()
    await _head(flex).move_to_well(plate.get_item("D2"))

    (cmd,) = _cmds(api, "moveToWell")
    self.assertEqual(cmd.args[2]["wellName"], "D2")
    self.assertEqual(
      cmd.args[2]["wellLocation"],
      {"origin": "top", "offset": {"x": 0, "y": 0, "z": 0}},
    )
    self.assertNotIn("coordinates", cmd.args[2])

  async def test_offset_above_the_well_rides_the_top_origin(self):
    """'10 mm above the D2 well' is an offset from the top, not a coordinate."""
    flex, api, plate = self._flex_with_plate()
    await flex.setup()
    await _head(flex).move_to_well(plate.get_item("D2"), offset=Coordinate(0, 0, 10), speed=50.0)

    (cmd,) = _cmds(api, "moveToWell")
    self.assertEqual(cmd.args[2]["wellLocation"]["origin"], "top")
    self.assertEqual(cmd.args[2]["wellLocation"]["offset"]["z"], 10)
    self.assertEqual(cmd.args[2]["speed"], 50.0)

  async def test_unknown_origin_is_refused_before_any_wire_command(self):
    flex, api, plate = self._flex_with_plate()
    await flex.setup()
    with self.assertRaisesRegex(ValueError, "origin must be one of"):
      await _head(flex).move_to_well(plate.get_item("A1"), origin="sideways")
    self.assertEqual(_cmds(api, "moveToWell"), [])

  async def test_no_mounted_tip_required(self):
    """Jogging to a well is for teaching and recovery, so it must not need a tip."""
    flex, api, plate = self._flex_with_plate()
    await flex.setup()
    await _head(flex).move_to_well(plate.get_item("A1"))
    self.assertEqual(len(_cmds(api, "moveToWell")), 1)


class TestMoveRelative(unittest.IsolatedAsyncioTestCase):
  """move_relative jogs one axis without reading the position first."""

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)
    await self.flex.setup()

  async def test_sends_axis_and_distance_only(self):
    await _head(self.flex).move_relative("z", -5.0)

    (cmd,) = _cmds(self.api, "moveRelative")
    self.assertEqual(cmd.args[2]["axis"], "z")
    self.assertEqual(cmd.args[2]["distance"], -5.0)
    self.assertEqual(_cmds(self.api, "savePosition"), [])

  async def test_unknown_axis_is_refused_before_any_wire_command(self):
    with self.assertRaisesRegex(ValueError, "axis must be one of"):
      await _head(self.flex).move_relative("w", 1.0)
    self.assertEqual(_cmds(self.api, "moveRelative"), [])


class TestMoveToAddressableArea(unittest.IsolatedAsyncioTestCase):
  """move_to_addressable_area targets a deck fixture by name."""

  async def test_names_the_area_and_carries_the_offset(self):
    flex, api = _flex_with_gripper(self)
    await flex.setup()
    await _head(flex).move_to_addressable_area("movableTrashA3", offset=Coordinate(0, 0, 5))

    (cmd,) = _cmds(api, "moveToAddressableArea")
    self.assertEqual(cmd.args[2]["addressableAreaName"], "movableTrashA3")
    self.assertEqual(cmd.args[2]["offset"], {"x": 0, "y": 0, "z": 5})
    self.assertFalse(cmd.args[2]["stayAtHighestPossibleZ"])


class TestSendCommandEscapeHatch(unittest.IsolatedAsyncioTestCase):
  """send_command reaches commands the driver wraps no method around."""

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)
    await self.flex.setup()

  async def test_passes_command_type_and_params_through_untouched(self):
    params = {"moduleId": "abc", "celsius": 37.0}
    await self.flex.send_command("heaterShaker/setTargetTemperature", params)

    (cmd,) = _cmds(self.api, "heaterShaker/setTargetTemperature")
    self.assertEqual(cmd.args[2], params)

  async def test_defaults_params_to_an_empty_payload(self):
    await self.flex.send_command("unsafe/engageAxes")
    (cmd,) = _cmds(self.api, "unsafe/engageAxes")
    self.assertEqual(cmd.args[2], {})


class TestSyncTipsToRobot(unittest.IsolatedAsyncioTestCase):
  """sync_tips_to_robot pushes PyLabRobot's tip layout onto the robot."""

  async def asyncSetUp(self):
    self.flex, self.api = _flex_with_gripper(self)

  async def test_splits_present_and_absent_into_one_command_each(self):
    rack = flex_96_tiprack_50ul(name="tips")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    await self.flex.setup()
    rack.set_tip_state({spot.get_identifier(): False for spot in rack.get_all_items()})
    rack.set_tip_state({"A1": True, "B1": True})

    await self.flex.sync_tips_to_robot(rack)

    cmds = _cmds(self.api, "setTipState")
    by_state = {c.args[2]["tipWellState"]: c.args[2]["wellNames"] for c in cmds}
    self.assertEqual(by_state["clean"], ["A1", "B1"])
    self.assertEqual(len(by_state["empty"]), 94)

  async def test_a_uniform_rack_sends_only_the_state_it_has(self):
    rack = flex_96_tiprack_50ul(name="tips")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    await self.flex.setup()
    await self.flex.sync_tips_to_robot(rack)

    states = {c.args[2]["tipWellState"] for c in _cmds(self.api, "setTipState")}
    self.assertEqual(states, {"clean"})
