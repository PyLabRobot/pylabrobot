"""Flex ASCII framing and serialized command exchange over PyLabRobot HID."""

import asyncio
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Optional

from pylabrobot.io.hid import HID

logger = logging.getLogger(__name__)


class FlexError(Exception):
  """A Flex failure preserving the command and raw instrument error code."""

  def __init__(
    self, message: str, command: Optional[str] = None, code: Optional[int] = None
  ) -> None:
    """Store failure details without borrowing another instrument's error table."""
    super().__init__(message)
    self.command = command
    self.code = code


def validate_timeout(value: Optional[float], name: str, allow_none: bool = False) -> None:
  """Reject invalid deadlines before any command can be transmitted."""
  if value is None:
    if allow_none:
      return
    raise ValueError(f"{name} is required")
  if not math.isfinite(value) or value <= 0:
    raise ValueError(f"{name} must be positive and finite")


def reports(payload: bytes) -> tuple[bytes, ...]:
  """Encode payload bytes into 64-byte reports, excluding the HID report ID."""
  if not payload:
    raise ValueError("Cannot encode an empty payload")
  return tuple(
    bytes([len(payload[offset : offset + 63])]) + payload[offset : offset + 63].ljust(63, b"\x00")
    for offset in range(0, len(payload), 63)
  )


class FlexLines:
  """Reassemble CRLF lines, ignoring all bytes beyond a report's length prefix."""

  def __init__(self) -> None:
    """Start with an empty receive buffer."""
    self._buffer = bytearray()

  def feed(self, report: bytes) -> tuple[str, ...]:
    """Return complete ASCII lines from a bounded HID report."""
    if not report or len(report) > 64 or report[0] > 63 or report[0] > len(report) - 1:
      raise FlexError("Invalid Flex HID report length")
    self._buffer.extend(report[1 : 1 + report[0]])
    lines = []
    while b"\r\n" in self._buffer:
      end = self._buffer.index(b"\r\n")
      raw = bytes(self._buffer[:end])
      del self._buffer[: end + 2]
      if not raw or len(raw) > 4096 or any(byte < 32 or byte > 126 for byte in raw):
        raise FlexError("Invalid ASCII response line")
      lines.append(raw.decode("ascii"))
    if len(self._buffer) > 4096:
      raise FlexError("Unterminated Flex response exceeds 4096 bytes")
    return tuple(lines)


@dataclass(frozen=True)
class FlexMessage:
  """An acknowledgment, data report, completion, or unsolicited termination."""

  kind: Literal["ack", "report", "completion", "interrupted"]
  opcode: str
  text: str
  code: Optional[int] = None


def parse_message(line: str) -> FlexMessage:
  """Parse response structure without inferring undocumented numeric meanings."""
  if line == "QIT":
    return FlexMessage("interrupted", "QIT", line)
  match = re.fullmatch(r"([A-Z]{3}) END (-?[0-9]+)", line)
  if match:
    return FlexMessage("completion", match[1], line, int(match[2]))
  if re.fullmatch(r"[A-Z]{3}", line):
    return FlexMessage("ack", line, line)
  if re.fullmatch(r"[A-Z]{3} [ -~]+", line):
    return FlexMessage("report", line[:3], line)
  raise FlexError(f"Malformed Flex response: {line!r}")


@dataclass(frozen=True)
class FlexTranscriptEntry:
  """Monotonic session transcript; feature report payloads are hexadecimal."""

  timestamp: float
  direction: Literal["out", "in", "feature"]
  text: str


@dataclass
class _Pending:
  """One command's independent acknowledgment and terminal state."""

  opcode: str
  ack: asyncio.Event = field(default_factory=asyncio.Event)
  done: asyncio.Event = field(default_factory=asyncio.Event)
  data: list[str] = field(default_factory=list)


class FlexConnection:
  """Keep one command in flight while continuously consuming instrument reports.

  Timeouts/cancellation invalidate the session because replies have no unique
  transaction IDs. The reader stays available for explicit abort; no motion is
  resent, and a fresh setup requires stop first.
  """

  def __init__(
    self,
    io: HID,
    on_message: Callable[[FlexMessage], None],
    on_failure: Callable[[FlexError], None],
    timeout: float,
  ) -> None:
    """Configure transport and observers without connecting to hardware."""
    validate_timeout(timeout, "timeout")
    self.io = io
    self.timeout = timeout
    self._on_message = on_message
    self._on_failure = on_failure
    self._reader: Optional[asyncio.Task[None]] = None
    self._pending: Optional[_Pending] = None
    self._failure: Optional[FlexError] = None
    self._commands = asyncio.Lock()
    self._writes = asyncio.Lock()
    self._abort_lock = asyncio.Lock()
    self._abort_done: Optional[asyncio.Future[int]] = None
    self.transcript: list[FlexTranscriptEntry] = []

  def _record(self, direction: Literal["out", "in", "feature"], text: str) -> None:
    """Keep exact lines with monotonic timestamps and log diagnostic traffic."""
    self.transcript.append(FlexTranscriptEntry(time.monotonic(), direction, text))
    logger.debug("Flex %s: %s", direction, text)

  def check_open(self) -> None:
    """Require an open reader and an unambiguous session."""
    if self._failure is not None:
      raise self._failure
    if self._reader is None or self._reader.done():
      raise FlexError("Call setup() before using the Flex")

  def _fail(self, error: FlexError, *, notify: bool = True) -> None:
    """Invalidate pending work and notify observers once, without auto-recovery."""
    if self._failure is None:
      self._failure = error
      if notify:
        logger.error("Flex session invalidated: %s", error)
        self._on_failure(error)
    if self._pending is not None:
      self._pending.ack.set()
      self._pending.done.set()

  def invalidate(self, error: FlexError) -> None:
    """Invalidate interrupted host-side work without stopping physical execution."""
    self._fail(error)

  def _route(self, message: FlexMessage) -> None:
    """Route exact opcode matches; unsolicited temperature data never finishes a command."""
    self._on_message(message)
    if message.opcode == "ABO" and message.kind == "completion":
      if self._abort_done is not None and not self._abort_done.done():
        self._abort_done.set_result(message.code if message.code is not None else -1)
      self._fail(FlexError("Instrument aborted; reconnect and inspect physical state", "ABO"))
      return
    if message.kind == "interrupted":
      self._fail(FlexError("Instrument reported QIT; run outcome is uncertain", "QIT"))
      return
    if self._failure is not None:
      return
    pending = self._pending
    if message.opcode == "TEM" and message.kind == "report":
      if pending is not None and pending.opcode == "TEM":
        pending.data.append(message.text)
      return
    if pending is None or message.opcode != pending.opcode:
      self._fail(FlexError(f"Unexpected response: {message.text}", message.opcode, message.code))
      return
    if message.kind == "ack":
      if pending.ack.is_set():
        self._fail(FlexError("Duplicate command acknowledgment", pending.opcode))
      pending.ack.set()
    elif message.kind == "completion":
      if message.code != 0:
        self._fail(FlexError(f"Instrument returned {message.text}", pending.opcode, message.code))
      elif not pending.ack.is_set() or pending.done.is_set():
        self._fail(FlexError("Completion without a unique acknowledgment", pending.opcode))
      pending.done.set()
    else:
      pending.data.append(message.text)

  async def _read_loop(self) -> None:
    """Read with short deadlines so the shared HID worker remains available to abort."""
    lines = FlexLines()
    try:
      while True:
        report = await self.io.read(size=64, timeout=100)
        if not report:
          await asyncio.sleep(0)
          continue
        for line in lines.feed(report):
          self._record("in", line)
          self._route(parse_message(line))
    except asyncio.CancelledError:
      raise
    except Exception as error:
      self._fail(FlexError(f"Flex reader failed: {error}"))
      if self._abort_done is not None and not self._abort_done.done():
        self._abort_done.cancel()

  async def setup(self) -> None:
    """Open a fresh reader; repeated healthy setup performs no I/O."""
    if self._reader is not None:
      self.check_open()
      return
    await self.io.setup()
    self._failure = None
    self._pending = None
    self.transcript = []
    self._reader = asyncio.create_task(self._read_loop())

  async def _write(self, payload: bytes, check_session: bool = True) -> None:
    """Write all fragments atomically with respect to other commands and abort."""
    async with self._writes:
      for report in reports(payload):
        if check_session:
          self.check_open()
        written = await self.io.write(report, report_id=b"\x00")
        if written != 65:
          raise FlexError(f"Short HID write: {written} of 65 bytes")

  async def command(self, line: str, completion_timeout: Optional[float]) -> tuple[str, ...]:
    """Wait for acceptance and then execution; only operator waits may be unbounded."""
    validate_timeout(completion_timeout, "completion_timeout", allow_none=True)
    if re.fullmatch(r"[A-Z]{3}(?: [ -~]+)?", line) is None:
      raise ValueError("Expected exactly one printable ASCII command")
    async with self._commands:
      self.check_open()
      pending = _Pending(line[:3])
      self._pending = pending
      try:
        self._record("out", line)
        await asyncio.wait_for(self._write(line.encode("ascii") + b"\r\n"), self.timeout)
        await asyncio.wait_for(pending.ack.wait(), self.timeout)
        self.check_open()
        await asyncio.wait_for(pending.done.wait(), completion_timeout)
        self.check_open()
        return tuple(pending.data)
      except BaseException as error:
        self._fail(FlexError(f"Command interrupted or failed: {line}: {error}", pending.opcode))
        raise
      finally:
        self._pending = None

  async def abort(self) -> None:
    """Send the observed Feature 01 00 then ESC-only sequence and wait for ABO END.

    Abort bypasses the command lock, including an indefinite PAU wait. Terminal
    acknowledgment is not a guarantee of a safe final physical position.
    """
    async with self._abort_lock:
      if self._reader is None or self._reader.done():
        raise FlexError("Abort requires a live connection")
      if self._abort_done is not None:
        raise FlexError("Abort already attempted; reconnect before another attempt")
      terminal = asyncio.get_running_loop().create_future()
      self._abort_done = terminal
      self._fail(FlexError("Abort requested; pending operations are uncertain", "ABO"))
      try:
        async with self._writes:
          self._record("feature", "0100")
          await asyncio.wait_for(
            self.io.send_feature_report(b"\x01\x00", report_id=b"\x00"), self.timeout
          )
        self._record("out", "<ESC>")
        await asyncio.wait_for(self._write(b"\x1b", check_session=False), self.timeout)
        code = await asyncio.wait_for(asyncio.shield(terminal), self.timeout)
        if code != 0:
          raise FlexError(f"Abort failed with code {code}", "ABO", code)
      finally:
        if not terminal.done():
          terminal.cancel()

  async def stop(self) -> None:
    """Close transport and wake waiters; this does not stop physical execution."""
    self._fail(
      FlexError("Flex connection closed; unfinished operations are uncertain"),
      notify=self._pending is not None,
    )
    if self._abort_done is not None and not self._abort_done.done():
      self._abort_done.cancel()
    reader, self._reader = self._reader, None
    try:
      if reader is not None:
        reader.cancel()
        try:
          await reader
        except asyncio.CancelledError:
          pass
    finally:
      await self.io.stop()
      async with self._commands:
        pass
      self._abort_done = None
