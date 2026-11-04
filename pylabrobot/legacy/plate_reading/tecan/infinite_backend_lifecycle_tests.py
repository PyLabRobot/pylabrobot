from typing import (
  Any,
  Awaitable,
  Callable,
  Dict,
  List,
  Literal,
  Optional,
  Sequence,
  Union,
  cast,
)

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.usb import USB
from pylabrobot.legacy.plate_reading.tecan import infinite_backend
from pylabrobot.legacy.plate_reading.tecan.infinite_backend import (
  ExperimentalTecanInfinite200ProBackend,
  _LuminescenceMeasurement,
  _LuminescenceRunDecoder,
  _MeasurementDecoder,
)
from pylabrobot.resources import Coordinate, Plate, Well, create_ordered_items_2d
from pylabrobot.testing.concurrency import AnyioTestBase

ReadEffect = Union[bytes, BaseException, Callable[[], Awaitable[bytes]]]
PublicRead = Literal["absorbance", "fluorescence", "luminescence"]


class RecordingUSB(HasLifespan):
  """Replay native USB frames and record transport ownership and wire events."""

  def __init__(
    self,
    reads: Optional[Sequence[ReadEffect]] = None,
    *,
    events: Optional[List[str]] = None,
    entry_errors: Optional[Dict[int, BaseException]] = None,
    block_entry: Optional[anyio.Event] = None,
    write_errors: Optional[Dict[str, BaseException]] = None,
    write_effects: Optional[Dict[str, Sequence[Optional[BaseException]]]] = None,
  ) -> None:
    super().__init__()
    self.reads = list(reads or [])
    self.events = events if events is not None else []
    self.entry_errors = entry_errors or {}
    self.block_entry = block_entry
    self.write_errors = write_errors or {}
    self.write_effects = {
      command: list(effects) for command, effects in (write_effects or {}).items()
    }
    self.commands: List[str] = []
    self.entries = 0
    self.exits = 0
    self.reconnect_calls = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingUSB does not accept lifespan options")
    self.entries += 1
    self.events.append("usb.enter")
    stack.push_shielded_async_callback(self._close)
    failure = self.entry_errors.get(self.entries)
    if failure is not None:
      raise failure
    if self.block_entry is not None:
      self.block_entry.set()
      await anyio.sleep_forever()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.exits += 1
    self.events.append("usb.exit")

  async def reconnect(self) -> None:
    """Replace a fake physical connection without leaving the USB lifespan."""
    if not self.is_active:
      raise RuntimeError("reconnect requires an active lifespan")
    self.reconnect_calls += 1
    self.events.append("usb.reconnect")
    failure = self.entry_errors.get(self.reconnect_calls + 1)
    if failure is not None:
      raise failure

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    del timeout
    await anyio.lowlevel.checkpoint()
    command = data[1 : data.index(b"\x03")].decode("ascii")
    self.commands.append(command)
    self.events.append(f"write:{command}")
    effects = self.write_effects.get(command)
    if effects:
      effect = effects.pop(0)
      if effect is not None:
        raise effect
    failure = self.write_errors.get(command)
    if failure is not None:
      raise failure

  async def read(self, size: int, timeout: Optional[float] = None) -> bytes:
    del size, timeout
    await anyio.lowlevel.checkpoint()
    if not self.reads:
      return _frame("ST")
    effect = self.reads.pop(0)
    if isinstance(effect, BaseException):
      raise effect
    return await effect() if callable(effect) else effect


class RecordingCapabilities(dict):
  """Record when backend-owned capability state is discarded."""

  def __init__(self, events: List[str]) -> None:
    super().__init__()
    self.events = events

  def clear(self) -> None:
    self.events.append("state.clear")
    super().clear()


class EmptyDecoder(_MeasurementDecoder):
  @property
  def count(self) -> int:
    return 0


def _frame(text: str) -> bytes:
  return ExperimentalTecanInfinite200ProBackend._frame_command(text)


def _entry_reads(capability: str = "700") -> List[bytes]:
  return [_frame("ST"), _frame("ST"), _frame("ST"), _frame(capability)]


def _make_backend(
  reads: Optional[Sequence[ReadEffect]] = None,
  **usb_kwargs: Any,
) -> tuple[ExperimentalTecanInfinite200ProBackend, RecordingUSB]:
  backend = ExperimentalTecanInfinite200ProBackend()
  usb = RecordingUSB(reads, **usb_kwargs)
  backend.io = cast(USB, usb)
  return backend, usb


def _one_well_plate() -> Plate:
  plate = Plate(
    "plate",
    size_x=10,
    size_y=10,
    size_z=10,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=1,
      dy=1,
      dz=0,
      item_dx=0,
      item_dy=0,
      size_x=4,
      size_y=4,
      size_z=5,
    ),
  )
  plate.location = Coordinate.zero()
  return plate


def _luminescence_measurement_frame(intensity: int = 1000) -> bytes:
  payload = bytearray(14)
  payload[10:14] = intensity.to_bytes(4, "big", signed=True)
  return _frame("14,BIN:") + bytes(payload) + bytes(4)


async def _run_public_read(
  backend: ExperimentalTecanInfinite200ProBackend, read: PublicRead
) -> None:
  plate = _one_well_plate()
  if read == "absorbance":
    await backend.read_absorbance(plate, [], wavelength=600)
  elif read == "fluorescence":
    await backend.read_fluorescence(plate, [], excitation_wavelength=485, emission_wavelength=520)
  else:
    await backend.read_luminescence(plate, [])


async def _cancelled_entry(backend: ExperimentalTecanInfinite200ProBackend) -> None:
  async with backend:
    pytest.fail("cancelled entry reached the body")


class TestTecanInfiniteLifecycle(AnyioTestBase):
  def test_inherits_managed_methods(self) -> None:
    assert ExperimentalTecanInfinite200ProBackend.__aenter__ is HasLifespan.__aenter__
    assert ExperimentalTecanInfinite200ProBackend.__aexit__ is HasLifespan.__aexit__
    assert ExperimentalTecanInfinite200ProBackend.setup is HasLifespan.setup
    assert ExperimentalTecanInfinite200ProBackend.stop is HasLifespan.stop

  async def test_rejects_options_before_usb_entry(self) -> None:
    backend, usb = _make_backend()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options reached the body")

    assert usb.events == []

  async def test_usb_entry_failure_rolls_back(self) -> None:
    failure = RuntimeError("USB entry failed")
    backend, usb = _make_backend(entry_errors={1: failure})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed USB entry reached the body")

    assert error_info.value is failure
    assert usb.events == ["usb.enter", "usb.exit"]
    assert not backend.is_active

  async def test_initialization_failure_closes_usb(self) -> None:
    failure = RuntimeError("initialization failed")
    backend, usb = _make_backend(write_errors={"INIT FORCE": failure})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed initialization reached the body")

    assert error_info.value is failure
    assert usb.events[-1] == "usb.exit"
    assert not backend.is_active

  async def test_capability_failure_closes_usb(self) -> None:
    failure = RuntimeError("capability failed")
    backend, usb = _make_backend([_frame("ST"), _frame("ST")], write_errors={"MODE ABS": failure})

    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pytest.fail("failed capability query reached the body")

    assert error_info.value is failure
    assert usb.events[-1] == "usb.exit"
    assert not backend.is_active

  async def test_cancelled_entry_closes_usb(self) -> None:
    initialization_started = anyio.Event()
    release_reply = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def blocked_reply() -> bytes:
      initialization_started.set()
      await release_reply.wait()
      return _frame("ST")

    backend, usb = _make_backend([_frame("ST"), blocked_reply])

    async def enter() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await _cancelled_entry(backend)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await initialization_started.wait()
      scopes[0].cancel()
      release_reply.set()

    events_before_test_cleanup = list(usb.events)
    if usb.is_active:
      await usb.stop()
    assert events_before_test_cleanup[-1] == "usb.exit"
    assert not backend.is_active

  async def test_normal_exit_cleans_protocol_before_usb_close(self) -> None:
    events: List[str] = []
    backend, usb = _make_backend(_entry_reads(), events=events)
    backend._mode_capabilities = RecordingCapabilities(events)

    async with backend:
      assert backend._ready

    assert events[-3:] == ["write:ABSOLUTE MTP,IN", "state.clear", "usb.exit"]
    assert not backend._ready
    assert usb.exits == 1

  async def test_cleanup_failures_do_not_skip_usb_close(self) -> None:
    backend, usb = _make_backend(_entry_reads())
    failure = RuntimeError("protocol cleanup failed")

    async def fail_cleanup() -> None:
      await anyio.lowlevel.checkpoint()
      raise failure

    backend._cleanup_protocol = fail_cleanup  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as error_info:
      async with backend:
        pass

    assert error_info.value is failure
    assert usb.exits == 1
    assert not backend.is_active

  async def test_active_run_cancellation_sends_exact_cleanup_order(self) -> None:
    backend, usb = _make_backend(_entry_reads())
    body_started = anyio.Event()

    async def run() -> None:
      async with backend:
        backend._run_active = True
        backend._active_step_loss_commands = ["CHECK MTP.STEPLOSS"]
        body_started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    cleanup_commands = usb.commands[-4:]
    assert cleanup_commands == [
      "TERMINATE",
      "CHECK MTP.STEPLOSS",
      "KEYLOCK OFF",
      "ABSOLUTE MTP,IN",
    ]
    assert usb.events[-1] == "usb.exit"

  @pytest.mark.parametrize("read", ["absorbance", "fluorescence", "luminescence"])
  async def test_public_read_cancelled_before_keylock_commit_has_no_run_cleanup(
    self, read: PublicRead
  ) -> None:
    backend, usb = _make_backend()
    read_started = anyio.Event()
    cancelled = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def run_read() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        read_started.set()
        try:
          await _run_public_read(backend, read)
        except anyio.get_cancelled_exc_class():
          cancelled.set()

    async with backend._command_lock:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_read)
        await read_started.wait()
        await anyio.lowlevel.checkpoint()
        scopes[0].cancel()

    assert cancelled.is_set()
    assert usb.commands == []
    assert not backend._run_active
    assert backend._active_step_loss_commands == []

  @pytest.mark.parametrize("read", ["absorbance", "fluorescence", "luminescence"])
  async def test_public_read_precommit_failure_preserves_prior_cleanup_for_parent(
    self, read: PublicRead
  ) -> None:
    failure = RuntimeError("KEYLOCK transport write failed")
    backend, usb = _make_backend(_entry_reads(), write_errors={"KEYLOCK ON": failure})

    async with backend:
      backend._run_active = True
      backend._active_step_loss_commands = ["CHECK PRIOR.STEPLOSS"]
      commands_before_read = list(usb.commands)

      with pytest.raises(RuntimeError) as error_info:
        await _run_public_read(backend, read)

      assert error_info.value is failure
      assert usb.commands == commands_before_read + ["KEYLOCK ON"]
      assert backend._run_active
      assert backend._active_step_loss_commands == ["CHECK PRIOR.STEPLOSS"]

    assert usb.commands[len(commands_before_read) :] == [
      "KEYLOCK ON",
      "TERMINATE",
      "CHECK PRIOR.STEPLOSS",
      "KEYLOCK OFF",
      "ABSOLUTE MTP,IN",
    ]
    assert not backend._run_active
    assert backend._active_step_loss_commands == []

  @pytest.mark.parametrize("read", ["absorbance", "fluorescence", "luminescence"])
  async def test_public_read_cancelled_after_keylock_commit_cleans_before_propagation(
    self, read: PublicRead
  ) -> None:
    response_started = anyio.Event()
    release_response = anyio.Event()
    cancelled = anyio.Event()
    events: List[str] = []
    scopes: List[anyio.CancelScope] = []

    async def keylock_response() -> bytes:
      response_started.set()
      await release_response.wait()
      events.append("keylock.response.complete")
      return _frame("ST")

    backend, usb = _make_backend([keylock_response], events=events)

    async def run_read() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await _run_public_read(backend, read)
        except anyio.get_cancelled_exc_class():
          events.append("cancel.propagated")
          cancelled.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_read)
      await response_started.wait()
      scopes[0].cancel()
      release_response.set()

    assert cancelled.is_set()
    assert usb.commands == ["KEYLOCK ON", "TERMINATE", "KEYLOCK OFF", "ABSOLUTE MTP,IN"]
    assert events.index("keylock.response.complete") < events.index("write:TERMINATE")
    assert events.index("write:ABSOLUTE MTP,IN") < events.index("cancel.propagated")
    assert not backend._run_active
    assert backend._active_step_loss_commands == []

  @pytest.mark.parametrize("read", ["absorbance", "fluorescence", "luminescence"])
  async def test_public_read_keylock_response_failure_cleans_committed_run(
    self, read: PublicRead
  ) -> None:
    failure = RuntimeError("KEYLOCK response read failed")
    events: List[str] = []
    backend, usb = _make_backend([failure], events=events)

    try:
      await _run_public_read(backend, read)
    except RuntimeError as error:
      events.append("failure.propagated")
      assert error is failure
    else:
      pytest.fail("KEYLOCK response failure did not propagate")

    assert usb.commands == ["KEYLOCK ON", "TERMINATE", "KEYLOCK OFF", "ABSOLUTE MTP,IN"]
    assert events.index("write:ABSOLUTE MTP,IN") < events.index("failure.propagated")
    assert not backend._run_active
    assert backend._active_step_loss_commands == []

  @pytest.mark.parametrize("read", ["absorbance", "fluorescence", "luminescence"])
  async def test_public_read_keylock_transport_failure_does_not_invent_run_cleanup(
    self, read: PublicRead
  ) -> None:
    failure = RuntimeError("KEYLOCK transport write failed")
    backend, usb = _make_backend(write_errors={"KEYLOCK ON": failure})

    with pytest.raises(RuntimeError) as error_info:
      await _run_public_read(backend, read)

    assert error_info.value is failure
    assert usb.commands == ["KEYLOCK ON"]
    assert not backend._run_active
    assert backend._active_step_loss_commands == []

  async def test_public_read_cancellation_before_terminate_runs_full_cleanup(self) -> None:
    events: List[str] = []
    backend, usb = _make_backend(_entry_reads(), events=events)
    body_started = anyio.Event()
    cancelled = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def skip_configuration(*args: Any, **kwargs: Any) -> None:
      del args, kwargs

    async def block_scan(*args: Any, **kwargs: Any) -> None:
      del args, kwargs
      backend._active_step_loss_commands = [
        "CHECK MTP.STEPLOSS",
        "CHECK ABS.STEPLOSS",
      ]
      body_started.set()
      await anyio.sleep_forever()

    backend._configure_absorbance = skip_configuration  # type: ignore[method-assign]
    backend._run_scan = block_scan  # type: ignore[method-assign]

    async def run_read() -> None:
      async with backend:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.read_absorbance(_one_well_plate(), [], wavelength=600)
          except anyio.get_cancelled_exc_class():
            events.append("cancel.propagated")
            cancelled.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_read)
      await body_started.wait()
      scopes[0].cancel()

    assert cancelled.is_set()
    assert usb.commands[-7:] == [
      "TERMINATE",
      "CHECK MTP.STEPLOSS",
      "CHECK ABS.STEPLOSS",
      "KEYLOCK OFF",
      "ABSOLUTE MTP,IN",
      "KEYLOCK OFF",
      "ABSOLUTE MTP,IN",
    ]
    for event in (
      "write:TERMINATE",
      "write:CHECK MTP.STEPLOSS",
      "write:CHECK ABS.STEPLOSS",
      "write:KEYLOCK OFF",
      "write:ABSOLUTE MTP,IN",
    ):
      assert events.index(event) < events.index("cancel.propagated")
    assert events.index("cancel.propagated") < events.index("usb.exit")

  async def test_public_read_cancellation_after_terminate_response_finishes_cleanup(self) -> None:
    events: List[str] = []
    backend, usb = _make_backend(_entry_reads(), events=events)
    terminate_complete = anyio.Event()
    release_terminate = anyio.Event()
    cancelled = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    original_send_command = backend._send_command

    async def skip_configuration(*args: Any, **kwargs: Any) -> None:
      del args, kwargs

    async def complete_scan(*args: Any, **kwargs: Any) -> None:
      decoder = kwargs["decoder"]
      assert isinstance(decoder, _LuminescenceRunDecoder)
      backend._active_step_loss_commands = [
        "CHECK MTP.STEPLOSS",
        "CHECK LUM.STEPLOSS",
      ]
      decoder.measurements.append(_LuminescenceMeasurement(intensity=1000))

    async def gate_after_terminate(
      command: str,
      wait_for_terminal: bool = True,
      allow_timeout: bool = False,
      read_response: bool = True,
      recover_on_timeout: bool = True,
      *,
      _on_commit: Optional[Callable[[], None]] = None,
    ) -> List[str]:
      result = await original_send_command(
        command,
        wait_for_terminal=wait_for_terminal,
        allow_timeout=allow_timeout,
        read_response=read_response,
        recover_on_timeout=recover_on_timeout,
        _on_commit=_on_commit,
      )
      if command == "TERMINATE":
        events.append("terminate.response.complete")
        terminate_complete.set()
        await release_terminate.wait()
      return result

    backend._configure_luminescence = skip_configuration  # type: ignore[method-assign]
    backend._run_scan = complete_scan  # type: ignore[method-assign]
    backend._send_command = gate_after_terminate  # type: ignore[method-assign]

    async def run_read() -> None:
      async with backend:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend.read_luminescence(_one_well_plate(), [])
          except anyio.get_cancelled_exc_class():
            events.append("cancel.propagated")
            cancelled.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_read)
      await terminate_complete.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert not cancelled.is_set()
      release_terminate.set()

    assert cancelled.is_set()
    assert usb.commands[usb.commands.index("TERMINATE") :][:5] == [
      "TERMINATE",
      "CHECK MTP.STEPLOSS",
      "CHECK LUM.STEPLOSS",
      "KEYLOCK OFF",
      "ABSOLUTE MTP,IN",
    ]
    assert events.index("write:CHECK LUM.STEPLOSS") < events.index("cancel.propagated")

  async def test_public_body_and_cleanup_failures_retain_full_state_for_parent_retry(self) -> None:
    body_error = ValueError("measurement failed")
    cleanup_error = RuntimeError("MTP step-loss check failed")
    backend, usb = _make_backend(
      _entry_reads(),
      write_effects={"CHECK MTP.STEPLOSS": [cleanup_error, None]},
    )

    async def skip_configuration(*args: Any, **kwargs: Any) -> None:
      del args, kwargs

    async def fail_scan(*args: Any, **kwargs: Any) -> None:
      del args, kwargs
      backend._active_step_loss_commands = [
        "CHECK MTP.STEPLOSS",
        "CHECK ABS.STEPLOSS",
      ]
      raise body_error

    backend._configure_absorbance = skip_configuration  # type: ignore[method-assign]
    backend._run_scan = fail_scan  # type: ignore[method-assign]

    async with backend:
      with pytest.raises(RuntimeError) as error_info:
        await backend.read_absorbance(_one_well_plate(), [], wavelength=600)

      assert error_info.value is cleanup_error
      assert cleanup_error.__context__ is body_error
      assert backend._run_active
      assert backend._active_step_loss_commands == [
        "CHECK MTP.STEPLOSS",
        "CHECK ABS.STEPLOSS",
      ]
      assert usb.commands[-5:] == [
        "TERMINATE",
        "CHECK MTP.STEPLOSS",
        "CHECK ABS.STEPLOSS",
        "KEYLOCK OFF",
        "ABSOLUTE MTP,IN",
      ]

    assert usb.commands[-5:] == [
      "TERMINATE",
      "CHECK MTP.STEPLOSS",
      "CHECK ABS.STEPLOSS",
      "KEYLOCK OFF",
      "ABSOLUTE MTP,IN",
    ]
    assert not backend._run_active
    assert backend._active_step_loss_commands == []
    assert usb.events[-1] == "usb.exit"

  async def test_late_step_loss_failure_retries_complete_cleanup_without_omission(self) -> None:
    cleanup_error = RuntimeError("ABS step-loss check failed")
    backend, usb = _make_backend(
      _entry_reads(),
      write_effects={"CHECK ABS.STEPLOSS": [cleanup_error, None]},
    )

    async def skip_configuration(*args: Any, **kwargs: Any) -> None:
      del args, kwargs

    async def fail_body_after_publishing_cleanup(*args: Any, **kwargs: Any) -> None:
      del args, kwargs
      backend._active_step_loss_commands = [
        "CHECK MTP.STEPLOSS",
        "CHECK ABS.STEPLOSS",
      ]
      raise ValueError("measurement failed")

    backend._configure_absorbance = skip_configuration  # type: ignore[method-assign]
    backend._run_scan = fail_body_after_publishing_cleanup  # type: ignore[method-assign]

    async with backend:
      with pytest.raises(RuntimeError) as error_info:
        await backend.read_absorbance(_one_well_plate(), [], wavelength=600)

      assert error_info.value is cleanup_error
      assert backend._run_active
      assert backend._active_step_loss_commands == [
        "CHECK MTP.STEPLOSS",
        "CHECK ABS.STEPLOSS",
      ]
      assert usb.commands[-5:] == [
        "TERMINATE",
        "CHECK MTP.STEPLOSS",
        "CHECK ABS.STEPLOSS",
        "KEYLOCK OFF",
        "ABSOLUTE MTP,IN",
      ]

    assert usb.commands[-10:] == [
      "TERMINATE",
      "CHECK MTP.STEPLOSS",
      "CHECK ABS.STEPLOSS",
      "KEYLOCK OFF",
      "ABSOLUTE MTP,IN",
      "TERMINATE",
      "CHECK MTP.STEPLOSS",
      "CHECK ABS.STEPLOSS",
      "KEYLOCK OFF",
      "ABSOLUTE MTP,IN",
    ]
    assert not backend._run_active
    assert backend._active_step_loss_commands == []
    assert usb.events[-1] == "usb.exit"

  async def test_sequential_reuse_rebuilds_state(self) -> None:
    backend, usb = _make_backend(_entry_reads("700") + _entry_reads("800"))

    async with backend:
      assert backend._mode_capabilities == {"ABS": {"#BEAM DIAMETER": "700"}}
      backend._pending_bin_events.append((1, b"stale"))
    assert backend._mode_capabilities == {}
    assert backend._pending_bin_events == []

    async with backend:
      assert backend._mode_capabilities == {"ABS": {"#BEAM DIAMETER": "800"}}

    assert usb.events.count("usb.enter") == 2
    assert usb.events.count("usb.exit") == 2
    assert not backend._ready


class TestTecanInfiniteRecovery(AnyioTestBase):
  async def test_recovery_reconnects_transport_inside_parent_scope(
    self, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    backend, usb = _make_backend(_entry_reads() + [_frame("ST"), _frame("ST")])
    sleeps: List[float] = []

    async def record_sleep(delay: float) -> None:
      sleeps.append(delay)
      await anyio.lowlevel.checkpoint()

    monkeypatch.setattr(infinite_backend.anyio, "sleep", record_sleep)

    async with backend:
      await backend._recover_transport()
      assert usb.events.count("usb.enter") == 1
      assert usb.events.count("usb.exit") == 0
      assert usb.reconnect_calls == 1

    assert sleeps == [0.2]
    assert usb.events.count("usb.exit") == 1

  async def test_recovery_failure_leaves_no_unowned_usb(self) -> None:
    failure = RuntimeError("re-entry failed")
    backend, usb = _make_backend(_entry_reads(), entry_errors={2: failure})

    async with backend:
      with pytest.raises(RuntimeError) as error_info:
        await backend._recover_transport()
      assert error_info.value is failure
      assert usb.events.count("usb.enter") == 1
      assert usb.events.count("usb.exit") == 0
      assert usb.reconnect_calls == 1
      commands = list(usb.commands)
      with pytest.raises(ConnectionError, match="recovery"):
        await backend._send_command("QQ")
      assert usb.commands == commands

    assert usb.events.count("usb.exit") == 1
    assert usb.commands == commands

  async def test_recovery_initialization_timeout_does_not_recover_recursively(self) -> None:
    recursive_reentry = AssertionError("recovery recursively re-entered USB")
    backend, usb = _make_backend(
      _entry_reads()
      + [TimeoutError("recovery QQ timed out"), TimeoutError("recovery INIT timed out")],
      entry_errors={3: recursive_reentry},
    )

    async with backend:
      with pytest.raises(TimeoutError, match="recovery INIT timed out"):
        await backend._recover_transport()
      assert usb.events.count("usb.enter") == 1
      assert usb.events.count("usb.exit") == 0
      assert usb.reconnect_calls == 1

    assert usb.events.count("usb.enter") == 1
    assert usb.events.count("usb.exit") == 1


class TestTecanInfiniteTransactions(AnyioTestBase):
  async def test_concurrent_commands_do_not_cross_frames(self) -> None:
    first_read_started = anyio.Event()
    release_first = anyio.Event()

    async def first_reply() -> bytes:
      first_read_started.set()
      await release_first.wait()
      return _frame("FIRST")

    backend, usb = _make_backend([first_reply, _frame("SECOND")])
    results: Dict[str, List[str]] = {}

    async def send(name: str) -> None:
      results[name] = await backend._send_command(f"?{name}")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send, "FIRST")
      await first_read_started.wait()
      task_group.start_soon(send, "SECOND")
      await anyio.lowlevel.checkpoint()
      assert usb.commands == ["?FIRST"]
      release_first.set()

    assert results == {"FIRST": ["FIRST"], "SECOND": ["SECOND"]}

  async def test_cancel_after_write_reconciles_terminal_frame(self) -> None:
    terminal_started = anyio.Event()
    release_terminal = anyio.Event()
    events: List[str] = []

    async def terminal_reply() -> bytes:
      terminal_started.set()
      await release_terminal.wait()
      events.append("terminal.read")
      return _frame("ST")

    backend, usb = _make_backend(_entry_reads() + [terminal_reply], events=events)
    scopes: List[anyio.CancelScope] = []
    cancelled = anyio.Event()

    async def use_backend() -> None:
      async with backend:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          try:
            await backend._send_command("COMMITTED")
          except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await terminal_started.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert not cancelled.is_set()
      release_terminal.set()

    assert cancelled.is_set()
    assert events.index("terminal.read") < events.index("usb.exit")

  async def test_shaking_holds_transaction_until_terminal_frame(self) -> None:
    terminal_started = anyio.Event()
    release_terminal = anyio.Event()

    async def terminal_reply() -> bytes:
      terminal_started.set()
      await release_terminal.wait()
      return _frame("ST")

    backend, usb = _make_backend(
      [
        _frame("ST"),
        _frame("ORBITAL"),
        _frame("ST"),
        _frame("1000"),
        _frame("ST"),
        _frame("1"),
        _frame("BY#T1000"),
        terminal_reply,
        _frame("CONCURRENT"),
      ]
    )
    concurrent_result: List[str] = []
    concurrent_started = anyio.Event()

    async def concurrent_command() -> None:
      concurrent_started.set()
      concurrent_result.extend(await backend._send_command("?CONCURRENT"))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(backend.shake, 1)
      await terminal_started.wait()
      task_group.start_soon(concurrent_command)
      await concurrent_started.wait()
      await anyio.lowlevel.checkpoint()
      await anyio.lowlevel.checkpoint()
      assert usb.commands[-1] == "SHAKING ON"
      release_terminal.set()

    assert concurrent_result == ["CONCURRENT"]

  async def test_plate_movement_holds_transaction_through_position_confirmation(self) -> None:
    terminal_started = anyio.Event()
    release_terminal = anyio.Event()
    position_started = anyio.Event()
    release_position = anyio.Event()

    async def terminal_reply() -> bytes:
      terminal_started.set()
      await release_terminal.wait()
      return _frame("ST")

    async def position_reply() -> bytes:
      position_started.set()
      await release_position.wait()
      return _frame("OUT")

    backend, usb = _make_backend(
      [_frame("BY#T5000"), terminal_reply, position_reply, _frame("CONCURRENT")]
    )
    concurrent_result: List[str] = []

    async def concurrent_command() -> None:
      concurrent_result.extend(await backend._send_command("?CONCURRENT"))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(backend.open)
      await terminal_started.wait()
      task_group.start_soon(concurrent_command)
      await anyio.lowlevel.checkpoint()
      assert usb.commands == ["ABSOLUTE MTP,OUT"]
      release_terminal.set()
      await position_started.wait()
      await anyio.lowlevel.checkpoint()
      assert usb.commands == ["ABSOLUTE MTP,OUT", "?ABSOLUTE MTP,POS"]
      release_position.set()

    assert usb.commands[:2] == ["ABSOLUTE MTP,OUT", "?ABSOLUTE MTP,POS"]
    assert concurrent_result == ["CONCURRENT"]

  async def test_scan_holds_transaction_through_measurements_and_terminal(self) -> None:
    measurement_started = anyio.Event()
    release_measurement = anyio.Event()
    terminal_started = anyio.Event()
    release_terminal = anyio.Event()

    async def measurement_reply() -> bytes:
      measurement_started.set()
      await release_measurement.wait()
      return _luminescence_measurement_frame()

    async def terminal_reply() -> bytes:
      terminal_started.set()
      await release_terminal.wait()
      return _frame("ST")

    backend, usb = _make_backend(
      [
        _frame("ST"),
        _frame("ST"),
        _frame("ST"),
        measurement_reply,
        terminal_reply,
        _frame("CONCURRENT"),
      ]
    )
    plate = _one_well_plate()
    decoder = _LuminescenceRunDecoder(1)
    concurrent_result: List[str] = []

    async def scan() -> None:
      await backend._run_scan(
        plate.get_all_items(), decoder, "Luminescence", ["CHECK MTP.STEPLOSS"], False, "UP"
      )

    async def concurrent_command() -> None:
      concurrent_result.extend(await backend._send_command("?CONCURRENT"))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(scan)
      await measurement_started.wait()
      task_group.start_soon(concurrent_command)
      await anyio.lowlevel.checkpoint()
      assert usb.commands[-1].startswith("SCANX ")
      release_measurement.set()
      await terminal_started.wait()
      await anyio.lowlevel.checkpoint()
      assert usb.commands[-1].startswith("SCANX ")
      release_terminal.set()

    assert decoder.count == 1
    assert concurrent_result == ["CONCURRENT"]


class TestTecanInfiniteTiming(AnyioTestBase):
  async def test_await_measurements_uses_anyio_deadline(self) -> None:
    backend, _ = _make_backend()
    backend._max_row_wait_s = 0.01

    async def idle_read(size: int, timeout: Optional[int] = None, **kwargs: Any) -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    backend._read_packet = idle_read  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Timed out while parsing absorbance results"):
      await backend._await_measurements(EmptyDecoder(1), 1, "Absorbance")
