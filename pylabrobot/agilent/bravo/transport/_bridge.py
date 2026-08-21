"""Shared synchronous-to-asynchronous bridge for Bravo transports.

Bravo controllers are synchronous and run inside ``asyncio.to_thread``, while
PyLabRobot's I/O layer is asynchronous. Every concrete transport crosses that
boundary the same way: it submits a coroutine to the event loop that owns its
connection and blocks the calling worker thread until the coroutine completes.
That crossing, its lifecycle, and the timeout accounting it needs live here, so
a concrete transport supplies only its own I/O object and its read/write bodies.
"""

import asyncio
import concurrent.futures
import logging
from abc import abstractmethod
from typing import Any, Coroutine, Optional, TypeVar

from .base import Transport

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Slack added on top of a coroutine's own internal timeout when bounding it via
# concurrent.futures.Future.result(). Named and centralized, rather than a handful
# of copy-pasted "+ 1.0" literals, because it is also the real cumulative ceiling on
# a call's duration whenever the coroutine's own timeout is not itself cumulative.
# SocketTransport documents the concrete numbers this implies for that transport.
_LOOP_HANDOFF_GRACE_S = 1.0

# Read size for receive(). Framed instrument protocols spoken over this bridge
# (this driver's Gemini protocol carries payloads up to roughly 512 bytes) fit
# comfortably in one read() call at this size, with headroom for framing and
# protocol overhead, instead of silently truncating the way the underlying I/O
# layer's much smaller defaults would.
_RECEIVE_BUFFER_SIZE = 4096


class _OuterBoundTimeout(TimeoutError):
  """The future-level bound firing, rather than a coroutine's own timeout.

  A ``TimeoutError`` like any other to a caller, which is what the contract
  promises; the distinct class exists so that the bridge itself can tell the two
  apart, since by the time one is caught the wording is all that separates them.
  """


def _outer_bound(timeout: float) -> float:
  """The future-level bound for a call whose coroutine enforces ``timeout`` itself.

  Deliberately a module-level function rather than a method, so a transport can
  use it without inheriting anything.

  Args:
    timeout: The timeout the coroutine was built with.

  Returns:
    ``timeout`` plus a fixed grace period, so that the coroutine's own timeout is
    what normally fires.
  """
  return timeout + _LOOP_HANDOFF_GRACE_S


class AsyncTransportBase(Transport):
  """A synchronous byte channel backed by an asynchronous PyLabRobot I/O object.

  Each call submits its coroutine to the event loop that owns the connection, via
  ``asyncio.run_coroutine_threadsafe``, and blocks the calling worker thread until
  the coroutine completes. This cannot deadlock: the caller is never the loop
  thread, so the loop remains free to run the coroutine.

  Subclasses own their I/O object, open and close it through :meth:`_open_io` and
  :meth:`_close_io`, and implement :meth:`Transport.send`, :meth:`Transport.receive`
  and :meth:`Transport.receive_exact` in terms of :meth:`_run` and
  :meth:`_run_receive`.
  """

  def __init__(self, transport_name: str, endpoint: str):
    """Record how this transport identifies itself in logs and error messages.

    Args:
      transport_name: Short name of the transport kind, e.g. ``"socket"``.
      endpoint: Identifier of the far end, e.g. ``"192.168.0.1:8000"``.
    """
    self._transport_name = transport_name
    self._endpoint = endpoint
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    self._connected = False

  @abstractmethod
  async def _open_io(self) -> None:
    """Open the underlying I/O object."""

  @abstractmethod
  async def _close_io(self) -> None:
    """Close the underlying I/O object."""

  async def setup(self) -> None:
    """Open the connection and capture the owning event loop.

    Raises:
      RuntimeError: If the transport is already set up. Opening a second time
        would strand whatever the first open allocated -- an I/O object holding a
        thread pool would leak its executor -- and leave the loop recorded here
        pointing at a connection nothing else can reach.
    """
    if self._connected:
      raise RuntimeError("Transport is already set up. Call stop() before setting up again.")
    loop = asyncio.get_running_loop()
    await self._open_io()
    self._loop = loop
    self._connected = True
    logger.debug("[%s] Bravo %s transport connected", self._endpoint, self._transport_name)

  async def stop(self) -> None:
    """Close the connection and release the owning event loop."""
    try:
      await self._close_io()
      logger.debug("[%s] Bravo %s transport disconnected", self._endpoint, self._transport_name)
    finally:
      self._connected = False
      self._loop = None

  def _run(self, coro: Coroutine[Any, Any, T], timeout: float) -> T:
    """Run a coroutine on the owning loop and block until it completes.

    ``self._loop`` is read exactly once into a local variable, and that local is
    used for both the not-set-up check and the ``run_coroutine_threadsafe`` call.
    Reading it twice would leave a window in which a concurrent ``stop()`` could
    clear ``self._loop`` between the check and the call, turning a would-be
    ``RuntimeError`` into an ``AttributeError`` from inside asyncio and leaking the
    un-awaited coroutine.

    ``future.result(timeout)`` raises ``concurrent.futures.TimeoutError`` both when
    the future itself does not complete within ``timeout``, and -- from Python 3.11
    onward, where ``concurrent.futures.TimeoutError`` *is* the builtin
    ``TimeoutError`` -- when the coroutine completes with its own inner
    ``TimeoutError``. Those two cases must not be confused: only the first is this
    bound firing. Class alone cannot tell them apart on 3.11+, and the caught
    exception itself is not trustworthy evidence either: the future can complete in
    the narrow window between ``result(timeout)`` raising and ``future.done()``
    being checked, so a caught exception that looks like the coroutine's own may in
    fact be this outer bound, or vice versa. So when the future is already done,
    this does not re-raise the caught exception; it calls ``future.result()`` again,
    with no timeout, to ask the future itself what actually happened -- its real
    result if the coroutine succeeded, or its real exception, with its own message
    and ``__cause__``, if it did not. Only an undone future means this outer bound
    genuinely fired.

    One thing this crossing does not carry: from Python 3.11 on, where
    ``concurrent.futures.TimeoutError`` is the builtin ``TimeoutError``, asyncio
    rebuilds an exception of exactly that class while copying it onto the future
    handed back here, so a coroutine's ``TimeoutError`` reaches the caller with
    its message intact but as a different object, stripped of its ``__cause__``.
    Anything that needs to tell one timeout from another must therefore go by the
    message, which is why each transport gives its timeouts distinct wording.

    Args:
      coro: The coroutine to run.
      timeout: The future-level bound, in seconds, and so the ceiling on the total
        duration of the call. For a coroutine that enforces a timeout of its own,
        this must sit above that timeout, and :meth:`_run_bounded` is the way to
        say so -- passing the coroutine's own timeout here would leave the two
        racing, and this bound's generic message would start winning.

    Returns:
      The coroutine's result.

    Raises:
      RuntimeError: If the transport has not been set up.
      TimeoutError: If the coroutine does not complete within ``timeout``.
    """
    loop = self._loop
    if loop is None:
      coro.close()
      raise RuntimeError("Transport is not set up. Call setup() first.")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
      return future.result(timeout)
    except concurrent.futures.TimeoutError as exc:
      if future.done():
        # The exception result(timeout) raised is not necessarily the coroutine's
        # own: the future can finish in the window between that raise and this
        # check, so re-raising `exc` could surface the outer timeout even though
        # the coroutine actually succeeded or failed differently. Ask the future
        # itself, which returns the real result or raises the real exception.
        return future.result()
      future.cancel()
      raise _OuterBoundTimeout(
        f"Bravo {self._transport_name} transport call did not complete within {timeout} seconds"
      ) from exc

  def _run_bounded(self, coro: Coroutine[Any, Any, T], coro_timeout: float) -> T:
    """Run a coroutine that enforces ``coro_timeout`` itself, bounded above it.

    The grace period between the two is applied here rather than at each call
    site, so that no transport has to remember the arithmetic. Getting it wrong
    by passing the coroutine's own timeout as the bound leaves the two racing,
    and this bound's generic message starts displacing the specific one the
    coroutine would have raised.

    Args:
      coro: The coroutine to run.
      coro_timeout: The timeout ``coro`` was built with, in seconds.

    Returns:
      The coroutine's result.

    Raises:
      RuntimeError: If the transport has not been set up.
      TimeoutError: If the coroutine does not complete within the bound.
    """
    return self._run(coro, _outer_bound(coro_timeout))

  def _run_receive(self, coro: Coroutine[Any, Any, bytes], coro_timeout: float) -> bytes:
    """Run a ``receive`` coroutine, honoring the contract's return-on-timeout rule.

    Shared by every transport so that the difference between ``receive`` and
    ``receive_exact`` -- the former returns ``b""`` on timeout, the latter raises
    -- cannot drift between transports.

    Args:
      coro: The coroutine that performs the read.
      coro_timeout: The timeout ``coro`` was built with, in seconds.

    Returns:
      The bytes ``coro`` produced, or ``b""`` if it timed out.
    """
    try:
      return self._run_bounded(coro, coro_timeout)
    except _OuterBoundTimeout:
      # The coroutine's own timeout should have fired first and did not, so
      # something upstream of the device is wrong -- a stalled loop, or a read
      # that outran its own bound. Logged apart from the ordinary case, and
      # louder, because the b"" this returns is otherwise indistinguishable from
      # the device simply having nothing to say.
      logger.warning(
        "[%s] receive() outer bound fired after %.3fs; returning b'' per Transport contract",
        self._endpoint,
        _outer_bound(coro_timeout),
      )
      return b""
    except TimeoutError:
      # Deliberate: base.Transport.receive's contract is to return b"" on timeout
      # rather than raise. This is the expected case -- the device said nothing
      # within the time it was given.
      logger.debug(
        "[%s] receive() timed out after %.3fs; returning b'' per Transport contract",
        self._endpoint,
        coro_timeout,
      )
      return b""

  @property
  def is_connected(self) -> bool:
    """Whether the transport has been set up and not yet stopped."""
    return self._connected
