"""Shared Celigo controller protocol helpers."""

import asyncio
import contextlib
from typing import Awaitable, TypeVar

from pylabrobot.celigo.errors import CeligoError

_T = TypeVar("_T")


def require_payload_length(
  payload: bytes,
  minimum_byte_count: int,
  operation: str,
) -> None:
  """Reject a truncated controller payload before it is decoded."""
  if len(payload) < minimum_byte_count:
    raise CeligoError(
      f"Truncated {operation} response: expected at least {minimum_byte_count} payload bytes, "
      f"got {len(payload)}"
    )


async def complete_cleanup(operation: Awaitable[_T]) -> _T:
  """Finish a cleanup operation before propagating task cancellation."""
  task = asyncio.ensure_future(operation)
  try:
    return await asyncio.shield(task)
  except asyncio.CancelledError:
    with contextlib.suppress(Exception):
      await task
    raise
