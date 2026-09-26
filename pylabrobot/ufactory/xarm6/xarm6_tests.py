import sys
import threading
import types
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.resources import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.ufactory.xarm6.xarm6 import XArm6, XArm6Error


def test_sync_constructor_does_not_require_an_async_backend() -> None:
  with patch(
    "pylabrobot.ufactory.xarm6.xarm6.anyio.CapacityLimiter",
    side_effect=RuntimeError("no active async backend"),
  ):
    driver = XArm6(ip="192.168.1.113")

  assert not driver.is_active
  assert driver.min_gripper_width == 71.0
  assert driver.max_gripper_width == 150.0
  assert driver.park_location is None


def _install_mock_xarm(mock_arm: MagicMock) -> MagicMock:
  mock_xarm = types.ModuleType("xarm")
  mock_wrapper = types.ModuleType("xarm.wrapper")
  mock_wrapper.XArmAPI = MagicMock(return_value=mock_arm)  # type: ignore[attr-defined]
  mock_xarm.wrapper = mock_wrapper  # type: ignore[attr-defined]
  sys.modules["xarm"] = mock_xarm
  sys.modules["xarm.wrapper"] = mock_wrapper
  return mock_wrapper.XArmAPI  # type: ignore[attr-defined,no-any-return]


async def _run_public_operation(driver: XArm6, operation: str) -> None:
  if operation == "motion":
    await driver.move_to_location(Coordinate(1, 2, 3), Rotation())
  elif operation == "gripper":
    await driver.move_gripper(100)
  else:
    assert operation == "halt"
    await driver.halt()


async def _run_arm_mutation(driver: XArm6, operation: str) -> None:
  if operation == "park":
    await driver.park()
  elif operation == "move_to_location":
    await driver.move_to_location(Coordinate(1, 2, 3), Rotation())
  elif operation == "pick_up_at_location":
    await driver.pick_up_at_location(Coordinate(1, 2, 3), Rotation(), 80)
  elif operation == "drop_at_location":
    await driver.drop_at_location(Coordinate(1, 2, 3), Rotation(), 80)
  elif operation == "move_to_joint_position":
    await driver.move_to_joint_position({1: 10})
  elif operation == "pick_up_at_joint_position":
    await driver.pick_up_at_joint_position({1: 10}, 80)
  else:
    assert operation == "drop_at_joint_position"
    await driver.drop_at_joint_position({1: 10}, 80)


class RecordingArm:
  """Software-only xArm SDK fake with controllable synchronous calls."""

  def __init__(
    self,
    events: list[str],
    *,
    fail_event: Optional[str] = None,
    block_event: Optional[str] = None,
    call_started: Optional[threading.Event] = None,
    release_call: Optional[threading.Event] = None,
  ) -> None:
    self.events = events
    self.fail_event = fail_event
    self.block_event = block_event
    self.call_started = call_started
    self.release_call = release_call
    self.call_counts: dict[str, int] = {}

  def _call(self, name: str) -> int:
    count = self.call_counts.get(name, 0) + 1
    self.call_counts[name] = count
    event = f"{name}:{count}"
    self.events.append(event)
    if event == self.block_event:
      assert self.call_started is not None
      assert self.release_call is not None
      self.call_started.set()
      self.release_call.wait(2)
      self.events.append(f"{event}.complete")
    if event == self.fail_event:
      return 7
    return 0

  def clean_error(self) -> int:
    return self._call("clean_error")

  def clean_warn(self) -> int:
    return self._call("clean_warn")

  def motion_enable(self, enable: bool) -> int:
    assert enable is True
    return self._call("motion_enable")

  def set_mode(self, mode: int) -> int:
    assert mode in (0, 2)
    return self._call("set_mode")

  def set_state(self, state: int) -> int:
    assert state == 0
    return self._call("set_state")

  def set_tcp_offset(self, offset: list[float]) -> int:
    assert offset == [0, 0, 50, 0, 0, 0]
    return self._call("set_tcp_offset")

  def set_tcp_load(self, mass: float, center: list[float]) -> int:
    assert mass == 1.5
    assert center == [1, 2, 3]
    return self._call("set_tcp_load")

  def set_gripper_mode(self, mode: int) -> int:
    assert mode == 1
    return self._call("set_gripper_mode")

  def set_gripper_enable(self, enable: bool) -> int:
    assert enable is True
    return self._call("set_gripper_enable")

  def set_position(self, **kwargs: Any) -> int:
    assert kwargs["wait"] is True
    return self._call("set_position")

  def move_gohome(self, **kwargs: Any) -> int:
    assert kwargs["wait"] is True
    return self._call("move_gohome")

  def get_position(self):
    result = self._call("get_position")
    return result, [100, 200, 300, 180, 0, 90]

  def get_servo_angle(self):
    result = self._call("get_servo_angle")
    return result, [10, 20, 30, 40, 50, 60]

  def set_servo_angle(self, **kwargs: Any) -> int:
    assert kwargs["wait"] is True
    return self._call("set_servo_angle")

  def set_gripper_position(self, units: int, **kwargs: Any) -> int:
    del units
    assert kwargs["wait"] is True
    return self._call("set_gripper_position")

  def get_gripper_position(self):
    result = self._call("get_gripper_position")
    return result, 850

  def emergency_stop(self) -> int:
    return self._call("emergency_stop")

  def disconnect(self) -> int:
    return self._call("disconnect")


SETUP_PHASES = [
  "clean_error:1",
  "clean_warn:1",
  "motion_enable:1",
  "set_mode:1",
  "set_state:1",
  "set_tcp_offset:1",
  "set_tcp_load:1",
  "set_gripper_mode:1",
  "set_gripper_enable:1",
  "set_mode:2",
  "set_state:2",
]


class TestXArm6Lifecycle(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("TestXArm6Lifecycle does not accept lifespan options")
    self.events: list[str] = []
    self.arms: list[RecordingArm] = []
    self.constructor_error: Optional[BaseException] = None
    self.constructor_started: Optional[threading.Event] = None
    self.release_constructor: Optional[threading.Event] = None

    def construct(ip: str) -> RecordingArm:
      assert ip == "192.168.1.113"
      self.events.append("construct")
      if self.constructor_started is not None:
        self.constructor_started.set()
        assert self.release_constructor is not None
        self.release_constructor.wait(2)
        self.events.append("construct.complete")
      if self.constructor_error is not None:
        raise self.constructor_error
      arm = self.arms.pop(0)
      return arm

    mock_xarm = types.ModuleType("xarm")
    mock_wrapper = types.ModuleType("xarm.wrapper")
    mock_wrapper.XArmAPI = construct  # type: ignore[attr-defined]
    mock_xarm.wrapper = mock_wrapper  # type: ignore[attr-defined]
    previous_xarm = sys.modules.get("xarm")
    previous_wrapper = sys.modules.get("xarm.wrapper")
    sys.modules["xarm"] = mock_xarm
    sys.modules["xarm.wrapper"] = mock_wrapper

    def restore_modules() -> None:
      if previous_xarm is None:
        sys.modules.pop("xarm", None)
      else:
        sys.modules["xarm"] = previous_xarm
      if previous_wrapper is None:
        sys.modules.pop("xarm.wrapper", None)
      else:
        sys.modules["xarm.wrapper"] = previous_wrapper

    stack.callback(restore_modules)

  def _driver(self) -> XArm6:
    return XArm6(
      ip="192.168.1.113",
      tcp_offset=(0, 0, 50, 0, 0, 0),
      tcp_load=(1.5, [1, 2, 3]),
    )

  def test_uses_native_managed_lifecycle(self) -> None:
    assert XArm6.setup is HasLifespan.setup
    assert XArm6.stop is HasLifespan.stop
    assert XArm6.__aenter__ is HasLifespan.__aenter__
    assert XArm6.__aexit__ is HasLifespan.__aexit__

  async def test_optional_sdk_import_failure_is_clean(self) -> None:
    driver = self._driver()
    sys.modules["xarm"] = None  # type: ignore[assignment]
    sys.modules["xarm.wrapper"] = None  # type: ignore[assignment]

    with pytest.raises(ModuleNotFoundError):
      async with driver:
        pytest.fail("missing SDK reached lifespan body")

    assert driver._arm is None

  async def test_skip_gripper_init_requires_bool(self) -> None:
    driver = self._driver()

    with pytest.raises(TypeError, match="skip_gripper_init must be a bool"):
      async with driver.lifespan(skip_gripper_init=1):
        pytest.fail("invalid option reached lifespan body")

    assert self.events == []
    assert driver._arm is None

  async def test_constructor_failure_does_not_disconnect(self) -> None:
    failure = RuntimeError("constructor failed")
    self.constructor_error = failure
    driver = self._driver()

    with pytest.raises(RuntimeError) as error_info:
      async with driver:
        pytest.fail("failed constructor reached lifespan body")

    assert error_info.value is failure
    assert self.events == ["construct"]
    assert driver._arm is None

  @pytest.mark.parametrize("operation", ["motion", "gripper", "halt"])
  async def test_public_operations_before_lifespan_raise_without_sdk_io(
    self, operation: str
  ) -> None:
    driver = self._driver()

    with pytest.raises(RuntimeError, match="no active lifespan"):
      await _run_public_operation(driver, operation)

    assert self.events == []

  @pytest.mark.parametrize("operation", ["motion", "gripper", "halt"])
  async def test_public_operations_during_entry_raise_without_sdk_io(self, operation: str) -> None:
    started = threading.Event()
    release = threading.Event()
    arm = RecordingArm(
      self.events,
      block_event="clean_error:1",
      call_started=started,
      release_call=release,
    )
    self.arms.append(arm)
    driver = self._driver()

    async def enter_driver() -> None:
      async with driver:
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_driver)
      assert await anyio.to_thread.run_sync(started.wait, 1)
      calls_before = list(self.events)
      try:
        with anyio.fail_after(0.1):
          with pytest.raises(RuntimeError, match="no active lifespan"):
            await _run_public_operation(driver, operation)
      finally:
        release.set()
      assert self.events[: len(calls_before)] == calls_before
      task_group.cancel_scope.cancel()

  @pytest.mark.parametrize("operation", ["motion", "gripper", "halt"])
  async def test_public_operations_during_exit_raise_without_sdk_io(self, operation: str) -> None:
    started = threading.Event()
    release = threading.Event()
    arm = RecordingArm(
      self.events,
      block_event="disconnect:1",
      call_started=started,
      release_call=release,
    )
    self.arms.append(arm)
    driver = self._driver()
    body_started = anyio.Event()
    leave_body = anyio.Event()

    async def use_driver() -> None:
      async with driver:
        body_started.set()
        await leave_body.wait()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_driver)
      await body_started.wait()
      leave_body.set()
      assert await anyio.to_thread.run_sync(started.wait, 1)
      calls_before = list(self.events)
      try:
        with anyio.fail_after(0.1):
          with pytest.raises(RuntimeError, match="no active lifespan"):
            await _run_public_operation(driver, operation)
      finally:
        release.set()
      assert self.events[: len(calls_before)] == calls_before

  @pytest.mark.parametrize("operation", ["motion", "gripper", "halt"])
  async def test_public_operations_after_lifespan_raise_without_sdk_io(
    self, operation: str
  ) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      pass
    events_before = list(self.events)

    with pytest.raises(RuntimeError, match="no active lifespan"):
      await _run_public_operation(driver, operation)

    assert self.events == events_before

  async def test_constructor_cancellation_waits_then_disconnects(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    self.constructor_started = threading.Event()
    self.release_constructor = threading.Event()
    driver = self._driver()
    scopes: list[anyio.CancelScope] = []

    async def enter_driver() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with driver:
          pytest.fail("cancelled constructor reached lifespan body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_driver)
      assert await anyio.to_thread.run_sync(self.constructor_started.wait, 1)
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "disconnect:1" not in self.events
      self.release_constructor.set()

    assert self.events == ["construct", "construct.complete", "disconnect:1"]
    assert driver._arm is None

  @pytest.mark.parametrize("failed_phase", SETUP_PHASES)
  async def test_each_setup_failure_disconnects(self, failed_phase: str) -> None:
    arm = RecordingArm(self.events, fail_event=failed_phase)
    self.arms.append(arm)
    driver = self._driver()

    with pytest.raises(XArm6Error) as error_info:
      async with driver:
        pytest.fail("failed setup reached lifespan body")

    assert error_info.value.code == 7
    assert self.events[-1] == "disconnect:1"
    assert self.events[:-1] == ["construct", *SETUP_PHASES[: SETUP_PHASES.index(failed_phase) + 1]]
    assert driver._arm is None

  @pytest.mark.parametrize("blocked_phase", SETUP_PHASES)
  async def test_each_setup_cancellation_waits_then_disconnects(self, blocked_phase: str) -> None:
    started = threading.Event()
    release = threading.Event()
    arm = RecordingArm(
      self.events,
      block_event=blocked_phase,
      call_started=started,
      release_call=release,
    )
    self.arms.append(arm)
    driver = self._driver()
    scopes: list[anyio.CancelScope] = []

    async def enter_driver() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with driver:
          pytest.fail("cancelled setup reached lifespan body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_driver)
      assert await anyio.to_thread.run_sync(started.wait, 1)
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "disconnect:1" not in self.events
      release.set()

    assert f"{blocked_phase}.complete" in self.events
    assert self.events[-1] == "disconnect:1"
    assert driver._arm is None

  async def test_body_failure_disconnects_and_preserves_failure(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()
    failure = RuntimeError("body failed")

    with pytest.raises(RuntimeError) as error_info:
      async with driver:
        raise failure

    assert error_info.value is failure
    assert self.events[-1] == "disconnect:1"
    assert driver._arm is None

  async def test_body_cancellation_disconnects_before_propagating(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()
    body_started = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def use_driver() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with driver:
          body_started.set()
          await anyio.sleep_forever()
      self.events.append("cancelled")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_driver)
      await body_started.wait()
      scopes[0].cancel()

    assert self.events[-2:] == ["disconnect:1", "cancelled"]
    assert driver._arm is None

  async def test_sequential_reuse_constructs_and_disconnects_each_arm(self) -> None:
    first = RecordingArm(self.events)
    second = RecordingArm(self.events)
    self.arms.extend([first, second])
    driver = self._driver()
    assert driver._sdk_limiter is None

    async with driver:
      assert driver._arm is first
      first_limiter = driver._sdk_limiter
      assert first_limiter is not None
    assert driver._sdk_limiter is None
    async with driver:
      assert driver._arm is second
      second_limiter = driver._sdk_limiter
      assert second_limiter is not None

    assert second_limiter is not first_limiter
    assert self.events.count("construct") == 2
    assert self.events.count("disconnect:1") == 2
    assert driver._arm is None
    assert driver._sdk_limiter is None

  async def test_sdk_calls_are_serialized_in_each_reused_lifespan(self) -> None:
    driver = self._driver()

    for _ in range(2):
      started = threading.Event()
      release = threading.Event()
      arm = RecordingArm(
        self.events,
        block_event="set_position:1",
        call_started=started,
        release_call=release,
      )
      self.arms.append(arm)
      lifespan_event_start = len(self.events)
      emergency_stops = self.events.count("emergency_stop:1")

      async with driver:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(
            driver.move_to_location,
            Coordinate(1, 2, 3),
            Rotation(),
          )
          assert await anyio.to_thread.run_sync(started.wait, 1)
          task_group.start_soon(driver.halt)
          await anyio.sleep(0.01)
          assert self.events.count("emergency_stop:1") == emergency_stops
          release.set()

      lifespan_events = self.events[lifespan_event_start:]
      assert lifespan_events.index("set_position:1.complete") < lifespan_events.index(
        "emergency_stop:1"
      )

  async def test_disconnect_failure_propagates_and_clears_arm(self) -> None:
    arm = RecordingArm(self.events, fail_event="disconnect:1")
    self.arms.append(arm)
    driver = self._driver()

    with pytest.raises(XArm6Error) as error_info:
      async with driver:
        pass

    assert error_info.value.code == 7
    assert self.events[-1] == "disconnect:1"
    assert driver._arm is None

  @pytest.mark.parametrize(
    ("operation", "blocked_event"),
    [("motion", "set_position:1"), ("gripper", "set_gripper_position:1")],
  )
  async def test_cancelled_wait_call_completes_before_disconnect(
    self, operation: str, blocked_event: str
  ) -> None:
    started = threading.Event()
    release = threading.Event()
    arm = RecordingArm(
      self.events,
      block_event=blocked_event,
      call_started=started,
      release_call=release,
    )
    self.arms.append(arm)
    driver = self._driver()
    scopes: list[anyio.CancelScope] = []

    async def use_driver() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with driver:
          if operation == "motion":
            await driver.move_to_location(Coordinate(1, 2, 3), Rotation())
          else:
            await driver.move_gripper(100)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_driver)
      assert await anyio.to_thread.run_sync(started.wait, 1)
      scopes[0].cancel()
      await anyio.sleep(0)
      assert "disconnect:1" not in self.events
      release.set()

    assert self.events[-2:] == [f"{blocked_event}.complete", "disconnect:1"]
    assert driver._arm is None

  async def test_freedrive_scope_orders_entry_body_exit_and_disconnect(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      async with driver.freedrive([0]):
        self.events.append("body")

    assert self.events[-6:] == [
      "set_mode:3",
      "set_state:3",
      "body",
      "set_mode:4",
      "set_state:4",
      "disconnect:1",
    ]

  @pytest.mark.parametrize(
    "operation",
    [
      "park",
      "move_to_location",
      "pick_up_at_location",
      "drop_at_location",
      "move_to_joint_position",
      "pick_up_at_joint_position",
      "drop_at_joint_position",
    ],
  )
  async def test_freedrive_scope_rejects_each_arm_mutation_before_sdk_io(
    self, operation: str
  ) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      async with driver.freedrive([0]):
        calls_before = list(self.events)
        with pytest.raises(RuntimeError, match="freedrive is scoped"):
          await _run_arm_mutation(driver, operation)
        assert self.events == calls_before

  async def test_freedrive_scope_guards_direct_transitions_and_mode_changing_recovery(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      async with driver.freedrive([0]):
        for transition in (
          lambda: driver.start_freedrive_mode([0]),
          driver.stop_freedrive_mode,
          driver.clear_errors,
        ):
          calls_before = list(self.events)
          with pytest.raises(RuntimeError, match="freedrive is scoped"):
            await transition()
          assert self.events == calls_before

  async def test_clear_errors_ends_durable_freedrive_before_later_scope(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      await driver.start_freedrive_mode([0])
      await driver.clear_errors()
      async with driver.freedrive([0]):
        pass

    assert self.events[-5:] == [
      "set_mode:5",
      "set_state:5",
      "set_mode:6",
      "set_state:6",
      "disconnect:1",
    ]

  async def test_freedrive_scope_allows_queries_gripper_and_emergency_halt(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      async with driver.freedrive([0]):
        assert (await driver.request_gripper_pose()).location == Coordinate(100, 200, 300)
        assert await driver.request_joint_position() == {
          1: 10,
          2: 20,
          3: 30,
          4: 40,
          5: 50,
          6: 60,
        }
        assert not await driver.is_gripper_closed()
        await driver.move_gripper(100)
        await driver.halt()

    assert "set_gripper_position:1" in self.events
    assert "emergency_stop:1" in self.events

  @pytest.mark.parametrize("failed_event", ["set_mode:3", "set_state:3"])
  async def test_freedrive_entry_failure_rolls_back_attempted_mode_change(
    self, failed_event: str
  ) -> None:
    arm = RecordingArm(self.events, fail_event=failed_event)
    self.arms.append(arm)
    driver = self._driver()

    with pytest.raises(XArm6Error):
      async with driver:
        async with driver.freedrive([0]):
          pytest.fail("failed freedrive entry reached body")

    failed_index = self.events.index(failed_event)
    assert "set_mode:4" in self.events[failed_index + 1 :]
    assert self.events[-1] == "disconnect:1"

  @pytest.mark.parametrize("blocked_event", ["set_mode:3", "set_state:3"])
  async def test_cancelled_freedrive_entry_waits_for_sdk_then_rolls_back(
    self, blocked_event: str
  ) -> None:
    started = threading.Event()
    release = threading.Event()
    arm = RecordingArm(
      self.events,
      block_event=blocked_event,
      call_started=started,
      release_call=release,
    )
    self.arms.append(arm)
    driver = self._driver()
    scopes: list[anyio.CancelScope] = []

    async def enter_freedrive() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with driver.freedrive([0]):
          await anyio.sleep_forever()

    async with driver:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_freedrive)
        assert await anyio.to_thread.run_sync(started.wait, 1)
        scopes[0].cancel()
        await anyio.sleep(0)
        release.set()

    assert f"{blocked_event}.complete" in self.events
    assert "set_mode:4" in self.events
    assert self.events[-1] == "disconnect:1"

  async def test_cancelled_freedrive_before_worker_dispatch_does_not_touch_sdk(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()
    worker_started = anyio.Event()
    worker_release = threading.Event()
    entry_started = anyio.Event()
    entry_finished = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    def occupy_worker() -> None:
      anyio.from_thread.run_sync(worker_started.set)
      worker_release.wait()

    async def enter_freedrive() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          entry_started.set()
          async with driver.freedrive([0]):
            pytest.fail("cancelled pre-dispatch entry reached scope body")
      finally:
        entry_finished.set()

    async with driver:
      calls_before = list(self.events)
      limiter = driver._sdk_limiter
      assert limiter is not None

      async def occupy_sdk_worker() -> None:
        await anyio.to_thread.run_sync(occupy_worker, limiter=limiter)

      async with anyio.create_task_group() as task_group:
        try:
          task_group.start_soon(occupy_sdk_worker)
          await worker_started.wait()
          task_group.start_soon(enter_freedrive)
          await entry_started.wait()
          await anyio.wait_all_tasks_blocked()
          assert not driver._freedrive_state_unknown
          scopes[0].cancel()
          with anyio.move_on_after(1):
            await entry_finished.wait()
          returned_before_worker_release = entry_finished.is_set()
        finally:
          worker_release.set()

      assert returned_before_worker_release
      assert self.events == calls_before
      assert driver._freedrive_scope_token is None
      assert not driver._freedrive_active
      assert not driver._freedrive_state_unknown

  async def test_freedrive_body_cancellation_restores_position_mode(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()
    entered = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def use_freedrive() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with driver.freedrive([0]):
          entered.set()
          await anyio.sleep_forever()

    async with driver:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_freedrive)
        await entered.wait()
        scopes[0].cancel()

    assert self.events[-3:] == ["set_mode:4", "set_state:4", "disconnect:1"]

  async def test_cleanup_failure_is_retried_before_disconnect(self) -> None:
    arm = RecordingArm(self.events, fail_event="set_mode:4")
    self.arms.append(arm)
    driver = self._driver()

    with pytest.raises(XArm6Error):
      async with driver:
        async with driver.freedrive([0]):
          pass

    assert self.events[-4:] == ["set_mode:4", "set_mode:5", "set_state:4", "disconnect:1"]

  async def test_set_state_cleanup_failure_is_retried_before_disconnect(self) -> None:
    arm = RecordingArm(self.events, fail_event="set_state:4")
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      with pytest.raises(XArm6Error) as error_info:
        async with driver.freedrive([0]):
          pass

      assert error_info.value.code == 7
      assert driver._freedrive_active
      assert driver._freedrive_state_unknown
      assert driver._freedrive_scope_token is None

    assert self.events[-4:] == [
      "set_state:4",
      "set_mode:5",
      "set_state:5",
      "disconnect:1",
    ]

  @pytest.mark.parametrize(
    "operation",
    [
      "park",
      "move_to_location",
      "pick_up_at_location",
      "drop_at_location",
      "move_to_joint_position",
      "pick_up_at_joint_position",
      "drop_at_joint_position",
    ],
  )
  async def test_failed_scope_cleanup_blocks_arm_mutation_until_direct_stop(
    self, operation: str
  ) -> None:
    arm = RecordingArm(self.events, fail_event="set_mode:4")
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      with pytest.raises(XArm6Error):
        async with driver.freedrive([0]):
          pass

      calls_before = list(self.events)
      with pytest.raises(RuntimeError, match="freedrive state requires recovery"):
        await _run_arm_mutation(driver, operation)
      assert self.events == calls_before

      await driver.halt()
      await driver.stop_freedrive_mode()
      await _run_arm_mutation(driver, operation)

  async def test_retry_recovery_clears_durable_freedrive_before_later_scope(self) -> None:
    arm = RecordingArm(self.events, fail_event="move_gohome:1")
    self.arms.append(arm)
    driver = self._driver()

    async with driver:
      await driver.start_freedrive_mode([0])
      await driver._call_sdk(
        arm.move_gohome,
        speed=50,
        mvacc=5000,
        wait=True,
        op="move_gohome",
        num_retries=1,
      )
      async with driver.freedrive([0]):
        pass

    assert self.events[-5:] == [
      "set_mode:5",
      "set_state:5",
      "set_mode:6",
      "set_state:6",
      "disconnect:1",
    ]
    assert "set_mode:7" not in self.events

  async def test_parent_exit_restores_live_scope_before_disconnect_and_invalidates_it(self) -> None:
    arm = RecordingArm(self.events)
    self.arms.append(arm)
    driver = self._driver()
    scope = driver.freedrive([0])

    async with driver:
      await scope.__aenter__()

    assert self.events[-3:] == ["set_mode:4", "set_state:4", "disconnect:1"]
    calls_before = list(self.events)
    await scope.__aexit__(None, None, None)
    assert self.events == calls_before

  async def test_scope_waits_for_in_flight_motion_before_mode_change(self) -> None:
    started = threading.Event()
    release = threading.Event()
    arm = RecordingArm(
      self.events,
      block_event="set_position:1",
      call_started=started,
      release_call=release,
    )
    self.arms.append(arm)
    driver = self._driver()
    entered = anyio.Event()

    async def enter_freedrive() -> None:
      async with driver.freedrive([0]):
        entered.set()

    async with driver:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(driver.move_to_location, Coordinate(1, 2, 3), Rotation())
        assert await anyio.to_thread.run_sync(started.wait, 1)
        task_group.start_soon(enter_freedrive)
        await anyio.sleep(0.01)
        assert "set_mode:3" not in self.events
        release.set()
        await entered.wait()

  async def test_scope_reserves_admission_before_mode_change_completes(self) -> None:
    started = threading.Event()
    release = threading.Event()
    arm = RecordingArm(
      self.events,
      block_event="set_mode:3",
      call_started=started,
      release_call=release,
    )
    self.arms.append(arm)
    driver = self._driver()
    rejected = anyio.Event()

    async def enter_freedrive() -> None:
      async with driver.freedrive([0]):
        pass

    async def attempt_motion() -> None:
      with pytest.raises(RuntimeError, match="freedrive is scoped"):
        await driver.move_to_location(Coordinate(1, 2, 3), Rotation())
      rejected.set()

    async with driver:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_freedrive)
        assert await anyio.to_thread.run_sync(started.wait, 1)
        task_group.start_soon(attempt_motion)
        await anyio.sleep(0.01)
        assert "set_position:1" not in self.events
        release.set()
        await rejected.wait()

  async def test_sequential_reuse_clears_freedrive_ownership(self) -> None:
    self.arms.extend([RecordingArm(self.events), RecordingArm(self.events)])
    driver = self._driver()

    for _ in range(2):
      async with driver:
        async with driver.freedrive([0]):
          pass

    assert self.events.count("construct") == 2
    assert self.events.count("disconnect:1") == 2


class TestXArm6SDK(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("TestXArm6SDK does not accept lifespan options")
    self.mock_arm = MagicMock()
    self.mock_arm.clean_error.return_value = 0
    self.mock_arm.clean_warn.return_value = 0
    self.mock_arm.motion_enable.return_value = 0
    self.mock_arm.set_mode.return_value = 0
    self.mock_arm.set_state.return_value = 0
    self.mock_arm.set_tcp_offset.return_value = 0
    self.mock_arm.set_tcp_load.return_value = 0
    self.mock_arm.set_gripper_mode.return_value = 0
    self.mock_arm.set_gripper_enable.return_value = 0
    self.mock_arm.disconnect.return_value = None
    self.mock_arm.get_position.return_value = [0, [100, 200, 300, 180, 0, 90]]
    self.mock_arm.set_position.return_value = 0

    self.MockXArmAPI = _install_mock_xarm(self.mock_arm)
    stack.callback(sys.modules.pop, "xarm.wrapper", None)
    stack.callback(sys.modules.pop, "xarm", None)

    self.driver = XArm6(ip="192.168.1.113")
    await stack.enter_async_context(self.driver.lifespan())

  async def test_setup(self):
    self.MockXArmAPI.assert_called_once_with("192.168.1.113")
    self.mock_arm.clean_error.assert_called_once()
    self.mock_arm.clean_warn.assert_called_once()
    self.mock_arm.motion_enable.assert_called_once_with(True)
    self.mock_arm.set_mode.assert_called_with(0)
    self.mock_arm.set_state.assert_called_with(0)
    self.mock_arm.set_gripper_mode.assert_called_once_with(1)
    self.mock_arm.set_gripper_enable.assert_called_once_with(True)

  async def test_setup_with_tcp_offset(self):
    driver = XArm6(ip="192.168.1.113", tcp_offset=(0, 0, 50, 0, 0, 0))
    async with driver:
      pass
    self.mock_arm.set_tcp_offset.assert_called_once_with([0, 0, 50, 0, 0, 0])

  async def test_setup_skip_gripper_init(self):
    driver = XArm6(ip="192.168.1.113")
    self.mock_arm.set_gripper_mode.reset_mock()
    self.mock_arm.set_gripper_enable.reset_mock()
    async with driver.lifespan(skip_gripper_init=True):
      pass
    self.mock_arm.set_gripper_mode.assert_not_called()
    self.mock_arm.set_gripper_enable.assert_not_called()

  async def test_call_sdk_command_success(self):
    result = await self.driver._call_sdk(
      self.mock_arm.set_position, x=1, y=2, z=3, op="set_position"
    )
    assert result is None
    self.mock_arm.set_position.assert_called_with(x=1, y=2, z=3)

  async def test_call_sdk_command_failure_raises(self):
    self.mock_arm.set_position.return_value = -2
    with pytest.raises(XArm6Error) as ctx:
      await self.driver._call_sdk(self.mock_arm.set_position, op="set_position")
    assert ctx.value.code == -2

  async def test_call_sdk_query_unwraps_data(self):
    pose = await self.driver._call_sdk(self.mock_arm.get_position, op="get_position")
    assert pose == [100, 200, 300, 180, 0, 90]

  async def test_call_sdk_query_failure_raises(self):
    self.mock_arm.get_position.return_value = [-1, None]
    with pytest.raises(XArm6Error) as ctx:
      await self.driver._call_sdk(self.mock_arm.get_position, op="get_position")
    assert ctx.value.code == -1

  async def test_call_sdk_ignores_none_return(self):
    self.mock_arm.emergency_stop.return_value = None
    result = await self.driver._call_sdk(self.mock_arm.emergency_stop, op="emergency_stop")
    assert result is None

  async def test_call_sdk_retries_after_clear_errors(self):
    self.mock_arm.move_gohome.side_effect = [9, 0]
    self.mock_arm.clean_error.reset_mock()
    await self.driver._call_sdk(
      self.mock_arm.move_gohome, speed=50, op="move_gohome", num_retries=1
    )
    # Called twice (once failing, once succeeding after clear_errors).
    assert self.mock_arm.move_gohome.call_count == 2
    self.mock_arm.clean_error.assert_called_once()

  async def test_call_sdk_reraises_if_all_retries_fail(self):
    self.mock_arm.move_gohome.side_effect = [9, 9]
    with pytest.raises(XArm6Error) as ctx:
      await self.driver._call_sdk(self.mock_arm.move_gohome, op="move_gohome", num_retries=1)
    assert ctx.value.code == 9
    assert self.mock_arm.move_gohome.call_count == 2

  async def test_call_sdk_no_retry_by_default(self):
    self.mock_arm.move_gohome.return_value = 9
    with pytest.raises(XArm6Error):
      await self.driver._call_sdk(self.mock_arm.move_gohome, op="move_gohome")
    assert self.mock_arm.move_gohome.call_count == 1

  async def test_call_sdk_multi_retry(self):
    self.mock_arm.move_gohome.side_effect = [9, 9, 0]
    await self.driver._call_sdk(self.mock_arm.move_gohome, op="move_gohome", num_retries=2)
    assert self.mock_arm.move_gohome.call_count == 3

  async def test_clear_errors_sequence(self):
    self.mock_arm.clean_error.reset_mock()
    self.mock_arm.clean_warn.reset_mock()
    self.mock_arm.motion_enable.reset_mock()
    await self.driver.clear_errors()
    self.mock_arm.clean_error.assert_called_once()
    self.mock_arm.clean_warn.assert_called_once()
    self.mock_arm.motion_enable.assert_called_once_with(True)


class TestXArm6Motion(AnyioTestBase):
  def _make_device(self, **kwargs) -> XArm6:
    """Build an XArm6 sharing the mock arm and the _call_sdk recorder."""
    device = XArm6(ip="192.168.1.113", **kwargs)
    device._arm = self.arm
    device._get_active_arm = lambda: self.arm  # type: ignore[method-assign]
    device._call_sdk = self.call_sdk  # type: ignore[method-assign]
    device.clear_errors = AsyncMock()  # type: ignore[method-assign]
    return device

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    del stack
    if kwargs:
      raise TypeError("TestXArm6Motion does not accept lifespan options")
    self.arm = MagicMock()

    sdk_returns = {
      self.arm.get_position: [100, 200, 300, 180, 0, 90],
      self.arm.get_servo_angle: [10, 20, 30, 40, 50, 60],
      self.arm.get_gripper_position: 850,
    }

    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return sdk_returns.get(func)

    self.call_sdk = AsyncMock(side_effect=call_sdk)
    self.device = self._make_device()

  def _sdk_calls_for(self, func) -> list:
    return [c for c in self.call_sdk.call_args_list if c.args and c.args[0] is func]

  # -- Gripper ---------------------------------------------------------------

  async def test_move_gripper_mm_to_units(self):
    # Default range is 71..150 mm mapped to 0..850 units, so 85 mm → ~151 units.
    await self.device.move_gripper(width=85, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert len(calls) == 1
    assert calls[0].args[1] == 151
    assert calls[0].kwargs["wait"] is True
    assert calls[0].kwargs["speed"] == 0

  async def test_move_gripper_midpoint(self):
    # Midpoint of the 71..150 range is 110.5 mm → 425 units.
    await self.device.move_gripper(width=110.5, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert calls[0].args[1] == 425

  async def test_move_gripper_force_sensing(self):
    await self.device.move_gripper(width=71, force_sensing=True)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert calls[0].args[1] == 0

  async def test_move_gripper_clamped_high(self):
    await self.device.move_gripper(width=200, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert calls[0].args[1] == 850

  async def test_move_gripper_clamped_low(self):
    await self.device.move_gripper(width=-5, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert calls[0].args[1] == 0

  async def test_is_gripper_closed_true(self):
    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return 5

    self.device._call_sdk = AsyncMock(side_effect=call_sdk)  # type: ignore[method-assign]
    assert await self.device.is_gripper_closed()

  async def test_is_gripper_closed_false(self):
    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return 500

    self.device._call_sdk = AsyncMock(side_effect=call_sdk)  # type: ignore[method-assign]
    assert not await self.device.is_gripper_closed()

  # -- Base arm --------------------------------------------------------------

  async def test_halt(self):
    await self.device.halt()
    assert len(self._sdk_calls_for(self.arm.emergency_stop)) == 1

  async def test_park_default_home_uses_retry(self):
    await self.device.park()
    calls = self._sdk_calls_for(self.arm.move_gohome)
    assert len(calls) == 1
    assert calls[0].kwargs["num_retries"] == 1
    assert len(self._sdk_calls_for(self.arm.set_position)) == 0

  async def test_park_with_location(self):
    device = self._make_device(
      park_location=Coordinate(x=250, y=0, z=300),
      park_rotation=Rotation(x=180, y=0, z=0),
    )
    await device.park()
    assert len(self._sdk_calls_for(self.arm.move_gohome)) == 0
    set_pos_calls = self._sdk_calls_for(self.arm.set_position)
    assert len(set_pos_calls) == 1
    assert set_pos_calls[0].kwargs["x"] == 250
    assert set_pos_calls[0].kwargs["y"] == 0
    assert set_pos_calls[0].kwargs["z"] == 300

  async def test_request_gripper_pose(self):
    location = await self.device.request_gripper_pose()
    assert location.location.x == 100
    assert location.location.y == 200
    assert location.location.z == 300
    assert location.rotation.x == 180
    assert location.rotation.y == 0
    assert location.rotation.z == 90

  # -- Cartesian motion ------------------------------------------------------

  async def test_move_to_location_defaults(self):
    await self.device.move_to_location(Coordinate(x=300, y=100, z=200), Rotation(x=180, y=0, z=0))
    calls = self._sdk_calls_for(self.arm.set_position)
    assert len(calls) == 1
    assert calls[0].kwargs["x"] == 300
    assert calls[0].kwargs["y"] == 100
    assert calls[0].kwargs["z"] == 200
    assert calls[0].kwargs["roll"] == 180
    assert calls[0].kwargs["pitch"] == 0
    assert calls[0].kwargs["yaw"] == 0
    assert calls[0].kwargs["speed"] == 100.0
    assert calls[0].kwargs["mvacc"] == 2000.0
    assert calls[0].kwargs["wait"] is True

  async def test_move_to_location_with_motion_profile(self):
    await self.device.move_to_location(
      Coordinate(x=0, y=0, z=0),
      Rotation(),
      speed=250,
      mvacc=3500,
    )
    calls = self._sdk_calls_for(self.arm.set_position)
    assert calls[0].kwargs["speed"] == 250
    assert calls[0].kwargs["mvacc"] == 3500

  async def test_pick_up_at_location_move_then_close(self):
    loc = Coordinate(x=300, y=100, z=50)
    rot = Rotation(x=180, y=0, z=0)
    await self.device.pick_up_at_location(loc, rot, resource_width=80)

    mcalls = self._sdk_calls_for(self.arm.set_position)
    assert len(mcalls) == 1
    assert mcalls[0].kwargs["z"] == 50

    grip_calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert len(grip_calls) == 1
    # 80 mm in 71..150 mm range → (80-71)/(150-71) * 850 ≈ 97 units.
    assert grip_calls[0].args[1] == 97

  async def test_drop_at_location_move_then_open_max(self):
    loc = Coordinate(x=300, y=100, z=50)
    rot = Rotation(x=180, y=0, z=0)
    await self.device.drop_at_location(loc, rot, resource_width=80)

    mcalls = self._sdk_calls_for(self.arm.set_position)
    assert len(mcalls) == 1
    assert mcalls[0].kwargs["z"] == 50

    grip_calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert len(grip_calls) == 1
    assert grip_calls[0].args[1] == 850  # SDK max

  # -- Joints ----------------------------------------------------------------

  async def test_request_joint_position(self):
    result = await self.device.request_joint_position()
    assert result == {1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60}

  async def test_move_to_joint_position_partial(self):
    await self.device.move_to_joint_position({1: 45, 3: -90})
    assert len(self._sdk_calls_for(self.arm.get_servo_angle)) == 1
    set_calls = self._sdk_calls_for(self.arm.set_servo_angle)
    assert len(set_calls) == 1
    assert set_calls[0].kwargs["angle"] == [45, 20, -90, 40, 50, 60]
    assert set_calls[0].kwargs["speed"] == 50.0
    assert set_calls[0].kwargs["mvacc"] == 500.0
    assert set_calls[0].kwargs["wait"] is True

  async def test_move_to_joint_position_with_motion_profile(self):
    await self.device.move_to_joint_position(
      {1: 0},
      speed=120,
      mvacc=900,
    )
    set_calls = self._sdk_calls_for(self.arm.set_servo_angle)
    assert set_calls[0].kwargs["speed"] == 120
    assert set_calls[0].kwargs["mvacc"] == 900

  async def test_pick_up_at_joint_position(self):
    await self.device.pick_up_at_joint_position({1: 0, 2: 0}, resource_width=80)
    assert len(self._sdk_calls_for(self.arm.set_servo_angle)) == 1
    grip_calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert len(grip_calls) == 1
    assert grip_calls[0].args[1] == 97

  async def test_drop_at_joint_position(self):
    await self.device.drop_at_joint_position({1: 0, 2: 0}, resource_width=80)
    assert len(self._sdk_calls_for(self.arm.set_servo_angle)) == 1
    grip_calls = self._sdk_calls_for(self.arm.set_gripper_position)
    assert len(grip_calls) == 1
    assert grip_calls[0].args[1] == 850

  # -- Freedrive -------------------------------------------------------------

  async def test_start_freedrive_mode(self):
    await self.device.start_freedrive_mode(free_axes=[0])
    mode_calls = self._sdk_calls_for(self.arm.set_mode)
    state_calls = self._sdk_calls_for(self.arm.set_state)
    assert mode_calls[0].args[1] == 2
    assert state_calls[0].args[1] == 0

  async def test_stop_freedrive_mode(self):
    await self.device.stop_freedrive_mode()
    mode_calls = self._sdk_calls_for(self.arm.set_mode)
    state_calls = self._sdk_calls_for(self.arm.set_state)
    assert mode_calls[0].args[1] == 0
    assert state_calls[0].args[1] == 0

  # -- Custom configuration --------------------------------------------------

  async def test_custom_gripper_range(self):
    device = self._make_device(gripper_min_mm=50.0, gripper_max_mm=100.0)
    await device.move_gripper(width=75.0, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    # Midpoint of 50..100 mm range → 0.5 * 850 = 425 units.
    assert calls[0].args[1] == 425
