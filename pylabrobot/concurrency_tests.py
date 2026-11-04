"""Tests for structured asynchronous resource lifecycles."""

import asyncio
import sys
import traceback
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional, cast

import anyio
import pytest
from typing_extensions import assert_type

import pylabrobot.concurrency as concurrency
from pylabrobot.concurrency import (
  AsyncExitStackWithShielding,
  HasLifespan,
  SiblingTaskErrors,
  non_cancelling_task_group,
)

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup, ExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup, ExceptionGroup


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
  """Run each asynchronous test with both supported AnyIO backends."""
  return cast(str, request.param)


def _formatted_failure(error: BaseException) -> str:
  """Return the complete rendering of one failure, including grouped and chained causes.

  Args:
    error: Failure reported to a caller.

  Returns:
    The text a caller sees for this failure.
  """
  return "".join(traceback.format_exception(type(error), error, error.__traceback__))


def _reachable_failures(error: BaseException) -> List[str]:
  """Return the cleanup failures a caller can reach from one reported failure.

  Args:
    error: Failure reported to a caller.

  Returns:
    The message of each chained cleanup failure, outermost first.

  Raises:
    AssertionError: If the exception context chain contains a cycle.
  """
  messages: List[str] = []
  seen: List[int] = []
  current: Optional[BaseException] = error
  while current is not None:
    assert id(current) not in seen, "exception context chain contains a cycle"
    seen.append(id(current))
    if isinstance(current, RuntimeError):
      messages.append(str(current))
    current = current.__context__
  return messages


async def _record(events: List[str], event: str) -> None:
  """Append one lifecycle event."""
  events.append(event)


class _ChildResource(HasLifespan):
  """Record child entry and exit, optionally failing during entry."""

  def __init__(self, events: List[str], fail_entry: bool = False) -> None:
    """Initialize the child recorder."""
    super().__init__()
    self._events = events
    self._fail_entry = fail_entry

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: object) -> None:
    """Record entry and register child cleanup."""
    self._events.append("enter-child")
    stack.push_shielded_async_callback(_record, self._events, "exit-child")
    if self._fail_entry:
      raise RuntimeError("child entry failed")


class _ParentResource(HasLifespan):
  """Record parent entry and own a child resource lifespan."""

  def __init__(self, events: List[str], fail_child_entry: bool = False) -> None:
    """Initialize the parent and its child."""
    super().__init__()
    self._events = events
    self.child = _ChildResource(events, fail_entry=fail_child_entry)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: object) -> None:
    """Record entry, register cleanup, and enter the child lifespan."""
    self._events.append("enter-parent")
    stack.push_shielded_async_callback(_record, self._events, "exit-parent")
    await stack.enter_async_context(self.child.lifespan())


class _CancellationResource(HasLifespan):
  """Expose events around a cancellation checkpoint in cleanup."""

  def __init__(self) -> None:
    """Initialize cleanup observation events."""
    super().__init__()
    self.cleanup_started = anyio.Event()
    self.cleanup_finished = anyio.Event()

  async def _cleanup(self) -> None:
    """Cross a cancellation checkpoint before completing cleanup."""
    self.cleanup_started.set()
    await anyio.lowlevel.checkpoint()
    self.cleanup_finished.set()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: object) -> None:
    """Register the observed cleanup callback."""
    stack.push_shielded_async_callback(self._cleanup)


class _StateResource(HasLifespan):
  """Block lifecycle transitions so state visibility can be observed."""

  def __init__(self) -> None:
    """Initialize transition gates and observations."""
    super().__init__()
    self.enter_started = anyio.Event()
    self.allow_entry = anyio.Event()
    self.body_started = anyio.Event()
    self.leave_body = anyio.Event()
    self.exit_started = anyio.Event()
    self.allow_exit = anyio.Event()

  async def _cleanup(self) -> None:
    """Block cleanup until the test permits exit."""
    self.exit_started.set()
    await self.allow_exit.wait()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: object) -> None:
    """Block entry until the test permits acquisition."""
    self.enter_started.set()
    await self.allow_entry.wait()
    stack.push_shielded_async_callback(self._cleanup)


class _OptionResource(HasLifespan):
  """Capture a keyword-only lifespan option."""

  def __init__(self) -> None:
    """Initialize without a captured option."""
    super().__init__()
    self.option: Optional[str] = None

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Capture the forwarded option."""
    self.option = cast(str, kwargs["option"])


class _NoSuperResource(HasLifespan):
  """Resource deliberately omitting parent initialization."""

  def __init__(self) -> None:
    """Initialize without calling the parent constructor."""

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
  ) -> None:
    """Provide a no-op lifespan for this test resource."""


@pytest.mark.anyio
async def test_no_option_lifespan_rejects_unknown_keyword_natively() -> None:
  resource = _NoSuperResource()
  with pytest.raises(TypeError, match="unexpected keyword argument 'unknown'"):
    async with resource.lifespan(unknown=True):
      pass


class _InteractiveResource(HasLifespan):
  """Record interactive lifespan entry, options, and cleanup attempts."""

  def __init__(
    self,
    events: List[str],
    fail_entries: int = 0,
    exit_failure: Optional[str] = None,
    cleanup_barrier: Optional[anyio.Event] = None,
  ) -> None:
    """Initialize interactive lifecycle observations and failure controls."""
    super().__init__()
    self._events = events
    self._fail_entries = fail_entries
    self._exit_failure = exit_failure
    self._cleanup_barrier = cleanup_barrier
    self.entries = 0
    self.option: Optional[str] = None

  async def _cleanup(self) -> None:
    """Record cleanup, optionally synchronize it, and optionally fail."""
    self._events.append("cleanup")
    if self._cleanup_barrier is not None:
      await self._cleanup_barrier.wait()
    if self._exit_failure is not None:
      raise RuntimeError(self._exit_failure)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Capture options, optionally fail entry, and register cleanup."""
    self.entries += 1
    self.option = cast(Optional[str], kwargs.get("option"))
    if self.entries <= self._fail_entries:
      raise RuntimeError("interactive entry failed")
    stack.push_shielded_async_callback(self._cleanup)


class _CancelledSetupResource(HasLifespan):
  """Block interactive entry and fail cleanup after setup cancellation."""

  def __init__(self) -> None:
    """Initialize entry synchronization events."""
    super().__init__()
    self.entry_started = anyio.Event()
    self.allow_entry = anyio.Event()

  async def _cleanup(self) -> None:
    """Fail cleanup after cancelled setup permits entry to finish."""
    raise RuntimeError("cancelled setup cleanup failed")

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: object) -> None:
    """Register failing cleanup and block resource entry."""
    stack.push_shielded_async_callback(self._cleanup)
    self.entry_started.set()
    await self.allow_entry.wait()


@pytest.mark.anyio
async def test_lifespan_cleans_up_children_before_parents() -> None:
  """A normal lifespan exit unwinds child resources before their parent."""
  events: List[str] = []

  async with _ParentResource(events):
    assert events == ["enter-parent", "enter-child"]

  assert events == ["enter-parent", "enter-child", "exit-child", "exit-parent"]


@pytest.mark.anyio
async def test_failed_child_entry_rolls_back_in_reverse_order() -> None:
  """A child entry failure unwinds every registered cleanup in reverse order."""
  events: List[str] = []
  resource = _ParentResource(events, fail_child_entry=True)

  with pytest.raises(RuntimeError, match="child entry failed"):
    async with resource:
      pytest.fail("failed entry must not reach the body")

  assert events == ["enter-parent", "enter-child", "exit-child", "exit-parent"]
  assert not resource.is_active
  assert not resource.child.is_active


@pytest.mark.anyio
async def test_cancellation_does_not_interrupt_async_cleanup() -> None:
  """Cancellation during the body cannot interrupt shielded async cleanup."""
  resource = _CancellationResource()
  body_started = anyio.Event()
  hold_body = anyio.Event()

  async def run_resource() -> None:
    """Hold the resource body until its task group is cancelled."""
    async with resource:
      body_started.set()
      await hold_body.wait()

  async with anyio.create_task_group() as task_group:
    task_group.start_soon(run_resource)
    await body_started.wait()
    task_group.cancel_scope.cancel()

  assert resource.cleanup_started.is_set()
  assert resource.cleanup_finished.is_set()
  assert not resource.is_active


@pytest.mark.anyio
async def test_cancellation_leaves_the_lifespan_and_stops_its_scope() -> None:
  """Cancellation during the body leaves the lifespan and ends the cancelled scope."""
  resource = _CancellationResource()
  body_started = anyio.Event()
  hold_body = anyio.Event()
  events: List[str] = []

  async def run_resource() -> None:
    """Record how cancellation leaves one lifespan body and its scope."""
    try:
      async with resource:
        body_started.set()
        await hold_body.wait()
        events.append("body-resumed")
    except anyio.get_cancelled_exc_class():
      events.append("cancelled")
      raise
    events.append("scope-continued")

  async with anyio.create_task_group() as task_group:
    task_group.start_soon(run_resource)
    await body_started.wait()
    task_group.cancel_scope.cancel()

  assert events == ["cancelled"]
  assert resource.cleanup_finished.is_set()
  assert not resource.is_active


@pytest.mark.anyio
async def test_nested_entry_rejects_only_the_inner_lifespan() -> None:
  """Nested entry fails without making the outer lifespan unusable."""
  resource = _OptionResource()

  async with resource.lifespan(option="outer"):
    assert resource.is_active
    with pytest.raises(RuntimeError, match="already has an active lifespan"):
      async with resource.lifespan(option="inner"):
        pytest.fail("nested entry must not reach the body")
    assert resource.is_active
    assert resource.option == "outer"

  assert not resource.is_active


@pytest.mark.anyio
async def test_exit_stack_attempts_all_callbacks_when_cleanup_fails() -> None:
  """A cleanup failure does not prevent earlier callbacks from being attempted."""
  attempts: List[str] = []

  async def fail(name: str) -> None:
    """Record one cleanup attempt and fail it."""
    attempts.append(name)
    raise RuntimeError(name)

  with pytest.raises(RuntimeError):
    async with AsyncExitStackWithShielding() as stack:
      stack.push_shielded_async_callback(fail, "first")
      stack.push_shielded_async_callback(fail, "second")

  assert attempts == ["second", "first"]


@pytest.mark.anyio
async def test_every_cleanup_failure_stays_reachable() -> None:
  """Every cleanup failure reaches the caller through the exception context chain."""
  attempts: List[str] = []

  async def fail(name: str) -> None:
    """Record one cleanup attempt and fail it."""
    attempts.append(name)
    raise RuntimeError(name)

  with pytest.raises(RuntimeError) as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      stack.push_shielded_async_callback(fail, "first")
      stack.push_shielded_async_callback(fail, "second")
      stack.push_shielded_async_callback(fail, "third")

  assert attempts == ["third", "second", "first"]
  assert _reachable_failures(exc_info.value) == ["first", "second", "third"]


@pytest.mark.anyio
async def test_every_registered_cleanup_kind_stays_reachable() -> None:
  """Synchronous and unshielded asynchronous cleanup failures also stay reachable."""
  attempts: List[str] = []

  def fail_sync(name: str) -> None:
    """Record one synchronous cleanup attempt and fail it."""
    attempts.append(name)
    raise RuntimeError(name)

  async def fail_async(name: str) -> None:
    """Record one asynchronous cleanup attempt and fail it."""
    attempts.append(name)
    raise RuntimeError(name)

  with pytest.raises(RuntimeError) as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      stack.callback(fail_sync, "first")
      stack.push_async_callback(fail_async, "second")
      stack.push_shielded_async_callback(fail_async, "third")

  assert attempts == ["third", "second", "first"]
  assert _reachable_failures(exc_info.value) == ["first", "second", "third"]


@pytest.mark.anyio
async def test_child_context_exit_failure_stays_reachable() -> None:
  """A child context manager failing to exit keeps earlier cleanup failures reachable."""
  attempts: List[str] = []

  class _FailingChild:
    """Fail asynchronous exit without inspecting the failure it unwinds."""

    def __init__(self, name: str) -> None:
      """Retain the name reported by this child's exit failure."""
      self._name = name

    async def __aenter__(self) -> None:
      """Enter the child context."""

    async def __aexit__(
      self,
      exception_type: Optional[type],
      exception: Optional[BaseException],
      traceback: Optional[Any],
    ) -> None:
      """Record the exit attempt and fail it.

      Args:
        exception_type: Type of the failure this exit unwinds, if any.
        exception: Failure this exit unwinds, if any.
        traceback: Traceback of the failure this exit unwinds, if any.

      Raises:
        RuntimeError: Always, to report a failed child exit.
      """
      attempts.append(self._name)
      raise RuntimeError(self._name)

  async def fail(name: str) -> None:
    """Record one cleanup attempt and fail it."""
    attempts.append(name)
    raise RuntimeError(name)

  with pytest.raises(RuntimeError) as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      await stack.enter_async_context(_FailingChild("first"))
      stack.push_shielded_async_callback(fail, "second")

  assert attempts == ["second", "first"]
  assert _reachable_failures(exc_info.value) == ["first", "second"]


@pytest.mark.anyio
async def test_cleanup_failure_chain_never_forms_a_cycle() -> None:
  """A cleanup failing with an already chained failure cannot create a context cycle."""
  reused = RuntimeError("reused")
  earlier = RuntimeError("earlier")
  earlier.__context__ = reused

  async def fail(error: BaseException) -> None:
    """Fail one cleanup with a caller-supplied failure."""
    raise error

  with pytest.raises(RuntimeError) as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      stack.push_shielded_async_callback(fail, reused)
      stack.push_shielded_async_callback(fail, earlier)

  assert exc_info.value is reused
  assert _reachable_failures(exc_info.value) == ["reused"]


@pytest.mark.anyio
async def test_successful_cleanup_never_suppresses_the_body_failure() -> None:
  """Cleanup registered through the stack cannot swallow the failure it unwinds."""
  attempts: List[str] = []

  async def succeed(name: str) -> None:
    """Record one successful asynchronous cleanup attempt."""
    attempts.append(name)

  with pytest.raises(ValueError, match="body failed"):
    async with AsyncExitStackWithShielding() as stack:
      stack.callback(attempts.append, "sync")
      stack.push_async_callback(succeed, "async")
      stack.push_shielded_async_callback(succeed, "shielded")
      raise ValueError("body failed")

  assert attempts == ["shielded", "async", "sync"]


@pytest.mark.anyio
async def test_generic_async_context_preserves_intentional_one_member_cleanup_group() -> None:
  """Generic async cleanup does not mistake an intentional group for task-group wrapping."""
  body_failure = RuntimeError("body failed")

  class _GroupingContext:
    """Group the body failure deliberately during cleanup."""

    async def __aenter__(self) -> None:
      """Enter without producing a value."""

    async def __aexit__(
      self,
      exception_type: Optional[type],
      exception: Optional[BaseException],
      traceback: Optional[Any],
    ) -> None:
      """Wrap the incoming body failure in an intentional cleanup group."""
      assert exception is body_failure
      raise BaseExceptionGroup("intentional cleanup group", [exception])

  with pytest.raises(BaseExceptionGroup, match="intentional cleanup group") as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      await stack.enter_async_context(_GroupingContext())
      raise body_failure

  assert exc_info.value.exceptions == (body_failure,)


@pytest.mark.anyio
async def test_managed_task_group_preserves_exact_scalar_body_failure() -> None:
  """The dedicated task-group boundary preserves scalar body identity and context."""
  failure = RuntimeError("body failed")
  prior_context = ValueError("prior context")
  failure.__context__ = prior_context

  with pytest.raises(RuntimeError, match="body failed") as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      task_group = await stack.enter_task_group()
      task_group.start_soon(anyio.sleep_forever)
      raise failure

  assert exc_info.value is failure
  assert exc_info.value.__context__ is prior_context


@pytest.mark.anyio
async def test_non_cancelling_group_waits_for_siblings_and_groups_failures_in_start_order() -> None:
  """An ordinary task failure does not prevent the others from finishing."""
  failures = (ValueError("first"), RuntimeError("second"))
  finished: List[str] = []

  async def fail(error: Exception) -> None:
    raise error

  async def succeed() -> None:
    await anyio.lowlevel.checkpoint()
    finished.append("sibling finished")

  with pytest.raises(SiblingTaskErrors) as raised:
    async with non_cancelling_task_group() as group:
      group.start_soon(fail, failures[0])
      group.start_soon(succeed)
      group.start_soon(fail, failures[1])

  assert finished == ["sibling finished"]
  assert raised.value.exceptions == failures


@pytest.mark.anyio
async def test_non_cancelling_group_body_failure_cancels_running_children() -> None:
  """A body failure retains ordinary task-group cancellation behavior."""
  started = anyio.Event()
  finished = anyio.Event()
  failure = RuntimeError("body failed")

  async def child() -> None:
    try:
      started.set()
      await anyio.sleep_forever()
    finally:
      finished.set()

  with pytest.raises(RuntimeError) as raised:
    async with non_cancelling_task_group() as group:
      group.start_soon(child)
      with anyio.fail_after(1):
        await started.wait()
      raise failure

  assert raised.value is failure
  assert finished.is_set()


@pytest.mark.anyio
async def test_non_cancelling_group_starts_tasks_while_body_is_active() -> None:
  """start_soon schedules work before the task-group body completes."""
  started = anyio.Event()

  async def child() -> None:
    started.set()

  async with non_cancelling_task_group() as group:
    group.start_soon(child)
    with anyio.fail_after(1):
      await started.wait()

  assert started.is_set()


@pytest.mark.anyio
async def test_non_cancelling_group_reports_prior_child_and_body_failures() -> None:
  """A body failure does not hide a child failure that was already collected."""
  child_failure = ValueError("child failed")
  body_failure = RuntimeError("body failed")

  async def child() -> None:
    raise child_failure

  with pytest.raises(BaseExceptionGroup) as raised:
    async with non_cancelling_task_group() as group:
      group.start_soon(child)
      await anyio.wait_all_tasks_blocked()
      raise body_failure

  assert set(raised.value.exceptions) == {body_failure, child_failure}


@pytest.mark.anyio
async def test_non_cancelling_group_propagates_external_cancellation() -> None:
  """Explicit cancellation still stops and joins the group's children."""
  started = anyio.Event()
  finished = anyio.Event()

  async def child() -> None:
    try:
      started.set()
      await anyio.sleep_forever()
    finally:
      finished.set()

  async def run_group() -> None:
    async with non_cancelling_task_group() as group:
      group.start_soon(child)

  async with anyio.create_task_group() as parent:
    parent.start_soon(run_group)
    await started.wait()
    parent.cancel_scope.cancel()

  assert finished.is_set()


@pytest.mark.anyio
async def test_managed_task_group_preserves_exact_grouped_body_failure() -> None:
  """The dedicated task-group boundary preserves a real body group's exact identity."""
  failures = (RuntimeError("first body failure"), ValueError("second body failure"))
  body_failure = BaseExceptionGroup("body failures", failures)

  with pytest.raises(BaseExceptionGroup, match="body failures") as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      task_group = await stack.enter_task_group()
      task_group.start_soon(anyio.sleep_forever)
      raise body_failure

  assert exc_info.value is body_failure
  assert exc_info.value.exceptions == failures


@pytest.mark.anyio
@pytest.mark.parametrize("failure_count", [1, 2])
async def test_managed_task_group_keeps_child_failures_grouped(failure_count: int) -> None:
  """The dedicated task-group boundary retains every exact child failure in its group."""
  failures = (RuntimeError("first child failed"), ValueError("second child failed"))
  ready = (anyio.Event(), anyio.Event())
  release = anyio.Event()

  async def fail(index: int) -> None:
    with anyio.CancelScope(shield=True):
      ready[index].set()
      await release.wait()
      raise failures[index]

  with pytest.raises(BaseExceptionGroup) as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      task_group = await stack.enter_task_group()
      for index in range(failure_count):
        task_group.start_soon(fail, index)
      for index in range(failure_count):
        await ready[index].wait()
      release.set()
      await anyio.sleep_forever()

  assert set(exc_info.value.exceptions) == set(failures[:failure_count])


@pytest.mark.anyio
@pytest.mark.parametrize("body_is_base_exception", [False, True])
async def test_managed_task_group_retains_simultaneous_body_and_child_failures(
  body_is_base_exception: bool,
) -> None:
  """The dedicated boundary reports exact body and child failures in the right group type."""

  class _BaseBodyFailure(BaseException):
    """Represent a non-Exception body failure for group selection."""

  body_failure: BaseException
  if body_is_base_exception:
    body_failure = _BaseBodyFailure("body failed")
  else:
    body_failure = RuntimeError("body failed")
  child_failure = ValueError("child failed")
  child_ready = anyio.Event()
  release_child = anyio.Event()

  async def fail_child() -> None:
    """Fail after the body has synchronized with this child."""
    with anyio.CancelScope(shield=True):
      child_ready.set()
      await release_child.wait()
      raise child_failure

  with pytest.raises(BaseExceptionGroup) as exc_info:
    async with AsyncExitStackWithShielding() as stack:
      task_group = await stack.enter_task_group()
      task_group.start_soon(fail_child)
      await child_ready.wait()
      release_child.set()
      raise body_failure

  if body_is_base_exception:
    assert type(exc_info.value) is BaseExceptionGroup
  else:
    assert type(exc_info.value) is ExceptionGroup
  assert set(exc_info.value.exceptions) == {body_failure, child_failure}


@pytest.mark.anyio
@pytest.mark.parametrize("body_is_base_exception", [False, True])
async def test_task_group_restoration_preserves_nested_group_metadata(
  body_is_base_exception: bool,
) -> None:
  """Every restored group retains source metadata without changing the source tree."""

  class _BaseBodyFailure(BaseException):
    """Represent a non-Exception body failure for group selection."""

  body_failure: BaseException
  if body_is_base_exception:
    body_failure = _BaseBodyFailure("body failed")
  else:
    body_failure = RuntimeError("body failed")
  child_failure = ValueError("child failed")
  sibling_failure = LookupError("sibling failed")
  marker = concurrency._TaskGroupBodyFailure()
  inner = BaseExceptionGroup("inner task failures", [marker, child_failure])
  outer = BaseExceptionGroup("outer task failures", [inner, sibling_failure])

  try:
    raise inner
  except BaseExceptionGroup:
    pass
  inner_cause = RuntimeError("inner cause")
  inner_context = RuntimeError("inner context")
  inner.__cause__ = inner_cause
  inner.__context__ = inner_context
  try:
    inner.add_note("inner note")
  except AttributeError:
    pass

  try:
    raise outer
  except BaseExceptionGroup:
    pass
  outer_cause = RuntimeError("outer cause")
  outer_context = RuntimeError("outer context")
  outer.__cause__ = outer_cause
  outer.__context__ = outer_context
  try:
    outer.add_note("outer note")
  except AttributeError:
    pass

  original_inner_members = inner.exceptions
  original_outer_members = outer.exceptions
  original_inner_traceback = inner.__traceback__
  original_outer_traceback = outer.__traceback__
  restored, marker_found = concurrency._restore_task_group_body_failure(outer, marker, body_failure)

  assert marker_found
  assert isinstance(restored, BaseExceptionGroup)
  restored_inner = restored.exceptions[0]
  assert isinstance(restored_inner, BaseExceptionGroup)
  expected_type = BaseExceptionGroup if body_is_base_exception else ExceptionGroup
  assert type(restored) is expected_type
  assert type(restored_inner) is expected_type
  assert restored_inner.exceptions == (body_failure, child_failure)
  assert restored.exceptions == (restored_inner, sibling_failure)

  for source, derived in ((inner, restored_inner), (outer, restored)):
    assert derived is not source
    assert derived.__traceback__ is source.__traceback__
    assert derived.__cause__ is source.__cause__
    assert derived.__context__ is source.__context__
    try:
      source_notes = source.__notes__
    except AttributeError:
      with pytest.raises(AttributeError):
        derived.__notes__
    else:
      assert derived.__notes__ == source_notes
      assert derived.__notes__ is not source_notes

  assert inner.exceptions is original_inner_members
  assert outer.exceptions is original_outer_members
  assert inner.__traceback__ is original_inner_traceback
  assert outer.__traceback__ is original_outer_traceback
  assert inner.__cause__ is inner_cause
  assert inner.__context__ is inner_context
  assert outer.__cause__ is outer_cause
  assert outer.__context__ is outer_context
  try:
    assert inner.__notes__ == ["inner note"]
    assert outer.__notes__ == ["outer note"]
  except AttributeError:
    pass


@pytest.mark.anyio
async def test_task_group_restoration_preserves_implicit_context_visibility() -> None:
  """Restoration retains an implicitly chained context without suppressing it."""
  marker = concurrency._TaskGroupBodyFailure()
  child_failure = ValueError("child failed")
  source = BaseExceptionGroup("task failures", [marker, child_failure])
  context_failure = RuntimeError("context failed")

  try:
    raise context_failure
  except RuntimeError:
    try:
      raise source
    except BaseExceptionGroup:
      pass

  assert source.__context__ is context_failure
  assert source.__cause__ is None
  assert source.__suppress_context__ is False

  body_failure = RuntimeError("body failed")
  restored, marker_found = concurrency._restore_task_group_body_failure(
    source, marker, body_failure
  )

  assert marker_found
  assert isinstance(restored, BaseExceptionGroup)
  assert restored.exceptions == (body_failure, child_failure)
  assert restored.__context__ is context_failure
  assert restored.__cause__ is None
  assert restored.__suppress_context__ is False


@pytest.mark.anyio
async def test_managed_task_group_external_cancellation_remains_scalar() -> None:
  """The dedicated task-group boundary never exposes its marker during cancellation."""
  entered = anyio.Event()
  observed: List[BaseException] = []

  async def run_managed_group() -> None:
    """Capture and re-raise cancellation from a managed task group."""
    try:
      async with AsyncExitStackWithShielding() as stack:
        task_group = await stack.enter_task_group()
        task_group.start_soon(anyio.sleep_forever)
        entered.set()
        await anyio.sleep_forever()
    except BaseException as error:
      observed.append(error)
      raise

  async with anyio.create_task_group() as outer_task_group:
    outer_task_group.start_soon(run_managed_group)
    await entered.wait()
    outer_task_group.cancel_scope.cancel()

  assert len(observed) == 1
  assert isinstance(observed[0], anyio.get_cancelled_exc_class())


@pytest.mark.anyio
async def test_cleanup_failure_while_cancelling_keeps_the_cancellation_reachable() -> None:
  """A cleanup failure replaces the cancellation it unwinds and keeps it reachable."""

  async def fail() -> None:
    """Fail one shielded cleanup."""
    raise RuntimeError("cleanup failed")

  with pytest.raises(RuntimeError) as exc_info:
    with anyio.CancelScope() as scope:
      async with AsyncExitStackWithShielding() as stack:
        stack.push_shielded_async_callback(fail)
        scope.cancel()
        await anyio.sleep_forever()

  assert isinstance(exc_info.value.__context__, anyio.get_cancelled_exc_class())


@pytest.mark.anyio
async def test_resource_is_active_only_while_body_is_running() -> None:
  """Entry and exit transitions are distinct from the fully active state."""
  resource = _StateResource()

  async def run_resource() -> None:
    """Drive one complete lifespan through externally controlled gates."""
    async with resource:
      resource.body_started.set()
      await resource.leave_body.wait()

  async with anyio.create_task_group() as task_group:
    task_group.start_soon(run_resource)
    await resource.enter_started.wait()
    assert not resource.is_active

    resource.allow_entry.set()
    await resource.body_started.wait()
    assert resource.is_active

    resource.leave_body.set()
    await resource.exit_started.wait()
    assert not resource.is_active
    resource.allow_exit.set()

  assert not resource.is_active


@pytest.mark.anyio
async def test_shield_timeout_bounds_async_cleanup() -> None:
  """A shield timeout bounds a cleanup callback that cannot finish."""
  cleanup_started = anyio.Event()
  never_finish = anyio.Event()

  async def cleanup() -> None:
    """Signal invocation and remain blocked."""
    cleanup_started.set()
    await never_finish.wait()

  with pytest.raises(TimeoutError):
    async with AsyncExitStackWithShielding() as stack:
      stack.push_shielded_async_callback(cleanup, shield_timeout=0)

  assert cleanup_started.is_set()


@pytest.mark.anyio
async def test_context_manager_forwards_lifespan_keyword_arguments() -> None:
  """The explicit lifespan forwards keyword options to resource acquisition."""
  resource = _OptionResource()

  async with resource.lifespan(option="value") as entered:
    assert entered is resource
    assert resource.option == "value"


def test_concrete_subclass_requires_a_lifespan_extension() -> None:
  """A concrete resource cannot omit both supported lifespan extension points."""
  with pytest.raises(TypeError, match="_enter_lifespan or lifespan"):

    class InvalidResource(HasLifespan):
      """Resource missing a lifespan implementation."""


@pytest.mark.anyio
async def test_direct_resource_can_compose_base_lifespan_entry() -> None:
  """A cooperative base hook leaves subclass acquisition and cleanup intact."""
  events: list[str] = []

  class ComposedResource(HasLifespan):
    """Own a resource after entering its base lifecycle layer."""

    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
      """Enter the parent hook before acquiring the child's resource."""
      await super()._enter_lifespan(stack)
      events.append("acquired")
      stack.callback(events.append, "released")

  async with ComposedResource() as resource:
    assert resource.is_active
    events.append("body")

  assert events == ["acquired", "body", "released"]


def test_abstract_subclass_may_defer_a_lifespan_extension() -> None:
  """An abstract resource may defer lifespan acquisition to a concrete subclass."""

  class AbstractResource(HasLifespan, ABC):
    """Abstract resource without lifecycle acquisition."""

    @abstractmethod
    async def operate(self) -> None:
      """Perform the resource operation."""

  with pytest.raises(TypeError, match="_enter_lifespan or lifespan"):

    class InvalidConcreteResource(AbstractResource):
      """Concrete resource that still omits lifecycle acquisition."""

      async def operate(self) -> None:
        """Perform no operation."""


def test_subclass_may_replace_the_complete_lifespan() -> None:
  """A complete lifespan context manager is a supported extension point."""

  class CustomResource(HasLifespan):
    """Resource providing its complete lifespan."""

    @asynccontextmanager
    async def lifespan(self, **kwargs: object) -> AsyncIterator[HasLifespan]:
      """Yield this resource from a custom lifespan."""
      yield self


@pytest.mark.anyio
async def test_custom_lifespan_may_compose_base_without_entry_override() -> None:
  """An explicit custom lifespan may delegate to the base's no-op acquisition."""
  events: list[str] = []

  class CustomResource(HasLifespan):
    """Compose the default lifespan without a separate entry implementation."""

    @asynccontextmanager
    async def lifespan(self, **kwargs: Any) -> AsyncIterator[HasLifespan]:
      """Own the custom body within the default lifespan's activity scope."""
      async with super().lifespan(**kwargs) as resource:
        events.append("enter")
        try:
          yield resource
        finally:
          events.append("exit")

  resource = CustomResource()
  async with resource:
    assert resource.is_active
    assert events == ["enter"]

  assert not resource.is_active
  assert events == ["enter", "exit"]


@pytest.mark.anyio
async def test_custom_lifespan_may_yield_another_resource() -> None:
  """A complete custom lifespan may expose a different active resource."""

  class DelegateResource(HasLifespan):
    """Resource exposed by its owner's complete lifespan."""

    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      """Provide a no-op lifespan for this test resource."""

  delegate = DelegateResource()

  class OwnerResource(HasLifespan):
    """Resource whose complete lifespan exposes its delegate."""

    @asynccontextmanager
    async def lifespan(self, **kwargs: Any) -> AsyncIterator[HasLifespan]:
      """Enter and expose the delegate resource."""
      async with delegate:
        yield delegate

  async with OwnerResource() as entered:
    assert_type(entered, HasLifespan)
    assert entered is delegate
    assert delegate.is_active

  assert not delegate.is_active


@pytest.mark.anyio
async def test_custom_lifespan_owns_its_activity_and_entry_policy() -> None:
  """A custom lifespan is not wrapped by the base lifecycle state machine."""
  events: List[str] = []

  class CustomResource(HasLifespan):
    """Resource recording its complete custom lifespan."""

    @asynccontextmanager
    async def lifespan(self, **kwargs: object) -> AsyncIterator[HasLifespan]:
      """Record entry and exit around the custom lifespan body."""
      events.append("enter")
      try:
        yield self
      finally:
        events.append("exit")

  resource = CustomResource()
  async with resource.lifespan():
    assert not resource.is_active
    async with resource.lifespan():
      assert not resource.is_active

  assert not resource.is_active
  assert events == ["enter", "enter", "exit", "exit"]


@pytest.mark.anyio
async def test_custom_lifespan_can_enter_parent_context_manager() -> None:
  """Custom context managers compose normally, without base-managed state."""
  events: List[str] = []

  class ParentResource(HasLifespan):
    """Resource providing the parent portion of a composed lifespan."""

    @asynccontextmanager
    async def lifespan(self, **kwargs: object) -> AsyncIterator[HasLifespan]:
      """Record parent entry and exit."""
      events.append("enter-parent")
      try:
        yield self
      finally:
        events.append("exit-parent")

  class ChildResource(ParentResource):
    """Resource composing child work around its parent lifespan."""

    @asynccontextmanager
    async def lifespan(self, **kwargs: object) -> AsyncIterator[HasLifespan]:
      """Record child entry and exit around the parent lifespan."""
      events.append("enter-child")
      try:
        async with super().lifespan(**kwargs) as resource:
          yield resource
      finally:
        events.append("exit-child")

  resource = ChildResource()
  async with resource.lifespan():
    assert not resource.is_active
    assert events == ["enter-child", "enter-parent"]

  assert not resource.is_active
  assert events == ["enter-child", "enter-parent", "exit-parent", "exit-child"]


@pytest.mark.anyio
async def test_custom_lifespan_can_enter_base_lifespan_explicitly() -> None:
  """Calling the base lifespan runs the resource's _enter_lifespan normally."""
  events: List[str] = []

  class CustomResource(HasLifespan):
    """Use the base lifespan explicitly inside a custom context manager."""

    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
      """Register one base-managed cleanup."""
      events.append("base.enter")
      stack.callback(events.append, "base.exit")

    @asynccontextmanager
    async def lifespan(self, **kwargs: Any) -> AsyncIterator[HasLifespan]:
      """Wrap the inherited base context without extra composition handling."""
      async with super().lifespan(**kwargs) as resource:
        events.append("custom.enter")
        try:
          yield resource
        finally:
          events.append("custom.exit")

  resource = CustomResource()
  async with resource.lifespan():
    assert resource.is_active
    assert events == ["base.enter", "custom.enter"]

  assert not resource.is_active
  assert events == ["base.enter", "custom.enter", "custom.exit", "base.exit"]


@pytest.mark.anyio
async def test_custom_lifespan_can_manage_its_own_activity_and_double_entry() -> None:
  """An override may provide its own state reporting and entry policy."""

  class CustomResource(HasLifespan):
    """Manage activity and overlapping entry without the base lifespan."""

    def __init__(self) -> None:
      """Start without an active custom lifespan."""
      super().__init__()
      self._entered = False

    @property
    def is_active(self) -> bool:
      """Report this custom lifespan's own activity state."""
      return self._entered

    @asynccontextmanager
    async def lifespan(self, **kwargs: object) -> AsyncIterator[HasLifespan]:
      """Reject overlapping entry and clear custom state on exit."""
      if self._entered:
        raise RuntimeError("already active")
      self._entered = True
      try:
        yield self
      finally:
        self._entered = False

  resource = CustomResource()
  async with resource.lifespan():
    assert resource.is_active
    with pytest.raises(RuntimeError, match="already active"):
      async with resource.lifespan():
        pytest.fail("nested entry reached body")
  assert not resource.is_active


def test_subclass_cannot_override_managed_lifecycle_methods() -> None:
  """Managed lifecycle entry points cannot be replaced by subclasses."""
  with pytest.raises(TypeError, match="__aenter__"):

    class InvalidEnterResource(_OptionResource):
      """Resource overriding managed context entry."""

      async def __aenter__(self) -> HasLifespan:
        """Enter the resource incorrectly."""
        return self

  with pytest.raises(TypeError, match="__aexit__"):

    class InvalidExitResource(_OptionResource):
      """Resource overriding managed context exit."""

      async def __aexit__(self, *args: object) -> None:
        """Exit the resource incorrectly."""

  with pytest.raises(TypeError, match="setup"):

    class InvalidSetupResource(_OptionResource):
      """Resource overriding managed compatibility setup."""

      async def setup(self, **lifespan_kwargs: Any) -> None:
        """Set up the resource incorrectly."""

  with pytest.raises(TypeError, match="stop"):

    class InvalidStopResource(_OptionResource):
      """Resource overriding managed compatibility stop."""

      async def stop(self) -> None:
        """Stop the resource incorrectly."""


def test_legacy_adapter_scaffolding_is_absent() -> None:
  package_root = Path(__file__).parent
  marker = "_legacy_lifecycle_" + "adapter"
  offenders = sorted(
    str(path.relative_to(package_root))
    for path in package_root.rglob("*.py")
    if path != Path(__file__) and marker in path.read_text(encoding="utf-8")
  )
  assert offenders == []


@pytest.mark.anyio
async def test_resource_without_super_init_starts_inactive() -> None:
  """A legacy constructor can enter its first lifespan without parent initialization."""
  resource = _NoSuperResource()
  assert not resource.is_active
  async with resource:
    assert resource.is_active
  assert not resource.is_active


@pytest.mark.anyio
async def test_inactive_defaults_do_not_share_active_state() -> None:
  """Entering one uninitialized instance does not activate another instance."""
  first = _NoSuperResource()
  second = _NoSuperResource()
  async with first:
    assert first.is_active
    assert not second.is_active


def test_mixin_cannot_supply_managed_lifecycle_methods() -> None:
  """Managed lifecycle entry points cannot be inherited from another base."""

  class EnterMixin:
    """Base supplying managed context entry."""

    async def __aenter__(self) -> Any:
      """Enter the resource incorrectly."""
      return self

  class ExitMixin:
    """Base supplying managed context exit."""

    async def __aexit__(self, *args: object) -> None:
      """Exit the resource incorrectly."""

  class SetupMixin:
    """Base supplying managed compatibility setup."""

    async def setup(self, **lifespan_kwargs: Any) -> None:
      """Set up the resource incorrectly."""

  class StopMixin:
    """Base supplying managed compatibility stop."""

    async def stop(self) -> None:
      """Stop the resource incorrectly."""

  with pytest.raises(TypeError, match="__aenter__"):

    class MixedEnterResource(EnterMixin, _OptionResource):
      """Resource resolving managed entry to a preceding base."""

  with pytest.raises(TypeError, match="__aexit__"):

    class MixedExitResource(ExitMixin, _OptionResource):
      """Resource resolving managed exit to a preceding base."""

  with pytest.raises(TypeError, match="setup"):

    class MixedSetupResource(SetupMixin, _OptionResource):
      """Resource resolving managed setup to a preceding base."""

  with pytest.raises(TypeError, match="stop"):

    class MixedStopResource(StopMixin, _OptionResource):
      """Resource resolving managed stop to a preceding base."""


def test_subclass_may_inherit_behavior_outside_the_managed_lifecycle() -> None:
  """A base that supplies no managed lifecycle method remains usable."""

  class HelperMixin:
    """Base supplying behavior outside the managed lifecycle."""

    def describe(self) -> str:
      """Describe this resource."""
      return "helper"

  class MixedResource(HelperMixin, _OptionResource):
    """Resource combining helper behavior with a managed lifecycle."""

  assert MixedResource().describe() == "helper"


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_setup_forwards_options_until_stop() -> None:
  """Deprecated setup forwards options and keeps the lifespan active until stop."""
  resource = _InteractiveResource([])

  with pytest.warns(DeprecationWarning):
    await resource.setup(option="value")

  assert resource.is_active
  assert resource.option == "value"
  await resource.stop()
  assert not resource.is_active


def test_interactive_manager_reuses_idle_runner_after_contention_across_loops() -> None:
  """Contended setup works again in a later asyncio.run session."""
  manager = concurrency.GlobalManager()

  async def run_pair() -> None:
    """Start two lifespans concurrently, then leave the manager idle."""
    resources = [_InteractiveResource([]), _InteractiveResource([])]
    await asyncio.gather(*(manager.manage_context(resource) for resource in resources))
    assert all(resource.is_active for resource in resources)
    await manager.stop_all()
    assert all(not resource.is_active for resource in resources)

  asyncio.run(run_pair())
  asyncio.run(run_pair())


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["trio"])
async def test_interactive_setup_rejects_trio() -> None:
  """Interactive setup directs Trio callers to scoped resource entry."""
  resource = _InteractiveResource([])

  with pytest.warns(DeprecationWarning), pytest.raises(RuntimeError, match="async with"):
    await resource.setup()

  assert resource.entries == 0


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["trio"])
async def test_interactive_stop_rejects_trio_when_unmanaged() -> None:
  """Interactive stop rejects Trio even when the resource is not managed."""
  resource = _InteractiveResource([])

  with pytest.raises(RuntimeError, match="async with"):
    await resource.stop()

  assert resource.entries == 0


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_setup_raises_entry_failure_and_can_retry() -> None:
  """A failed setup is surfaced and leaves no manager state that prevents retry."""
  resource = _InteractiveResource([], fail_entries=1)

  with (
    pytest.warns(DeprecationWarning),
    pytest.raises(RuntimeError, match="interactive entry failed"),
  ):
    await resource.setup()

  assert not resource.is_active
  with pytest.warns(DeprecationWarning):
    await resource.setup()
  assert resource.is_active
  await resource.stop()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_cancelled_setup_surfaces_cleanup_failure() -> None:
  """Cancelled setup surfaces failing cleanup while retaining cancellation context."""
  resource = _CancelledSetupResource()

  with pytest.warns(DeprecationWarning):
    setup_task = asyncio.create_task(resource.setup())
    await resource.entry_started.wait()
    setup_task.cancel()
    resource.allow_entry.set()
    with pytest.raises(RuntimeError, match="cancelled setup cleanup failed") as exc_info:
      await setup_task

  assert isinstance(exc_info.value.__context__, asyncio.CancelledError)
  assert not resource.is_active


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_stop_raises_exit_failure() -> None:
  """A managed lifespan exit failure reaches the caller of stop."""
  resource = _InteractiveResource([], exit_failure="interactive exit failed")
  with pytest.warns(DeprecationWarning):
    await resource.setup()

  with pytest.raises(RuntimeError, match="interactive exit failed"):
    await resource.stop()

  assert not resource.is_active


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_cancelled_release_completes_manager_cleanup() -> None:
  """Cancelling a release still exits the lifespan and clears manager state."""
  events: List[str] = []
  cleanup_barrier = anyio.Event()
  resource = _InteractiveResource(events, cleanup_barrier=cleanup_barrier)
  manager = concurrency.GlobalManager()
  await manager.manage_context(resource)

  release_task = asyncio.create_task(manager.release_context(resource))
  with anyio.fail_after(1):
    while events != ["cleanup"]:
      await anyio.lowlevel.checkpoint()
    release_task.cancel()
    cleanup_barrier.set()
    with pytest.raises(asyncio.CancelledError):
      await release_task

  assert not resource.is_active
  assert manager._contexts == {}
  assert manager._running_task is None
  await manager.manage_context(resource)
  assert resource.is_active
  await manager.release_context(resource)
  assert manager._contexts == {}


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_cancelled_release_surfaces_cleanup_failure() -> None:
  """A cancelled release reports its cleanup failure with cancellation context."""
  events: List[str] = []
  cleanup_barrier = anyio.Event()
  resource = _InteractiveResource(
    events, exit_failure="interactive exit failed", cleanup_barrier=cleanup_barrier
  )
  manager = concurrency.GlobalManager()
  await manager.manage_context(resource)

  release_task = asyncio.create_task(manager.release_context(resource))
  with anyio.fail_after(1):
    while events != ["cleanup"]:
      await anyio.lowlevel.checkpoint()
    release_task.cancel()
    cleanup_barrier.set()
    with pytest.raises(RuntimeError, match="interactive exit failed") as exc_info:
      await release_task

  assert isinstance(exc_info.value.__context__, asyncio.CancelledError)
  assert not resource.is_active
  assert manager._contexts == {}
  assert manager._running_task is None


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_duplicate_setup_cannot_enter_twice() -> None:
  """A second setup cannot create another lifespan for the same resource."""
  resource = _InteractiveResource([])
  with pytest.warns(DeprecationWarning):
    await resource.setup()

  with (
    pytest.warns(DeprecationWarning),
    pytest.raises(RuntimeError, match="already has an active lifespan"),
  ):
    await resource.setup()

  assert resource.entries == 1
  await resource.stop()


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_stop_all_attempts_every_exit_simultaneously() -> None:
  """Concurrent stop-all starts every cleanup before surfacing an exit failure."""
  cleanup_barrier = anyio.Event()
  first_events: List[str] = []
  second_events: List[str] = []
  first = _InteractiveResource(
    first_events, exit_failure="interactive exit failed", cleanup_barrier=cleanup_barrier
  )
  second = _InteractiveResource(second_events, cleanup_barrier=cleanup_barrier)
  manager = concurrency.GlobalManager()
  await manager.manage_context(first)
  await manager.manage_context(second)

  errors: List[BaseException] = []

  async def stop_all() -> None:
    """Capture the failure returned to the stop-all caller."""
    try:
      await manager.stop_all()
    except BaseException as error:
      errors.append(error)

  with anyio.fail_after(1):
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(stop_all)
      while first_events != ["cleanup"] or second_events != ["cleanup"]:
        await anyio.lowlevel.checkpoint()
      cleanup_barrier.set()

  assert first_events == ["cleanup"]
  assert second_events == ["cleanup"]
  assert len(errors) == 1
  assert "interactive exit failed" in _formatted_failure(errors[0])


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_interactive_stop_all_keeps_every_exit_failure_visible() -> None:
  """Concurrent stop-all exits every resource and surfaces each exit failure."""
  cleanup_barrier = anyio.Event()
  failures = ["first exit failed", "second exit failed", "third exit failed"]
  events: List[List[str]] = [[] for _ in failures]
  resources = [
    _InteractiveResource(resource_events, exit_failure=failure, cleanup_barrier=cleanup_barrier)
    for resource_events, failure in zip(events, failures)
  ]
  manager = concurrency.GlobalManager()
  for resource in resources:
    await manager.manage_context(resource)

  errors: List[BaseException] = []

  async def stop_all() -> None:
    """Capture the failure returned to the stop-all caller."""
    try:
      await manager.stop_all()
    except BaseException as error:
      errors.append(error)

  with anyio.fail_after(1):
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(stop_all)
      while any(resource_events != ["cleanup"] for resource_events in events):
        await anyio.lowlevel.checkpoint()
      cleanup_barrier.set()

  assert events == [["cleanup"], ["cleanup"], ["cleanup"]]
  assert not any(resource.is_active for resource in resources)
  assert manager._contexts == {}
  assert len(errors) == 1
  reported = _formatted_failure(errors[0])
  assert [failure for failure in failures if failure in reported] == failures
