import asyncio
import unittest
from typing import Dict, Optional, cast
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.brooks.precise_flex import (
  OutOfRangeOfMotionError,
  PreciseFlexArm,
  PreciseFlexAxis,
  PreciseFlexCartesianPose,
  PreciseFlexDriver,
)
from pylabrobot.events import EventBus, PLREvent, event_context, use_event_bus
from pylabrobot.resources import Coordinate, Rotation


def mocked(method: object) -> AsyncMock:
  """A real method that a test replaced with an ``AsyncMock``.

  Assertions like ``call_args_list`` live on the mock, not on the declared
  method type, so they need narrowing before mypy will accept them.
  """
  return cast(AsyncMock, method)


def _make_arm(closed_gripper_position: float = 500.0) -> PreciseFlexDriver:
  """An arm whose transport is stubbed out, so tests assert on the commands it would send."""
  arm = PreciseFlexDriver(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=closed_gripper_position,
  )
  # Every command answers nothing, but a joint read: the arm's own, parked with its jaws closed.
  parked = "301.11 0.018 179.925 -180.644 70.685"
  arm.send_command = AsyncMock(  # type: ignore[method-assign]
    side_effect=lambda command: parked if command == "wherej" else ""
  )
  # Wide gripper limits, as if setup had read them; a gripper move refuses without any.
  arm.gripper.configuration = MagicMock(soft_limit_range=(0.0, 10_000.0))
  return arm


class TestPreciseFlexPose(unittest.IsolatedAsyncioTestCase):
  """Every joint's location, worked out from a joint state (l1 302, l2 289, gripper 162)."""

  def test_straight_out(self):
    arm = _make_arm()
    joints = {
      PreciseFlexAxis.BASE: 100.0,
      PreciseFlexAxis.SHOULDER: 0.0,
      PreciseFlexAxis.ELBOW: 0.0,
      PreciseFlexAxis.WRIST: 0.0,
    }
    pose = arm.arm._forward_kinematics(joints)
    self.assertEqual(pose.shoulder_joint_location, Coordinate(0.0, 0.0, 100.0))
    self.assertEqual(pose.elbow_joint_location, Coordinate(302.0, 0.0, 100.0))
    self.assertEqual(pose.wrist_joint_location, Coordinate(591.0, 0.0, 100.0))
    self.assertEqual(pose.gripper_pose.location, Coordinate(753.0, 0.0, 100.0))


class TestPreciseFlex400Gripper(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    # closed_gripper_position=500 ⇒ the narrowest jaw width (60mm) maps to 500 units.
    self.arm = _make_arm(closed_gripper_position=500.0)

  def _sent_commands(self) -> list[str]:
    """What was sent, less the joint reads a move waits on before it."""
    sent = [c.args[0] for c in mocked(self.arm.send_command).call_args_list]
    return [command for command in sent if command != "wherej"]

  async def test_move_gripper_force_sensing_false_opens_with_position(self):
    # 80 mm ⇒ 500 + (80 - 60) = 520 firmware units.
    await self.arm.gripper.move_to_jaw_position(width=80.0, force_sensing=False)
    self.assertEqual(self._sent_commands(), ["GripOpenPos 520.0", "gripper 1"])

  async def test_move_gripper_force_sensing_true_closes_with_position(self):
    # 60 mm (the closed reference) ⇒ exactly closed_gripper_position.
    await self.arm.gripper.move_to_jaw_position(width=60.0, force_sensing=True)
    self.assertEqual(self._sent_commands(), ["GripClosePos 500.0", "gripper 2"])

  async def test_move_gripper_position_command_precedes_move(self):
    await self.arm.gripper.move_to_jaw_position(width=120.0, force_sensing=False)
    commands = self._sent_commands()
    self.assertLess(
      commands.index("GripOpenPos 560.0"),
      commands.index("gripper 1"),
      "Position must be set before the gripper move command fires.",
    )

  async def test_force_sensing_branches_use_different_firmware_commands(self):
    await self.arm.gripper.move_to_jaw_position(width=90.0, force_sensing=False)
    await self.arm.gripper.move_to_jaw_position(width=90.0, force_sensing=True)
    self.assertEqual(
      self._sent_commands(), ["GripOpenPos 530.0", "gripper 1", "GripClosePos 530.0", "gripper 2"]
    )

  async def test_a_gripper_move_before_setup_is_refused(self):
    arm = _make_arm()
    arm.gripper.configuration = None
    for move in (
      lambda: arm.gripper.move_to_jaw_position(80.0, force_sensing=False),
      lambda: arm.gripper.move_to_jaw_position_firmware_units(520.0, force_sensing=False),
    ):
      with self.assertRaisesRegex(RuntimeError, "run setup"):
        await move()
    self.assertEqual([c.args[0] for c in mocked(arm.send_command).call_args_list], [])

  async def test_jaw_width_range_advertised(self):
    self.assertEqual(self.arm.gripper.jaw_width_range, (60.0, 145.0))

  async def test_closed_gripper_position_shifts_units(self):
    # Different anchor ⇒ same width yields a different firmware-unit target.
    arm = _make_arm(closed_gripper_position=1000.0)
    await arm.gripper.move_to_jaw_position(width=80.0, force_sensing=False)
    commands = [c.args[0] for c in mocked(arm.send_command).call_args_list]
    # 80 mm ⇒ 1000 + (80 - 60) = 1020 units.
    self.assertEqual(
      [command for command in commands if command != "wherej"], ["GripOpenPos 1020.0", "gripper 1"]
    )

  def test_mm_to_firmware_units_helper(self):
    # Direct check of the linear mapping.
    self.assertEqual(self.arm.gripper._mm_to_firmware_units(60.0), 500.0)
    self.assertEqual(self.arm.gripper._mm_to_firmware_units(145.0), 585.0)
    self.assertEqual(self.arm.gripper._mm_to_firmware_units(100.0), 540.0)


class TestPreciseFlexEvents(unittest.IsolatedAsyncioTestCase):
  async def test_gripper_event_uses_default_length_unit_field(self):
    arm = _make_arm()
    # The jaws stand closed, so the move opens them.
    arm.arm.request_joint_state = AsyncMock(return_value={PreciseFlexAxis.GRIPPER: 0.0})  # type: ignore[method-assign]
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await arm.gripper.move_to_jaw_position(width=80.0)

    self.assertEqual(events[0].data["width"], 80.0)
    self.assertNotIn("width_mm", events[0].data)

  async def test_gripper_event_and_nested_firmware_commands_inherit_resource_context(self):
    arm = PreciseFlexDriver(
      host="localhost",
      gripper_length=162.0,
      gripper_z_offset=0.0,
      closed_gripper_position=500.0,
    )
    arm.io.write = AsyncMock()  # type: ignore[method-assign]
    arm.io.readline = AsyncMock(return_value=b"0\n")  # type: ignore[method-assign]
    arm.gripper.configuration = MagicMock(soft_limit_range=(0.0, 10_000.0))
    arm.arm.request_joint_state = AsyncMock(return_value={PreciseFlexAxis.GRIPPER: 0.0})  # type: ignore[method-assign]
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with (
      use_event_bus(event_bus),
      event_context(
        resources=[{"name": "sample_plate"}],
        source={"name": "source_nest"},
        destination={"name": "destination_nest"},
      ),
    ):
      await arm.gripper.move_to_jaw_position_firmware_units(520.0)

    self.assertEqual(events[0].name, "precise_flex.move_gripper_joint_position.started")
    self.assertEqual(events[-1].name, "precise_flex.move_gripper_joint_position.completed")
    self.assertEqual(events[0].data["gripper_joint_position"], 520.0)
    self.assertEqual(events[0].data["device"]["name"], "precise_flex")
    self.assertEqual(events[0].context["resources"], [{"name": "sample_plate"}])
    self.assertEqual(events[0].context["source"], {"name": "source_nest"})
    self.assertEqual(events[0].context["destination"], {"name": "destination_nest"})

    firmware_events = [
      event for event in events if event.name == "precise_flex.firmware_command.started"
    ]
    self.assertEqual(
      [event.data["command"] for event in firmware_events],
      ["GripOpenPos 520.0", "gripper 1"],
    )
    self.assertTrue(
      all(event.context["resources"] == [{"name": "sample_plate"}] for event in firmware_events)
    )


def _stub_soft_limits(
  driver: PreciseFlexDriver,
  soft_limits: Dict[PreciseFlexAxis, tuple],
  z_range: Optional[tuple] = None,
) -> None:
  """Give the arm stub configuration carrying only its soft limits; the gripper is unconstrained."""
  driver.arm.configuration = MagicMock(soft_limits=soft_limits, z_range=z_range)
  driver.gripper.configuration = MagicMock(soft_limit_range=(float("-inf"), float("inf")))


class TestPreciseFlex400OutOfRangeRecovery(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.arm = _make_arm()
    self.arm.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
    # Minimal stub configuration: only the soft limits the recovery logic reads.
    _stub_soft_limits(
      self.arm,
      {
        PreciseFlexAxis.SHOULDER: (-93.0, 93.0),
        PreciseFlexAxis.ELBOW: (12.0, 348.0),
        PreciseFlexAxis.WRIST: (-960.0, 960.0),
      },
    )

  def _stub_transport(self, wherej: str) -> None:
    """Stub the transport: ``wherej`` returns ``wherej``, ``Speed`` a 50% profile, other writes no-op.

    The recovery logic reads the live pose and profile speed over the transport, so we feed those
    rather than reassigning the arm's own methods.
    """

    async def respond(command: str) -> str:
      if command == "wherej":
        return wherej
      if command.startswith("Speed "):
        return f"{self.arm.arm.profile_index} 50.0"
      return ""

    self.arm.send_command = AsyncMock(side_effect=respond)  # type: ignore[method-assign]

  def _move_one_axis_cmds(self) -> list[str]:
    return [
      c.args[0]
      for c in mocked(self.arm.send_command).call_args_list
      if c.args[0].startswith("MoveOneAxis")
    ]

  async def test_recover_moves_offenders_toward_limit_in_order_and_skips_wrist(self):
    """Each recoverable offender is driven 1 unit *inside* the violated limit (above-max down,
    below-min up), shoulder before elbow per _RECOVERY_ORDER; the wrist is never auto-moved."""
    # wherej (no rail): base shoulder elbow wrist gripper - shoulder/elbow/wrist out of range.
    self._stub_transport("0 93.5 9.0 962.0 0")
    recovered = await self.arm.arm.recover_axes_within_limits()
    self.assertEqual(
      recovered, {PreciseFlexAxis.SHOULDER: 92.0, PreciseFlexAxis.ELBOW: 13.0}
    )  # wrist excluded
    self.assertEqual(
      self._move_one_axis_cmds(), ["MoveOneAxis 2 92.0 1", "MoveOneAxis 3 13.0 1"]
    )  # shoulder (2) before elbow (3)

  async def test_recover_skips_axis_too_far_out_of_range(self):
    """An axis past its limit by more than max_distance is left in place (no unattended big sweep)."""
    # shoulder 120 deg is 27 past the 93 limit, beyond the 5 cap; elbow/wrist in range.
    self._stub_transport("0 120.0 30.0 0.0 0")
    recovered = await self.arm.arm.recover_axes_within_limits()
    self.assertEqual(recovered, {})
    self.assertEqual(self._move_one_axis_cmds(), [])


class TestPreciseFlexParking(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.arm = _make_arm()
    self.arm.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]

  def _full_soft_limits(self) -> None:
    _stub_soft_limits(
      self.arm,
      {
        PreciseFlexAxis.BASE: (0.0, 400.0),
        PreciseFlexAxis.SHOULDER: (-93.0, 93.0),
        PreciseFlexAxis.ELBOW: (12.0, 348.0),
        PreciseFlexAxis.WRIST: (-960.0, 960.0),
      },
      z_range=(0.0, 400.0),
    )

  def _movej_cmds(self) -> list[str]:
    return [
      c.args[0]
      for c in mocked(self.arm.send_command).call_args_list
      if c.args[0].startswith("moveJ")
    ]

  def test_named_constants_are_orientation_only_planar_folds(self):
    """The three parking orientations are planar folds (ELBOW 180) that never pin Z (PreciseFlexAxis.BASE), so
    one orientation works on any reach; they differ only in which way the gripper faces."""
    for pose in (
      PreciseFlexArm.PARKING_POSITION_BACK,
      PreciseFlexArm.PARKING_POSITION_RIGHT,
      PreciseFlexArm.PARKING_POSITION_FRONT,
    ):
      self.assertNotIn(PreciseFlexAxis.BASE, pose)
      self.assertEqual(pose[PreciseFlexAxis.ELBOW], 180.0)
    self.assertEqual(PreciseFlexArm.PARKING_POSITION_BACK[PreciseFlexAxis.SHOULDER], 90.0)
    self.assertEqual(PreciseFlexArm.PARKING_POSITION_FRONT[PreciseFlexAxis.SHOULDER], -90.0)

  def test_assignment_rejects_non_axis_keys(self):
    """The validating setter refuses a pose keyed by anything but PreciseFlexAxis members."""
    bad_pose: dict = {"base": 100.0}
    with self.assertRaises(ValueError):
      self.arm.arm.parking_position = bad_pose

  def test_assignment_rejects_out_of_limit_value_once_configured(self):
    """Once the soft limits are known, a value outside them is rejected at assignment."""
    _stub_soft_limits(self.arm, {PreciseFlexAxis.SHOULDER: (-93.0, 93.0)})
    with self.assertRaises(ValueError):
      self.arm.arm.parking_position = {PreciseFlexAxis.SHOULDER: 200.0}

  def test_assignment_accepts_named_constant(self):
    """A named constant assigns cleanly and round-trips through the getter."""
    self.arm.arm.parking_position = PreciseFlexArm.PARKING_POSITION_FRONT
    pose = self.arm.arm.parking_position
    assert pose is not None
    self.assertEqual(pose[PreciseFlexAxis.SHOULDER], -90.0)

  async def test_park_fills_z_at_three_quarters_travel_and_keeps_orientation(self):
    """park() fills the omitted Z column at 3/4 of the discovered travel and keeps the orientation."""
    self._full_soft_limits()
    # Current pose deliberately differs from the target (base 50 not 300; orientation 10/200/90 not
    # 0/180/180) so the assertion proves park() supplied the fill and orientation, not the live pose.
    self.arm.send_command = AsyncMock(return_value="50 10 200 90 0")  # type: ignore[method-assign]
    self.arm.arm.parking_position = PreciseFlexArm.PARKING_POSITION_RIGHT
    await self.arm.arm.park()
    # Z filled at 3/4 of 400 = 300; orientation = RIGHT (0/180/180); gripper carried from current.
    self.assertEqual(self._movej_cmds(), ["moveJ 1 300.0 0.0 180.0 180.0 0.0"])

  async def test_park_respects_an_explicit_base(self):
    """A pose that already sets PreciseFlexAxis.BASE is parked as-is (no Z fill)."""
    self._full_soft_limits()
    # base 50 in the current pose so the explicit 123 (neither the 300 fill nor the live 50) proves
    # the supplied base is honored and not Z-filled; elbow/wrist carry from current.
    self.arm.send_command = AsyncMock(return_value="50 10 200 90 0")  # type: ignore[method-assign]
    self.arm.arm.parking_position = {PreciseFlexAxis.BASE: 123.0, PreciseFlexAxis.SHOULDER: 0.0}
    await self.arm.arm.park()
    self.assertEqual(self._movej_cmds(), ["moveJ 1 123.0 0.0 200.0 90.0 0.0"])

  async def test_park_without_position_falls_back_to_movetosafe(self):
    """While parking_position is unset (no configuration), park() uses the firmware movetosafe."""
    await self.arm.arm.park()
    sent = [call.args[0] for call in mocked(self.arm.send_command).call_args_list]
    # The one motion command, then the read of where the arm stopped.
    self.assertEqual([command for command in sent if command != "wherej"], ["movetosafe"])
    self.assertEqual(sent[0], "movetosafe")
    self.assertEqual(self._movej_cmds(), [])


class TestPreciseFlexSmoothCartesianRoute(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.arm = _make_arm()
    self.arm.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
    self.current_joints = {
      PreciseFlexAxis.BASE: 100.0,
      PreciseFlexAxis.SHOULDER: 0.0,
      PreciseFlexAxis.ELBOW: 180.0,
      PreciseFlexAxis.WRIST: 180.0,
      PreciseFlexAxis.GRIPPER: 70.0,
    }
    self.current_pose = PreciseFlexCartesianPose(
      location=Coordinate(10.0, 20.0, 100.0),
      rotation=Rotation(x=-180.0, y=90.0, z=0.0),
      rail_position=123.0,
      orientation="right",
      wrist="ccw",
    )
    self.arm.arm.request_joint_state = AsyncMock(return_value=self.current_joints)  # type: ignore[method-assign]
    # The route plans from the pose the joint state read gives.
    fk = patch(
      "pylabrobot.brooks.precise_flex.driver.features.arm.kinematics.fk",
      return_value=self.current_pose,
    )
    fk.start()
    self.addCleanup(fk.stop)

  def _stub_profile_transport(self, profile: str = "1 50 0 100 100 0 0 25 0") -> None:
    async def respond(command: str) -> str:
      if command == "Profile 1":
        return profile
      return ""

    self.arm.send_command = AsyncMock(side_effect=respond)  # type: ignore[method-assign]

  def _movej_cmds(self) -> list[str]:
    return [
      c.args[0]
      for c in mocked(self.arm.send_command).call_args_list
      if c.args[0].startswith("moveJ")
    ]

  def _profile_cmds(self) -> list[str]:
    return [
      c.args[0]
      for c in mocked(self.arm.send_command).call_args_list
      if c.args[0].startswith("Profile")
    ]

  async def test_move_through_cartesian_poses_plans_from_one_state_snapshot(self):
    """A smooth route reads the joint state once, fills omitted pose fields from the planned pose,
    queues all joint moves, then reads once more where the arm stopped."""
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
      "pylabrobot.brooks.precise_flex.driver.master.kinematics.ik",
      side_effect=[
        {1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
        {1: 120.0, 2: 11.0, 3: 21.0, 4: 31.0, 6: 123.0},
      ],
    ) as ik:
      await self.arm.arm.move_through_cartesian_poses(poses)

    self.assertEqual(mocked(self.arm.arm.request_joint_state).await_count, 2)
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
      "pylabrobot.brooks.precise_flex.driver.master.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      await self.arm.arm.move_through_cartesian_poses([pose])

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
      "pylabrobot.brooks.precise_flex.driver.master.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      await self.arm.arm.move_through_cartesian_poses([pose], blend=False)

    self.assertEqual(self._profile_cmds(), [])
    self.assertEqual(self._movej_cmds(), ["moveJ 1 110.0 10.0 20.0 30.0 70.0"])
    self.assertEqual(mocked(self.arm.arm.request_joint_state).await_count, 2)

  async def test_move_through_cartesian_poses_blocks_before_motion_on_limit_failure(self):
    pose = PreciseFlexCartesianPose(
      location=Coordinate(200.0, 20.0, 110.0),
      rotation=Rotation(x=-180.0, y=90.0, z=10.0),
    )
    self.arm.arm._assert_within_soft_limits = MagicMock(side_effect=ValueError("bad target"))  # type: ignore[method-assign]

    with patch(
      "pylabrobot.brooks.precise_flex.driver.master.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      with self.assertRaisesRegex(ValueError, "bad target"):
        await self.arm.arm.move_through_cartesian_poses([pose])

    self.assertEqual(self._movej_cmds(), [])
    self.assertEqual(self._profile_cmds(), [])
    # Read once to plan from, and not again: nothing was sent.
    mocked(self.arm.arm.request_joint_state).assert_awaited_once()


_LOGGER = "pylabrobot.brooks.precise_flex.driver.features.arm"


class TestPreciseFlex400AutoRecoverOnMove(unittest.IsolatedAsyncioTestCase):
  """A commanded move that finds an axis out of range: default recovers; opt-out raises."""

  def setUp(self):
    self.arm = _make_arm()
    self.arm.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
    _stub_soft_limits(
      self.arm,
      {
        PreciseFlexAxis.SHOULDER: (-93.0, 93.0),
        PreciseFlexAxis.ELBOW: (12.0, 348.0),
        PreciseFlexAxis.WRIST: (-960.0, 960.0),
      },
    )

  def _stub(self, out_of_range: str, recovered: str = "") -> None:
    """wherej returns ``out_of_range`` until a MoveOneAxis fires, then ``recovered`` (if given)."""
    state = {"recovered": False}

    async def respond(command: str) -> str:
      if command == "wherej":
        return recovered if (state["recovered"] and recovered) else out_of_range
      if command.startswith("Speed "):
        return f"{self.arm.arm.profile_index} 50.0"
      if command.startswith("MoveOneAxis"):
        state["recovered"] = True
      return ""

    self.arm.send_command = AsyncMock(side_effect=respond)  # type: ignore[method-assign]

  def _cmds(self, prefix: str) -> list[str]:
    return [
      c.args[0]
      for c in mocked(self.arm.send_command).call_args_list
      if c.args[0].startswith(prefix)
    ]

  async def test_opted_out_raises_and_does_not_move_or_recover(self):
    """Opted out: an out-of-range axis raises OutOfRangeOfMotionError; no recovery, no moveJ."""
    self.arm._recover_out_of_range = False
    self._stub("0 93.5 90.0 0.0 0")  # base shoulder elbow wrist gripper; shoulder 93.5 > 93
    with self.assertRaises(OutOfRangeOfMotionError) as ctx:
      await self.arm.arm.move_to_joint_state({PreciseFlexAxis.SHOULDER: 0.0})
    self.assertIn(PreciseFlexAxis.SHOULDER, ctx.exception.axes)
    self.assertEqual(self._cmds("MoveOneAxis"), [])
    self.assertEqual(self._cmds("moveJ"), [])

  async def test_on_recovers_offender_then_retries_move(self):
    """Opt-in on: the offending axis is nudged in range (MoveOneAxis), then the moveJ is retried."""
    self.arm._recover_out_of_range = True
    self._stub("0 93.5 90.0 0.0 0", recovered="0 92.0 90.0 0.0 0")
    with self.assertLogs(_LOGGER, level="INFO") as cm:
      await self.arm.arm.move_to_joint_state({PreciseFlexAxis.SHOULDER: 0.0})
    self.assertEqual(self._cmds("MoveOneAxis"), ["MoveOneAxis 2 92.0 1"])  # shoulder back in range
    self.assertEqual(len(self._cmds("moveJ")), 1)  # move retried and sent
    log = "\n".join(cm.output)
    self.assertIn("commanded move blocked", log)  # WARNING on entry
    self.assertIn("retried successfully", log)  # INFO on success

  async def test_on_but_unrecoverable_reraises_once_without_moving(self):
    """Opt-in on but the axis is too far out (recovery skips it): re-raise after one try, no loop."""
    self.arm._recover_out_of_range = True
    self._stub("0 120.0 90.0 0.0 0")  # shoulder 27 past the limit, beyond the recovery cap
    with self.assertLogs(_LOGGER, level="ERROR") as cm:
      with self.assertRaises(OutOfRangeOfMotionError):
        await self.arm.arm.move_to_joint_state({PreciseFlexAxis.SHOULDER: 0.0})
    self.assertEqual(self._cmds("moveJ"), [])  # never moved
    self.assertIn("auto-recovery did not clear", "\n".join(cm.output))  # ERROR before re-raise

  async def test_in_range_move_reads_position_once(self):
    """Happy path: the out-of-range check reuses the merge read, so a move issues a single wherej
    before moveJ (no redundant position read)."""
    self._stub("0 0.0 90.0 0.0 0")  # all axes in range
    await self.arm.arm.move_to_joint_state({PreciseFlexAxis.SHOULDER: 10.0})
    sent = self._cmds("")
    (move,) = self._cmds("moveJ")
    self.assertEqual(sent[: sent.index(move)], ["wherej"])  # exactly one position read before it

  async def test_move_to_location_is_also_guarded(self):
    """The Cartesian path funnels through the same guard: an out-of-range axis raises and sends no
    moveJ, like the joint path. The IK target is stubbed - it is the guard wiring, not IK, pinned
    here."""
    self.arm._recover_out_of_range = False
    self._stub("0 93.5 90.0 0.0 0")  # current shoulder 93.5 > 93, out of range
    in_range = {
      PreciseFlexAxis.BASE: 0.0,
      PreciseFlexAxis.SHOULDER: 0.0,
      PreciseFlexAxis.ELBOW: 90.0,
      PreciseFlexAxis.WRIST: 0.0,
      PreciseFlexAxis.GRIPPER: 0.0,
    }
    with patch.object(self.arm.arm, "_cart_to_joints", MagicMock(return_value=in_range)):
      with self.assertRaises(OutOfRangeOfMotionError) as ctx:
        await self.arm.arm.move_to_location(Coordinate(400.0, 0.0, 200.0), 0.0)
    self.assertIn(PreciseFlexAxis.SHOULDER, ctx.exception.axes)
    self.assertEqual(self._cmds("moveJ"), [])


def _recording_arm(events: list) -> PreciseFlexDriver:
  """An arm whose socket records each write/read and yields control between them.

  The ``await asyncio.sleep(0)`` after every write hands the event loop to any other ready coroutine,
  so an UNserialized implementation would let a second concurrent command write before the first
  reads - producing ``[w, w, r, r]``. With the per-exchange lock the pairs stay together.
  """
  arm = PreciseFlexDriver(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=500.0,
  )
  arm.io = MagicMock()

  async def write(data: bytes) -> None:
    events.append(("w", data))
    await asyncio.sleep(0)

  async def readline() -> bytes:
    events.append(("r", None))
    await asyncio.sleep(0)
    return b"0\r\n"

  arm.io.write = write
  arm.io.readline = readline
  return arm


class TestSendCommandSerialization(unittest.IsolatedAsyncioTestCase):
  """Each request->reply pair is one lock-held exchange, so concurrent callers cannot interleave
  their write/read on the single shared connection (port 10100 refuses a second socket)."""

  async def test_concurrent_send_commands_do_not_interleave(self):
    """Two commands run concurrently each keep their own write immediately paired with their read."""
    events: list = []
    arm = _recording_arm(events)
    await asyncio.gather(arm.send_command("A"), arm.send_command("B"))
    self.assertEqual([kind for kind, _ in events], ["w", "r", "w", "r"])

  async def test_lock_released_after_an_error(self):
    """A failing exchange releases the lock, so a later command is not deadlocked behind it."""
    arm = _recording_arm([])
    calls = {"n": 0}

    async def readline() -> bytes:
      calls["n"] += 1
      if calls["n"] == 1:
        raise TimeoutError()
      return b"0 ok\r\n"

    arm.io.readline = readline  # type: ignore[method-assign,assignment]
    with self.assertRaises(TimeoutError):
      await arm.send_command("A")
    self.assertFalse(arm._io_lock.locked())
    self.assertEqual(await arm.send_command("B"), "ok")
