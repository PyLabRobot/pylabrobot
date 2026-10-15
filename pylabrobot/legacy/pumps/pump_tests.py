import inspect
import sys
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, call

import anyio
import pytest

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.legacy.pumps import PumpArray
from pylabrobot.legacy.pumps.backend import PumpArrayBackend, PumpBackend
from pylabrobot.legacy.pumps.calibration import PumpCalibration
from pylabrobot.legacy.pumps.chatterbox import (
  PumpArrayChatterboxBackend,
  PumpChatterboxBackend,
)
from pylabrobot.legacy.pumps.errors import NotCalibratedError
from pylabrobot.legacy.pumps.pump import Pump
from pylabrobot.legacy.pumps import pump as pump_module
from pylabrobot.legacy.pumps import pumparray as pumparray_module
from pylabrobot.testing.concurrency import AnyioTestBase


def _group_contains(group: BaseExceptionGroup, target: BaseException) -> bool:
  """Return whether a nested task-group failure contains the original exception."""
  return any(
    error is target or (isinstance(error, BaseExceptionGroup) and _group_contains(error, target))
    for error in group.exceptions
  )


def _context_contains(error: BaseException, target: BaseException) -> bool:
  """Check whether cleanup retains its original failure in the exception chain."""
  seen: set[int] = set()
  current: BaseException | None = error
  while current is not None and id(current) not in seen:
    if current is target:
      return True
    seen.add(id(current))
    current = current.__context__
  return False


def test_pump_backend_requires_pump_operations() -> None:
  assert inspect.isabstract(PumpBackend)
  assert {
    "run_revolutions",
    "run_continuously",
    "halt",
  }.issubset(PumpBackend.__abstractmethods__)


def test_pump_abcs_do_not_define_lifecycle_methods() -> None:
  assert PumpBackend.setup is HasLifespan.setup
  assert PumpBackend.stop is HasLifespan.stop
  assert PumpArrayBackend.setup is HasLifespan.setup
  assert PumpArrayBackend.stop is HasLifespan.stop


class TestPumpChatterboxes(AnyioTestBase):
  def test_chatterboxes_use_managed_lifecycle_methods(self) -> None:
    for cls in (PumpChatterboxBackend, PumpArrayChatterboxBackend):
      assert cls.setup is HasLifespan.setup
      assert cls.stop is HasLifespan.stop

  async def test_pump_chatterbox_normal_exit_preserves_output(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = PumpChatterboxBackend()
    async with backend:
      await backend.run_revolutions(1)
      await backend.run_continuously(2)
      await backend.halt()
    assert capsys.readouterr().out.splitlines() == [
      "Setting up the pump.",
      "Running 1 revolutions.",
      "Running continuously at speed 2.",
      "Halting the pump.",
      "Stopping the pump.",
    ]

  async def test_pump_array_chatterbox_cancelled_body_still_stops(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    entered = anyio.Event()
    backend = PumpArrayChatterboxBackend(num_channels=3)

    async def use_backend() -> None:
      async with backend:
        entered.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await entered.wait()
      task_group.cancel_scope.cancel()

    assert capsys.readouterr().out == ("Setting up the pump array.\nStopping the pump array.\n")
    assert not backend.is_active

  async def test_pump_chatterbox_sequential_reuse(self, capsys: pytest.CaptureFixture[str]) -> None:
    backend = PumpChatterboxBackend()
    async with backend:
      pass
    async with backend:
      pass
    assert capsys.readouterr().out == (
      "Setting up the pump.\nStopping the pump.\nSetting up the pump.\nStopping the pump.\n"
    )

  async def test_pump_array_chatterbox_rejects_lifespan_options(
    self, capsys: pytest.CaptureFixture[str]
  ) -> None:
    backend = PumpArrayChatterboxBackend()
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options must not reach the body")
    assert capsys.readouterr().out == ""


@pytest.mark.anyio
async def test_run_for_duration_stops_pump_after_duration(anyio_backend: Any) -> None:
  backend = Mock(spec=PumpBackend)
  pump = Pump(backend=backend)

  await pump.run_for_duration(speed=2, duration=0)

  assert backend.run_continuously.await_args_list == [
    call(speed=2),
    call(speed=0),
  ]


@pytest.mark.anyio
async def test_run_for_duration_cancellation_waits_for_pump_stop(anyio_backend: Any) -> None:
  backend = Mock(spec=PumpBackend)
  pump = Pump(backend=backend)
  running = anyio.Event()
  stop_started = anyio.Event()
  release_stop = anyio.Event()
  stop_finished = anyio.Event()
  finished = anyio.Event()
  scopes: list[anyio.CancelScope] = []
  observed_cancellation: list[BaseException] = []

  async def run_continuously(speed: float) -> None:
    if speed != 0:
      running.set()
      return
    stop_started.set()
    await release_stop.wait()
    stop_finished.set()

  backend.run_continuously.side_effect = run_continuously

  async def run_pump() -> None:
    with anyio.CancelScope() as scope:
      scopes.append(scope)
      try:
        await pump.run_for_duration(speed=2, duration=60)
      except anyio.get_cancelled_exc_class() as error:
        observed_cancellation.append(error)
        raise
    finished.set()

  async with anyio.create_task_group() as task_group:
    task_group.start_soon(run_pump)
    await running.wait()
    scopes[0].cancel()
    with anyio.fail_after(1):
      await stop_started.wait()
    assert not finished.is_set()
    release_stop.set()
    await finished.wait()

  assert stop_finished.is_set()
  assert len(observed_cancellation) == 1


@pytest.mark.anyio
async def test_timed_pump_start_failure_after_dispatch_stops_before_raising(
  anyio_backend: Any,
) -> None:
  backend = Mock(spec=PumpBackend)
  pump = Pump(backend=backend)
  failure = RuntimeError("start failed after dispatch")
  events: list[str] = []

  async def run(speed: float) -> None:
    events.append(f"speed:{speed}")
    if speed != 0:
      raise failure

  backend.run_continuously.side_effect = run
  with pytest.raises(RuntimeError) as error_info:
    await pump.run_for_duration(2, 60)
  assert error_info.value is failure
  assert events == ["speed:2", "speed:0"]


@pytest.mark.anyio
async def test_timed_pump_cancelled_during_start_stops_before_cancellation(
  anyio_backend: Any,
) -> None:
  backend = Mock(spec=PumpBackend)
  pump = Pump(backend=backend)
  started = anyio.Event()
  release = anyio.Event()
  events: list[str] = []
  scopes: list[anyio.CancelScope] = []

  async def run(speed: float) -> None:
    events.append(f"speed:{speed}")
    if speed != 0:
      started.set()
      await release.wait()

  backend.run_continuously.side_effect = run

  async def operate() -> None:
    with anyio.CancelScope() as scope:
      scopes.append(scope)
      try:
        await pump.run_for_duration(2, 60)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

  async with anyio.create_task_group() as group:
    group.start_soon(operate)
    await started.wait()
    scopes[0].cancel()
    release.set()

  assert events == ["speed:2", "speed:0", "cancelled"]


@pytest.mark.anyio
async def test_timed_pump_cancelled_before_start_does_not_issue_stop(anyio_backend: Any) -> None:
  backend = Mock(spec=PumpBackend)
  pump = Pump(backend=backend)
  with anyio.CancelScope() as scope:
    scope.cancel()
    await pump.run_for_duration(2, 60)
  backend.run_continuously.assert_not_awaited()


@pytest.mark.anyio
async def test_timed_pump_stalled_stop_times_out_and_retains_start_failure(
  anyio_backend: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setattr(pump_module, "_PUMP_STOP_TIMEOUT", 0.02, raising=False)
  backend = Mock(spec=PumpBackend)
  pump = Pump(backend=backend)
  failure = RuntimeError("start may have committed")
  speeds: list[float] = []

  async def run(speed: float) -> None:
    speeds.append(speed)
    if speed:
      raise failure
    await anyio.sleep(0.1)

  backend.run_continuously.side_effect = run
  with anyio.fail_after(1):
    with pytest.raises(TimeoutError) as error_info:
      await pump.run_for_duration(2, 60)
  assert _context_contains(error_info.value, failure)
  assert speeds == [2, 0]


class TestPump(unittest.IsolatedAsyncioTestCase):
  """Tests for the Pump class.

  Currently, only the Cole Palmer Masterflex pump is implemented.
  """

  def setUp(self):
    self.mock_backend = Mock(spec=PumpBackend)
    lifespan = MagicMock()
    lifespan.__aenter__ = AsyncMock(return_value=self.mock_backend)
    lifespan.__aexit__ = AsyncMock(return_value=None)
    self.mock_backend.lifespan.return_value = lifespan
    self.test_calibration = PumpCalibration.load_calibration(1, num_items=1)

  async def test_setup(self):
    """Test that the Pump class can be initialized."""
    pump = Pump(backend=self.mock_backend)
    async with pump:
      self.assertIsNone(pump.calibration)
      self.assertEqual(pump.backend, self.mock_backend)

  async def test_operations_await_backend(self):
    """Test that Pump waits for each backend operation."""
    pump = Pump(backend=self.mock_backend)
    async with pump:
      await pump.run_revolutions(num_revolutions=1)
      await pump.run_continuously(speed=2)
      await pump.halt()

    self.mock_backend.run_revolutions.assert_awaited_once_with(num_revolutions=1)
    self.mock_backend.run_continuously.assert_awaited_once_with(speed=2)
    self.mock_backend.halt.assert_awaited_once_with()


class RecordingPumpArrayBackend(PumpArrayBackend):
  def __init__(self, num_channels: int = 6) -> None:
    super().__init__()
    self._num_channels = num_channels
    self.calls: list[tuple[str, object, object]] = []

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, **backend_kwargs: Any
  ) -> None:
    if backend_kwargs:
      raise TypeError("RecordingPumpArrayBackend does not accept lifespan options")

  @property
  def num_channels(self) -> int:
    return self._num_channels

  async def run_revolutions(self, num_revolutions: list[float], use_channels: list[int]) -> None:
    self.calls.append(("revolutions", num_revolutions, use_channels))

  async def run_continuously(self, speed: list[float], use_channels: list[int]) -> None:
    self.calls.append(("continuous", speed, use_channels))

  async def halt(self) -> None:
    self.calls.append(("halt", None, None))


class TestPumpArray(AnyioTestBase):
  """Tests for the AgrowPumpArrayTester class."""

  async def test_setup(self):
    """Test that the AgrowPumpArrayTester class can be initialized."""
    backend = RecordingPumpArrayBackend()
    pump_array = PumpArray(backend=backend)
    async with pump_array:
      assert pump_array.num_channels == 6
      assert pump_array.calibration is None

  async def test_run_continuously(self):
    backend = RecordingPumpArrayBackend()
    pump_array = PumpArray(backend=backend)

    # valid
    await pump_array.run_continuously(speed=1, use_channels=[0])
    assert backend.calls == [("continuous", [1.0], [0])]

    # invalid: speed incorrect length for use_channels
    with pytest.raises(ValueError):
      await pump_array.run_continuously(speed=[1, 1], use_channels=[0])

    # invalid speed: cannot be negative
    with pytest.raises(ValueError):
      await pump_array.run_continuously(speed=[-1], use_channels=[0])

  async def test_invalid_channels(self):
    pump_array = PumpArray(backend=RecordingPumpArrayBackend())

    # invalid: max index is n-1=5
    with pytest.raises(ValueError):
      await pump_array.run_continuously(speed=[1] * 6, use_channels=[2, 3, 4, 5, 6, 7])

    # invalid: channels must be unique
    with pytest.raises(ValueError):
      await pump_array.run_continuously(speed=[1] * 6, use_channels=[1, 1, 1, 1, 1, 1])

    # invalid: too many channels
    with pytest.raises(ValueError):
      await pump_array.run_continuously(speed=[1] * 7, use_channels=[1, 2, 3, 4, 5, 6, 7])

  async def test_halt(self):
    backend = RecordingPumpArrayBackend()
    pump_array = PumpArray(backend=backend)
    await pump_array.halt()
    assert backend.calls == [("halt", None, None)]

  async def test_run_for_duration(self):
    backend = RecordingPumpArrayBackend()
    pump_array = PumpArray(backend=backend)

    # can use an int or float
    await pump_array.run_for_duration(speed=1, use_channels=[0], duration=0)
    assert backend.calls == [
      ("continuous", [1.0], [0]),
      ("continuous", [0.0], [0]),
    ]
    backend.calls.clear()
    await pump_array.run_for_duration(speed=1, use_channels=[0], duration=0.0)
    assert backend.calls == [
      ("continuous", [1.0], [0]),
      ("continuous", [0.0], [0]),
    ]

  async def test_timed_array_start_failure_after_dispatch_zeros_selected_channels(self) -> None:
    backend = RecordingPumpArrayBackend(3)
    pump_array = PumpArray(backend)
    failure = RuntimeError("start may have committed")

    async def run(speed: list[float], use_channels: list[int]) -> None:
      backend.calls.append(("continuous", speed, use_channels))
      if speed != [0.0, 0.0]:
        raise failure

    backend.run_continuously = run  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as error_info:
      await pump_array.run_for_duration([1, 2], [0, 2], 60)
    assert error_info.value is failure
    assert backend.calls == [
      ("continuous", [1.0, 2.0], [0, 2]),
      ("continuous", [0.0, 0.0], [0, 2]),
    ]

  async def test_timed_array_cancelled_during_start_zeros_before_cancellation(self) -> None:
    backend = RecordingPumpArrayBackend(3)
    pump_array = PumpArray(backend)
    started = anyio.Event()
    release = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    events: list[str] = []

    async def run(speed: list[float], use_channels: list[int]) -> None:
      events.append(f"{speed}:{use_channels}")
      if speed != [0.0, 0.0]:
        started.set()
        await release.wait()

    backend.run_continuously = run  # type: ignore[method-assign]

    async def operate() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await pump_array.run_for_duration([1, 2], [0, 2], 60)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with anyio.create_task_group() as group:
      group.start_soon(operate)
      await started.wait()
      scopes[0].cancel()
      release.set()

    assert events == ["[1.0, 2.0]:[0, 2]", "[0.0, 0.0]:[0, 2]", "cancelled"]

  async def test_timed_array_cancelled_before_start_does_not_zero(self) -> None:
    backend = RecordingPumpArrayBackend(3)
    pump_array = PumpArray(backend)
    with anyio.CancelScope() as scope:
      scope.cancel()
      await pump_array.run_for_duration(2, [1], 60)
    assert backend.calls == []

  @pytest.mark.parametrize(
    ("speed", "use_channels", "message"),
    [(-1, [1], "Speed must be positive"), (1, [3], "out of range")],
  )
  async def test_invalid_timed_array_input_does_not_attempt_zero(
    self, speed: int, use_channels: list[int], message: str
  ) -> None:
    backend = RecordingPumpArrayBackend(3)
    pump_array = PumpArray(backend)
    run = pump_array.run_continuously
    attempts: list[tuple[object, object]] = []

    async def record_attempt(speed: object, use_channels: object) -> None:
      attempts.append((speed, use_channels))
      await run(speed=speed, use_channels=use_channels)  # type: ignore[arg-type]

    pump_array.run_continuously = record_attempt  # type: ignore[method-assign]
    with pytest.raises(ValueError, match=message):
      await pump_array.run_for_duration(speed, use_channels, 60)
    assert all(attempt_speed != 0 for attempt_speed, _ in attempts)
    assert backend.calls == []

  async def test_timed_array_stalled_zero_times_out_and_retains_start_error(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(pumparray_module, "_PUMP_STOP_TIMEOUT", 0.02, raising=False)
    backend = RecordingPumpArrayBackend(2)
    pump_array = PumpArray(backend)
    failure = RuntimeError("channel start may have committed")
    speeds: list[list[float]] = []

    async def run(speed: list[float], use_channels: list[int]) -> None:
      speeds.append(speed)
      if speed != [0.0]:
        raise failure
      await anyio.sleep(0.1)

    backend.run_continuously = run  # type: ignore[method-assign]
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError) as error_info:
        await pump_array.run_for_duration(2, [1], 60)
    assert _context_contains(error_info.value, failure)
    assert speeds == [[2.0], [0.0]]

  async def test_run_invalid_duration(self):
    pump_array = PumpArray(backend=RecordingPumpArrayBackend())

    # cannot use negative int or float
    with pytest.raises(ValueError):
      await pump_array.run_for_duration(speed=1, use_channels=[0], duration=-1)
    with pytest.raises(ValueError):
      await pump_array.run_for_duration(speed=1, use_channels=[0], duration=-1.0)

  async def test_volume_pump_duration(self):
    backend = RecordingPumpArrayBackend()
    calibration = PumpCalibration.load_calibration(1, num_items=6)
    calibration.calibration_mode = "duration"
    pump_array = PumpArray(backend=backend, calibration=calibration)
    calls: list[tuple[object, object, object]] = []

    # valid: can use an int or float
    async def run_for_duration(speed: object, use_channels: object, duration: object) -> None:
      calls.append((speed, use_channels, duration))

    pump_array.run_for_duration = run_for_duration  # type: ignore[method-assign]
    await pump_array.pump_volume(speed=1, use_channels=[0], volume=1)
    assert calls == [(1, 0, 1.0)]

  async def test_volume_pump_revolutions(self):
    backend = RecordingPumpArrayBackend()
    calibration = PumpCalibration.load_calibration(1, num_items=6)
    calibration.calibration_mode = "revolutions"
    pump_array = PumpArray(backend=backend, calibration=calibration)
    calls: list[tuple[object, object]] = []

    # valid: can use an int or float
    async def run_revolutions(num_revolutions: object, use_channels: object) -> None:
      calls.append((num_revolutions, use_channels))

    pump_array.run_revolutions = run_revolutions  # type: ignore[assignment]
    await pump_array.pump_volume(speed=1, use_channels=[0], volume=1)
    assert calls == [(1.0, 0)]

  async def test_calibration_missing(self):
    pump_array = PumpArray(backend=RecordingPumpArrayBackend())

    # invalid: no calibration
    with pytest.raises(NotCalibratedError):
      await pump_array.pump_volume(speed=1, use_channels=[0], volume=1)

  async def test_invalid_volume(self):
    calibration = PumpCalibration.load_calibration(1, num_items=6)
    pump_array = PumpArray(backend=RecordingPumpArrayBackend(), calibration=calibration)

    # invalid: volume cannot be negative
    with pytest.raises(ValueError):
      await pump_array.pump_volume(speed=1, use_channels=[0], volume=-1)

  async def test_run_for_duration_cancellation_waits_for_channel_stop(self) -> None:
    backend = RecordingPumpArrayBackend()
    pump_array = PumpArray(backend)
    started = anyio.Event()
    stop_started = anyio.Event()
    release_stop = anyio.Event()
    events: list[str] = []

    async def run(speed: list[float], use_channels: list[int]) -> None:
      if speed != [0.0]:
        events.append("run")
        started.set()
        return
      events.append("stop.started")
      stop_started.set()
      await release_stop.wait()
      events.append("stop.finished")

    backend.run_continuously = run  # type: ignore[method-assign]

    async def operate() -> None:
      try:
        await pump_array.run_for_duration(5, [2], 60)
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(operate)
      await started.wait()
      task_group.cancel_scope.cancel()
      with anyio.CancelScope(shield=True):
        with anyio.fail_after(1):
          await stop_started.wait()
        assert events == ["run", "stop.started"]
        release_stop.set()

    assert events == ["run", "stop.started", "stop.finished", "cancelled"]

  async def test_duration_volume_tasks_are_children_of_the_calling_task(self) -> None:
    calibration = PumpCalibration.load_calibration(1, num_items=2)
    calibration.calibration_mode = "duration"
    pump_array = PumpArray(RecordingPumpArrayBackend(2), calibration=calibration)
    parent_ids: list[int | None] = []

    async def record_task(speed: object, use_channels: object, duration: object) -> None:
      del speed, use_channels, duration
      parent_ids.append(anyio.get_current_task().parent_id)

    pump_array.run_for_duration = record_task  # type: ignore[method-assign]
    caller_id = anyio.get_current_task().id
    await pump_array.pump_volume(speed=[1, 2], use_channels=[0, 1], volume=[3, 4])

    assert parent_ids == [caller_id, caller_id]

  async def test_revolution_volume_waits_for_every_owned_channel_task(self) -> None:
    calibration = PumpCalibration.load_calibration(1, num_items=2)
    calibration.calibration_mode = "revolutions"
    pump_array = PumpArray(RecordingPumpArrayBackend(2), calibration=calibration)
    started = [anyio.Event(), anyio.Event()]
    release = [anyio.Event(), anyio.Event()]
    returned = anyio.Event()

    async def run_revolutions(num_revolutions: object, use_channels: int) -> None:
      del num_revolutions
      started[use_channels].set()
      await release[use_channels].wait()

    pump_array.run_revolutions = run_revolutions  # type: ignore[assignment]

    async def operate() -> None:
      await pump_array.pump_volume(speed=[1, 2], use_channels=[0, 1], volume=[3, 4])
      returned.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(operate)
      await started[0].wait()
      await started[1].wait()
      assert not returned.is_set()
      release[0].set()
      release[1].set()

    assert returned.is_set()

  async def test_duration_volume_sibling_failure_cancels_and_stops_other_channel(self) -> None:
    calibration = PumpCalibration.load_calibration(1, num_items=2)
    calibration.calibration_mode = "duration"
    backend = RecordingPumpArrayBackend(2)
    pump_array = PumpArray(backend, calibration=calibration)
    other_channel_started = anyio.Event()
    failure = RuntimeError("channel failed")
    events: list[str] = []

    async def run(speed: list[float], use_channels: list[int]) -> None:
      channel = use_channels[0]
      if speed == [0.0]:
        if channel == 1:
          events.append("stop.finished")
        return
      if channel == 1:
        other_channel_started.set()
        return
      await other_channel_started.wait()
      raise failure

    backend.run_continuously = run  # type: ignore[method-assign]

    with pytest.raises(BaseExceptionGroup) as error_info:
      await pump_array.pump_volume(speed=[1, 1], use_channels=[0, 1], volume=[60, 60])

    assert _group_contains(error_info.value, failure)
    assert events == ["stop.finished"]


if __name__ == "__main__":
  unittest.main()
