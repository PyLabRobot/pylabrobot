import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.brooks.precise_flex import (
  Axis,
  IKError,
  OutOfRangeOfMotionError,
  PreciseFlex,
  PreciseFlexCartesianPose,
  PreciseFlexError,
  StationAccess,
  WorkEnvelope,
)
from pylabrobot.events import EventBus, PLREvent, event_context, use_event_bus
from pylabrobot.resources import Coordinate, Rotation


def mocked(method: object) -> AsyncMock:
  """A real method that a test replaced with an ``AsyncMock``.

  Assertions like ``call_args_list`` live on the mock, not on the declared
  method type, so they need narrowing before mypy will accept them.
  """
  return cast(AsyncMock, method)


_PAD_1 = Coordinate(329.9, 80.29, 40.48)


def _make_arm(
  closed_gripper_position: float = 500.0,
  gripper_units: float = 503.0,
) -> PreciseFlex:
  """An arm whose transport is stubbed out, so tests assert on the commands it would send.

  `wherej` answers with a pose because pick and place read the gripper axis back to tell a
  held plate from empty jaws; `gripper_units` is what those reads see. The default is the
  bench proportion: the jaws are opened a step (10) off the grip position and a plate holds
  them a little (3) above it, inside that opening. A held position wider than the opening
  would describe a plate the fingers had already hit on the way in.
  """
  arm = PreciseFlex(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=closed_gripper_position,
  )

  async def reply(command: str) -> str:
    if command == "wherej":
      return f"40.48 84.76 229.84 -312.57 {gripper_units}"
    return ""

  arm.send_command = AsyncMock(side_effect=reply)  # type: ignore[method-assign]
  return arm


class TestPreciseFlex400Gripper(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    # closed_gripper_position=500 â‡’ min_gripper_width(60mm) maps to 500 units.
    self.arm = _make_arm(closed_gripper_position=500.0)

  def _sent_commands(self) -> list[str]:
    return [c.args[0] for c in mocked(self.arm.send_command).call_args_list]

  async def test_move_gripper_force_sensing_false_opens_with_position(self):
    # 80 mm â‡’ 500 + (80 - 60) = 520 firmware units. The settle poll runs first, so the
    # actuation is the tail of the exchange, not the whole of it.
    await self.arm.move_gripper(width=80.0, force_sensing=False)
    self.assertEqual(self._sent_commands()[-2:], ["GripOpenPos 520.0", "gripper 1"])

  async def test_move_gripper_force_sensing_true_closes_with_position(self):
    # 60 mm (the closed reference) â‡’ exactly closed_gripper_position.
    await self.arm.move_gripper(width=60.0, force_sensing=True)
    self.assertEqual(self._sent_commands()[-2:], ["GripClosePos 500.0", "gripper 2"])

  async def test_move_gripper_position_command_precedes_move(self):
    await self.arm.move_gripper(width=120.0, force_sensing=False)
    commands = self._sent_commands()
    self.assertLess(
      commands.index("GripOpenPos 560.0"),
      commands.index("gripper 1"),
      "Position must be set before the gripper move command fires.",
    )

  async def test_force_sensing_branches_use_different_firmware_commands(self):
    await self.arm.move_gripper(width=90.0, force_sensing=False)
    await self.arm.move_gripper(width=90.0, force_sensing=True)
    commands = self._sent_commands()
    self.assertIn("gripper 1", commands)
    self.assertIn("gripper 2", commands)
    self.assertIn("GripOpenPos 530.0", commands)
    self.assertIn("GripClosePos 530.0", commands)

  async def test_min_max_gripper_width_advertised(self):
    self.assertEqual(self.arm.min_gripper_width, 60.0)
    self.assertEqual(self.arm.max_gripper_width, 145.0)

  async def test_closed_gripper_position_shifts_units(self):
    # Different anchor â‡’ same width yields a different firmware-unit target.
    arm = _make_arm(closed_gripper_position=1000.0)
    await arm.move_gripper(width=80.0, force_sensing=False)
    commands = [c.args[0] for c in mocked(arm.send_command).call_args_list]
    # 80 mm â‡’ 1000 + (80 - 60) = 1020 units.
    self.assertEqual(commands[-2:], ["GripOpenPos 1020.0", "gripper 1"])

  def test_mm_to_firmware_units_helper(self):
    # Direct check of the linear mapping.
    self.assertEqual(self.arm._mm_to_firmware_units(60.0), 500.0)
    self.assertEqual(self.arm._mm_to_firmware_units(145.0), 585.0)
    self.assertEqual(self.arm._mm_to_firmware_units(100.0), 540.0)


class TestGripperWidthsAdoptedFromSoftLimits(unittest.IsolatedAsyncioTestCase):
  """A width has to mean the same jaw travel before and after the axis limits are read.

  Bench numbers: the gripper axis reports [69.0, 134.0] and the fitted gripper closes at
  75.5. The two differ, which is the case that used to strand the top of the range and
  shift every width underneath it.
  """

  AXIS_MIN, AXIS_MAX = 69.0, 134.0
  CLOSED_AT = 75.5

  def setUp(self):
    self.arm = _make_arm(closed_gripper_position=self.CLOSED_AT)

  def _discover(self, arm=None, limits=None):
    discovered = MagicMock()
    discovered.gripper_axis_limits = limits or (self.AXIS_MIN, self.AXIS_MAX)
    discovered.has_rail = False
    discovered.is_dual_gripper = False
    (arm or self.arm)._adopt_configuration(discovered)

  def _sent(self, arm=None) -> list[str]:
    return [c.args[0] for c in mocked((arm or self.arm).send_command).call_args_list]

  async def test_a_width_commands_the_same_travel_before_and_after_discovery(self):
    await self.arm.move_gripper(80.0, force_sensing=False)
    before = self._sent()
    self._discover()
    await self.arm.move_gripper(80.0, force_sensing=False)
    after = self._sent()[len(before) :]
    self.assertEqual(before[0], after[0], "discovery moved what 80 mm means")

  async def test_opening_to_the_advertised_max_reaches_the_axis_ceiling(self):
    self._discover()
    await self.arm.move_gripper(self.arm.max_gripper_width, force_sensing=False)
    self.assertIn(f"GripOpenPos {self.AXIS_MAX}", self._sent())

  async def test_closing_to_the_advertised_min_reaches_the_axis_floor(self):
    self._discover()
    await self.arm.move_gripper(self.arm.min_gripper_width, force_sensing=True)
    self.assertIn(f"GripClosePos {self.AXIS_MIN}", self._sent())

  async def test_an_advertised_end_survives_its_own_float_round_trip(self):
    # Limits that are not halves: the reconverted ceiling lands a few ulps out, and the
    # guard has to read that as float dust rather than as out of range.
    arm = _make_arm(closed_gripper_position=75.53)
    self._discover(arm=arm, limits=(70.3, 134.097))
    await arm.move_gripper(arm.max_gripper_width, force_sensing=False)
    self.assertIn("GripOpenPos 134.097", self._sent(arm))

  def test_the_soft_limits_stay_in_firmware_units(self):
    self._discover()
    self.assertEqual(
      (self.arm._gripper_soft_min, self.arm._gripper_soft_max), (self.AXIS_MIN, self.AXIS_MAX)
    )


class TestPreciseFlexEvents(unittest.IsolatedAsyncioTestCase):
  async def test_gripper_event_uses_default_length_unit_field(self):
    arm = _make_arm()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await arm.move_gripper(width=80.0)

    self.assertEqual(events[0].data["width"], 80.0)
    self.assertNotIn("width_mm", events[0].data)

  async def test_gripper_event_and_nested_firmware_commands_inherit_resource_context(self):
    arm = PreciseFlex(
      host="localhost",
      gripper_length=162.0,
      gripper_z_offset=0.0,
      closed_gripper_position=500.0,
    )
    arm.io.write = AsyncMock()  # type: ignore[method-assign]
    arm.io.readline = AsyncMock(return_value=b"0\n")  # type: ignore[method-assign]
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
      await arm.move_gripper_joint_position(520.0)

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
      [
        "wherej",  # the settle poll the jaws wait behind
        "wherej",
        "GripOpenPos 520.0",
        "gripper 1",
      ],
    )
    self.assertTrue(
      all(event.context["resources"] == [{"name": "sample_plate"}] for event in firmware_events)
    )


class TestPreciseFlex400OutOfRangeRecovery(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.arm = _make_arm()
    self.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
    # Minimal stub configuration: only the soft limits the recovery logic reads.
    self.arm._configuration = MagicMock(
      soft_limits={
        Axis.SHOULDER: (-93.0, 93.0),
        Axis.ELBOW: (12.0, 348.0),
        Axis.WRIST: (-960.0, 960.0),
      }
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
        return f"{self.arm.profile_index} 50.0"
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
    recovered = await self.arm.recover_axes_within_limits()
    self.assertEqual(recovered, {Axis.SHOULDER: 92.0, Axis.ELBOW: 13.0})  # wrist excluded
    self.assertEqual(
      self._move_one_axis_cmds(), ["MoveOneAxis 2 92.0 1", "MoveOneAxis 3 13.0 1"]
    )  # shoulder (2) before elbow (3)

  async def test_recover_skips_axis_too_far_out_of_range(self):
    """An axis past its limit by more than max_distance is left in place (no unattended big sweep)."""
    # shoulder 120 deg is 27 past the 93 limit, beyond the 5 cap; elbow/wrist in range.
    self._stub_transport("0 120.0 30.0 0.0 0")
    recovered = await self.arm.recover_axes_within_limits()
    self.assertEqual(recovered, {})
    self.assertEqual(self._move_one_axis_cmds(), [])


class TestPreciseFlexParking(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.arm = _make_arm()
    self.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]

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
      c.args[0]
      for c in mocked(self.arm.send_command).call_args_list
      if c.args[0].startswith("moveJ")
    ]

  def test_named_constants_are_orientation_only_planar_folds(self):
    """The three parking orientations are planar folds (ELBOW 180) that never pin Z (Axis.BASE), so
    one orientation works on any reach; they differ only in which way the gripper faces."""
    for pose in (
      PreciseFlex.PARKING_POSITION_BACK,
      PreciseFlex.PARKING_POSITION_RIGHT,
      PreciseFlex.PARKING_POSITION_FRONT,
    ):
      self.assertNotIn(Axis.BASE, pose)
      self.assertEqual(pose[Axis.ELBOW], 180.0)
    self.assertEqual(PreciseFlex.PARKING_POSITION_BACK[Axis.SHOULDER], 90.0)
    self.assertEqual(PreciseFlex.PARKING_POSITION_FRONT[Axis.SHOULDER], -90.0)

  def test_assignment_rejects_non_axis_keys(self):
    """The validating setter refuses a pose keyed by anything but Axis members."""
    bad_pose: dict = {"base": 100.0}
    with self.assertRaises(ValueError):
      self.arm.parking_position = bad_pose

  def test_assignment_rejects_out_of_limit_value_once_configured(self):
    """Once the soft limits are known, a value outside them is rejected at assignment."""
    self.arm._configuration = MagicMock(soft_limits={Axis.SHOULDER: (-93.0, 93.0)})
    with self.assertRaises(ValueError):
      self.arm.parking_position = {Axis.SHOULDER: 200.0}

  def test_assignment_accepts_named_constant(self):
    """A named constant assigns cleanly and round-trips through the getter."""
    self.arm.parking_position = PreciseFlex.PARKING_POSITION_FRONT
    pose = self.arm.parking_position
    assert pose is not None
    self.assertEqual(pose[Axis.SHOULDER], -90.0)

  async def test_park_fills_z_at_three_quarters_travel_and_keeps_orientation(self):
    """park() fills the omitted Z column at 3/4 of the discovered travel and keeps the orientation."""
    self.arm._configuration = self._full_soft_limits()
    # Current pose deliberately differs from the target (base 50 not 300; orientation 10/200/90 not
    # 0/180/180) so the assertion proves park() supplied the fill and orientation, not the live pose.
    self.arm.send_command = AsyncMock(return_value="50 10 200 90 0")  # type: ignore[method-assign]
    self.arm.parking_position = PreciseFlex.PARKING_POSITION_RIGHT
    await self.arm.park()
    # Z filled at 3/4 of 400 = 300; orientation = RIGHT (0/180/180); gripper carried from current.
    self.assertEqual(self._movej_cmds(), ["moveJ 1 300.0 0.0 180.0 180.0 0.0"])

  async def test_park_respects_an_explicit_base(self):
    """A pose that already sets Axis.BASE is parked as-is (no Z fill)."""
    self.arm._configuration = self._full_soft_limits()
    # base 50 in the current pose so the explicit 123 (neither the 300 fill nor the live 50) proves
    # the supplied base is honored and not Z-filled; elbow/wrist carry from current.
    self.arm.send_command = AsyncMock(return_value="50 10 200 90 0")  # type: ignore[method-assign]
    self.arm.parking_position = {Axis.BASE: 123.0, Axis.SHOULDER: 0.0}
    await self.arm.park()
    self.assertEqual(self._movej_cmds(), ["moveJ 1 123.0 0.0 200.0 90.0 0.0"])

  async def test_park_without_position_falls_back_to_movetosafe(self):
    """While parking_position is unset (no configuration), park() uses the firmware movetosafe."""
    await self.arm.park()
    mocked(self.arm.send_command).assert_awaited_with("movetosafe")
    self.assertEqual(self._movej_cmds(), [])


class TestPreciseFlexSmoothCartesianRoute(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.arm = _make_arm()
    self.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
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
    self.arm._request_state = AsyncMock(return_value=(self.current_joints, self.current_pose))  # type: ignore[method-assign]

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
      "pylabrobot.brooks.precise_flex.precise_flex.kinematics.ik",
      side_effect=[
        {1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
        {1: 120.0, 2: 11.0, 3: 21.0, 4: 31.0, 6: 123.0},
      ],
    ) as ik:
      await self.arm.move_through_cartesian_poses(poses)

    mocked(self.arm._request_state).assert_awaited_once()
    mocked(self.arm._wait_for_eom).assert_awaited_once()
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
      "pylabrobot.brooks.precise_flex.precise_flex.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      await self.arm.move_through_cartesian_poses([pose])

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
      "pylabrobot.brooks.precise_flex.precise_flex.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      await self.arm.move_through_cartesian_poses([pose], blend=False)

    self.assertEqual(self._profile_cmds(), [])
    self.assertEqual(self._movej_cmds(), ["moveJ 1 110.0 10.0 20.0 30.0 70.0"])
    mocked(self.arm._wait_for_eom).assert_awaited_once()

  async def test_move_through_cartesian_poses_blocks_before_motion_on_limit_failure(self):
    pose = PreciseFlexCartesianPose(
      location=Coordinate(200.0, 20.0, 110.0),
      rotation=Rotation(x=-180.0, y=90.0, z=10.0),
    )
    self.arm._assert_within_soft_limits = MagicMock(side_effect=ValueError("bad target"))  # type: ignore[method-assign]

    with patch(
      "pylabrobot.brooks.precise_flex.precise_flex.kinematics.ik",
      return_value={1: 110.0, 2: 10.0, 3: 20.0, 4: 30.0, 6: 123.0},
    ):
      with self.assertRaisesRegex(ValueError, "bad target"):
        await self.arm.move_through_cartesian_poses([pose])

    self.assertEqual(self._movej_cmds(), [])
    self.assertEqual(self._profile_cmds(), [])
    mocked(self.arm._wait_for_eom).assert_not_awaited()


_LOGGER = "pylabrobot.brooks.precise_flex.precise_flex"


class TestPreciseFlex400AutoRecoverOnMove(unittest.IsolatedAsyncioTestCase):
  """A commanded move that finds an axis out of range: default recovers; opt-out raises."""

  def setUp(self):
    self.arm = _make_arm()
    self.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
    self.arm._configuration = MagicMock(
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
        return f"{self.arm.profile_index} 50.0"
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
      await self.arm.move_to_joint_position({Axis.SHOULDER: 0.0})
    self.assertIn(Axis.SHOULDER, ctx.exception.axes)
    self.assertEqual(self._cmds("MoveOneAxis"), [])
    self.assertEqual(self._cmds("moveJ"), [])

  async def test_on_recovers_offender_then_retries_move(self):
    """Opt-in on: the offending axis is nudged in range (MoveOneAxis), then the moveJ is retried."""
    self.arm._recover_out_of_range = True
    self._stub("0 93.5 90.0 0.0 0", recovered="0 92.0 90.0 0.0 0")
    with self.assertLogs(_LOGGER, level="INFO") as cm:
      await self.arm.move_to_joint_position({Axis.SHOULDER: 0.0})
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
        await self.arm.move_to_joint_position({Axis.SHOULDER: 0.0})
    self.assertEqual(self._cmds("moveJ"), [])  # never moved
    self.assertIn("auto-recovery did not clear", "\n".join(cm.output))  # ERROR before re-raise

  async def test_in_range_move_reads_position_once(self):
    """Happy path: the out-of-range check reuses the merge read, so a move issues a single wherej
    before moveJ (no redundant position read)."""
    self._stub("0 0.0 90.0 0.0 0")  # all axes in range
    await self.arm.move_to_joint_position({Axis.SHOULDER: 10.0})
    self.assertEqual(self._cmds("wherej"), ["wherej"])  # exactly one position read
    self.assertEqual(len(self._cmds("moveJ")), 1)

  async def test_move_to_location_is_also_guarded(self):
    """The Cartesian path funnels through the same guard: an out-of-range axis raises and sends no
    moveJ, like the joint path. The IK target is stubbed - it is the guard wiring, not IK, pinned
    here."""
    self.arm._recover_out_of_range = False
    self._stub("0 93.5 90.0 0.0 0")  # current shoulder 93.5 > 93, out of range
    in_range = {
      Axis.BASE: 0.0,
      Axis.SHOULDER: 0.0,
      Axis.ELBOW: 90.0,
      Axis.WRIST: 0.0,
      Axis.GRIPPER: 0.0,
    }
    with patch.object(self.arm, "_cart_to_joints", AsyncMock(return_value=in_range)):
      with self.assertRaises(OutOfRangeOfMotionError) as ctx:
        await self.arm.move_to_location(Coordinate(400.0, 0.0, 200.0), 0.0)
    self.assertIn(Axis.SHOULDER, ctx.exception.axes)
    self.assertEqual(self._cmds("moveJ"), [])


def _make_linked_arm() -> PreciseFlex:
  """An arm whose socket is stubbed too, for asserting on the bring-up sequence."""
  arm = _make_arm()
  arm.io = MagicMock()
  arm.io.setup = AsyncMock()
  arm.io.stop = AsyncMock()
  arm.io.write = AsyncMock()
  arm.io._host = "localhost"
  arm.io._port = 10100
  return arm


class TestPreciseFlexLifecycle(unittest.IsolatedAsyncioTestCase):
  """Opening the link, taking control, and homing are three separate verbs.

  A caller that only wants to read a position can connect and initialize without
  the arm ever moving; only ``home`` sweeps it.
  """

  def setUp(self):
    self.arm = _make_linked_arm()

  def _sent(self) -> list[str]:
    return [c.args[0] for c in mocked(self.arm.send_command).call_args_list]

  def _assert_moved_nothing(self):
    for command in self._sent():
      verb = command.split()[0].lower()
      self.assertNotIn(
        verb,
        ("home", "homeall", "movej", "movec", "moveoneaxis", "gripper"),
        f"bring-up must not move the arm, but it sent {command!r}",
      )

  async def test_connect_opens_the_link_and_agrees_the_protocol(self):
    await self.arm.connect()
    mocked(self.arm.io.setup).assert_awaited_once()
    self.assertEqual(self._sent(), ["mode 0"])

  async def test_connect_does_not_raise_power(self):
    await self.arm.connect()
    self.assertNotIn("hp 1", self._sent())
    self._assert_moved_nothing()

  async def test_initialize_takes_control_without_moving(self):
    self.arm._request_configuration = AsyncMock(side_effect=RuntimeError("no controller"))
    await self.arm.initialize()
    sent = self._sent()
    self.assertIn("attach 1", sent)
    self.assertIn("freemode -1", sent)
    self.assertTrue(any(c.startswith("hp 1") for c in sent), sent)
    self._assert_moved_nothing()

  async def test_initialize_adopts_what_the_controller_reports(self):
    # The link lengths ride on this: without it the arm solves IK for a different machine.
    discovered = MagicMock()
    discovered.soft_limits = {
      Axis.SHOULDER: (-93.0, 93.0),
      Axis.ELBOW: (12.0, 348.0),
      Axis.WRIST: (-960.0, 960.0),
    }
    self.arm._request_configuration = AsyncMock(return_value=discovered)
    self.arm._adopt_configuration = MagicMock()
    self.arm._log_configuration_summary = MagicMock()
    self.arm._assess_configuration = MagicMock()

    await self.arm.initialize()

    self.arm._adopt_configuration.assert_called_once_with(discovered)

  async def test_initialize_falls_back_to_defaults_when_discovery_fails(self):
    self.arm._request_configuration = AsyncMock(side_effect=RuntimeError("no controller"))
    self.arm._adopt_configuration = MagicMock()

    await self.arm.initialize()

    self.arm._adopt_configuration.assert_not_called()

  async def test_disconnect_hands_the_arm_back_and_closes_the_link(self):
    await self.arm.disconnect()
    sent = self._sent()
    self.assertIn("attach 0", sent)
    self.assertIn("hp 0", sent)
    mocked(self.arm.io.write).assert_awaited_once_with(b"exit\n")
    mocked(self.arm.io.stop).assert_awaited_once()

  async def test_setup_connects_then_initializes_then_homes_in_that_order(self):
    calls: list[str] = []
    self.arm.connect = AsyncMock(side_effect=lambda: calls.append("connect"))
    self.arm.initialize = AsyncMock(side_effect=lambda: calls.append("initialize"))
    self.arm.home = AsyncMock(side_effect=lambda: calls.append("home"))
    self.arm._handle_out_of_range_axes = AsyncMock()

    await self.arm.setup()

    self.assertEqual(calls, ["connect", "initialize", "home"])

  async def test_setup_skip_home_brings_the_arm_up_without_sweeping_it(self):
    self.arm.connect = AsyncMock()
    self.arm.initialize = AsyncMock()
    self.arm.home = AsyncMock()
    self.arm._handle_out_of_range_axes = AsyncMock()

    await self.arm.setup(skip_home=True)

    mocked(self.arm.home).assert_not_awaited()

  async def test_setup_still_checks_soft_limits_when_discovery_fails(self):
    # Discovery is best-effort, but losing it must not silently skip the
    # out-of-range recovery that makes an unusable arm usable again.
    self.arm.connect = AsyncMock()
    self.arm.home = AsyncMock()
    self.arm._request_configuration = AsyncMock(side_effect=RuntimeError("no controller"))
    self.arm._handle_out_of_range_axes = AsyncMock()

    await self.arm.setup()

    mocked(self.arm._handle_out_of_range_axes).assert_awaited_once()

  async def test_stop_is_disconnect(self):
    self.arm.disconnect = AsyncMock()
    await self.arm.stop()
    mocked(self.arm.disconnect).assert_awaited_once()


class TestPreciseFlexSingleAxisMoves(unittest.IsolatedAsyncioTestCase):
  """One axis moves and the rest hold their live values.

  Both verbs run through the guarded joint path, so a target outside the soft
  limits is refused here rather than by the controller.
  """

  def setUp(self):
    self.arm = _make_arm()
    self.arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
    self.arm._configuration = MagicMock(
      z_range=(0.0, 400.0),
      soft_limits={
        Axis.BASE: (0.0, 400.0),
        Axis.SHOULDER: (-93.0, 93.0),
        Axis.ELBOW: (12.0, 348.0),
        Axis.WRIST: (-960.0, 960.0),
      },
    )
    # wherej, no rail: base shoulder elbow wrist gripper
    self.arm.send_command = AsyncMock(return_value="50 10 200 90 0")  # type: ignore[method-assign]

  def _movej_cmds(self) -> list[str]:
    return [
      c.args[0]
      for c in mocked(self.arm.send_command).call_args_list
      if c.args[0].startswith("moveJ")
    ]

  async def test_move_one_axis_moves_only_that_axis(self):
    await self.arm.move_one_axis(Axis.SHOULDER, 42.0)
    # shoulder becomes 42; base/elbow/wrist/gripper carry from the live pose.
    self.assertEqual(self._movej_cmds(), ["moveJ 1 50.0 42.0 200.0 90.0 0.0"])

  async def test_move_one_axis_relative_offsets_from_the_live_position(self):
    await self.arm.move_one_axis_relative(Axis.ELBOW, -20.0)
    # elbow 200 - 20 = 180; everything else carries from the live pose.
    self.assertEqual(self._movej_cmds(), ["moveJ 1 50.0 10.0 180.0 90.0 0.0"])

  async def test_move_one_axis_uses_the_guarded_path_not_the_recovery_primitive(self):
    # MoveOneAxis is the unguarded recovery primitive; a normal move must not use it.
    await self.arm.move_one_axis(Axis.SHOULDER, 42.0)
    sent = [c.args[0] for c in mocked(self.arm.send_command).call_args_list]
    self.assertFalse([c for c in sent if c.startswith("MoveOneAxis")], sent)

  async def test_move_one_axis_refuses_a_target_outside_the_soft_limits(self):
    with self.assertRaises(ValueError):
      await self.arm.move_one_axis(Axis.SHOULDER, 200.0)
    self.assertEqual(self._movej_cmds(), [])

  async def test_move_one_axis_relative_refuses_an_offset_that_leaves_the_limits(self):
    with self.assertRaises(ValueError):
      await self.arm.move_one_axis_relative(Axis.SHOULDER, 500.0)
    self.assertEqual(self._movej_cmds(), [])


class TestPreciseFlexMoveToSafe(unittest.IsolatedAsyncioTestCase):
  """The controller's own safe retraction, reachable without going through park()."""

  def setUp(self):
    self.arm = _make_arm()

  async def test_move_to_safe_hands_the_route_to_the_controller(self):
    await self.arm.move_to_safe()
    mocked(self.arm.send_command).assert_awaited_with("movetosafe")

  async def test_move_to_safe_commands_no_joint_target(self):
    # The controller plans the route, so the driver must not send joints of its own.
    await self.arm.move_to_safe()
    sent = [c.args[0] for c in mocked(self.arm.send_command).call_args_list]
    self.assertFalse([c for c in sent if c.startswith(("moveJ", "moveC"))], sent)


class TestPickAndPlaceAreComposedOfMoves(unittest.IsolatedAsyncioTestCase):
  """Pick and place are built from the verbs this arm is known to answer.

  The controller's own PickPlate reads a station location, which is persistent controller
  state with a type of its own, and it runs the approach out of reach of the caller. Every
  leg here is an ordinary guarded move plus a gripper command, so the route is the
  driver's and the access geometry is the caller's.
  """

  def setUp(self):
    self.arm = _make_arm()
    self.moves: list[tuple[float, float, float]] = []

    async def record(location, direction, **kwargs):
      self.moves.append((round(location.x, 2), round(location.y, 2), round(location.z, 2)))

    patcher = patch.object(self.arm, "move_to_location", AsyncMock(side_effect=record))
    patcher.start()
    self.addCleanup(patcher.stop)

  def _sent(self) -> list[str]:
    return [c.args[0] for c in mocked(self.arm.send_command).call_args_list]

  async def test_a_vertical_pick_goes_above_the_plate_down_and_lifts_it_clear(self):
    await self.arm.pick_up_at_location(
      _PAD_1,
      direction=2.03,
      resource_width=80.0,
      resource_height=14.0,
      travel_margin=10.0,
      access=StationAccess(clearance=20.0, grasp_offset=0.0),
    )
    self.assertEqual(
      self.moves,
      [(329.9, 80.29, 60.48), (329.9, 80.29, 40.48), (329.9, 80.29, 64.48)],
      "approach at +clearance, plate, then up by the resource plus the margin",
    )

  async def test_the_lift_off_a_pick_clears_the_skirt_by_default(self):
    # Departing at grip height leaves the skirt in the nest for the next traverse to drag.
    await self.arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0)
    self.assertEqual(
      self.moves[-1],
      (329.9, 80.29, round(40.476 + 14.35 + 10.0 + 10.0, 2)),
      "the resource, the travel margin, and the station's loaded allowance",
    )

  async def test_the_lift_is_the_caller_s_to_set(self):
    await self.arm.pick_up_at_location(
      _PAD_1,
      direction=2.03,
      resource_width=80.0,
      resource_height=44.0,
      travel_margin=6.0,
      access=StationAccess(grasp_offset=0.0),
    )
    self.assertEqual(self.moves[-1], (329.9, 80.29, round(40.476 + 50.0, 2)))

  async def test_a_retreat_holding_a_plate_carries_the_station_s_grasp_offset(self):
    # An operator raises grasp_offset because that nest is deep; a fixed lift leaves the
    # plate still inside it when the traverse starts.
    await self.arm.pick_up_at_location(
      _PAD_1,
      direction=2.03,
      resource_width=80.0,
      resource_height=14.0,
      travel_margin=10.0,
      access=StationAccess(clearance=20.0, grasp_offset=60.0),
    )
    self.assertEqual(self.moves[-1], (329.9, 80.29, round(40.476 + 14.0 + 10.0 + 60.0, 2)))

  async def test_a_retreat_with_empty_jaws_does_not(self):
    # Nothing is being carried out of the nest after a release, so the allowance for a
    # held plate does not apply.
    await self.arm.drop_at_location(
      _PAD_1,
      direction=2.03,
      resource_height=14.0,
      travel_margin=10.0,
      access=StationAccess(clearance=20.0, grasp_offset=60.0),
    )
    self.assertEqual(self.moves[-1], (329.9, 80.29, 64.48))

  async def test_the_jaws_open_before_the_arm_reaches_in(self):
    # Descending onto a plate with the fingers still closed is how you break one.
    order: list[str] = []

    async def note_move(location, direction, **kwargs):
      order.append("move")

    async def note_grip(position, force_sensing=False):
      order.append("close" if force_sensing else "open")

    with patch.object(self.arm, "move_to_location", AsyncMock(side_effect=note_move)):
      with patch.object(self.arm, "move_gripper_joint_position", AsyncMock(side_effect=note_grip)):
        await self.arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0)
    self.assertEqual(order, ["open", "move", "move", "close", "move"])

  async def test_the_jaws_open_a_step_off_the_grip_position_not_to_the_stop(self):
    # Sweeping to the axis stop is a long move to no purpose, and on a full deck it is the
    # move that meets the neighbours.
    await self.arm.pick_up_at_location(
      _PAD_1, direction=2.03, resource_width=80.0, jaw_opening=10.0
    )
    self.assertIn("GripOpenPos 510.0", self._sent())  # closed_gripper_position 500 + 10

  async def test_the_jaws_never_open_past_the_axis_ceiling(self):
    self.arm._gripper_soft_max = 505.0
    await self.arm.pick_up_at_location(
      _PAD_1, direction=2.03, resource_width=80.0, jaw_opening=40.0
    )
    self.assertIn("GripOpenPos 505.0", self._sent())

  async def test_the_grip_commands_the_calibrated_position_not_the_axis_floor(self):
    # Commanding past a held plate holds a standing position error, which the controller
    # reads as an overheating motor (-3104) rather than as a grip.
    await self.arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0)
    self.assertIn("GripClosePos 500.0", self._sent())

  async def test_a_horizontal_pick_stands_off_comes_in_lifts_and_backs_out(self):
    # A shelf is entered along the approach direction, not from above.
    await self.arm.pick_up_at_location(
      Coordinate(300.0, 0.0, 50.0),
      direction=0.0,
      resource_width=80.0,
      access=StationAccess(approach="horizontal", clearance=40.0, z_above=10.0),
    )
    self.assertEqual(
      self.moves,
      [
        (260.0, 0.0, 60.0),
        (260.0, 0.0, 50.0),
        (300.0, 0.0, 50.0),
        (300.0, 0.0, 60.0),
        (260.0, 0.0, 60.0),
      ],
    )

  async def test_a_place_reaches_in_releases_and_lifts_the_fingers_clear(self):
    # The fingers sit around the skirt after opening, so they rise the same way.
    await self.arm.drop_at_location(
      _PAD_1,
      direction=2.03,
      resource_height=14.0,
      travel_margin=10.0,
      access=StationAccess(clearance=20.0),
    )
    self.assertEqual(
      self.moves,
      [(329.9, 80.29, 60.48), (329.9, 80.29, 40.48), (329.9, 80.29, 64.48)],
    )

  async def test_a_place_opens_a_step_off_where_the_plate_held_the_jaws(self):
    # Sweeping to the full width would meet the neighbours in a hotel.
    await self.arm.drop_at_location(_PAD_1, direction=2.03, jaw_opening=10.0)
    self.assertIn("GripOpenPos 513.0", self._sent())  # jaws held at 503 by the plate

  async def test_nothing_is_written_to_a_station(self):
    await self.arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0)
    for verb in ("locXyz", "locAngles", "locConfig", "StationType", "pickplate", "placeplate"):
      self.assertFalse([c for c in self._sent() if c.lower().startswith(verb.lower())], verb)

  async def test_the_grasp_data_still_goes_out_before_the_pick(self):
    # Where the close stops is `closed_gripper_position`, not anything in here; whether the
    # controller reads GraspData for a plain `gripper 2` is unconfirmed on the arm.
    await self.arm.pick_up_at_location(
      _PAD_1, direction=2.03, resource_width=80.0, finger_speed_pct=40.0, grasp_force=12.0
    )
    self.assertEqual(self._sent()[0], "GraspData 80.0 40.0 12.0")
    self.assertIn("GripClosePos 500.0", self._sent())


class TestPickReportsEmptyJaws(unittest.IsolatedAsyncioTestCase):
  """A pick that closes on nothing has to say so, or the arm carries air to the next station."""

  def _arm(self, gripper_units: float) -> PreciseFlex:
    arm = _make_arm(gripper_units=gripper_units)
    patcher = patch.object(arm, "move_to_location", AsyncMock())
    patcher.start()
    self.addCleanup(patcher.stop)
    return arm

  async def test_jaws_settling_on_the_grip_position_mean_no_plate(self):
    arm = self._arm(gripper_units=500.5)
    with self.assertRaises(PreciseFlexError) as caught:
      await arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0)
    self.assertIn("nothing in it", str(caught.exception))

  async def test_jaws_held_open_by_a_plate_are_a_successful_pick(self):
    arm = self._arm(gripper_units=503.0)
    await arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0)

  async def test_a_failed_pick_leaves_the_arm_clear_of_the_station(self):
    # Aborting where it stands parks the arm down in the nest for whatever runs next.
    arm = _make_arm(gripper_units=500.5)
    moves: list[float] = []

    async def record(location, direction, **kwargs):
      moves.append(round(location.z, 2))

    with patch.object(arm, "move_to_location", AsyncMock(side_effect=record)):
      with self.assertRaises(PreciseFlexError):
        await arm.pick_up_at_location(
          _PAD_1, direction=2.03, resource_width=80.0, resource_height=14.0, travel_margin=10.0
        )
    self.assertEqual(moves[-1], 64.48, "the arm should have risen clear before raising")


class TestPickTargetIsCheckedBeforeItMoves(unittest.IsolatedAsyncioTestCase):
  """An unreachable target must fail on the call, not part way through a motion."""

  def setUp(self):
    self.arm = _make_arm()
    self.arm._configuration = MagicMock(
      work_envelope=WorkEnvelope(inner=100.0, outer=600.0, zmin=0.0, zmax=400.0)
    )

  def _sent(self) -> list[str]:
    return [c.args[0] for c in mocked(self.arm.send_command).call_args_list]

  async def test_a_target_beyond_the_annulus_is_refused(self):
    with self.assertRaises(IKError):
      await self.arm.pick_up_at_location(
        Coordinate(2000.0, 0.0, 40.0), direction=0.0, resource_width=80.0
      )
    self.assertFalse(self._sent(), "nothing should reach the controller")

  async def test_a_target_below_the_z_travel_is_refused(self):
    with self.assertRaises(IKError):
      await self.arm.drop_at_location(Coordinate(300.0, 80.0, -50.0), direction=0.0)

  async def test_a_retreat_that_would_leave_the_z_travel_is_refused_too(self):
    # The grip point is inside the travel and the lift is not. Catching it on the lift
    # would mean failing with the plate already in the jaws.
    with self.assertRaises(IKError):
      await self.arm.pick_up_at_location(
        Coordinate(300.0, 80.0, 395.0),
        direction=0.0,
        resource_width=80.0,
        access=StationAccess(clearance=2.0, grasp_offset=20.0),
      )
    self.assertFalse(self._sent(), "nothing should reach the controller")

  async def test_a_shelf_is_measured_by_its_own_lift_not_the_standoff(self):
    # `clearance` is a horizontal standoff on a shelf, so it is not a height at all, and
    # a station near the top of the travel must not be refused for it.
    with patch.object(self.arm, "move_to_location", AsyncMock()):
      await self.arm.drop_at_location(
        Coordinate(300.0, 80.0, 395.0),
        direction=0.0,
        access=StationAccess(approach="horizontal", clearance=100.0, z_above=3.0),
      )


class TestNothingIsCommandedWhileTheArmIsStillMoving(unittest.IsolatedAsyncioTestCase):
  """`moveJ` returns as soon as the controller accepts it, and the connection stays free
  during the motion, so a command sent straight after lands mid-travel. For the gripper that
  is a plate in the jaws: the fingers were opened a step off the grip position, and closing
  them while the arm is still coming down the approach meets the skirt on the way in.

  The settle poll is the barrier. These tests read the wire with the poll marked, so they see
  motion ordering rather than call ordering.
  """

  SETTLED = "<<settled>>"

  def setUp(self):
    self.arm = _make_arm()
    self.wire: list[str] = []

    async def record(command: str) -> str:
      self.wire.append(command)
      return "40.48 84.76 229.84 -312.57 503.0" if command == "wherej" else ""

    self.arm.send_command = AsyncMock(side_effect=record)  # type: ignore[method-assign]

    async def note_settled(*_args: object, **_kwargs: object) -> None:
      self.wire.append(self.SETTLED)

    patcher = patch.object(self.arm, "_wait_for_eom", AsyncMock(side_effect=note_settled))
    patcher.start()
    self.addCleanup(patcher.stop)

  def _assert_the_arm_had_stopped_before_each_gripper_command(self) -> None:
    moves = [i for i, command in enumerate(self.wire) if command.startswith("moveJ")]
    for i, command in enumerate(self.wire):
      if not command.startswith("gripper"):
        continue
      preceding = [m for m in moves if m < i]
      if not preceding:
        continue
      self.assertIn(
        self.SETTLED,
        self.wire[preceding[-1] + 1 : i],
        f"{command!r} was issued with the arm still moving: {self.wire}",
      )

  async def test_a_pick_does_not_close_the_jaws_during_the_descent(self):
    await self.arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0)
    self.assertIn("gripper 2", self.wire)
    self._assert_the_arm_had_stopped_before_each_gripper_command()

  async def test_a_place_does_not_open_the_jaws_during_the_descent(self):
    await self.arm.drop_at_location(_PAD_1, direction=2.03)
    self.assertIn("gripper 1", self.wire)
    self._assert_the_arm_had_stopped_before_each_gripper_command()

  async def test_a_gripper_command_after_a_bare_travel_move_waits_too(self):
    # The same exposure outside pick and place: `move_to_location` returns while the arm
    # is still travelling, so the barrier belongs to the gripper, not to one call site.
    await self.arm.move_to_location(_PAD_1, direction=2.03)
    await self.arm.move_gripper(width=80.0)
    self._assert_the_arm_had_stopped_before_each_gripper_command()

  async def test_the_controller_s_safe_retraction_waits_for_the_arm_too(self):
    await self.arm.move_to_location(_PAD_1, direction=2.03)
    await self.arm.move_to_safe()
    self.assertEqual(self.wire[-2:], [self.SETTLED, "movetosafe"])


class TestPickAndPlaceOnARailArm(unittest.IsolatedAsyncioTestCase):
  """A rail-equipped arm refuses any Cartesian move that does not say where the rail belongs,
  so every leg of a pick has to carry it. The bench arm has no rail, which is why nothing
  here shows up in a hardware run.
  """

  def setUp(self):
    self.arm = _make_arm()
    self.arm._has_rail = True

  def _sent(self) -> list[str]:
    return [c.args[0] for c in mocked(self.arm.send_command).call_args_list]

  async def test_a_pick_runs_to_completion_instead_of_stranding_the_arm(self):
    await self.arm.pick_up_at_location(
      _PAD_1, direction=2.03, resource_width=80.0, rail_position=120.0
    )
    self.assertIn("gripper 2", self._sent(), "the pick never reached the grip")
    self.assertTrue([c for c in self._sent() if c.startswith("MoveRail")])

  async def test_a_place_runs_to_completion_instead_of_stranding_the_arm(self):
    await self.arm.drop_at_location(_PAD_1, direction=2.03, rail_position=120.0)
    self.assertIn("gripper 1", self._sent(), "the place never reached the release")

  async def test_every_leg_is_told_where_the_rail_belongs(self):
    legs: list[object] = []

    async def record(location, direction, **kwargs):
      legs.append(kwargs.get("rail_position"))

    with patch.object(self.arm, "move_to_location", AsyncMock(side_effect=record)):
      await self.arm.pick_up_at_location(
        _PAD_1, direction=2.03, resource_width=80.0, rail_position=120.0
      )
    self.assertEqual(legs, [120.0, 120.0, 120.0])


class TestPickAndPlaceEventsCarryTheStationAccess(unittest.IsolatedAsyncioTestCase):
  """The event payload is built from the call's own arguments, so an argument the payload
  does not name is a TypeError the moment a listener is installed.
  """

  async def test_a_pick_with_an_access_still_emits(self):
    arm = _make_arm()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with patch.object(arm, "move_to_location", AsyncMock()):
      with use_event_bus(event_bus):
        await arm.pick_up_at_location(
          _PAD_1, direction=2.03, resource_width=80.0, access=StationAccess(clearance=20.0)
        )

    self.assertEqual(events[0].name, "precise_flex.pick_up_at_location.started")

  async def test_a_place_with_an_access_still_emits(self):
    arm = _make_arm()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with patch.object(arm, "move_to_location", AsyncMock()):
      with use_event_bus(event_bus):
        await arm.drop_at_location(_PAD_1, direction=2.03, access=StationAccess(clearance=20.0))

    self.assertEqual(events[0].name, "precise_flex.drop_at_location.started")

  async def test_a_pick_at_its_own_speed_still_emits(self):
    arm = _make_arm()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with (
      patch.object(arm, "move_to_location", AsyncMock()),
      patch.object(arm, "_set_speed", AsyncMock()),
      patch.object(arm, "_request_speed", AsyncMock(return_value=100.0)),
    ):
      with use_event_bus(event_bus):
        await arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0, speed_pct=20.0)

    self.assertEqual(events[0].name, "precise_flex.pick_up_at_location.started")
    self.assertEqual(events[0].data["speed_pct"], 20.0)

  async def test_a_place_at_its_own_speed_still_emits(self):
    arm = _make_arm()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with (
      patch.object(arm, "move_to_location", AsyncMock()),
      patch.object(arm, "_set_speed", AsyncMock()),
      patch.object(arm, "_request_speed", AsyncMock(return_value=100.0)),
    ):
      with use_event_bus(event_bus):
        await arm.drop_at_location(_PAD_1, direction=2.03, speed_pct=15.0)

    self.assertEqual(events[0].name, "precise_flex.drop_at_location.started")
    self.assertEqual(events[0].data["speed_pct"], 15.0)


class TestAMoveCanRunAtItsOwnSpeed(unittest.IsolatedAsyncioTestCase):
  """A slow move must not leave the arm slow for everything that follows."""

  def setUp(self):
    self.arm = _make_arm()
    for name in ("_reach_in", "_back_out", "_grip", "_release", "_open_to", "_set_grasp_data"):
      patcher = patch.object(self.arm, name, AsyncMock())
      patcher.start()
      self.addCleanup(patcher.stop)

    self.speeds: list[float] = []

    async def record_set(pct: float) -> None:
      self.speeds.append(pct)

    for name, mock in (
      ("_set_speed", AsyncMock(side_effect=record_set)),
      ("_request_speed", AsyncMock(return_value=100.0)),
    ):
      patcher = patch.object(self.arm, name, mock)
      patcher.start()
      self.addCleanup(patcher.stop)

  async def test_no_speed_asked_for_leaves_the_profile_untouched(self):
    await self.arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0)

    self.assertEqual(self.speeds, [], "a move with no speed of its own must not write one")

  async def test_a_pick_at_its_own_speed_puts_the_prior_speed_back(self):
    await self.arm.pick_up_at_location(_PAD_1, direction=2.03, resource_width=80.0, speed_pct=20.0)

    self.assertEqual(self.speeds, [20.0, 100.0])

  async def test_a_place_at_its_own_speed_puts_the_prior_speed_back(self):
    await self.arm.drop_at_location(_PAD_1, direction=2.03, speed_pct=15.0)

    self.assertEqual(self.speeds, [15.0, 100.0])

  async def test_the_rail_traverse_runs_at_the_move_s_speed_too(self):
    """On a place the arm is carrying the plate down the rail, which is the whole
    reason a move asks to go slowly. Traversing before the speed is set would be
    the one leg still running fast."""
    self.arm._has_rail = True
    order: list[str] = []
    mocked(self.arm._set_speed).side_effect = lambda pct: order.append(f"speed={pct}")

    with patch.object(
      self.arm, "move_rail", AsyncMock(side_effect=lambda mm: order.append("rail"))
    ):
      await self.arm.drop_at_location(_PAD_1, direction=2.03, rail_position=120.0, speed_pct=15.0)

    self.assertEqual(order, ["speed=15.0", "rail", "speed=100.0"])

  async def test_a_speed_the_arm_will_not_accept_is_refused_before_it_moves(self):
    """Rejecting it after the traverse leaves the arm somewhere it was not asked to be."""
    self.arm._has_rail = True
    mocked(self.arm._set_speed).side_effect = ValueError("speed_pct must be 0-100")

    with patch.object(self.arm, "move_rail", AsyncMock()) as rail:
      with self.assertRaises(ValueError):
        await self.arm.drop_at_location(
          _PAD_1, direction=2.03, rail_position=120.0, speed_pct=400.0
        )

    rail.assert_not_called()

  async def test_a_fault_mid_move_still_puts_the_prior_speed_back(self):
    mocked(self.arm._grip).side_effect = PreciseFlexError(
      0, "the gripper closed with nothing in it"
    )

    with self.assertRaises(PreciseFlexError):
      await self.arm.pick_up_at_location(
        _PAD_1, direction=2.03, resource_width=80.0, speed_pct=20.0
      )

    self.assertEqual(
      self.speeds, [20.0, 100.0], "a fault between the move and the restore strands the arm slow"
    )
