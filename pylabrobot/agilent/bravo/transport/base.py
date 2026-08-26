"""Synchronous byte transport for Bravo controllers.

Bravo controllers are synchronous and run inside ``asyncio.to_thread``. This
interface is the boundary at which those synchronous calls reach PyLabRobot's
asynchronous I/O layer.
"""

import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# How long each read waits while draining: long enough for a byte already on its
# way to land, short enough that draining a quiet connection is not a stall.
_DRAIN_READ_TIMEOUT_S = 0.1

# Total ceiling on one drain, so a device streaming continuously cannot hold one
# open indefinitely. Bounded by elapsed time rather than by a byte count, because
# how many stale bytes a wedged device produces says nothing about how long the
# caller recovering from an error can afford to spend discarding them.
_DRAIN_BUDGET_S = 2.0


class Transport(ABC):
  """A synchronous byte channel to a Bravo instrument.

  An implementation supplies :meth:`send`, :meth:`receive`, :meth:`receive_exact`
  and :attr:`is_connected`. :meth:`drain` is not among them: it is written here in
  terms of :meth:`receive`, so every transport answers it identically and none has
  to write it.
  """

  @abstractmethod
  def send(self, data: bytes) -> None:
    """Send raw bytes to the device.

    Args:
      data: The bytes to send.

    Raises:
      TimeoutError: If the bytes cannot be handed to the device in time. Every
        transport reports a send timeout this way, whatever its underlying I/O
        layer raises, so that a caller can handle one across all of them.
    """

  @abstractmethod
  def receive(self, timeout: float = 2.0) -> bytes:
    """Receive whatever response bytes are available.

    If no bytes arrive before ``timeout`` elapses, returns ``b""`` rather
    than raising. This is deliberately different from ``receive_exact``,
    which raises ``TimeoutError`` on timeout instead of returning a partial
    result.

    Reading a framed protocol wants ``receive_exact``. This is for reading
    whatever is pending without knowing how much of it there is, which is what
    discarding a stale buffer after a protocol error needs.

    Args:
      timeout: Maximum time to wait, in seconds.

    Returns:
      The bytes received, or ``b""`` if none arrived before the timeout
      elapsed.
    """

  @abstractmethod
  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    """Receive exactly ``num_bytes``, blocking until they arrive.

    Args:
      num_bytes: The exact number of bytes to read.
      timeout: Maximum time to wait, in seconds.

    Returns:
      Exactly ``num_bytes`` bytes.

    Raises:
      TimeoutError: If the full count does not arrive within the timeout.
    """

  @property
  @abstractmethod
  def is_connected(self) -> bool:
    """Whether the transport is currently connected."""

  def drain(self) -> int:
    """Discard whatever the device has already sent, and report how much.

    This exists for error recovery. A protocol error can leave the receive buffer
    holding the tail of a frame that was misread, or a whole frame that landed
    after the caller stopped listening. Either way the next framed read would take
    those stale bytes for the head of its own frame and stay out of step from
    there, so recovery means throwing them away first.

    Reads until one comes back empty, so a buffer holding more than a single read
    is emptied rather than merely shortened, and gives up after ``_DRAIN_BUDGET_S``
    in total so that a device sending continuously cannot hold a drain open.

    This is also the one caller for which :meth:`receive` is the right
    tool: it wants whatever is pending, without knowing or caring how much, which
    is exactly what ``receive`` offers and ``receive_exact`` cannot.

    Returns:
      The number of bytes discarded, which is zero when the device had nothing
      pending. An idle connection is the ordinary case here, not an error.

    Raises:
      RuntimeError: If the transport has not been set up.
    """
    deadline = time.monotonic() + _DRAIN_BUDGET_S
    discarded = 0
    while time.monotonic() < deadline:
      stale = self.receive(timeout=_DRAIN_READ_TIMEOUT_S)
      if len(stale) == 0:
        return discarded
      discarded += len(stale)
    logger.warning(
      "%s.drain() gave up after %.1fs with bytes still arriving; discarded %d",
      type(self).__name__,
      _DRAIN_BUDGET_S,
      discarded,
    )
    return discarded
