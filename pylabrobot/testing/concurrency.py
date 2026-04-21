import inspect
from contextlib import contextmanager

import anyio
import pytest

from pylabrobot.concurrency import _AsyncResourceBase


def lifespan_kwargs(**kwargs):
  def decorator(func):
    func._lifespan_kwargs = kwargs
    return func

  return decorator


# Note: pytest doesn't like classes with __new__, so we use _AsyncResourceBase instead of AsyncResource
class AnyioTestBase(_AsyncResourceBase):
  """A test base class enabling structured concurrency.

  Intended as a replacement for `unittest.IsolatedAsyncioTestCase`.
  The `unittest` test paradigm of setUp -> test -> tearDown is
  fundamentally incompatible with structured concurrency.

  It is recommended to move away from `unittest` and towards `pytest`,
  but this class can be used to ease the transition,
  by not requiring the test cases to be re-written.

  To convert a test case from `unittest.IsolatedAsyncioTestCase` to `AnyioTestBase`,
  you need to replace all `setUp`/`asyncSetUp`/`asyncTearDown`/`tearDown` methods
  with a single `_lifespan` context manager method instead.
  Then, the test cases themselves can remain unchanged.

  Example
  ```python
    from contextlib import asynccontextmanager

    from pylabrobot.testing.structured_async import AnyioTestBase

    class TestMyClass(AnyioTestBase):
      @asynccontextmanager
      async def _lifespan(self):
        self.lh = LiquidHandler(...)
        async with self.lh:
          yield

      def test_my_test(self):
        self.assertIsNotNone(self.lh)
  ```
  """

  def __init_subclass__(cls):
    def wrap(wrapped):
      @pytest.mark.parametrize("backend", ["asyncio", "trio"])
      def sync_wrapper(self, backend, *args, **kwargs):
        lifespan_kwargs = getattr(wrapped, "_lifespan_kwargs", {})

        async def async_wrapper():
          async with self._lifespan(**lifespan_kwargs):
            if inspect.iscoroutinefunction(wrapped):
              return await wrapped(self, *args, **kwargs)
            else:
              return wrapped(self, *args, **kwargs)

        return anyio.run(async_wrapper, backend=backend)

      sync_wrapper.original_func = wrapped
      return sync_wrapper

    for name, value in list(vars(cls).items()):
      if name in {"setUp", "asyncSetUp", "tearDown", "asyncTearDown"}:
        raise TypeError(
          f"Class {cls.__name__} should not have {name} method, use _lifespan or _enter_lifespan instead."
        )
      if name.startswith("test_"):
        setattr(cls, name, wrap(value))

  async def _enter_lifespan(self, stack):
    """Helper for the _lifespan implementation; override this instead of _lifespan.

    Note, child classes may add keyword-only arguments to the signature, as _lifespan
    forwards those.
    """
    pass

  def assertEqual(self, first, second, msg=None):
    assert first == second, msg or f"{first} != {second}"

  def assertNotEqual(self, first, second, msg=None):
    assert first != second, msg or f"{first} == {second}"

  def assertIn(self, member, container, msg=None):
    assert member in container, msg or f"{member!r} not found in {container!r}"

  def assertNotIn(self, member, container, msg=None):
    assert member not in container, msg or f"{member!r} found in {container!r}"

  def assertAlmostEqual(self, first, second, places=7, msg=None, delta=None):
    if delta is not None:
      assert abs(first - second) <= delta, msg or f"{first} != {second} within {delta}"
    else:
      assert round(abs(first - second), places) == 0, (
        msg or f"{first} != {second} within {places} places"
      )

  def assertIsInstance(self, obj, cls, msg=None):
    assert isinstance(obj, cls), msg or f"{obj!r} is not an instance of {cls.__name__}"

  def assertTrue(self, expr, msg=None):
    assert expr, msg or f"{expr!r} is not True"

  def assertFalse(self, expr, msg=None):
    assert not expr, msg or f"{expr!r} is not False"

  def assertIsNone(self, obj, msg=None):
    assert obj is None, msg or f"{obj!r} is not None"

  def assertGreater(self, a, b, msg=None):
    assert a > b, msg or f"{a} not greater than {b}"

  def assertIsNotNone(self, obj, msg=None):
    assert obj is not None, msg or f"{obj!r} is None"

  @contextmanager
  def assertRaises(self, exc_type, exc_value=None, msg=None):
    class Context:
      def __init__(self):
        self.exception = None

    ctx = Context()
    try:
      yield ctx
    except Exception as e:
      ctx.exception = e
      if not isinstance(e, exc_type):
        raise AssertionError(msg or f"Expected exception of type {exc_type.__name__}, got {e!r}")
      if exc_value is not None and e != exc_value:
        raise AssertionError(msg or f"Expected {exc_value!r}, got {e!r}")
      if msg is not None and str(e) != msg:
        raise AssertionError(msg or f"Expected {msg}, got {e}")
    else:
      raise AssertionError(msg or "No exception raised")

  @contextmanager
  def assertRaisesRegex(self, exc_type, regex, msg=None):
    with self.assertRaises(exc_type) as ctx:
      yield ctx
    if ctx.exception is not None:
      import re

      if not re.search(regex, str(ctx.exception)):
        raise AssertionError(msg or f"{regex!r} does not match {str(ctx.exception)!r}")

  @contextmanager
  def assertWarns(self, expected_warning):
    with pytest.warns(expected_warning):
      yield

  def fail(self, msg):
    pytest.fail(msg)
