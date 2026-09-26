from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from pylabrobot.brooks.precise_flex import (
  Axis,
  OutOfRangeOfMotionError,
  PreciseFlex,
  PreciseFlexCartesianPose,
  PreciseFlexError,
)
from pylabrobot.brooks.precise_flex.precise_flex import MotionProfile
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.events import EventBus, PLREvent, event_context, use_event_bus
from pylabrobot.io.socket import Socket
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.testing.concurrency import AnyioTestBase


def mocked(method: object) -> AsyncMock:
  """A real method that a test replaced with an ``AsyncMock``.

  Assertions like ``call_args_list`` live on the mock, not on the declared
  method type, so they need narrowing before mypy will accept them.
  """
  return cast(AsyncMock, method)


def _make_arm(closed_gripper_position: float = 500.0) -> PreciseFlex:
  """An arm whose transport is stubbed out, so tests assert on the commands it would send."""
  arm = PreciseFlex(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=closed_gripper_position,
  )
  arm.send_command = AsyncMock(return_value="")  # type: ignore[method-assign]
  return arm


def _make_transaction_arm(io: object) -> PreciseFlex:
  """Return an arm using controlled software I/O while retaining the real command layer."""
  arm = PreciseFlex(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=500.0,
  )
  arm.io = cast(Socket, io)
  return arm


def _make_lock_blocked_arm() -> tuple[PreciseFlex, list[str], anyio.Event, anyio.Event]:
  """Return a real command layer whose active transaction can hold the command lock."""
  commands: list[str] = []
  active_read_started = anyio.Event()
  release_active_reply = anyio.Event()
  io = MagicMock()

  async def write(data: bytes) -> None:
    commands.append(data.decode("ascii").strip())

  async def readline() -> bytes:
    if commands[-1] == "active":
      active_read_started.set()
      await release_active_reply.wait()
    return b"0 1\n"

  io.write = write
  io.readline = readline
  return _make_transaction_arm(io), commands, active_read_started, release_active_reply


def _make_reply_cancelling_arm(
  cancel_command: str, scopes: list[anyio.CancelScope]
) -> tuple[PreciseFlex, list[str], list[str]]:
  """Return a real command layer that cancels after parsing a selected command's reply."""
  commands: list[str] = []
  consumed: list[str] = []
  io = MagicMock()

  async def write(data: bytes) -> None:
    commands.append(data.decode("ascii").strip())

  async def readline() -> bytes:
    consumed.append(commands[-1])
    if commands[-1] == cancel_command:
      scopes[0].cancel()
    return b"0 1\n"

  io.write = write
  io.readline = readline
  return _make_transaction_arm(io), commands, consumed


class _CancelAfterAcquireLock:
  """Lock double that requests cancellation immediately after acquisition."""

  def __init__(self) -> None:
    self.scope: anyio.CancelScope | None = None
    self.acquired = False

  async def __aenter__(self) -> None:
    self.acquired = True
    assert self.scope is not None
    self.scope.cancel()

  async def __aexit__(self, *args: object) -> None:
    del args


class TestPreciseFlexCommandTransactions(AnyioTestBase):
  async def test_concurrent_commands_do_not_cross_replies(self) -> None:
    writes: list[str] = []
    first_write = anyio.Event()
    second_write = anyio.Event()
    io = MagicMock()

    async def write(data: bytes) -> None:
      writes.append(data.decode("ascii").strip())
      if len(writes) == 1:
        first_write.set()
      elif len(writes) == 2:
        second_write.set()

    async def readline() -> bytes:
      with anyio.move_on_after(0.01):
        await second_write.wait()
      return f"0 {writes[-1]}\n".encode("ascii")

    io.write = write
    io.readline = readline
    arm = _make_transaction_arm(io)
    results: dict[str, str] = {}

    async def send(command: str) -> None:
      results[command] = await arm.send_command(command)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send, "first")
      await first_write.wait()
      task_group.start_soon(send, "second")

    assert results == {"first": "first", "second": "second"}
    assert writes == ["first", "second"]

  async def test_cancel_before_transaction_commit_writes_nothing(self) -> None:
    writes: list[str] = []
    first_read_started = anyio.Event()
    release_first_reply = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    queued_scope_ready = anyio.Event()
    io = MagicMock()

    async def write(data: bytes) -> None:
      writes.append(data.decode("ascii").strip())

    async def readline() -> bytes:
      first_read_started.set()
      await release_first_reply.wait()
      return b"0 ok\n"

    io.write = write
    io.readline = readline
    arm = _make_transaction_arm(io)

    async def queued_command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        queued_scope_ready.set()
        await arm.send_command("queued")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(arm.send_command, "active")
      await first_read_started.wait()
      task_group.start_soon(queued_command)
      await queued_scope_ready.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert writes == ["active"]
      release_first_reply.set()

    assert writes == ["active"]

  @pytest.mark.parametrize("command", ["nop", "hp 1 20", "attach 1"])
  async def test_cancel_after_write_consumes_reply_before_propagating(self, command: str) -> None:
    write_completed = anyio.Event()
    release_reply = anyio.Event()
    reply_consumed: list[bytes] = []
    cancellation_seen: list[bool] = []
    scopes: list[anyio.CancelScope] = []
    io = MagicMock()

    async def write(data: bytes) -> None:
      assert data == command.encode("ascii") + b"\n"
      write_completed.set()

    async def readline() -> bytes:
      await release_reply.wait()
      reply_consumed.append(b"0 committed\n")
      return reply_consumed[-1]

    io.write = write
    io.readline = readline
    arm = _make_transaction_arm(io)

    async def send() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await arm.send_command(command)
        except anyio.get_cancelled_exc_class():
          cancellation_seen.append(True)
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await write_completed.wait()
      scopes[0].cancel()
      await anyio.sleep(0)
      assert reply_consumed == []
      assert cancellation_seen == []
      release_reply.set()

    assert reply_consumed == [b"0 committed\n"]
    assert cancellation_seen == [True]

  async def test_failed_command_emits_one_failed_event(self) -> None:
    io = MagicMock()
    io.write = AsyncMock()
    io.readline = AsyncMock(return_value=b"-202 invalid argument\n")
    arm = _make_transaction_arm(io)
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus), event_context(run_id="run-1"):
      with pytest.raises(PreciseFlexError) as error_info:
        await arm.send_command("bad")

    assert error_info.value.replycode == -202
    assert [event.name for event in events] == [
      "precise_flex.firmware_command.started",
      "precise_flex.firmware_command.failed",
    ]
    assert events[-1].data["command"] == "bad"
    assert events[-1].data["error_type"] == "PreciseFlexError"
    assert events[-1].context["run_id"] == "run-1"


class TestPreciseFlex400Gripper(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    del stack, kwargs
    # closed_gripper_position=500 ⇒ min_gripper_width(60mm) maps to 500 units.
    self.arm = _make_arm(closed_gripper_position=500.0)

  def _sent_commands(self) -> list[str]:
    return [c.args[0] for c in mocked(self.arm.send_command).call_args_list]

  async def test_move_gripper_force_sensing_false_opens_with_position(self):
    # 80 mm ⇒ 500 + (80 - 60) = 520 firmware units.
    await self.arm.move_gripper(width=80.0, force_sensing=False)
    assert self._sent_commands() == ["GripOpenPos 520.0", "gripper 1"]

  async def test_move_gripper_force_sensing_true_closes_with_position(self):
    # 60 mm (the closed reference) ⇒ exactly closed_gripper_position.
    await self.arm.move_gripper(width=60.0, force_sensing=True)
    assert self._sent_commands() == ["GripClosePos 500.0", "gripper 2"]

  async def test_move_gripper_position_command_precedes_move(self):
    await self.arm.move_gripper(width=120.0, force_sensing=False)
    commands = self._sent_commands()
    assert commands.index("GripOpenPos 560.0") < commands.index("gripper 1")

  async def test_force_sensing_branches_use_different_firmware_commands(self):
    await self.arm.move_gripper(width=90.0, force_sensing=False)
    await self.arm.move_gripper(width=90.0, force_sensing=True)
    commands = self._sent_commands()
    assert "gripper 1" in commands
    assert "gripper 2" in commands
    assert "GripOpenPos 530.0" in commands
    assert "GripClosePos 530.0" in commands

  async def test_min_max_gripper_width_advertised(self):
    assert self.arm.min_gripper_width == 60.0
    assert self.arm.max_gripper_width == 145.0

  async def test_closed_gripper_position_shifts_units(self):
    # Different anchor ⇒ same width yields a different firmware-unit target.
    arm = _make_arm(closed_gripper_position=1000.0)
    await arm.move_gripper(width=80.0, force_sensing=False)
    commands = [c.args[0] for c in mocked(arm.send_command).call_args_list]
    # 80 mm ⇒ 1000 + (80 - 60) = 1020 units.
    assert commands == ["GripOpenPos 1020.0", "gripper 1"]


class TestPreciseFlexRestraintCommands(AnyioTestBase):
  async def test_direct_methods_preserve_firmware_commands(self) -> None:
    arm = _make_arm()

    await arm.release_brake(2)
    await arm.set_brake(3)
    await arm.zero_torque(True, 5)
    await arm.zero_torque(False)

    assert [call.args[0] for call in mocked(arm.send_command).call_args_list] == [
      "releaseBrake 2",
      "setBrake 3",
      "zeroTorque 1 5",
      "zeroTorque 0",
    ]


class TestPreciseFlexEvents(AnyioTestBase):
  async def test_gripper_event_uses_default_length_unit_field(self):
    arm = _make_arm()
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await arm.move_gripper(width=80.0)

    assert events[0].data["width"] == 80.0
    assert "width_mm" not in events[0].data

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

    assert events[0].name == "precise_flex.move_gripper_joint_position.started"
    assert events[-1].name == "precise_flex.move_gripper_joint_position.completed"
    assert events[0].data["gripper_joint_position"] == 520.0
    assert events[0].data["device"]["name"] == "precise_flex"
    assert events[0].context["resources"] == [{"name": "sample_plate"}]
    assert events[0].context["source"] == {"name": "source_nest"}
    assert events[0].context["destination"] == {"name": "destination_nest"}

    firmware_events = [
      event for event in events if event.name == "precise_flex.firmware_command.started"
    ]
    assert [event.data["command"] for event in firmware_events] == [
      "GripOpenPos 520.0",
      "gripper 1",
    ]
    assert all(
      event.context["resources"] == [{"name": "sample_plate"}] for event in firmware_events
    )


class TestPreciseFlex400OutOfRangeRecovery(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    del stack, kwargs
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
    assert recovered == {Axis.SHOULDER: 92.0, Axis.ELBOW: 13.0}  # wrist excluded
    assert self._move_one_axis_cmds() == [
      "MoveOneAxis 2 92.0 1",
      "MoveOneAxis 3 13.0 1",
    ]  # shoulder (2) before elbow (3)

  async def test_recover_skips_axis_too_far_out_of_range(self):
    """An axis past its limit by more than max_distance is left in place (no unattended big sweep)."""
    # shoulder 120 deg is 27 past the 93 limit, beyond the 5 cap; elbow/wrist in range.
    self._stub_transport("0 120.0 30.0 0.0 0")
    recovered = await self.arm.recover_axes_within_limits()
    assert recovered == {}
    assert self._move_one_axis_cmds() == []

  async def test_recover_axes_cancellation_restores_profile_speed(self) -> None:
    scopes: list[anyio.CancelScope] = []
    arm, commands, consumed = _make_reply_cancelling_arm("Speed 1 20.0", scopes)
    arm._configuration = self.arm._configuration
    arm.request_joint_position = AsyncMock(  # type: ignore[method-assign]
      return_value={
        Axis.BASE: 0.0,
        Axis.SHOULDER: 93.5,
        Axis.ELBOW: 90.0,
        Axis.WRIST: 0.0,
        Axis.GRIPPER: 0.0,
      }
    )
    arm._move_one_axis = AsyncMock()  # type: ignore[method-assign]
    arm._request_speed = AsyncMock(return_value=50.0)  # type: ignore[method-assign]

    async def recover() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await arm.recover_axes_within_limits()

    await recover()

    assert commands == ["Speed 1 20.0", "Speed 1 50.0"]
    assert consumed == commands
    mocked(arm._move_one_axis).assert_not_awaited()

  async def test_recover_cancellation_before_speed_commit_writes_no_profile_values(self) -> None:
    arm, commands, active_read_started, release_active_reply = _make_lock_blocked_arm()
    arm._configuration = self.arm._configuration
    arm.request_joint_position = AsyncMock(  # type: ignore[method-assign]
      return_value={
        Axis.BASE: 0.0,
        Axis.SHOULDER: 93.5,
        Axis.ELBOW: 90.0,
        Axis.WRIST: 0.0,
        Axis.GRIPPER: 0.0,
      }
    )
    speed_read = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def request_speed() -> float:
      speed_read.set()
      return 50.0

    arm._request_speed = request_speed  # type: ignore[assignment]

    async def recover() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await arm.recover_axes_within_limits()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(arm.send_command, "active")
      await active_read_started.wait()
      task_group.start_soon(recover)
      await speed_read.wait()
      await anyio.sleep(0)
      scopes[0].cancel()
      await anyio.sleep(0)
      assert commands == ["active"]
      release_active_reply.set()

    assert commands == ["active"]

  async def test_recover_cancellation_at_precommit_checkpoint_writes_no_profile_values(
    self,
  ) -> None:
    io = MagicMock()
    io.write = AsyncMock()
    io.readline = AsyncMock(return_value=b"0 1\n")
    arm = _make_transaction_arm(io)
    arm._configuration = self.arm._configuration
    arm.request_joint_position = AsyncMock(  # type: ignore[method-assign]
      return_value={
        Axis.BASE: 0.0,
        Axis.SHOULDER: 93.5,
        Axis.ELBOW: 90.0,
        Axis.WRIST: 0.0,
        Axis.GRIPPER: 0.0,
      }
    )
    arm._request_speed = AsyncMock(return_value=50.0)  # type: ignore[method-assign]
    arm._move_one_axis = AsyncMock()  # type: ignore[method-assign]
    lock = _CancelAfterAcquireLock()
    arm._command_lock = lock  # type: ignore[assignment]

    with anyio.CancelScope() as scope:
      lock.scope = scope
      await arm.recover_axes_within_limits()

    assert lock.acquired
    io.write.assert_not_awaited()
    io.readline.assert_not_awaited()
    mocked(arm._move_one_axis).assert_not_awaited()


class TestPreciseFlexParking(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    del stack, kwargs
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

  async def test_park_fills_z_at_three_quarters_travel_and_keeps_orientation(self):
    """park() fills the omitted Z column at 3/4 of the discovered travel and keeps the orientation."""
    self.arm._configuration = self._full_soft_limits()
    # Current pose deliberately differs from the target (base 50 not 300; orientation 10/200/90 not
    # 0/180/180) so the assertion proves park() supplied the fill and orientation, not the live pose.
    self.arm.send_command = AsyncMock(return_value="50 10 200 90 0")  # type: ignore[method-assign]
    self.arm.parking_position = PreciseFlex.PARKING_POSITION_RIGHT
    await self.arm.park()
    # Z filled at 3/4 of 400 = 300; orientation = RIGHT (0/180/180); gripper carried from current.
    assert self._movej_cmds() == ["moveJ 1 300.0 0.0 180.0 180.0 0.0"]

  async def test_park_respects_an_explicit_base(self):
    """A pose that already sets Axis.BASE is parked as-is (no Z fill)."""
    self.arm._configuration = self._full_soft_limits()
    # base 50 in the current pose so the explicit 123 (neither the 300 fill nor the live 50) proves
    # the supplied base is honored and not Z-filled; elbow/wrist carry from current.
    self.arm.send_command = AsyncMock(return_value="50 10 200 90 0")  # type: ignore[method-assign]
    self.arm.parking_position = {Axis.BASE: 123.0, Axis.SHOULDER: 0.0}
    await self.arm.park()
    assert self._movej_cmds() == ["moveJ 1 123.0 0.0 200.0 90.0 0.0"]

  async def test_park_without_position_falls_back_to_movetosafe(self):
    """While parking_position is unset (no configuration), park() uses the firmware movetosafe."""
    await self.arm.park()
    mocked(self.arm.send_command).assert_awaited_once_with("movetosafe")
    assert self._movej_cmds() == []


def test_mm_to_firmware_units_helper() -> None:
  arm = _make_arm(closed_gripper_position=500.0)
  assert arm._mm_to_firmware_units(60.0) == 500.0
  assert arm._mm_to_firmware_units(145.0) == 585.0
  assert arm._mm_to_firmware_units(100.0) == 540.0


def test_named_constants_are_orientation_only_planar_folds() -> None:
  """Parking orientations leave Z unspecified and differ only in arm direction."""
  for pose in (
    PreciseFlex.PARKING_POSITION_BACK,
    PreciseFlex.PARKING_POSITION_RIGHT,
    PreciseFlex.PARKING_POSITION_FRONT,
  ):
    assert Axis.BASE not in pose
    assert pose[Axis.ELBOW] == 180.0
  assert PreciseFlex.PARKING_POSITION_BACK[Axis.SHOULDER] == 90.0
  assert PreciseFlex.PARKING_POSITION_FRONT[Axis.SHOULDER] == -90.0


def test_parking_assignment_rejects_non_axis_keys() -> None:
  arm = _make_arm()
  bad_pose: dict = {"base": 100.0}
  with pytest.raises(ValueError):
    arm.parking_position = bad_pose


def test_parking_assignment_rejects_out_of_limit_value_once_configured() -> None:
  arm = _make_arm()
  arm._configuration = MagicMock(soft_limits={Axis.SHOULDER: (-93.0, 93.0)})
  with pytest.raises(ValueError):
    arm.parking_position = {Axis.SHOULDER: 200.0}


def test_parking_assignment_accepts_named_constant() -> None:
  arm = _make_arm()
  arm.parking_position = PreciseFlex.PARKING_POSITION_FRONT
  pose = arm.parking_position
  assert pose is not None
  assert pose[Axis.SHOULDER] == -90.0


class TestPreciseFlexSmoothCartesianRoute(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    del stack, kwargs
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
    assert self._movej_cmds() == [
      "moveJ 1 110.0 10.0 20.0 30.0 70.0",
      "moveJ 1 120.0 11.0 21.0 31.0 70.0",
    ]
    planned_pose_args = [call.args[0] for call in ik.call_args_list]
    assert [pose.orientation for pose in planned_pose_args] == ["right", "right"]
    assert [pose.wrist for pose in planned_pose_args] == ["ccw", "ccw"]
    # Rail-less PF400 still needs the shoulder/reference rail position for IK.
    assert [pose.rail_position for pose in planned_pose_args] == [123.0, 123.0]

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

    assert self._profile_cmds() == [
      "Profile 1",
      "Profile 1 50.0 0.0 100.0 100.0 0.0 0.0 -1 0",
      "Profile 1 50.0 0.0 100.0 100.0 0.0 0.0 25.0 0",
    ]

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

    assert self._profile_cmds() == []
    assert self._movej_cmds() == ["moveJ 1 110.0 10.0 20.0 30.0 70.0"]
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
      with pytest.raises(ValueError, match="bad target"):
        await self.arm.move_through_cartesian_poses([pose])

    assert self._movej_cmds() == []
    assert self._profile_cmds() == []
    mocked(self.arm._wait_for_eom).assert_not_awaited()

  async def test_cartesian_route_cancellation_halts_then_restores_profile(self) -> None:
    original = MotionProfile(1, 50.0, 0.0, 100.0, 100.0, 0.0, 0.0, 25.0, False)
    target = dict(self.current_joints)
    route_waiting = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    actions: list[str] = []
    self.arm._plan_cartesian_pose_route = AsyncMock(return_value=[target])  # type: ignore[method-assign]
    self.arm.request_motion_profile_values = AsyncMock(return_value=original)  # type: ignore[method-assign]

    async def set_profile(
      profile: int,
      speed: float,
      speed2: float,
      acceleration: float,
      deceleration: float,
      acceleration_ramp: float,
      deceleration_ramp: float,
      in_range: float,
      straight: bool,
    ) -> None:
      await anyio.lowlevel.checkpoint()
      profile_values = MotionProfile(
        profile,
        speed,
        speed2,
        acceleration,
        deceleration,
        acceleration_ramp,
        deceleration_ramp,
        in_range,
        straight,
      )
      actions.append("restore" if profile_values.in_range == 25.0 else "blend")

    async def wait_for_route() -> None:
      route_waiting.set()
      try:
        await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        with anyio.CancelScope(shield=True):
          actions.append("halt")
        raise

    self.arm.set_motion_profile_values = set_profile  # type: ignore[assignment]
    self.arm._wait_for_eom = wait_for_route  # type: ignore[assignment]

    async def move() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await self.arm.move_through_cartesian_poses([self.current_pose])

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(move)
      await route_waiting.wait()
      scopes[0].cancel()

    assert actions == ["blend", "halt", "restore"]

  async def test_cartesian_route_cancellation_after_blend_commit_restores_profile(self) -> None:
    original = MotionProfile(1, 50.0, 0.0, 100.0, 100.0, 0.0, 0.0, 25.0, False)
    target = dict(self.current_joints)
    scopes: list[anyio.CancelScope] = []
    temporary_profile = "Profile 1 50.0 0.0 100.0 100.0 0.0 0.0 -1 0"
    arm, commands, consumed = _make_reply_cancelling_arm(temporary_profile, scopes)
    arm._plan_cartesian_pose_route = AsyncMock(return_value=[target])  # type: ignore[method-assign]
    arm.request_motion_profile_values = AsyncMock(return_value=original)  # type: ignore[method-assign]
    arm._move_j = AsyncMock()  # type: ignore[method-assign]
    arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]

    async def move() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await arm.move_through_cartesian_poses([self.current_pose])

    await move()

    assert commands == [temporary_profile, "Profile 1 50.0 0.0 100.0 100.0 0.0 0.0 25.0 0"]
    assert consumed == commands
    mocked(arm._move_j).assert_not_awaited()
    mocked(arm._wait_for_eom).assert_not_awaited()

  async def test_cartesian_route_cancellation_before_blend_commit_writes_no_profile_values(
    self,
  ) -> None:
    arm, commands, active_read_started, release_active_reply = _make_lock_blocked_arm()
    original = MotionProfile(1, 50.0, 0.0, 100.0, 100.0, 0.0, 0.0, 25.0, False)
    profile_read = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    arm._plan_cartesian_pose_route = AsyncMock(return_value=[self.current_joints])  # type: ignore[method-assign]
    arm._move_j = AsyncMock()  # type: ignore[method-assign]
    arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]

    async def request_profile(profile_index: int) -> MotionProfile:
      assert profile_index == 1
      profile_read.set()
      return original

    arm.request_motion_profile_values = request_profile  # type: ignore[assignment]

    async def move() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await arm.move_through_cartesian_poses([self.current_pose])

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(arm.send_command, "active")
      await active_read_started.wait()
      task_group.start_soon(move)
      await profile_read.wait()
      await anyio.sleep(0)
      scopes[0].cancel()
      await anyio.sleep(0)
      assert commands == ["active"]
      release_active_reply.set()

    assert commands == ["active"]
    mocked(arm._move_j).assert_not_awaited()
    mocked(arm._wait_for_eom).assert_not_awaited()

  async def test_cartesian_route_cancellation_at_precommit_checkpoint_writes_no_profile_values(
    self,
  ) -> None:
    io = MagicMock()
    io.write = AsyncMock()
    io.readline = AsyncMock(return_value=b"0 1\n")
    arm = _make_transaction_arm(io)
    original = MotionProfile(1, 50.0, 0.0, 100.0, 100.0, 0.0, 0.0, 25.0, False)
    arm._plan_cartesian_pose_route = AsyncMock(return_value=[self.current_joints])  # type: ignore[method-assign]
    arm.request_motion_profile_values = AsyncMock(return_value=original)  # type: ignore[method-assign]
    arm._move_j = AsyncMock()  # type: ignore[method-assign]
    arm._wait_for_eom = AsyncMock()  # type: ignore[method-assign]
    lock = _CancelAfterAcquireLock()
    arm._command_lock = lock  # type: ignore[assignment]

    with anyio.CancelScope() as scope:
      lock.scope = scope
      await arm.move_through_cartesian_poses([self.current_pose])

    assert lock.acquired
    io.write.assert_not_awaited()
    io.readline.assert_not_awaited()
    mocked(arm._move_j).assert_not_awaited()
    mocked(arm._wait_for_eom).assert_not_awaited()

  async def test_cartesian_route_failure_restores_profile(self) -> None:
    original = MotionProfile(1, 50.0, 0.0, 100.0, 100.0, 0.0, 0.0, 25.0, False)
    target = dict(self.current_joints)
    profiles: list[MotionProfile] = []
    self.arm._plan_cartesian_pose_route = AsyncMock(return_value=[target])  # type: ignore[method-assign]
    self.arm.request_motion_profile_values = AsyncMock(return_value=original)  # type: ignore[method-assign]
    self.arm._move_j = AsyncMock(side_effect=PreciseFlexError(-202, "move failed"))  # type: ignore[method-assign]

    async def set_profile(
      profile: int,
      speed: float,
      speed2: float,
      acceleration: float,
      deceleration: float,
      acceleration_ramp: float,
      deceleration_ramp: float,
      in_range: float,
      straight: bool,
    ) -> None:
      profiles.append(
        MotionProfile(
          profile,
          speed,
          speed2,
          acceleration,
          deceleration,
          acceleration_ramp,
          deceleration_ramp,
          in_range,
          straight,
        )
      )

    self.arm.set_motion_profile_values = set_profile  # type: ignore[assignment]

    with pytest.raises(PreciseFlexError, match="move failed"):
      await self.arm.move_through_cartesian_poses([self.current_pose])

    assert [profile.in_range for profile in profiles] == [-1, 25.0]


_LOGGER = "pylabrobot.brooks.precise_flex.precise_flex"


class TestPreciseFlex400AutoRecoverOnMove(AnyioTestBase):
  """A commanded move that finds an axis out of range: default recovers; opt-out raises."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    del stack, kwargs
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

  async def test_opted_out_raises_and_does_not_move_or_recover(self) -> None:
    """Opted out: an out-of-range axis raises OutOfRangeOfMotionError; no recovery, no moveJ."""
    self.arm._recover_out_of_range = False
    self._stub("0 93.5 90.0 0.0 0")  # base shoulder elbow wrist gripper; shoulder 93.5 > 93
    with pytest.raises(OutOfRangeOfMotionError) as error_info:
      await self.arm.move_to_joint_position({Axis.SHOULDER: 0.0})
    assert Axis.SHOULDER in error_info.value.axes
    assert self._cmds("MoveOneAxis") == []
    assert self._cmds("moveJ") == []

  async def test_on_recovers_offender_then_retries_move(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    """Opt-in on: the offending axis is nudged in range (MoveOneAxis), then the moveJ is retried."""
    self.arm._recover_out_of_range = True
    self._stub("0 93.5 90.0 0.0 0", recovered="0 92.0 90.0 0.0 0")
    with caplog.at_level("INFO", logger=_LOGGER):
      await self.arm.move_to_joint_position({Axis.SHOULDER: 0.0})
    assert self._cmds("MoveOneAxis") == ["MoveOneAxis 2 92.0 1"]
    assert len(self._cmds("moveJ")) == 1
    assert "commanded move blocked" in caplog.text
    assert "retried successfully" in caplog.text

  async def test_on_but_unrecoverable_reraises_once_without_moving(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    """Opt-in on but the axis is too far out (recovery skips it): re-raise after one try, no loop."""
    self.arm._recover_out_of_range = True
    self._stub("0 120.0 90.0 0.0 0")  # shoulder 27 past the limit, beyond the recovery cap
    with caplog.at_level("ERROR", logger=_LOGGER):
      with pytest.raises(OutOfRangeOfMotionError):
        await self.arm.move_to_joint_position({Axis.SHOULDER: 0.0})
    assert self._cmds("moveJ") == []
    assert "auto-recovery did not clear" in caplog.text

  async def test_in_range_move_reads_position_once(self):
    """Happy path: the out-of-range check reuses the merge read, so a move issues a single wherej
    before moveJ (no redundant position read)."""
    self._stub("0 0.0 90.0 0.0 0")  # all axes in range
    await self.arm.move_to_joint_position({Axis.SHOULDER: 10.0})
    assert self._cmds("wherej") == ["wherej"]
    assert len(self._cmds("moveJ")) == 1

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
      with pytest.raises(OutOfRangeOfMotionError) as error_info:
        await self.arm.move_to_location(Coordinate(400.0, 0.0, 200.0), 0.0)
    assert Axis.SHOULDER in error_info.value.axes
    assert self._cmds("moveJ") == []
