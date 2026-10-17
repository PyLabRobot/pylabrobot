"""What every public `PreciseFlex` method sends, pinned command by command.

A fake controller answers at the socket, with the replies a PF400 gave. Numbers compare to within
1e-9; everything else must match exactly.
"""

import math
import unittest
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.brooks.precise_flex import Axis, PreciseFlex, PreciseFlexCartesianPose
from pylabrobot.resources import Coordinate, Rotation

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
  "GripOpenPos 120.0": "0",
  "IsFullyClosed": "0 -1",
  "GripClosePos 80.0": "0",
  "gripper 2": "0",
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

_REPLIES: Dict[str, str] = {
  **_PF400_REPLIES,
  "wherej": "0 200 0 180 0 100",
  "wherec": "0 160.054 8.185 269.973 -1.704 90 180 1",
  "mode": "0 0",
  "hp": "0 1",
  "state": "0 0",
  "nop": "0",
  "mspeed": "0 50",
  "payload": "0 0",
  "base": "0 0 0 0 0",
  "tool": "0 0 0 162 0 0 0",
  "selectRobot": "0 1",
  "GripOpenPos": "0 120",
  "GripClosePos": "0 80",
  "GraspData": "0 0 0 0",
  "IsFullyClosed": "0 0",
  "sysState": "0 7",
  "pd 2800": "0 1",
  "attach": "0 -1",
  "sig 10001": "0 10001 1",
  "Speed2 1": "0 1 30",
  "Accel 1": "0 1 60",
  "AccRamp 1": "0 1 0.2",
  "Decel 1": "0 1 70",
  "DecRamp 1": "0 1 0.3",
  "InRange 1": "0 1 10",
  "Straight 1": "0 1 True",
  "Profile 1": "0 1 20 0 100 100 0.1 0.1 10 0",
  "destC": "0 160.054 8.185 269.973 -1.704 90 180 1",
  "destJ": "0 200 0 180 0 100",
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

  async def write(self, data: bytes) -> None:
    command = data.decode().removesuffix("\n")
    # `exit` closes the session and is answered by nothing.
    self.sent.append("<io.write> exit" if command == "exit" else command)
    self._last = command

  async def readline(self) -> bytes:
    return (self._replies.get(self._last, "0") + "\r\n").encode()


def _make_arm(fake: _FakeController, has_rail: bool = False) -> PreciseFlex:
  """A PF400 whose socket is the fake."""
  arm = PreciseFlex(
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
  Axis.BASE: 200.0,
  Axis.SHOULDER: 10.0,
  Axis.ELBOW: 170.0,
  Axis.WRIST: 20.0,
  Axis.GRIPPER: 100.0,
}
_JR = {**_J, Axis.RAIL: 500.0}
_LOC = Coordinate(x=400.0, y=100.0, z=150.0)
_POSES = [
  PreciseFlexCartesianPose(location=Coordinate(400.0, 100.0, 150.0), rotation=Rotation(z=0)),
  PreciseFlexCartesianPose(location=Coordinate(420.0, 80.0, 160.0), rotation=Rotation(z=10)),
]

_Case = Tuple[str, Callable[[PreciseFlex], Awaitable[Any]], List[str], Optional[Type[Exception]]]

# (name, call, commands sent, exception raised or None), each on an arm already set up.
_CASES: List[_Case] = [
  (
    "stop",
    lambda arm: arm.stop(),
    [
      "attach 0",
      "hp 0",
      "<io.write> exit",
    ],
    None,
  ),
  (
    "request_mode",
    lambda arm: arm.request_mode(),
    [
      "mode",
    ],
    None,
  ),
  (
    "set_response_mode_pc",
    lambda arm: arm.set_response_mode("pc"),
    [
      "mode 0",
    ],
    None,
  ),
  (
    "set_response_mode_verbose",
    lambda arm: arm.set_response_mode("verbose"),
    [
      "mode 1",
    ],
    None,
  ),
  (
    "request_system_state",
    lambda arm: arm.request_system_state(),
    [
      "sysState",
    ],
    None,
  ),
  (
    "power_on_robot",
    lambda arm: arm.power_on_robot(),
    [
      "hp 1 20",
    ],
    None,
  ),
  (
    "recover_from_fault",
    lambda arm: arm.recover_from_fault(),
    [
      "sysState",
      "hp 1 20",
      "attach 1",
      "home",
    ],
    None,
  ),
  (
    "power_off_robot",
    lambda arm: arm.power_off_robot(),
    [
      "hp 0",
    ],
    None,
  ),
  (
    "set_power_on",
    lambda arm: arm.set_power(True, 20),
    [
      "hp 1 20",
    ],
    None,
  ),
  (
    "set_power_off",
    lambda arm: arm.set_power(False),
    [
      "hp 0",
    ],
    None,
  ),
  (
    "request_power_state",
    lambda arm: arm.request_power_state(),
    [
      "hp",
    ],
    None,
  ),
  (
    "attach",
    lambda arm: arm.attach(1),
    [
      "attach 1",
    ],
    None,
  ),
  (
    "attach_query",
    lambda arm: arm.attach(),
    [
      "attach",
    ],
    None,
  ),
  (
    "detach",
    lambda arm: arm.detach(),
    [
      "attach 0",
    ],
    None,
  ),
  (
    "home",
    lambda arm: arm.home(),
    [
      "home",
    ],
    None,
  ),
  (
    "home_all",
    lambda arm: arm.home_all(),
    [
      "homeAll",
    ],
    None,
  ),
  (
    "request_state",
    lambda arm: arm.request_state(),
    [
      "state",
    ],
    None,
  ),
  (
    "request_parameter",
    lambda arm: arm.request_parameter(2700),
    [
      "pd 2700",
    ],
    None,
  ),
  (
    "request_parameter_indexed",
    lambda arm: arm.request_parameter(16050, 1, 2, 3),
    [
      "pd 16050 1 2 3",
    ],
    None,
  ),
  (
    "set_parameter",
    lambda arm: arm.set_parameter(2704, 140),
    [
      "pc 2704 140",
    ],
    None,
  ),
  (
    "set_parameter_indexed",
    lambda arm: arm.set_parameter(16050, 5, 1, 2, 3),
    [
      "pc 16050 1 2 3 5",
    ],
    None,
  ),
  (
    "set_axis_parameter",
    lambda arm: arm.set_axis_parameter(2700, Axis.SHOULDER, 300),
    [
      "pc 2700 1 0 2 300",
    ],
    None,
  ),
  (
    "nop",
    lambda arm: arm.nop(),
    [
      "nop",
    ],
    None,
  ),
  (
    "request_signal",
    lambda arm: arm.request_signal(10001),
    [
      "sig 10001",
    ],
    None,
  ),
  (
    "set_signal",
    lambda arm: arm.set_signal(10001, 1),
    [
      "sig 10001 1",
    ],
    None,
  ),
  (
    "request_monitor_speed",
    lambda arm: arm.request_monitor_speed(),
    [
      "mspeed",
    ],
    None,
  ),
  (
    "set_monitor_speed",
    lambda arm: arm.set_monitor_speed(50),
    [
      "mspeed 50",
    ],
    None,
  ),
  (
    "request_payload",
    lambda arm: arm.request_payload(),
    [
      "payload",
    ],
    None,
  ),
  (
    "set_payload",
    lambda arm: arm.set_payload(25),
    [
      "payload 25",
    ],
    None,
  ),
  (
    "request_profile_speed",
    lambda arm: arm.request_profile_speed(1),
    [
      "Speed 1",
    ],
    None,
  ),
  (
    "set_profile_speed",
    lambda arm: arm.set_profile_speed(1, 40),
    [
      "Speed 1 40",
    ],
    None,
  ),
  (
    "request_profile_speed2",
    lambda arm: arm.request_profile_speed2(1),
    [
      "Speed2 1",
    ],
    None,
  ),
  (
    "set_profile_speed2",
    lambda arm: arm.set_profile_speed2(1, 30),
    [
      "Speed2 1 30",
    ],
    None,
  ),
  (
    "request_profile_acceleration",
    lambda arm: arm.request_profile_acceleration(1),
    [
      "Accel 1",
    ],
    None,
  ),
  (
    "set_profile_acceleration",
    lambda arm: arm.set_profile_acceleration(1, 60),
    [
      "Accel 1 60",
    ],
    None,
  ),
  (
    "request_profile_acceleration_ramp",
    lambda arm: arm.request_profile_acceleration_ramp(1),
    [
      "AccRamp 1",
    ],
    None,
  ),
  (
    "set_profile_acceleration_ramp",
    lambda arm: arm.set_profile_acceleration_ramp(1, 0.2),
    [
      "AccRamp 1 0.2",
    ],
    None,
  ),
  (
    "request_profile_deceleration",
    lambda arm: arm.request_profile_deceleration(1),
    [
      "Decel 1",
    ],
    None,
  ),
  (
    "set_profile_deceleration",
    lambda arm: arm.set_profile_deceleration(1, 70),
    [
      "Decel 1 70",
    ],
    None,
  ),
  (
    "request_profile_deceleration_ramp",
    lambda arm: arm.request_profile_deceleration_ramp(1),
    [
      "DecRamp 1",
    ],
    None,
  ),
  (
    "set_profile_deceleration_ramp",
    lambda arm: arm.set_profile_deceleration_ramp(1, 0.3),
    [
      "DecRamp 1 0.3",
    ],
    None,
  ),
  (
    "request_profile_in_range",
    lambda arm: arm.request_profile_in_range(1),
    [
      "InRange 1",
    ],
    None,
  ),
  (
    "set_profile_in_range",
    lambda arm: arm.set_profile_in_range(1, 10),
    [
      "InRange 1 10",
    ],
    None,
  ),
  (
    "request_profile_straight",
    lambda arm: arm.request_profile_straight(1),
    [
      "Straight 1",
    ],
    None,
  ),
  (
    "set_profile_straight_on",
    lambda arm: arm.set_profile_straight(1, True),
    [
      "Straight 1 1",
    ],
    None,
  ),
  (
    "set_profile_straight_off",
    lambda arm: arm.set_profile_straight(1, False),
    [
      "Straight 1 0",
    ],
    None,
  ),
  (
    "request_motion_profile_values",
    lambda arm: arm.request_motion_profile_values(1),
    [
      "Profile 1",
    ],
    None,
  ),
  (
    "set_motion_profile_values",
    lambda arm: arm.set_motion_profile_values(1, 40, 30, 60, 70, 0.2, 0.3, 10, True),
    [
      "Profile 1 40 30 60 70 0.2 0.3 10 -1",
    ],
    None,
  ),
  (
    "release_brake",
    lambda arm: arm.release_brake(2),
    [
      "releaseBrake 2",
    ],
    None,
  ),
  (
    "set_brake",
    lambda arm: arm.set_brake(2),
    [
      "setBrake 2",
    ],
    None,
  ),
  (
    "zero_torque_on",
    lambda arm: arm.zero_torque(True, 3),
    [
      "zeroTorque 1 3",
    ],
    None,
  ),
  (
    "zero_torque_off",
    lambda arm: arm.zero_torque(False),
    [
      "zeroTorque 0",
    ],
    None,
  ),
  (
    "start_freedrive_mode_all",
    lambda arm: arm.start_freedrive_mode(),
    [
      "freemode 1",
      "freemode 2",
      "freemode 3",
      "freemode 4",
    ],
    None,
  ),
  (
    "start_freedrive_mode_axes",
    lambda arm: arm.start_freedrive_mode([1, 2]),
    [
      "freemode 1",
      "freemode 2",
    ],
    None,
  ),
  (
    "stop_freedrive_mode",
    lambda arm: arm.stop_freedrive_mode(),
    [
      "freemode -1",
    ],
    None,
  ),
  (
    "halt",
    lambda arm: arm.halt(),
    [
      "halt",
    ],
    None,
  ),
  (
    "change_config",
    lambda arm: arm.change_config(1),
    [
      "ChangeConfig 1",
    ],
    None,
  ),
  (
    "change_config2",
    lambda arm: arm.change_config2(1),
    [
      "ChangeConfig2 1",
    ],
    None,
  ),
  (
    "request_manufacturer",
    lambda arm: arm.request_manufacturer(),
    [
      "pd 100",
    ],
    None,
  ),
  (
    "request_controller_model",
    lambda arm: arm.request_controller_model(),
    [
      "pd 101",
    ],
    None,
  ),
  (
    "request_hardware_version",
    lambda arm: arm.request_hardware_version(),
    [
      "pd 102",
    ],
    None,
  ),
  (
    "request_gpl_version",
    lambda arm: arm.request_gpl_version(),
    [
      "pd 103",
    ],
    None,
  ),
  (
    "request_controller_serial",
    lambda arm: arm.request_controller_serial(),
    [
      "pd 110",
    ],
    None,
  ),
  (
    "request_robot_name",
    lambda arm: arm.request_robot_name(),
    [
      "pd 2002",
    ],
    None,
  ),
  (
    "request_robot_type",
    lambda arm: arm.request_robot_type(),
    [
      "pd 116",
    ],
    None,
  ),
  (
    "request_axis_count",
    lambda arm: arm.request_axis_count(),
    [
      "pd 2000",
    ],
    None,
  ),
  (
    "request_extra_axis_count",
    lambda arm: arm.request_extra_axis_count(),
    [
      "pd 2004",
    ],
    None,
  ),
  (
    "request_axis_mask",
    lambda arm: arm.request_axis_mask(),
    [
      "pd 2003",
    ],
    None,
  ),
  (
    "request_version",
    lambda arm: arm.request_version(),
    [
      "version",
    ],
    None,
  ),
  (
    "request_joint_limits_soft",
    lambda arm: arm.request_joint_limits(),
    [
      "pd 16078",
      "pd 16077",
    ],
    None,
  ),
  (
    "request_joint_limits_hard",
    lambda arm: arm.request_joint_limits(hard=True),
    [
      "pd 16076",
      "pd 16075",
    ],
    None,
  ),
  (
    "request_reference_speed",
    lambda arm: arm.request_reference_speed(),
    [
      "pd 2700",
    ],
    None,
  ),
  (
    "request_reference_acceleration",
    lambda arm: arm.request_reference_acceleration(),
    [
      "pd 2702",
    ],
    None,
  ),
  (
    "request_link_lengths",
    lambda arm: arm.request_link_lengths(),
    [
      "pd 16050",
    ],
    None,
  ),
  (
    "request_tool_length",
    lambda arm: arm.request_tool_length(),
    [
      "pd 16051",
    ],
    None,
  ),
  (
    "request_kinematic_parameters",
    lambda arm: arm.request_kinematic_parameters(),
    [
      "pd 16050",
      "pd 16051",
    ],
    None,
  ),
  (
    "request_reference_cartesian_speed",
    lambda arm: arm.request_reference_cartesian_speed(),
    [
      "pd 2701",
    ],
    None,
  ),
  (
    "request_reference_cartesian_acceleration",
    lambda arm: arm.request_reference_cartesian_acceleration(),
    [
      "pd 2703",
    ],
    None,
  ),
  (
    "request_max_speed_percent",
    lambda arm: arm.request_max_speed_percent(),
    [
      "pd 2704",
    ],
    None,
  ),
  (
    "request_max_acceleration_percent",
    lambda arm: arm.request_max_acceleration_percent(),
    [
      "pd 2705",
    ],
    None,
  ),
  (
    "request_max_deceleration_percent",
    lambda arm: arm.request_max_deceleration_percent(),
    [
      "pd 2706",
    ],
    None,
  ),
  (
    "request_base",
    lambda arm: arm.request_base(),
    [
      "base",
    ],
    None,
  ),
  (
    "set_base",
    lambda arm: arm.set_base(1.0, 2.0, 3.0, 4.0),
    [
      "base 1.0 2.0 3.0 4.0",
    ],
    None,
  ),
  (
    "request_tool_transformation_values",
    lambda arm: arm.request_tool_transformation_values(),
    [
      "tool",
    ],
    None,
  ),
  (
    "reset",
    lambda arm: arm.reset(1),
    [
      "reset 1",
    ],
    None,
  ),
  (
    "request_selected_robot",
    lambda arm: arm.request_selected_robot(),
    [
      "selectRobot",
    ],
    None,
  ),
  (
    "select_robot",
    lambda arm: arm.select_robot(1),
    [
      "selectRobot 1",
    ],
    None,
  ),
  (
    "recover_axes_within_limits",
    lambda arm: arm.recover_axes_within_limits(),
    [
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "request_joint_position",
    lambda arm: arm.request_joint_position(),
    [
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_joint_position",
    lambda arm: arm.move_to_joint_position(_J),
    [
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 200.0 10.0 170.0 20.0 100.0",
    ],
    None,
  ),
  (
    "move_to_joint_position_speed",
    lambda arm: arm.move_to_joint_position(_J, speed_pct=30),
    [
      "Speed 1 30",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 200.0 10.0 170.0 20.0 100.0",
    ],
    None,
  ),
  (
    "request_gripper_pose",
    lambda arm: arm.request_gripper_pose(),
    [
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_location",
    lambda arm: arm.move_to_location(_LOC, direction=0.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 150.0 84.32164182987252 231.74396572121486 43.934392448912625 100.0",
    ],
    None,
  ),
  (
    "move_to_location_speed",
    lambda arm: arm.move_to_location(_LOC, direction=30.0, speed_pct=40),
    [
      "Speed 1 40",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 150.0 65.5007745559521 232.2285377690509 92.27068767499696 100.0",
    ],
    None,
  ),
  (
    "move_through_cartesian_poses",
    lambda arm: arm.move_through_cartesian_poses(_POSES),
    [
      "wherej",
      "wherej",
      "wherej",
      "Profile 1",
      "Profile 1 20.0 0.0 100.0 100.0 0.1 0.1 -1 0",
      "moveJ 1 150.0 84.32164182987252 231.74396572121486 43.934392448912625 100.0",
      "moveJ 1 160.0 72.08031179618793 233.35093364951658 64.56875455429554 100.0",
      "wherej",
      "wherej",
      "Profile 1 20.0 0.0 100.0 100.0 0.1 0.1 10.0 0",
    ],
    None,
  ),
  (
    "move_through_cartesian_poses_unblended",
    lambda arm: arm.move_through_cartesian_poses(_POSES, speed_pct=30, blend=False),
    [
      "Speed 1 30",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 150.0 84.32164182987252 231.74396572121486 43.934392448912625 100.0",
      "moveJ 1 160.0 72.08031179618793 233.35093364951658 64.56875455429554 100.0",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "dest_c",
    lambda arm: arm.dest_c(),
    [
      "destC",
    ],
    None,
  ),
  (
    "dest_j",
    lambda arm: arm.dest_j(),
    [
      "destJ",
    ],
    None,
  ),
  (
    "here_j",
    lambda arm: arm.here_j(2),
    [
      "hereJ 2",
    ],
    None,
  ),
  (
    "here_c",
    lambda arm: arm.here_c(2),
    [
      "hereC 2",
    ],
    None,
  ),
  (
    "move_gripper_open",
    lambda arm: arm.move_gripper(110.0),
    ["wherej", "wherej", "GripOpenPos 130.0", "gripper 1"],
    None,
  ),
  (
    "move_gripper_close",
    lambda arm: arm.move_gripper(90.0, force_sensing=True),
    ["wherej", "wherej", "GripClosePos 110.0", "gripper 2"],
    None,
  ),
  (
    "move_gripper_joint_position",
    lambda arm: arm.move_gripper_joint_position(120.0),
    ["wherej", "wherej", "GripOpenPos 120.0", "gripper 1"],
    None,
  ),
  (
    "move_gripper_joint_position_force",
    lambda arm: arm.move_gripper_joint_position(90.0, force_sensing=True),
    ["wherej", "wherej", "GripClosePos 90.0", "gripper 2"],
    None,
  ),
  (
    "is_gripper_closed",
    lambda arm: arm.is_gripper_closed(),
    [
      "IsFullyClosed",
    ],
    None,
  ),
  (
    "are_grippers_closed",
    lambda arm: arm.are_grippers_closed(),
    [],
    ValueError,
  ),
  (
    "move_rail",
    lambda arm: arm.move_rail(100.0),
    [],
    RuntimeError,
  ),
  (
    "pick_up_at_joint_position",
    lambda arm: arm.pick_up_at_joint_position(_J, resource_width=85.0),
    [
      "GraspData 85.0 50.0 10.0",
      "locAngles 1 200.0 10.0 170.0 20.0 100.0",
      "StationType 1 1 0 100 0 10",
      "pickplate 1 0 0",
    ],
    None,
  ),
  (
    "drop_at_joint_position",
    lambda arm: arm.drop_at_joint_position(_J, resource_width=85.0),
    [
      "locAngles 1 200.0 10.0 170.0 20.0 100.0",
      "StationType 1 1 0 100 0 10",
      "placeplate 1 0 0",
    ],
    None,
  ),
  (
    "pick_up_at_location",
    lambda arm: arm.pick_up_at_location(_LOC, direction=0.0, resource_width=85.0),
    [
      "GraspData 85.0 50.0 10.0",
      "wherej",
      "wherej",
      "wherej",
      "locAngles 1 150.0 84.32164182987252 231.74396572121486 43.934392448912625 100.0",
      "StationType 1 1 0 100 0 10",
      "pickplate 1 0 0",
    ],
    None,
  ),
  (
    "drop_at_location",
    lambda arm: arm.drop_at_location(_LOC, direction=0.0, resource_width=85.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "locAngles 1 150.0 84.32164182987252 231.74396572121486 43.934392448912625 100.0",
      "StationType 1 1 0 100 0 10",
      "placeplate 1 0 0",
    ],
    None,
  ),
  (
    "park",
    lambda arm: arm.park(),
    [
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 301.125 0.0 180.0 180.0 100.0",
    ],
    None,
  ),
  (
    "send_command",
    lambda arm: arm.send_command("pd 2000"),
    [
      "pd 2000",
    ],
    None,
  ),
]

_RAIL_CASES: List[_Case] = [
  (
    "rail_move_rail",
    lambda arm: arm.move_rail(250.0),
    ["wherej", "wherej", "Rail 1 250.0", "MoveRail 1 1"],
    None,
  ),
  (
    "rail_move_to_joint_position",
    lambda arm: arm.move_to_joint_position(_JR),
    [
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 200.0 10.0 170.0 20.0 100.0 500.0 ",
    ],
    None,
  ),
  (
    "rail_move_to_location",
    lambda arm: arm.move_to_location(_LOC, direction=0.0, rail_position=300.0),
    [
      "wherej",
      "wherej",
      "Rail 1 300.0",
      "MoveRail 1 1",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    ValueError,
  ),
  (
    "rail_start_freedrive_mode",
    lambda arm: arm.start_freedrive_mode(),
    [
      "freemode 1",
      "freemode 2",
      "freemode 3",
      "freemode 4",
      "freemode 6",
    ],
    None,
  ),
  (
    "rail_request_joint_position",
    lambda arm: arm.request_joint_position(),
    [
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "rail_pick_up_at_location",
    lambda arm: arm.pick_up_at_location(
      _LOC, direction=0.0, resource_width=85.0, rail_position=300.0
    ),
    [
      "wherej",
      "wherej",
      "Rail 1 300.0",
      "MoveRail 1 1",
      "GraspData 85.0 50.0 10.0",
      "wherej",
      "wherej",
      "wherej",
      "locAngles 1 500.0 150.0 99.75007617280966 123.39657317069872 -223.1466493435084 70.685",
      "StationType 1 1 0 100 0 10",
      "pickplate 1 0 0",
    ],
    None,
  ),
]

_SETUP = [
  "mode 0",
  "hp 1 20",
  "attach 1",
  "freemode -1",
  "pd 16078",
  "pd 16077",
  "pd 2003",
  "pd 2002",
  "version",
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
  "home",
  "pd 2800",
  "wherej",
  "wherej",
  "wherej",
]

_SETUP_SKIP_HOME = [
  "mode 0",
  "hp 1 20",
  "attach 1",
  "freemode -1",
  "pd 16078",
  "pd 16077",
  "pd 2003",
  "pd 2002",
  "version",
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
  "pd 2800",
  "wherej",
  "wherej",
  "wherej",
]

_RAIL_SETUP = [
  "mode 0",
  "hp 1 20",
  "attach 1",
  "freemode -1",
  "pd 16078",
  "pd 16077",
  "pd 2003",
  "pd 2002",
  "version",
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
  "home",
  "pd 2800",
  "wherej",
  "wherej",
  "wherej",
]


class TestPreciseFlexWire(unittest.IsolatedAsyncioTestCase):
  """Each public method sends exactly the commands it sends today."""

  def setUp(self) -> None:
    sleep = patch("pylabrobot.brooks.precise_flex.precise_flex.asyncio.sleep", new=AsyncMock())
    sleep.start()
    self.addCleanup(sleep.stop)

  def assert_wire(self, sent: List[str], expected: List[str]) -> None:
    """Commands match exactly, except numbers, which match to within 1e-9."""
    self.assertEqual(len(sent), len(expected), f"sent {sent}")
    for got, want in zip(sent, expected):
      got_tokens, want_tokens = got.split(" "), want.split(" ")
      self.assertEqual(len(got_tokens), len(want_tokens), f"{got!r} != {want!r}")
      for g, w in zip(got_tokens, want_tokens):
        if _is_number(w) and _is_number(g):
          self.assertTrue(math.isclose(float(g), float(w), abs_tol=1e-9), f"{got!r} != {want!r}")
        else:
          self.assertEqual(g, w, f"{got!r} != {want!r}")

  async def _run_setup(self, has_rail: bool, **kwargs: Any) -> Tuple[PreciseFlex, _FakeController]:
    fake = _FakeController(_RAIL_REPLIES if has_rail else None)
    arm = _make_arm(fake, has_rail=has_rail)
    await arm.setup(**kwargs)
    return arm, fake

  async def test_setup(self):
    _, fake = await self._run_setup(has_rail=False)
    self.assert_wire(fake.sent, _SETUP)

  async def test_setup_skip_home(self):
    _, fake = await self._run_setup(has_rail=False, skip_home=True)
    self.assert_wire(fake.sent, _SETUP_SKIP_HOME)

  async def test_setup_with_rail(self):
    _, fake = await self._run_setup(has_rail=True)
    self.assert_wire(fake.sent, _RAIL_SETUP)

  async def _check(self, cases: List[_Case], has_rail: bool) -> None:
    for name, call, expected, error in cases:
      with self.subTest(name):
        arm, fake = await self._run_setup(has_rail=has_rail)
        fake.sent.clear()
        if error is None:
          await call(arm)
        else:
          with self.assertRaises(error):
            await call(arm)
        self.assert_wire(fake.sent, expected)

  async def test_every_public_method(self):
    await self._check(_CASES, has_rail=False)

  async def test_rail_arm(self):
    await self._check(_RAIL_CASES, has_rail=True)


if __name__ == "__main__":
  unittest.main()
