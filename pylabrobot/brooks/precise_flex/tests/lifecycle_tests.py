from typing import Any, Awaitable, Callable, Optional, cast

import anyio
import pytest

from pylabrobot.brooks.precise_flex import PreciseFlex, PreciseFlexCartesianPose
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.events import EventBus, PLREvent, event_context, use_event_bus
from pylabrobot.io.socket import Socket
from pylabrobot.resources import Coordinate, Rotation
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingSocket(HasLifespan):
  """Record PreciseFlex transport effects without opening hardware."""

  def __init__(
    self,
    events: list[str],
    *,
    fail_command: Optional[str] = None,
    block_command: Optional[str] = None,
    command_started: Optional[anyio.Event] = None,
    release_command: Optional[anyio.Event] = None,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.events = events
    self.fail_command = fail_command
    self.block_command = block_command
    self.command_started = command_started
    self.release_command = release_command
    self.entry_error = entry_error
    self.exit_error = exit_error
    self._host = "ignored"
    self._port = 0

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    if kwargs:
      raise TypeError("RecordingSocket does not accept lifespan options")
    self.events.append("socket.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("socket.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    del timeout
    command = data.decode("ascii").rstrip()
    await anyio.lowlevel.checkpoint()
    self.events.append(f"write:{command}")
    if command == self.fail_command:
      raise RuntimeError(f"failed at {command}")
    if command == self.block_command:
      if self.command_started is not None:
        self.command_started.set()
      if self.release_command is not None:
        await self.release_command.wait()
      else:
        await anyio.sleep_forever()

  async def readline(self, timeout: Optional[float] = None) -> bytes:
    del timeout
    await anyio.lowlevel.checkpoint()
    # Lifecycle commands ignore the value. The malformed first configuration value
    # exercises setup's existing best-effort discovery path and ends discovery early.
    return b"0 invalid\r\n"


def make_arm(
  events: list[str],
  **socket_kwargs: Any,
) -> tuple[PreciseFlex, RecordingSocket]:
  """Construct a PreciseFlex with its external Socket replaced by a recording double."""
  arm = PreciseFlex(
    host="ignored",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=500.0,
    port=0,
  )
  recording_socket = RecordingSocket(events, **socket_kwargs)
  arm.io = cast(Socket, recording_socket)
  return arm, recording_socket


ENTRY = [
  "socket.enter",
  "write:mode 0",
  "write:hp 1 20",
  "write:attach 1",
  "write:home",
  "write:freemode -1",
  "write:pd 16078",
]
EXIT = ["write:attach 0", "write:hp 0", "write:exit", "socket.exit"]


def test_managed_methods_are_inherited() -> None:
  assert PreciseFlex.setup is HasLifespan.setup
  assert PreciseFlex.stop is HasLifespan.stop
  assert PreciseFlex.__aenter__ is HasLifespan.__aenter__
  assert PreciseFlex.__aexit__ is HasLifespan.__aexit__


class TestPreciseFlexLifecycle(AnyioTestBase):
  async def test_entry_and_exit_order(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    async with arm:
      assert arm.is_active
      events.append("body")

    assert events == ENTRY + ["body"] + EXIT
    assert not arm.is_active

  async def test_skip_home_omits_only_home(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    async with arm.lifespan(skip_home=True):
      events.append("body")

    assert events == [event for event in ENTRY if event != "write:home"] + ["body"] + EXIT

  async def test_rejects_unknown_lifespan_options_before_socket_entry(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    with pytest.raises(
      TypeError,
      match="unexpected keyword argument 'unknown'",
    ):
      async with arm.lifespan(unknown=True):
        pytest.fail("invalid options must not reach the body")
    assert events == []

  async def test_rejects_non_boolean_skip_home_before_socket_entry(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    with pytest.raises(TypeError, match="skip_home must be a bool"):
      async with arm.lifespan(skip_home=1):
        pytest.fail("invalid options must not reach the body")
    assert events == []

  async def test_socket_entry_failure_issues_no_commands(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events, entry_error=RuntimeError("socket open failed"))

    with pytest.raises(RuntimeError, match="socket open failed"):
      async with arm:
        pytest.fail("failed entry must not reach the body")

    assert events == ["socket.enter"]
    assert not arm.is_active

  async def test_power_failure_runs_exit_and_closes_socket(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events, fail_command="hp 1 20")

    with pytest.raises(RuntimeError, match="failed at hp 1 20"):
      async with arm:
        pytest.fail("failed entry must not reach the body")

    assert events == [
      "socket.enter",
      "write:mode 0",
      "write:hp 1 20",
      "write:exit",
      "socket.exit",
    ]
    assert not arm.is_active

  async def test_attach_failure_powers_off_then_exits(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events, fail_command="attach 1")

    with pytest.raises(RuntimeError, match="failed at attach 1"):
      async with arm:
        pytest.fail("failed entry must not reach the body")

    assert events[-3:] == ["write:hp 0", "write:exit", "socket.exit"]
    assert "write:attach 0" not in events
    assert not arm.is_active

  async def test_cancellation_during_home_completes_all_cleanup(self) -> None:
    events: list[str] = []
    home_started = anyio.Event()
    release_home = anyio.Event()
    arm, _ = make_arm(
      events,
      block_command="home",
      command_started=home_started,
      release_command=release_home,
    )

    async def run_arm() -> None:
      try:
        async with arm:
          pytest.fail("cancelled entry must not reach the body")
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_arm)
      await home_started.wait()
      task_group.cancel_scope.cancel()
      release_home.set()

    assert events[-5:] == EXIT + ["cancelled"]
    assert not arm.is_active

  async def test_body_cancellation_completes_all_cleanup(self) -> None:
    events: list[str] = []
    body_started = anyio.Event()
    arm, _ = make_arm(events)

    async def run_arm() -> None:
      try:
        async with arm:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_arm)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-5:] == EXIT + ["cancelled"]
    assert not arm.is_active

  async def test_detach_failure_does_not_skip_later_cleanup(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events, fail_command="attach 0")

    with pytest.raises(RuntimeError, match="failed at attach 0"):
      async with arm.lifespan(skip_home=True):
        pass

    assert events[-4:] == EXIT
    assert not arm.is_active

  async def test_cleanup_failures_remain_in_exception_chain(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(
      events,
      fail_command="attach 0",
      exit_error=RuntimeError("socket close failed"),
    )

    with pytest.raises(RuntimeError, match="socket close failed") as error_info:
      async with arm.lifespan(skip_home=True):
        pass

    messages: list[str] = []
    error: Optional[BaseException] = error_info.value
    while error is not None:
      messages.append(str(error))
      error = error.__context__
    assert "failed at attach 0" in messages
    assert "socket close failed" in messages
    assert events[-4:] == EXIT

  async def test_sequential_reuse_runs_complete_cycles(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    async with arm.lifespan(skip_home=True):
      pass
    async with arm.lifespan(skip_home=True):
      pass

    cycle = [event for event in ENTRY if event != "write:home"] + EXIT
    assert events == cycle + cycle

  async def test_freedrive_scope_orders_events_body_and_commands(self) -> None:
    events: list[str] = []
    emitted: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(emitted.append)
    arm, _ = make_arm(events)

    with use_event_bus(event_bus):
      async with arm.lifespan(skip_home=True):
        emitted.clear()
        async with arm.freedrive([1, 3]):
          events.append("body")

    assert (
      events
      == [event for event in ENTRY if event != "write:home"]
      + [
        "write:freemode 1",
        "write:freemode 3",
        "body",
        "write:freemode -1",
      ]
      + EXIT
    )
    transition_names = [
      event.name
      for event in emitted
      if event.name.startswith("precise_flex.start_freedrive")
      or event.name.startswith("precise_flex.stop_freedrive")
    ]
    assert transition_names == [
      "precise_flex.start_freedrive.started",
      "precise_flex.start_freedrive.completed",
      "precise_flex.stop_freedrive.started",
      "precise_flex.stop_freedrive.completed",
    ]

  async def test_freedrive_scope_uses_no_rail_defaults(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    async with arm.lifespan(skip_home=True):
      async with arm.freedrive():
        pass

    freedrive_commands = [event for event in events if event.startswith("write:freemode")]
    assert freedrive_commands == [
      "write:freemode -1",
      "write:freemode 1",
      "write:freemode 2",
      "write:freemode 3",
      "write:freemode 4",
      "write:freemode -1",
    ]

  async def test_partial_axis_failure_rolls_back_before_scope_entry_fails(self) -> None:
    events: list[str] = []
    arm, socket = make_arm(events)

    async with arm.lifespan(skip_home=True):
      socket.fail_command = "freemode 2"
      with pytest.raises(RuntimeError, match="failed at freemode 2"):
        async with arm.freedrive([1, 2, 3]):
          pytest.fail("partial entry reached the body")
      socket.fail_command = None

    assert events[-7:] == [
      "write:freemode 1",
      "write:freemode 2",
      "write:freemode -1",
      *EXIT,
    ]

  async def test_body_cancellation_stops_freedrive_before_propagating(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)
    entered = anyio.Event()

    async def use_freedrive() -> None:
      try:
        async with arm.freedrive([1]):
          entered.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with arm.lifespan(skip_home=True):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_freedrive)
        await entered.wait()
        task_group.cancel_scope.cancel()

    assert events.index("write:freemode -1", len(ENTRY)) < events.index("cancelled")

  async def test_scope_rejects_direct_transitions_and_motion_but_allows_halt(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    async with arm.lifespan(skip_home=True):
      async with arm.freedrive([1]):
        with pytest.raises(RuntimeError, match="scope"):
          await arm.start_freedrive_mode([2])
        with pytest.raises(RuntimeError, match="scope"):
          await arm.stop_freedrive_mode()
        with pytest.raises(RuntimeError, match="freedrive"):
          await arm.home()
        await arm.halt()

    scope_events = events[len([event for event in ENTRY if event != "write:home"]) : -len(EXIT)]
    assert scope_events == [
      "write:freemode 1",
      "write:halt",
      "write:freemode -1",
    ]

  @pytest.mark.parametrize(
    "operation",
    [
      lambda arm: arm.recover_from_fault(),
      lambda arm: arm.recover_axes_within_limits(),
      lambda arm: arm.home(),
      lambda arm: arm.home_all(),
      lambda arm: arm.release_brake(1),
      lambda arm: arm.set_brake(1),
      lambda arm: arm.zero_torque(True, 1),
      lambda arm: arm.zero_torque(False),
      lambda arm: arm.change_config(),
      lambda arm: arm.change_config2(),
      lambda arm: arm.move_to_joint_position({}),
      lambda arm: arm.move_to_location(Coordinate(), 0),
      lambda arm: arm.move_through_cartesian_poses(
        [PreciseFlexCartesianPose(Coordinate(), Rotation())]
      ),
      lambda arm: arm.move_gripper(80),
      lambda arm: arm.move_gripper_joint_position(520),
      lambda arm: arm.move_rail(10),
      lambda arm: arm.pick_up_at_joint_position({}, 80),
      lambda arm: arm.drop_at_joint_position({}, 80),
      lambda arm: arm.pick_up_at_location(Coordinate(), 0, 80),
      lambda arm: arm.drop_at_location(Coordinate(), 0, 80),
      lambda arm: arm.park(),
    ],
  )
  async def test_scope_rejects_each_public_motion_before_device_mutation(
    self, operation: Callable[[PreciseFlex], Awaitable[None]]
  ) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    async with arm.lifespan(skip_home=True):
      arm._has_rail = True
      async with arm.freedrive([1]):
        before = list(events)
        with pytest.raises(RuntimeError, match="freedrive"):
          await operation(arm)
        assert events == before

  async def test_scope_reserves_motion_guard_before_entry_command_completes(self) -> None:
    events: list[str] = []
    command_started = anyio.Event()
    release_command = anyio.Event()
    arm, _ = make_arm(
      events,
      block_command="freemode 1",
      command_started=command_started,
      release_command=release_command,
    )
    scope = arm.freedrive([1])

    async with arm.lifespan(skip_home=True):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(scope.__aenter__)
        await command_started.wait()
        try:
          with anyio.fail_after(0.1):
            with pytest.raises(RuntimeError, match="freedrive"):
              await arm.home()
        finally:
          release_command.set()
      await scope.__aexit__(None, None, None)

    assert "write:home" not in events

  async def test_scope_waits_for_in_flight_motion_before_reserving_ownership(self) -> None:
    events: list[str] = []
    actions: list[str] = []
    motion_paused = anyio.Event()
    release_motion = anyio.Event()
    scope_entered = anyio.Event()
    arm, _ = make_arm(events)
    lifespan = arm.lifespan(skip_home=True)
    scope = arm.freedrive([1])

    async def pause_speed(speed_pct: float) -> None:
      assert speed_pct == 10
      actions.append("motion.paused")
      motion_paused.set()
      await release_motion.wait()

    async def finish_motion(build_target: object) -> None:
      del build_target
      actions.append("motion.mutated")

    async def enter_scope() -> None:
      await scope.__aenter__()
      actions.append("scope.entered")
      scope_entered.set()

    async def run_motion() -> None:
      await arm.move_to_joint_position({}, 10)

    await lifespan.__aenter__()
    arm._set_speed = pause_speed  # type: ignore[method-assign]
    arm._guarded_move_j = finish_motion  # type: ignore[method-assign]
    try:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_motion)
        await motion_paused.wait()
        task_group.start_soon(enter_scope)
        with anyio.move_on_after(0.05):
          await scope_entered.wait()
        entered_before_release = scope_entered.is_set()
        release_motion.set()
      assert not entered_before_release
      assert actions == ["motion.paused", "motion.mutated", "scope.entered"]
      await scope.__aexit__(None, None, None)
    finally:
      release_motion.set()
      await lifespan.__aexit__(None, None, None)

  async def test_cancelled_scope_waiting_for_in_flight_motion_mutates_nothing(self) -> None:
    events: list[str] = []
    motion_paused = anyio.Event()
    release_motion = anyio.Event()
    scope_waiting = anyio.Event()
    scope_finished = anyio.Event()
    transition_started = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    arm, _ = make_arm(events)

    async def pause_speed(speed_pct: float) -> None:
      del speed_pct
      motion_paused.set()
      await release_motion.wait()

    async def finish_motion(build_target: object) -> None:
      del build_target

    async def start_freedrive(free_axes: Optional[list[int]] = None) -> None:
      del free_axes
      transition_started.set()
      await anyio.sleep_forever()

    async def enter_scope() -> None:
      scope = arm.freedrive([1])
      entered = False
      try:
        with anyio.CancelScope() as cancel_scope:
          scopes.append(cancel_scope)
          scope_waiting.set()
          await scope.__aenter__()
          entered = True
      finally:
        if entered:
          await scope.__aexit__(None, None, None)
        scope_finished.set()

    async def run_motion() -> None:
      await arm.move_to_joint_position({}, 10)

    async with arm.lifespan(skip_home=True):
      arm._set_speed = pause_speed  # type: ignore[method-assign]
      arm._guarded_move_j = finish_motion  # type: ignore[method-assign]
      arm._start_freedrive_transition = start_freedrive  # type: ignore[method-assign]
      baseline = list(events)
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_motion)
        await motion_paused.wait()
        task_group.start_soon(enter_scope)
        await scope_waiting.wait()
        with anyio.move_on_after(0.05):
          await transition_started.wait()
        transition_started_before_cancel = transition_started.is_set()
        scopes[0].cancel()
        await scope_finished.wait()
        release_motion.set()

      assert events == baseline
      assert not transition_started_before_cancel

  async def test_parent_teardown_waits_for_admitted_motion_before_cleanup(self) -> None:
    events: list[str] = []
    actions: list[str] = []
    motion_paused = anyio.Event()
    release_motion = anyio.Event()
    parent_exited = anyio.Event()
    arm, _ = make_arm(events)
    lifespan = arm.lifespan(skip_home=True)

    async def pause_speed(speed_pct: float) -> None:
      del speed_pct
      motion_paused.set()
      await release_motion.wait()

    async def finish_motion(build_target: object) -> None:
      del build_target
      actions.append("motion.mutated")

    async def exit_parent() -> None:
      await lifespan.__aexit__(None, None, None)
      actions.append("parent.exited")
      parent_exited.set()

    async def run_motion() -> None:
      await arm.move_to_joint_position({}, 10)

    await lifespan.__aenter__()
    arm._set_speed = pause_speed  # type: ignore[method-assign]
    arm._guarded_move_j = finish_motion  # type: ignore[method-assign]
    try:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_motion)
        await motion_paused.wait()
        task_group.start_soon(exit_parent)
        with anyio.move_on_after(0.05):
          await parent_exited.wait()
        exited_before_release = parent_exited.is_set()
        release_motion.set()
      assert not exited_before_release
      assert actions == ["motion.mutated", "parent.exited"]
    finally:
      release_motion.set()
      if arm.is_active:
        await lifespan.__aexit__(None, None, None)

  async def test_scope_rejects_preexisting_durable_freedrive(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    async with arm.lifespan(skip_home=True):
      await arm.start_freedrive_mode([1])
      with pytest.raises(RuntimeError, match="already active"):
        async with arm.freedrive([2]):
          pytest.fail("scope adopted durable freedrive")
      await arm.stop_freedrive_mode()

  async def test_failed_scope_cleanup_is_retryable_and_scope_state_is_reusable(self) -> None:
    events: list[str] = []
    arm, socket = make_arm(events)

    async with arm.lifespan(skip_home=True):
      socket.fail_command = "freemode -1"
      with pytest.raises(RuntimeError, match="failed at freemode -1"):
        async with arm.freedrive([1]):
          pass
      socket.fail_command = None
      await arm.stop_freedrive_mode()
      async with arm.freedrive([2]):
        pass

    assert [event for event in events if event.startswith("write:freemode")] == [
      "write:freemode -1",
      "write:freemode 1",
      "write:freemode -1",
      "write:freemode -1",
      "write:freemode 2",
      "write:freemode -1",
    ]

  async def test_parent_exit_force_stops_live_scope_before_detach_and_invalidates_it(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)
    lifespan = arm.lifespan(skip_home=True)
    scope = arm.freedrive([1])

    await lifespan.__aenter__()
    await scope.__aenter__()
    await lifespan.__aexit__(None, None, None)
    commands_after_parent_exit = list(events)
    await scope.__aexit__(None, None, None)

    assert commands_after_parent_exit[-5:] == ["write:freemode -1", *EXIT]
    assert events == commands_after_parent_exit

  async def test_sequential_freedrive_scope_reuse_runs_complete_cycles(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    async with arm.lifespan(skip_home=True):
      async with arm.freedrive([1]):
        pass
      async with arm.freedrive([2]):
        pass

    assert [event for event in events if event.startswith("write:freemode")] == [
      "write:freemode -1",
      "write:freemode 1",
      "write:freemode -1",
      "write:freemode 2",
      "write:freemode -1",
    ]

  async def test_lifecycle_events_preserve_context_and_outcomes(self) -> None:
    resources = [{"name": "workcell"}]
    lifecycle_names = {
      "precise_flex.setup.started",
      "precise_flex.setup.completed",
      "precise_flex.setup.failed",
      "precise_flex.stop.started",
      "precise_flex.stop.completed",
      "precise_flex.stop.failed",
    }

    async def collect_events(
      *, fail_command: Optional[str] = None
    ) -> tuple[list[PLREvent], Optional[BaseException]]:
      emitted: list[PLREvent] = []
      event_bus = EventBus()
      event_bus.subscribe(emitted.append)
      arm, _ = make_arm([], fail_command=fail_command)
      error: Optional[BaseException] = None
      try:
        with use_event_bus(event_bus), event_context(resources=resources):
          async with arm.lifespan(skip_home=True):
            pass
      except BaseException as exc:
        error = exc
      return emitted, error

    completed, completed_error = await collect_events()
    assert completed_error is None
    completed_lifecycle = [event for event in completed if event.name in lifecycle_names]
    assert [event.name for event in completed_lifecycle] == [
      "precise_flex.setup.started",
      "precise_flex.setup.completed",
      "precise_flex.stop.started",
      "precise_flex.stop.completed",
    ]
    assert all(event.context["resources"] == resources for event in completed_lifecycle)
    assert completed_lifecycle[0].data["skip_home"] is True
    assert (
      completed_lifecycle[0].context["operation_id"]
      == completed_lifecycle[1].context["operation_id"]
    )
    assert (
      completed_lifecycle[2].context["operation_id"]
      == completed_lifecycle[3].context["operation_id"]
    )

    setup_failed, setup_error = await collect_events(fail_command="attach 1")
    assert isinstance(setup_error, RuntimeError)
    assert [event.name for event in setup_failed if event.name in lifecycle_names] == [
      "precise_flex.setup.started",
      "precise_flex.setup.failed",
    ]

    stop_failed, stop_error = await collect_events(fail_command="attach 0")
    assert isinstance(stop_error, RuntimeError)
    stop_lifecycle = [event for event in stop_failed if event.name in lifecycle_names]
    assert [event.name for event in stop_lifecycle] == [
      "precise_flex.setup.started",
      "precise_flex.setup.completed",
      "precise_flex.stop.started",
      "precise_flex.stop.failed",
    ]
    assert stop_lifecycle[-1].data["error_type"] == "RuntimeError"
    assert stop_lifecycle[-1].data["error_message"] == "failed at attach 0"


class TestPreciseFlexSetupCompatibility(AnyioTestBase):
  _anyio_backends = ["asyncio"]

  async def test_setup_compatibility_forwards_skip_home(self) -> None:
    events: list[str] = []
    arm, _ = make_arm(events)

    with pytest.warns(DeprecationWarning):
      await arm.setup(skip_home=True)
    assert arm.is_active
    assert "write:home" not in events

    await arm.stop()
    assert not arm.is_active
    assert events[-4:] == EXIT
