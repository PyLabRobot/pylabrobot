import asyncio
import concurrent.futures
import gc
import unittest
import warnings
from unittest.mock import patch

from pylabrobot.agilent.bravo.transport._bridge import AsyncTransportBase


class _StubIO:
  """Stands in for a pylabrobot.io object that opens and closes without a device."""

  def __init__(self):
    self.open_calls = 0
    self.close_calls = 0

  async def setup(self) -> None:
    self.open_calls += 1

  async def stop(self) -> None:
    self.close_calls += 1


class _StubTransport(AsyncTransportBase):
  """The bridge with a device-free I/O object, so _run can be exercised directly."""

  def __init__(self):
    super().__init__(transport_name="stub", endpoint="stub-endpoint")
    self._io = _StubIO()

  async def _open_io(self) -> None:
    await self._io.setup()

  async def _close_io(self) -> None:
    await self._io.stop()

  def send(self, data: bytes) -> None:
    raise NotImplementedError

  def receive(self, timeout: float = 2.0) -> bytes:
    raise NotImplementedError

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    raise NotImplementedError


class _RacedFuture:
  """Stands in for the concurrent.futures.Future that run_coroutine_threadsafe returns.

  Pins the race _run must handle: result(timeout) raises concurrent.futures.TimeoutError,
  but by the time done() is checked the future has a real outcome behind it -- a value
  or the coroutine's own exception -- decided before that check.
  """

  def __init__(self, outcome_kind: str, outcome_value):
    self._kind = outcome_kind  # "value" or "exception"
    self._value = outcome_value
    self.cancel_calls = 0

  def result(self, timeout=None):
    if timeout is not None:
      raise concurrent.futures.TimeoutError()
    if self._kind == "value":
      return self._value
    raise self._value

  def done(self) -> bool:
    return True

  def cancel(self) -> bool:
    self.cancel_calls += 1
    return False


class AsyncTransportBaseTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.transport = _StubTransport()
    await self.transport.setup()
    self.addAsyncCleanup(self.transport.stop)

  async def test_run_converts_future_timeout_to_builtin_timeout_error(self):
    async def slow() -> bytes:
      await asyncio.sleep(0.5)
      return b"x"

    def blocking_call() -> bytes:
      return self.transport._run(slow(), 0.05)

    with self.assertRaises(TimeoutError) as ctx:
      await asyncio.to_thread(blocking_call)

    # This is the outer bound genuinely firing (the coroutine never got
    # anywhere near completing), so it must carry the outer-bound message.
    self.assertIn("did not complete within", str(ctx.exception))

  async def test_setup_twice_raises(self):
    # Opening again would strand what the first open allocated -- an I/O object
    # holding a thread pool would leak its executor -- and leave the loop recorded
    # here pointing at a connection nothing else can reach.
    with self.assertRaises(RuntimeError) as ctx:
      await self.transport.setup()

    self.assertIn("already set up", str(ctx.exception))
    self.assertEqual(self.transport._io.open_calls, 1)

  async def test_run_receive_returns_empty_bytes_when_the_outer_bound_fires(self):
    # A coroutine with no timeout of its own, so the outer, future-level bound is
    # the only one that can fire. Python 3.9 surfaces that bound from
    # result(timeout) as a concurrent.futures.TimeoutError, which is a different
    # class from the builtin TimeoutError there; _run has to have converted it,
    # or _run_receive would raise where Transport.receive says return b"".
    async def stalled() -> bytes:
      await asyncio.sleep(5)
      return b"x"

    def blocking_call() -> bytes:
      return self.transport._run_receive(stalled(), 0.05)

    with self.assertLogs("pylabrobot.agilent.bravo.transport._bridge", "WARNING") as logs:
      result = await asyncio.to_thread(blocking_call)

    self.assertEqual(result, b"")
    # Logged apart from an ordinary read timeout, and louder: this b"" means the
    # bridge failed, not that the device had nothing to say, and the two are
    # otherwise indistinguishable to whoever reads the log.
    self.assertIn("outer bound fired", "\n".join(logs.output))

  async def test_run_receive_allows_the_coroutine_its_own_timeout_plus_grace(self):
    # The future is bounded above the timeout the coroutine was built with, so
    # that the coroutine's own timeout is the one that fires. Bounding it at that
    # timeout instead would throw away a result that arrived within the grace
    # period, and report it as the b"" that means no data came.
    async def slower_than_its_own_timeout() -> bytes:
      await asyncio.sleep(0.3)
      return b"late but real"

    def blocking_call() -> bytes:
      return self.transport._run_receive(slower_than_its_own_timeout(), 0.05)

    result = await asyncio.to_thread(blocking_call)
    self.assertEqual(result, b"late but real")

  async def test_run_raises_runtime_error_not_attribute_error_when_loop_is_none(self):
    # Reproducing the actual TOCTOU race (stop() clearing self._loop between
    # _run's check and its run_coroutine_threadsafe call) requires winning a
    # narrow, non-deterministic interleaving. Instead, this pins the
    # observable end state of that race -- self._loop already None when
    # _run is entered -- and asserts the two properties the bug broke:
    # a RuntimeError (not an AttributeError from inside asyncio), and no
    # leaked, un-awaited coroutine.
    self.transport._loop = None

    async def coro() -> bytes:
      return b"x"

    pending = coro()
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      with self.assertRaises(RuntimeError):
        self.transport._run(pending, 1.0)
      del pending
      gc.collect()
      never_awaited = [w for w in caught if "was never awaited" in str(w.message)]
      self.assertEqual(never_awaited, [])

  async def test_run_returns_real_result_when_future_races_to_success(self):
    # Pins the race the future.done() branch must resolve correctly: the
    # coroutine actually succeeded, but result(timeout) still raised
    # concurrent.futures.TimeoutError because it fired in the narrow window
    # before the future was marked done. _run must return the real result,
    # not the outer timeout.
    fake_future = _RacedFuture("value", b"real result")

    async def coro() -> bytes:
      return b"unused"

    pending = coro()

    def blocking_call() -> bytes:
      with patch.object(asyncio, "run_coroutine_threadsafe", return_value=fake_future):
        return self.transport._run(pending, 1.0)

    try:
      result = await asyncio.to_thread(blocking_call)
    finally:
      pending.close()

    self.assertEqual(result, b"real result")
    self.assertEqual(fake_future.cancel_calls, 0)

  async def test_run_reraises_coroutines_own_exception_when_future_races_to_failure(self):
    # Same race, but the coroutine itself failed with a non-timeout error.
    # _run must surface that real exception, not the outer
    # concurrent.futures.TimeoutError caught from result(timeout).
    inner_exc = ValueError("the coroutine's own failure")
    fake_future = _RacedFuture("exception", inner_exc)

    async def coro() -> bytes:
      return b"unused"

    pending = coro()

    def blocking_call() -> None:
      with patch.object(asyncio, "run_coroutine_threadsafe", return_value=fake_future):
        self.transport._run(pending, 1.0)

    try:
      with self.assertRaises(ValueError) as ctx:
        await asyncio.to_thread(blocking_call)
    finally:
      pending.close()

    self.assertIs(ctx.exception, inner_exc)


if __name__ == "__main__":
  unittest.main()
