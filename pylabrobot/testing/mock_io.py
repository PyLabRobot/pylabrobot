"""Loop-agnostic asynchronous I/O test doubles."""

import inspect
from typing import Any, Dict, List, Tuple, cast

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.io import IOBase

Call = Tuple[Tuple[Any, ...], Dict[str, Any]]


def _reserve_effect(side_effect: Any) -> Any:
  """Take the side effect belonging to one call without suspending.

  Args:
    side_effect: Configured side effect, which may be a queue of effects.

  Returns:
    The effect this call must use.

  Raises:
    IndexError: If a configured queue of effects is exhausted.
  """
  if isinstance(side_effect, list):
    if not side_effect:
      raise IndexError("Mock side effect list exhausted")
    return side_effect.pop(0)
  return side_effect


class CustomReadMock:
  """Record asynchronous reads and provide deterministic side effects."""

  def __init__(self) -> None:
    """Initialize empty call and side-effect storage."""
    self.calls: List[Call] = []
    self.side_effect: Any = None

  async def __call__(self, *args: Any, **kwargs: Any) -> bytes:
    """Record a read and return or raise its configured side effect."""
    self.calls.append((args, dict(kwargs)))
    effect = _reserve_effect(self.side_effect)
    try:
      await anyio.lowlevel.checkpoint()
    except anyio.get_cancelled_exc_class():
      if isinstance(self.side_effect, list):
        self.side_effect.insert(0, effect)
      raise
    if isinstance(effect, BaseException):
      raise effect
    if effect is None:
      await anyio.sleep_forever()
      raise AssertionError("Unreachable after sleep_forever")
    if callable(effect):
      result = effect(*args, **kwargs)
      if inspect.isawaitable(result):
        result = await result
      return cast(bytes, result)
    return cast(bytes, effect)

  def reset_mock(self) -> None:
    """Clear recorded calls and configured side effects."""
    self.calls.clear()
    self.side_effect = None


class CustomWriteMock:
  """Record asynchronous writes and provide deterministic side effects."""

  def __init__(self) -> None:
    """Initialize empty call and side-effect storage."""
    self.calls: List[Call] = []
    self.side_effect: Any = None

  async def __call__(self, data: bytes, *args: Any, **kwargs: Any) -> Any:
    """Record a write and return or raise its configured side effect."""
    call_args = (data,) + args
    self.calls.append((call_args, dict(kwargs)))
    effect = _reserve_effect(self.side_effect)
    try:
      await anyio.lowlevel.checkpoint()
    except anyio.get_cancelled_exc_class():
      if isinstance(self.side_effect, list):
        self.side_effect.insert(0, effect)
      raise
    with anyio.CancelScope(shield=True):
      if isinstance(effect, BaseException):
        raise effect
      if callable(effect):
        result = effect(data, *args, **kwargs)
        if inspect.isawaitable(result):
          return await result
        return result
      return effect

  def reset_mock(self) -> None:
    """Clear recorded calls and configured side effects."""
    self.calls.clear()
    self.side_effect = None


class MockIO(IOBase):
  """Provide recorded asynchronous reads and writes through an I/O interface."""

  def __init__(self) -> None:
    """Initialize independent read and write test doubles."""
    super().__init__()
    self._read = CustomReadMock()
    self._write = CustomWriteMock()

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
  ) -> None:
    """Enter the stateless mock lifespan without acquiring resources."""
    await super()._enter_lifespan(stack)

  @property
  def read(self) -> CustomReadMock:
    """Return the configured asynchronous read double."""
    return self._read

  @property
  def write(self) -> CustomWriteMock:
    """Return the configured asynchronous write double."""
    return self._write
