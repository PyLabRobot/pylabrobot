"""One framed request and its reply.

The transports below this module move bytes; the device layer above it decides which commands to
send and in what order. This is the single place that knows the shape of an exchange: purge, write
the header, write the payload, read the acknowledgement, read the reply frame, check it and turn a
non-zero status into an exception.

A link is what a device holds. It owns the input/output lifecycle and nothing else, so a device
never touches a transport directly and never learns which kind of transport it got.
"""

from __future__ import annotations

import asyncio
import logging

from pylabrobot.agilent.biotek.lhc.comm.connection import transport_for
from pylabrobot.agilent.biotek.lhc.comm.transport import DEFAULT_READ_TIMEOUT, Transport
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.error_handling import (
  NOT_ACKNOWLEDGED,
  PORT_WOULD_NOT_OPEN,
  REPLY_TIMED_OUT,
  ErrorKind,
  fail,
  raise_for_status,
)
from pylabrobot.agilent.biotek.lhc.serialization.command import Command
from pylabrobot.agilent.biotek.lhc.serialization.frame import HEADER_LENGTH, Header

logger = logging.getLogger(__name__)

ACK = 0x06
NAK = 0x15

_ACK_POLL_INTERVAL = 0.01
_PAYLOAD_WRITE_DELAY = 0.001
_PURGE_ROUNDS = 6


class Link:
  """The framed connection to one instrument.

  Args:
    port: The port the instrument is on. Ignored when ``io`` is given.
    family: Which family the instrument belongs to, which decides what an error code means.
    name: Human-readable instrument name, used in logs and error messages.
    timeout: Default reply timeout in seconds. A command that answers only once it has finished
      moving carries a longer one of its own.
    io: An already built transport to use instead of opening ``port``. This is how a capture and
      replay double is supplied to a test; there is no other way past the port string.
  """

  def __init__(
    self,
    port: str = "",
    family: InstrumentFamily = InstrumentFamily.EL406,
    name: str = "BioTek instrument",
    timeout: float = DEFAULT_READ_TIMEOUT,
    io: Transport | None = None,
  ) -> None:
    if io is None and not port:
      raise ValueError("either a port or a transport is required")
    self._io = io if io is not None else transport_for(port=port, name=name, timeout=timeout)
    self._family = family
    self._name = name
    self._timeout = timeout
    self._open = False
    self._exchange = asyncio.Lock()

  @property
  def family(self) -> InstrumentFamily:
    """Which family the instrument belongs to."""
    return self._family

  @property
  def name(self) -> str:
    """The instrument's name, as it appears in logs and error messages."""
    return self._name

  @property
  def port(self) -> str:
    """The port the instrument is on."""
    return self._io.port

  @property
  def is_open(self) -> bool:
    """Whether the link is open."""
    return self._open

  async def setup(self) -> None:
    """Open the link. Calling this on an open link does nothing.

    Raises:
      LinkError: If the port will not open.
    """
    if self._open:
      return
    try:
      await self._io.setup()
    except Exception as error:
      raise fail(
        ErrorKind.LINK,
        f"{self._name} on {self._io.port} will not open: {error}",
        operation="open",
        code=PORT_WOULD_NOT_OPEN,
      ) from error
    self._open = True
    logger.info("opened %s on %s", self._name, self._io.port)

  async def stop(self) -> None:
    """Close the link. Calling this on a closed link does nothing."""
    if not self._open:
      return
    self._open = False
    await self._io.stop()
    logger.info("closed %s on %s", self._name, self._io.port)

  async def purge(self) -> None:
    """Discard whatever is buffered in either direction."""
    for _ in range(_PURGE_ROUNDS):
      await self._io.purge()

  async def request(self, command: Command, operation: str = "") -> bytes:
    """Send a command and read its reply.

    One exchange at a time: the lock is what keeps two callers from interleaving their frames on a
    link that has no way to tell one reply from another.

    Args:
      command: The command to send.
      operation: What is being attempted, for the message of any exception raised.

    Returns:
      The reply's answer, with the instrument's status split off, and nothing at all for a command
      the instrument does not answer.

    Raises:
      LinkError: If the link is closed, the write fails, nothing acknowledges the command, or the
        reply does not arrive intact.
      BiotekError: A subclass matching what failed, if the instrument reports an error status. A
        command that is not answered reports no status, so nothing is raised for one.
    """
    if not self._open:
      raise fail(ErrorKind.LINK, f"{self._name} is not open", operation=operation or "request")
    timeout = self._timeout if command.timeout is None else command.timeout
    async with self._exchange:
      await self.purge()
      await self._write(command.to_bytes(), operation)
      await self._read_ack(operation)
      if not command.expects_reply:
        return b""
      header, payload = await self._read_reply(command, timeout, operation)
    if not command.reply_is_intact(header, payload):
      raise fail(
        ErrorKind.LINK,
        f"reply to command {command.number} did not arrive intact",
        operation=operation or "request",
      )
    status, answer = command.parse_reply(payload)
    raise_for_status(status, self._family, operation)
    return answer

  async def _write(self, frame: bytes, operation: str) -> None:
    """Write a frame, header first and payload second, as the instrument expects it.

    Args:
      frame: The header followed by the payload.
      operation: What is being attempted, for the message of any exception raised.

    Raises:
      LinkError: If the write fails.
    """
    header, payload = frame[:HEADER_LENGTH], frame[HEADER_LENGTH:]
    try:
      await self._io.write(header)
      if payload:
        await asyncio.sleep(_PAYLOAD_WRITE_DELAY)
        await self._io.write(payload)
    except Exception as error:
      raise fail(
        ErrorKind.LINK,
        f"writing to {self._name} on {self._io.port} failed: {error}",
        operation=operation or "write",
      ) from error
    logger.debug("[%s] sent %s", self._io.port, frame.hex())

  async def _read_ack(self, operation: str) -> None:
    """Wait for the instrument to acknowledge a command.

    Args:
      operation: What is being attempted, for the message of any exception raised.

    Raises:
      LinkError: If the instrument refuses the command or does not answer at all.
    """
    deadline = asyncio.get_running_loop().time() + self._timeout
    while asyncio.get_running_loop().time() < deadline:
      byte = await self._io.read(1)
      if not byte:
        await asyncio.sleep(_ACK_POLL_INTERVAL)
        continue
      if byte[0] == ACK:
        return
      if byte[0] == NAK:
        raise fail(
          ErrorKind.LINK,
          f"{self._name} refused the command",
          operation=operation or "acknowledge",
          code=NOT_ACKNOWLEDGED,
        )
      logger.debug("[%s] discarding %#04x while waiting to be acknowledged", self._io.port, byte[0])
    raise fail(
      ErrorKind.LINK,
      f"{self._name} did not acknowledge the command within {self._timeout:g}s",
      operation=operation or "acknowledge",
      code=NOT_ACKNOWLEDGED,
    )

  async def _read_reply(
    self, command: Command, timeout: float, operation: str
  ) -> tuple[Header, bytes]:
    """Read a reply frame: the header, then as many payload bytes as it declares.

    Args:
      command: The command that was sent, named in any exception raised.
      timeout: How long to wait for the header and the payload, each.
      operation: What is being attempted, for the message of any exception raised.

    Returns:
      The reply header and its payload.

    Raises:
      LinkError: If either part does not arrive in time.
    """
    raw = await self._io.read_exactly(HEADER_LENGTH, timeout)
    if len(raw) < HEADER_LENGTH:
      raise fail(
        ErrorKind.LINK,
        f"{self._name} answered command {command.number} with {len(raw)} of "
        f"{HEADER_LENGTH} header bytes within {timeout:g}s",
        operation=operation or "read",
        code=REPLY_TIMED_OUT,
      )
    header = Header.from_bytes(raw)
    payload = await self._io.read_exactly(header.payload_length, timeout)
    if len(payload) < header.payload_length:
      raise fail(
        ErrorKind.LINK,
        f"{self._name} answered command {command.number} with {len(payload)} of "
        f"{header.payload_length} payload bytes within {timeout:g}s",
        operation=operation or "read",
        code=REPLY_TIMED_OUT,
      )
    logger.debug("[%s] got %s %s", self._io.port, raw.hex(), payload.hex())
    return header, payload
