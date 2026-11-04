"""Presto XML framing and response routing over PyLabRobot HID."""

import logging
import math
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import anyio
import anyio.abc
import anyio.lowlevel

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.hid import HID

logger = logging.getLogger(__name__)

ERROR_CODES: dict[int, str] = {
  2: "Received an unknown command.",
  3: "Already connected to another port.",
  4: "Head position error.",
  5: "Magnets position error.",
  6: "Turntable position error.",
  7: "Heater unit position error.",
  8: "Lock position error.",
  11: "Invalid command argument.",
  13: "Protocol memory error.",
  14: "Protocol memory is full.",
  15: "No protocols found from the protocols memory.",
  16: "Protocol was not found from the protocols memory.",
  17: "Given tip name was not found from the protocol.",
  18: "Given step name was not found from the given tip of the protocol.",
  19: "A name of a step to start was not given.",
  20: "A name of a tip where to start the step was not given.",
  23: (
    "Protocol name is invalid. Maximum length of the name is 100 bytes e.g. 100 ASCII characters."
  ),
  24: "Invalid protocol file.",
  25: "Protocol is not executable.",
  27: "Protocol is too large and can't be loaded.",
  28: "Instrument is executing, please wait.",
  32: "No protocol is currently running.",
  33: "Data transmit to USB port failed (timed out).",
  34: "Cannot run magnets down without tips.",
  35: "Magnetic head is missing.",
  38: "Plate not detected in processing position.",
  124: "Protocol already running.",
  321: "Execution failed.",
}


class PrestoError(Exception):
  """Instrument or connection failure, with the reported code and command name."""

  def __init__(
    self, message: str, code: Optional[int] = None, command: Optional[str] = None
  ) -> None:
    """Store the instrument error and its command context."""
    super().__init__(message)
    self.code = code
    self.command = command


def response_error(element: ET.Element) -> PrestoError:
  """Preserve the instrument's error text, including unknown error codes."""
  error = element.find("Error")
  code_text = error.get("code") if error is not None else None
  code = int(code_text) if code_text is not None else None
  message = (error.text or "").strip() if error is not None else ""
  description = ERROR_CODES.get(code) if code is not None else None
  if description and description != message:
    message = f"{description} ({message})" if message else description
  return PrestoError(message or f"Command failed (code {code})", code, element.get("name"))


def command_xml(name: str, **attributes: Optional[str]) -> bytes:
  """Encode a command, escaping all user-provided attribute values."""
  element = ET.Element("Cmd", name=name)
  element.attrib.update({key: value for key, value in attributes.items() if value is not None})
  return ET.tostring(element, encoding="unicode").encode("utf-8") + b"\n"


class XMLMessages:
  """Frame adjacent XML documents before parsing, without parser flush delays."""

  def __init__(self) -> None:
    """Start with an empty XML framing buffer."""
    self._buffer = bytearray()
    self._position = 0
    self._depth = 0
    self._special: Optional[bytes] = None

  def feed(self, data: bytes) -> list[ET.Element]:
    """Return complete documents, handling quoted attributes, CDATA and comments."""
    self._buffer.extend(data)
    messages: list[ET.Element] = []
    while self._position < len(self._buffer):
      if self._special is not None:
        end = self._buffer.find(self._special, self._position)
        if end < 0:
          self._position = max(self._position, len(self._buffer) - len(self._special) + 1)
          break
        self._position = end + len(self._special)
        self._special = None
        continue
      start = self._buffer.find(b"<", self._position)
      if start < 0:
        self._position = len(self._buffer)
        break
      tail = self._buffer[start:]
      special = False
      for opening, closing in ((b"<![CDATA[", b"]]>"), (b"<!--", b"-->"), (b"<?", b"?>")):
        if opening.startswith(tail):
          self._position = start
          return messages
        if tail.startswith(opening):
          self._special = closing
          self._position = start + len(opening)
          special = True
          break
      if special:
        continue
      quote = 0
      end = start + 1
      while end < len(self._buffer):
        byte = self._buffer[end]
        if quote:
          if byte == quote:
            quote = 0
        elif byte in (34, 39):
          quote = byte
        elif byte == 62:
          break
        end += 1
      if end == len(self._buffer):
        self._position = start
        break
      token = self._buffer[start : end + 1]
      if token.startswith(b"<!"):
        raise PrestoError("XML declarations are not supported in instrument messages")
      if token.startswith(b"</"):
        self._depth -= 1
      elif not token.endswith(b"/>"):
        self._depth += 1
      self._position = end + 1
      if self._depth < 0:
        raise PrestoError("Unexpected XML closing tag")
      if self._depth == 0:
        element = ET.fromstring(bytes(self._buffer[: self._position]))
        if element.tag not in ("Res", "Evt"):
          raise PrestoError(f"Unexpected XML message: {element.tag}")
        messages.append(element)
        del self._buffer[: self._position]
        self._position = 0
    return messages


@dataclass
class _PendingWaiter:
  """One command or rotation waiter signaled by the reader or session failure."""

  event: anyio.Event
  result: Optional[ET.Element] = None
  error: Optional[PrestoError] = None
  done: bool = False

  def set_result(self, element: ET.Element) -> None:
    """Record a terminal XML element and wake the waiting caller."""
    if not self.done:
      self.done = True
      self.result = element
      self.event.set()

  def set_exception(self, error: PrestoError) -> None:
    """Record a failure and wake the waiting caller."""
    if not self.done:
      self.done = True
      self.error = error
      self.event.set()

  async def wait(self) -> ET.Element:
    """Wait for the response or raise the recorded failure."""
    await self.event.wait()
    if self.error is not None:
      raise self.error
    assert self.result is not None
    return self.result


class PrestoConnection(HasLifespan):
  """Serialize commands and independently deliver every instrument event.

  A timeout or cancellation during a command invalidates the session. Reconnect
  before another command: the wire protocol has names but no transaction IDs,
  so a late response to a repeated command cannot otherwise be distinguished.
  """

  def __init__(self, io: HID, on_event: Callable[[ET.Element], None], timeout: float) -> None:
    """Configure the transport, event observer and command deadline."""
    super().__init__()
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be positive and finite")
    self.io = io
    self.timeout = timeout
    self._on_event = on_event
    self._open = False
    self._reader_running = False
    self._pending: Optional[_PendingWaiter] = None
    self._pending_name: Optional[str] = None
    self._terminal: Optional[_PendingWaiter] = None
    self._failure: Optional[PrestoError] = None
    self._raise_on_error = True
    self._events: deque[ET.Element] = deque()
    self._event_ready = anyio.Event()
    self._commands = anyio.Lock()
    self._writes = anyio.Lock()
    self._command_in_progress = False

  def check_open(self) -> None:
    """Refuse commands outside a healthy session."""
    if self._failure is not None:
      raise self._failure
    if not self._open or not self._reader_running:
      raise PrestoError("Call setup() before using the Presto")

  def _fail(self, error: PrestoError) -> None:
    """Wake every pending operation and event consumer on terminal I/O failure."""
    self._failure = error
    for waiter in (self._pending, self._terminal):
      if waiter is not None:
        waiter.set_exception(error)
    self._event_ready.set()

  async def _read_loop(self, reader_scope: anyio.CancelScope) -> None:
    """Decode length-prefixed HID packets and route responses and events."""
    messages = XMLMessages()
    try:
      with reader_scope:
        while self._failure is None and self._open:
          # HID.read polls with a short internal worker timeout and yields until a report arrives.
          raw = await self.io.read(size=64)
          if not raw:
            await anyio.sleep(0)
            continue
          length = raw[0]
          if length > 63 or length > len(raw) - 1:
            raise PrestoError(f"Invalid HID payload length {length} in {len(raw)}-byte report")
          for element in messages.feed(raw[1 : 1 + length]):
            if element.tag == "Res":
              if element.get("name") != self._pending_name:
                logger.debug("Ignoring unmatched response %s", element.get("name"))
                continue
              target = self._terminal if self._terminal is not None else self._pending
              if target is not None and not target.done:
                if element.get("ok", "false").lower() != "true" and (
                  self._raise_on_error or self._terminal is not None
                ):
                  target.set_exception(response_error(element))
                elif self._terminal is None:
                  target.set_result(element)
            else:
              self._on_event(element)
              self._events.append(element)
              self._event_ready.set()
              if self._terminal is not None and not self._terminal.done:
                if element.get("name") in ("Ready", "Aborted", "Error"):
                  self._terminal.set_result(element)
    except anyio.get_cancelled_exc_class():
      raise
    except Exception as error:
      if not self._open:
        return
      logger.error("Presto reader failed: %s", error)
      self._fail(PrestoError(f"Presto connection failed: {error}"))
    finally:
      self._reader_running = False

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Open HID and begin a fresh session with empty event and response state."""
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io.lifespan())
    self._failure = None
    self._pending = None
    self._pending_name = None
    self._terminal = None
    self._events = deque()
    self._event_ready = anyio.Event()
    self._commands = anyio.Lock()
    self._writes = anyio.Lock()
    self._command_in_progress = False
    self._open = True
    self._reader_running = True
    stack.push_shielded_async_callback(self._wait_for_commands)
    group = await stack.enter_task_group()
    reader_scope = anyio.CancelScope(shield=True)
    group.start_soon(self._read_loop, reader_scope)
    stack.callback(self._stop_reader, reader_scope, group)

  def _stop_reader(self, reader_scope: anyio.CancelScope, group: anyio.abc.TaskGroup) -> None:
    """Wake waiters and cancel the background reader before its task group joins."""
    self._open = False
    self._fail(PrestoError("Presto connection closed"))
    reader_scope.shield = False
    reader_scope.cancel()
    group.cancel_scope.cancel()

  async def _wait_for_commands(self) -> None:
    """Ensure pending callers have observed closure before another session opens."""
    async with self._commands:
      pass

  async def _write_payload_locked(self, payload: bytes, *, check_session: bool = True) -> None:
    """Write a complete XML command as zero-padded 64-byte output reports while holding _writes."""
    if not payload.endswith(b"\n"):
      payload += b"\n"
    for offset in range(0, len(payload), 63):
      if check_session and self._failure is not None:
        raise self._failure
      chunk = payload[offset : offset + 63]
      report = bytes([len(chunk)]) + chunk.ljust(63, b"\x00")
      written = await self.io.write(report, report_id=b"\x00")
      if written != 65:
        raise OSError(f"Short HID output report write: {written} of 65 bytes")

  async def _write_payload(self, payload: bytes, *, check_session: bool = True) -> None:
    """Write a complete XML command as zero-padded 64-byte output reports."""
    async with self._writes:
      await self._write_payload_locked(payload, check_session=check_session)

  async def _exchange(self, payload: bytes, waiter: _PendingWaiter) -> ET.Element:
    """Include both transmission and response wait in the command deadline."""
    await self._write_payload(payload)
    return await waiter.wait()

  async def command(
    self, payload: bytes, *, terminal: bool = False, raise_on_error: bool = True
  ) -> ET.Element:
    """Wait for a matching response, or Ready/Error/Aborted for a rotation."""
    name = ET.fromstring(payload).get("name")
    if not name:
      raise ValueError("Command must have a name")
    async with self._commands:
      self.check_open()
      waiter = _PendingWaiter(anyio.Event())
      self._command_in_progress = True
      self._pending_name = name
      self._raise_on_error = raise_on_error
      if terminal:
        self._terminal = waiter
      else:
        self._pending = waiter
      try:
        with anyio.fail_after(self.timeout):
          return await self._exchange(payload, waiter)
      except TimeoutError as error:
        failure = PrestoError(f"Timeout waiting for {name}; reconnect before continuing")
        self._fail(failure)
        raise failure from error
      except anyio.get_cancelled_exc_class():
        self._fail(PrestoError(f"{name} was cancelled; reconnect before continuing"))
        raise
      except OSError as error:
        failure = PrestoError(f"Failed to send {name}: {error}; reconnect before continuing")
        self._fail(failure)
        raise failure from error
      finally:
        self._command_in_progress = False
        if self._pending is waiter:
          self._pending = None
        if self._terminal is waiter:
          self._terminal = None
        self._pending_name = None

  async def disconnect(self) -> None:
    """Send Disconnect without requiring the optional response."""
    if self._command_in_progress:
      return
    async with self._commands:
      self.check_open()
      with anyio.fail_after(self.timeout):
        await self._write_payload(command_xml("Disconnect"))

  async def get_event(self) -> ET.Element:
    """Return the next event, or raise promptly when the connection closes."""
    self.check_open()
    await anyio.lowlevel.checkpoint()
    while not self._events and self._failure is None and self._open:
      ready = self._event_ready
      await ready.wait()
      if self._event_ready is ready and not self._events and self._failure is None and self._open:
        self._event_ready = anyio.Event()
    if self._failure is not None or not self._open:
      raise self._failure or PrestoError("Presto connection closed")
    element = self._events.popleft()
    if not self._events and self._failure is None and self._open:
      self._event_ready = anyio.Event()
    return element

  async def abort(self) -> None:
    """Send the two-phase abort independently of a pending command.

    The feature payload is two bytes; HID supplies the unnumbered report ID.
    A fresh session is required afterwards to discard interrupted replies.
    """
    if not self._open:
      raise PrestoError("Call setup() before using the Presto")
    self._fail(PrestoError("Presto aborted; stop() and setup() before continuing"))
    with anyio.CancelScope(shield=True):
      async with self._writes:
        with anyio.fail_after(self.timeout):
          await self.io.send_feature_report(b"\x01\x00")
        with anyio.fail_after(self.timeout):
          await self._write_payload_locked(b"\x1b\n", check_session=False)
    await anyio.lowlevel.checkpoint_if_cancelled()
