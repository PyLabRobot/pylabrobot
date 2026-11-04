"""What every public `PreciseFlex` method sends, pinned command by command.

A fake controller answers at the socket, from replies a PF400 gave and filled-in values for the
rest. Numbers compare to within 1e-9; everything else must match exactly.
"""

import contextlib
import logging
import math
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple, Type, cast
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
import sniffio

from pylabrobot.brooks.precise_flex import (
  PreciseFlex,
  PreciseFlexArm,
  PreciseFlexAxis,
  PreciseFlexCartesianPose,
  PreciseFlexDriver,
  PreciseFlexError,
  PreciseFlexGripper,
  PreciseFlexRail,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.testing.concurrency import AnyioTestBase

# Replies a PF400 gave, from IO-level logs of a real arm; its serial left out.
_PF400_REPLIES: Dict[str, str] = {
  "mode 0": "0",
  "hp 1 20": "0",
  "attach 1": "0",
  "freemode -1": "0",
  "pd 16078": "0 1.5, -93, 12, -960, 69",
  "pd 16077": "0 401.5, 93, 348, 960, 134",
  "pd 2003": "0 47",
  "pd 2002": "0 PreciseFlex 400SX",
  "version": (
    "0 TCP Command Server 3.0D4 10-25-2024, Load-Save Module 3.0B2 12-04-2020, "
    "PARobot Module 3.0D4 10-25-2024, SSGrip Module 3.0D4 10-25-2024, "
    "PARobot Auto Center Module 3.0D3 10-23-2024, IntelliGuide 1.0 05-22-2024"
  ),
  "VToolProperty System CameraCount": "2",  # the relay answers the bare value
  "pd 2700": "0 500, 360, 720, 720, 400",
  "pd 2702": "0 3500, 600, 920, 4000, 10000",
  "pd 2704": "0 150",
  "pd 2705": "0 300",
  "pd 2706": "0 300",
  "pd 16050": "0 0, 302, 289, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0",
  "pd 16051": "0 0, 0, 162, 0, 0, 0",
  "pd 100": "0 Brooks Automation",
  "pd 101": "0 G5400B.2",
  "pd 102": "0 CPU 10-105.2-25, FPGA 6.1 01-23-2024, JMP 0, PWR -1, RMII",
  "pd 103": "0 GPL 5.1D4, Apr 25 2025, Release, ECM",
  "pd 110": "0 000000-00000000",
  "pd 116": "0 12, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0",
  "pd 2000": "0 5",
  "pd 2004": "0 0",
  "pd 16076": "0 0, -95, 10, -962, 69",
  "pd 16075": "0 402, 95, 350, 962, 135",
  "pd 2701": "0 500, 500, 400",
  "pd 2703": "0 1000, 2500, 10000",
  "pd 234": "0 21",
  "waitForEom": "0",
  "home": "0",
  "freemode 1": "0",
  "freemode 2": "0",
  "freemode 3": "0",
  "freemode 4": "0",
  "Speed 1 20": "0",
  "Speed 1 60": "0",
  "IsFullyClosed": "0 -1",
  # Something held, as a grasp answered on the arm.
  "GraspPlate 70.0 50.0 10.0": "0 -1",
  "attach 0": "0",
  "hp 0": "0",
  "Speed 1": "0 1 20",
  "Speed 1 20.0": "0",
  "Speed 1 50.0": "0",
  "pd 2800": "0 0",
  "StereoParam 1 1": "0 unknown_process unknown_tool 150 0.5 4 2 3 63 2 1000 ",
  "destJ 1": "0 269.913 81.507 218.952 64.548 70.691",
  "halt": "0",
  "sysState": "0 7",
  "MoveOneAxis 1 2.5 1": "0",
}

# Likewise the arm's own, read in one sweep as it stood parked with its jaws closed.
_REPLIES: Dict[str, str] = {
  **_PF400_REPLIES,
  "wherej": "0 301.11 0.018 179.925 -180.644 70.685",
  "wherec": "0 174.988 -1.602 301.11 -0.701 90 180 1",
  "mode": "0 0",
  "hp": "0 1",
  "state": "0 1",
  "nop": "0",
  "mspeed": "0 50",
  "payload": "0 25",
  "base": "0 0 0 0 0",
  "tool": "0 0 0 162 0 0 0",
  "selectRobot": "0 1",
  "GripOpenPos": "0 100",
  "GripClosePos": "0 75.5",
  "GraspData": "0 80 50 10",
  "IsFullyClosed": "0 -1",
  "sysState": "0 7",
  "pd 2800": "0 1",
  "attach": "0 1",
  "sig 10001": "0 10001 0",
  "Speed2 1": "0 1 0",
  "Accel 1": "0 1 100",
  "AccRamp 1": "0 1 0.1",
  "Decel 1": "0 1 100",
  "DecRamp 1": "0 1 0.1",
  "InRange 1": "0 1 10",
  "Straight 1": "0 1 False",
  "Profile 1": "0 1 50 0 100 100 0.1 0.1 10 0",
  "destC": "0 174.988 -1.601 301.11 -0.702 90 180 1",
  "destJ": "0 301.11 0.018 179.925 -180.644 70.685",
}

# A rail arm: a sixth axis in the soft limits and the joint readback.
_RAIL_REPLIES: Dict[str, str] = {
  "pd 16078": "0 1.5, -93, 12, -960, 69, 0",
  "pd 16077": "0 401.5, 93, 348, 960, 134, 1000",
  "wherej": "0 301.11 0.018 179.925 -180.644 70.685 500",
}


class _FakeController:
  """Records each command written to the socket and answers it from a table; "0" otherwise."""

  def __init__(self, overrides: Optional[Dict[str, str]] = None) -> None:
    self.sent: List[str] = []
    self._replies = {**_REPLIES, **(overrides or {})}
    self._last = ""

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    del timeout
    command = data.decode().removesuffix("\n")
    # `exit` closes the session and is answered by nothing.
    self.sent.append("<io.write> exit" if command == "exit" else command)
    self._last = command

  async def readline(self, timeout: Optional[float] = None) -> bytes:
    del timeout
    return (self._replies.get(self._last, "0") + "\r\n").encode()


def _make_arm(fake: _FakeController, has_rail: bool = False) -> PreciseFlexDriver:
  """A PF400 whose socket is the fake."""
  arm = PreciseFlexDriver(
    host="pf400",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=80.0,
    has_rail=has_rail,
  )
  arm.io = MagicMock()
  arm.io.setup = AsyncMock()
  arm.io.stop = AsyncMock()
  arm.io.write = AsyncMock(side_effect=fake.write)
  arm.io.readline = AsyncMock(side_effect=fake.readline)
  arm.io._host, arm.io._port = "pf400", 10100
  return arm


def _is_number(token: str) -> bool:
  try:
    float(token)
  except ValueError:
    return False
  return True


_J = {
  PreciseFlexAxis.BASE: 200.0,
  PreciseFlexAxis.SHOULDER: 10.0,
  PreciseFlexAxis.ELBOW: 170.0,
  PreciseFlexAxis.WRIST: 20.0,
  PreciseFlexAxis.GRIPPER: 100.0,
}
_JR = {**_J, PreciseFlexAxis.RAIL: 500.0}
# The tool point: the v23's fingers grip 51.25 mm below the flange plane, so Z stands that higher.
_LOC = Coordinate(x=400.0, y=100.0, z=150.0)
_POSES = [
  PreciseFlexCartesianPose(location=Coordinate(400.0, 100.0, 150.0), rotation=Rotation(z=0)),
  PreciseFlexCartesianPose(location=Coordinate(420.0, 80.0, 160.0), rotation=Rotation(z=10)),
]


def _rail(arm: PreciseFlexDriver) -> PreciseFlexRail:
  """The rail of an arm the test set up with one."""
  assert arm.rail is not None
  return arm.rail


_Case = Tuple[
  str, Callable[[PreciseFlexDriver], Awaitable[Any]], List[str], Optional[Type[Exception]]
]

# (name, call, commands sent, exception raised or None), each on an arm already set up.
_CASES: List[_Case] = [
  ("stop", lambda arm: arm.stop(), ["attach 0", "hp 0", "<io.write> exit"], None),
  ("request_mode", lambda arm: arm.request_mode(), ["mode"], None),
  ("set_response_mode_pc", lambda arm: arm.set_response_mode("pc"), ["mode 0"], None),
  ("set_response_mode_verbose", lambda arm: arm.set_response_mode("verbose"), ["mode 1"], None),
  ("request_system_state", lambda arm: arm.request_system_state(), ["sysState"], None),
  ("power_on_robot", lambda arm: arm.power_on_robot(), ["hp 1 20"], None),
  (
    "recover_from_fault",
    lambda arm: arm.recover_from_fault(),
    [
      "sysState",
      "hp 1 20",
      "attach 1",
      "state",
      "state",
      "wherej",
      "home",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  ("power_off_robot", lambda arm: arm.power_off_robot(), ["hp 0"], None),
  ("request_power_state", lambda arm: arm.request_power_state(), ["hp"], None),
  ("attach", lambda arm: arm.attach(1), ["attach 1"], None),
  ("attach_query", lambda arm: arm.attach(), ["attach"], None),
  ("detach", lambda arm: arm.detach(), ["attach 0"], None),
  (
    "home",
    lambda arm: arm.home(),
    ["state", "state", "wherej", "home", "state", "state", "wherej"],
    None,
  ),
  ("home_all", lambda arm: arm.home_all(), ["homeAll"], None),
  ("request_state", lambda arm: arm.arm.request_state(), ["state"], None),
  ("request_parameter", lambda arm: arm.request_parameter(2700), ["pd 2700"], None),
  (
    "request_parameter_indexed",
    lambda arm: arm.request_parameter(16050, 1, 2, 3),
    ["pd 16050 1 2 3"],
    None,
  ),
  ("set_parameter", lambda arm: arm.set_parameter(2704, 140), ["pc 2704 140"], None),
  (
    "set_parameter_indexed",
    lambda arm: arm.set_parameter(16050, 5, 1, 2, 3),
    ["pc 16050 1 2 3 5"],
    None,
  ),
  (
    "set_axis_parameter",
    lambda arm: arm.set_axis_parameter(2700, PreciseFlexAxis.SHOULDER, 300),
    ["pc 2700 1 0 2 300"],
    None,
  ),
  ("nop", lambda arm: arm.nop(), ["nop"], None),
  ("request_signal", lambda arm: arm.request_signal(10001), ["sig 10001"], None),
  ("set_signal", lambda arm: arm.set_signal(10001, 1), ["sig 10001 1"], None),
  ("request_monitor_speed", lambda arm: arm.arm.request_monitor_speed(), ["mspeed"], None),
  ("set_monitor_speed", lambda arm: arm.arm.set_monitor_speed(50), ["mspeed 50"], None),
  ("request_payload", lambda arm: arm.arm.request_payload(), ["payload"], None),
  ("set_payload", lambda arm: arm.arm.set_payload(25), ["payload 25"], None),
  ("request_profile_speed", lambda arm: arm.arm.request_profile_speed(1), ["Speed 1"], None),
  ("set_profile_speed", lambda arm: arm.arm.set_profile_speed(1, 40), ["Speed 1 40"], None),
  ("request_profile_speed2", lambda arm: arm.arm.request_profile_speed2(1), ["Speed2 1"], None),
  ("set_profile_speed2", lambda arm: arm.arm.set_profile_speed2(1, 30), ["Speed2 1 30"], None),
  (
    "request_profile_acceleration",
    lambda arm: arm.arm.request_profile_acceleration(1),
    ["Accel 1"],
    None,
  ),
  (
    "set_profile_acceleration",
    lambda arm: arm.arm.set_profile_acceleration(1, 60),
    ["Accel 1 60"],
    None,
  ),
  (
    "request_profile_acceleration_ramp",
    lambda arm: arm.arm.request_profile_acceleration_ramp(1),
    ["AccRamp 1"],
    None,
  ),
  (
    "set_profile_acceleration_ramp",
    lambda arm: arm.arm.set_profile_acceleration_ramp(1, 0.2),
    ["AccRamp 1 0.2"],
    None,
  ),
  (
    "request_profile_deceleration",
    lambda arm: arm.arm.request_profile_deceleration(1),
    ["Decel 1"],
    None,
  ),
  (
    "set_profile_deceleration",
    lambda arm: arm.arm.set_profile_deceleration(1, 70),
    ["Decel 1 70"],
    None,
  ),
  (
    "request_profile_deceleration_ramp",
    lambda arm: arm.arm.request_profile_deceleration_ramp(1),
    ["DecRamp 1"],
    None,
  ),
  (
    "set_profile_deceleration_ramp",
    lambda arm: arm.arm.set_profile_deceleration_ramp(1, 0.3),
    ["DecRamp 1 0.3"],
    None,
  ),
  (
    "request_profile_in_range",
    lambda arm: arm.arm.request_profile_in_range(1),
    ["InRange 1"],
    None,
  ),
  ("set_profile_in_range", lambda arm: arm.arm.set_profile_in_range(1, 10), ["InRange 1 10"], None),
  (
    "request_profile_straight",
    lambda arm: arm.arm.request_profile_straight(1),
    ["Straight 1"],
    None,
  ),
  (
    "set_profile_straight_on",
    lambda arm: arm.arm.set_profile_straight(1, True),
    ["Straight 1 1"],
    None,
  ),
  (
    "set_profile_straight_off",
    lambda arm: arm.arm.set_profile_straight(1, False),
    ["Straight 1 0"],
    None,
  ),
  (
    "request_motion_profile_values",
    lambda arm: arm.arm.request_motion_profile_values(1),
    ["Profile 1"],
    None,
  ),
  (
    "set_motion_profile_values",
    lambda arm: arm.arm.set_motion_profile_values(1, 40, 30, 60, 70, 0.2, 0.3, 10, True),
    ["Profile 1 40 30 60 70 0.2 0.3 10 -1"],
    None,
  ),
  ("release_brake", lambda arm: arm.arm.release_brake(2), ["releaseBrake 2"], None),
  ("set_brake", lambda arm: arm.arm.reengage_brake(2), ["setBrake 2"], None),
  ("zero_torque_on", lambda arm: arm.arm.start_zero_torque(3), ["zeroTorque 1 3"], None),
  ("zero_torque_off", lambda arm: arm.arm.stop_zero_torque(), ["zeroTorque 0"], None),
  (
    "start_freedrive_mode_all",
    lambda arm: arm.arm.start_freedrive_mode(),
    ["freemode 1", "freemode 2", "freemode 3", "freemode 4"],
    None,
  ),
  (
    "start_freedrive_mode_axes",
    lambda arm: arm.arm.start_freedrive_mode([1, 2]),
    ["freemode 1", "freemode 2"],
    None,
  ),
  ("stop_freedrive_mode", lambda arm: arm.arm.stop_freedrive_mode(), ["freemode -1"], None),
  ("halt", lambda arm: arm.arm.halt(), ["halt"], None),
  ("change_config", lambda arm: arm.arm.change_elbow_orientation(1), ["ChangeConfig 1"], None),
  (
    "change_config2",
    lambda arm: arm.arm.change_elbow_orientation_by_algorithm(1),
    ["ChangeConfig2 1"],
    None,
  ),
  ("request_manufacturer", lambda arm: arm.request_manufacturer(), ["pd 100"], None),
  ("request_controller_model", lambda arm: arm.request_controller_model(), ["pd 101"], None),
  ("request_hardware_version", lambda arm: arm.request_hardware_version(), ["pd 102"], None),
  ("request_gpl_version", lambda arm: arm.request_gpl_version(), ["pd 103"], None),
  ("request_controller_serial", lambda arm: arm.request_controller_serial(), ["pd 110"], None),
  ("request_robot_name", lambda arm: arm.request_robot_name(), ["pd 2002"], None),
  ("request_robot_type", lambda arm: arm.request_robot_type(), ["pd 116"], None),
  ("request_axis_count", lambda arm: arm.request_axis_count(), ["pd 2000"], None),
  ("request_extra_axis_count", lambda arm: arm.request_extra_axis_count(), ["pd 2004"], None),
  ("request_axis_mask", lambda arm: arm.request_axis_mask(), ["pd 2003"], None),
  ("request_version", lambda arm: arm.request_version(), ["version"], None),
  (
    "request_joint_limits_soft",
    lambda arm: arm.arm.request_joint_limits(),
    ["pd 16078", "pd 16077"],
    None,
  ),
  (
    "request_joint_limits_hard",
    lambda arm: arm.arm.request_joint_limits(hard=True),
    ["pd 16076", "pd 16075"],
    None,
  ),
  ("request_reference_speed", lambda arm: arm.arm.request_reference_speed(), ["pd 2700"], None),
  (
    "request_reference_acceleration",
    lambda arm: arm.arm.request_reference_acceleration(),
    ["pd 2702"],
    None,
  ),
  ("request_link_lengths", lambda arm: arm.arm.request_link_lengths(), ["pd 16050"], None),
  ("request_tool_length", lambda arm: arm.arm.request_tool_length(), ["pd 16051"], None),
  (
    "request_reference_cartesian_speed",
    lambda arm: arm.arm.request_reference_cartesian_speed(),
    ["pd 2701"],
    None,
  ),
  (
    "request_reference_cartesian_acceleration",
    lambda arm: arm.arm.request_reference_cartesian_acceleration(),
    ["pd 2703"],
    None,
  ),
  ("request_max_speed_percent", lambda arm: arm.arm.request_max_speed_percent(), ["pd 2704"], None),
  (
    "request_max_acceleration_percent",
    lambda arm: arm.arm.request_max_acceleration_percent(),
    ["pd 2705"],
    None,
  ),
  (
    "request_max_deceleration_percent",
    lambda arm: arm.arm.request_max_deceleration_percent(),
    ["pd 2706"],
    None,
  ),
  ("request_base", lambda arm: arm.arm.request_base(), ["base"], None),
  ("set_base", lambda arm: arm.arm.set_base(1.0, 2.0, 3.0, 4.0), ["base 1.0 2.0 3.0 4.0"], None),
  (
    "request_tool_transformation_values",
    lambda arm: arm.arm.request_tool_transformation_values(),
    ["tool"],
    None,
  ),
  ("reset", lambda arm: arm.reset(1), ["reset 1"], None),
  ("request_selected_robot", lambda arm: arm.request_selected_robot(), ["selectRobot"], None),
  ("select_robot", lambda arm: arm.select_robot(1), ["selectRobot 1"], None),
  (
    "recover_axes_within_limits",
    lambda arm: arm.arm.recover_axes_within_limits(),
    ["state", "state", "wherej"],
    None,
  ),
  (
    "request_joint_state",
    lambda arm: arm.arm.request_joint_state(),
    ["state", "state", "wherej"],
    None,
  ),
  (
    "move_to_joint_position",
    lambda arm: arm.arm.move_to_joint_state(_J),
    ["state", "state", "wherej", "moveJ 1 200.0 10.0 170.0 20.0 100.0", "state", "state", "wherej"],
    None,
  ),
  (
    "move_to_joint_position_speed",
    lambda arm: arm.arm.move_to_joint_state(_J, speed_percent=30),
    [
      "Speed 1 30",
      "state",
      "state",
      "wherej",
      "moveJ 1 200.0 10.0 170.0 20.0 100.0",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "request_gripper_pose",
    lambda arm: arm.arm.request_gripper_pose(),
    ["state", "state", "wherej"],
    None,
  ),
  (
    "move_to_location",
    lambda arm: arm.arm.move_to_location(_LOC, direction=0.0),
    [
      "state",
      "state",
      "wherej",
      "moveJ 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_location_speed",
    lambda arm: arm.arm.move_to_location(_LOC, direction=30.0, speed_percent=40),
    [
      "Speed 1 40",
      "state",
      "state",
      "wherej",
      "moveJ 1 201.25 -57.13213751792647 127.7714622309491 -40.63932471302264 70.685",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "move_through_cartesian_poses",
    lambda arm: arm.arm.move_through_cartesian_poses(_POSES),
    [
      "state",
      "state",
      "wherej",
      "Profile 1",
      "Profile 1 50.0 0.0 100.0 100.0 0.1 0.1 -1 0",
      "moveJ 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "moveJ 1 211.25 -49.554898023417685 126.64906635048342 -67.09416832706574 70.685",
      "state",
      "state",
      "wherej",
      "Profile 1 50.0 0.0 100.0 100.0 0.1 0.1 10.0 0",
    ],
    None,
  ),
  (
    "move_through_cartesian_poses_unblended",
    lambda arm: arm.arm.move_through_cartesian_poses(_POSES, speed_percent=30, blend=False),
    [
      "Speed 1 30",
      "state",
      "state",
      "wherej",
      "moveJ 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "moveJ 1 211.25 -49.554898023417685 126.64906635048342 -67.09416832706574 70.685",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  ("dest_c", lambda arm: arm.arm._unchecked_fw_request_cartesian_destination(), ["destC"], None),
  ("dest_j", lambda arm: arm.arm.request_destination_joint_state(), ["destJ"], None),
  ("here_j", lambda arm: arm.arm.set_station_to_current_joint_state(2), ["hereJ 2"], None),
  (
    "here_c",
    lambda arm: arm.arm._unchecked_fw_set_station_to_current_cartesian_location(2),
    ["hereC 2"],
    None,
  ),
  (
    "move_gripper_open",
    lambda arm: arm.gripper.move_to_jaw_position(110.0),
    ["state", "state", "wherej", "ReleasePlate 130.0 50.0", "state", "state", "wherej"],
    None,
  ),
  (
    "move_gripper_close",
    lambda arm: arm.gripper.move_to_jaw_position(50.0),
    ["state", "state", "wherej", "GraspPlate 70.0 50.0 10.0", "state", "state", "wherej"],
    None,
  ),
  (
    "move_to_jaw_position_firmware_units",
    lambda arm: arm.gripper.move_to_jaw_position_firmware_units(120.0),
    ["state", "state", "wherej", "ReleasePlate 120.0 50.0", "state", "state", "wherej"],
    None,
  ),
  (
    "move_gripper_joint_position_force",
    lambda arm: arm.gripper.move_to_jaw_position_firmware_units(70.0, force_sensing=True),
    ["state", "state", "wherej", "GraspPlate 70.0 50.0 10.0", "state", "state", "wherej"],
    None,
  ),
  (
    "move_gripper_position",
    lambda arm: arm.gripper.move_to_jaw_position(50.0, force_sensing=False),
    ["state", "state", "wherej", "MoveOneAxis 5 70.0 1", "state", "state", "wherej"],
    None,
  ),
  ("sense_fully_closed", lambda arm: arm.gripper.sense_fully_closed(), ["IsFullyClosed"], None),
  ("sense_each_fully_closed", lambda arm: arm.gripper.sense_each_fully_closed(), [], ValueError),
  (
    "pick_up_at_joint_position",
    lambda arm: arm.arm.pick_up_at_joint_state(_J, resource_width=85.0),
    [
      "GraspData 105.0 50.0 10.0",
      "state",
      "state",
      "wherej",
      "locAngles 1 200.0 10.0 170.0 20.0 100.0",
      "StationType 1 1 0 100 0 10",
      "pickplate 1 0 0",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "drop_at_joint_position",
    lambda arm: arm.arm.drop_at_joint_state(_J, resource_width=85.0),
    [
      "state",
      "state",
      "wherej",
      "locAngles 1 200.0 10.0 170.0 20.0 100.0",
      "StationType 1 1 0 100 0 10",
      "placeplate 1 0 0",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "pick_up_at_location",
    lambda arm: arm.arm.pick_up_at_location(_LOC, direction=0.0, resource_width=85.0),
    [
      "GraspData 105.0 50.0 10.0",
      "state",
      "state",
      "wherej",
      "locAngles 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "StationType 1 1 0 100 0 10",
      "pickplate 1 0 0",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "drop_at_location",
    lambda arm: arm.arm.drop_at_location(_LOC, direction=0.0, resource_width=85.0),
    [
      "state",
      "state",
      "wherej",
      "locAngles 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "StationType 1 1 0 100 0 10",
      "placeplate 1 0 0",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "park",
    lambda arm: arm.arm.park(),
    [
      "state",
      "state",
      "wherej",
      "moveJ 1 301.125 0.0 180.0 180.0 70.685",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_location_rail_position_without_rail",
    lambda arm: arm.arm.move_to_location(_LOC, direction=0.0, rail_position=300.0),
    [],
    RuntimeError,
  ),
  (
    "pick_up_at_location_rail_position_without_rail",
    lambda arm: arm.arm.pick_up_at_location(
      _LOC, direction=0.0, resource_width=85.0, rail_position=300.0
    ),
    [],
    RuntimeError,
  ),
  (
    "drop_at_location_rail_position_without_rail",
    lambda arm: arm.arm.drop_at_location(
      _LOC, direction=0.0, resource_width=85.0, rail_position=300.0
    ),
    [],
    RuntimeError,
  ),
]

_RAIL_CASES: List[_Case] = [
  (
    "rail_move_rail",
    lambda arm: _rail(arm).move_rail(250.0),
    ["state", "state", "Rail 1 250.0", "MoveRail 1 1"],
    None,
  ),
  (
    "rail_move_to_joint_position",
    lambda arm: arm.arm.move_to_joint_state(_JR),
    [
      "state",
      "state",
      "wherej",
      "moveJ 1 200.0 10.0 170.0 20.0 100.0 500.0 ",
      "state",
      "state",
      "wherej",
    ],
    None,
  ),
  (
    "rail_move_to_location",
    lambda arm: arm.arm.move_to_location(_LOC, direction=0.0, rail_position=300.0),
    ["state", "state", "Rail 1 300.0", "MoveRail 1 1", "state", "state", "wherej"],
    ValueError,
  ),
  (
    "rail_start_freedrive_mode",
    lambda arm: arm.arm.start_freedrive_mode(),
    ["freemode 1", "freemode 2", "freemode 3", "freemode 4", "freemode 6"],
    None,
  ),
  (
    # The station is past the shoulder's soft limit: refused before anything is sent.
    "rail_pick_up_at_location",
    lambda arm: arm.arm.pick_up_at_location(
      _LOC, direction=0.0, resource_width=85.0, rail_position=300.0
    ),
    [
      "state",
      "state",
      "Rail 1 300.0",
      "MoveRail 1 1",
      "GraspData 105.0 50.0 10.0",
      "state",
      "state",
      "wherej",
    ],
    ValueError,
  ),
]

_SETUP_BEFORE_HOME = [
  "mode 0",
  "hp 1 20",
  "attach 1",
  "freemode -1",
  "pd 16078",
  "pd 16077",
  "pd 2003",
  "pd 2002",
  "version",
  "VToolProperty System CameraCount",
  "pd 2700",
  "pd 2702",
  "pd 2704",
  "pd 2705",
  "pd 2706",
  "pd 16050",
  "pd 16051",
  "pd 100",
  "pd 101",
  "pd 102",
  "pd 103",
  "pd 110",
  "pd 116",
  "pd 2000",
  "pd 2004",
  "pd 16076",
  "pd 16075",
  "pd 2701",
  "pd 2703",
  "sysState",
]

_SETUP_AFTER_HOME = ["pd 2800", "state", "state", "wherej"]

_SETUP_SKIP_HOME = _SETUP_BEFORE_HOME + _SETUP_AFTER_HOME

_SETUP = (
  _SETUP_BEFORE_HOME
  + ["state", "state", "wherej", "home", "state", "state", "wherej"]
  + _SETUP_AFTER_HOME
)


class _FastEomTestBase(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    stack.enter_context(
      patch("pylabrobot.brooks.precise_flex.driver.features.arm.anyio.sleep", new=AsyncMock())
    )


class TestPreciseFlexWire(_FastEomTestBase):
  """Each public method sends its exact wire command sequence."""

  def assert_wire(self, sent: List[str], expected: List[str]) -> None:
    """Commands match exactly, except numbers, which match to within 1e-9."""
    assert len(sent) == len(expected), f"sent {sent}"
    for got, want in zip(sent, expected):
      got_tokens, want_tokens = got.split(" "), want.split(" ")
      assert len(got_tokens) == len(want_tokens), f"{got!r} != {want!r}"
      for g, w in zip(got_tokens, want_tokens):
        if _is_number(w) and _is_number(g):
          assert math.isclose(float(g), float(w), abs_tol=1e-9), f"{got!r} != {want!r}"
        else:
          assert g == w, f"{got!r} != {want!r}"

  @contextlib.asynccontextmanager
  async def _open_setup(
    self, has_rail: bool, **kwargs: Any
  ) -> AsyncIterator[Tuple[PreciseFlexDriver, _FakeController]]:
    fake = _FakeController(_RAIL_REPLIES if has_rail else None)
    arm = _make_arm(fake, has_rail=has_rail)
    async with arm.lifespan(**kwargs):
      yield arm, fake

  async def test_setup(self) -> None:
    async with self._open_setup(has_rail=False) as (_, fake):
      self.assert_wire(fake.sent, _SETUP)

  async def test_setup_skip_home(self) -> None:
    async with self._open_setup(has_rail=False, skip_home=True) as (_, fake):
      self.assert_wire(fake.sent, _SETUP_SKIP_HOME)

  async def test_setup_with_rail(self) -> None:
    async with self._open_setup(has_rail=True) as (_, fake):
      self.assert_wire(fake.sent, _SETUP)

  async def test_a_failed_setup_hands_the_arm_back_and_closes_the_links(self) -> None:
    # -3101 is the arm's reply to `state` while it pressed on something.
    fake = _FakeController({"state": "-3101"})
    arm = _make_arm(fake)
    image_io, server_io = MagicMock(stop=AsyncMock()), MagicMock(stop=AsyncMock())
    arm._vision_image_io, arm._vision_server_io = image_io, server_io
    with pytest.raises(PreciseFlexError):
      async with arm.lifespan(skip_home=True):
        pass
    assert fake.sent[-3:] == ["attach 0", "hp 0", "<io.write> exit"]
    image_io.stop.assert_awaited_once()
    server_io.stop.assert_awaited_once()
    cast(AsyncMock, arm.io.stop).assert_awaited_once()

  async def test_stop_after_a_failed_setup_keeps_the_setup_error(self) -> None:
    fake = _FakeController({"state": "-3101"})
    arm = _make_arm(fake)
    with pytest.raises(PreciseFlexError):
      try:
        async with arm.lifespan(skip_home=True):
          pass
      finally:
        sent = len(fake.sent)
        if sniffio.current_async_library() == "asyncio":
          await arm.stop()
    assert len(fake.sent) == sent

  async def test_a_failure_while_connecting_closes_the_link(self) -> None:
    fake = _FakeController({"mode 0": "-1"})
    arm = _make_arm(fake)
    with pytest.raises(PreciseFlexError):
      async with arm.lifespan(skip_home=True):
        pass
    cast(AsyncMock, arm.io.stop).assert_awaited_once()

  async def test_the_setup_error_is_raised_when_handing_back_fails(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    fake = _FakeController({"state": "-3101", "attach 0": "-1"})
    arm = _make_arm(fake)
    cast(AsyncMock, arm.io.stop).side_effect = OSError("closed")
    with (
      pytest.raises(PreciseFlexError) as raised,
      caplog.at_level("WARNING", logger="pylabrobot.brooks.precise_flex.driver.master"),
    ):
      async with arm.lifespan(skip_home=True):
        pass
    assert raised.value.replycode == -3101
    assert "hp 0" in fake.sent
    assert caplog.records

  async def _check(self, cases: List[_Case], has_rail: bool) -> None:
    for name, call, expected, error in cases:
      async with self._open_setup(has_rail=has_rail) as (arm, fake):
        fake.sent.clear()
        if name == "stop":
          continue
        if error is None:
          await call(arm)
        else:
          with pytest.raises(error):
            await call(arm)
        self.assert_wire(fake.sent, expected)
      if name == "stop":
        self.assert_wire(fake.sent, expected)

  async def test_every_public_method(self) -> None:
    await self._check(_CASES, has_rail=False)

  async def test_rail_arm(self) -> None:
    await self._check(_RAIL_CASES, has_rail=True)

  async def test_configuration_flat_names_are_deprecated(self) -> None:
    for has_rail in (False, True):
      async with self._open_setup(has_rail=has_rail) as (arm, _):
        config = arm.configuration
        expected = {
          "soft_limits": await arm.arm.request_joint_limits(),
          "hard_limits": await arm.arm.request_joint_limits(hard=True),
          "max_cartesian_speed": config.arm.max_cartesian_speed,
          "max_cartesian_acceleration": config.arm.max_cartesian_acceleration,
          "kinematics": config.arm.kinematics,
          "kinematics_source": config.arm.kinematics_source,
          "reach_class": config.arm.reach_class,
          "z_range": config.arm.z_range,
          "work_envelope": config.arm.work_envelope,
          "gripper_width_range": config.gripper.soft_limit_range,
          "is_dual_gripper": config.gripper.is_dual_gripper,
          "power_state": await arm.request_system_state(),
          "is_vision_gripper": config.has_vision_gripper,
        }
        for name, value in expected.items():
          with pytest.warns(DeprecationWarning):
            assert getattr(config, name) == value
        for name in ("max_joint_speed", "max_joint_acceleration", "max_joint_deceleration"):
          with pytest.warns(DeprecationWarning):
            assert set(getattr(config, name)) - set(config.arm.soft_limits) == {
              PreciseFlexAxis.GRIPPER
            }
        assert config.has_rail == has_rail

  async def test_setup_is_connect_initialize_home(self) -> None:
    async with self._open_setup(has_rail=False) as (_, whole):
      whole_sent = list(whole.sent)
    fake = _FakeController()
    arm = _make_arm(fake)
    async with arm.io.lifespan():
      await arm.set_response_mode("pc")
      await arm.power_on_robot()
      await arm.attach(1)
      await arm.arm.stop_freedrive_mode()
      taken_control = len(fake.sent)
      await arm.discover()
      discovery = fake.sent[taken_control:]
      await arm.home()
      await arm.arm._handle_out_of_range_axes()
    assert fake.sent == whole_sent
    assert all(c.split()[0] in ("pd", "version", "VToolProperty", "sysState") for c in discovery), (
      discovery
    )

  async def test_a_failed_configuration_read_ends_setup(self) -> None:
    fake = _FakeController()
    arm = _make_arm(fake)
    failing_read = AsyncMock(side_effect=TimeoutError("pd 116"))
    arm._request_configuration = failing_read  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
      async with arm:
        pass
    assert arm.arm.configuration is None

  async def test_at_speed_restores_the_profile_speed(self) -> None:
    async with self._open_setup(has_rail=False) as (arm, fake):
      for _, fail in (("after the moves", False), ("after a fault", True)):
        fake.sent.clear()
        with contextlib.suppress(RuntimeError):
          async with arm.arm.at_speed(35):
            await arm.arm.move_to_joint_state(_J)
            if fail:
              raise RuntimeError("fault mid-move")
        speeds = [c for c in fake.sent if c.startswith("Speed")]
        assert speeds == ["Speed 1", "Speed 1 35", "Speed 1 20.0"]
      fake.sent.clear()
      async with arm.arm.at_speed(None):
        pass
      assert fake.sent == []

  async def test_only_an_arm_with_a_rail_has_one(self) -> None:
    async with self._open_setup(has_rail=False) as (rail_less, _):
      assert rail_less.rail is None
    async with self._open_setup(has_rail=True) as (with_rail, _):
      assert isinstance(with_rail.rail, PreciseFlexRail)

  async def test_recovering_an_axis_out_of_range(self) -> None:
    fake = _FakeController({"Speed 1": "0 1 60"})
    arm = _make_arm(fake)
    async with arm:
      fake.sent.clear()
      fake._replies["wherej"] = "0 301.02 93.476 179.609 83.93 125.867"
      assert await arm.arm.recover_axes_within_limits() == {PreciseFlexAxis.SHOULDER: 92.0}
      self.assert_wire(
        fake.sent,
        [
          "state",
          "state",
          "wherej",
          "Speed 1",
          "Speed 1 20.0",
          # Each axis is moved from where it was just read.
          "state",
          "state",
          "wherej",
          "MoveOneAxis 2 92.0 1",
          "state",
          "state",
          "wherej",
          "Speed 1 60.0",
        ],
      )


class TestProbeZUsingZtouch(_FastEomTestBase):
  """`HeightDetect`'s replies are the arm's: `0 4.15` on contact, -2101 at the limit. The v23's back
  flat stands 27.90 mm above the Z joint."""

  @contextlib.asynccontextmanager
  async def _arm(
    self, reply: str = "0 4.15", **overrides: str
  ) -> AsyncIterator[Tuple[PreciseFlexDriver, _FakeController]]:
    fake = _FakeController(
      {
        "pd 112 0 0 1": "0 189117",
        "wherej": "0 29.989 -31.304 102.074 -72.846 125.979",
        "HeightDetect 1 -28.0 -15.0": reply,
        "HeightDetect 2 -28.0 -15.0": "0 4.36",
        **overrides,
      }
    )
    arm = _make_arm(fake)
    async with arm:
      fake.sent.clear()
      yield arm, fake

  async def test_contact_returns_the_back_flat_and_reads_where_the_arm_stopped(self) -> None:
    async with self._arm() as (arm, fake):
      assert await arm.arm.probe_z_using_ztouch(search_end_position=29.889) == 32.05
      index = fake.sent.index("HeightDetect 1 -28.0 -15.0")
      assert fake.sent[index + 1 : index + 5] == ["state", "state", "wherej", "mspeed 50"]
      assert "moveJ 1 6.15 -31.304 102.074 -72.846 125.979" in fake.sent[index + 5 :]

  async def test_reaching_the_end_returns_none_and_does_not_move_after(self) -> None:
    async with self._arm("-2101") as (arm, fake):
      result = await arm.arm.probe_z_using_ztouch(search_end_position=29.889)
      assert result is None
      assert not [c for c in fake.sent if c.startswith("moveJ")]

  async def test_a_post_detection_distance_of_0_does_not_move_after(self) -> None:
    async with self._arm() as (arm, fake):
      await arm.arm.probe_z_using_ztouch(search_end_position=29.889, post_detection_distance=0)
      assert not [c for c in fake.sent if c.startswith("moveJ")]

  async def test_a_second_measurement_sends_mode_2(self) -> None:
    async with self._arm() as (arm, fake):
      result = await arm.arm.probe_z_using_ztouch(
        search_end_position=29.889, perform_second_measurement_on_lift_off=True
      )
      assert result == 32.26
      assert "HeightDetect 2 -28.0 -15.0" in fake.sent

  async def test_the_search_speed_is_the_default_or_the_argument_and_is_put_back(self) -> None:
    for arguments, sent in (({}, "mspeed 50"), ({"search_speed_percent": 40}, "mspeed 40")):
      async with self._arm(mspeed="0 30") as (arm, fake):
        await arm.arm.probe_z_using_ztouch(search_end_position=29.889, **arguments)
        search = fake.sent.index("HeightDetect 1 -28.0 -15.0")
        assert fake.sent.index(sent) < search
        assert fake.sent.index("mspeed 30") > search

  async def test_a_default_search_speed_out_of_range_is_refused_on_assignment(self) -> None:
    async with self._arm() as (arm, _):
      with pytest.raises(ValueError):
        arm.arm.default_ztouch_search_speed_percent = 4

  async def test_the_approach_and_the_rest_run_at_the_default_or_the_argument_speed(self) -> None:
    # The profile speed was 20 before: the approach once ran at whatever it was.
    for arguments, sent in (
      ({}, "Speed 1 75.0"),
      ({"approach_speed_percent": 30.0}, "Speed 1 30.0"),
    ):
      async with self._arm() as (arm, fake):
        await arm.arm.probe_z_using_ztouch(
          search_start_position=57.889, search_end_position=29.889, **arguments
        )
        speeds = [c for c in fake.sent if c.startswith(("Speed 1 ", "moveJ"))]
        assert speeds[0::3] == [sent] * 2  # before the approach, before the rest
        assert [c[:5] for c in speeds[1::3]] == ["moveJ"] * 2
        assert speeds[2::3] == ["Speed 1 20.0"] * 2

  async def test_a_default_approach_speed_out_of_range_is_refused_on_assignment(self) -> None:
    async with self._arm() as (arm, _):
      for speed_percent in (0.0, 101.0):
        with pytest.raises(ValueError):
          arm.arm.default_ztouch_approach_speed_percent = speed_percent

  async def test_the_search_waits_for_its_read_timeout_and_puts_the_connections_back(self) -> None:
    for arguments, waited in (({}, 120.0), ({"read_timeout": 45.0}, 45.0)):
      async with self._arm() as (arm, fake):
        arm.io._read_timeout = 30.0
        timeouts = []

        async def readline() -> bytes:
          timeouts.append((fake._last, arm.io._read_timeout))
          return await fake.readline()

        arm.io.readline = AsyncMock(side_effect=readline)  # type: ignore[method-assign]
        await arm.arm.probe_z_using_ztouch(search_end_position=29.889, **arguments)
        assert ("HeightDetect 1 -28.0 -15.0", waited) in timeouts
        assert {t for c, t in timeouts if not c.startswith("HeightDetect")} == {30.0}

  async def test_no_licence_or_a_bad_argument_sends_no_search(self) -> None:
    # The licence word with bit 14 cleared.
    async with self._arm(**{"pd 112 0 0 1": f"0 {189117 & ~(1 << 14)}"}) as (arm, fake):
      with pytest.raises(RuntimeError):
        await arm.arm.probe_z_using_ztouch(search_end_position=29.889)
    async with self._arm() as (arm, fake):
      for arguments in (
        {"search_end_position": 29.889, "detection_force": 0.0},
        {"search_end_position": 29.889, "search_speed_percent": 4},
        {"search_end_position": 29.889, "approach_speed_percent": 0.0},
        {"search_end_position": 29.889, "post_detection_distance": -1.0},
        {"search_end_position": 29.889, "read_timeout": 0.0},
        {"search_end_position": 29.0},  # under the Z joint's soft limit
        {"search_end_position": 53.15},  # under 10 mm of search
      ):
        with pytest.raises(ValueError):
          await arm.arm.probe_z_using_ztouch(**arguments)
      assert not [c for c in fake.sent if c.startswith("HeightDetect")]

  async def test_an_s23_returns_its_own_back_flat(self) -> None:
    # The s23's back flat stands 37.53 mm above the Z joint.
    async with self._arm(**{"VToolProperty System CameraCount": "0"}) as (arm, _):
      assert await arm.arm.probe_z_using_ztouch(search_end_position=39.519) == 41.68

  async def test_an_interrupt_halts_on_the_second_port_and_discards_the_false_height(self) -> None:
    # The search's reply arrives once it ends: the false height a halted search gave on the arm.
    async with self._arm() as (arm, fake):
      blocked = anyio.Event()
      late_replies = [b"0 176.87\r\n"]
      scopes: list[anyio.CancelScope] = []
      cancelled: list[bool] = []

      async def readline(*args: Any, **kwargs: Any) -> bytes:
        if fake.sent[-1:] and fake.sent[-1].startswith("HeightDetect"):
          if not blocked.is_set():
            blocked.set()
            await anyio.sleep_forever()
          if late_replies:
            return late_replies.pop()
        return await fake.readline()

      arm.io.readline = AsyncMock(side_effect=readline)  # type: ignore[method-assign]
      status = MagicMock(setup=AsyncMock(), write=AsyncMock(), stop=AsyncMock())

      async def run_probe() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await arm.arm.probe_z_using_ztouch(search_end_position=29.889)
          except anyio.get_cancelled_exc_class():
            cancelled.append(True)
            raise

      with patch("pylabrobot.brooks.precise_flex.driver.features.arm.Socket", return_value=status):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_probe)
          await blocked.wait()
          scopes[0].cancel()
      assert cancelled == [True]
      status.write.assert_awaited_once_with(b"selectrobot 1\nhalt\n")
      assert late_replies == []
      assert await arm.send_command("mspeed") == "50"

  async def test_an_interrupt_preserves_cancellation_when_speed_restore_fails(self) -> None:
    async with self._arm() as (arm, fake):
      blocked = anyio.Event()
      late_replies = [b"0 176.87\r\n"]
      scopes: list[anyio.CancelScope] = []
      cancelled: list[bool] = []

      async def readline(*args: Any, **kwargs: Any) -> bytes:
        if fake.sent[-1:] and fake.sent[-1].startswith("HeightDetect"):
          if not blocked.is_set():
            blocked.set()
            await anyio.sleep_forever()
          if late_replies:
            return late_replies.pop()
        if fake.sent[-1:] == ["mspeed 50"] and blocked.is_set():
          raise OSError("socket closed during restore")
        return await fake.readline()

      arm.io.readline = AsyncMock(side_effect=readline)  # type: ignore[method-assign]
      status = MagicMock(setup=AsyncMock(), write=AsyncMock(), stop=AsyncMock())

      async def run_probe() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await arm.arm.probe_z_using_ztouch(search_end_position=29.889, search_speed_percent=40)
          except anyio.get_cancelled_exc_class():
            cancelled.append(True)
            raise

      with patch("pylabrobot.brooks.precise_flex.driver.features.arm.Socket", return_value=status):
        async with anyio.create_task_group() as tg:
          tg.start_soon(run_probe)
          await blocked.wait()
          scopes[0].cancel()
      assert cancelled == [True]


class TestPreciseFlexDefaults(_FastEomTestBase):
  """A `default_*` attribute set on an arm, or on the class, is what goes out."""

  @contextlib.asynccontextmanager
  async def _arm(self) -> AsyncIterator[Tuple[PreciseFlexDriver, _FakeController]]:
    fake = _FakeController({"Speed 1": "0 1 60"})
    arm = _make_arm(fake)
    async with arm:
      fake.sent.clear()
      yield arm, fake

  async def test_grasp_defaults_set_on_the_arm(self) -> None:
    async with self._arm() as (arm, fake):
      arm.gripper.default_finger_speed_percent = 30.0
      arm.gripper.default_grasp_force = 5.0
      await arm.arm.pick_up_at_joint_state(_J, resource_width=85.0)
      assert fake.sent[0] == "GraspData 105.0 30.0 5.0"

  async def test_grasp_default_set_on_the_class(self, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PreciseFlexGripper, "default_grasp_force", 7.0)
    async with self._arm() as (arm, fake):
      await arm.arm.pick_up_at_location(_LOC, direction=0.0, resource_width=85.0)
      assert fake.sent[0] == "GraspData 105.0 50.0 7.0"

  async def test_recovery_speed_default_set_on_the_arm(self) -> None:
    async with self._arm() as (arm, fake):
      arm.arm.default_recovery_speed_percent = 10.0
      fake._replies["wherej"] = "0 301.02 93.476 179.609 83.93 125.867"
      await arm.arm.recover_axes_within_limits()
      assert "Speed 1 10.0" in fake.sent


@contextlib.asynccontextmanager
async def _arm_after_homing() -> AsyncIterator[Tuple[PreciseFlexDriver, _FakeController]]:
  """An arm inside its lifespan as it stood after homing, jaws open at 125.844."""
  fake = _FakeController(
    {"Speed 1": "0 1 60", "wherej": "0 301.105 2.235 179.732 -180.776 125.844"}
  )
  arm = _make_arm(fake)
  async with arm:
    fake.sent.clear()
    yield arm, fake


class TestGripperTargetsStayInsideTheirLimits(_FastEomTestBase):
  """Jaw widths stay in mm, and a target is held inside the gripper axis."""

  async def test_jaw_widths_stay_in_mm_after_setup(self) -> None:
    async with _arm_after_homing() as (arm, _):
      # The axis's soft limits (69, 134) through the calibration pair (60 mm at 80 units).
      assert arm.gripper.jaw_width_range == (49.0, 114.0)

  async def test_a_target_at_the_axis_end_is_held_inside_it(self) -> None:
    async with _arm_after_homing() as (arm, fake):
      await arm.gripper.move_to_jaw_position_firmware_units(135.0, force_sensing=False)
      await arm.gripper.move_to_jaw_position(114.0, force_sensing=False)  # the advertised maximum
      moves = [c for c in fake.sent if c.startswith("MoveOneAxis 5")]
      assert moves == ["MoveOneAxis 5 133.5 1"] * 2


def _park_closing(arm: PreciseFlexDriver, asked: bool) -> Awaitable[None]:
  """Park at a pose whose gripper target closes the jaws below the live reading."""
  arm.arm.parking_position = {
    **PreciseFlexArm.PARKING_POSITION_RIGHT,
    PreciseFlexAxis.GRIPPER: 90.0,
  }
  return arm.arm.park(close_gripper_without_force_sensing=asked)


def _park_unset(arm: PreciseFlexDriver, asked: bool) -> Awaitable[None]:
  """Park with ``parking_position`` cleared so ``movetosafe`` runs."""
  arm.arm.parking_position = None
  return arm.arm.park(close_gripper_without_force_sensing=asked)


class TestClosingTheGripperSensesForce(_FastEomTestBase):
  """Only the jaws' own moves close them, limiting the force; anything else closing them is asked."""

  @pytest.mark.parametrize(
    ("name", "call", "sent"),
    [
      (
        "joint move",
        lambda arm, asked: arm.arm.move_to_joint_state(
          {PreciseFlexAxis.GRIPPER: 90.0}, close_gripper_without_force_sensing=asked
        ),
        "moveJ 1 301.105 2.235 179.732 -180.776 90.0",
      ),
      ("park", _park_closing, "moveJ 1 301.125 0.0 180.0 180.0 90.0"),
      ("park, no parking position", _park_unset, "movetosafe"),
      (
        "elbow change",
        lambda arm, asked: arm.arm.change_elbow_orientation(
          2, close_gripper_without_force_sensing=asked
        ),
        "ChangeConfig 2",
      ),
      (
        "elbow change by algorithm",
        lambda arm, asked: arm.arm.change_elbow_orientation_by_algorithm(
          2, close_gripper_without_force_sensing=asked
        ),
        "ChangeConfig2 2",
      ),
    ],
  )
  async def test_a_close_without_force_sensing_is_refused_unless_asked(
    self,
    name: str,
    call: Callable[[PreciseFlexDriver, bool], Awaitable[None]],
    sent: str,
  ) -> None:
    async with _arm_after_homing() as (arm, fake):
      with pytest.raises(ValueError, match="without sensing force"):
        await call(arm, False)
      assert [c for c in fake.sent if c not in ("state", "wherej")] == [], name
      await call(arm, True)
      assert [c for c in fake.sent if c not in ("state", "wherej")] == [sent], name

  async def test_a_close_on_nothing_warns_and_moves_back_to_the_width(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    logger_name = "pylabrobot.brooks.precise_flex.driver.features.gripper"
    gripper_logger = logging.getLogger(logger_name)
    gripper_logger.addHandler(caplog.handler)
    try:
      async with _arm_after_homing() as (arm, fake):
        fake._replies["GraspPlate 80.0 50.0 10.0"] = "0 0"  # the arm's answer with the jaws empty
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
          await arm.gripper.move_to_jaw_position_firmware_units(80.0)
        assert any("closed on nothing" in record.getMessage() for record in caplog.records)
        # On nothing the jaws ran past 80 (to 72.9 on the arm), so they are moved back to it.
        jaw_moves = [c for c in fake.sent if c.startswith(("GraspPlate", "ReleasePlate"))]
        assert jaw_moves == ["GraspPlate 80.0 50.0 10.0", "ReleasePlate 80.0 50.0"]

      async with _arm_after_homing() as (arm, fake):
        fake._replies["GraspPlate 80.0 50.0 10.0"] = "0 -1"  # its answer holding a block
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
          await arm.gripper.move_to_jaw_position_firmware_units(80.0)
        assert caplog.records == []
        assert "ReleasePlate 80.0 50.0" not in fake.sent
    finally:
      gripper_logger.removeHandler(caplog.handler)

  async def test_recovery_leaves_an_over_open_gripper(self) -> None:
    async with _arm_after_homing() as (arm, fake):
      # A constructed reading: the jaws open past the axis's 134.
      fake._replies["wherej"] = "0 301.105 2.235 179.732 -180.776 136.0"
      assert await arm.arm.recover_axes_within_limits() == {}
      assert [c for c in fake.sent if c.startswith("MoveOneAxis")] == []


class TestMotionSettlesBeforeTheNextCommand(_FastEomTestBase):
  """The gripper and the rail wait for the arm to stop before they move."""

  async def test_the_gripper_and_the_rail_wait_for_the_arm_to_stop(self) -> None:
    for _, move in (
      ("jaws", lambda arm: arm.gripper.move_to_jaw_position(70.0, force_sensing=True)),
      ("jaws, firmware units", lambda arm: arm.gripper.move_to_jaw_position_firmware_units(90.0)),
      ("rail", lambda arm: _rail(arm).move_rail(250.0)),
    ):
      fake = _FakeController(_RAIL_REPLIES)
      arm = _make_arm(fake, has_rail=True)
      async with arm:
        fake.sent.clear()
        stopped = AsyncMock(side_effect=lambda: fake.sent.append("<stopped>"))
        arm.arm._wait_for_eom = stopped  # type: ignore[method-assign]
        await move(arm)
        assert fake.sent[0] == "<stopped>"


class TestRefusals(AnyioTestBase):
  """Arguments the controller would not accept are refused before anything is sent."""

  async def test_zero_torque_on_no_axis_is_refused(self) -> None:
    fake = _FakeController()
    arm = _make_arm(fake)
    with pytest.raises(ValueError, match="axis_mask"):
      await arm.arm.start_zero_torque(0)
    assert fake.sent == []

  async def test_a_parameter_addressed_in_part_is_refused(self) -> None:
    # The controller's `pc` takes 2 or 5 arguments; a unit alone would be written to another one.
    fake = _FakeController()
    arm = _make_arm(fake)
    with pytest.raises(ValueError, match="together or not at all"):
      await arm.set_parameter(16050, 5, unit_number=1)
    assert fake.sent == []


_DEPRECATED_DRIVER_MEMBERS: List[
  Tuple[str, Callable[[PreciseFlexDriver], Awaitable[Any]], Callable[..., Any]]
] = [
  (
    "set_monitor_speed",
    lambda a: a.set_monitor_speed(speed_pct=50),
    lambda a: a.arm.set_monitor_speed(50),
  ),
  ("set_payload", lambda a: a.set_payload(payload_pct=25), lambda a: a.arm.set_payload(25)),
  (
    "set_profile_speed",
    lambda a: a.set_profile_speed(1, speed_pct=40),
    lambda a: a.arm.set_profile_speed(1, 40),
  ),
  (
    "set_profile_speed2",
    lambda a: a.set_profile_speed2(1, speed2_pct=30),
    lambda a: a.arm.set_profile_speed2(1, 30),
  ),
  (
    "set_profile_acceleration",
    lambda a: a.set_profile_acceleration(1, acceleration_pct=60),
    lambda a: a.arm.set_profile_acceleration(1, 60),
  ),
  (
    "set_profile_deceleration",
    lambda a: a.set_profile_deceleration(1, deceleration_pct=70),
    lambda a: a.arm.set_profile_deceleration(1, 70),
  ),
  (
    "set_motion_profile_values",
    lambda a: a.set_motion_profile_values(
      1,
      speed_pct=40,
      speed2_pct=30,
      acceleration_pct=60,
      deceleration_pct=70,
      acceleration_ramp=0.2,
      deceleration_ramp=0.3,
      in_range=10,
      straight=True,
    ),
    lambda a: a.arm.set_motion_profile_values(1, 40, 30, 60, 70, 0.2, 0.3, 10, True),
  ),
  (
    "move_to_joint_position",
    lambda a: a.move_to_joint_position(position=_J, speed_pct=30),
    lambda a: a.arm.move_to_joint_state(_J, speed_percent=30),
  ),
  (
    "move_to_location",
    lambda a: a.move_to_location(_LOC, direction=0.0, speed_pct=40),
    lambda a: a.arm.move_to_location(_LOC, direction=0.0, speed_percent=40),
  ),
  (
    "move_through_cartesian_poses",
    lambda a: a.move_through_cartesian_poses(_POSES, speed_pct=30),
    lambda a: a.arm.move_through_cartesian_poses(_POSES, speed_percent=30),
  ),
  (
    "recover_axes_within_limits",
    lambda a: a.recover_axes_within_limits(speed_pct=10),
    lambda a: a.arm.recover_axes_within_limits(speed_percent=10),
  ),
  (
    "pick_up_at_joint_position",
    lambda a: a.pick_up_at_joint_position(position=_J, resource_width=85.0, finger_speed_pct=30),
    lambda a: a.arm.pick_up_at_joint_state(_J, resource_width=85.0, finger_speed_percent=30),
  ),
  (
    "dest_c",
    lambda a: a.dest_c(arg1=0),
    lambda a: a.arm._unchecked_fw_request_cartesian_destination(mode=0),
  ),
  (
    "dest_j",
    lambda a: a.dest_j(arg1=0),
    lambda a: a.arm.request_destination_joint_state(mode=0),
  ),
  (
    "here_j",
    lambda a: a.here_j(location_index=2),
    lambda a: a.arm.set_station_to_current_joint_state(station_index=2),
  ),
  (
    "here_c",
    lambda a: a.here_c(location_index=2),
    lambda a: a.arm._unchecked_fw_set_station_to_current_cartesian_location(station_index=2),
  ),
  (
    "drop_at_joint_position",
    lambda a: a.drop_at_joint_position(position=_J, resource_width=85.0),
    lambda a: a.arm.drop_at_joint_state(_J, resource_width=85.0),
  ),
  (
    "pick_up_at_location",
    lambda a: a.pick_up_at_location(_LOC, direction=0.0, resource_width=85.0, finger_speed_pct=30),
    lambda a: a.arm.pick_up_at_location(
      _LOC, direction=0.0, resource_width=85.0, finger_speed_percent=30
    ),
  ),
  ("zero_torque_on", lambda a: a.zero_torque(True, 3), lambda a: a.arm.start_zero_torque(3)),
  ("zero_torque_off", lambda a: a.zero_torque(False), lambda a: a.arm.stop_zero_torque()),
  (
    "pick_up_at_joint_position",
    lambda a: a.pick_up_at_joint_position(_J, resource_width=85.0),
    lambda a: a.arm.pick_up_at_joint_state(_J, resource_width=85.0),
  ),
  (
    "drop_at_joint_position",
    lambda a: a.drop_at_joint_position(_J, resource_width=85.0),
    lambda a: a.arm.drop_at_joint_state(_J, resource_width=85.0),
  ),
  (
    "pick_up_at_location",
    lambda a: a.pick_up_at_location(_LOC, direction=0.0, resource_width=85.0),
    lambda a: a.arm.pick_up_at_location(_LOC, direction=0.0, resource_width=85.0),
  ),
  (
    "move_gripper",
    lambda a: a.move_gripper(110.0),
    lambda a: a.gripper.move_to_jaw_position(110.0),
  ),
  (
    "move_gripper_joint_position",
    lambda a: a.move_gripper_joint_position(90.0, force_sensing=True),
    lambda a: a.gripper.move_to_jaw_position_firmware_units(90.0, force_sensing=True),
  ),
]

_DEPRECATED_FORWARDS: List[Tuple[Callable[..., Any], Callable[..., Any]]] = [
  (lambda a: a.request_joint_position, lambda a: a.arm.request_joint_state),
  (lambda a: a.request_state, lambda a: a.arm.request_state),
  (lambda a: a.request_gripper_pose, lambda a: a.arm.request_gripper_pose),
  (lambda a: a.is_gripper_closed, lambda a: a.gripper.sense_fully_closed),
  (lambda a: a.are_grippers_closed, lambda a: a.gripper.sense_each_fully_closed),
  (lambda a: a.request_monitor_speed, lambda a: a.arm.request_monitor_speed),
  (lambda a: a.request_payload, lambda a: a.arm.request_payload),
  (lambda a: a.request_profile_speed, lambda a: a.arm.request_profile_speed),
  (lambda a: a.request_profile_speed2, lambda a: a.arm.request_profile_speed2),
  (lambda a: a.request_profile_acceleration, lambda a: a.arm.request_profile_acceleration),
  (
    lambda a: a.request_profile_acceleration_ramp,
    lambda a: a.arm.request_profile_acceleration_ramp,
  ),
  (lambda a: a.set_profile_acceleration_ramp, lambda a: a.arm.set_profile_acceleration_ramp),
  (lambda a: a.request_profile_deceleration, lambda a: a.arm.request_profile_deceleration),
  (
    lambda a: a.request_profile_deceleration_ramp,
    lambda a: a.arm.request_profile_deceleration_ramp,
  ),
  (lambda a: a.set_profile_deceleration_ramp, lambda a: a.arm.set_profile_deceleration_ramp),
  (lambda a: a.request_profile_in_range, lambda a: a.arm.request_profile_in_range),
  (lambda a: a.set_profile_in_range, lambda a: a.arm.set_profile_in_range),
  (lambda a: a.request_profile_straight, lambda a: a.arm.request_profile_straight),
  (lambda a: a.set_profile_straight, lambda a: a.arm.set_profile_straight),
  (lambda a: a.request_motion_profile_values, lambda a: a.arm.request_motion_profile_values),
  (lambda a: a.release_brake, lambda a: a.arm.release_brake),
  (lambda a: a.set_brake, lambda a: a.arm.reengage_brake),
  (lambda a: a.start_freedrive_mode, lambda a: a.arm.start_freedrive_mode),
  (lambda a: a.stop_freedrive_mode, lambda a: a.arm.stop_freedrive_mode),
  (lambda a: a.halt, lambda a: a.arm.halt),
  (lambda a: a.change_config, lambda a: a.arm.change_elbow_orientation),
  (lambda a: a.change_config2, lambda a: a.arm.change_elbow_orientation_by_algorithm),
  (lambda a: a.request_joint_limits, lambda a: a.arm.request_joint_limits),
  (lambda a: a.request_reference_speed, lambda a: a.arm.request_reference_speed),
  (lambda a: a.request_reference_acceleration, lambda a: a.arm.request_reference_acceleration),
  (lambda a: a.request_link_lengths, lambda a: a.arm.request_link_lengths),
  (lambda a: a.request_tool_length, lambda a: a.arm.request_tool_length),
  (lambda a: a.request_kinematic_parameters, lambda a: a.arm.request_kinematic_parameters),
  (
    lambda a: a.request_reference_cartesian_speed,
    lambda a: a.arm.request_reference_cartesian_speed,
  ),
  (
    lambda a: a.request_reference_cartesian_acceleration,
    lambda a: a.arm.request_reference_cartesian_acceleration,
  ),
  (lambda a: a.request_max_speed_percent, lambda a: a.arm.request_max_speed_percent),
  (lambda a: a.request_max_acceleration_percent, lambda a: a.arm.request_max_acceleration_percent),
  (lambda a: a.request_max_deceleration_percent, lambda a: a.arm.request_max_deceleration_percent),
  (lambda a: a.request_base, lambda a: a.arm.request_base),
  (lambda a: a.set_base, lambda a: a.arm.set_base),
  (
    lambda a: a.request_tool_transformation_values,
    lambda a: a.arm.request_tool_transformation_values,
  ),
  (lambda a: a.drop_at_location, lambda a: a.arm.drop_at_location),
  (lambda a: a.park, lambda a: a.arm.park),
]


class TestDeprecatedDriverMembers(_FastEomTestBase):
  """A deprecated member or keyword still works, warns, and matches what its successor does."""

  async def _sent(
    self,
    call: Callable[[PreciseFlexDriver], Awaitable[Any]],
    has_rail: bool = False,
    out_of_range: bool = False,
  ) -> Tuple[List[str], str]:
    fake = _FakeController(_RAIL_REPLIES if has_rail else {"Speed 1": "0 1 60"})
    arm = _make_arm(fake, has_rail=has_rail)
    async with arm:
      fake.sent.clear()
      if out_of_range:  # so recovery sets its speed
        fake._replies["wherej"] = "0 301.02 93.476 179.609 83.93 125.867"
      result = await call(arm)
      return list(fake.sent), repr(result)  # a pose compares by its fields only through its repr

  async def test_each_deprecated_member(self) -> None:
    for name, old, new in _DEPRECATED_DRIVER_MEMBERS:
      out_of_range = name == "recover_axes_within_limits"
      expected = await self._sent(new, out_of_range=out_of_range)
      with pytest.warns(DeprecationWarning):
        got = await self._sent(old, out_of_range=out_of_range)
      assert got == expected, name

  async def test_each_deprecated_forward(self) -> None:
    arm = _make_arm(_FakeController())
    for old, new in _DEPRECATED_FORWARDS:
      old_method, new_method = old(arm), new(arm)
      with patch.object(
        new_method.__self__, new_method.__name__, AsyncMock(return_value="result")
      ) as successor:
        with pytest.warns(DeprecationWarning):
          result = await old_method(1, key="value")
        successor.assert_awaited_once_with(1, key="value")
        assert result == "result"

  async def test_move_rail(self) -> None:
    expected = await self._sent(lambda a: _rail(a).move_rail(250.0), has_rail=True)
    with pytest.warns(DeprecationWarning):
      got = await self._sent(lambda a: a.move_rail(250.0), has_rail=True)
    assert got == expected
    with pytest.warns(DeprecationWarning), pytest.raises(RuntimeError):
      await self._sent(lambda a: a.move_rail(250.0))

  def test_pick_and_parking_attributes(self) -> None:
    arm = _make_arm(_FakeController())
    for name, successor, value in (
      ("location_index", "station_index", 3),
      ("horizontal_compliance", "horizontal_compliance", True),
      ("horizontal_compliance_torque", "horizontal_compliance_torque", 20),
      ("parking_position", "parking_position", {PreciseFlexAxis.SHOULDER: 0.0}),
    ):
      with pytest.warns(DeprecationWarning):
        setattr(arm, name, value)
      assert getattr(arm.arm, successor) == value
      with pytest.warns(DeprecationWarning):
        assert getattr(arm, name) == value
    for name in ("PARKING_POSITION_BACK", "PARKING_POSITION_RIGHT", "PARKING_POSITION_FRONT"):
      assert getattr(PreciseFlexDriver, name) is getattr(PreciseFlexArm, name)

  def test_the_driver_class_was_preciseflex(self) -> None:
    with pytest.warns(DeprecationWarning):
      arm = PreciseFlex(
        host="pf400", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=80.0
      )
    assert isinstance(arm, PreciseFlexDriver)

  def test_profile_index(self) -> None:
    arm = _make_arm(_FakeController())
    with pytest.warns(DeprecationWarning):
      arm.profile_index = 2
    assert arm.arm.profile_index == 2
    with pytest.warns(DeprecationWarning):
      assert arm.profile_index == 2

  async def test_gripper_attributes(self) -> None:
    arm = _make_arm(_FakeController())
    with pytest.warns(DeprecationWarning):
      assert arm.min_gripper_width == arm.gripper.jaw_width_range[0]
    with pytest.warns(DeprecationWarning):
      arm.max_gripper_width = 150.0
    assert arm.gripper.jaw_width_range == (60.0, 150.0)
    with pytest.warns(DeprecationWarning):
      arm.closed_gripper_position = 90.0
    assert arm.gripper.closed_gripper_position == 90.0

  def test_old_module_paths(self) -> None:
    import importlib
    import sys

    for old, name in (
      ("pylabrobot.brooks.precise_flex.precise_flex", "PreciseFlexDriver"),
      ("pylabrobot.brooks.precise_flex.config", "PreciseFlexConfiguration"),
      ("pylabrobot.brooks.precise_flex.errors", "PreciseFlexError"),
    ):
      sys.modules.pop(old, None)
      with pytest.warns(DeprecationWarning):
        module = importlib.import_module(old)
      assert hasattr(module, name)

  def test_joint_pose(self) -> None:
    from pylabrobot.brooks.precise_flex import kinematics

    with pytest.warns(DeprecationWarning):
      assert kinematics.JointPose is kinematics.JointState

  def test_axis(self) -> None:
    from pylabrobot.brooks import precise_flex
    from pylabrobot.brooks.precise_flex import kinematics

    for module in (precise_flex, kinematics):
      with pytest.warns(DeprecationWarning):
        assert module.Axis is kinematics.PreciseFlexAxis
