from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Union, cast

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.inheco.control_box import InhecoTECControlBox
from pylabrobot.inheco.thermoshake import InhecoThermoShake
from pylabrobot.resources import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase

CommandEffect = Union[BaseException, Callable[[], Awaitable[None]]]


class FakeControlBox(HasLifespan):
  """Record commands without owning or accessing hardware."""

  def __init__(
    self,
    events: List[str],
    effects: Optional[Dict[str, List[CommandEffect]]] = None,
  ) -> None:
    super().__init__()
    self.events = events
    self.effects: Dict[str, Deque[CommandEffect]] = defaultdict(deque)
    for command, command_effects in (effects or {}).items():
      self.effects[command].extend(command_effects)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("FakeControlBox does not accept lifespan options")
    self.events.append("control_box.enter")
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    self.events.append("control_box.exit")

  async def send_command(self, command: str) -> str:
    await anyio.lowlevel.checkpoint_if_cancelled()
    self.events.append(f"{command}.started")
    with anyio.CancelScope(shield=True):
      if self.effects[command]:
        effect = self.effects[command].popleft()
        if isinstance(effect, BaseException):
          raise effect
        await effect()
      self.events.append(f"{command}.complete")
    await anyio.lowlevel.checkpoint()
    return ""


def make_thermoshake(control_box: FakeControlBox) -> InhecoThermoShake:
  """Build an Inheco ThermoShake with a hardware-free borrowed control box."""
  return InhecoThermoShake(
    index=1,
    control_box=cast(InhecoTECControlBox, control_box),
    name="thermoshake",
    size_x=147,
    size_y=104,
    size_z=118,
    child_location=Coordinate(x=9.62, y=9.22, z=109.9),
  )


class TestInhecoThermoShake(AnyioTestBase):
  async def test_cancelled_timed_shake_completes_stop_command(self) -> None:
    events: list[str] = []
    shaking_started = anyio.Event()

    async def mark_started() -> None:
      shaking_started.set()

    control_box = FakeControlBox(events=events, effects={"1ASE1": [mark_started]})
    thermoshake = make_thermoshake(control_box)

    async def shake() -> None:
      try:
        await thermoshake.shake(speed=500, duration=60)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with control_box:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(shake)
        await shaking_started.wait()
        task_group.cancel_scope.cancel()

    assert events[-4:-1] == ["1ASE0.started", "1ASE0.complete", "cancelled"]


class TestInhecoThermoShakeShakingScope(AnyioTestBase):
  async def test_scope_configures_starts_yields_and_stops_with_confirmed_state(self) -> None:
    events: List[str] = []
    control_box = FakeControlBox(events)
    thermoshake = make_thermoshake(control_box)

    async with control_box:
      async with thermoshake.shaking(speed=500, shape=2):
        events.append("body")
        assert thermoshake._shaking_active
        assert not thermoshake._shaking_state_unknown
      assert not thermoshake._shaking_active
      assert not thermoshake._shaking_state_unknown

    assert events == [
      "control_box.enter",
      "1SSR500.started",
      "1SSR500.complete",
      "1SSS2.started",
      "1SSS2.complete",
      "1ASE1.started",
      "1ASE1.complete",
      "body",
      "1ASE0.started",
      "1ASE0.complete",
      "control_box.exit",
    ]

  async def test_scope_requires_active_borrowed_control_box_without_owning_it(self) -> None:
    events: List[str] = []
    control_box = FakeControlBox(events)
    thermoshake = make_thermoshake(control_box)

    with pytest.raises(RuntimeError, match="active InhecoTECControlBox lifespan"):
      async with thermoshake.shaking(500):
        pytest.fail("inactive parent reached body")

    assert events == []
    assert not isinstance(thermoshake, HasLifespan)

    async with control_box:
      async with thermoshake.shaking(500):
        pass

    assert events.count("control_box.enter") == 1
    assert events.count("control_box.exit") == 1

  async def test_scope_body_failure_still_stops(self) -> None:
    events: List[str] = []
    control_box = FakeControlBox(events)
    thermoshake = make_thermoshake(control_box)

    async with control_box:
      with pytest.raises(ValueError, match="body failed"):
        async with thermoshake.shaking(500):
          raise ValueError("body failed")

    assert events.index("1ASE0.complete") < events.index("control_box.exit")

  async def test_cleanup_failure_replaces_body_failure_and_allows_direct_retry(self) -> None:
    events: List[str] = []
    body_failure = ValueError("body failed")
    cleanup_failure = RuntimeError("stop failed")
    control_box = FakeControlBox(events, effects={"1ASE0": [cleanup_failure]})
    thermoshake = make_thermoshake(control_box)

    async with control_box:
      with pytest.raises(RuntimeError, match="stop failed") as error_info:
        async with thermoshake.shaking(500):
          raise body_failure
      assert error_info.value is cleanup_failure
      assert error_info.value.__context__ is body_failure
      assert thermoshake._shaking_active
      assert thermoshake._shaking_state_unknown
      await thermoshake.stop_shaking()

    assert not thermoshake._shaking_active
    assert not thermoshake._shaking_state_unknown

  async def test_cancellation_after_ase1_reconciles_stop_before_propagating(self) -> None:
    events: List[str] = []
    ase1_started = anyio.Event()
    release_ase1 = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def hold_ase1() -> None:
      ase1_started.set()
      await release_ase1.wait()

    control_box = FakeControlBox(events, effects={"1ASE1": [hold_ase1]})
    thermoshake = make_thermoshake(control_box)

    async def run_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with thermoshake.shaking(500):
            pytest.fail("cancelled entry reached body")
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with control_box:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_scope)
        await ase1_started.wait()
        scopes[0].cancel()
        await anyio.sleep(0)
        assert "cancelled" not in events
        release_ase1.set()

    assert events.index("1ASE0.complete") < events.index("cancelled")
    assert not thermoshake._shaking_active

  async def test_scope_rejects_nested_and_all_direct_mutations_before_io(self) -> None:
    events: List[str] = []
    control_box = FakeControlBox(events)
    thermoshake = make_thermoshake(control_box)

    async with control_box:
      async with thermoshake.shaking(500):
        events_before = list(events)
        with pytest.raises(RuntimeError, match="already scoped"):
          async with thermoshake.shaking(600):
            pytest.fail("nested scope reached body")
        with pytest.raises(RuntimeError, match="scope"):
          await thermoshake.start_shaking(600)
        with pytest.raises(RuntimeError, match="scope"):
          await thermoshake.stop_shaking()
        with pytest.raises(RuntimeError, match="scope"):
          await thermoshake.set_shaker_speed(600)
        with pytest.raises(RuntimeError, match="scope"):
          await thermoshake.set_shaker_shape(3)
        assert events == events_before

  async def test_scope_rejects_preexisting_durable_shaking_without_adopting_it(self) -> None:
    events: List[str] = []
    control_box = FakeControlBox(events)
    thermoshake = make_thermoshake(control_box)

    async with control_box:
      await thermoshake.start_shaking(500)
      events_before = list(events)
      with pytest.raises(RuntimeError, match="already active"):
        async with thermoshake.shaking(600):
          pytest.fail("scope adopted durable state")
      assert events == events_before
      await thermoshake.stop_shaking()

  async def test_durable_start_and_sequential_scopes_remain_supported(self) -> None:
    events: List[str] = []
    control_box = FakeControlBox(events)
    thermoshake = make_thermoshake(control_box)

    async with control_box:
      await thermoshake.start_shaking(500, shape=1)
      assert thermoshake._shaking_active
      await thermoshake.stop_shaking()
      async with thermoshake.shaking(600):
        pass
      async with thermoshake.shaking(700, shape=3):
        pass

    assert events.count("1ASE1.started") == 3
    assert events.count("1ASE0.started") == 3

  async def test_timed_shake_uses_scope_and_preserves_duration(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    events: List[str] = []
    sleeps: List[float] = []
    control_box = FakeControlBox(events)
    thermoshake = make_thermoshake(control_box)

    async def record_sleep(delay: float) -> None:
      sleeps.append(delay)
      await anyio.lowlevel.checkpoint()

    monkeypatch.setattr(anyio, "sleep", record_sleep)
    async with control_box:
      await thermoshake.shake(speed=500, duration=12.5)

    assert sleeps == [12.5]
    assert events.count("1ASE1.started") == 1
    assert events.count("1ASE0.started") == 1
