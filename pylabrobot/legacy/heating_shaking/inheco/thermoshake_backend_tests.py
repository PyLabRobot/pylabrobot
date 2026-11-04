from typing import List, Tuple
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.legacy.heating_shaking.heater_shaker import HeaterShaker
from pylabrobot.legacy.heating_shaking.inheco import thermoshake_backend as thermoshake_module
from pylabrobot.legacy.heating_shaking.inheco.thermoshake_backend import InhecoThermoshakeBackend
from pylabrobot.legacy.temperature_controlling.inheco import control_box as control_box_module
from pylabrobot.legacy.temperature_controlling.inheco import (
  temperature_controller as temperature_controller_module,
)
from pylabrobot.legacy.temperature_controlling.inheco.control_box import InhecoTECControlBox
from pylabrobot.legacy.temperature_controlling.inheco.control_box_tests import (
  RecordingHID,
  make_control_box,
  settle,
)
from pylabrobot.legacy.temperature_controlling.inheco.cpac_backend import InhecoCPACBackend
from pylabrobot.legacy.temperature_controlling.temperature_controller import TemperatureController
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase

# Deadlock guard for the event-driven cancellation tests. Those finish in milliseconds, so this
# is orders of magnitude larger than any correct run, but finite: a cleanup that never
# dispatches fails the test rather than hanging the run.
_REAL_TIME_DEADLOCK_GUARD = 60.0

# The cleanup bound the overrun tests shrink to. They read the overrun's consequence, not the
# bound's value, so they shrink the constant rather than wait out the real one and keep both
# backends on a real clock.
_SHRUNK_CLEANUP_TIMEOUT = 0.05

# How long a held exchange stays held after the observation that needed it is over. A correct run
# never waits for it: an event releases the exchange as soon as the overrun has been observed.
# This is a determinism and speed aid, not a hang preventer. What ends a run whose cleanup has no
# bound at all is the holding sibling's own `move_on_after(timeout)` inside the shield (see
# `send_command`): that deadline expires by itself and releases the transaction, so the unbounded
# cleanup completes either way. Measured with `shield_timeout=` deleted from both registration
# sites, on both backends: the two overrun tests fail in about 4 s with this fallback and in about
# 15 s with it raised to 600.0, and neither run hangs. Keep it below `_COMMAND_TIMEOUT` (3, in
# `control_box.py`): at or above that the sibling's deadline wins first, this fallback becomes
# dead code, and the tests stay green for a different reason than the one described here.
_UNBOUNDED_CLEANUP_RELEASE = 2.0

# Device indexes used throughout, chosen so the two children never share a command prefix.
_CPAC_INDEX = 1
_THERMOSHAKE_INDEX = 3


def _cpac(control_box: InhecoTECControlBox) -> InhecoCPACBackend:
  """Build a CPAC backend borrowing the supplied control box."""
  return InhecoCPACBackend(index=_CPAC_INDEX, control_box=control_box)


def _thermoshake(control_box: InhecoTECControlBox) -> InhecoThermoshakeBackend:
  """Build a ThermoShake backend borrowing the supplied control box."""
  return InhecoThermoshakeBackend(index=_THERMOSHAKE_INDEX, control_box=control_box)


def _temperature_controller(backend: InhecoCPACBackend) -> TemperatureController:
  """Wrap a CPAC backend in the frontend that owns its shutdown actions."""
  return TemperatureController(
    name="test_inheco_cpac",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate.zero(),
  )


def _heater_shaker(backend: InhecoThermoshakeBackend) -> HeaterShaker:
  """Wrap a ThermoShake backend in the frontend that owns its shutdown actions."""
  return HeaterShaker(
    name="test_inheco_thermoshake",
    size_x=10,
    size_y=10,
    size_z=10,
    backend=backend,
    child_location=Coordinate.zero(),
  )


def _transport_state(io: RecordingHID) -> Tuple[int, int]:
  """Return how often the borrowed transport has been opened and closed."""
  return io.entries, io.exits


async def _release_held_exchange_when_observed(io: RecordingHID, observed: anyio.Event) -> None:
  """Hand the shared transaction back once the overrun has been observed.

  The wait is event-driven, so a correct run releases the exchange the moment the observation
  finishes. The fallback bound only shortens and de-randomizes the case the tests are aimed at
  catching; see `_UNBOUNDED_CLEANUP_RELEASE` for what actually ends a cleanup that has no bound
  of its own.
  """
  with anyio.move_on_after(_UNBOUNDED_CLEANUP_RELEASE):
    await observed.wait()
  io.release_blocked.set()


class TestInhecoBorrowedChildrenLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    for backend_class in (InhecoCPACBackend, InhecoThermoshakeBackend):
      assert backend_class.setup is HasLifespan.setup
      assert backend_class.stop is HasLifespan.stop
      assert backend_class.__aenter__ is HasLifespan.__aenter__
      assert backend_class.__aexit__ is HasLifespan.__aexit__

  async def test_cpac_requires_active_control_box(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch)
    backend = _cpac(control_box)

    with pytest.raises(
      RuntimeError, match="Inheco temperature backends require an active control box"
    ):
      async with backend:
        pytest.fail("entry without an active control box reached the body")

    # The rejection happens before any device work, so nothing reached the transport.
    assert io.events == []
    assert not backend.is_active

  async def test_thermoshake_requires_active_control_box(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)
    backend = _thermoshake(control_box)

    with pytest.raises(
      RuntimeError, match="Inheco temperature backends require an active control box"
    ):
      async with backend:
        pytest.fail("entry without an active control box reached the body")

    assert io.events == []
    assert not backend.is_active

  async def test_borrowed_children_reject_lifespan_options(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      baseline = len(io.events)
      for backend in (_cpac(control_box), _thermoshake(control_box)):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
          async with backend.lifespan(unknown=True):
            pytest.fail("invalid entry reached the body")

      # Rejected before any device work, and the borrowed box is untouched.
      assert io.events[baseline:] == []
      assert control_box.is_active

  async def test_cpac_does_not_close_control_box(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      opened = _transport_state(io)
      backend = _cpac(control_box)
      async with backend:
        assert control_box.is_active

      # The child ran its own cleanup but left the borrowed box open for the next child.
      assert io.commands == ["1ATE0"]
      assert _transport_state(io) == opened
      assert control_box.is_active

    assert _transport_state(io) == (1, 1)

  async def test_thermoshake_does_not_close_control_box(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      opened = _transport_state(io)
      backend = _thermoshake(control_box)
      async with backend:
        assert control_box.is_active

      assert io.commands == ["3ASE0", "3ATE0"]
      assert _transport_state(io) == opened
      assert control_box.is_active

    assert _transport_state(io) == (1, 1)

  async def test_thermoshake_exit_order_is_ase0_then_ate0(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      async with _thermoshake(control_box):
        pass

      # The composing lifespan registers the inherited temperature cleanup first, so LIFO
      # stops shaking before it stops temperature control, exactly as the legacy stop did.
      assert io.commands == ["3ASE0", "3ATE0"]

  async def test_cpac_cancellation_completes_ate0(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch)
    body_started = anyio.Event()

    async with control_box:
      backend = _cpac(control_box)

      async def use_backend() -> None:
        try:
          async with backend:
            body_started.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
          io.events.append("cancelled")
          raise

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(use_backend)
          await body_started.wait()
          task_group.cancel_scope.cancel()

      # Cleanup is shielded, so cancellation does not abandon the stop command.
      assert io.commands == ["1ATE0"]
      assert io.events[-1] == "cancelled"
      assert control_box.is_active
      assert io.exits == 0

  async def test_thermoshake_cancellation_completes_ase0_and_ate0(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)
    body_started = anyio.Event()

    async with control_box:
      backend = _thermoshake(control_box)

      async def use_backend() -> None:
        try:
          async with backend:
            body_started.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
          io.events.append("cancelled")
          raise

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(use_backend)
          await body_started.wait()
          task_group.cancel_scope.cancel()

      assert io.commands == ["3ASE0", "3ATE0"]
      assert io.events[-1] == "cancelled"
      assert control_box.is_active
      assert io.exits == 0

  async def test_cancelled_child_cleanup_does_not_starve_a_held_transaction(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A cleanup that must wait for the shared transaction still completes after release.

    Cleanup is shielded and bounded, so a cleanup queued behind another child's in-flight
    command waits for it rather than being abandoned or waiting forever.
    """
    control_box, io = make_control_box(monkeypatch, blocked_event="read")
    entered = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async with control_box:
      backend = _thermoshake(control_box)
      baseline = len(io.events)

      async def hold_transaction() -> None:
        await control_box.send_command("0ADD1")

      async def use_backend() -> None:
        # Only this task is cancelled, so the test body below stays alive to observe the
        # queued cleanup and then release the held exchange.
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with backend:
            entered.set()
            await anyio.sleep_forever()

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(hold_transaction)
          await io.blocked_started.wait()
          task_group.start_soon(use_backend)
          await entered.wait()
          scopes[0].cancel()
          await settle()
          try:
            # The held exchange still owns the transaction, so no cleanup byte is out yet.
            assert io.commands == ["0ADD1"]
          finally:
            # Released even when the assertion above fails, because the held exchange is
            # shielded and would otherwise outlive the guard.
            io.release_blocked.set()

      assert io.commands == ["0ADD1", "3ASE0", "3ATE0"]
      assert io.events[baseline] == "write:0ADD1"
      assert control_box.is_active
      io.blocked_event = None

  async def test_borrowed_children_can_run_sequentially(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      cpac = _cpac(control_box)
      thermoshake = _thermoshake(control_box)

      async with cpac:
        await cpac.set_target_temperature(37)
        await cpac.start_temperature_control()
      async with thermoshake:
        await thermoshake.start_shaking(speed=100)
      async with cpac:
        pass

      assert io.commands == [
        "1STT370",
        "1ATE1",
        "1ATE0",
        "1SSR100",
        "1SSS0",
        "3ASE1",
        "3ASE0",
        "3ATE0",
        "1ATE0",
      ]
      assert _transport_state(io) == (1, 0)
      assert control_box.is_active

    assert _transport_state(io) == (1, 1)


class TestInhecoBorrowedChildrenComposition(AnyioTestBase):
  """Pin the exact transcript each Wave 12 frontend produces around a borrowed child.

  Both transcripts contain a redundant stop, because the frontend and the backend each own
  the same shutdown command. That duplication is pre-existing and is carried through
  deliberately; these tests exist so a future change to either owner is visible rather than
  silent.
  """

  async def test_temperature_controller_exit_transcript(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      device = _temperature_controller(_cpac(control_box))
      baseline = len(io.commands)
      async with device:
        pass

      # `TemperatureController._pre_backend_stop` deactivates, and the backend lifespan then
      # runs its own registered stop.
      assert io.commands[baseline:] == ["1ATE0", "1ATE0"]
      assert control_box.is_active
      assert io.exits == 0

  async def test_heater_shaker_exit_transcript(self, monkeypatch: pytest.MonkeyPatch) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      device = _heater_shaker(_thermoshake(control_box))
      baseline = len(io.commands)
      async with device:
        pass

      # `HeaterShaker._pre_backend_stop` registers the forced shake stop first and the
      # deactivation second, so LIFO deactivates before it stops shaking. The backend
      # lifespan then contributes its own stop pair in the opposite order.
      assert io.commands[baseline:] == ["3ATE0", "3ASE0", "3ASE0", "3ATE0"]
      assert control_box.is_active
      assert io.exits == 0

  async def test_one_shaking_scope_sends_one_ase1_ase0_pair(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    control_box, io = make_control_box(monkeypatch)

    async with control_box:
      device = _heater_shaker(_thermoshake(control_box))
      async with device:
        baseline = len(io.commands)
        async with device.shaking(speed=100):
          pass
        scoped: List[str] = io.commands[baseline:]

    # The Wave 12 scope starts and stops shaking exactly once; the device does not support
    # locking, so the scope adds no clamp commands either.
    assert scoped == ["1SSR100", "1SSS0", "3ASE1", "3ASE0"]
    assert scoped.count("3ASE1") == 1
    assert scoped.count("3ASE0") == 1


class TestInhecoBorrowedChildrenCleanupBound(AnyioTestBase):
  """Pin that each registered cleanup's dispatch is bounded, and by how much.

  A registered cleanup must wait for the control box's shared transaction before it can send
  its stop command, and that wait is unbounded on its own. `shield_timeout` is the only thing
  that makes it finite, and it has to be, because cleanup runs inside a shield: no caller's
  deadline and no cancellation can reach in and end it. Without the bound a child whose
  sibling has stalled the shared box does not fail its exit, it never leaves it.

  `test_cancelled_child_cleanup_does_not_starve_a_held_transaction` above does not cover this.
  It shows a queued cleanup survives a wait and completes afterwards, which is true of a
  cleanup with any bound and equally true of one with none.
  """

  def test_cleanup_bound_is_a_fixed_ten_seconds(self) -> None:
    # Asserted against the literal and against its derivation, not one or the other. The
    # derivation alone moves with the constant, so it cannot tell 10.0 from 3600.0; the
    # literal alone would let the two operands drift apart from the value they explain.
    assert temperature_controller_module._CLEANUP_TIMEOUT == 10.0
    assert (
      temperature_controller_module._CLEANUP_TIMEOUT
      == 2 * control_box_module._COMMAND_TIMEOUT + 4.0
    )
    # Both registration sites read one constant, so the two cleanups cannot be bounded
    # differently by accident.
    assert thermoshake_module._CLEANUP_TIMEOUT == temperature_controller_module._CLEANUP_TIMEOUT

  async def test_cpac_cleanup_overrun_ends_the_lifespan(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A CPAC cleanup that cannot reach the shared transaction gives up and the exit returns.

    The overrun surfaces as a bare `TimeoutError`, because `shield_timeout` is implemented as
    `anyio.fail_after`; the callback frame in the traceback is what names the action. Nothing
    here reads the message, deliberately.
    """
    control_box, io = make_control_box(monkeypatch, blocked_event="read")
    observed = anyio.Event()

    async def hold_transaction() -> None:
      await control_box.send_command("0ADD1")

    async with control_box:
      backend = _cpac(control_box)
      baseline = len(io.commands)

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(hold_transaction)
          task_group.start_soon(_release_held_exchange_when_observed, io, observed)
          await io.blocked_started.wait()
          try:
            with patch.object(
              temperature_controller_module, "_CLEANUP_TIMEOUT", _SHRUNK_CLEANUP_TIMEOUT
            ):
              with pytest.raises(TimeoutError):
                async with backend:
                  pass
          finally:
            # Released even when the assertion above fails, so a failing run hands the
            # transaction back rather than burning the deadlock guard.
            observed.set()

      # The cleanup never owned the transaction, so it put no byte on the wire, and the
      # lifespan is closed rather than parked inside its shield.
      assert io.commands[baseline:] == ["0ADD1"]
      assert not backend.is_active
      assert control_box.is_active
      assert io.exits == 0
      io.blocked_event = None

  async def test_thermoshake_cleanup_overrun_ends_the_lifespan(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Both of a ThermoShake's registered cleanups are bounded, not just the inherited one.

    The two sites read the constant from different module namespaces, so both are shrunk
    here; leaving either at its real value would make this test wait out that bound instead.
    """
    control_box, io = make_control_box(monkeypatch, blocked_event="read")
    observed = anyio.Event()

    async def hold_transaction() -> None:
      await control_box.send_command("0ADD1")

    async with control_box:
      backend = _thermoshake(control_box)
      baseline = len(io.commands)

      with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(hold_transaction)
          task_group.start_soon(_release_held_exchange_when_observed, io, observed)
          await io.blocked_started.wait()
          try:
            with patch.object(
              temperature_controller_module, "_CLEANUP_TIMEOUT", _SHRUNK_CLEANUP_TIMEOUT
            ):
              with patch.object(thermoshake_module, "_CLEANUP_TIMEOUT", _SHRUNK_CLEANUP_TIMEOUT):
                with pytest.raises(TimeoutError):
                  async with backend:
                    pass
          finally:
            observed.set()

      assert io.commands[baseline:] == ["0ADD1"]
      assert not backend.is_active
      assert control_box.is_active
      assert io.exits == 0
      io.blocked_event = None
