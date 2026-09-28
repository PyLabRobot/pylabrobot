import os
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence, cast
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.command_line import CommandLineResult, CommandLineTransport
from pylabrobot.io.serial import Serial
from pylabrobot.micronic import MicronicError, MicronicRD235, SaneScanner, TwainScanner
from pylabrobot.micronic.code_reader.driver import (
  DecodeResult,
  RackScanResult,
  cluster_axis,
  decode_image,
  find_duplicate_ids,
  is_tube_id,
  iter_positions,
  rack_position,
)
from pylabrobot.micronic.code_reader.scanner import Scanner
from pylabrobot.resources.tube_rack import TubeRack
from pylabrobot.testing.concurrency import AnyioTestBase


def _rack(num_items_x: int = 12, num_items_y: int = 8) -> TubeRack:
  rack = MagicMock(spec=TubeRack)
  rack.num_items_x = num_items_x
  rack.num_items_y = num_items_y
  return rack


class RecordingScanner(Scanner):
  """Record scanner lifecycle and acquisition without invoking an executable."""

  image_extension = "bmp"

  def __init__(
    self,
    events: Optional[list[str]] = None,
    *,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    acquire_error: Optional[BaseException] = None,
    acquire_started: Optional[anyio.Event] = None,
  ) -> None:
    super().__init__()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.acquire_error = acquire_error
    self.acquire_started = acquire_started
    self.acquire_calls: list[tuple[Path, float]] = []

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingScanner does not accept lifespan options")
    self.events.append("scanner.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("scanner.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def acquire(self, output_path: Path, timeout: float) -> dict[str, object]:
    await anyio.lowlevel.checkpoint()
    self.acquire_calls.append((output_path, timeout))
    output_path.touch()
    if self.acquire_started is not None:
      self.acquire_started.set()
      await anyio.sleep_forever()
    if self.acquire_error is not None:
      raise self.acquire_error
    return {"source": "test"}


ReadEffect = bytes | Callable[[], Awaitable[bytes]]


class RecordingSerial(HasLifespan):
  """Record rack-reader lifecycle and protocol calls without opening a port."""

  def __init__(
    self,
    events: Optional[list[str]] = None,
    *,
    reads: Sequence[ReadEffect] = (),
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    entry_started: Optional[anyio.Event] = None,
    reset_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.events = events if events is not None else []
    self.reads = deque(reads)
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.entry_started = entry_started
    self.reset_error = reset_error

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingSerial does not accept lifespan options")
    self.events.append("serial.enter")
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)
    if self.entry_started is not None:
      self.entry_started.set()
      await anyio.sleep_forever()

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("serial.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def reset_input_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    if self.reset_error is not None:
      raise self.reset_error
    self.events.append("serial.reset")

  async def write(self, data: bytes) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append(f"serial.write:{data!r}")

  async def read(self, num_bytes: int = 1) -> bytes:
    assert num_bytes == 1
    await anyio.lowlevel.checkpoint()
    if not self.reads:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")
    effect = self.reads.popleft()
    value = await effect() if callable(effect) else effect
    self.events.append(f"serial.read:{value!r}")
    return value


def _make_reader(
  *,
  events: Optional[list[str]] = None,
  scanner: Optional[RecordingScanner] = None,
  serial: Optional[RecordingSerial] = None,
  **kwargs: Any,
) -> tuple[MicronicRD235, RecordingScanner, RecordingSerial]:
  """Construct a reader with native hardware-free child resources."""
  shared_events = events if events is not None else []
  scanner = scanner or RecordingScanner(shared_events)
  serial = serial or RecordingSerial(shared_events)
  reader = MicronicRD235(scanner=scanner, serial_port="FAKE", **kwargs)
  reader.io = cast(Serial, serial)
  return reader, scanner, serial


async def _enter_and_wait(resource: HasLifespan) -> None:
  """Enter a resource and wait until its surrounding task is cancelled."""
  async with resource:
    await anyio.sleep_forever()


class RecordingCommandLine(HasLifespan):
  """Record scanner command-line lifecycle and run calls without starting a process."""

  def __init__(
    self,
    executable: str,
    *,
    resolved_executable: Optional[str] = None,
    result: CommandLineResult = CommandLineResult(0, "", ""),
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    run_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.executable = executable
    self.resolved_executable = resolved_executable
    self.result = result
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.run_error = run_error
    self.events: list[str] = []
    self.runs: list[tuple[list[str], float]] = []

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingCommandLine does not accept lifespan options")
    self.events.append("command_line.enter")
    if self.resolved_executable is not None:
      self.executable = self.resolved_executable
    if self.entry_error is not None:
      raise self.entry_error
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("command_line.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def run(self, arguments: Sequence[str], timeout: float) -> CommandLineResult:
    await anyio.lowlevel.checkpoint()
    self.runs.append((list(arguments), timeout))
    if self.run_error is not None:
      raise self.run_error
    return self.result


def _recording_command_line(
  executable: str,
  **kwargs: Any,
) -> tuple[CommandLineTransport, RecordingCommandLine]:
  """Build a scanner-compatible native command-line transport double."""
  recording = RecordingCommandLine(executable, **kwargs)
  return cast(CommandLineTransport, recording), recording


class DecodeWorkerGate:
  """Block a synchronous decoder so tests can inspect cancellation ownership."""

  def __init__(self, *, error: Optional[BaseException] = None) -> None:
    self.started = threading.Event()
    self.release = threading.Event()
    self.finished = threading.Event()
    self.error = error
    self.worker_ident: Optional[int] = None
    self.paths: list[Path] = []

  def __call__(self, image_path: Path) -> tuple[dict[str, DecodeResult], dict[str, object]]:
    self.worker_ident = threading.get_ident()
    self.paths.append(image_path)
    self.started.set()
    try:
      if not self.release.wait(timeout=5):
        raise RuntimeError("decode worker gate timed out")
      if self.error is not None:
        raise self.error
      return (
        {"A1": DecodeResult(tube_id="1111111111", method="test")},
        {"decodedWells": 1},
      )
    finally:
      self.finished.set()


async def _wait_for_thread_event(event: threading.Event) -> None:
  """Wait for a worker-thread event without blocking the async runner."""
  with anyio.fail_after(1):
    while not event.is_set():
      await anyio.sleep(0)


class MetadataRecordingReader(MicronicRD235):
  """Record which thread publishes decode metadata."""

  metadata_assignment_ident: Optional[int]

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    self.metadata_assignment_ident = None
    super().__init__(*args, **kwargs)

  @property
  def last_decode_metadata(self) -> dict[str, object]:
    return self._last_decode_metadata

  @last_decode_metadata.setter
  def last_decode_metadata(self, value: dict[str, object]) -> None:
    self._last_decode_metadata = value
    self.metadata_assignment_ident = threading.get_ident()


class RacingScanGate:
  """Force check-then-await scan admission to race deterministically."""

  def __init__(self) -> None:
    self.held = False
    self.waiters = 0
    self.both_waiting = anyio.Event()

  @property
  def value(self) -> int:
    return 0 if self.held else 1

  async def acquire(self) -> None:
    self.waiters += 1
    if self.waiters == 2:
      self.both_waiting.set()
    await self.both_waiting.wait()
    if self.held:
      await anyio.sleep_forever()
    self.held = True

  def acquire_nowait(self) -> None:
    if self.held:
      raise anyio.WouldBlock
    self.held = True

  def release(self) -> None:
    self.held = False


class TestScannerClasses(AnyioTestBase):
  """Tests for scanner command construction and error handling."""

  def test_scanner_classes_inherit_managed_methods(self) -> None:
    for scanner_class in (TwainScanner, SaneScanner):
      assert scanner_class.__aenter__ is HasLifespan.__aenter__
      assert scanner_class.__aexit__ is HasLifespan.__aexit__
      assert scanner_class.setup is HasLifespan.setup
      assert scanner_class.stop is HasLifespan.stop

  async def test_sane_lifespan_owns_command_line(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      output_path = Path(image_dir) / "rack.tiff"
      output_path.touch()
      command_line, recording = _recording_command_line(
        "scanimage", resolved_executable="/usr/bin/scanimage"
      )
      constructed_with: list[dict[str, str]] = []

      def construct_command_line(**kwargs: str) -> CommandLineTransport:
        constructed_with.append(kwargs)
        return command_line

      with patch(
        "pylabrobot.micronic.code_reader.scanner.CommandLineTransport",
        new=construct_command_line,
      ):
        scanner = SaneScanner(
          sane_device="avision:libusb:001:004",
          scanimage_path="scanimage",
        )
      async with scanner:
        assert scanner.scanimage_path == "/usr/bin/scanimage"
        recording.events.append("body")
        metadata = await scanner.acquire(output_path, timeout=1.0)

      assert scanner.command_line is command_line
      assert constructed_with == [
        {
          "human_readable_device_name": "Micronic SANE rack scanner",
          "executable": "scanimage",
        }
      ]
      assert metadata["source"] == "sane"
      assert scanner.image_extension == "tiff"
      assert recording.events == ["command_line.enter", "body", "command_line.exit"]
      assert recording.runs == [
        (
          [
            "--device-name",
            "avision:libusb:001:004",
            "--format=tiff",
            "--output-file",
            str(output_path),
          ],
          16.0,
        )
      ]

  def test_sane_scanner_raises_when_scanimage_missing(self):
    with patch("pylabrobot.micronic.code_reader.scanner.shutil.which", return_value=None):
      with pytest.raises(MicronicError):
        SaneScanner()

  def test_twain_scanner_resolves_path_from_env(self):
    with (
      patch.dict(os.environ, {"MICRONIC_TWAIN_SCANNER_PATH": "/opt/twain_scan"}, clear=False),
      patch("pylabrobot.micronic.code_reader.scanner.shutil.which", return_value=None),
    ):
      scanner = TwainScanner()
    assert scanner.twain_scanner_path == "/opt/twain_scan"

  def test_twain_scanner_raises_when_helper_missing(self):
    with (
      patch.dict(os.environ, {}, clear=True),
      patch("pylabrobot.micronic.code_reader.scanner.shutil.which", return_value=None),
    ):
      with pytest.raises(MicronicError):
        TwainScanner()

  async def test_twain_lifespan_owns_command_line(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      output_path = Path(image_dir) / "rack.bmp"
      output_path.touch()
      command_line, recording = _recording_command_line(
        "twain_scan", resolved_executable="/opt/twain_scan"
      )
      scanner = TwainScanner(
        twain_source="AVA6PlusG",
        command_line=command_line,
      )
      async with scanner:
        assert scanner.twain_scanner_path == "/opt/twain_scan"
        recording.events.append("body")
        await scanner.acquire(output_path, timeout=1.25)

      assert scanner.command_line is command_line
      assert recording.events == ["command_line.enter", "body", "command_line.exit"]
      assert recording.runs == [([str(output_path), "AVA6PlusG", "1250"], 16.25)]

  async def test_command_line_entry_failure_is_translated(self) -> None:
    command_line, recording = _recording_command_line(
      "twain_scan",
      resolved_executable="/opt/twain_scan",
      entry_error=FileNotFoundError("missing helper"),
    )
    scanner = TwainScanner(command_line=command_line)

    with pytest.raises(MicronicError, match="missing helper"):
      async with scanner:
        pytest.fail("failed scanner entry reached the body")

    assert scanner.twain_scanner_path == "twain_scan"
    assert recording.events == ["command_line.enter"]

  async def test_cancelled_scanner_body_closes_command_line(self) -> None:
    command_line, recording = _recording_command_line("/opt/twain_scan")
    scanner = TwainScanner(command_line=command_line)
    body_entered = anyio.Event()
    body_exited = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def use_scanner() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with scanner:
            body_entered.set()
            await anyio.sleep_forever()
      finally:
        body_exited.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_scanner)
      await body_entered.wait()
      scopes[0].cancel()
      await body_exited.wait()
      task_group.cancel_scope.cancel()

    assert recording.events == ["command_line.enter", "command_line.exit"]

  async def test_command_line_close_failure_propagates(self) -> None:
    command_line, recording = _recording_command_line(
      "/opt/twain_scan", exit_error=RuntimeError("close failed")
    )
    scanner = TwainScanner(command_line=command_line)

    with pytest.raises(RuntimeError, match="close failed"):
      async with scanner:
        pass

    assert recording.events == ["command_line.enter", "command_line.exit"]

  async def test_scanner_supports_sequential_reuse(self) -> None:
    command_line, recording = _recording_command_line("/opt/twain_scan")
    scanner = TwainScanner(command_line=command_line)

    async with scanner:
      recording.events.append("first")
    async with scanner:
      recording.events.append("second")

    assert recording.events == [
      "command_line.enter",
      "first",
      "command_line.exit",
      "command_line.enter",
      "second",
      "command_line.exit",
    ]

  @pytest.mark.parametrize("scanner_class", [TwainScanner, SaneScanner])
  async def test_scanner_rejects_lifespan_options(self, scanner_class: type) -> None:
    command_line, recording = _recording_command_line("scanner-helper")
    scanner = scanner_class(command_line=command_line)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with scanner.lifespan(unexpected=True):
        pytest.fail("invalid scanner entry reached the body")

    assert recording.events == []

  async def test_scanner_raises_on_helper_failure(self):
    command_line, _ = _recording_command_line(
      "/opt/twain_scan",
      result=CommandLineResult(2, "", "scanner fault"),
    )
    scanner = TwainScanner(command_line=command_line)

    with pytest.raises(MicronicError, match="scanner fault"):
      await scanner.acquire(Path("rack.bmp"), timeout=1.0)

  async def test_scanner_raises_when_helper_creates_no_image(self):
    command_line, _ = _recording_command_line("/opt/twain_scan")
    scanner = TwainScanner(command_line=command_line)

    with tempfile.TemporaryDirectory() as image_dir:
      with pytest.raises(MicronicError, match="did not create image"):
        await scanner.acquire(Path(image_dir) / "rack.bmp", timeout=1.0)

  async def test_scanner_wraps_helper_timeout(self):
    command_line, _ = _recording_command_line("/opt/twain_scan", run_error=TimeoutError())
    scanner = TwainScanner(command_line=command_line)

    with pytest.raises(MicronicError, match="timed out"):
      await scanner.acquire(Path("rack.bmp"), timeout=1.0)

  async def test_scanner_wraps_missing_helper(self):
    command_line, _ = _recording_command_line("/missing/twain_scan", run_error=FileNotFoundError())
    scanner = TwainScanner(command_line=command_line)

    with pytest.raises(MicronicError, match="was not found"):
      await scanner.acquire(Path("rack.bmp"), timeout=1.0)


class TestMicronicRD235(AnyioTestBase):
  """Tests for the managed Micronic code-reader lifecycle and operations."""

  def test_reader_inherits_managed_methods(self) -> None:
    assert MicronicRD235.__aenter__ is HasLifespan.__aenter__
    assert MicronicRD235.__aexit__ is HasLifespan.__aexit__
    assert MicronicRD235.setup is HasLifespan.setup
    assert MicronicRD235.stop is HasLifespan.stop

  def test_rejects_non_positive_device_timeouts(self) -> None:
    with pytest.raises(ValueError, match="scanner_timeout"):
      MicronicRD235(scanner=RecordingScanner(), serial_port="FAKE", scanner_timeout=0)
    with pytest.raises(ValueError, match="serial_timeout"):
      MicronicRD235(scanner=RecordingScanner(), serial_port="FAKE", serial_timeout=0)

  async def test_reader_enters_scanner_then_serial(self) -> None:
    events: list[str] = []
    reader, _, _ = _make_reader(events=events)

    async with reader:
      events.append("body")

    assert events[:3] == ["scanner.enter", "serial.enter", "body"]

  async def test_reader_closes_serial_then_scanner(self) -> None:
    events: list[str] = []
    reader, _, _ = _make_reader(events=events)

    async with reader:
      pass

    assert events[-2:] == ["serial.exit", "scanner.exit"]

  async def test_scanner_entry_failure_does_not_enter_serial(self) -> None:
    events: list[str] = []
    scanner = RecordingScanner(events, entry_error=RuntimeError("scanner failed"))
    reader, _, _ = _make_reader(events=events, scanner=scanner)

    with pytest.raises(RuntimeError, match="scanner failed"):
      async with reader:
        pytest.fail("failed entry reached body")

    assert events == ["scanner.enter"]

  async def test_serial_entry_failure_unwinds_scanner(self) -> None:
    events: list[str] = []
    serial = RecordingSerial(events, entry_error=RuntimeError("serial failed"))
    reader, _, _ = _make_reader(events=events, serial=serial)

    with pytest.raises(RuntimeError, match="serial failed"):
      async with reader:
        pytest.fail("failed entry reached body")

    assert events == ["scanner.enter", "serial.enter", "scanner.exit"]

  async def test_cancelled_entry_unwinds_children(self) -> None:
    events: list[str] = []
    entry_started = anyio.Event()
    serial = RecordingSerial(events, entry_started=entry_started)
    reader, _, _ = _make_reader(events=events, serial=serial)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(_enter_and_wait, reader)
      await entry_started.wait()
      task_group.cancel_scope.cancel()

    assert events == ["scanner.enter", "serial.enter", "serial.exit", "scanner.exit"]
    assert not reader.is_active

  async def test_cancelled_body_closes_children(self) -> None:
    events: list[str] = []
    body_started = anyio.Event()
    reader, _, _ = _make_reader(events=events)

    async def use_reader() -> None:
      try:
        async with reader:
          body_started.set()
          await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class():
        events.append("cancelled")
        raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_reader)
      await body_started.wait()
      task_group.cancel_scope.cancel()

    assert events[-3:] == ["serial.exit", "scanner.exit", "cancelled"]

  async def test_cleanup_failure_does_not_skip_scanner(self) -> None:
    events: list[str] = []
    serial = RecordingSerial(events, exit_error=RuntimeError("serial close failed"))
    reader, _, _ = _make_reader(events=events, serial=serial)

    with pytest.raises(RuntimeError, match="serial close failed"):
      async with reader:
        pass

    assert events[-2:] == ["serial.exit", "scanner.exit"]

  async def test_reader_supports_sequential_reuse(self) -> None:
    events: list[str] = []
    reader, _, _ = _make_reader(events=events)

    async with reader:
      pass
    async with reader:
      pass

    assert events == [
      "scanner.enter",
      "serial.enter",
      "serial.exit",
      "scanner.exit",
      "scanner.enter",
      "serial.enter",
      "serial.exit",
      "scanner.exit",
    ]

  async def test_reader_rejects_lifespan_options(self) -> None:
    events: list[str] = []
    reader, _, _ = _make_reader(events=events)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with reader.lifespan(unexpected=True):
        pytest.fail("invalid entry reached body")

    assert events == []

  async def test_reader_exit_waits_for_active_scan(self) -> None:
    events: list[str] = []
    read_started = anyio.Event()
    release_read = anyio.Event()
    owner_finished = anyio.Event()

    async def blocked_read() -> bytes:
      read_started.set()
      await release_read.wait()
      return b"\r"

    serial = RecordingSerial(events, reads=[blocked_read])
    reader, _, _ = _make_reader(events=events, serial=serial)

    async def own_reader(task_group: anyio.abc.TaskGroup) -> None:
      async with reader:
        task_group.start_soon(reader.scan_rack_id)
        await read_started.wait()
      owner_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(own_reader, task_group)
      await read_started.wait()
      await anyio.sleep(0)
      assert not owner_finished.is_set()
      assert "serial.exit" not in events
      release_read.set()
      await owner_finished.wait()

    assert events[-2:] == ["serial.exit", "scanner.exit"]

  async def test_operations_reject_inactive_reader(self) -> None:
    reader, _, serial = _make_reader()

    with pytest.raises(RuntimeError, match="active lifespan"):
      await reader.scan_rack_id()
    with pytest.raises(RuntimeError, match="active lifespan"):
      await reader.scan_rack(_rack())

    assert serial.events == []

  async def test_rack_id_transactions_are_serialized(self) -> None:
    events: list[str] = []
    first_read_started = anyio.Event()
    release_first_read = anyio.Event()
    acquire_started = anyio.Event()
    rack_id_finished = anyio.Event()
    results: list[str] = []

    async def blocked_first_byte() -> bytes:
      first_read_started.set()
      await release_first_read.wait()
      return b"1"

    reads: list[ReadEffect] = [blocked_first_byte, *([b"1"] * 5), b"\r", *([b"2"] * 6), b"\r"]
    serial = RecordingSerial(events, reads=reads)
    scanner = RecordingScanner(events, acquire_started=acquire_started)
    reader, _, _ = _make_reader(events=events, scanner=scanner, serial=serial)

    async def read_id() -> None:
      results.append(await reader.scan_rack_id())
      rack_id_finished.set()

    async with reader:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(reader.scan_rack, _rack())
        await first_read_started.wait()
        task_group.start_soon(read_id)
        await anyio.sleep(0)
        assert events.count("serial.reset") == 1
        release_first_read.set()
        await acquire_started.wait()
        with anyio.fail_after(0.1):
          await rack_id_finished.wait()
        task_group.cancel_scope.cancel()

    assert results == ["222222"]

  async def test_acquire_image_runs_scanner_and_tracks_metadata(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      reader, scanner, _ = _make_reader(image_dir=image_dir, scanner_timeout=1.25, keep_images=True)
      image_path = await reader._acquire_image()

      assert reader.last_image_path == image_path
      assert reader.last_scan_metadata == {"source": "test"}
      assert scanner.acquire_calls == [(image_path, 1.25)]
      assert image_path.name.startswith("micronic_")
      assert image_path.suffix == ".bmp"

  async def test_acquire_image_removes_partial_image_after_failure(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      scanner = RecordingScanner(acquire_error=MicronicError("acquisition failed"))
      reader, _, _ = _make_reader(scanner=scanner, image_dir=image_dir)

      with pytest.raises(MicronicError, match="acquisition failed"):
        await reader._acquire_image()

      assert list(Path(image_dir).iterdir()) == []

  async def test_scan_rack_id_uses_plr_serial(self) -> None:
    instances: list[dict[str, Any]] = []
    recording = RecordingSerial(reads=[*(bytes([value]) for value in b"9500017722"), b"\r"])

    def construct_serial(**kwargs: Any) -> Serial:
      instances.append(kwargs)
      return cast(Serial, recording)

    with patch("pylabrobot.micronic.code_reader.driver.Serial", new=construct_serial):
      reader = MicronicRD235(scanner=RecordingScanner(), serial_port="/dev/ttyUSB0")

    async with reader:
      rack_id = await reader.scan_rack_id(timeout=1.0)

    assert rack_id == "9500017722"
    assert instances[0]["port"] == "/dev/ttyUSB0"
    assert instances[0]["bytesize"] == 7
    assert instances[0]["parity"] == "E"
    assert "serial.reset" in recording.events
    assert "serial.write:b'<t>\\r\\n'" in recording.events

  async def test_rejects_mismatched_rack_shape(self) -> None:
    reader, _, _ = _make_reader()
    async with reader:
      with pytest.raises(MicronicError):
        await reader.scan_rack(_rack(num_items_x=6, num_items_y=4), timeout=1.0)

  async def test_rejects_concurrent_scan(self) -> None:
    reader, _, _ = _make_reader()
    async with reader:
      await reader._scan_lock.acquire()
      try:
        with pytest.raises(MicronicError):
          await reader.scan_rack(_rack(), timeout=1.0)
      finally:
        reader._scan_lock.release()

  async def test_scan_rack_times_out(self) -> None:
    reader, _, _ = _make_reader()

    async def slow(rack: TubeRack) -> RackScanResult:
      del rack
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    with patch.object(reader, "_scan_rack", slow):
      async with reader:
        with pytest.raises(TimeoutError):
          await reader.scan_rack(rack=_rack(), timeout=0.01)

  async def test_scan_rack_propagates_micronic_error(self) -> None:
    reader, _, _ = _make_reader()
    async with reader:
      with pytest.raises(MicronicError):
        await reader.scan_rack(_rack(num_items_x=6, num_items_y=4), timeout=1.0)

  async def test_scan_rack_id_reads_barcode(self) -> None:
    reader, _, _ = _make_reader()
    with patch.object(
      reader, "_read_barcode", AsyncMock(return_value="9500017722")
    ) as read_barcode:
      async with reader:
        rack_id = await reader.scan_rack_id(timeout=5.0)

    assert rack_id == "9500017722"
    read_barcode.assert_awaited_once_with()

  async def test_scan_rack_id_returns_noread_for_unrecognized_response(self) -> None:
    serial = RecordingSerial(reads=[b"?", b"\r"])
    reader, _, _ = _make_reader(serial=serial)
    async with reader:
      rack_id = await reader.scan_rack_id(timeout=1.0)

    assert rack_id == "NOREAD"

  async def test_scan_rack_id_returns_noread_when_serial_stays_quiet(self) -> None:
    """An idle serial worker is bounded by the caller's rack-ID deadline."""
    reader, _, _ = _make_reader(serial_timeout=0.01)
    async with reader:
      with anyio.fail_after(1):
        assert await reader.scan_rack_id(timeout=1.0) == "NOREAD"

  async def test_scan_rack_id_wraps_serial_error(self) -> None:
    serial = RecordingSerial(reset_error=OSError("port disconnected"))
    reader, _, _ = _make_reader(serial=serial)
    async with reader:
      with pytest.raises(MicronicError, match="port disconnected"):
        await reader.scan_rack_id(timeout=1.0)

  def test_decode_rack_returns_noread_for_missing_wells(self) -> None:
    reader, _, _ = _make_reader()
    decoded = {"A1": DecodeResult(tube_id="1111111111", method="test")}

    result = reader._build_rack_scan_result(decoded, "9500017722")

    assert len(result.entries) == 96
    assert result.entries[0].status == "OK"
    assert result.entries[1].status == "NOREAD"
    assert result.entries[1].tube_id is None
    assert result.entries[1].barcode is None

  def test_decode_rack_represents_missing_rack_id(self) -> None:
    reader, _, _ = _make_reader()
    decoded = {"A1": DecodeResult(tube_id="1111111111", method="test")}

    result = reader._build_rack_scan_result(decoded, "NOREAD")

    assert result.rack_barcode is None
    assert result.entries[0].status == "OK"
    assert result.entries[1].status == "NOREAD"


class TestMicronicRD235DecodeWorker(AnyioTestBase):
  """Tests for async-task ownership of non-abandoning decode workers."""

  async def test_scan_rack_populates_result(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      reader, scanner, _ = _make_reader(image_dir=image_dir, keep_images=True)
      decoded = {
        "A1": DecodeResult(tube_id="1111111111", method="test"),
        "A2": DecodeResult(tube_id="2222222222", method="test"),
      }
      with (
        patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 2}),
        ),
      ):
        async with reader:
          result = await reader.scan_rack(_rack(), timeout=1.0)

      assert result.rack_id == "9500017722"
      assert result.rack_barcode is not None
      assert result.rack_barcode.data == "9500017722"
      assert result.rack_barcode.symbology == "Code 128 (Subset B and C)"
      assert result.entries[0].position == "A1"
      assert result.entries[0].tube_id == "1111111111"
      assert result.entries[0].barcode is not None
      assert result.entries[0].barcode.data == "1111111111"
      assert result.entries[0].barcode.symbology == "DataMatrix"
      assert result.entries[1].tube_id == "2222222222"
      assert reader.last_scan_metadata == {"source": "test"}
      assert reader.last_decode_metadata == {"decodedWells": 2}
      assert len(scanner.acquire_calls) == 1

  async def test_reader_can_scan_twice(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      reader, scanner, _ = _make_reader(image_dir=image_dir, keep_images=True)
      decoded = {"A1": DecodeResult(tube_id="1111111111", method="test")}
      with (
        patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
        patch(
          "pylabrobot.micronic.code_reader.driver.decode_image",
          return_value=(decoded, {"decodedWells": 1}),
        ),
      ):
        async with reader:
          first = await reader.scan_rack(_rack(), timeout=1.0)
          second = await reader.scan_rack(_rack(), timeout=1.0)

      assert first.rack_id == "9500017722"
      assert second.rack_id == "9500017722"
      assert len(scanner.acquire_calls) == 2

  async def test_decode_runs_in_non_abandoning_worker(self) -> None:
    reader, _, _ = _make_reader()
    gate = DecodeWorkerGate()
    async_ident = threading.get_ident()

    async def release_worker() -> None:
      await _wait_for_thread_event(gate.started)
      assert not gate.finished.is_set()
      gate.release.set()

    with (
      patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
      patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
    ):
      async with reader:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(release_worker)
          await reader.scan_rack(_rack(), timeout=1)

    assert gate.finished.is_set()
    assert gate.worker_ident != async_ident

  async def test_cancellation_waits_for_decode_worker(self) -> None:
    events: list[str] = []
    reader, _, _ = _make_reader(events=events)
    gate = DecodeWorkerGate()
    scopes: list[anyio.CancelScope] = []
    outcomes: list[str] = []
    owner_finished = anyio.Event()

    async def own_reader() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with reader:
          await reader.scan_rack(_rack(), timeout=1)
      outcomes.append("cancelled" if scope.cancelled_caught else "returned")
      owner_finished.set()

    with (
      patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
      patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(own_reader)
        await _wait_for_thread_event(gate.started)
        scopes[0].cancel()
        await anyio.sleep(0)
        assert not owner_finished.is_set()
        assert "serial.exit" not in events
        gate.release.set()
        await owner_finished.wait()

    assert gate.finished.is_set()
    assert outcomes == ["cancelled"]
    assert events[-2:] == ["serial.exit", "scanner.exit"]

  async def test_timeout_waits_for_decode_worker(self) -> None:
    events: list[str] = []
    reader, _, _ = _make_reader(events=events)
    gate = DecodeWorkerGate()

    async def release_after_timeout() -> None:
      await _wait_for_thread_event(gate.started)
      await anyio.sleep(0.03)
      assert "serial.exit" not in events
      gate.release.set()

    with (
      patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
      patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
    ):
      async with reader:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(release_after_timeout)
          with pytest.raises(TimeoutError):
            await reader.scan_rack(_rack(), timeout=0.01)

    assert gate.finished.is_set()
    assert events[-2:] == ["serial.exit", "scanner.exit"]

  async def test_cancellation_takes_precedence_over_decode_failure(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      reader, _, _ = _make_reader(image_dir=image_dir)
      gate = DecodeWorkerGate(error=MicronicError("decode failed"))
      scopes: list[anyio.CancelScope] = []
      outcomes: list[str] = []
      finished = anyio.Event()

      async def scan() -> None:
        try:
          with anyio.CancelScope() as scope:
            scopes.append(scope)
            await reader.scan_rack(_rack(), timeout=1)
          outcomes.append("cancelled" if scope.cancelled_caught else "returned")
        except BaseException as exc:
          outcomes.append(type(exc).__name__)
        finally:
          assert reader._scan_lock.value == 1
          assert reader._active_scans == 0
          assert list(Path(image_dir).iterdir()) == []
          finished.set()

      with (
        patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
        patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
      ):
        async with reader:
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(scan)
            await _wait_for_thread_event(gate.started)
            scopes[0].cancel()
            gate.release.set()
            await finished.wait()

      assert outcomes == ["cancelled"]

  async def test_timeout_takes_precedence_over_decode_failure(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      reader, _, _ = _make_reader(image_dir=image_dir)
      gate = DecodeWorkerGate(error=MicronicError("decode failed"))

      async def release_after_timeout() -> None:
        await _wait_for_thread_event(gate.started)
        await anyio.sleep(0.03)
        gate.release.set()

      with (
        patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
        patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
      ):
        async with reader:
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(release_after_timeout)
            with pytest.raises(TimeoutError):
              await reader.scan_rack(_rack(), timeout=0.01)
          assert reader._scan_lock.value == 1
          assert reader._active_scans == 0

      assert list(Path(image_dir).iterdir()) == []

  async def test_cancelled_decode_releases_image_before_propagating(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      reader, _, _ = _make_reader(image_dir=image_dir)
      gate = DecodeWorkerGate()
      scopes: list[anyio.CancelScope] = []
      observations: list[list[Path]] = []
      outcomes: list[str] = []
      cancelled = anyio.Event()

      async def scan() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await reader.scan_rack(_rack(), timeout=1)
        observations.append(list(Path(image_dir).iterdir()))
        outcomes.append("cancelled" if scope.cancelled_caught else "returned")
        cancelled.set()

      with (
        patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
        patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
      ):
        async with reader:
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(scan)
            await _wait_for_thread_event(gate.started)
            scopes[0].cancel()
            gate.release.set()
            await cancelled.wait()

      assert observations == [[]]
      assert outcomes == ["cancelled"]

  async def test_cancelled_decode_releases_scan_lock_before_propagating(self) -> None:
    reader, _, _ = _make_reader()
    gate = DecodeWorkerGate()
    scopes: list[anyio.CancelScope] = []
    lock_values: list[int] = []
    outcomes: list[str] = []
    cancelled = anyio.Event()

    async def scan() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await reader.scan_rack(_rack(), timeout=1)
      lock_values.append(reader._scan_lock.value)
      outcomes.append("cancelled" if scope.cancelled_caught else "returned")
      cancelled.set()

    with (
      patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
      patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
    ):
      async with reader:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(scan)
          await _wait_for_thread_event(gate.started)
          scopes[0].cancel()
          gate.release.set()
          await cancelled.wait()

    assert lock_values == [1]
    assert outcomes == ["cancelled"]

  async def test_decode_failure_releases_image_and_lock(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      reader, _, _ = _make_reader(image_dir=image_dir)
      gate = DecodeWorkerGate(error=MicronicError("decode failed"))
      gate.release.set()

      with (
        patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
        patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
      ):
        async with reader:
          with pytest.raises(MicronicError, match="decode failed"):
            await reader.scan_rack(_rack(), timeout=1)
          assert reader._scan_lock.value == 1

      assert list(Path(image_dir).iterdir()) == []

  async def test_decode_metadata_is_committed_on_async_task(self) -> None:
    scanner = RecordingScanner()
    serial = RecordingSerial()
    reader = MetadataRecordingReader(scanner=scanner, serial_port="FAKE")
    reader.io = cast(Serial, serial)
    gate = DecodeWorkerGate()
    gate.release.set()
    async_ident = threading.get_ident()

    with (
      patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
      patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
    ):
      async with reader:
        await reader.scan_rack(_rack(), timeout=1)

    assert gate.worker_ident != async_ident
    assert reader.metadata_assignment_ident == async_ident

  async def test_scan_can_run_after_cancelled_decode(self) -> None:
    reader, _, _ = _make_reader()
    gate = DecodeWorkerGate()
    scopes: list[anyio.CancelScope] = []
    outcomes: list[str] = []
    first_finished = anyio.Event()

    async def first_scan() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await reader.scan_rack(_rack(), timeout=1)
      outcomes.append("cancelled" if scope.cancelled_caught else "returned")
      first_finished.set()

    with (
      patch.object(reader, "_read_barcode", AsyncMock(return_value="9500017722")),
      patch("pylabrobot.micronic.code_reader.driver.decode_image", new=gate),
    ):
      async with reader:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(first_scan)
          await _wait_for_thread_event(gate.started)
          scopes[0].cancel()
          gate.release.set()
          await first_finished.wait()
        result = await reader.scan_rack(_rack(), timeout=1)

    assert outcomes == ["cancelled"]
    assert result.entries[0].tube_id == "1111111111"

  async def test_concurrent_scan_is_rejected_atomically(self) -> None:
    reader, _, _ = _make_reader()
    original_lock = reader._scan_lock
    racing_gate = RacingScanGate()
    reader._scan_lock = cast(Any, racing_gate)
    failures: list[BaseException] = []
    finished = anyio.Event()

    async def scan() -> None:
      try:
        await reader.scan_rack(_rack(), timeout=0.1)
      except BaseException as exc:
        failures.append(exc)
        if isinstance(exc, MicronicError) and "already in progress" in str(exc):
          finished.set()

    with patch.object(reader, "_read_barcode", AsyncMock(return_value="NOREAD")):
      async with reader:
        try:
          async with anyio.create_task_group() as task_group:
            task_group.start_soon(scan)
            task_group.start_soon(scan)
            with anyio.fail_after(0.05):
              await finished.wait()
            task_group.cancel_scope.cancel()
        finally:
          reader._scan_lock = original_lock

    assert sum("already in progress" in str(error) for error in failures) == 1

  async def test_acquisition_cancellation_removes_partial_image(self) -> None:
    with tempfile.TemporaryDirectory() as image_dir:
      acquire_started = anyio.Event()
      scanner = RecordingScanner(acquire_started=acquire_started)
      reader, _, _ = _make_reader(scanner=scanner, image_dir=image_dir)
      scopes: list[anyio.CancelScope] = []
      finished = anyio.Event()

      async def acquire() -> None:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await reader._acquire_image()
        finished.set()

      async with anyio.create_task_group() as task_group:
        task_group.start_soon(acquire)
        await acquire_started.wait()
        assert len(list(Path(image_dir).iterdir())) == 1
        scopes[0].cancel()
        await finished.wait()

      assert list(Path(image_dir).iterdir()) == []


class TestDecodeHelpers(unittest.TestCase):
  """Tests for image-decoding validation and rack-coordinate parsing."""

  def test_tube_id_validation(self):
    self.assertTrue(is_tube_id("0123456789"))
    self.assertFalse(is_tube_id("123456789"))
    self.assertFalse(is_tube_id("12345A7890"))
    self.assertFalse(is_tube_id(1234567890))

  def test_rack_position_maps_scanner_orientation(self):
    self.assertEqual(rack_position(scan_row=0, scan_col=0), "H12")
    self.assertEqual(rack_position(scan_row=11, scan_col=7), "A1")

  def test_iter_positions_is_row_major(self):
    positions = list(iter_positions())
    self.assertEqual(len(positions), 96)
    self.assertEqual(positions[:2], ["A1", "A2"])
    self.assertEqual(positions[-2:], ["H11", "H12"])

  def test_cluster_axis_uses_detected_centers(self):
    self.assertEqual(cluster_axis([0.0, 2.0, 100.0, 102.0], 2, 10.0), [1.0, 101.0])

  def test_cluster_axis_interpolates_missing_centers(self):
    self.assertEqual(cluster_axis([0.0, 50.0, 100.0], 5, 10.0), [0.0, 25.0, 50.0, 75.0, 100.0])

  def test_cluster_axis_rejects_insufficient_data(self):
    with self.assertRaisesRegex(MicronicError, "Could not fit"):
      cluster_axis([1.0, 2.0], 8, 10.0)

  def test_duplicate_ids_are_sorted_and_unique(self):
    decoded = {
      "A1": DecodeResult("2222222222", "test"),
      "A2": DecodeResult("1111111111", "test"),
      "A3": DecodeResult("2222222222", "test"),
      "A4": DecodeResult("1111111111", "test"),
      "A5": DecodeResult("1111111111", "test"),
    }
    self.assertEqual(find_duplicate_ids(decoded), ["1111111111", "2222222222"])

  def test_decode_image_rejects_too_few_full_image_codes(self):
    image = MagicMock()
    image.size = (100, 100)
    image_context = MagicMock()
    image_context.__enter__.return_value.convert.return_value = image
    image_module = MagicMock()
    image_module.open.return_value = image_context
    zxingcpp = MagicMock()
    zxingcpp.read_barcodes.return_value = []

    with patch(
      "pylabrobot.micronic.code_reader.driver.import_decode_dependencies",
      return_value=(MagicMock(), MagicMock(), zxingcpp, image_module, MagicMock()),
    ):
      with self.assertRaisesRegex(MicronicError, "Only 0 DataMatrix"):
        decode_image(Path("rack.bmp"))


if __name__ == "__main__":
  unittest.main()
