"""Tests for dual-backend asynchronous test infrastructure."""

import inspect

import pytest

from pylabrobot.testing import concurrency
from pylabrobot.testing.concurrency import AnyioTestBase

pytest_plugins = ("pytester",)


def test_fixture_free_method_does_not_apply_empty_usefixtures_mark(
  pytester: pytest.Pytester,
) -> None:
  """Do not warn while wrapping a test method that requests no fixtures."""
  pytester.makepyfile(
    """
    from pylabrobot.testing.concurrency import AnyioTestBase


    class TestFixtureFree(AnyioTestBase):
      \"\"\"Declare one test method without fixture arguments.\"\"\"

      async def test_example(self) -> None:
        \"\"\"Provide a fixture-free asynchronous test method.\"\"\"
    """
  )

  result = pytester.runpytest("-q", "-W", "error::pytest.PytestWarning")

  result.assert_outcomes(passed=2)


def test_collects_both_backends_and_honors_inherited_override(
  pytester: pytest.Pytester,
) -> None:
  """Collect inherited tests using the subclass's backend selection."""
  pytester.makepyfile(
    """
    from pylabrobot.testing.concurrency import AnyioTestBase


    class TestBothBackends(AnyioTestBase):
      \"\"\"Exercise the default backend selection.\"\"\"

      async def test_example(self) -> None:
        \"\"\"Run once on each default backend.\"\"\"


    class TestAsyncioOnly(TestBothBackends):
      \"\"\"Restrict inherited tests to asyncio.\"\"\"

      _anyio_backends = [\"asyncio\"]
    """
  )

  result = pytester.runpytest("--collect-only", "-q")

  result.assert_outcomes()
  result.stdout.fnmatch_lines(
    [
      "test_collects_both_backends_and_honors_inherited_override.py::*TestBothBackends*asyncio*",
      "test_collects_both_backends_and_honors_inherited_override.py::*TestBothBackends*trio*",
      "test_collects_both_backends_and_honors_inherited_override.py::*TestAsyncioOnly*asyncio*",
      "*3 tests collected*",
    ]
  )


def test_multiple_inheritance_uses_leftmost_method_and_backends(
  pytester: pytest.Pytester,
) -> None:
  """Honor normal MRO precedence for inherited methods and backends."""
  pytester.makepyfile(
    """
    from pylabrobot.testing.concurrency import AnyioTestBase


    class First(AnyioTestBase):
      \"\"\"Provide the preferred test method and backend policy.\"\"\"

      _anyio_backends = [\"asyncio\"]

      async def test_precedence(self) -> None:
        \"\"\"Pass when selected through normal MRO precedence.\"\"\"


    class Second(AnyioTestBase):
      \"\"\"Provide conflicting behavior that must not take precedence.\"\"\"

      _anyio_backends = [\"trio\"]

      async def test_precedence(self) -> None:
        \"\"\"Fail if the later base incorrectly takes precedence.\"\"\"
        raise AssertionError(\"selected later base\")


    class TestCombined(First, Second):
      \"\"\"Combine test bases using normal left-to-right precedence.\"\"\"
    """
  )

  result = pytester.runpytest("-v")

  result.assert_outcomes(passed=1)
  result.stdout.fnmatch_lines(["*TestCombined*asyncio*"])


def test_diamond_inheritance_uses_c3_method_and_backends(
  pytester: pytest.Pytester,
) -> None:
  """Prefer a right-base declaration over the left base's shared ancestor."""
  pytester.makepyfile(
    """
    from pylabrobot.testing.concurrency import AnyioTestBase


    class Root(AnyioTestBase):
      \"\"\"Provide behavior inherited indirectly through the left base.\"\"\"

      _anyio_backends = [\"asyncio\"]

      async def test_precedence(self) -> None:
        \"\"\"Fail if the shared ancestor incorrectly takes precedence.\"\"\"
        raise AssertionError(\"selected shared ancestor\")


    class Left(Root):
      \"\"\"Inherit the shared ancestor without declaring behavior.\"\"\"


    class Right(Root):
      \"\"\"Override the shared ancestor later in the C3 linearization.\"\"\"

      _anyio_backends = [\"trio\"]

      async def test_precedence(self) -> None:
        \"\"\"Pass when selected according to C3 precedence.\"\"\"


    class TestCombined(Left, Right):
      \"\"\"Combine the diamond hierarchy under test.\"\"\"
    """
  )

  result = pytester.runpytest("-v")

  result.assert_outcomes(passed=1)
  result.stdout.fnmatch_lines(["*TestCombined*trio*"])


def test_runs_each_test_inside_the_class_lifespan(pytester: pytest.Pytester) -> None:
  """Enter and exit a fresh class lifespan around every async test."""
  pytester.makepyfile(
    """
    from typing import Any

    from pylabrobot.concurrency import AsyncExitStackWithShielding
    from pylabrobot.testing.concurrency import AnyioTestBase


    class TestLifespan(AnyioTestBase):
      \"\"\"Observe class lifecycle state while a test runs.\"\"\"

      def _leave_lifespan(self) -> None:
        \"\"\"Record lifespan exit.\"\"\"
        self.entered = False

      async def _enter_lifespan(
        self, stack: AsyncExitStackWithShielding, **kwargs: Any
      ) -> None:
        \"\"\"Record lifespan entry and register exit.\"\"\"
        self.entered = True
        stack.callback(self._leave_lifespan)

      async def test_is_active(self) -> None:
        \"\"\"Observe active lifecycle state during the test.\"\"\"
        assert self.entered
        assert self.is_active
    """
  )

  result = pytester.runpytest("-q")

  result.assert_outcomes(passed=2)


def test_forwards_lifespan_kwargs_only_to_the_decorated_test(
  pytester: pytest.Pytester,
) -> None:
  """Keep lifespan keyword arguments scoped to one decorated method."""
  pytester.makepyfile(
    """
    from typing import Any, Dict

    from pylabrobot.concurrency import AsyncExitStackWithShielding
    from pylabrobot.testing.concurrency import AnyioTestBase, lifespan_kwargs


    class TestLifespanKwargs(AnyioTestBase):
      \"\"\"Capture lifespan options for each test invocation.\"\"\"

      async def _enter_lifespan(
        self, stack: AsyncExitStackWithShielding, **kwargs: Any
      ) -> None:
        \"\"\"Capture this test's lifespan keyword arguments.\"\"\"
        self.received_kwargs: Dict[str, Any] = kwargs

      @lifespan_kwargs(example=\"value\")
      async def test_with_kwargs(self) -> None:
        \"\"\"Receive explicitly decorated lifespan options.\"\"\"
        assert self.received_kwargs == {\"example\": \"value\"}

      async def test_without_kwargs(self) -> None:
        \"\"\"Receive no options leaked from another test.\"\"\"
        assert self.received_kwargs == {}
    """
  )

  result = pytester.runpytest("-q")

  result.assert_outcomes(passed=4)


def test_preserves_existing_pytest_marks(pytester: pytest.Pytester) -> None:
  """Retain parametrization and selection marks while wrapping tests."""
  pytester.makepyfile(
    """
    import pytest

    from pylabrobot.testing.concurrency import AnyioTestBase


    class TestMarked(AnyioTestBase):
      \"\"\"Exercise marks attached before class creation.\"\"\"

      @pytest.mark.parametrize(\"value\", [1, 2])
      @pytest.mark.example
      async def test_marked(self, value: int) -> None:
        \"\"\"Run every marked parameter on both backends.\"\"\"
        assert value in (1, 2)
    """
  )

  result = pytester.runpytest("-q", "-m", "example")

  result.assert_outcomes(passed=4)


def test_forwards_requested_fixtures_to_the_test_method(pytester: pytest.Pytester) -> None:
  """Pass every requested fixture value to the wrapped test method."""
  pytester.makepyfile(
    """
    import pytest

    from pylabrobot.testing.concurrency import AnyioTestBase


    @pytest.fixture
    def example_value() -> str:
      \"\"\"Provide one fixture value.\"\"\"
      return \"provided\"


    class TestRequestedFixtures(AnyioTestBase):
      \"\"\"Request a fixture from a test method.\"\"\"

      async def test_receives_fixture(self, example_value: str) -> None:
        \"\"\"Receive the value produced by the requested fixture.\"\"\"
        assert example_value == \"provided\"
    """
  )

  result = pytester.runpytest("-q")

  result.assert_outcomes(passed=2)


def test_accepts_backend_options_from_any_mapping(pytester: pytest.Pytester) -> None:
  """Select backends and their options from a mapping that is not a dict."""
  pytester.makepyfile(
    """
    from types import MappingProxyType

    from pylabrobot.testing.concurrency import AnyioTestBase


    class TestMappingBackends(AnyioTestBase):
      \"\"\"Declare backend options through a read-only mapping.\"\"\"

      _anyio_backends = MappingProxyType({\"asyncio\": {\"use_uvloop\": False}})

      async def test_example(self) -> None:
        \"\"\"Run once on the single mapped backend.\"\"\"
    """
  )

  result = pytester.runpytest("-v")

  result.assert_outcomes(passed=1)
  result.stdout.fnmatch_lines(["*TestMappingBackends*asyncio*"])


def test_test_infrastructure_uses_public_pytest_interfaces() -> None:
  """Collect and wrap test methods without depending on pytest internals."""
  source = inspect.getsource(concurrency)

  assert "_pytest" not in source


def test_rejects_a_leading_base_outside_the_test_hierarchy() -> None:
  """Reject a preceding base whose declared tests cannot be wrapped."""

  class PlainMixin:
    """Declare test behavior outside the test hierarchy."""

    async def test_example(self) -> None:
      """Represent a test method that cannot be wrapped."""

  with pytest.raises(TypeError, match="PlainMixin.*AnyioTestBase subclass"):

    class InvalidLeadingMixinTest(PlainMixin, AnyioTestBase):
      """Combine an unsupported base before a test base."""


def test_rejects_a_trailing_base_outside_the_test_hierarchy() -> None:
  """Reject a following base whose declared tests cannot be wrapped."""

  class PlainMixin:
    """Declare test behavior outside the test hierarchy."""

    async def test_example(self) -> None:
      """Represent a test method that cannot be wrapped."""

  with pytest.raises(TypeError, match="PlainMixin.*AnyioTestBase subclass"):

    class InvalidTrailingMixinTest(AnyioTestBase, PlainMixin):
      """Combine an unsupported base after a test base."""


def test_shared_test_behavior_uses_a_test_base(pytester: pytest.Pytester) -> None:
  """Share test behavior between classes through an intermediate test base."""
  pytester.makepyfile(
    """
    from pylabrobot.testing.concurrency import AnyioTestBase


    class SharedBehavior(AnyioTestBase):
      \"\"\"Declare test behavior shared by several test classes.\"\"\"

      _anyio_backends = [\"asyncio\"]

      async def test_shared(self) -> None:
        \"\"\"Run once for every class inheriting this behavior.\"\"\"


    class TestFirst(SharedBehavior):
      \"\"\"Inherit the shared behavior.\"\"\"


    class TestSecond(SharedBehavior):
      \"\"\"Inherit the shared behavior alongside its own test.\"\"\"

      async def test_own(self) -> None:
        \"\"\"Run in addition to the inherited behavior.\"\"\"
    """
  )

  result = pytester.runpytest("-q")

  result.assert_outcomes(passed=3)


def test_rejects_setup_hook() -> None:
  """Reject unittest setup in favor of lifespan entry."""
  with pytest.raises(TypeError, match="setUp.*implement _enter_lifespan"):

    class InvalidSetUpTest(AnyioTestBase):
      """Declare an invalid synchronous setup hook."""

      def setUp(self) -> None:
        """Represent forbidden unittest setup."""


def test_rejects_async_setup_hook() -> None:
  """Reject asynchronous unittest setup in favor of lifespan entry."""
  with pytest.raises(TypeError, match="asyncSetUp.*implement _enter_lifespan"):

    class InvalidAsyncSetUpTest(AnyioTestBase):
      """Declare an invalid asynchronous setup hook."""

      async def asyncSetUp(self) -> None:
        """Represent forbidden asynchronous unittest setup."""


def test_rejects_teardown_hook() -> None:
  """Reject unittest teardown in favor of lifespan entry."""
  with pytest.raises(TypeError, match="tearDown.*implement _enter_lifespan"):

    class InvalidTearDownTest(AnyioTestBase):
      """Declare an invalid synchronous teardown hook."""

      def tearDown(self) -> None:
        """Represent forbidden unittest teardown."""


def test_rejects_async_teardown_hook() -> None:
  """Reject asynchronous unittest teardown in favor of lifespan entry."""
  with pytest.raises(TypeError, match="asyncTearDown.*implement _enter_lifespan"):

    class InvalidAsyncTearDownTest(AnyioTestBase):
      """Declare an invalid asynchronous teardown hook."""

      async def asyncTearDown(self) -> None:
        """Represent forbidden asynchronous unittest teardown."""
