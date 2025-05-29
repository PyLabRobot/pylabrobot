from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from .errors import ChannelizedError
from ..resources import TipSpot


@dataclass
class RetryContext:
  """Context passed to error handlers."""

  kwargs: Dict[str, Any]
  attempt: int = 0
  retry: bool = False


ErrorHandler = Callable[[Exception, RetryContext], Optional[Awaitable[None]]]


async def run_with_error_handlers(
  func: Callable[..., Awaitable[Any]],
  kwargs: Dict[str, Any],
  error_handlers: List[ErrorHandler],
):
  """Run ``func`` with ``kwargs`` and call ``error_handlers`` on errors."""

  attempt = 0
  while True:
    try:
      return await func(**kwargs)
    except Exception as e:  # noqa: BLE001
      ctx = RetryContext(kwargs=kwargs, attempt=attempt)
      for handler in error_handlers:
        result = handler(e, ctx)
        result = await result if inspect.isawaitable(result) else None
      if ctx.retry:
        kwargs = ctx.kwargs
        attempt += 1
        continue
      raise


def try_next_tip_spot(spots: Iterable[TipSpot]) -> ErrorHandler:
  """Return an error handler that retries pick up with the next tip spot."""

  iterator = iter(spots)

  def handler(error: Exception, ctx: RetryContext):
    if not isinstance(error, ChannelizedError):
      return None
    tip_spots = list(ctx.kwargs.get("tip_spots", []))
    use_channels = list(ctx.kwargs.get("use_channels", []))
    channel_to_idx = {ch: i for i, ch in enumerate(use_channels)}
    changed = False
    for channel in error.errors:
      idx = channel_to_idx.get(channel)
      if idx is None:
        continue
      try:
        tip_spots[idx] = next(iterator)
        changed = True
      except StopIteration:
        break
    if changed:
      ctx.kwargs["tip_spots"] = tip_spots
      ctx.retry = True
    return None

  return handler


def increase_liquid_height(increment: float) -> ErrorHandler:
  """Increase the liquid height for the failing channels and retry."""

  def handler(error: Exception, ctx: RetryContext):
    if not isinstance(error, ChannelizedError):
      return None
    liquid_height = list(ctx.kwargs.get("liquid_height", []))
    use_channels = list(ctx.kwargs.get("use_channels", []))
    if not liquid_height:
      return None
    channel_to_idx = {ch: i for i, ch in enumerate(use_channels)}
    changed = False
    for channel in error.errors:
      idx = channel_to_idx.get(channel)
      if idx is None:
        continue
      lh = liquid_height[idx]
      liquid_height[idx] = (lh or 0) + increment
      changed = True
    if changed:
      ctx.kwargs["liquid_height"] = liquid_height
      ctx.retry = True
    return None

  return handler
