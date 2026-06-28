import unittest
from typing import Tuple
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.brooks.precise_flex import (
  Axis,
  OutOfRangeOfMotionError,
  PreciseFlexArmBackend,
  PreciseFlexCartesianPose,
)
from pylabrobot.resources import Coordinate, Rotation


def _make_backend(
  closed_gripper_position: float = 500.0,
) -> Tuple[PreciseFlexArmBackend, MagicMock]:
  driver = MagicMock()
  driver.send_command = AsyncMock(return_value="")
  driver.io._host = "localhost"
  backend = PreciseFlexArmBackend(
    driver=driver,
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=closed_gripper_position,
  )
  return backend, driver


class TestPreciseFlex400Gripper(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    # closed_gripper_position=500 ⇒ min_gripper_width(60mm) maps to 500 units.
    self.backend, self.driver = _make_backend(closed_gripper_position=500.0)

  def _sent_commands(self) -> list[str]:
    return [c.args[0] for c in self.driver.send_command.call_args_list]

  async def test_move_gripper_force_sensing_false_opens_with_position(self):
    # 80 mm ⇒ 500 + (80 - 60) = 520 firmware units.
    await self.backend.move_gripper(width=80.0, force_sensing=False)
    self.assertEqual(self._sent_commands(), ["GripOpenPos 520.0", "gripper 1"])

  async def test_move_gripper_force_sensing_true_closes_with_position(self):
    # 60 mm (the closed reference) ⇒ exactly closed_gripper_position.
    await self.backend.move_gripper(width=60.0, force_sensing=True)
    self.assertEqual(self._sent_commands(), ["GripClosePos 500.0", "gripper 2"])

  async def test_move_gripper_position_command_precedes_move(self):
    await self.backend.move_gripper(width=120.0, force_sensing=False)
    commands = self._sent_commands()
    self.assertLess(
      commands.index("GripOpenPos 560.0"),
      commands.index("gripper 1"),
      "Position must be set before the gripper move command fires.",
    )

  async def test_force_sensing_branches_use_different_firmware_commands(self):
    await self.backend.move_gripper(width=90.0, force_sensing=False)
    await self.backend.move_gripper(width=90.0, force_sensing=True)
    commands = self._sent_commands()
    self.assertIn("gripper 1", commands)
    self.assertIn("gripper 2", commands)
    self.assertIn("GripOpenPos 530.0", commands)
    self.assertIn("GripClosePos 530.0", commands)

  async def test_min_max_gripper_width_advertised(self):
    self.assertEqual(self.backend.min_gripper_width, 60.0)
    self.assertEqual(self.backend.max_gripper_width, 145.0)

  async def test_closed_gripper_position_shifts_units(self):
    # Different anchor ⇒ same width yields a different firmware-unit target.
    backend, driver = _make_backend(closed_gripper_position=1000.0)
    await backend.move_gripper(width=80.0, force_sensing=False)
    commands = [c.args[0] for c in driver.send_command.call_args_list]
    # 80 mm ⇒ 1000 + (80 - 60) = 1020 units.
    self.assertEqual(commands, ["GripOpenPos 1020.0", "gripper 1"])

  def test_mm_to_firmware_units_helper(self):
    # Direct check of the linear mapping.
    self.assertEqual(self.backend._mm_to_firmware_units(60.0), 500.0)
    self.assertEqual(self.backend._mm_to_firmware_units(145.0), 585.0)
    self.assertEqual(self.backend._mm_to_firmware_units(100.0), 540.0)


class TestPreciseFlex400OutOfRangeRecovery(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend, self.driver = _make_backend()
    self.driver._wait_for_eom = AsyncMock()
    # Minimal stub configuration: only the soft limits the recovery logic reads.
    self.backend._configuration = MagicMock(
      soft_limits={
        Axis.SHOULDER: (-93.0, 93.0),
        Axis.ELBOW: (12.0, 348.0),
        Axis.WRIST: (-960.0, 960.0),
      }
    )

  def _stub_transport(self, wherej: str) -> None:
    """Stub the driver: ``wherej`` returns ``wherej``, ``Speed`` a 50% profile, other writes no-op.

    The recovery logic reads the live pose and profile speed over the transport, so we feed those
    rather than reassigning backend methods (mirrors the STAR tests' driver-boundary mocking).
    """

    async def respond(command: str) -> str:
      if command == "wherej":
        return wherej
      if command.startswith("Speed "):
        return f"{self.backend.profile_index} 50.0"
      return ""

    self.driver.send_command = AsyncMock(side_effect=respond)

  def _move_one_axis_cmds(self) -> list[str]:
    return [
      c.args[0]
      for c in self.driver.send_command.call_args_list
      if c.args[0].startswith("MoveOneAxis")
    ]

  async def test_recover_moves_offenders_toward_limit_in_order_and_skips_wrist(self):
    """Each recoverable offender is driven 1 unit *inside* the violated limit (above-max down,
    below-min up), shoulder before elbow per _RECOVERY_ORDER; the wrist is never auto-moved."""
    # wherej (no rail): base shoulder elbow wrist gripper - shoulder/elbow/wrist out of range.
    self._stub_transport("0 93.5 9.0 962.0 0")
    recovered = await self.backend.recover_axes_within_limits()
    self.assertEqual(recovered, {Axis.SHOULDER: 92.0, Axis.ELBOW: 13.0})  # wrist excluded
    self.assertEqual(
      self._move_one_axis_cmds(), ["MoveOneAxis 2 92.0 1", "MoveOneAxis 3 13.0 1"]
    )  # shoulder (2) before elbow (3)

  async def test_recover_skips_axis_too_far_out_of_range(self):
    """An axis past its limit by more than max_distance is left in place (no unattended big sweep)."""
    # shoulder 120 deg is 27 past the 93 limit, beyond the 5 cap; elbow/wrist in range.
    self._stub_transport("0 120.0 30.0 0.0 0")
    recovered = await self.backend.recover_axes_within_limits()
    self.assertEqual(recovered, {})
    self.assertEqual(self._move_one_axis_cmds(), [])


class TestPreciseFlexParking(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend, self.driver = _make_backend()
    self.driver._wait_for_eom = AsyncMock()

  def _full_soft_limits(self) -> MagicMock:
    return MagicMock(
      z_range=(0.0, 400.0),
      soft_limits={
        Axis.BASE: (0.0, 400.0),
        Axis.SHOULDER: (-93.0, 93.0),
        Axis.ELBOW: (12.0, 348.0),
        Axis.WRIST: (-960.0, 960.0),
      },
    )

  def _movej_cmds(self) -> list[str]:
    return [
      c.args[0] for c in self.driver.send_command.call_args_list if c.args[0].startswith("moveJ")
    ]

  def test_named_constants_are_orientation_only_planar_folds(self):
    """The three parking orientations are planar folds (ELBOW 180) that never pin Z (Axis.BASE), so
    one orientation works on any reach; they differ only in which way the gripper faces."""
    for pose in (
      PreciseFlexArmBackend.PARKING_POSITION_BACK,
      PreciseFlexArmBackend.PARKING_POSITION_RIGHT,
      PreciseFlexArmBackend.PARKING_POSITION_FRONT,
    ):
      self.assertNotIn(Axis.BASE, pose)
      self.assertEqual(pose[Axis.ELBOW], 180.0)
    self.assertEqual(PreciseFlexArmBackend.PARKING_POSITION_BACK[Axis.SHOULDER], 90.0)
    self.assertEqual(PreciseFlexArmBackend.PARKING_POSITION_FRONT[Axis.SHOULDER], -90.0)

  def test_assignment_rejects_non_axis_keys(self):
    """The validating setter refuses a pose keyed by anything but Axis members."""
    bad_pose: dict = {"base": 100.0}
    with self.assertRaises(ValueError):
      self.backend.parking_position = bad_pose

  def test_assignment_rejects_out_of_limit_value_once_configured(self):
    """Once the soft limits are known, a value outside them is rejected at assignment."""
    self.backend._configuration = MagicMock(soft_limits={Axis.SHOULDER: (-93.0, 93.0)})
    with self.assertRaises(ValueError):
      self.backend.parking_position = {Axis.SHOULDER: 200.0}

  def test_assignment_accepts_named_constant(self):
    """A named constant assigns cleanly and round-trips through the getter."""
    self.backend.parking_position = PreciseFlexArmBackend.PARKING_POSITION_FRONT
    pose = self.backend.parking_position
    assert pose is not None
    self.assertEqual(pose[Axis.SHOULDER], -90.0)

  async def test_park_fills_z_at_three_quarters_travel_and_keeps_orientation(self):
    """park() fills the omitted Z column at 3/4 of the discovered travel and keeps the orientation."""
    self.backend._configuration = self._full_soft_limits()
    # Current pose deliberately differs from the target (base 50 not 300; orientation 10/200/90 not
    # 0/180/180) so the assertion proves park() supplied the fill and orientation, not the live pose.
    self.driver.send_command = AsyncMock(return_value="50 10 200 90 0")
    self.backend.parking_position = PreciseFlexArmBackend.PARKING_POSITION_RIGHT
    await self.backend.park()
    # Z filled at 3/4 of 400 = 300; orientation = RIGHT (0/180/180); gripper carried from current.
    self.assertEqual(self._movej_cmds(), ["moveJ 1 300.0 0.0 180.0 180.0 0.0"])

  async def test_park_respects_an_explicit_base(self):
    """A pose that already sets Axis.BASE is parked as-is (no Z fill)."""
    self.backend._configuration = self._full_soft_limits()
    # base 50 in the current pose so the explicit 123 (neither the 300 fill nor the live 50) proves
    # the supplied base is honored and not Z-filled; elbow/wrist carry from current.
    self.driver.send_command = AsyncMock(return_value="50 10 200 90 0")
    self.backend.parking_position = {Axis.BASE: 123.0, Axis.SHOULDER: 0.0}
    await self.backend.park()
    self.assertEqual(self._movej_cmds(), ["moveJ 1 123.0 0.0 200.0 90.0 0.0"])

  async def test_park_without_position_falls_back_to_movetosafe(self):
    """While parking_position is unset (no configuration), park() uses the firmware movetosafe."""
    await self.backend.park()
    self.driver.send_command.assert_awaited_once_with("movetosafe")
    self.assertEqual(self._movej_cmds(), [])


class TestPreciseFlexSmoothCartesianRoute(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.backend, self.driver = _make_backend()
    self.driver._wait_for_eom = AsyncMock()
    self.current_joints = {
      Axis.BASE: 100.0,
      Axis.SHOULDER: 0.0,
      Axis.ELBOW: 180.0,
      Axis.WRIST: 180.0,
      Axis.GRIPPER: 70.0,
    }
    self.current_pose = PreciseFlexCartesianPose(
      location=Coordinate(10.0, 20.0, 100.0),
      rotation=Rotation(x=-180.0, y=90.0, z=0.0),
      rail_position=123.0,
      orientation="right",
      wrist="ccw",
    )
    self.backend._request_state = AsyncMock(return_value=(self.current_joints, self.current_pose))

  def _stub_profile_transport(self, profile: str = "1 50 0 100 100 0 0 25 0") -> None:
    async def respond(command: str) -> str:
      if command == "Profile 1":
        return profile
      return ""

    self.driver.send_command = AsyncMock(side_effect=respond)

  def _movej_cmds(self) -> list[str]:
    return [
      c.args[0] for c in self.driver.send_command.call_args_list if c.args[0].startswith("moveJ")
    ]

  def _profile_cmds(self) -> list[str]:
    return [
      c.args[0]
      for c in self.driver.send_command.call_args_list
      if c.args[0].startswith("Profile")
    ]

  async def test_move_through_cartesian_poses_plans_from_one_state_snapshot(self):
    """A smooth route snapshots state once, fills omitted pose fields from the planned pose,
    queues all joint moves, then waits once at the end."""
    self._stub_profile_transport()
    poses = [
      PreciseFlexCartesianPose(
        location=Coordinate(200.0, 20.0, 110.0),
        rotation=Rotation(x=-180.0, y=90.0, z=10.0),
      ),
      PreciseFlexCartesianPose(
        location=Coordinate(210.0, 20.0, 120.0),
        rotation=Rotation(x=-180.0, y=90.0, z=20.0),
      ),
    ]

    with patch(
      "pylabrobot.brooks.precise_flex.arm_backend.kinematics.ik",
      side_effect=[
        {1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
        {1: 120.0, 2: 11.0, 3: 21.0, 4: 31.0, 6: 123.0},
      ],
    ) as ik:
      await self.backend.move_through_cartesian_poses(poses)

    self.backend._request_state.assert_awaited_once()
    self.driver._wait_for_eom.assert_awaited_once()
    self.assertEqual(
      self._movej_cmds(),
      [
        "moveJ 1 110.0 10.0 20.0 30.0 70.0",
        "moveJ 1 120.0 11.0 21.0 31.0 70.0",
      ],
    )
    planned_pose_args = [call.args[0] for call in ik.call_args_list]
    self.assertEqual([pose.orientation for pose in planned_pose_args], ["right", "right"])
    self.assertEqual([pose.wrist for pose in planned_pose_args], ["ccw", "ccw"])
    # Rail-less PF400 still needs the shoulder/reference rail position for IK.
    self.assertEqual([pose.rail_position for pose in planned_pose_args], [123.0, 123.0])

  async def test_move_through_cartesian_poses_temporarily_enables_blending(self):
    self._stub_profile_transport()
    pose = PreciseFlexCartesianPose(
      location=Coordinate(200.0, 20.0, 110.0),
      rotation=Rotation(x=-180.0, y=90.0, z=10.0),
    )

    with patch(
      "pylabrobot.brooks.precise_flex.arm_backend.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      await self.backend.move_through_cartesian_poses([pose])

    self.assertEqual(
      self._profile_cmds(),
      [
        "Profile 1",
        "Profile 1 50.0 0.0 100.0 100.0 0.0 0.0 -1 0",
        "Profile 1 50.0 0.0 100.0 100.0 0.0 0.0 25.0 0",
      ],
    )

  async def test_move_through_cartesian_poses_can_skip_profile_blending(self):
    pose = PreciseFlexCartesianPose(
      location=Coordinate(200.0, 20.0, 110.0),
      rotation=Rotation(x=-180.0, y=90.0, z=10.0),
    )

    with patch(
      "pylabrobot.brooks.precise_flex.arm_backend.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      await self.backend.move_through_cartesian_poses(
        [pose],
        backend_params=PreciseFlexArmBackend.MoveThroughCartesianPosesParams(blend=False),
      )

    self.assertEqual(self._profile_cmds(), [])
    self.assertEqual(self._movej_cmds(), ["moveJ 1 110.0 10.0 20.0 30.0 70.0"])
    self.driver._wait_for_eom.assert_awaited_once()

  async def test_move_through_cartesian_poses_blocks_before_motion_on_limit_failure(self):
    pose = PreciseFlexCartesianPose(
      location=Coordinate(200.0, 20.0, 110.0),
      rotation=Rotation(x=-180.0, y=90.0, z=10.0),
    )
    self.backend._assert_within_soft_limits = MagicMock(side_effect=ValueError("bad target"))

    with patch(
      "pylabrobot.brooks.precise_flex.arm_backend.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      with self.assertRaisesRegex(ValueError, "bad target"):
        await self.backend.move_through_cartesian_poses([pose])

    self.assertEqual(self._movej_cmds(), [])
    self.assertEqual(self._profile_cmds(), [])
    self.driver._wait_for_eom.assert_not_awaited()


_LOGGER = "pylabrobot.brooks.precise_flex.arm_backend"


class TestPreciseFlex400AutoRecoverOnMove(unittest.IsolatedAsyncioTestCase):
  """A commanded move that finds an axis out of range: default recovers; opt-out raises."""

  def setUp(self):
    self.backend, self.driver = _make_backend()
    self.driver._wait_for_eom = AsyncMock()
    self.backend._configuration = MagicMock(
      soft_limits={
        Axis.SHOULDER: (-93.0, 93.0),
        Axis.ELBOW: (12.0, 348.0),
        Axis.WRIST: (-960.0, 960.0),
      }
    )

  def _stub(self, out_of_range: str, recovered: str = "") -> None:
    """wherej returns ``out_of_range`` until a MoveOneAxis fires, then ``recovered`` (if given)."""
    state = {"recovered": False}

    async def respond(command: str) -> str:
      if command == "wherej":
        return recovered if (state["recovered"] and recovered) else out_of_range
      if command.startswith("Speed "):
        return f"{self.backend.profile_index} 50.0"
      if command.startswith("MoveOneAxis"):
        state["recovered"] = True
      return ""

    self.driver.send_command = AsyncMock(side_effect=respond)

  def _cmds(self, prefix: str) -> list[str]:
    return [
      c.args[0] for c in self.driver.send_command.call_args_list if c.args[0].startswith(prefix)
    ]

  async def test_opted_out_raises_and_does_not_move_or_recover(self):
    """Opted out: an out-of-range axis raises OutOfRangeOfMotionError; no recovery, no moveJ."""
    self.backend._recover_out_of_range = False
    self._stub("0 93.5 90.0 0.0 0")  # base shoulder elbow wrist gripper; shoulder 93.5 > 93
    with self.assertRaises(OutOfRangeOfMotionError) as ctx:
      await self.backend.move_to_joint_position({Axis.SHOULDER: 0.0})
    self.assertIn(Axis.SHOULDER, ctx.exception.axes)
    self.assertEqual(self._cmds("MoveOneAxis"), [])
    self.assertEqual(self._cmds("moveJ"), [])

  async def test_on_recovers_offender_then_retries_move(self):
    """Opt-in on: the offending axis is nudged in range (MoveOneAxis), then the moveJ is retried."""
    self.backend._recover_out_of_range = True
    self._stub("0 93.5 90.0 0.0 0", recovered="0 92.0 90.0 0.0 0")
    with self.assertLogs(_LOGGER, level="INFO") as cm:
      await self.backend.move_to_joint_position({Axis.SHOULDER: 0.0})
    self.assertEqual(self._cmds("MoveOneAxis"), ["MoveOneAxis 2 92.0 1"])  # shoulder back in range
    self.assertEqual(len(self._cmds("moveJ")), 1)  # move retried and sent
    log = "\n".join(cm.output)
    self.assertIn("commanded move blocked", log)  # WARNING on entry
    self.assertIn("retried successfully", log)  # INFO on success

  async def test_on_but_unrecoverable_reraises_once_without_moving(self):
    """Opt-in on but the axis is too far out (recovery skips it): re-raise after one try, no loop."""
    self.backend._recover_out_of_range = True
    self._stub("0 120.0 90.0 0.0 0")  # shoulder 27 past the limit, beyond the recovery cap
    with self.assertLogs(_LOGGER, level="ERROR") as cm:
      with self.assertRaises(OutOfRangeOfMotionError):
        await self.backend.move_to_joint_position({Axis.SHOULDER: 0.0})
    self.assertEqual(self._cmds("moveJ"), [])  # never moved
    self.assertIn("auto-recovery did not clear", "\n".join(cm.output))  # ERROR before re-raise

  async def test_in_range_move_reads_position_once(self):
    """Happy path: the out-of-range check reuses the merge read, so a move issues a single wherej
    before moveJ (no redundant position read)."""
    self._stub("0 0.0 90.0 0.0 0")  # all axes in range
    await self.backend.move_to_joint_position({Axis.SHOULDER: 10.0})
    self.assertEqual(self._cmds("wherej"), ["wherej"])  # exactly one position read
    self.assertEqual(len(self._cmds("moveJ")), 1)

  async def test_move_to_location_is_also_guarded(self):
    """The Cartesian path funnels through the same guard: an out-of-range axis raises and sends no
    moveJ, like the joint path. The IK target is stubbed - it is the guard wiring, not IK, pinned
    here."""
    self.backend._recover_out_of_range = False
    self._stub("0 93.5 90.0 0.0 0")  # current shoulder 93.5 > 93, out of range
    in_range = {
      Axis.BASE: 0.0,
      Axis.SHOULDER: 0.0,
      Axis.ELBOW: 90.0,
      Axis.WRIST: 0.0,
      Axis.GRIPPER: 0.0,
    }
    with patch.object(self.backend, "_cart_to_joints", AsyncMock(return_value=in_range)):
      with self.assertRaises(OutOfRangeOfMotionError) as ctx:
        await self.backend.move_to_location(Coordinate(400.0, 0.0, 200.0), 0.0)
    self.assertIn(Axis.SHOULDER, ctx.exception.axes)
    self.assertEqual(self._cmds("moveJ"), [])
