"""Bounded message updates and error mapping shared by optional RPC capabilities."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Generic, TypeVar, cast

import grpc
import sniffio
from google.protobuf.message import Message

from pylabrobot.rpc.errors import BusyError, CapacityError, DataLossError, StateError, StorageError

M = TypeVar("M", bound=Message)
API_VERSION = "pylabrobot.rpc.v1"
logger = logging.getLogger(__name__)


def build_channel_options(
  maximum_message_bytes: int = 4 * 1024 * 1024,
) -> tuple[tuple[str, int], ...]:
  """Set explicit message bounds and require applications to reconcile ambiguous writes."""
  if maximum_message_bytes <= 0:
    raise ValueError("maximum_message_bytes must be positive")
  return (
    ("grpc.max_send_message_length", maximum_message_bytes),
    ("grpc.max_receive_message_length", maximum_message_bytes),
    ("grpc.enable_retries", 0),
  )


def copy_message(value: M) -> M:
  """Return a separate protobuf value, preserving its concrete message type."""
  result = type(value)()
  result.CopyFrom(value)
  return result


def require_asyncio(owner: str) -> None:
  """Reject non-asyncio backends before acquiring a `grpc.aio` resource.

  Args:
    owner: Name of the integration, reported in the error.

  Raises:
    RuntimeError: If the running AnyIO backend is not asyncio.
  """
  backend = sniffio.current_async_library()
  if backend != "asyncio":
    raise RuntimeError(
      f"{owner} only supports the asyncio backend, because `grpc.aio` is asyncio-only "
      f"(running under {backend!r})."
    )


def require_device(actual: str, expected: str) -> None:
  """Reject a request addressed to a different instrument."""
  if actual != expected:
    raise KeyError(actual)


@asynccontextmanager
async def translate_rpc_errors(context: grpc.aio.ServicerContext) -> AsyncIterator[None]:
  """Translate application failures into consistent gRPC status codes."""
  try:
    yield
  except (ValueError, TypeError) as exc:
    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
  except KeyError:
    await context.abort(grpc.StatusCode.NOT_FOUND, "requested device or result was not found")
  except (BusyError, CapacityError) as exc:
    await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(exc))
  except StateError as exc:
    await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
  except DataLossError as exc:
    await context.abort(grpc.StatusCode.DATA_LOSS, str(exc))
  except StorageError as exc:
    await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
  except Exception:
    logger.exception("RPC operation failed")
    await context.abort(grpc.StatusCode.INTERNAL, "internal device service error")


class Latest(Generic[M]):
  """Keep one independent latest snapshot per subscriber, with bounded admission."""

  def __init__(self, value: M | None = None, limit: int = 16) -> None:
    """Set an optional initial snapshot and maximum concurrent subscriptions."""
    if limit < 1:
      raise ValueError("subscription limit must be positive")
    self.value = copy_message(value) if value is not None else None
    self.limit = limit
    self.queues: set[asyncio.Queue[M]] = set()

  def publish(self, value: M) -> None:
    """Replace pending snapshots; consumers reconcile skipped intermediate revisions."""
    self.value = copy_message(value)
    for queue in self.queues:
      if queue.full():
        queue.get_nowait()
      queue.put_nowait(copy_message(value))

  @contextmanager
  def subscribe(self) -> Iterator[asyncio.Queue[M]]:
    """Supply current state immediately and remove the subscription when closed."""
    if len(self.queues) >= self.limit:
      raise CapacityError("too many subscriptions")
    queue: asyncio.Queue[M] = asyncio.Queue(maxsize=1)
    self.queues.add(queue)
    if self.value is not None:
      queue.put_nowait(copy_message(self.value))
    try:
      yield queue
    finally:
      self.queues.discard(queue)


class Updates(Generic[M]):
  """Read typed snapshots from one client-owned RPC stream."""

  def __init__(self, call: grpc.aio.UnaryStreamCall[Any, M]) -> None:
    """Wrap a stream without changing the remote operation's lifetime."""
    self.call = call

  async def get(self) -> M:
    """Read a snapshot, raising EOFError when the server ends the stream."""
    value = await self.call.read()
    if value is grpc.aio.EOF:
      raise EOFError("update stream finished")
    return cast(M, value)
