# Structured Concurrency in PyLabRobot

## API

In PyLabRobot, all asynchronous resources expose the [`pylabrobot.concurrency.AsyncResource` API](pylabrobot.concurrency.AsyncResource): Resources are usable exactly within the body of `async with resource:`.
What exactly *usable* means may depend on the resource though,
as some functionality *may* be available outside the `async with` block too.
Unless that is specified by the API for a specific resource, you should not rely on it.

### Implementing `AsyncResource`

When implementing `AsyncResource` for a new class, you should not write `__aenter__` and `__aexit__` directly, as this is difficult to get right.
Instead, you should implement the `_lifespan` async context manager.
It is often most convenient to do so in terms of a `contextlib.AsyncExitStack`,
so the default implementation of `_lifespan` does that and delegates to a `_enter_lifespan(stack)` coroutine.
There is no `_exit_lifespan` (because separate enter and exit calls are the antithesis of structured concurrency),
instead, all cleanup is registered with the `stack`.

### Legacy `setup`/`stop` calls

For historical reasons and to support certain interactive use-cases,
we still expose a `setup`/`stop` API in subclasses of `Machine`.
Note however that, with this API, you give away control over the scope of the async work: For example, there is no way to reliably catch all errors in background tasks, or to handle cancellation of tasks consistenly. Do not use that in production scripts.

## Testing

Previous testing within PLR relied on `unittest.IsolatedAsyncioTestCase`.
Unfortunately, the `unittest` paradigm is fundamentally incompatible with structured concurrency.
There is no structured scope enclosing the tests, and all attempts to work around this failed.

Instead, we provide `pylabrobot.testing.concurrency.AnyioTestBase`.
This is *not* a `unittest.TestCase` on purpose, in order not to trigger `pytest`'s
`unittest` compatibility mode. It *does* however reimplement the asserts from `unittest`,
as to streamline test conversion.
Test cases can be left as-is, but the `setUp`/`asyncSetUp` / `tearDown`/`asyncTearDown` logic needs to be replaced by a `_lifespan` or `_enter_lifespan` implementation (it is a `AsyncResource` itself).

### Gotchas:
- `unittest.AsyncMock` creates `async` methods that do never yield.
  This is a problem if they are used in a tight loop, with no other yield point;
  leading to a deadlock. This appears in the wild in reader loops of I/O plumbing,
  so we provide `pylabrobot.testing.mock_io.MockIO` as a more focussed alternative.

## TODOs in the refactor

### References to `setup`
 - Developer docs
 - Many error messages
 - `.setup_done()` calls

### References to `unittest`
 - Async tests now *require* pytest - let's remove all calls to `unittest.main()`
