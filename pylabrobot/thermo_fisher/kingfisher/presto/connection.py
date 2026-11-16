"""Presto XML framing and response routing over PyLabRobot HID."""

import asyncio
import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Optional

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


class PrestoConnection:
  """Serialize commands and independently deliver every instrument event.

  A timeout or cancellation during a command invalidates the session. Reconnect
  before another command: the wire protocol has names but no transaction IDs,
  so a late response to a repeated command cannot otherwise be distinguished.
  """

  def __init__(self, io: HID, on_event: Callable[[ET.Element], None], timeout: float) -> None:
    """Configure the transport, event observer and command deadline."""
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be positive and finite")
    self.io = io
    self.timeout = timeout
    self._on_event = on_event
    self._read_task: Optional[asyncio.Task[None]] = None
    self._pending: Optional[asyncio.Future[ET.Element]] = None
    self._pending_name: Optional[str] = None
    self._terminal: Optional[asyncio.Future[ET.Element]] = None
    self._closed: Optional[asyncio.Future[None]] = None
    self._failure: Optional[PrestoError] = None
    self._raise_on_error = True
    self._events: asyncio.Queue[ET.Element] = asyncio.Queue()
    self._commands = asyncio.Lock()
    self._writes = asyncio.Lock()

  def check_open(self) -> None:
    """Refuse commands outside a healthy session."""
    if self._failure is not None:
      raise self._failure
    if self._read_task is None or self._read_task.done():
      raise PrestoError("Call setup() before using the Presto")

  def _fail(self, error: PrestoError) -> None:
    """Wake every pending operation and event consumer on terminal I/O failure."""
    self._failure = error
    for future in (self._pending, self._terminal):
      if future is not None and not future.done():
        future.set_exception(error)
    if self._closed is not None and not self._closed.done():
      self._closed.set_result(None)

  async def _read_loop(self) -> None:
    """Decode length-prefixed HID packets and route responses and events."""
    messages = XMLMessages()
    try:
      while self._failure is None:
        # HID shares one worker for reads/writes. Short reads bound write/abort latency.
        raw = await self.io.read(size=64, timeout=100)
        if not raw:
          await asyncio.sleep(0)
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
            if target is not None and not target.done():
              if element.get("ok", "false").lower() != "true" and (
                self._raise_on_error or self._terminal is not None
              ):
                target.set_exception(response_error(element))
              elif self._terminal is None:
                target.set_result(element)
          else:
            self._on_event(element)
            self._events.put_nowait(element)
            if self._terminal is not None and not self._terminal.done():
              if element.get("name") in ("Ready", "Aborted", "Error"):
                self._terminal.set_result(element)
    except asyncio.CancelledError:
      raise
    except Exception as error:
      logger.error("Presto reader failed: %s", error)
      self._fail(PrestoError(f"Presto connection failed: {error}"))

  async def setup(self) -> None:
    """Open HID and begin a fresh session with empty event and response state."""
    if self._read_task is not None:
      self.check_open()
      return
    await self.io.setup()
    self._failure = None
    self._events = asyncio.Queue()
    self._closed = asyncio.get_running_loop().create_future()
    self._read_task = asyncio.create_task(self._read_loop())

  async def stop(self) -> None:
    """Wake waiters, cancel the reader, and close HID, including failed sessions."""
    self._fail(PrestoError("Presto connection closed"))
    task, self._read_task = self._read_task, None
    try:
      if task is not None:
        task.cancel()
        try:
          await task
        except asyncio.CancelledError:
          pass
    finally:
      await self.io.stop()
      async with self._commands:
        pass  # Pending callers have observed closure before another session opens.

  async def _write_payload(self, payload: bytes, *, check_session: bool = True) -> None:
    """Write a complete XML command as zero-padded 64-byte output reports."""
    async with self._writes:
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

  async def _exchange(self, payload: bytes, future: asyncio.Future[ET.Element]) -> ET.Element:
    """Include both transmission and response wait in the command deadline."""
    await self._write_payload(payload)
    return await future

  async def command(
    self, payload: bytes, *, terminal: bool = False, raise_on_error: bool = True
  ) -> ET.Element:
    """Wait for a matching response, or Ready/Error/Aborted for a rotation."""
    name = ET.fromstring(payload).get("name")
    if not name:
      raise ValueError("Command must have a name")
    async with self._commands:
      self.check_open()
      future: asyncio.Future[ET.Element] = asyncio.get_running_loop().create_future()
      self._pending_name = name
      self._raise_on_error = raise_on_error
      if terminal:
        self._terminal = future
      else:
        self._pending = future
      try:
        return await asyncio.wait_for(self._exchange(payload, future), timeout=self.timeout)
      except asyncio.TimeoutError as error:
        failure = PrestoError(f"Timeout waiting for {name}; reconnect before continuing")
        self._fail(failure)
        raise failure from error
      except asyncio.CancelledError:
        self._fail(PrestoError(f"{name} was cancelled; reconnect before continuing"))
        raise
      except OSError as error:
        failure = PrestoError(f"Failed to send {name}: {error}; reconnect before continuing")
        self._fail(failure)
        raise failure from error
      finally:
        if not future.done():
          future.cancel()
        elif not future.cancelled():
          future.exception()  # Retrieve failures even when transmission failed first.
        self._pending = None
        self._terminal = None
        self._pending_name = None

  async def disconnect(self) -> None:
    """Send Disconnect without requiring the optional response."""
    if self._commands.locked():
      return
    async with self._commands:
      self.check_open()
      await asyncio.wait_for(self._write_payload(command_xml("Disconnect")), self.timeout)

  async def get_event(self) -> ET.Element:
    """Return the next event, or raise promptly when the connection closes."""
    self.check_open()
    assert self._closed is not None
    receive = asyncio.create_task(self._events.get())
    try:
      done, _ = await asyncio.wait((receive, self._closed), return_when=asyncio.FIRST_COMPLETED)
      if receive in done:
        return receive.result()
      raise self._failure or PrestoError("Presto connection closed")
    finally:
      receive.cancel()
      try:
        await receive
      except asyncio.CancelledError:
        pass

  async def abort(self) -> None:
    """Send the two-phase abort independently of a pending command.

    The feature payload is two bytes; HID supplies the unnumbered report ID.
    A fresh session is required afterwards to discard interrupted replies.
    """
    if self._read_task is None:
      raise PrestoError("Call setup() before using the Presto")
    self._fail(PrestoError("Presto aborted; stop() and setup() before continuing"))
    await asyncio.wait_for(self.io.send_feature_report(b"\x01\x00"), self.timeout)
    await asyncio.wait_for(self._write_payload(b"\x1b\n", check_session=False), self.timeout)
