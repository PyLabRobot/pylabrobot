"""Lifecycle tests for the legacy Byonoy plate reader backends."""

import struct
import sys
import threading
from typing import Any, Optional, cast

import anyio
import pytest
from trio.testing import MockClock

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.boundary import BoundaryRead
from pylabrobot.io.hid import HID
from pylabrobot.legacy.plate_reading.byonoy.byonoy_backend import (
  ByonoyAbsorbance96AutomateBackend,
  ByonoyLuminescence96AutomateBackend,
  _ByonoyBase,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingHID(HasLifespan):
  """Software HID double recording lifespan and heartbeat activity."""

  def __init__(
    self,
    events: list[str],
    *,
    entry_error: Optional[BaseException] = None,
    heartbeat_error: Optional[BaseException] = None,
    block_heartbeat: bool = False,
    responses: Optional[list[bytes]] = None,
    blocked_reads: Optional[set[int]] = None,
    block_before_write: bool = False,
    blocked_writes: Optional[set[int]] = None,
    task_responses: bool = False,
    automatic_byonoy_responses: bool = False,
    read_delay: float = 0,
  ) -> None:
    super().__init__()
    self.events = events
    self.entry_error = entry_error
    self.heartbeat_error = heartbeat_error
    self.block_heartbeat = block_heartbeat
    self.responses = list(responses or [])
    self.blocked_reads = blocked_reads or set()
    self.block_before_write = block_before_write
    self.blocked_writes = blocked_writes or set()
    self.task_responses = task_responses
    self.automatic_byonoy_responses = automatic_byonoy_responses
    self.read_delay = read_delay
    self.heartbeat_started = threading.Event()
    self.heartbeat_writes = 0
    self.writes: list[bytes] = []
    self.read_calls: list[tuple[int, Optional[int]]] = []
    self.consumed_reads = 0
    self.read_started = anyio.Event()
    self.release_read = anyio.Event()
    self.write_started = anyio.Event()
    self.blocked_write_started = anyio.Event()
    self.release_write = anyio.Event()
    self.task_reports: dict[int, int] = {}
    self.write_attempts = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingHID does not accept lifespan options")
    self.events.append("hid.enter")
    stack.push_shielded_async_callback(self._exit_hid)
    if self.entry_error is not None:
      raise self.entry_error

  async def _exit_hid(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("hid.exit")

  async def write(self, data: bytes, report_id: bytes = b"\x00") -> None:
    del report_id
    report = int.from_bytes(data[:2], byteorder="little")
    self.write_attempts += 1
    self.write_started.set()
    if self.block_before_write or self.write_attempts in self.blocked_writes:
      self.blocked_write_started.set()
      await self.release_write.wait()
    self.writes.append(data)
    self.task_reports[anyio.get_current_task().id] = report
    self.events.append(f"write:{report:#06x}")
    if report != 0x0040:
      await anyio.lowlevel.checkpoint()
      return

    self.heartbeat_writes += 1
    self.heartbeat_started.set()
    if self.heartbeat_error is not None:
      raise self.heartbeat_error
    if self.block_heartbeat:
      try:
        await anyio.sleep_forever()
      finally:
        self.events.append("heartbeat.done")
    await anyio.lowlevel.checkpoint()

  async def read(self, size: int, timeout: Optional[int] = None) -> bytes:
    self.read_calls.append((size, timeout))
    if len(self.read_calls) in self.blocked_reads:
      self.read_started.set()
      await self.release_read.wait()
    if self.read_delay:
      await anyio.sleep(self.read_delay)
    await anyio.lowlevel.checkpoint()
    self.consumed_reads += 1
    if self.task_responses:
      report = self.task_reports[anyio.get_current_task().id]
      return struct.pack("<H", report) + bytes([report & 0xFF])
    if self.automatic_byonoy_responses:
      report = int.from_bytes(self.writes[-1][:2], byteorder="little")
      if report == 0x0320:
        return _absorbance_chunk()
      if report == 0x0330:
        return _wavelength_response()
    if self.responses:
      return self.responses.pop(0)
    await anyio.sleep_forever()
    raise AssertionError("unreachable")

  async def read_to_boundary(self) -> BoundaryRead:
    """Return one scripted report with its physical boundary."""
    return BoundaryRead(await self.read(64), True, True, False)


class FailingInitializationBackend(ByonoyAbsorbance96AutomateBackend):
  """Absorbance backend whose reference initialization fails."""

  async def initialize_measurements(self) -> None:
    raise RuntimeError("initialization failed")


class CancellingInitializationBackend(ByonoyAbsorbance96AutomateBackend):
  """Absorbance backend whose reference initialization waits for cancellation."""

  def __init__(self, initialization_started: anyio.Event) -> None:
    super().__init__()
    self.initialization_started = initialization_started

  async def initialize_measurements(self) -> None:
    self.initialization_started.set()
    await anyio.sleep_forever()


def _with_io(backend: _ByonoyBase, io: RecordingHID) -> _ByonoyBase:
  """Install a software HID double on a Byonoy backend."""
  backend.io = cast(HID, io)
  backend._ping_interval = 0.01
  return backend


async def _wait_for_thread_event(event: threading.Event) -> None:
  """Wait portably for a signal that legacy code may set from a thread."""
  while not event.is_set():
    await anyio.sleep(0.001)


def _group_contains(group: BaseExceptionGroup, expected: BaseException) -> bool:
  """Return whether an exception group contains the exact expected failure."""
  for error in group.exceptions:
    if error is expected:
      return True
    if isinstance(error, BaseExceptionGroup) and _group_contains(error, expected):
      return True
  return False


def _absorbance_chunk() -> bytes:
  """Build one final absorbance row with literal protocol values."""
  return (
    struct.pack(
      "<HBBhhI12fBB",
      0x0500,
      0,
      1,
      660,
      0,
      125,
      *[float(value) for value in range(12)],
      0,
      100,
    )
    + b"\x00\x00"
  )


def _wavelength_response() -> bytes:
  """Build one wavelength-discovery response containing two wavelengths."""
  return struct.pack("<H30h", 0x0330, 450, 660, *([0] * 28))


def _luminescence_chunks() -> list[bytes]:
  """Build eight luminescence chunks containing one complete 96-well result."""
  return [
    (
      struct.pack(
        "<HBBII12fBB",
        0x0600,
        sequence,
        8,
        2_000_000,
        500,
        *[float(sequence * 12 + column) for column in range(12)],
        0,
        100,
      )
      + b"\x00\x00"
    )
    for sequence in range(8)
  ]


class TestByonoyLifecycle(AnyioTestBase):
  async def test_managed_methods_are_inherited(self) -> None:
    assert _ByonoyBase.__aenter__ is HasLifespan.__aenter__
    assert ByonoyAbsorbance96AutomateBackend.__aenter__ is HasLifespan.__aenter__
    assert ByonoyLuminescence96AutomateBackend.__aenter__ is HasLifespan.__aenter__
    assert _ByonoyBase.__aexit__ is HasLifespan.__aexit__
    assert ByonoyAbsorbance96AutomateBackend.__aexit__ is HasLifespan.__aexit__
    assert ByonoyLuminescence96AutomateBackend.__aexit__ is HasLifespan.__aexit__
    assert _ByonoyBase.setup is HasLifespan.setup
    assert ByonoyAbsorbance96AutomateBackend.setup is HasLifespan.setup
    assert ByonoyLuminescence96AutomateBackend.setup is HasLifespan.setup
    assert _ByonoyBase.stop is HasLifespan.stop
    assert ByonoyAbsorbance96AutomateBackend.stop is HasLifespan.stop
    assert ByonoyLuminescence96AutomateBackend.stop is HasLifespan.stop

  async def test_hid_entry_failure_rolls_back(self) -> None:
    events: list[str] = []
    error = RuntimeError("hid entry failed")
    backend = _with_io(
      ByonoyLuminescence96AutomateBackend(), RecordingHID(events, entry_error=error)
    )

    with pytest.raises(RuntimeError, match="hid entry failed"):
      async with backend:
        pytest.fail("failed HID entry reached body")

    assert events == ["hid.enter", "hid.exit"]
    assert not backend.is_active

  async def test_absorbance_initialization_failure_closes_hid(self) -> None:
    events: list[str] = []
    backend = _with_io(FailingInitializationBackend(), RecordingHID(events))

    with pytest.raises(RuntimeError, match="initialization failed"):
      async with backend:
        pytest.fail("failed initialization reached body")

    assert events == ["hid.enter", "hid.exit"]
    assert not backend.is_active

  async def test_cancelled_initialization_closes_hid(self) -> None:
    events: list[str] = []
    initialization_started = anyio.Event()
    backend = _with_io(
      CancellingInitializationBackend(initialization_started), RecordingHID(events)
    )

    async def enter_backend() -> None:
      async with backend:
        pytest.fail("cancelled initialization reached body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter_backend)
      await initialization_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["hid.enter", "hid.exit"]
    assert not backend.is_active

  async def test_heartbeat_is_joined_before_hid_close(self) -> None:
    events: list[str] = []
    io = RecordingHID(events, block_heartbeat=True)
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)
    before = set(threading.enumerate())

    async with backend:
      backend._start_background_pings()
      await _wait_for_thread_event(io.heartbeat_started)

    assert events[-2:] == ["heartbeat.done", "hid.exit"]
    assert set(threading.enumerate()) == before

  async def test_heartbeat_failure_fails_owning_lifespan(self) -> None:
    events: list[str] = []
    error = RuntimeError("heartbeat failed")
    io = RecordingHID(events, heartbeat_error=error)
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)

    with pytest.raises(BaseExceptionGroup) as error_info:
      async with backend:
        backend._start_background_pings()
        await _wait_for_thread_event(io.heartbeat_started)
        await anyio.lowlevel.checkpoint()

    assert _group_contains(error_info.value, error)
    assert events[-1] == "hid.exit"

  async def test_sequential_reuse_starts_fresh_heartbeat(self) -> None:
    events: list[str] = []
    io = RecordingHID(events)
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)
    before = set(threading.enumerate())

    async with backend:
      backend._start_background_pings()
      while io.heartbeat_writes < 1:
        await anyio.sleep(0.001)
    first_cycle_writes = io.heartbeat_writes

    io.heartbeat_started.clear()
    async with backend:
      backend._start_background_pings()
      while io.heartbeat_writes == first_cycle_writes:
        await anyio.sleep(0.001)

    assert events.count("hid.enter") == 2
    assert events.count("hid.exit") == 2
    assert io.heartbeat_writes > first_cycle_writes
    assert set(threading.enumerate()) == before

  async def test_lifespan_rejects_options_before_hid_entry(self) -> None:
    events: list[str] = []
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), RecordingHID(events))

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options reached body")

    assert events == []
    assert not backend.is_active

  async def test_command_frame_and_matching_response_are_preserved(self) -> None:
    unrelated = b"\x99\x99unrelated"
    matching = b"\x34\x12matching"
    io = RecordingHID([], responses=[unrelated, matching])
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)

    response = await backend.send_command(
      report_id=0x1234,
      payload=b"\xab",
      routing_info=b"\x80\x40",
    )

    assert io.writes == [b"\x34\x12\xab" + b"\x00" * 59 + b"\x80\x40"]
    assert io.read_calls == [(64, None), (64, None)]
    assert response is matching
    assert io.responses == []

  async def test_concurrent_command_transactions_do_not_cross_responses(self) -> None:
    io = RecordingHID([], blocked_reads={1}, task_responses=True)
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)
    responses: dict[int, bytes] = {}

    async def send(report: int) -> None:
      response = await backend.send_command(report, b"command")
      assert response is not None
      responses[report] = response

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send, 0x1111)
      await io.read_started.wait()
      task_group.start_soon(send, 0x2222)
      await anyio.sleep(0.01)
      writes_before_first_response = list(io.writes)
      io.release_read.set()

    assert [int.from_bytes(write[:2], "little") for write in writes_before_first_response] == [
      0x1111
    ]
    assert responses == {
      0x1111: b"\x11\x11\x11",
      0x2222: b'"""',
    }

  async def test_heartbeat_waits_for_command_transaction(self) -> None:
    io = RecordingHID([], blocked_reads={1}, task_responses=True)
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)
    command_done = anyio.Event()

    async def send() -> None:
      await backend.send_command(0x1234, b"command")
      command_done.set()

    async with backend:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(send)
        await io.read_started.wait()
        backend._start_background_pings()
        await anyio.sleep(0.03)
        writes_while_command_active = list(io.writes)
        io.release_read.set()
        await command_done.wait()
        await _wait_for_thread_event(io.heartbeat_started)
        task_group.cancel_scope.cancel()

    assert [int.from_bytes(write[:2], "little") for write in writes_while_command_active] == [
      0x1234
    ]
    assert [int.from_bytes(write[:2], "little") for write in io.writes[:2]] == [0x1234, 0x0040]

  async def test_cancelled_transaction_lock_wait_writes_nothing(self) -> None:
    io = RecordingHID([])
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)
    scope = anyio.CancelScope()
    finished = anyio.Event()

    async def send() -> None:
      with scope:
        await backend.send_command(0x1234, b"command", wait_for_response=False)
      finished.set()

    async with anyio.create_task_group() as task_group:
      async with backend._transaction_lock:
        task_group.start_soon(send)
        while backend._transaction_lock.statistics().tasks_waiting != 1:
          await anyio.lowlevel.checkpoint()
        scope.cancel()
      await finished.wait()

    assert io.writes == []

  async def test_cancelled_precommit_write_writes_nothing(self) -> None:
    io = RecordingHID([], block_before_write=True)
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)
    scope = anyio.CancelScope()
    finished = anyio.Event()

    async def send() -> None:
      with scope:
        await backend.send_command(0x1234, b"command", wait_for_response=False)
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.write_started.wait()
      scope.cancel()
      io.release_write.set()
      await finished.wait()

    assert io.writes == []

  async def test_cancelled_postcommit_command_drains_matching_response(self) -> None:
    io = RecordingHID([], blocked_reads={1}, task_responses=True)
    backend = _with_io(ByonoyLuminescence96AutomateBackend(), io)
    scope = anyio.CancelScope()
    finished = anyio.Event()
    returned: list[bytes] = []

    async def send() -> None:
      with scope:
        response = await backend.send_command(0x1234, b"command")
        assert response is not None
        returned.append(response)
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(send)
      await io.read_started.wait()
      scope.cancel()
      await anyio.sleep(0.01)
      finished_before_response = finished.is_set()
      io.release_read.set()
      await finished.wait()

    assert not finished_before_response
    assert io.consumed_reads == 1
    assert returned == []

  async def test_absorbance_measurement_is_one_finite_transaction(self) -> None:
    unrelated = b"\x99\x99unrelated"
    chunk = _absorbance_chunk()
    io = RecordingHID([], responses=[unrelated, chunk], blocked_reads={1})
    backend = cast(
      ByonoyAbsorbance96AutomateBackend,
      _with_io(ByonoyAbsorbance96AutomateBackend(), io),
    )
    rows: list[float] = []

    async def measure() -> None:
      rows.extend(await backend._run_abs_measurement(660, 0, False))

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measure)
      await io.read_started.wait()
      task_group.start_soon(backend.send_command, 0x7777, b"concurrent", False)
      await anyio.sleep(0.01)
      writes_while_measurement_active = list(io.writes)
      io.release_read.set()

    assert [int.from_bytes(write[:2], "little") for write in writes_while_measurement_active] == [
      0x0010,
      0x0200,
      0x0320,
    ]
    assert [int.from_bytes(write[:2], "little") for write in io.writes] == [
      0x0010,
      0x0200,
      0x0320,
      0x7777,
    ]
    assert io.read_calls == [(64, None), (64, None)]
    assert rows == [float(value) for value in range(12)]

  async def test_absorbance_initialization_precedes_wavelength_discovery(self) -> None:
    io = RecordingHID([], automatic_byonoy_responses=True)
    backend = cast(
      ByonoyAbsorbance96AutomateBackend,
      _with_io(ByonoyAbsorbance96AutomateBackend(), io),
    )

    async with backend:
      assert backend.available_wavelengths == [450, 660]

    assert [int.from_bytes(write[:2], "little") for write in io.writes] == [
      0x0010,
      0x0200,
      0x0320,
      0x0330,
    ]
    assert io.writes[2][:7] == b"\x20\x03\x94\x02\x00\x00\x01"
    assert io.writes[3][-2:] == b"\x80\x40"

  async def test_luminescence_sequence_is_one_transaction(self) -> None:
    unrelated = b"\x99\x99unrelated"
    io = RecordingHID([], responses=[unrelated, *_luminescence_chunks()], blocked_reads={1})
    backend = cast(
      ByonoyLuminescence96AutomateBackend,
      _with_io(ByonoyLuminescence96AutomateBackend(), io),
    )
    results: list[dict] = []
    command_done = anyio.Event()

    async def measure() -> None:
      results.extend(await backend.read_luminescence(cast(Any, None), [], 0, 2))

    async def send_concurrent_command() -> None:
      await backend.send_command(0x7777, b"concurrent", wait_for_response=False)
      command_done.set()

    async with backend:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(measure)
        await io.read_started.wait()
        task_group.start_soon(send_concurrent_command)
        backend._start_background_pings()
        await anyio.sleep(0.03)
        writes_while_measurement_active = list(io.writes)
        io.release_read.set()
        await command_done.wait()
        await _wait_for_thread_event(io.heartbeat_started)
        task_group.cancel_scope.cancel()

    assert [int.from_bytes(write[:2], "little") for write in writes_while_measurement_active] == [
      0x0010,
      0x0200,
      0x0340,
    ]
    assert all(write[-2:] == b"\x00\x00" for write in io.writes[:3])
    assert io.writes[2][:22] == (b"\x40\x03\x80\x84\x1e\x00" + b"\xff" * 12 + b"\x00\x00\x00\x00")
    assert {int.from_bytes(write[:2], "little") for write in io.writes[3:]} == {
      0x0040,
      0x7777,
    }
    assert io.read_calls == [(64, None)] * 9
    assert io.responses == []
    assert results[0]["data"] == [
      [float(row * 12 + column) for column in range(12)] for row in range(8)
    ]

  async def test_cancelled_precommit_luminescence_writes_nothing(self) -> None:
    io = RecordingHID([], responses=_luminescence_chunks(), blocked_writes={1})
    backend = cast(
      ByonoyLuminescence96AutomateBackend,
      _with_io(ByonoyLuminescence96AutomateBackend(), io),
    )
    scope = anyio.CancelScope()
    finished = anyio.Event()

    async def measure() -> None:
      with scope:
        await backend.read_luminescence(cast(Any, None), [], 0)
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measure)
      await io.blocked_write_started.wait()
      scope.cancel()
      io.release_write.set()
      await finished.wait()

    assert io.writes == []

  async def test_cancelled_postcommit_luminescence_drains_sequence(self) -> None:
    io = RecordingHID([], responses=_luminescence_chunks(), blocked_writes={2})
    backend = cast(
      ByonoyLuminescence96AutomateBackend,
      _with_io(ByonoyLuminescence96AutomateBackend(), io),
    )
    scope = anyio.CancelScope()
    finished = anyio.Event()
    returned: list[list[dict]] = []

    async def measure() -> None:
      with scope:
        returned.append(await backend.read_luminescence(cast(Any, None), [], 0))
      finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(measure)
      await io.blocked_write_started.wait()
      scope.cancel()
      await anyio.sleep(0.01)
      finished_before_release = finished.is_set()
      io.release_write.set()
      await finished.wait()

    assert not finished_before_release
    assert [int.from_bytes(write[:2], "little") for write in io.writes] == [
      0x0010,
      0x0200,
      0x0340,
    ]
    assert io.consumed_reads == 8
    assert io.responses == []
    assert returned == []


class TestByonoyMeasurementTimeout(AnyioTestBase):
  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_absorbance_measurement_preserves_120_second_limit(self) -> None:
    io = RecordingHID([], read_delay=1)
    backend = cast(
      ByonoyAbsorbance96AutomateBackend,
      _with_io(ByonoyAbsorbance96AutomateBackend(), io),
    )

    with pytest.raises(TimeoutError, match="Measurement timeout"):
      await backend._run_abs_measurement(660, 0, False)

    assert io.read_calls and all(timeout is None for _, timeout in io.read_calls)

  async def test_luminescence_measurement_preserves_120_second_limit(self) -> None:
    io = RecordingHID([], read_delay=1)
    backend = cast(
      ByonoyLuminescence96AutomateBackend,
      _with_io(ByonoyLuminescence96AutomateBackend(), io),
    )

    with pytest.raises(TimeoutError, match="Reading luminescence data timed out after 2 minutes"):
      await backend.read_luminescence(cast(Any, None), [], 0)

    assert io.read_calls and all(timeout is None for _, timeout in io.read_calls)
