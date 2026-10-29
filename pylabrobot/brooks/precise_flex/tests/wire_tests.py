"""What every public `PreciseFlex` method sends, pinned command by command.

A fake controller answers at the socket, from replies a PF400 gave and filled-in values for the
rest. Numbers compare to within 1e-9; everything else must match exactly.
"""

import contextlib
import math
import unittest
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.brooks.precise_flex import (
  PreciseFlex,
  PreciseFlexArm,
  PreciseFlexAxis,
  PreciseFlexCartesianPose,
  PreciseFlexDriver,
  PreciseFlexGripper,
  PreciseFlexRail,
)
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

  async def write(self, data: bytes) -> None:
    command = data.decode().removesuffix("\n")
    # `exit` closes the session and is answered by nothing.
    self.sent.append("<io.write> exit" if command == "exit" else command)
    self._last = command

  async def readline(self) -> bytes:
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
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "home",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
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
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "home",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
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
    lambda arm: arm.arm.request_state(),
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
    lambda arm: arm.set_axis_parameter(2700, PreciseFlexAxis.SHOULDER, 300),
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
    lambda arm: arm.arm.request_monitor_speed(),
    [
      "mspeed",
    ],
    None,
  ),
  (
    "set_monitor_speed",
    lambda arm: arm.arm.set_monitor_speed(50),
    [
      "mspeed 50",
    ],
    None,
  ),
  (
    "request_payload",
    lambda arm: arm.arm.request_payload(),
    [
      "payload",
    ],
    None,
  ),
  (
    "set_payload",
    lambda arm: arm.arm.set_payload(25),
    [
      "payload 25",
    ],
    None,
  ),
  (
    "request_profile_speed",
    lambda arm: arm.arm.request_profile_speed(1),
    [
      "Speed 1",
    ],
    None,
  ),
  (
    "set_profile_speed",
    lambda arm: arm.arm.set_profile_speed(1, 40),
    [
      "Speed 1 40",
    ],
    None,
  ),
  (
    "request_profile_speed2",
    lambda arm: arm.arm.request_profile_speed2(1),
    [
      "Speed2 1",
    ],
    None,
  ),
  (
    "set_profile_speed2",
    lambda arm: arm.arm.set_profile_speed2(1, 30),
    [
      "Speed2 1 30",
    ],
    None,
  ),
  (
    "request_profile_acceleration",
    lambda arm: arm.arm.request_profile_acceleration(1),
    [
      "Accel 1",
    ],
    None,
  ),
  (
    "set_profile_acceleration",
    lambda arm: arm.arm.set_profile_acceleration(1, 60),
    [
      "Accel 1 60",
    ],
    None,
  ),
  (
    "request_profile_acceleration_ramp",
    lambda arm: arm.arm.request_profile_acceleration_ramp(1),
    [
      "AccRamp 1",
    ],
    None,
  ),
  (
    "set_profile_acceleration_ramp",
    lambda arm: arm.arm.set_profile_acceleration_ramp(1, 0.2),
    [
      "AccRamp 1 0.2",
    ],
    None,
  ),
  (
    "request_profile_deceleration",
    lambda arm: arm.arm.request_profile_deceleration(1),
    [
      "Decel 1",
    ],
    None,
  ),
  (
    "set_profile_deceleration",
    lambda arm: arm.arm.set_profile_deceleration(1, 70),
    [
      "Decel 1 70",
    ],
    None,
  ),
  (
    "request_profile_deceleration_ramp",
    lambda arm: arm.arm.request_profile_deceleration_ramp(1),
    [
      "DecRamp 1",
    ],
    None,
  ),
  (
    "set_profile_deceleration_ramp",
    lambda arm: arm.arm.set_profile_deceleration_ramp(1, 0.3),
    [
      "DecRamp 1 0.3",
    ],
    None,
  ),
  (
    "request_profile_in_range",
    lambda arm: arm.arm.request_profile_in_range(1),
    [
      "InRange 1",
    ],
    None,
  ),
  (
    "set_profile_in_range",
    lambda arm: arm.arm.set_profile_in_range(1, 10),
    [
      "InRange 1 10",
    ],
    None,
  ),
  (
    "request_profile_straight",
    lambda arm: arm.arm.request_profile_straight(1),
    [
      "Straight 1",
    ],
    None,
  ),
  (
    "set_profile_straight_on",
    lambda arm: arm.arm.set_profile_straight(1, True),
    [
      "Straight 1 1",
    ],
    None,
  ),
  (
    "set_profile_straight_off",
    lambda arm: arm.arm.set_profile_straight(1, False),
    [
      "Straight 1 0",
    ],
    None,
  ),
  (
    "request_motion_profile_values",
    lambda arm: arm.arm.request_motion_profile_values(1),
    [
      "Profile 1",
    ],
    None,
  ),
  (
    "set_motion_profile_values",
    lambda arm: arm.arm.set_motion_profile_values(1, 40, 30, 60, 70, 0.2, 0.3, 10, True),
    [
      "Profile 1 40 30 60 70 0.2 0.3 10 -1",
    ],
    None,
  ),
  (
    "release_brake",
    lambda arm: arm.arm.release_brake(2),
    [
      "releaseBrake 2",
    ],
    None,
  ),
  (
    "set_brake",
    lambda arm: arm.arm.reengage_brake(2),
    [
      "setBrake 2",
    ],
    None,
  ),
  (
    "zero_torque_on",
    lambda arm: arm.arm.start_zero_torque(3),
    [
      "zeroTorque 1 3",
    ],
    None,
  ),
  (
    "zero_torque_off",
    lambda arm: arm.arm.stop_zero_torque(),
    [
      "zeroTorque 0",
    ],
    None,
  ),
  (
    "start_freedrive_mode_all",
    lambda arm: arm.arm.start_freedrive_mode(),
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
    lambda arm: arm.arm.start_freedrive_mode([1, 2]),
    [
      "freemode 1",
      "freemode 2",
    ],
    None,
  ),
  (
    "stop_freedrive_mode",
    lambda arm: arm.arm.stop_freedrive_mode(),
    [
      "freemode -1",
    ],
    None,
  ),
  (
    "halt",
    lambda arm: arm.arm.halt(),
    [
      "halt",
    ],
    None,
  ),
  (
    "change_config",
    lambda arm: arm.arm.change_elbow_orientation(1),
    [
      "ChangeConfig 1",
    ],
    None,
  ),
  (
    "change_config2",
    lambda arm: arm.arm.change_elbow_orientation_by_algorithm(1),
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
    lambda arm: arm.arm.request_joint_limits(),
    [
      "pd 16078",
      "pd 16077",
    ],
    None,
  ),
  (
    "request_joint_limits_hard",
    lambda arm: arm.arm.request_joint_limits(hard=True),
    [
      "pd 16076",
      "pd 16075",
    ],
    None,
  ),
  (
    "request_reference_speed",
    lambda arm: arm.arm.request_reference_speed(),
    [
      "pd 2700",
    ],
    None,
  ),
  (
    "request_reference_acceleration",
    lambda arm: arm.arm.request_reference_acceleration(),
    [
      "pd 2702",
    ],
    None,
  ),
  (
    "request_link_lengths",
    lambda arm: arm.arm.request_link_lengths(),
    [
      "pd 16050",
    ],
    None,
  ),
  (
    "request_tool_length",
    lambda arm: arm.arm.request_tool_length(),
    [
      "pd 16051",
    ],
    None,
  ),
  (
    "request_kinematic_parameters",
    lambda arm: arm.arm.request_kinematic_parameters(),
    [
      "pd 16050",
      "pd 16051",
    ],
    None,
  ),
  (
    "request_reference_cartesian_speed",
    lambda arm: arm.arm.request_reference_cartesian_speed(),
    [
      "pd 2701",
    ],
    None,
  ),
  (
    "request_reference_cartesian_acceleration",
    lambda arm: arm.arm.request_reference_cartesian_acceleration(),
    [
      "pd 2703",
    ],
    None,
  ),
  (
    "request_max_speed_percent",
    lambda arm: arm.arm.request_max_speed_percent(),
    [
      "pd 2704",
    ],
    None,
  ),
  (
    "request_max_acceleration_percent",
    lambda arm: arm.arm.request_max_acceleration_percent(),
    [
      "pd 2705",
    ],
    None,
  ),
  (
    "request_max_deceleration_percent",
    lambda arm: arm.arm.request_max_deceleration_percent(),
    [
      "pd 2706",
    ],
    None,
  ),
  (
    "request_base",
    lambda arm: arm.arm.request_base(),
    [
      "base",
    ],
    None,
  ),
  (
    "set_base",
    lambda arm: arm.arm.set_base(1.0, 2.0, 3.0, 4.0),
    [
      "base 1.0 2.0 3.0 4.0",
    ],
    None,
  ),
  (
    "request_tool_transformation_values",
    lambda arm: arm.arm.request_tool_transformation_values(),
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
    lambda arm: arm.arm.recover_axes_within_limits(),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "request_joint_state",
    lambda arm: arm.arm.request_joint_state(),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_joint_position",
    lambda arm: arm.arm.move_to_joint_state(_J),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 200.0 10.0 170.0 20.0 100.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_joint_position_speed",
    lambda arm: arm.arm.move_to_joint_state(_J, speed_percent=30),
    [
      "Speed 1 30",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 200.0 10.0 170.0 20.0 100.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "request_gripper_pose",
    lambda arm: arm.arm.request_gripper_pose(),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_location",
    lambda arm: arm.arm.move_to_location(_LOC, direction=0.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_location_speed",
    lambda arm: arm.arm.move_to_location(_LOC, direction=30.0, speed_percent=40),
    [
      "Speed 1 40",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 201.25 -57.13213751792647 127.7714622309491 -40.63932471302264 70.685",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_through_cartesian_poses",
    lambda arm: arm.arm.move_through_cartesian_poses(_POSES),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "Profile 1",
      "Profile 1 50.0 0.0 100.0 100.0 0.1 0.1 -1 0",
      "moveJ 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "moveJ 1 211.25 -49.554898023417685 126.64906635048342 -67.09416832706574 70.685",
      "wherej",
      "wherej",
      "wherej",
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
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "moveJ 1 211.25 -49.554898023417685 126.64906635048342 -67.09416832706574 70.685",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "dest_c",
    lambda arm: arm.arm._unchecked_fw_request_cartesian_destination(),
    [
      "destC",
    ],
    None,
  ),
  (
    "dest_j",
    lambda arm: arm.arm.request_destination_joint_state(),
    [
      "destJ",
    ],
    None,
  ),
  (
    "here_j",
    lambda arm: arm.arm.set_station_to_current_joint_state(2),
    [
      "hereJ 2",
    ],
    None,
  ),
  (
    "here_c",
    lambda arm: arm.arm._unchecked_fw_set_station_to_current_cartesian_location(2),
    [
      "hereC 2",
    ],
    None,
  ),
  (
    "move_gripper_open",
    lambda arm: arm.gripper.move_to_jaw_position(110.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "ReleasePlate 130.0 50.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_gripper_close",
    lambda arm: arm.gripper.move_to_jaw_position(50.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "GraspPlate 70.0 50.0 10.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_to_jaw_position_firmware_units",
    lambda arm: arm.gripper.move_to_jaw_position_firmware_units(120.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "ReleasePlate 120.0 50.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_gripper_joint_position_force",
    lambda arm: arm.gripper.move_to_jaw_position_firmware_units(70.0, force_sensing=True),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "GraspPlate 70.0 50.0 10.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "move_gripper_position",
    lambda arm: arm.gripper.move_to_jaw_position(50.0, force_sensing=False),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "MoveOneAxis 5 70.0 1",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "sense_fully_closed",
    lambda arm: arm.gripper.sense_fully_closed(),
    [
      "IsFullyClosed",
    ],
    None,
  ),
  (
    "sense_each_fully_closed",
    lambda arm: arm.gripper.sense_each_fully_closed(),
    [],
    ValueError,
  ),
  (
    "pick_up_at_joint_position",
    lambda arm: arm.arm.pick_up_at_joint_state(_J, resource_width=85.0),
    [
      "GraspData 105.0 50.0 10.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "locAngles 1 200.0 10.0 170.0 20.0 100.0",
      "StationType 1 1 0 100 0 10",
      "pickplate 1 0 0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "drop_at_joint_position",
    lambda arm: arm.arm.drop_at_joint_state(_J, resource_width=85.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "locAngles 1 200.0 10.0 170.0 20.0 100.0",
      "StationType 1 1 0 100 0 10",
      "placeplate 1 0 0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "pick_up_at_location",
    lambda arm: arm.arm.pick_up_at_location(_LOC, direction=0.0, resource_width=85.0),
    [
      "GraspData 105.0 50.0 10.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "locAngles 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "StationType 1 1 0 100 0 10",
      "pickplate 1 0 0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "drop_at_location",
    lambda arm: arm.arm.drop_at_location(_LOC, direction=0.0, resource_width=85.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "locAngles 1 201.25 -38.74046014630248 128.25603427878514 -89.51557413248264 70.685",
      "StationType 1 1 0 100 0 10",
      "placeplate 1 0 0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "park",
    lambda arm: arm.arm.park(),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 301.125 0.0 180.0 180.0 70.685",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
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
    [
      "wherej",
      "wherej",
      "wherej",
      "Rail 1 250.0",
      "MoveRail 1 1",
    ],
    None,
  ),
  (
    "rail_move_to_joint_position",
    lambda arm: arm.arm.move_to_joint_state(_JR),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
      "moveJ 1 200.0 10.0 170.0 20.0 100.0 500.0 ",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    "rail_move_to_location",
    lambda arm: arm.arm.move_to_location(_LOC, direction=0.0, rail_position=300.0),
    [
      "wherej",
      "wherej",
      "wherej",
      "Rail 1 300.0",
      "MoveRail 1 1",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    ValueError,
  ),
  (
    "rail_start_freedrive_mode",
    lambda arm: arm.arm.start_freedrive_mode(),
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
    "rail_request_joint_state",
    lambda arm: arm.arm.request_joint_state(),
    [
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    None,
  ),
  (
    # The station is past the shoulder's soft limit: refused before anything is sent.
    "rail_pick_up_at_location",
    lambda arm: arm.arm.pick_up_at_location(
      _LOC, direction=0.0, resource_width=85.0, rail_position=300.0
    ),
    [
      "wherej",
      "wherej",
      "wherej",
      "Rail 1 300.0",
      "MoveRail 1 1",
      "GraspData 105.0 50.0 10.0",
      "wherej",
      "wherej",
      "wherej",
      "wherej",
    ],
    ValueError,
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
  "wherej",
  "wherej",
  "wherej",
  "wherej",
  "home",
  "wherej",
  "wherej",
  "wherej",
  "wherej",
  "pd 2800",
  "wherej",
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
  "pd 2800",
  "wherej",
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
  "wherej",
  "wherej",
  "wherej",
  "wherej",
  "home",
  "wherej",
  "wherej",
  "wherej",
  "wherej",
  "pd 2800",
  "wherej",
  "wherej",
  "wherej",
  "wherej",
]


class TestPreciseFlexWire(unittest.IsolatedAsyncioTestCase):
  """Each public method sends exactly the commands it sends today."""

  def setUp(self) -> None:
    sleep = patch(
      "pylabrobot.brooks.precise_flex.driver.features.arm.asyncio.sleep", new=AsyncMock()
    )
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

  async def _run_setup(
    self, has_rail: bool, **kwargs: Any
  ) -> Tuple[PreciseFlexDriver, _FakeController]:
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

  async def test_configuration_flat_names_are_deprecated(self):
    for has_rail in (False, True):
      with self.subTest(has_rail=has_rail):
        arm, _ = await self._run_setup(has_rail=has_rail)
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
          with self.subTest(name), self.assertWarns(DeprecationWarning):
            self.assertEqual(getattr(config, name), value)
        for name in ("max_joint_speed", "max_joint_acceleration", "max_joint_deceleration"):
          with self.subTest(name), self.assertWarns(DeprecationWarning):
            self.assertEqual(
              set(getattr(config, name)) - set(config.arm.soft_limits), {PreciseFlexAxis.GRIPPER}
            )
        self.assertEqual(config.has_rail, has_rail)

  async def test_setup_is_connect_initialize_home(self):
    _, whole = await self._run_setup(has_rail=False)
    fake = _FakeController()
    arm = _make_arm(fake)
    await arm.connect()
    await arm.power_on_robot()
    await arm.attach(1)
    await arm.arm.stop_freedrive_mode()
    taken_control = len(fake.sent)
    await arm.discover()
    discovery = fake.sent[taken_control:]
    await arm.home()
    await arm.arm._handle_out_of_range_axes()
    self.assertEqual(fake.sent, whole.sent)
    self.assertTrue(
      all(c.split()[0] in ("pd", "version", "VToolProperty", "sysState") for c in discovery),
      discovery,
    )

  async def test_a_failed_configuration_read_ends_setup(self):
    fake = _FakeController()
    arm = _make_arm(fake)
    failing_read = AsyncMock(side_effect=TimeoutError("pd 116"))
    arm._request_configuration = failing_read  # type: ignore[method-assign]
    with self.assertRaises(TimeoutError):
      await arm.setup()
    self.assertIsNone(arm.arm.configuration)

  async def test_at_speed_restores_the_profile_speed(self):
    arm, fake = await self._run_setup(has_rail=False)
    for name, fail in (("after the moves", False), ("after a fault", True)):
      with self.subTest(name):
        fake.sent.clear()
        with contextlib.suppress(RuntimeError):
          async with arm.arm.at_speed(35):
            await arm.arm.move_to_joint_state(_J)
            if fail:
              raise RuntimeError("fault mid-move")
        speeds = [c for c in fake.sent if c.startswith("Speed")]
        self.assertEqual(speeds, ["Speed 1", "Speed 1 35", "Speed 1 20.0"])
    fake.sent.clear()
    async with arm.arm.at_speed(None):
      pass
    self.assertEqual(fake.sent, [])

  async def test_only_an_arm_with_a_rail_has_one(self):
    rail_less, _ = await self._run_setup(has_rail=False)
    self.assertIsNone(rail_less.rail)
    with_rail, _ = await self._run_setup(has_rail=True)
    self.assertIsInstance(with_rail.rail, PreciseFlexRail)

  async def test_recovering_an_axis_out_of_range(self):
    fake = _FakeController({"Speed 1": "0 1 60"})
    arm = _make_arm(fake)
    await arm.setup()
    fake.sent.clear()
    fake._replies["wherej"] = "0 301.02 93.476 179.609 83.93 125.867"
    self.assertEqual(await arm.arm.recover_axes_within_limits(), {PreciseFlexAxis.SHOULDER: 92.0})
    self.assert_wire(
      fake.sent,
      [
        "wherej",
        "wherej",
        "wherej",
        "wherej",
        "Speed 1",
        "Speed 1 20.0",
        # Each axis is moved from where it was just read.
        "wherej",
        "wherej",
        "wherej",
        "wherej",
        "MoveOneAxis 2 92.0 1",
        "wherej",
        "wherej",
        "wherej",
        "wherej",
        "Speed 1 60.0",
      ],
    )


class TestPreciseFlexDefaults(unittest.IsolatedAsyncioTestCase):
  """A `default_*` attribute set on an arm, or on the class, is what goes out."""

  def setUp(self) -> None:
    sleep = patch(
      "pylabrobot.brooks.precise_flex.driver.features.arm.asyncio.sleep", new=AsyncMock()
    )
    sleep.start()
    self.addCleanup(sleep.stop)

  async def _arm(self) -> Tuple[PreciseFlexDriver, _FakeController]:
    fake = _FakeController({"Speed 1": "0 1 60"})
    arm = _make_arm(fake)
    await arm.setup()
    fake.sent.clear()
    return arm, fake

  async def test_grasp_defaults_set_on_the_arm(self):
    arm, fake = await self._arm()
    arm.gripper.default_finger_speed_percent = 30.0
    arm.gripper.default_grasp_force = 5.0
    await arm.arm.pick_up_at_joint_state(_J, resource_width=85.0)
    self.assertEqual(fake.sent[0], "GraspData 105.0 30.0 5.0")

  async def test_grasp_default_set_on_the_class(self):
    self.addCleanup(
      setattr, PreciseFlexGripper, "default_grasp_force", PreciseFlexGripper.default_grasp_force
    )
    PreciseFlexGripper.default_grasp_force = 7.0
    arm, fake = await self._arm()
    await arm.arm.pick_up_at_location(_LOC, direction=0.0, resource_width=85.0)
    self.assertEqual(fake.sent[0], "GraspData 105.0 50.0 7.0")

  async def test_recovery_speed_default_set_on_the_arm(self):
    arm, fake = await self._arm()
    arm.arm.default_recovery_speed_percent = 10.0
    fake._replies["wherej"] = "0 301.02 93.476 179.609 83.93 125.867"
    await arm.arm.recover_axes_within_limits()
    self.assertIn("Speed 1 10.0", fake.sent)


async def _arm_after_homing() -> Tuple[PreciseFlexDriver, _FakeController]:
  """An arm set up as it stood after homing, its jaws open at 125.844 on the gripper axis."""
  fake = _FakeController(
    {"Speed 1": "0 1 60", "wherej": "0 301.105 2.235 179.732 -180.776 125.844"}
  )
  arm = _make_arm(fake)
  await arm.setup()
  fake.sent.clear()
  return arm, fake


class TestGripperTargetsStayInsideTheirLimits(unittest.IsolatedAsyncioTestCase):
  """Jaw widths stay in mm, and a target is held inside the gripper axis."""

  def setUp(self) -> None:
    sleep = patch(
      "pylabrobot.brooks.precise_flex.driver.features.arm.asyncio.sleep", new=AsyncMock()
    )
    sleep.start()
    self.addCleanup(sleep.stop)

  async def test_jaw_widths_stay_in_mm_after_setup(self):
    arm, _ = await _arm_after_homing()
    # The axis's soft limits (69, 134) through the calibration pair (60 mm at 80 units).
    self.assertEqual(arm.gripper.jaw_width_range, (49.0, 114.0))

  async def test_a_target_at_the_axis_end_is_held_inside_it(self):
    arm, fake = await _arm_after_homing()
    await arm.gripper.move_to_jaw_position_firmware_units(135.0, force_sensing=False)
    await arm.gripper.move_to_jaw_position(114.0, force_sensing=False)  # the advertised maximum
    moves = [c for c in fake.sent if c.startswith("MoveOneAxis 5")]
    self.assertEqual(moves, ["MoveOneAxis 5 133.5 1"] * 2)


class TestClosingTheGripperSensesForce(unittest.IsolatedAsyncioTestCase):
  """Only the jaws' own moves close them, limiting the force; anything else closing them is asked."""

  setUp = TestGripperTargetsStayInsideTheirLimits.setUp

  async def test_a_close_without_force_sensing_is_refused_unless_asked(self):
    def park_closing(arm: PreciseFlexDriver, asked: bool):
      arm.arm.parking_position = {
        **PreciseFlexArm.PARKING_POSITION_RIGHT,
        PreciseFlexAxis.GRIPPER: 90.0,
      }
      return arm.arm.park(close_gripper_without_force_sensing=asked)

    def park_unset(arm: PreciseFlexDriver, asked: bool):
      arm.arm.parking_position = None
      return arm.arm.park(close_gripper_without_force_sensing=asked)

    for name, call, sent in (
      (
        "joint move",
        lambda arm, asked: arm.arm.move_to_joint_state(
          {PreciseFlexAxis.GRIPPER: 90.0}, close_gripper_without_force_sensing=asked
        ),
        "moveJ 1 301.105 2.235 179.732 -180.776 90.0",
      ),
      ("park", park_closing, "moveJ 1 301.125 0.0 180.0 180.0 90.0"),
      ("park, no parking position", park_unset, "movetosafe"),
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
    ):
      with self.subTest(name):
        arm, fake = await _arm_after_homing()
        with self.assertRaisesRegex(ValueError, "without sensing force"):
          await call(arm, False)
        self.assertEqual([c for c in fake.sent if c != "wherej"], [])
        await call(arm, True)
        self.assertEqual([c for c in fake.sent if c != "wherej"], [sent])

  async def test_recovery_leaves_an_over_open_gripper(self):
    arm, fake = await _arm_after_homing()
    # A constructed reading: the jaws open past the axis's 134.
    fake._replies["wherej"] = "0 301.105 2.235 179.732 -180.776 136.0"
    self.assertEqual(await arm.arm.recover_axes_within_limits(), {})
    self.assertEqual([c for c in fake.sent if c.startswith("MoveOneAxis")], [])


class TestMotionSettlesBeforeTheNextCommand(unittest.IsolatedAsyncioTestCase):
  """The gripper and the rail wait for the arm to stop before they move."""

  def setUp(self) -> None:
    sleep = patch(
      "pylabrobot.brooks.precise_flex.driver.features.arm.asyncio.sleep", new=AsyncMock()
    )
    sleep.start()
    self.addCleanup(sleep.stop)

  async def test_the_gripper_and_the_rail_wait_for_the_arm_to_stop(self):
    for name, move in (
      ("jaws", lambda arm: arm.gripper.move_to_jaw_position(70.0, force_sensing=True)),
      ("jaws, firmware units", lambda arm: arm.gripper.move_to_jaw_position_firmware_units(90.0)),
      ("rail", lambda arm: _rail(arm).move_rail(250.0)),
    ):
      with self.subTest(name):
        fake = _FakeController(_RAIL_REPLIES)
        arm = _make_arm(fake, has_rail=True)
        await arm.setup()
        fake.sent.clear()
        stopped = AsyncMock(side_effect=lambda: fake.sent.append("<stopped>"))
        arm.arm._wait_for_eom = stopped  # type: ignore[method-assign]
        await move(arm)
        self.assertEqual(fake.sent[0], "<stopped>")


class TestRefusals(unittest.IsolatedAsyncioTestCase):
  """Arguments the controller would not accept are refused before anything is sent."""

  async def test_zero_torque_on_no_axis_is_refused(self):
    fake = _FakeController()
    arm = _make_arm(fake)
    with self.assertRaisesRegex(ValueError, "axis_mask"):
      await arm.arm.start_zero_torque(0)
    self.assertEqual(fake.sent, [])

  async def test_a_parameter_addressed_in_part_is_refused(self):
    # The controller's `pc` takes 2 or 5 arguments; a unit alone would be written to another one.
    fake = _FakeController()
    arm = _make_arm(fake)
    with self.assertRaisesRegex(ValueError, "together or not at all"):
      await arm.set_parameter(16050, 5, unit_number=1)
    self.assertEqual(fake.sent, [])


_DEPRECATED_KEYWORDS: List[
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
  ("release_brake", lambda a: a.release_brake(2), lambda a: a.arm.release_brake(2)),
  ("set_brake", lambda a: a.set_brake(2), lambda a: a.arm.reengage_brake(2)),
  ("zero_torque_on", lambda a: a.zero_torque(True, 3), lambda a: a.arm.start_zero_torque(3)),
  ("zero_torque_off", lambda a: a.zero_torque(False), lambda a: a.arm.stop_zero_torque()),
  (
    "start_freedrive_mode",
    lambda a: a.start_freedrive_mode([1, 2]),
    lambda a: a.arm.start_freedrive_mode([1, 2]),
  ),
  ("stop_freedrive_mode", lambda a: a.stop_freedrive_mode(), lambda a: a.arm.stop_freedrive_mode()),
  ("halt", lambda a: a.halt(), lambda a: a.arm.halt()),
  ("change_config", lambda a: a.change_config(1), lambda a: a.arm.change_elbow_orientation(1)),
  (
    "change_config2",
    lambda a: a.change_config2(1),
    lambda a: a.arm.change_elbow_orientation_by_algorithm(1),
  ),
  (
    "request_joint_limits",
    lambda a: a.request_joint_limits(),
    lambda a: a.arm.request_joint_limits(),
  ),
  (
    "request_reference_speed",
    lambda a: a.request_reference_speed(),
    lambda a: a.arm.request_reference_speed(),
  ),
  (
    "request_reference_acceleration",
    lambda a: a.request_reference_acceleration(),
    lambda a: a.arm.request_reference_acceleration(),
  ),
  (
    "request_link_lengths",
    lambda a: a.request_link_lengths(),
    lambda a: a.arm.request_link_lengths(),
  ),
  ("request_tool_length", lambda a: a.request_tool_length(), lambda a: a.arm.request_tool_length()),
  (
    "request_kinematic_parameters",
    lambda a: a.request_kinematic_parameters(),
    lambda a: a.arm.request_kinematic_parameters(),
  ),
  (
    "request_reference_cartesian_speed",
    lambda a: a.request_reference_cartesian_speed(),
    lambda a: a.arm.request_reference_cartesian_speed(),
  ),
  (
    "request_reference_cartesian_acceleration",
    lambda a: a.request_reference_cartesian_acceleration(),
    lambda a: a.arm.request_reference_cartesian_acceleration(),
  ),
  (
    "request_max_speed_percent",
    lambda a: a.request_max_speed_percent(),
    lambda a: a.arm.request_max_speed_percent(),
  ),
  (
    "request_max_acceleration_percent",
    lambda a: a.request_max_acceleration_percent(),
    lambda a: a.arm.request_max_acceleration_percent(),
  ),
  (
    "request_max_deceleration_percent",
    lambda a: a.request_max_deceleration_percent(),
    lambda a: a.arm.request_max_deceleration_percent(),
  ),
  ("request_base", lambda a: a.request_base(), lambda a: a.arm.request_base()),
  ("set_base", lambda a: a.set_base(1, 2, 3, 4), lambda a: a.arm.set_base(1, 2, 3, 4)),
  (
    "request_tool_transformation_values",
    lambda a: a.request_tool_transformation_values(),
    lambda a: a.arm.request_tool_transformation_values(),
  ),
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
    "drop_at_location",
    lambda a: a.drop_at_location(_LOC, direction=0.0, resource_width=85.0),
    lambda a: a.arm.drop_at_location(_LOC, direction=0.0, resource_width=85.0),
  ),
  ("park", lambda a: a.park(), lambda a: a.arm.park()),
]


class TestDeprecatedPercentKeywords(unittest.IsolatedAsyncioTestCase):
  """A `*_pct` keyword still works, warns, and sends what its `*_percent` successor sends."""

  def setUp(self) -> None:
    sleep = patch(
      "pylabrobot.brooks.precise_flex.driver.features.arm.asyncio.sleep", new=AsyncMock()
    )
    sleep.start()
    self.addCleanup(sleep.stop)

  async def _sent(
    self, call: Callable[[PreciseFlexDriver], Awaitable[Any]], out_of_range: bool
  ) -> List[str]:
    fake = _FakeController({"Speed 1": "0 1 60"})
    arm = _make_arm(fake)
    await arm.setup()
    fake.sent.clear()
    if out_of_range:  # so recovery sets its speed
      fake._replies["wherej"] = "0 301.02 93.476 179.609 83.93 125.867"
    await call(arm)
    return fake.sent

  async def test_each_deprecated_keyword(self):
    for name, old, new in _DEPRECATED_KEYWORDS:
      with self.subTest(name):
        out_of_range = name == "recover_axes_within_limits"
        expected = await self._sent(new, out_of_range)
        with self.assertWarns(DeprecationWarning):
          sent = await self._sent(old, out_of_range)
        self.assertEqual(sent, expected)


_MOVED_TO_FEATURES: List[
  Tuple[str, Callable[[PreciseFlexDriver], Awaitable[Any]], Callable[..., Any]]
] = [
  (
    "request_joint_position",
    lambda a: a.request_joint_position(),
    lambda a: a.arm.request_joint_state(),
  ),
  ("request_state", lambda a: a.request_state(), lambda a: a.arm.request_state()),
  (
    "request_gripper_pose",
    lambda a: a.request_gripper_pose(),
    lambda a: a.arm.request_gripper_pose(),
  ),
  (
    "move_to_joint_position",
    lambda a: a.move_to_joint_position(_J, speed_pct=30),
    lambda a: a.arm.move_to_joint_state(_J, speed_percent=30),
  ),
  (
    "move_to_location",
    lambda a: a.move_to_location(_LOC, 0.0),
    lambda a: a.arm.move_to_location(_LOC, 0.0),
  ),
  (
    "move_through_cartesian_poses",
    lambda a: a.move_through_cartesian_poses(_POSES),
    lambda a: a.arm.move_through_cartesian_poses(_POSES),
  ),
  (
    "recover_axes_within_limits",
    lambda a: a.recover_axes_within_limits(),
    lambda a: a.arm.recover_axes_within_limits(),
  ),
  ("dest_c", lambda a: a.dest_c(), lambda a: a.arm._unchecked_fw_request_cartesian_destination()),
  ("dest_j", lambda a: a.dest_j(), lambda a: a.arm.request_destination_joint_state()),
  ("here_j", lambda a: a.here_j(2), lambda a: a.arm.set_station_to_current_joint_state(2)),
  (
    "here_c",
    lambda a: a.here_c(2),
    lambda a: a.arm._unchecked_fw_set_station_to_current_cartesian_location(2),
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
  ("is_gripper_closed", lambda a: a.is_gripper_closed(), lambda a: a.gripper.sense_fully_closed()),
  (
    "request_monitor_speed",
    lambda a: a.request_monitor_speed(),
    lambda a: a.arm.request_monitor_speed(),
  ),
  ("set_monitor_speed", lambda a: a.set_monitor_speed(50), lambda a: a.arm.set_monitor_speed(50)),
  ("request_payload", lambda a: a.request_payload(), lambda a: a.arm.request_payload()),
  ("set_payload", lambda a: a.set_payload(25), lambda a: a.arm.set_payload(25)),
  (
    "request_profile_speed",
    lambda a: a.request_profile_speed(1),
    lambda a: a.arm.request_profile_speed(1),
  ),
  (
    "set_profile_speed",
    lambda a: a.set_profile_speed(1, 40),
    lambda a: a.arm.set_profile_speed(1, 40),
  ),
  (
    "request_profile_speed2",
    lambda a: a.request_profile_speed2(1),
    lambda a: a.arm.request_profile_speed2(1),
  ),
  (
    "set_profile_speed2",
    lambda a: a.set_profile_speed2(1, 30),
    lambda a: a.arm.set_profile_speed2(1, 30),
  ),
  (
    "request_profile_acceleration",
    lambda a: a.request_profile_acceleration(1),
    lambda a: a.arm.request_profile_acceleration(1),
  ),
  (
    "set_profile_acceleration",
    lambda a: a.set_profile_acceleration(1, 60),
    lambda a: a.arm.set_profile_acceleration(1, 60),
  ),
  (
    "request_profile_acceleration_ramp",
    lambda a: a.request_profile_acceleration_ramp(1),
    lambda a: a.arm.request_profile_acceleration_ramp(1),
  ),
  (
    "set_profile_acceleration_ramp",
    lambda a: a.set_profile_acceleration_ramp(1, 0.2),
    lambda a: a.arm.set_profile_acceleration_ramp(1, 0.2),
  ),
  (
    "request_profile_deceleration",
    lambda a: a.request_profile_deceleration(1),
    lambda a: a.arm.request_profile_deceleration(1),
  ),
  (
    "set_profile_deceleration",
    lambda a: a.set_profile_deceleration(1, 70),
    lambda a: a.arm.set_profile_deceleration(1, 70),
  ),
  (
    "request_profile_deceleration_ramp",
    lambda a: a.request_profile_deceleration_ramp(1),
    lambda a: a.arm.request_profile_deceleration_ramp(1),
  ),
  (
    "set_profile_deceleration_ramp",
    lambda a: a.set_profile_deceleration_ramp(1, 0.3),
    lambda a: a.arm.set_profile_deceleration_ramp(1, 0.3),
  ),
  (
    "request_profile_in_range",
    lambda a: a.request_profile_in_range(1),
    lambda a: a.arm.request_profile_in_range(1),
  ),
  (
    "set_profile_in_range",
    lambda a: a.set_profile_in_range(1, 10),
    lambda a: a.arm.set_profile_in_range(1, 10),
  ),
  (
    "request_profile_straight",
    lambda a: a.request_profile_straight(1),
    lambda a: a.arm.request_profile_straight(1),
  ),
  (
    "set_profile_straight",
    lambda a: a.set_profile_straight(1, True),
    lambda a: a.arm.set_profile_straight(1, True),
  ),
  (
    "request_motion_profile_values",
    lambda a: a.request_motion_profile_values(1),
    lambda a: a.arm.request_motion_profile_values(1),
  ),
  (
    "set_motion_profile_values",
    lambda a: a.set_motion_profile_values(1, 40, 30, 60, 70, 0.2, 0.3, 10, True),
    lambda a: a.arm.set_motion_profile_values(1, 40, 30, 60, 70, 0.2, 0.3, 10, True),
  ),
]


class TestDeprecatedDriverMembers(unittest.IsolatedAsyncioTestCase):
  """A driver member that moved to a feature still works, warns, and sends what its successor sends."""

  def setUp(self) -> None:
    sleep = patch(
      "pylabrobot.brooks.precise_flex.driver.features.arm.asyncio.sleep", new=AsyncMock()
    )
    sleep.start()
    self.addCleanup(sleep.stop)

  async def _sent(
    self, call: Callable[[PreciseFlexDriver], Awaitable[Any]], has_rail: bool = False
  ):
    fake = _FakeController(_RAIL_REPLIES if has_rail else None)
    arm = _make_arm(fake, has_rail=has_rail)
    await arm.setup()
    fake.sent.clear()
    result = await call(arm)
    return fake.sent, repr(result)  # a pose compares by its fields only through its repr

  async def test_each_moved_method(self):
    for name, old, new in _MOVED_TO_FEATURES:
      with self.subTest(name):
        expected = await self._sent(new)
        with self.assertWarns(DeprecationWarning):
          got = await self._sent(old)
        self.assertEqual(got, expected)

  async def test_move_rail(self):
    expected = await self._sent(lambda a: _rail(a).move_rail(250.0), has_rail=True)
    with self.assertWarns(DeprecationWarning):
      got = await self._sent(lambda a: a.move_rail(250.0), has_rail=True)
    self.assertEqual(got, expected)
    with self.assertWarns(DeprecationWarning), self.assertRaises(RuntimeError):
      await self._sent(lambda a: a.move_rail(250.0))

  def test_pick_and_parking_attributes(self):
    arm = _make_arm(_FakeController())
    for name, successor, value in (
      ("location_index", "station_index", 3),
      ("horizontal_compliance", "horizontal_compliance", True),
      ("horizontal_compliance_torque", "horizontal_compliance_torque", 20),
      ("parking_position", "parking_position", {PreciseFlexAxis.SHOULDER: 0.0}),
    ):
      with self.subTest(name):
        with self.assertWarns(DeprecationWarning):
          setattr(arm, name, value)
        self.assertEqual(getattr(arm.arm, successor), value)
        with self.assertWarns(DeprecationWarning):
          self.assertEqual(getattr(arm, name), value)
    for name in ("PARKING_POSITION_BACK", "PARKING_POSITION_RIGHT", "PARKING_POSITION_FRONT"):
      self.assertIs(getattr(PreciseFlexDriver, name), getattr(PreciseFlexArm, name))

  def test_the_driver_class_was_preciseflex(self):
    with self.assertWarns(DeprecationWarning):
      arm = PreciseFlex(
        host="pf400", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=80.0
      )
    self.assertIsInstance(arm, PreciseFlexDriver)

  def test_profile_index(self):
    arm = _make_arm(_FakeController())
    with self.assertWarns(DeprecationWarning):
      arm.profile_index = 2
    self.assertEqual(arm.arm.profile_index, 2)
    with self.assertWarns(DeprecationWarning):
      self.assertEqual(arm.profile_index, 2)

  async def test_gripper_attributes(self):
    arm = _make_arm(_FakeController())
    with self.assertWarns(DeprecationWarning):
      self.assertEqual(arm.min_gripper_width, arm.gripper.jaw_width_range[0])
    with self.assertWarns(DeprecationWarning):
      arm.max_gripper_width = 150.0
    self.assertEqual(arm.gripper.jaw_width_range, (60.0, 150.0))
    with self.assertWarns(DeprecationWarning):
      arm.closed_gripper_position = 90.0
    self.assertEqual(arm.gripper.closed_gripper_position, 90.0)

  def test_old_module_paths(self):
    import importlib
    import sys

    for old, name in (
      ("pylabrobot.brooks.precise_flex.precise_flex", "PreciseFlexDriver"),
      ("pylabrobot.brooks.precise_flex.config", "PreciseFlexConfiguration"),
      ("pylabrobot.brooks.precise_flex.errors", "PreciseFlexError"),
    ):
      with self.subTest(old):
        sys.modules.pop(old, None)
        with self.assertWarns(DeprecationWarning):
          module = importlib.import_module(old)
        self.assertTrue(hasattr(module, name))

  def test_joint_pose(self):
    from pylabrobot.brooks.precise_flex import kinematics

    with self.assertWarns(DeprecationWarning):
      self.assertIs(kinematics.JointPose, kinematics.JointState)

  def test_axis(self):
    from pylabrobot.brooks import precise_flex
    from pylabrobot.brooks.precise_flex import kinematics

    for module in (precise_flex, kinematics):
      with self.subTest(module.__name__), self.assertWarns(DeprecationWarning):
        self.assertIs(module.Axis, kinematics.PreciseFlexAxis)


if __name__ == "__main__":
  unittest.main()
