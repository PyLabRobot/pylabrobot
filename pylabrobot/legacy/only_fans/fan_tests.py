from abc import abstractmethod
from typing import Any, List, Tuple
from unittest.mock import patch

import anyio
import pytest
from trio.testing import MockClock

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.only_fans import Fan, FanBackend
from pylabrobot.legacy.only_fans import fan as fan_module
from pylabrobot.testing import AnyioTestBase


class RecordingFanBackendBase(FanBackend):
  """Resource-free fan backend double recording each fan-domain operation."""

  def __init__(self) -> None:
    super().__init__()
    self.events: List[str] = []
    self.started = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("fan backend double does not accept lifespan options")

  async def turn_on(self, intensity: int) -> None:
    self.events.append(f"on:{intensity}")
    self.started.set()

  @abstractmethod
  async def turn_off(self) -> None:
    """Record a shutdown that contains a cancellation checkpoint."""


class RecordingFanBackend(RecordingFanBackendBase):
  """Fan backend double whose shutdown checkpoints between its two phases.

  An unshielded timed shutdown is therefore cancelled at the checkpoint and never
  records ``off.completed``.
  """

  async def turn_off(self) -> None:
    self.events.append("off.started")
    await anyio.sleep(0)
    self.events.append("off.completed")


class FailingTurnOffFanBackend(RecordingFanBackendBase):
  """Fan backend double whose checkpointed shutdown always fails."""

  async def turn_off(self) -> None:
    self.events.append("off.started")
    await anyio.sleep(0)
    raise RuntimeError("fan turn-off failed")


class HangingTurnOffFanBackend(RecordingFanBackendBase):
  """Fan backend double whose shutdown never completes."""

  async def turn_off(self) -> None:
    self.events.append("off.started")
    await anyio.sleep_forever()


async def _abandoned_cleanup(duration: float) -> Tuple[float, str]:
  """Cancel a timed run whose turn-off hangs; report when and how the wait was abandoned.

  Args:
    duration: Run duration requested of :meth:`Fan.turn_on`.

  Returns:
    The seconds from cancellation to abandonment, and the resulting error message.
  """
  backend = HangingTurnOffFanBackend()
  fan = Fan(backend=backend)
  errors: List[BaseException] = []
  scopes: List[anyio.CancelScope] = []
  finished = anyio.Event()

  async def run_timed_fan() -> None:
    try:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await fan.turn_on(50, duration)
    except TimeoutError as error:
      errors.append(error)
    finally:
      finished.set()

  async with anyio.create_task_group() as task_group:
    task_group.start_soon(run_timed_fan)
    await backend.started.wait()
    scopes[0].cancel()
    start = anyio.current_time()
    await finished.wait()
  elapsed = anyio.current_time() - start

  assert len(errors) == 1
  assert backend.events == ["on:50", "off.started"]
  return elapsed, str(errors[0])


class TestTimedFan(AnyioTestBase):
  async def test_timed_turn_on_cancellation_completes_turn_off(self) -> None:
    backend = RecordingFanBackend()
    fan = Fan(backend=backend)

    with anyio.fail_after(1):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(fan.turn_on, 50, 60)
        await backend.started.wait()
        task_group.cancel_scope.cancel()

    assert backend.events == ["on:50", "off.started", "off.completed"]

  async def test_timed_turn_on_failure_preserves_cleanup_error(self) -> None:
    backend = FailingTurnOffFanBackend()
    fan = Fan(backend=backend)
    errors: List[BaseException] = []
    scopes: List[anyio.CancelScope] = []
    finished = anyio.Event()

    async def run_timed_fan() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await fan.turn_on(50, 60)
      except RuntimeError as error:
        errors.append(error)
      finally:
        finished.set()

    with anyio.fail_after(1):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_timed_fan)
        await backend.started.wait()
        scopes[0].cancel()
        await finished.wait()

    assert len(errors) == 1
    assert str(errors[0]) == "fan turn-off failed"
    assert isinstance(errors[0].__context__, anyio.get_cancelled_exc_class())
    assert backend.events == ["on:50", "off.started"]

  async def test_timed_turn_on_shutdown_overrun_names_unconfirmed_fan(self) -> None:
    # This test reads the overrun error's text, not the bound's value, so it shrinks the
    # bound rather than observing the real one and keeps both backends on a real clock. The
    # requested duration is small as well, so no bound this test might be run against can
    # cost meaningful real time.
    with patch.object(fan_module, "_FAN_TURN_OFF_TIMEOUT", 0.05):
      _, message = await _abandoned_cleanup(0.01)

    assert "fan may still be running" in message


class TestTimedFanCleanupBound(AnyioTestBase):
  """Observe the timed shutdown's bound itself, on a clock that costs no real time.

  A shutdown that never completes is abandoned exactly when its bound expires, so the time
  elapsed between cancellation and abandonment *is* the bound the frontend applied. The
  bound is a frontend cancel-scope deadline, which resolves its clock through the running
  backend, so only a backend-native virtual clock can advance it without real waiting. Trio
  supplies one; asyncio has no packaged equivalent, so this one test runs trio-only. The
  bound under test is backend-independent, and every other behavior of the cleanup path
  keeps asyncio and Trio coverage in :class:`TestTimedFan`.
  """

  # One test and one backend means one node, so this class-level clock is per-test by
  # construction and has no other node to leak virtual time or counters into. The single
  # assertion measures intervals rather than absolute instants, so even a repeated node
  # would read the same values. Adding a test, a backend, or an absolute-time assertion to
  # this class would require a per-node clock instead.
  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_timed_turn_on_cleanup_bound_does_not_scale_with_duration(self) -> None:
    short_elapsed, _ = await _abandoned_cleanup(60)
    long_elapsed, _ = await _abandoned_cleanup(3600)

    # The cleanup bound is the fixed constant, so a one-hour run must not buy its shutdown
    # a one-hour wait: both runs abandon the hung turn-off at the same moment.
    assert short_elapsed == pytest.approx(fan_module._FAN_TURN_OFF_TIMEOUT, abs=0.5)
    assert long_elapsed == pytest.approx(fan_module._FAN_TURN_OFF_TIMEOUT, abs=0.5)
    assert long_elapsed == pytest.approx(short_elapsed, abs=0.5)
