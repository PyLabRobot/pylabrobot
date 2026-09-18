"""IO for a WebSocket client connection.

A WebSocket carries messages rather than bytes: the protocol frames them, so a read returns one
whole message and there is nothing to scan for a delimiter. That is why this has no `readline`,
`readuntil` or `read_exact` the way `Socket` does - framing is the transport's job here, not the
caller's.

Messages arrive whether or not anyone is reading. A background task drains the connection into a
bounded queue so a slow caller cannot apply backpressure all the way to the device, which on a
telemetry stream would stall the instrument rather than the reader.
"""

import asyncio
import base64
import logging
import ssl
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, Mapping, Optional, Union

import websockets
import websockets.asyncio.client
import websockets.exceptions

from pylabrobot.io.capture import Command, capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import IOBase
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

if TYPE_CHECKING:
  from pylabrobot.io.capture import CaptureReader

logger = logging.getLogger(__name__)

# Headers that carry a credential. Sending one of these over `ws://` puts it on the wire in clear
# text, which a `wss://` url is the whole point of avoiding, so the connection is refused rather
# than made insecurely.
_CREDENTIAL_HEADERS: FrozenSet[str] = frozenset({"authorization", "cookie", "x-api-key"})

# The schemes that encrypt. Anything else is plain text as far as a credential is concerned.
_SECURE_SCHEMES: FrozenSet[str] = frozenset({"wss", "https"})

# Query parameters that carry a credential. A url is logged, serialized and recorded as a
# capture's device id, so one of these has to come out of it first - keeping a token out of the
# headers is no use if the url puts it back. Named, like `_CREDENTIAL_HEADERS`, so what is
# covered can be read off the list.
_CREDENTIAL_QUERY_PARAMS: FrozenSet[str] = frozenset({"token", "access_token", "api_key", "apikey"})

_REDACTED = "<redacted>"


def redact_url(url: str) -> str:
  """The url with anything that authenticates taken out of it.

  Userinfo goes entirely, and a credential query parameter keeps its name and loses its value, so
  what is left still says which endpoint this was without saying how to reach it as you.

  Args:
    url: the url to redact.

  Returns:
    The url, safe to log, serialize and record.
  """
  parts = urllib.parse.urlsplit(url)
  netloc = parts.netloc
  if "@" in netloc:
    netloc = f"{_REDACTED}@{netloc.rsplit('@', 1)[1]}"
  query = urllib.parse.urlencode(
    [
      (name, _REDACTED if name.lower() in _CREDENTIAL_QUERY_PARAMS else value)
      for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    ]
  )
  return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


class _Closed:
  """Marks the end of the stream in the queue, so a waiting read learns the link is gone.

  Without it a read waits on a queue that nothing will ever fill again. It is queued behind
  whatever had already arrived, so messages read out before the failure they preceded.
  """


_CLOSED = _Closed()


@dataclass
class WebSocketCommand(Command):
  """One message in or out.

  Attributes:
    data: the message. Text as it was sent; binary base64 encoded, since a capture file is JSON.
    binary: whether the frame was binary, which is what says how to read `data` back.
  """

  data: str
  binary: bool

  def __init__(
    self,
    device_id: str,
    action: str,
    data: str,
    binary: bool,
    module: str = "websocket",
  ):
    """
    Args:
      device_id: which link this was on, as its url.
      action: `"recv"` or `"send"`.
      data: the message, encoded as `data` describes.
      binary: whether the frame was binary.
      module: the module name recorded with the command.
    """
    super().__init__(module=module, device_id=device_id, action=action)
    self.data = data
    self.binary = binary


class WebSocket(IOBase):
  """IO for reading from and writing to a WebSocket server.

  `write` has no deadline and can wait indefinitely on a peer that answers pings but stops
  reading. Do NOT wrap it in `asyncio.wait_for` to bound it: cancelling a send is what `write`
  documents as unsafe, and a caller that retries on the timeout sends the same message twice.
  The library's own advice for a peer like that is to close the connection rather than cancel the
  send, so a caller that needs a bound should call `stop` on one - which fails the send with a
  `ConnectionError` that honestly says the message did not go.
  """

  def __init__(
    self,
    human_readable_device_name: str,
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    ssl_context: Optional[ssl.SSLContext] = None,
    read_timeout: float = 30,
    max_queued_messages: int = 1000,
  ):
    """
    Args:
      human_readable_device_name: what to call this device in errors.
      url: the server to connect to, `ws://` or `wss://`. The scheme decides whether the link is
        encrypted, so there is no separate flag for it.
      headers: headers to send with the opening handshake, e.g. an `Authorization` header. Not
        serialized: see `serialize`.
      ssl_context: the TLS context for a `wss://` url. Defaults to the library's own.
      read_timeout: how long `read` waits for a message before raising, in seconds.
      max_queued_messages: how many arrived messages to hold for a caller that has not read them
        yet. Once full, the oldest is dropped: the newest state is what a telemetry stream is for,
        and holding the line instead would stall the device.

    Raises:
      ValueError: If the read timeout or the queue size is not positive, or a credential header
        would be sent over an unencrypted url.
      RuntimeError: If capture or validation is active.
    """
    if get_capture_or_validation_active():
      raise RuntimeError(
        "Cannot create a new WebSocket object while capture or validation is active"
      )
    if read_timeout <= 0:
      raise ValueError("read_timeout must be greater than zero")
    if max_queued_messages <= 0:
      raise ValueError("max_queued_messages must be greater than zero")

    self.human_readable_device_name = human_readable_device_name
    self._url = url
    self._headers = dict(headers or {})
    self._ssl_context = ssl_context
    self._read_timeout = read_timeout
    self._max_queued_messages = max_queued_messages

    # What every message about this link names it by. The url itself is only ever handed to
    # `connect`; nothing else sees it.
    self._safe_url = redact_url(url)

    self._refuse_credentials_in_the_clear()

    self._connection: Optional[websockets.asyncio.client.ClientConnection] = None
    self._drain_task: Optional[asyncio.Task[None]] = None
    # Everything below describes ONE connection, and is rebuilt by `_connect` for the next one.
    # A queue that outlived its connection hands a reader the previous link's messages, and worse
    # its end-of-stream marker, which would end a live link on its first read.
    #
    # None until there is a connection, rather than built here: an asyncio primitive binds to a
    # loop when it is constructed, and this object is built in ordinary synchronous code - where
    # on Python 3.9 there may be no loop to bind to at all. Nothing needs either of these before
    # `setup`, so neither exists before it.
    self._queue: Optional[asyncio.Queue[Union[str, bytes, _Closed]]] = None
    # What the stream ended with, as the cause of the error a read past the end raises. Set for
    # any end the device made, including an orderly one; None when this side closed the link,
    # which had no error to carry. The read fails either way: there is nothing more to read.
    self._closed_by: Optional[BaseException] = None
    # Whether the end of this connection's stream has been marked in the queue. One marker per
    # connection: it says there is nothing more, which a second cannot say twice.
    self._ended = False
    self._dropping = False
    # Whether a connection was ever opened, which is what tells "never set up" from "closed".
    # Not reset with the rest: it is about this object's history, not the current link.
    self._ever_connected = False
    # How many messages were dropped because nothing read them in time, over the life of this
    # object. Reported rather than counted silently: a caller that falls behind is a caller
    # missing device state.
    self.dropped_messages = 0
    self._write_lock: Optional[asyncio.Lock] = None

  def _refuse_credentials_in_the_clear(self) -> None:
    """Refuse to open an unencrypted link carrying a credential, wherever it is carried.

    A header and a url are the same disclosure: moving a token from `Authorization` into
    `?token=` does not make sending it in clear text any safer, so both are refused.

    Raises:
      ValueError: If a credential is set and the url is not an encrypted scheme.
    """
    parts = urllib.parse.urlsplit(self._url)
    if parts.scheme in _SECURE_SCHEMES:
      return
    offending = sorted(name for name in self._headers if name.lower() in _CREDENTIAL_HEADERS)
    if "@" in parts.netloc:
      offending.append("the url's userinfo")
    offending += sorted(
      name
      for name, _ in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
      if name.lower() in _CREDENTIAL_QUERY_PARAMS
    )
    if offending:
      raise ValueError(
        f"refusing to send {', '.join(offending)} to {self._safe_url}: an unencrypted websocket "
        f"puts the credential on the wire in clear text. Use a wss:// url, or take the "
        f"credential out."
      )

  async def setup(self):
    """Open the link and start reading messages off it.

    Repeatable: a second call closes the link the first opened rather than leaving it open with
    nothing holding it, which would leave its drain task feeding the same queue as the new one's.
    """
    await self._connect()

  async def _connect(self):
    """Connect, and start the task that drains the connection into the queue.

    Anything the previous connection left is closed first, and discarded only once there is a
    connection to replace it with, so what a read gets afterwards is this connection's and only
    this connection's.

    Raises:
      Exception: whatever connecting raised, recorded as what closed the link. The previous
        connection's ended queue is left in place, so a read still reports the link as closed
        rather than waiting on an empty queue that nothing will ever fill.
    """
    await self._disconnect()
    # `ssl` is only passed when there is a context: the library picks its own default for a
    # `wss://` url, and handing it None explicitly is not the same as leaving it alone.
    tls: Dict[str, Any] = {} if self._ssl_context is None else {"ssl": self._ssl_context}
    try:
      connection = await websockets.asyncio.client.connect(
        self._url,
        additional_headers=self._headers or None,
        **tls,
      )
    except Exception as exc:
      # The previous connection's ended queue stays, and its marker is what a read will reach.
      # Why this connect failed is more use to that reader than why the old link ended, which
      # they have already been told.
      self._closed_by = exc
      raise
    # Only now, with a connection in hand. A fresh queue put here before connecting would be
    # left empty and unmarked by a connect that raised, and every read would wait it out.
    self._queue = asyncio.Queue(maxsize=self._max_queued_messages)
    # One lock for the life of the object, built here for the same reason the queue is. A write
    # already in flight is on the connection that is going away, so it does not want a new one.
    self._write_lock = self._write_lock or asyncio.Lock()
    self._closed_by = None
    self._ended = False
    self._dropping = False
    self._connection = connection
    self._ever_connected = True
    logger.info("Opened websocket to %s", self._safe_url)
    self._drain_task = asyncio.create_task(self._drain(connection))

  async def stop(self):
    """Close the link."""
    await self._disconnect()

  async def _disconnect(self):
    """Stop draining, close the connection, and mark the end of its stream.

    Does nothing on a link that is not open, so it is safe to call before opening one.

    The marker is what a reader blocked on `read` is woken by. Closing the link cancels the drain
    task, and a cancelled task marks nothing, so without this the reader waits out its whole
    timeout on a link that is already gone - which is the case the marker exists for.
    """
    open_link = self._drain_task is not None or self._connection is not None

    if self._drain_task is not None:
      self._drain_task.cancel()
      # Whatever the task ended with is already recorded on `_closed_by` for a read to report, and
      # closing a link is not where it should surface, so it is awaited only to join it.
      await asyncio.gather(self._drain_task, return_exceptions=True)
      self._drain_task = None

    if self._connection is not None:
      logger.info("Closing websocket to %s", self._safe_url)
      try:
        await self._connection.close()
      except OSError as e:
        logger.warning("Error while closing websocket connection: %s", e)
      finally:
        self._connection = None

    if open_link:
      self._mark_ended()

  async def reconnect(self):
    """Close the link and open it again.

    Whether and when to do this is the caller's: a device that expects its stream to survive a
    reboot wants it, and one driving a short-lived connection does not, so no policy is applied
    here.

    Anything the previous connection delivered but nobody read is discarded with it. Carrying it
    over would hand a reader state from before the drop with nothing marking where it stopped
    describing the device.
    """
    await self._connect()

  def serialize(self) -> Dict[str, Any]:
    """What this link is, without what authenticates it.

    `headers` is deliberately left out. It is where an `Authorization` header lives, and serialized
    state is written to disk and passed around, which is not somewhere a credential belongs.

    Returns:
      The link's address and limits.
    """
    return {
      "human_readable_device_name": self.human_readable_device_name,
      "url": self._safe_url,
      "type": "WebSocket",
      "read_timeout": self._read_timeout,
      "max_queued_messages": self._max_queued_messages,
    }

  async def _drain(self, connection: "websockets.asyncio.client.ClientConnection") -> None:
    """Read messages off the connection into the queue until it ends.

    Runs for as long as the connection does. Whatever ends it is recorded and marked in the queue
    rather than raised here, since nothing awaits this task: a read is what surfaces it.

    Args:
      connection: the connection to drain. Passed in rather than read off the instance so a
        reconnect that replaced it cannot make this task drain the new one.
    """
    try:
      while True:
        self._enqueue(await connection.recv())
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      self._closed_by = exc
      self._mark_ended()
      if isinstance(exc, websockets.exceptions.ConnectionClosedOK):
        logger.info("Websocket to %s closed", self._safe_url)
      else:
        logger.warning("Websocket to %s ended: %r", self._safe_url, exc)

  def _mark_ended(self) -> None:
    """Mark the end of this connection's stream, so a read past it raises rather than waiting.

    Once per connection. Both the drain task and a local close want to mark it, and whichever
    gets there first is the one that describes it: `_closed_by` already says which that was.

    A second marker is not free. `_enqueue` drops the oldest message to make room for it, so
    marking twice costs an unread message exactly when a reader has fallen behind.
    """
    if self._ended or self._queue is None:
      return
    self._ended = True
    self._enqueue(_CLOSED)

  def _enqueue(self, message: Union[str, bytes, _Closed]) -> None:
    """Put a message on the queue, dropping the oldest if there is no room.

    Args:
      message: the message that arrived, or the end-of-stream marker.
    """
    queue = self._queue
    if queue is None:
      return
    if queue.full():
      queue.get_nowait()
      self.dropped_messages += 1
      if not self._dropping:
        self._dropping = True
        # Once per spell of dropping, not once per message: a caller that has stopped reading
        # would otherwise be told so by every message it is failing to read.
        logger.warning(
          "websocket %s has %d unread messages; dropping the oldest to keep reading",
          self._safe_url,
          self._max_queued_messages,
        )
    else:
      self._dropping = False
    queue.put_nowait(message)

  async def read(self, timeout: Optional[float] = None) -> bytes:
    """Read the next message that arrived.

    Messages are returned in the order they arrived, including any that arrived before this was
    called. A text frame is returned UTF-8 encoded, since this returns bytes.

    Args:
      timeout: how long to wait for a message. Defaults to the link's `read_timeout`.

    Returns:
      The message.

    Raises:
      RuntimeError: If the link was never set up. A link that WAS set up and has since closed
        raises ConnectionError instead: that it is shut is not the same as never having been open,
        and telling a caller to call `setup` is no answer to the second.
      TimeoutError: If no message arrived in time.
      ConnectionError: If the stream has ended, after everything that arrived first is read.
    """
    if not self._ever_connected:
      raise RuntimeError(
        f"WebSocket for '{self.human_readable_device_name}' not set up; call setup() first"
      )
    timeout = self._read_timeout if timeout is None else timeout
    # Bound once, and used for both the wait and the marker below. A read can be waiting while a
    # reconnect swaps `self._queue`, and putting this queue's marker into that one would end the
    # new connection's stream before it delivered anything.
    queue = self._queue
    if queue is None:
      raise ConnectionError(f"Websocket to {self._safe_url} is closed")
    try:
      message = await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError as exc:
      logger.error("read timeout: %r", exc)
      raise TimeoutError(f"Timeout while reading from websocket after {timeout} seconds") from exc

    if isinstance(message, _Closed):
      # Back where it came from: the stream stays ended for every read after this one.
      queue.put_nowait(message)
      raise ConnectionError(f"Websocket to {self._safe_url} is closed") from self._closed_by

    logger.log(LOG_LEVEL_IO, "[%s] recv %s", self._safe_url, message)
    if isinstance(message, bytes):
      # Base64 rather than as it stands: a capture file is JSON, which carries text.
      capturer.record(
        WebSocketCommand(
          device_id=self._safe_url,
          action="recv",
          data=base64.b64encode(message).decode("ascii"),
          binary=True,
        )
      )
      return message

    # Text as text, so a capture file stays readable - the messages here are usually JSON, and
    # hex or base64 would put a decode between a reader and every one of them.
    capturer.record(
      WebSocketCommand(device_id=self._safe_url, action="recv", data=message, binary=False)
    )
    return message.encode("utf-8")

  async def write(self, data: bytes, text: bool = False) -> None:
    """Send one message.

    There is no timeout, deliberately. A send only waits when the library's own write buffer is
    full, and by then the frame is already committed to it, so cancelling the wait does not
    un-send the message - it only tells the caller it failed when it did not. A caller that
    retried on that would send the instrument the same command twice. The library's flow control
    is what bounds this; a deadline that cannot mean "it did not go" is worse than none.

    Args:
      data: the message to send, as one frame.
      text: whether to send it as a text frame rather than a binary one. A JSON API wants text,
        and a peer is entitled to refuse the other: the two are different frame types on the
        wire, not an encoding detail. `data` is UTF-8 decoded to send it.

    Raises:
      RuntimeError: If the link was never set up. A link that WAS set up and has since closed
        raises ConnectionError, as a read on one does.
      UnicodeDecodeError: If `text` is set and `data` is not UTF-8. A text frame carries text,
        and bytes that do not decode are not text - they wanted a binary frame.
      ConnectionError: If the link is closed, or closes before the message goes.
    """
    if not self._ever_connected:
      raise RuntimeError(
        f"WebSocket for '{self.human_readable_device_name}' not set up; call setup() first"
      )
    connection, lock = self._connection, self._write_lock
    if connection is None or lock is None:
      raise ConnectionError(f"Websocket to {self._safe_url} is closed")
    async with lock:
      try:
        await connection.send(data.decode("utf-8") if text else data)
      except websockets.exceptions.ConnectionClosed as exc:
        logger.error("write error: %r", exc)
        raise ConnectionError(f"Websocket to {self._safe_url} is closed") from exc

      logger.log(LOG_LEVEL_IO, "[%s] send %s", self._safe_url, data)
      capturer.record(
        WebSocketCommand(
          device_id=self._safe_url,
          action="send",
          data=data.decode("utf-8") if text else base64.b64encode(data).decode("ascii"),
          binary=not text,
        )
      )


class WebSocketValidator(WebSocket):
  """A link that answers from a capture file instead of a device, for validating a run."""

  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    url: str,
    read_timeout: float = 30,
    max_queued_messages: int = 1000,
  ):
    """
    Args:
      cr: the capture to answer from.
      human_readable_device_name: what to call this device in errors.
      url: the url the capture was taken against, which every command in it must name.
      read_timeout: unused, since nothing is waited for.
      max_queued_messages: unused, since nothing is queued.
    """
    super().__init__(
      human_readable_device_name=human_readable_device_name,
      url=url,
      read_timeout=read_timeout,
      max_queued_messages=max_queued_messages,
    )
    self.cr = cr

  async def setup(self):
    """Mock setup for validation."""

  async def stop(self):
    """Mock stop for validation."""

  async def _connect(self):
    """Mock connect for validation."""

  async def _disconnect(self):
    """Mock disconnect for validation."""

  def _next(self, action: str) -> WebSocketCommand:
    """Take the next command off the capture and check it is the one expected here.

    Args:
      action: the action this call is, `"recv"` or `"send"`.

    Returns:
      The recorded command.

    Raises:
      ValidationError: If the capture's next command is a different one.
    """
    # Read as a plain record first. A capture that diverged onto another transport holds a
    # command of a different shape, and building this dataclass out of it raises TypeError for a
    # missing field - which says nothing about what actually went wrong.
    recorded = self.cr.next_command()
    if not (
      recorded.get("module") == "websocket"
      and recorded.get("device_id") == self._safe_url
      and recorded.get("action") == action
    ):
      raise ValidationError(
        f"Expected websocket {action} on {self._safe_url}, got "
        f"{recorded.get('module')} {recorded.get('action')} on {recorded.get('device_id')}"
      )
    return WebSocketCommand(**recorded)

  async def read(self, timeout: Optional[float] = None) -> bytes:
    """Return the next recorded message.

    Args:
      timeout: unused, since nothing is waited for.

    Returns:
      The message, as `read` would have returned it.
    """
    recorded = self._next("recv")
    if recorded.binary:
      return base64.b64decode(recorded.data)
    return recorded.data.encode("utf-8")

  async def write(self, data: bytes, text: bool = False) -> None:
    """Check what was sent against what the capture recorded.

    Args:
      data: the message the caller sent.
      text: whether it was sent as a text frame.

    Raises:
      ValidationError: If it is not what was recorded, or was not the frame type recorded.
    """
    recorded = self._next("send")
    if recorded.binary == text:
      raise ValidationError(
        f"Expected a {'binary' if recorded.binary else 'text'} frame, "
        f"got a {'text' if text else 'binary'} one"
      )
    expected = recorded.data.encode("utf-8") if text else base64.b64decode(recorded.data)
    if expected != data:
      align_sequences(expected=expected.decode("latin-1"), actual=data.decode("latin-1"))
      raise ValidationError("Data mismatch: difference was written to stdout.")
