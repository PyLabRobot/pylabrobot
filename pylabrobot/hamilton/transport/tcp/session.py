"""Ownership, correlation, and lifetime of one Hamilton TCP connection.

Responses must echo the request's HARP sequence and reverse its addresses.
Sequence numbers wrap after 256 requests per destination. Reuse after a completed
transaction assumes the peer sends no delayed duplicate terminal responses;
the wire has no generation field with which to distinguish such duplicates.
An uncertain transaction prevents all further writes in that session.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Mapping, Optional, TypeVar, Union

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand, hamilton_error_for_entry
from pylabrobot.hamilton.transport.tcp.error_tables import HC_RESULT_PROTOCOL
from pylabrobot.hamilton.transport.tcp.events import EventSubscription, OverflowPolicy
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError, parse_hamilton_error_entries
from pylabrobot.hamilton.transport.tcp.introspection import HamiltonIntrospection, ObjectRegistry
from pylabrobot.hamilton.transport.tcp.messages import CommandResponse, RegistrationResponse
from pylabrobot.hamilton.transport.tcp.packets import Address, HarpPacket, IpPacket
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.io.binary import Reader
from pylabrobot.io.socket import Socket
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError

logger = logging.getLogger(__name__)
ResultT = TypeVar("ResultT")


class SessionState(Enum):
  """Whether the connection may start or finish a transaction."""

  CLOSED = auto()
  CONNECTING = auto()
  READY = auto()
  WAITING = auto()
  UNCERTAIN = auto()


@dataclass(frozen=True)
class PendingTransaction:
  """One request's immutable correlation fields and response handoff."""

  destination: Address
  source: Address
  sequence: int
  response: asyncio.Future[CommandResponse]

  def matches(self, message: CommandResponse) -> bool:
    """Require the peer to echo the sequence and reverse both addresses."""
    return (
      message.harp.src == self.destination
      and message.harp.dst == self.source
      and message.harp.seq == self.sequence
    )


_TERMINAL_ACTIONS = frozenset(
  (
    Hoi2Action.STATUS_RESPONSE,
    Hoi2Action.STATUS_EXCEPTION,
    Hoi2Action.COMMAND_RESPONSE,
    Hoi2Action.COMMAND_EXCEPTION,
    Hoi2Action.INVALID_ACTION_RESPONSE,
    Hoi2Action.STATUS_WARNING,
    Hoi2Action.COMMAND_WARNING,
  )
)


class TCPSession:
  """Own one connection's I/O, transactions, discovery, and event subscriptions."""

  def __init__(
    self,
    io: Socket,
    read_timeout: float = 300.0,
    error_codes: Optional[Mapping[tuple[int, int, int, int, int], str]] = None,
  ):
    """Create an unopened session with independent caches and protocol state."""
    self._io = io
    self._read_timeout = read_timeout
    self._error_codes = dict(error_codes or {})
    self.registry = ObjectRegistry()
    self.global_object_addresses: list[Address] = []
    self.client_id: Optional[int] = None
    self.introspection = HamiltonIntrospection(self.registry, self.global_object_addresses, self)
    self.state = SessionState.CLOSED
    self.client_address: Optional[Address] = None
    self.sequence_numbers: dict[Address, int] = {}
    self.command_lock = asyncio.Lock()
    self.pending: Optional[PendingTransaction] = None
    self.reader_task: Optional[asyncio.Task[None]] = None
    self._write_task: Optional[asyncio.Task[None]] = None
    self._subscriptions: set[EventSubscription] = set()

  def require_active(self) -> None:
    """Reject even cached discovery access on an expired or uncertain session."""
    if self.state not in (SessionState.READY, SessionState.WAITING):
      raise ConnectionError(
        f"Hamilton TCP session is {self.state.name.lower()} - call stop() then setup()"
      )

  def events(self, capacity: int = 64, overflow: OverflowPolicy = "error") -> EventSubscription:
    """Subscribe to events for this connection, with explicit overflow behavior."""
    self.require_active()
    subscription = EventSubscription(capacity, overflow, self._subscriptions.discard)
    self._subscriptions.add(subscription)
    return subscription

  async def execute(
    self, command: TCPCommand[ResultT], *, read_timeout: Optional[float] = None
  ) -> ResultT:
    """Exchange and decode without diagnostic I/O or hidden retries."""
    response = await self.exchange(command, read_timeout=read_timeout)
    action = Hoi2Action(response.hoi.action_code)
    payload, prefix = command._strip_warning_prefix(response)
    is_exception = action in (
      Hoi2Action.STATUS_EXCEPTION,
      Hoi2Action.COMMAND_EXCEPTION,
      Hoi2Action.INVALID_ACTION_RESPONSE,
    )
    if is_exception:
      indexed_entries = dict(enumerate(parse_hamilton_error_entries(response.hoi.params)))
    else:
      indexed_entries = {i: entry for i, entry in enumerate(prefix) if not entry.is_success}
    if is_exception or indexed_entries:
      errors: dict[int, Exception] = {}
      channels: dict[int, Exception] = {}
      for index, entry in indexed_entries.items():
        key = (entry.module_id, entry.node_id, entry.object_id, entry.interface_id, entry.result)
        fallback = (entry.module_id, entry.node_id, entry.object_id, entry.action_id, entry.result)
        description = (
          self._error_codes.get(key)
          or self._error_codes.get(fallback)
          or HC_RESULT_PROTOCOL.get(entry.result)
          or f"HC_RESULT=0x{entry.result:04X}"
        )
        error = hamilton_error_for_entry(entry, description)
        errors[index] = error
        channel = command._channel_index_for_entry(index, entry)
        if channel is not None:
          channels.setdefault(channel, error)
      if command.uses_physical_channels and channels:
        raise ChannelizedError(
          errors=channels,
          raw_response=response.hoi.params,
          hoi_entries=list(indexed_entries.values()),
          hoi_exceptions=errors,
        )
      raise HoiError(
        exceptions=errors,
        entries=list(indexed_entries.values()),
        raw_response=response.hoi.params,
        action=action,
      )
    return command.parse_response_parameters(payload)

  async def exchange(
    self, command: TCPCommand[object], *, read_timeout: Optional[float] = None
  ) -> CommandResponse:
    """Return the complete matching terminal frame, including firmware errors.

    No decoding, suppression, diagnostic queries, or retransmission occurs here.
    The request itself specifies its HOI action.
    """
    timeout = self._read_timeout if read_timeout is None else read_timeout
    return await self.transact(command, timeout)

  def allocate_sequence(self, destination: Address) -> int:
    """Allocate a per-destination eight-bit sequence number."""
    sequence = (self.sequence_numbers.get(destination, 0) + 1) % 256
    self.sequence_numbers[destination] = sequence
    return sequence

  def require_ready(self) -> None:
    """Reject commands on closed, connecting, or uncertain sessions."""
    if self.state is not SessionState.READY:
      raise ConnectionError(
        f"{self._io._unique_id} session is {self.state.name.lower()} - call stop() then setup()"
      )

  def start_reader(self) -> None:
    """Transfer socket ownership to the background reader after the handshake."""
    if self.state is not SessionState.CONNECTING:
      raise RuntimeError("Reader can only start after connecting")
    self.state = SessionState.READY
    self.reader_task = asyncio.create_task(self._read_loop())

  async def read_message(
    self, timeout: Optional[float] = None
  ) -> Optional[Union[RegistrationResponse, CommandResponse]]:
    """Consume a complete length-prefixed frame before parsing its envelope."""
    size_data = await self._io.read_exact(2, timeout=timeout)
    if self.state is SessionState.CLOSED:
      raise ConnectionError("Hamilton TCP session stopped")
    payload = await self._io.read_exact(Reader(size_data).u16(), timeout=timeout)
    data = size_data + payload
    ip = IpPacket.unpack(data)
    if ip.protocol != 6:
      logger.warning("Dropping unexpected IP protocol %d", ip.protocol)
      return None
    harp = HarpPacket.unpack(ip.payload)
    if harp.protocol == 3:
      return RegistrationResponse.from_bytes(data)
    if harp.protocol != 2 or not harp.payload:
      logger.debug("Dropping HARP control frame or protocol %d", harp.protocol)
      return None
    return CommandResponse.from_bytes(data)

  async def _read_loop(self) -> None:
    """Read independently of request deadlines and route only recognized frames."""
    try:
      while self.state is not SessionState.CLOSED:
        try:
          message = await self.read_message(timeout=float("inf"))
        except ValueError as exc:
          logger.warning("Skipping unparsable frame: %s", exc)
          continue
        if isinstance(message, CommandResponse):
          self.deliver(message)
    except asyncio.CancelledError:
      self.fail(ConnectionError("Hamilton TCP reader cancelled"))
      raise
    except Exception as exc:
      self.fail(exc)
      logger.warning("Hamilton TCP reader stopped: %s", exc)

  def deliver(self, message: CommandResponse) -> None:
    """Route events and hand only matching terminal responses to the caller."""
    if self.state is SessionState.CLOSED:
      return
    action = message.hoi.action_code
    if action == Hoi2Action.EVENT:
      for subscription in tuple(self._subscriptions):
        subscription._publish(message)
      return
    pending = self.pending
    if pending is None or pending.response.done() or not pending.matches(message):
      logger.warning(
        "Dropping unmatched response from %s to %s seq=%d action=%#x",
        message.harp.src,
        message.harp.dst,
        message.harp.seq,
        action,
      )
      return
    if action == Hoi2Action.COMMAND_ACK:
      return
    if action not in _TERMINAL_ACTIONS:
      logger.warning("Dropping nonterminal HOI action %#x", action)
      return
    pending.response.set_result(message)

  def fail(self, exc: Exception) -> None:
    """Make an uncertain connection unusable and wake its waiting caller."""
    if self.state is SessionState.CLOSED:
      return
    self.state = SessionState.UNCERTAIN
    for subscription in tuple(self._subscriptions):
      subscription._finish(exc)
    pending = self.pending
    self.pending = None
    if pending is not None and not pending.response.done():
      pending.response.set_exception(exc)
    if self._write_task is not None and not self._write_task.done():
      self._write_task.cancel()

  async def transact(self, command: TCPCommand[object], read_timeout: float) -> CommandResponse:
    """Write once and wait for this request's terminal response under the lock.

    Cancellation while waiting for the lock leaves the session alone. Once the
    write begins, any failure without an observed terminal response makes the
    session uncertain. Decoding happens after the transaction releases the lock.
    """
    async with self.command_lock:
      self.require_ready()
      if self.client_address is None:
        raise RuntimeError("Client address missing after handshake")
      sequence = self.allocate_sequence(command.dest)
      message = command.build(src=self.client_address, seq=sequence)
      response: asyncio.Future[CommandResponse] = asyncio.get_running_loop().create_future()
      pending = PendingTransaction(command.dest, self.client_address, sequence, response)
      self.pending = pending
      self.state = SessionState.WAITING
      deadline: Optional[asyncio.TimerHandle] = None
      write_task = asyncio.create_task(self._io.write(message))
      self._write_task = write_task

      def expire() -> None:
        """End only this transaction's wait without cancelling its caller."""
        if not response.done():
          response.set_exception(asyncio.TimeoutError("Timeout waiting for Hamilton TCP response"))

      try:
        try:
          await write_task
        except asyncio.CancelledError:
          if self.state in (SessionState.CLOSED, SessionState.UNCERTAIN):
            if response.done() and not response.cancelled():
              response.result()
            raise ConnectionError("Hamilton TCP session ended during write") from None
          raise
        except Exception as exc:
          self.fail(exc)
          raise
        if self.state is not SessionState.WAITING:
          raise ConnectionError("Hamilton TCP session ended during write")
        deadline = asyncio.get_running_loop().call_later(read_timeout, expire)
        return await asyncio.shield(response)
      finally:
        if deadline is not None:
          deadline.cancel()
        # A recorded terminal response establishes completion even if cancellation
        # won the race to wake the caller. Never resurrect a failed/stopped session.
        completed = response.done() and not response.cancelled() and response.exception() is None
        if self.state is SessionState.WAITING:
          self.state = SessionState.READY if completed else SessionState.UNCERTAIN
        if self.state is SessionState.UNCERTAIN:
          for subscription in tuple(self._subscriptions):
            subscription._finish(ConnectionError("Hamilton TCP transaction outcome is uncertain"))
        if self.pending is pending:
          self.pending = None
        if not response.done():
          response.cancel()
        if self._write_task is write_task:
          self._write_task = None

  async def stop(self) -> None:
    """Fail waiters and finish owned tasks before closing this session's socket.

    Subscriptions end immediately and discard buffered events.
    """
    for subscription in tuple(self._subscriptions):
      subscription._finish()
    self.fail(ConnectionError("Hamilton TCP session stopped - call setup()"))
    self.state = SessionState.CLOSED
    tasks = [task for task in (self.reader_task, self._write_task) if task is not None]
    for task in tasks:
      task.cancel()
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
    await self._io.stop()
