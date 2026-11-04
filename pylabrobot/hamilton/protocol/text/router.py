"""Match asynchronous Hamilton text replies to the commands awaiting them.

Several commands can be in flight at once, and replies can arrive in completion
order rather than send order. A reader task owned by the router lifespan continuously reads
the link and hands each reply to its waiter; commands register before writing so a
fast reply cannot arrive before its waiter exists. Replies with an ID match that
ID; replies without one match the module and command prefix in send order.
Unmatched replies are dropped rather than assigned to another command.

The link can be any ``pylabrobot.io`` transport: STAR and Vantage use USB, a
heater shaker box uses USB, and STAR V uses a socket. The router borrows that
transport; its owner opens and closes it and enters the router's lifespan.

Command construction and reply interpretation belong to the owner, which supplies
the module identifier length, a reply ID parser, and a reply error checker.
"""

import logging
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Deque, Dict, Optional, Protocol

import anyio

from pylabrobot.hamilton.protocol.text.framing import read_id
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.io import IOBase
from pylabrobot.io.usb import USB

logger = logging.getLogger(__name__)


@dataclass
class HamiltonTask:
  """A command sent to the device and awaiting its response."""

  id_: Optional[int]
  cmd: str
  completed: anyio.Event
  response: Optional[str] = None
  error: Optional[BaseException] = None
  abandoned: bool = False


class ReplyReader(Protocol):
  """Produce one complete protocol reply from a transport-specific boundary."""

  async def read_reply(self) -> bytes:
    """Read one framed reply."""


class USBReplyReader:
  """Use an observed USB short packet (including ZLP) as a reply delimiter."""

  def __init__(self, io: USB) -> None:
    self.io = io
    self._initial = True
    self._invalid = False

  async def read_reply(self) -> bytes:
    """Reject incomplete or unaligned replies rather than routing partial bytes."""
    if self._invalid:
      raise ValueError("Incomplete Hamilton USB reply")
    first = self._initial
    self._initial = False
    self._invalid = True
    if first:
      start = await self.io.read_to_boundary(boundary="packet", consume_immediate_boundary=False)
      if start.data or not start.started_at_boundary or not start.ended_at_boundary:
        raise ValueError("Incomplete Hamilton USB reply")
    result = await self.io.read_to_boundary(boundary="short_packet_end")
    if (not first and not result.started_at_boundary) or not result.ended_at_boundary:
      raise ValueError("Incomplete Hamilton USB reply")
    self._invalid = False
    return result.data


class WriteNotDispatchedError(RuntimeError):
  """A transport-confirmed write failure before any command bytes were dispatched."""


class ReplyRouter:
  """Match replies from one owned reader to commands awaiting them."""

  def __init__(
    self,
    io: IOBase,
    module_id_length: int,
    parse_id: Callable[[str], Optional[int]],
    raise_for_error: Callable[[str], None],
    packet_read_timeout: float = 3,
    read_timeout: float = 30,
    reader: Optional[ReplyReader] = None,
  ):
    self.io = io
    self.module_id_length = module_id_length
    self._parse_id = parse_id
    self._raise_for_error = raise_for_error
    self.packet_read_timeout = packet_read_timeout
    self.read_timeout = read_timeout
    if reader is None:
      raise ValueError("A framed reply reader is required")
    self.reader = reader
    self.id_ = 0
    self._active = False
    self._stopping = False
    self._waiting_tasks_by_id: Dict[int, HamiltonTask] = {}
    self._waiting_tasks_by_command: Dict[str, Deque[HamiltonTask]] = {}

  def next_id(self) -> int:
    """Generate a command ID modulo 10000."""
    self.id_ += 1
    return self.id_ % 10000

  async def send(
    self,
    cmd: str,
    id_: Optional[int],
    write_timeout: Optional[int] = None,
    read_timeout: Optional[float] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Write a command and optionally wait for its matching reply."""
    await anyio.lowlevel.checkpoint_if_cancelled()
    if not self._active or self._stopping:
      raise RuntimeError("No active reply router is accepting commands.")
    if not wait:
      await self.io.write(cmd.encode(), timeout=write_timeout)
      return None
    if read_timeout is None:
      read_timeout = self.read_timeout
    task = self._register(id_=id_, cmd=cmd)
    dispatch_possible = False

    def mark_dispatch() -> None:
      """Record when USB begins the native write, before bytes can be sent."""
      nonlocal dispatch_possible
      dispatch_possible = True

    try:
      await anyio.lowlevel.checkpoint_if_cancelled()
      if isinstance(self.io, USB):
        await self.io.write(cmd.encode(), timeout=write_timeout, on_dispatch=mark_dispatch)
      else:
        dispatch_possible = True
        await self.io.write(cmd.encode(), timeout=write_timeout)
      try:
        with anyio.fail_after(read_timeout):
          await task.completed.wait()
      except TimeoutError:
        raise TimeoutError(f"Timeout while waiting for response to command {cmd}.") from None
      if task.error is not None:
        raise task.error
      return task.response
    except WriteNotDispatchedError:
      dispatch_possible = False
      raise
    finally:
      if (
        task.id_ is None
        and dispatch_possible
        and not task.completed.is_set()
        and not self._stopping
      ):
        task.abandoned = True
      else:
        self._remove(task)

  async def send_raw(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[float] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Send exactly the given command, extracting any embedded ID."""
    return await self.send(
      cmd=command,
      id_=read_id(command),
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
    )

  def _register(self, id_: Optional[int], cmd: str) -> HamiltonTask:
    """Register a waiter before a write can make its reply available."""
    task = HamiltonTask(id_=id_, cmd=cmd, completed=anyio.Event())
    if id_ is not None:
      if id_ in self._waiting_tasks_by_id:
        raise ValueError(f"A command with id {id_} is already pending.")
      self._waiting_tasks_by_id[id_] = task
    else:
      prefix = cmd[: self.module_id_length + 2]
      self._waiting_tasks_by_command.setdefault(prefix, deque()).append(task)
    return task

  def _remove(self, task: HamiltonTask) -> None:
    """Remove a pending command if it remains registered."""
    if task.id_ is not None:
      if self._waiting_tasks_by_id.get(task.id_) is task:
        del self._waiting_tasks_by_id[task.id_]
      return
    prefix = task.cmd[: self.module_id_length + 2]
    waiting = self._waiting_tasks_by_command.get(prefix)
    if waiting is None:
      return
    try:
      waiting.remove(task)
    except ValueError:
      return
    if not waiting:
      del self._waiting_tasks_by_command[prefix]

  def _find_waiter(self, response: str, response_id: Optional[int]) -> Optional[HamiltonTask]:
    """Take the waiter identified by a reply."""
    if response_id is not None:
      return self._waiting_tasks_by_id.pop(response_id, None)
    prefix = response[: self.module_id_length + 2]
    waiting = self._waiting_tasks_by_command.get(prefix)
    if not waiting:
      return None
    task = waiting.popleft()
    if not waiting:
      del self._waiting_tasks_by_command[prefix]
    return None if task.abandoned else task

  def _fail_waiters(self, error: BaseException) -> None:
    """Wake every pending command with the same reader failure."""
    tasks = list(self._waiting_tasks_by_id.values())
    for waiting in self._waiting_tasks_by_command.values():
      tasks.extend(waiting)
    self._waiting_tasks_by_id.clear()
    self._waiting_tasks_by_command.clear()
    for task in tasks:
      task.error = error
      task.completed.set()

  def _stop(self, scope: anyio.CancelScope) -> None:
    """Stop the reader and wake waiters before its task group joins."""
    self._stopping = True
    scope.cancel()
    self._fail_waiters(RuntimeError("Stopping the reply router."))

  async def _run(self, reader_scope: anyio.CancelScope) -> None:
    """Route replies until the lifespan ends or a fatal read failure reaches its owner."""
    failure: BaseException = RuntimeError("Stopping the reply router.")
    try:
      with reader_scope:
        while True:
          response = (await self.reader.read_reply()).decode("utf-8")
          try:
            response_id = self._parse_id(response)
          except ValueError as error:
            logger.warning("Could not parse response: %s (%s)", response, error)
            continue
          task = self._find_waiter(response, response_id)
          if task is None:
            logger.warning("nothing was waiting for this reply, and it was dropped: %s", response)
            continue
          try:
            self._raise_for_error(response)
          except Exception as error:
            task.error = error
          else:
            task.response = response
          task.completed.set()
    except BaseException as error:
      failure = error
      raise
    finally:
      self._stopping = True
      self._fail_waiters(failure)

  @asynccontextmanager
  async def lifespan(self) -> AsyncIterator["ReplyRouter"]:
    """Own the reply reader while borrowing an active transport."""
    if self._active:
      raise RuntimeError("Reply router already has an active lifespan")
    if isinstance(self.reader, USBReplyReader):
      self.reader = USBReplyReader(self.reader.io)
    self._active = True
    self._stopping = False
    try:
      async with AsyncExitStackWithShielding() as stack:
        group = await stack.enter_task_group()
        reader_scope = anyio.CancelScope(shield=True)
        stack.callback(self._stop, reader_scope)
        group.start_soon(self._run, reader_scope)
        yield self
    finally:
      self._stopping = True
      self._active = False
