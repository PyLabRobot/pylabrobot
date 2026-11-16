"""Loop-agnostic INCO test double and log capture helpers for CERTUS FLEX unit tests."""

import contextlib
import logging
from typing import Awaitable, Callable, Iterator, Optional, Sequence, Union

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan

from ._inco import Argument, IncoError


class _ListHandler(logging.Handler):
  """Collect log records directly on a logger when parent propagation is disabled."""

  def __init__(self, level: int) -> None:
    """Initialize an empty record list at ``level``."""
    super().__init__(level=level)
    self.records: list[logging.LogRecord] = []

  def emit(self, record: logging.LogRecord) -> None:
    """Append ``record`` to the in-memory list."""
    self.records.append(record)


@contextlib.contextmanager
def capture_logs(logger_name: str, level: int) -> Iterator[list[logging.LogRecord]]:
  """Capture log records emitted under ``logger_name`` at or above ``level``."""
  target = logging.getLogger(logger_name)
  previous_level = target.level
  handler = _ListHandler(level)
  target.setLevel(min(previous_level, level) if previous_level != logging.NOTSET else level)
  target.addHandler(handler)
  try:
    yield handler.records
  finally:
    target.removeHandler(handler)
    target.setLevel(previous_level)


class FakeInco(HasLifespan):
  """In-memory INCO session recording calls without sockets or AsyncMock."""

  def __init__(
    self,
    *,
    strings: Optional[Sequence[Union[str, BaseException]]] = None,
    default_number: float = 4.0,
  ) -> None:
    """Create a disconnected in-memory INCO session double."""
    super().__init__()
    self.strings: list[Union[str, BaseException]] = list(strings or [])
    self.default_number = default_number
    self.number_handler: Optional[Callable[[str], Awaitable[float]]] = None
    self.call_handler: Optional[Callable[..., Awaitable[None]]] = None
    self.string_calls: list[str] = []
    self.number_calls: list[str] = []
    self.put_calls: list[tuple[str, float]] = []
    self.proc_calls: list[tuple[str, tuple[Argument, ...]]] = []
    self.open_count = 0
    self.close_count = 0
    self.reconnect_count = 0
    self._connected = False

  @property
  def is_connected(self) -> bool:
    """Whether the simulated INCO session is active and connected."""
    return self._connected and self.is_active

  def _disconnect_current(self) -> None:
    """Mark the current connection closed once."""
    if self._connected:
      self._connected = False
      self.close_count += 1

  def _on_close(self) -> None:
    """Record lifespan exit."""
    self._disconnect_current()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Record lifespan entry and register teardown accounting."""
    await super()._enter_lifespan(stack)
    self.open_count += 1
    self._connected = True
    stack.callback(self._on_close)

  async def reconnect(self) -> None:
    """Reopen the simulated INCO connection within an active lifespan."""
    await anyio.lowlevel.checkpoint()
    if not self.is_active:
      raise RuntimeError("INCO connection is closed; enter its lifespan first")
    self._disconnect_current()
    self.reconnect_count += 1
    self.open_count += 1
    self._connected = True

  def _require_connected(self) -> None:
    """Reject calls when the simulated connection is closed."""
    if not self.is_connected:
      raise RuntimeError("INCO connection is closed; enter its lifespan first")

  async def get_string(self, path: str) -> str:
    """Return the next configured string reply for ``path``."""
    await anyio.lowlevel.checkpoint()
    self._require_connected()
    self.string_calls.append(path)
    if not self.strings:
      return ""
    item = self.strings.pop(0)
    if isinstance(item, BaseException):
      if not isinstance(item, IncoError):
        self._disconnect_current()
      raise item
    return item

  async def get_number(self, path: str) -> float:
    """Return the configured numeric reply for ``path``."""
    await anyio.lowlevel.checkpoint()
    self._require_connected()
    self.number_calls.append(path)
    if self.number_handler is not None:
      try:
        return await self.number_handler(path)
      except IncoError:
        raise
      except BaseException:
        self._disconnect_current()
        raise
    return self.default_number

  async def put_number(self, path: str, value: float) -> None:
    """Record a numeric variable write."""
    await anyio.lowlevel.checkpoint()
    self._require_connected()
    self.put_calls.append((path, value))

  async def call(self, path: str, *arguments: Argument) -> None:
    """Record a procedure call and invoke ``call_handler`` when set."""
    await anyio.lowlevel.checkpoint()
    self._require_connected()
    self.proc_calls.append((path, arguments))
    if self.call_handler is not None:
      try:
        await self.call_handler(path, *arguments)
      except IncoError:
        raise
      except BaseException:
        self._disconnect_current()
        raise
