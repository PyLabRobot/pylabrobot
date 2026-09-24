"""The dialogue with a Hamilton machine: which reply answers which command.

Hamilton machines answer asynchronously - several commands can be in flight at once and replies
come back in completion order - so a command cannot simply write and then read. Every command
carries an id, a background thread reads continuously, and each reply is handed back to the
command waiting on it.

The link itself is any `pylabrobot.io` transport: a STAR and a Vantage are reached over USB, a
heater shaker box over USB, a STAR V over a socket. Nothing here depends on which.

Assembling a command and understanding what a reply says are not this class's business. It is
given three things by whoever owns it: how long a module identifier is, how to read the id out of
a reply, and how to raise when a reply reports an error.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from pylabrobot.hamilton.protocol.text.framing import read_id
from pylabrobot.io.io import IOBase

logger = logging.getLogger(__name__)


@dataclass
class HamiltonTask:
  """A command that has been sent, awaiting a response."""

  id_: Optional[int]
  loop: asyncio.AbstractEventLoop
  fut: asyncio.Future
  cmd: str
  timeout_time: float


class ReplyRouter:
  """Holds the link to a Hamilton machine and matches replies to the commands awaiting them."""

  def __init__(
    self,
    io: IOBase,
    module_id_length: int,
    parse_id: Callable[[str], Optional[int]],
    raise_for_error: Callable[[str], None],
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
  ):
    """
    Args:
      io: The transport handle for the machine.
      module_id_length: Number of characters in a module identifier.
      parse_id: Read the command id out of a reply, or None if it carries none.
      raise_for_error: Raise if a reply reports an error, otherwise return.
      packet_read_timeout: Timeout in seconds for reading a single packet.
      read_timeout: Timeout in seconds for reading a full response.
    """
    self.io = io
    self.module_id_length = module_id_length
    self._parse_id = parse_id
    self._raise_for_error = raise_for_error

    # TODO: not used by the reader yet.
    self.packet_read_timeout = packet_read_timeout
    self.read_timeout = read_timeout

    self.id_ = 0
    self._reading_thread: Optional[threading.Thread] = None
    self._reading_thread_stop = threading.Event()
    # The reading thread and the event loop both change this, so every change goes through the lock.
    self._waiting_tasks: List[HamiltonTask] = []
    self._waiting_tasks_lock = threading.Lock()

  def start(self) -> None:
    """Begin reading replies. The caller opens the transport first."""
    self._reading_thread_stop.clear()
    self._reading_thread = threading.Thread(target=self._reading_thread_main, daemon=True)
    self._reading_thread.start()

  def stop(self) -> None:
    """Stop reading and fail every command still waiting. The caller closes the transport."""
    self._reading_thread_stop.set()
    if self._reading_thread is not None:
      self._reading_thread.join(timeout=10)
      self._reading_thread = None
    with self._waiting_tasks_lock:
      waiting, self._waiting_tasks = self._waiting_tasks, []
    for task in waiting:
      task.loop.call_soon_threadsafe(
        task.fut.set_exception, RuntimeError("Stopping the reply router.")
      )

  def next_id(self) -> int:
    """continuously generate unique ids 0 <= x < 10000."""
    self.id_ += 1
    return self.id_ % 10000

  async def send(
    self,
    cmd: str,
    id_: Optional[int],
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Write an assembled command and return the reply that answers it.

    Args:
      cmd: The assembled command string.
      id_: The id the command carries, used to recognise its reply. When None, the reply is
        matched on the module and command instead.
      write_timeout: Write timeout in seconds. If None, the io's own timeout is used.
      read_timeout: Read timeout in seconds. If None, `self.read_timeout` is used.
      wait: If False, return None immediately after sending.

    Returns:
      The reply, or None when `wait` is False.
    """
    if not wait:
      await self.io.write(cmd.encode(), timeout=write_timeout)
      return None

    if read_timeout is None:
      read_timeout = self.read_timeout

    # Wait for the reply before asking for it. The reading thread runs alongside this one, and a
    # device can answer before a task registered after the write would exist - the reply then
    # arrives with nothing waiting for it, is dropped, and the command times out with its answer
    # already gone past. Registering first closes that window; the task is taken back off again if
    # the write never happens.
    loop = asyncio.get_event_loop()
    fut: asyncio.Future[str] = loop.create_future()
    task = self._start_reading(id_, loop, fut, cmd, read_timeout)
    try:
      await self.io.write(cmd.encode(), timeout=write_timeout)
    except BaseException:
      with self._waiting_tasks_lock:
        if task in self._waiting_tasks:
          self._waiting_tasks.remove(task)
      raise
    return await fut

  async def send_raw(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Write a command string exactly as given, reading its id back out of it."""
    return await self.send(
      cmd=command,
      id_=read_id(command),
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
    )

  def _start_reading(
    self,
    id_: Optional[int],
    loop: asyncio.AbstractEventLoop,
    fut: asyncio.Future,
    cmd: str,
    timeout: int,
  ) -> HamiltonTask:
    """Submit a task to the reading thread, and hand it back so a caller can take it off again.

    Returns:
      The task now waiting for a reply.
    """

    timeout_time = time.time() + timeout
    task = HamiltonTask(id_=id_, loop=loop, fut=fut, cmd=cmd, timeout_time=timeout_time)
    with self._waiting_tasks_lock:
      self._waiting_tasks.append(task)

    if self._reading_thread is None or not self._reading_thread.is_alive():
      self._reading_thread_stop.clear()
      self._reading_thread = threading.Thread(target=self._reading_thread_main, daemon=True)
      self._reading_thread.start()

    return task

  def _reading_thread_main(self) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(self._continuously_read())

  async def _continuously_read(self) -> None:
    """Continuously read from the USB port until stop is requested.

    Tasks are stored in the `self._waiting_tasks` list, and contain a future that will be
    completed when the task is finished. Tasks are submitted to the list using the
    `self._start_reading` method.

    On each iteration, read the USB port. If a response is received, parse it and check if it is
    relevant to any of the tasks. If so, complete the future and remove the task from the
    list. If a task has timed out, complete the future with a `TimeoutError`.
    """

    while not self._reading_thread_stop.is_set():
      with self._waiting_tasks_lock:
        now = time.time()
        timed_out = [task for task in self._waiting_tasks if now > task.timeout_time]
        for task in timed_out:
          self._waiting_tasks.remove(task)
        waiting = len(self._waiting_tasks)
      for task in timed_out:
        logger.warning("Timeout while waiting for response to command %s.", task.cmd)
        task.loop.call_soon_threadsafe(
          task.fut.set_exception,
          TimeoutError(f"Timeout while waiting for response to command {task.cmd}."),
        )

      if waiting == 0:
        await asyncio.sleep(0.01)
        continue

      try:
        resp = (await self.io.read()).decode("utf-8")
      except TimeoutError:
        continue

      if resp == "":
        continue

      # Parse response.
      try:
        response_id = self._parse_id(resp)
      except ValueError as e:
        logger.warning("Could not parse response: %s (%s)", resp, e)
        continue

      module_and_command = resp[: self.module_id_length + 2]
      with self._waiting_tasks_lock:
        matched = next(
          (
            task
            for task in self._waiting_tasks
            # if the command has no id, we have to check the command itself
            if response_id == task.id_
            or (task.id_ is None and task.cmd.startswith(module_and_command))
          ),
          None,
        )
        if matched is not None:
          self._waiting_tasks.remove(matched)

      if matched is None:
        # Deliberately not guessed at. Handing it to whichever outstanding command shares its
        # module and code answers that command with something that was never its answer, and a
        # reply arriving with nothing waiting for it is worth seeing rather than repairing.
        logger.warning("nothing was waiting for this reply, and it was dropped: %s", resp)
        continue

      try:
        self._raise_for_error(resp)
      except Exception as e:
        matched.loop.call_soon_threadsafe(matched.fut.set_exception, e)
      else:
        matched.loop.call_soon_threadsafe(matched.fut.set_result, resp)
