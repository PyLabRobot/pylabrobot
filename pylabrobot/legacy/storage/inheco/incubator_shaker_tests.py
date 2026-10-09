from collections import deque
from contextlib import suppress
from typing import Any, Deque, Dict, List, Optional, Tuple, Union, cast
from unittest.mock import patch

import anyio
import pytest
from trio.testing import MockClock

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.storage.inheco import incubator_shaker_backend as backend_module
from pylabrobot.legacy.storage.inheco.incubator_shaker import IncubatorShakerStack
from pylabrobot.legacy.storage.inheco.incubator_shaker_backend import (
  InhecoIncubatorShakerStackBackend,
  InhecoIncubatorUnitType,
)
from pylabrobot.resources import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase

CommandEffect = Union[BaseException, anyio.Event]

_REAL_TIME_DEADLOCK_GUARD = 10.0
_REAL_TIME_STALL = 1.0


def _response(payload: str = "", *, header: int = 0xB2) -> bytes:
  """Build one independently specified successful Inheco response frame."""
  return bytes([header]) + payload.encode("ascii") + bytes([header, 0x20, 0x60])


def _type_code(unit_type: InhecoIncubatorUnitType) -> str:
  """Return the firmware's literal type code for a test composition."""
  return {
    "incubator_mp": "0",
    "incubator_shaker_mp": "1",
    "incubator_dwp": "2",
    "incubator_shaker_dwp": "3",
    "unknown": "9",
  }[unit_type]


def _decode_command(data: bytes) -> Tuple[int, str]:
  """Decode the stack index and command from a production wire packet."""
  text = data[3:-1].decode("ascii")
  return int(text[2]), text[3:]


class RecordingSerial(HasLifespan):
  """Typed software Serial double for lifecycle and transaction behavior."""

  def __init__(
    self,
    compositions: Optional[List[List[InhecoIncubatorUnitType]]] = None,
    *,
    dip_header: int = 0xB2,
    read_failures: Optional[Dict[Tuple[int, str], BaseException]] = None,
    write_failures: Optional[Dict[Tuple[int, str], BaseException]] = None,
    payloads: Optional[Dict[Tuple[int, str], str]] = None,
    close_error: Optional[BaseException] = None,
    dispatch_event_command: Optional[Tuple[int, str]] = None,
  ) -> None:
    super().__init__()
    self.compositions: Deque[List[InhecoIncubatorUnitType]] = deque(
      compositions or [["incubator_shaker_mp"]]
    )
    self.composition: List[InhecoIncubatorUnitType] = []
    self.dip_header = dip_header
    self.read_failures = read_failures or {}
    self.write_failures = write_failures or {}
    self.payloads = payloads or {}
    self.close_error = close_error
    self.dispatch_event_command = dispatch_event_command
    self.events: List[str] = []
    self.commands: List[Tuple[int, str]] = []
    self.serial_kwargs: Dict[str, Any] = {}
    self.entered_ports: List[Optional[str]] = []
    self.entries = 0
    self.exits = 0
    self.dtr = True
    self.rts = True
    self._port = "software-port"
    self._vid = 0x0403
    self._pid = 0x6001
    self._dip_pending = True
    self.blocked_command: Optional[Tuple[int, str]] = None
    self.blocked_started = anyio.Event()
    self.release_blocked = anyio.Event()
    self.dispatch_recorded = anyio.Event()
    self.read_stall = 0.0

  @property
  def port(self) -> str:
    """Return the software port selected for this entry."""
    return self._port

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, *, port: Optional[str] = None
  ) -> None:
    """Open one scripted transport generation and register its close."""
    self.entries += 1
    self.entered_ports.append(port)
    if port is not None:
      self._port = port
    self.composition = list(self.compositions.popleft())
    self._dip_pending = True
    self.events.append("serial.enter")
    stack.push_shielded_async_callback(self._close)
    await anyio.lowlevel.checkpoint()

  async def _close(self) -> None:
    """Record closure after all backend-owned unit callbacks."""
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("serial.exit")
    if self.close_error is not None:
      raise self.close_error

  async def write(self, data: bytes) -> None:
    """Record a wire commit before yielding, then apply a scripted failure."""
    stack_index, command = _decode_command(data)
    self.commands.append((stack_index, command))
    self.events.append(f"write:{stack_index}:{command}")
    if self.dispatch_event_command == (stack_index, command):
      self.dispatch_recorded.set()
    if command == "ACD":
      self.events.append(f"unit:{stack_index}")
    await anyio.lowlevel.checkpoint()
    failure = self.write_failures.get((stack_index, command))
    if failure is not None:
      raise failure

  async def read(self, size: int) -> bytes:
    """Return a complete frame, optionally blocking or failing at the response boundary."""
    del size
    stack_index, command = self.commands[-1]
    self.events.append(f"read:{stack_index}:{command}")
    await anyio.lowlevel.checkpoint()
    if self.blocked_command == (stack_index, command):
      self.blocked_started.set()
      await self.release_blocked.wait()
    if self.read_stall:
      await anyio.sleep(self.read_stall)
    failure = self.read_failures.get((stack_index, command))
    if failure is not None:
      raise failure

    if self._dip_pending:
      self._dip_pending = False
      return _response(header=self.dip_header)
    if command == "RDA0,1":
      return _response(str(len(self.composition)))
    if command == "RTS":
      return _response(_type_code(self.composition[stack_index]))
    if (stack_index, command) in self.payloads:
      return _response(self.payloads[(stack_index, command)])
    if command in {"RHE", "RSE"}:
      return _response("0")
    return _response()


def make_recording_backend(
  monkeypatch: pytest.MonkeyPatch,
  compositions: Optional[List[List[InhecoIncubatorUnitType]]] = None,
  **io_kwargs: Any,
) -> Tuple[InhecoIncubatorShakerStackBackend, RecordingSerial]:
  """Build the production backend with a typed software transport."""
  io = RecordingSerial(compositions, **io_kwargs)

  def build_serial(**kwargs: Any) -> Serial:
    io.serial_kwargs = dict(kwargs)
    return cast(Serial, io)

  monkeypatch.setattr(backend_module, "Serial", build_serial)
  backend = InhecoIncubatorShakerStackBackend()
  return backend, io


def cleanup_events(events: List[str]) -> List[str]:
  """Return unit completion and Serial-close events from a full transcript."""
  return [event for event in events if event.startswith("unit:") or event == "serial.exit"]


async def settle() -> None:
  """Give child tasks room to reach an event-defined blocking boundary."""
  for _ in range(10):
    await anyio.lowlevel.checkpoint()


class FakeIO(HasLifespan):
  """Record transport closure without opening a serial device."""

  def __init__(self, events: List[str]) -> None:
    super().__init__()
    self.events = events

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Register the software transport's test-owned close."""
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    """Record the software transport close."""
    self.events.append("io.stop")


class FakeInhecoBackend(InhecoIncubatorShakerStackBackend):
  """Exercise the real shaker state machine above a command-level software double."""

  def __init__(self, composition: Optional[List[InhecoIncubatorUnitType]] = None) -> None:
    super().__init__(port="unused")
    self.composition = composition or ["incubator_shaker_mp"]
    self.events: List[str] = []
    self.effects: Dict[Tuple[int, str], CommandEffect] = {}
    self.physical_shaking: Dict[int, bool] = {}
    self.io = FakeIO(self.events)  # type: ignore[assignment]

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, *, port: Optional[str] = None
  ) -> None:
    """Publish scripted units and register test-owned unit and transport cleanup."""
    if port is not None and not isinstance(port, str):
      raise TypeError("port must be a string or None")
    await stack.enter_async_context(self.io.lifespan())
    self.events.append("setup")
    self.unit_composition = list(self.composition)
    self.physical_shaking = {index: False for index in range(len(self.composition))}
    for index in range(len(self.composition)):
      stack.push_shielded_async_callback(self._stop_unit_for_exit, index, shield_timeout=95.2)
    self.setup_finished = True

  async def send_command(self, command: str, **kwargs: Any) -> str:
    index = kwargs.get("stack_index", 0)
    self.events.append(f"{index}:{command}")
    effect = self.effects.get((index, command))
    if isinstance(effect, BaseException):
      raise effect
    if isinstance(effect, anyio.Event):
      effect.set()
      await anyio.sleep_forever()
    if command == "RSE":
      return "1" if self.physical_shaking[index] else "0"
    if command == "ASEND":
      self.physical_shaking[index] = True
    elif command == "ASE0":
      self.physical_shaking[index] = False
    if command == "RHE":
      return "0"
    return ""


def make_stack(
  composition: Optional[List[InhecoIncubatorUnitType]] = None,
) -> tuple[IncubatorShakerStack, FakeInhecoBackend]:
  """Build a stack whose complete device boundary is a software double."""
  backend = FakeInhecoBackend(composition)
  stack = IncubatorShakerStack(backend)
  if composition is not None and "incubator_mp" in composition:
    stack._incubator_loading_tray_location = dict(stack._incubator_loading_tray_location)
    stack._incubator_loading_tray_location["incubator_mp"] = Coordinate(x=30.5, y=-150.5, z=20)
  return stack, backend


def shaker_events(events: List[str]) -> List[str]:
  """Return only shaker mutation commands from a complete lifecycle transcript."""
  return [
    event
    for event in events
    if ":SSP" in event or event.endswith(":ASEND") or event.endswith(":ASE0")
  ]


def exception_chain(error: BaseException) -> List[BaseException]:
  """Return an exception and its contexts, rejecting cycles."""
  chain: List[BaseException] = []
  current: Optional[BaseException] = error
  while current is not None:
    assert current not in chain
    chain.append(current)
    current = current.__context__
  return chain


class TestInhecoStackLifecycle(AnyioTestBase):
  def test_managed_methods_are_inherited(self) -> None:
    """Deleting the native entry and restoring either legacy managed method fails this guard."""
    assert InhecoIncubatorShakerStackBackend.setup is HasLifespan.setup
    assert InhecoIncubatorShakerStackBackend.stop is HasLifespan.stop
    assert FakeInhecoBackend.setup is HasLifespan.setup

  async def test_port_option_is_forwarded_to_serial_lifespan(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Dropping or forwarding the wrong explicit port fails at the transport boundary."""
    backend, io = make_recording_backend(monkeypatch, [[]])

    async with backend.lifespan(port="software-override"):
      pass

    assert io.entered_ports == ["software-override"]
    assert io.exits == 1

  async def test_dip_failure_closes_serial(self, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retaining the direct interactive close makes this backend-dependent failure fail."""
    backend, io = make_recording_backend(
      monkeypatch, [[]], read_failures={(0, "RTS"): RuntimeError("DIP read failed")}
    )

    with pytest.raises(RuntimeError, match="failed DIP switch verification"):
      async with backend:
        pytest.fail("DIP verification failure reached the body")

    assert io.events == ["serial.enter", "write:0:RTS", "read:0:RTS", "serial.exit"]
    assert io.exits == 1

  async def test_discovery_failure_does_not_publish_partial_composition(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Appending each discovered type to public state exposes a partial stack on failure."""
    failure = RuntimeError("unit 1 discovery failed")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp"]],
      read_failures={(1, "RTS"): failure},
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed discovery reached the body")

    assert error_info.value is failure
    assert backend.unit_composition == []
    assert cleanup_events(io.events) == ["unit:0", "serial.exit"]

  async def test_initialize_failure_cleans_discovered_units_in_reverse(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Registering cleanup after the loop leaks initialized units when a later AID fails."""
    failure = RuntimeError("unit 2 initialize failed")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_mp"]],
      write_failures={(2, "AID"): failure},
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed initialization reached the body")

    assert error_info.value is failure
    assert backend.unit_composition == []
    assert cleanup_events(io.events) == ["unit:2", "unit:1", "unit:0", "serial.exit"]

  async def test_aid_write_failure_after_dispatch_registers_unit_cleanup(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Publishing ownership only after a successful AID write leaks an ambiguous unit."""
    failure = RuntimeError("AID write failed after dispatch")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_mp"]],
      write_failures={(0, "AID"): failure},
      dispatch_event_command=(0, "AID"),
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed AID write reached the body")

    assert error_info.value is failure
    assert io.dispatch_recorded.is_set()
    assert cleanup_events(io.events) == ["unit:0", "serial.exit"]

  async def test_cancellation_during_aid_response_cleans_that_unit(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Registering after send_command returns loses AID ownership at its final checkpoint."""
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_mp"]],
      dispatch_event_command=(2, "AID"),
    )
    io.blocked_command = (2, "AID")
    scopes: List[anyio.CancelScope] = []

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend:
          pytest.fail("cancelled AID entry reached the body")

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_backend)
        await io.blocked_started.wait()
        assert io.dispatch_recorded.is_set()
        scopes[0].cancel()
        io.blocked_command = None
        io.release_blocked.set()

    assert cleanup_events(io.events) == ["unit:2", "unit:1", "unit:0", "serial.exit"]
    assert backend.unit_composition == []

  async def test_entry_cancellation_cleans_units_then_serial(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Unshielded or deferred registrations abandon initialized units on entry cancellation."""
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_mp", "incubator_dwp"]],
    )
    io.blocked_command = (3, "RTS")
    scopes: List[anyio.CancelScope] = []

    async def enter_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend:
          pytest.fail("cancelled entry reached the body")

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_backend)
        await io.blocked_started.wait()
        scopes[0].cancel()
        io.blocked_command = None
        io.release_blocked.set()

    assert cleanup_events(io.events) == ["unit:2", "unit:1", "unit:0", "serial.exit"]
    assert backend.unit_composition == []

  async def test_body_cancellation_cleans_all_units_then_serial(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Ascending registration or unshielded exit changes the required reverse cleanup transcript."""
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_shaker_mp"]],
    )
    body_started = anyio.Event()

    async def use_backend() -> None:
      async with backend:
        backend._shaking_active.update({0: True, 1: True, 2: True})
        backend._shaking_state_unknown.update({0: True, 1: True, 2: True})
        backend._shaking_scope_tokens.update({0: object(), 1: object(), 2: object()})
        body_started.set()
        await anyio.sleep_forever()

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_backend)
        await body_started.wait()
        assert backend._shaking_scope_tokens
        task_group.cancel_scope.cancel()

    assert cleanup_events(io.events) == ["unit:2", "unit:1", "unit:0", "serial.exit"]
    assert backend._shaking_active == {}
    assert backend._shaking_state_unknown == {}
    assert backend._shaking_scope_tokens == {}

  async def test_multiple_cleanup_failures_remain_visible(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """A plain finally chain hides one unit failure or skips later cleanup."""
    unit_zero = RuntimeError("unit zero close failed")
    unit_two = RuntimeError("unit two close failed")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_mp", "incubator_dwp", "incubator_mp"]],
      read_failures={(0, "ACD"): unit_zero, (2, "ACD"): unit_two},
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert [str(error) for error in exception_chain(error_info.value)][:2] == [
      "unit zero close failed",
      "unit two close failed",
    ]
    assert cleanup_events(io.events) == ["unit:2", "unit:1", "unit:0", "serial.exit"]

  @pytest.mark.parametrize("failure_boundary", ["write_failures", "read_failures"])
  async def test_unit_cleanup_temperature_query_failure_still_stops_shaker_and_closes(
    self, monkeypatch: pytest.MonkeyPatch, failure_boundary: str
  ) -> None:
    """A failed RHE query must not skip independent shaker and drawer safety actions."""
    failure = RuntimeError(f"RHE {failure_boundary} failure")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_shaker_mp"]],
      **{failure_boundary: {(2, "RHE"): failure}},
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        io.commands.clear()

    assert error_info.value is failure
    assert io.commands == [
      (2, "RHE"),
      (2, "RSE"),
      (2, "ACD"),
      (1, "RHE"),
      (1, "RSE"),
      (1, "ACD"),
      (0, "RHE"),
      (0, "RSE"),
      (0, "ACD"),
    ]

  @pytest.mark.parametrize("failure_boundary", ["write_failures", "read_failures"])
  async def test_unit_cleanup_temperature_stop_failure_still_stops_shaker_and_closes(
    self, monkeypatch: pytest.MonkeyPatch, failure_boundary: str
  ) -> None:
    """A failed SHE0 command must not skip independent shaker and drawer safety actions."""
    failure = RuntimeError(f"SHE0 {failure_boundary} failure")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_shaker_mp"]],
      payloads={(2, "RHE"): "1"},
      **{failure_boundary: {(2, "SHE0"): failure}},
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        io.commands.clear()

    assert error_info.value is failure
    assert io.commands == [
      (2, "RHE"),
      (2, "SHE0"),
      (2, "RSE"),
      (2, "ACD"),
      (1, "RHE"),
      (1, "RSE"),
      (1, "ACD"),
      (0, "RHE"),
      (0, "RSE"),
      (0, "ACD"),
    ]

  @pytest.mark.parametrize("failure_boundary", ["write_failures", "read_failures"])
  async def test_unit_cleanup_shaker_query_failure_still_attempts_stop_and_closes(
    self, monkeypatch: pytest.MonkeyPatch, failure_boundary: str
  ) -> None:
    """An unknown RSE result must trigger ASE0 without hiding the query failure."""
    failure = RuntimeError(f"RSE {failure_boundary} failure")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_shaker_mp"]],
      **{failure_boundary: {(2, "RSE"): failure}},
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        io.commands.clear()

    assert error_info.value is failure
    assert io.commands == [
      (2, "RHE"),
      (2, "RSE"),
      (2, "ASE0"),
      (2, "ACD"),
      (1, "RHE"),
      (1, "RSE"),
      (1, "ACD"),
      (0, "RHE"),
      (0, "RSE"),
      (0, "ACD"),
    ]
    assert backend._shaking_active == {}
    assert backend._shaking_state_unknown == {}

  @pytest.mark.parametrize("failure_boundary", ["write_failures", "read_failures"])
  async def test_unit_cleanup_shaker_stop_failure_retains_retryable_state_and_closes(
    self, monkeypatch: pytest.MonkeyPatch, failure_boundary: str
  ) -> None:
    """A failed ASE0 command must retain retry state while drawer closure still runs."""
    failure = RuntimeError(f"ASE0 {failure_boundary} failure")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_shaker_mp"]],
      payloads={(2, "RSE"): "1"},
      **{failure_boundary: {(2, "ASE0"): failure}},
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        io.commands.clear()

    assert error_info.value is failure
    assert io.commands == [
      (2, "RHE"),
      (2, "RSE"),
      (2, "ASE0"),
      (2, "ACD"),
      (1, "RHE"),
      (1, "RSE"),
      (1, "ACD"),
      (0, "RHE"),
      (0, "RSE"),
      (0, "ACD"),
    ]
    assert backend._shaking_active == {2: True}
    assert backend._shaking_state_unknown == {2: True}

  @pytest.mark.parametrize("failure_boundary", ["write_failures", "read_failures"])
  async def test_unit_cleanup_drawer_failure_does_not_skip_later_units(
    self, monkeypatch: pytest.MonkeyPatch, failure_boundary: str
  ) -> None:
    """A failed drawer close must preserve reverse cleanup of every remaining unit."""
    failure = RuntimeError(f"ACD {failure_boundary} failure")
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp", "incubator_shaker_mp"]],
      **{failure_boundary: {(2, "ACD"): failure}},
    )

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        io.commands.clear()

    assert error_info.value is failure
    assert io.commands == [
      (2, "RHE"),
      (2, "RSE"),
      (2, "ACD"),
      (1, "RHE"),
      (1, "RSE"),
      (1, "ACD"),
      (0, "RHE"),
      (0, "RSE"),
      (0, "ACD"),
    ]

  async def test_parent_cleanup_invalidates_tokens_before_first_unit_io(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Deferring token invalidation until after cleanup I/O leaves stale ownership visible."""
    backend, io = make_recording_backend(monkeypatch)
    lifespan = backend.lifespan()
    await lifespan.__aenter__()
    backend._shaking_scope_tokens[0] = object()
    io.commands.clear()
    io.blocked_command = (0, "RHE")

    async def exit_backend() -> None:
      await lifespan.__aexit__(None, None, None)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(exit_backend)
        await io.blocked_started.wait()
        assert backend._shaking_scope_tokens == {}
        io.blocked_command = None
        io.release_blocked.set()

    assert io.commands == [(0, "RHE"), (0, "RSE"), (0, "ACD")]

  async def test_sequential_reuse_rebuilds_backend_and_frontend_state(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Keeping stale public composition or Machine state breaks a second lifespan."""
    backend, io = make_recording_backend(
      monkeypatch,
      [
        ["incubator_shaker_mp", "incubator_shaker_dwp"],
        ["incubator_shaker_mp"],
      ],
    )
    frontend = IncubatorShakerStack(backend)

    async with frontend:
      first_units = list(frontend.units)
      assert not frontend.setup_finished
      assert backend.unit_composition == ["incubator_shaker_mp", "incubator_shaker_dwp"]
    assert not frontend.setup_finished

    async with frontend:
      assert not frontend.setup_finished
      assert backend.unit_composition == ["incubator_shaker_mp"]
      assert len(frontend.units) == len(frontend.loading_trays) == len(frontend.children) == 1
      assert frontend.units[0] not in first_units

    assert io.entries == 2
    assert io.exits == 2

  def test_cleanup_bound_covers_queueing_and_five_unit_commands(self) -> None:
    """Shrinking the fixed bound below one queued plus five cleanup exchanges fails this literal."""
    assert backend_module._CLEANUP_TIMEOUT == 95.2

  async def test_registered_unit_cleanup_has_a_finite_bound(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Deleting shield_timeout lets cleanup wait indefinitely behind the command owner."""
    backend, io = make_recording_backend(monkeypatch)

    async def hold_transaction() -> None:
      with suppress(TimeoutError):
        await backend.send_command("RZZ", delay=0)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with patch.object(backend_module, "_CLEANUP_TIMEOUT", 0.05, create=True):
        lifespan = backend.lifespan()
        await lifespan.__aenter__()
        io.blocked_command = (0, "RZZ")
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(hold_transaction)
          await io.blocked_started.wait()
          with pytest.raises(TimeoutError):
            await lifespan.__aexit__(None, None, None)
          io.blocked_command = None
          io.release_blocked.set()

    assert io.exits == 1

  async def test_timed_out_unit_action_does_not_skip_later_actions_or_units(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Sharing one action deadline skips later safety attempts after temperature stalls."""
    backend, io = make_recording_backend(
      monkeypatch,
      [["incubator_shaker_mp", "incubator_shaker_dwp"]],
    )
    attempts: List[Tuple[int, str]] = []

    async def stop_temperature(stack_index: int) -> None:
      await anyio.lowlevel.checkpoint()
      attempts.append((stack_index, "temperature"))
      await anyio.sleep_forever()

    async def stop_shaking(stack_index: int) -> None:
      await anyio.lowlevel.checkpoint()
      attempts.append((stack_index, "shaker"))

    async def close_drawer(stack_index: int) -> None:
      await anyio.lowlevel.checkpoint()
      attempts.append((stack_index, "drawer"))

    monkeypatch.setattr(backend, "_stop_temperature_for_exit", stop_temperature)
    monkeypatch.setattr(backend, "_stop_shaking_for_exit", stop_shaking)
    monkeypatch.setattr(backend, "close", close_drawer)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      with patch.object(backend_module, "_CLEANUP_TIMEOUT", 0.05):
        with pytest.raises(TimeoutError) as error_info:
          async with backend:
            attempts.clear()

    assert attempts == [
      (1, "temperature"),
      (1, "shaker"),
      (1, "drawer"),
      (0, "temperature"),
      (0, "shaker"),
      (0, "drawer"),
    ]
    assert len(exception_chain(error_info.value)) >= 2
    assert io.exits == 1


class TestInhecoStackTransactions(AnyioTestBase):
  async def test_cancelled_aid_waiter_does_not_publish_cleanup_ownership(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Calling the dispatch hook before cancellable lock wait publishes unattempted AID work."""
    backend, io = make_recording_backend(monkeypatch)
    io._dip_pending = False
    io.blocked_command = (0, "RFV0")
    queued = anyio.Event()
    dispatched = anyio.Event()

    async def held_command() -> None:
      await backend.send_command("RFV0", delay=0)

    async def waiting_aid() -> None:
      queued.set()
      await backend._send_command("AID", delay=0, _on_dispatch=dispatched.set)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as holder_group:
        holder_group.start_soon(held_command)
        await io.blocked_started.wait()
        async with anyio.create_task_group() as waiter_group:
          waiter_group.start_soon(waiting_aid)
          await queued.wait()
          await settle()
          waiter_group.cancel_scope.cancel()
        io.blocked_command = None
        io.release_blocked.set()

    assert not dispatched.is_set()
    assert io.commands == [(0, "RFV0")]

  async def test_send_command_transactions_do_not_interleave(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Removing the AnyIO transaction lock allows the second write before the first response."""
    backend, io = make_recording_backend(monkeypatch)
    io._dip_pending = False
    io.blocked_command = (0, "RFV0")

    async def send(command: str) -> None:
      await backend.send_command(command, delay=0)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(send, "RFV0")
        await io.blocked_started.wait()
        task_group.start_soon(send, "RFV2")
        await settle()
        assert io.commands == [(0, "RFV0")]
        io.blocked_command = None
        io.release_blocked.set()

    assert io.commands == [(0, "RFV0"), (0, "RFV2")]

  async def test_cancelled_command_reconciles_response(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Removing the post-write shield abandons a response and desynchronizes the next command."""
    backend, io = make_recording_backend(monkeypatch)
    io._dip_pending = False
    io.blocked_command = (0, "RFV0")
    scopes: List[anyio.CancelScope] = []
    observed: List[str] = []

    async def command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await backend.send_command("RFV0", delay=0)
        except anyio.get_cancelled_exc_class():
          observed.append("cancelled")
          raise

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(command)
        await io.blocked_started.wait()
        scopes[0].cancel()
        await settle()
        assert observed == []
        io.blocked_command = None
        io.release_blocked.set()

    assert observed == ["cancelled"]
    assert io.events.count("read:0:RFV0") == 1
    assert await backend.send_command("RFV2", delay=0) == ""

  async def test_cancelled_lock_waiter_writes_nothing(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Shielding lock wait and deleting the final pre-write stop commits a cancelled command."""
    backend, io = make_recording_backend(monkeypatch)
    io._dip_pending = False
    io.blocked_command = (0, "RFV0")
    queued = anyio.Event()

    async def held_command() -> None:
      await backend.send_command("RFV0", delay=0)

    async def waiting_command() -> None:
      queued.set()
      await backend.send_command("RFV2", delay=0)

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as holder_group:
        holder_group.start_soon(held_command)
        await io.blocked_started.wait()
        async with anyio.create_task_group() as waiter_group:
          waiter_group.start_soon(waiting_command)
          await queued.wait()
          await settle()
          waiter_group.cancel_scope.cancel()
        io.blocked_command = None
        io.release_blocked.set()

    assert io.commands == [(0, "RFV0")]

  async def test_overrun_yields_to_pending_cancellation(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Raising a stored timeout before the final checkpoint masks external cancellation."""
    backend, io = make_recording_backend(monkeypatch)
    io._dip_pending = False
    io.blocked_command = (0, "RFV0")
    observed: List[str] = []

    async def command() -> None:
      try:
        await backend.send_command("RFV0", delay=0, write_timeout=0.02, read_timeout=0.03)
      except anyio.get_cancelled_exc_class():
        observed.append("cancelled")
        raise
      except TimeoutError:
        observed.append("timeout")

    async def answer_late() -> None:
      await io.blocked_started.wait()
      await anyio.sleep(_REAL_TIME_STALL)
      io.release_blocked.set()

    with anyio.move_on_after(_REAL_TIME_DEADLOCK_GUARD) as guard_scope:
      async with anyio.create_task_group() as late_group:
        late_group.start_soon(answer_late)
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(command)
          await io.blocked_started.wait()
          task_group.cancel_scope.cancel()
        late_group.cancel_scope.cancel()

    assert not guard_scope.cancelled_caught
    assert observed == ["cancelled"]

  async def test_queued_command_uses_defaults_from_its_own_call(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Reading shared timeout defaults only after lock acquisition changes an in-flight call."""
    backend, io = make_recording_backend(monkeypatch)
    io._dip_pending = False
    io.blocked_command = (0, "RFV0")
    backend.read_timeout = 0.03
    queued = anyio.Event()
    observed: List[str] = []

    async def first() -> None:
      await backend.send_command("RFV0", delay=0)

    async def second() -> None:
      queued.set()
      try:
        await backend.send_command("RFV2", delay=0, write_timeout=0.02)
      except TimeoutError:
        observed.append("timeout")

    with anyio.fail_after(_REAL_TIME_DEADLOCK_GUARD):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(first)
        await io.blocked_started.wait()
        task_group.start_soon(second)
        await queued.wait()
        await settle()
        backend.read_timeout = 5.0
        io.blocked_command = None
        io.read_stall = 0.2
        io.release_blocked.set()

    assert observed == ["timeout"]


class TestInhecoTemperatureDeadline(AnyioTestBase):
  """Observe the AnyIO deadline on a Trio virtual clock without touching hardware."""

  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_wait_for_temperature_uses_anyio_deadline(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Retaining asyncio's loop clock fails under Trio and loses backend-neutral deadlines."""
    backend, io = make_recording_backend(
      monkeypatch,
      payloads={(0, "RTT"): "370", (0, "RHE"): "1", (0, "RAT1"): "200"},
    )
    backend.unit_composition = ["incubator_shaker_mp"]
    io._dip_pending = False

    start = anyio.current_time()
    with pytest.raises(TimeoutError, match="Timeout after 2.0s"):
      await backend.wait_for_temperature(stack_index=0, interval_s=0.5, timeout_s=2.0)
    elapsed = anyio.current_time() - start

    assert elapsed == pytest.approx(2.0, abs=0.05)

  async def test_wait_for_temperature_deadline_covers_initial_target_request(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    """Creating the deadline after initial queries lets a blocked RTT outlive the operation."""
    backend, io = make_recording_backend(
      monkeypatch,
      payloads={(0, "RTT"): "370", (0, "RHE"): "1", (0, "RAT1"): "200"},
    )
    backend.unit_composition = ["incubator_shaker_mp"]
    io._dip_pending = False
    io.blocked_command = (0, "RTT")

    async def release_committed_target_request() -> None:
      await io.blocked_started.wait()
      io.blocked_command = None
      io.release_blocked.set()

    with anyio.fail_after(5.0):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(release_committed_target_request)
        with pytest.raises(TimeoutError) as error_info:
          await backend.wait_for_temperature(stack_index=0, timeout_s=0.05)

    assert str(error_info.value) == (
      "Timeout after 0.1s: temperature nan °C did not reach target nan °C ±0.20 °C."
    )
    assert io.commands == [(0, "RTT")]


class TestInhecoShakingScopes(AnyioTestBase):
  async def test_one_unit_scope_starts_and_stops_with_durable_shake_defaults(self) -> None:
    stack, backend = make_stack()

    async with stack:
      backend.events.clear()
      async with stack[0].shaking(rpm=600):
        assert shaker_events(backend.events) == ["0:SSP30,30,100,100,90", "0:ASEND"]
      assert shaker_events(backend.events) == [
        "0:SSP30,30,100,100,90",
        "0:ASEND",
        "0:ASE0",
      ]

  async def test_different_units_have_independent_scopes(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])

    async with stack:
      backend.events.clear()
      async with stack[0].shaking(rpm=600):
        async with stack[1].shaking(rpm=900):
          assert backend.physical_shaking == {0: True, 1: True}
        assert backend.physical_shaking == {0: True, 1: False}

  async def test_same_unit_nested_scope_is_rejected_before_a_command(self) -> None:
    stack, backend = make_stack()

    async with stack:
      async with stack[0].shaking(rpm=600):
        before = list(backend.events)
        with pytest.raises(RuntimeError, match="already scoped"):
          async with stack[0].shaking(rpm=900):
            pytest.fail("nested scope reached its body")
        assert backend.events == before

  async def test_non_shaker_unit_scope_rejects_without_mutation(self) -> None:
    stack, backend = make_stack(["incubator_mp"])

    async with stack:
      backend.events.clear()
      with pytest.raises(RuntimeError, match="shaker-capable"):
        async with stack[0].shaking(rpm=600):
          pytest.fail("non-shaker scope reached its body")
      assert shaker_events(backend.events) == []

  async def test_stack_scope_skips_non_shakers_and_cleans_up_in_reverse_order(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_mp", "incubator_shaker_mp"])

    async with stack:
      backend.events.clear()
      async with stack.shaking(rpm=600):
        assert backend.physical_shaking == {0: True, 1: False, 2: True}
      assert shaker_events(backend.events) == [
        "0:SSP30,30,100,100,90",
        "0:ASEND",
        "2:SSP30,30,100,100,90",
        "2:ASEND",
        "2:ASE0",
        "0:ASE0",
      ]
      assert [event for event in backend.events if event.endswith(":RSE")] == ["0:RSE", "2:RSE"]

  async def test_later_stack_start_failure_rolls_back_earlier_units(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])
    backend.effects[(1, "ASEND")] = RuntimeError("unit 1 start failed")

    async with stack:
      backend.events.clear()
      with pytest.raises(RuntimeError, match="unit 1 start failed"):
        async with stack.shaking(rpm=600):
          pytest.fail("failed stack scope reached its body")
      assert shaker_events(backend.events)[-2:] == ["1:ASE0", "0:ASE0"]
      assert backend.physical_shaking == {0: False, 1: False}

  async def test_later_physical_conflict_rejects_stack_scope_before_any_mutation(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])

    async with stack:
      backend.physical_shaking[1] = True
      backend.events.clear()
      with pytest.raises(RuntimeError, match="already active"):
        async with stack.shaking(rpm=600):
          pytest.fail("stack scope adopted untracked physical shaking")
      assert backend.events == ["0:RSE", "1:RSE"]
      assert shaker_events(backend.events) == []
      assert backend.physical_shaking == {0: False, 1: True}

  async def test_stack_cleanup_failure_chain_contains_both_unit_errors(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])

    async with stack:
      try:
        with pytest.raises(RuntimeError, match="unit 0 stop failed") as error_info:
          async with stack.shaking(rpm=600):
            backend.events.clear()
            backend.effects[(0, "ASE0")] = RuntimeError("unit 0 stop failed")
            backend.effects[(1, "ASE0")] = RuntimeError("unit 1 stop failed")
        messages = [str(error) for error in exception_chain(error_info.value)]
        assert messages[:2] == ["unit 0 stop failed", "unit 1 stop failed"]
        assert [event for event in backend.events if event.endswith(":ASE0")] == [
          "1:ASE0",
          "0:ASE0",
        ]
      finally:
        backend.effects.clear()

  async def test_cancellation_while_waiting_for_backend_serialization_sends_nothing(self) -> None:
    stack, backend = make_stack()
    entered = anyio.Event()

    async def wait_for_scope() -> None:
      entered.set()
      async with stack[0].shaking(rpm=600):
        pytest.fail("cancelled waiter reached its body")

    async with stack:
      async with backend._shaking_transition_lock:
        backend.events.clear()
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(wait_for_scope)
          await entered.wait()
          await anyio.sleep(0)
          task_group.cancel_scope.cancel()
      assert shaker_events(backend.events) == []

  async def test_all_direct_aliases_for_scoped_index_reject_before_mutation(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])

    async with stack:
      unit = stack[0]
      async with unit.shaking(rpm=600):
        backend.events.clear()
        calls = [
          backend.shake(stack_index=0, rpm=600),
          backend.stop_shaking(stack_index=0),
          backend.set_shaker_status(True, stack_index=0),
          backend.set_shaker_parameters(3, 3, 10, 10, 90, stack_index=0),
          backend.set_shaker_pattern(
            pattern="orbital", stack_index=0, rpm=600, amplitude_x_mm=3, amplitude_y_mm=3
          ),
          unit.shake(rpm=600),
          unit.stop_shaking(),
          unit.set_shaker_status(False),
          unit.set_shaker_parameters(3, 3, 10, 10, 90, stack_index=0),
          unit.set_shaker_pattern(pattern="orbital", rpm=600, amplitude_x_mm=3, amplitude_y_mm=3),
          stack.shake(rpm=600),
          stack.stop_all_shaking(),
        ]
        for call in calls:
          with pytest.raises(RuntimeError, match="scope"):
            await call
        assert shaker_events(backend.events) == []

  async def test_stack_durable_start_preflights_every_unit_before_mutation(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])

    async with stack:
      async with stack[1].shaking(rpm=600):
        backend.events.clear()
        with pytest.raises(RuntimeError, match="scope"):
          await stack.shake(rpm=900)
        assert shaker_events(backend.events) == []

  async def test_stop_all_preflights_later_scope_before_stopping_earlier_unit(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])

    async with stack:
      await stack[0].set_shaker_status(True)
      async with stack[1].shaking(rpm=600):
        backend.events.clear()
        with pytest.raises(RuntimeError, match="scope"):
          await stack.stop_all_shaking()
        assert shaker_events(backend.events) == []
        assert backend.physical_shaking == {0: True, 1: True}

  async def test_stack_scope_rejects_durable_unit_without_stopping_it(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])

    async with stack:
      await stack[1].shake(rpm=600)
      backend.events.clear()
      with pytest.raises(RuntimeError, match="already active"):
        async with stack.shaking(rpm=900):
          pytest.fail("stack scope adopted durable shaking")
      assert shaker_events(backend.events) == []
      assert backend.physical_shaking[1]

  async def test_parent_cleanup_invalidates_live_unit_scope_token(self) -> None:
    stack, backend = make_stack()
    lifespan = stack.lifespan()
    await lifespan.__aenter__()
    scope = stack[0].shaking(rpm=600)
    await scope.__aenter__()

    await lifespan.__aexit__(None, None, None)
    stop_count = shaker_events(backend.events).count("0:ASE0")
    await scope.__aexit__(None, None, None)

    assert shaker_events(backend.events).count("0:ASE0") == stop_count

  async def test_sequential_stack_reuse_rebuilds_units_trays_and_children(self) -> None:
    stack, _ = make_stack(["incubator_shaker_mp", "incubator_shaker_dwp"])

    async with stack:
      first_units = list(stack.units)
      first_trays = list(stack.loading_trays)
      assert len(stack.children) == 2
    async with stack:
      assert len(stack.units) == 2
      assert len(stack.loading_trays) == 2
      assert len(stack.children) == 2
      assert all(unit not in first_units for unit in stack.units)
      assert all(tray not in first_trays for tray in stack.loading_trays)

  async def test_sequential_rediscovery_of_zero_units_clears_stale_collections(self) -> None:
    stack, backend = make_stack(["incubator_shaker_mp"])

    async with stack:
      assert len(stack.units) == 1
      assert len(stack.loading_trays) == 1
      assert len(stack.children) == 1
    backend.composition = []
    async with stack:
      assert stack.units == []
      assert stack.loading_trays == []
      assert stack.children == []
