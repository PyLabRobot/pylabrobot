"""Shared Celigo controller protocol helpers."""

from typing import Awaitable, TypeVar

import anyio

from pylabrobot.revvity.celigo.errors import CeligoError

_T = TypeVar("_T")


def validate_payload_length(
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
  with anyio.CancelScope(shield=True):
    result = await operation
  await anyio.lowlevel.checkpoint()
  return result
