"""Shared validation for Celigo controller protocol responses."""

import asyncio
import contextlib
import struct
from dataclasses import dataclass
from typing import Awaitable, TypeVar

from pylabrobot.celigo.errors import CeligoError

_T = TypeVar("_T")

# Controller response shared by the galvo and laser-targeting subsystems.
TARGETING_STATUS_OPCODE = 12


@dataclass(frozen=True)
class TargetingControllerStatus:
  """Decoded wire response shared by the galvo and laser components."""

  x_busy: bool
  y_busy: bool
  x_dac_count: int
  y_dac_count: int
  fire_table_size: int
  points_loaded: int
  fire_table_index: int
  firing_status: int
  capture_armed: bool
  capture_table_size: int


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


def parse_targeting_controller_status(payload: bytes) -> TargetingControllerStatus:
  """Decode the controller's shared galvo/laser targeting-status response."""
  require_payload_length(payload, 23, "targeting controller status")
  x_ready, y_ready, x_dac_count, y_dac_count = struct.unpack_from(">BBHH", payload, 0)
  fire_table_size, points_loaded, fire_table_index = struct.unpack_from(">iii", payload, 6)
  firing_status = payload[18]
  capture_armed, capture_table_size = struct.unpack_from(">hh", payload, 19)
  return TargetingControllerStatus(
    x_busy=x_ready == 0,
    y_busy=y_ready == 0,
    x_dac_count=x_dac_count,
    y_dac_count=y_dac_count,
    fire_table_size=fire_table_size,
    points_loaded=points_loaded,
    fire_table_index=fire_table_index,
    firing_status=firing_status,
    capture_armed=capture_armed != 0,
    capture_table_size=capture_table_size,
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
