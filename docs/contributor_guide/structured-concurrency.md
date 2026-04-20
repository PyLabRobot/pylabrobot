# Structured Concurrency in PyLabRobot

## API

In PyLabRobot, all asynchronous resources expose the `pylabrobot.concurrency.AsyncResource` API: Resources are usable exactly within the body of `async with resource:`.
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
Note however that, with this API, you give away control over the scope of the async work: For example, there is no way to reliably catch all errors in background tasks, or to handle cancellation of tasks consistently. Do not use that in production scripts.

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

## Notes from the refactor:
- Timeout semantics may have changed slightly. Usually, that's the case because previous
  timeout semantics are often confusing or ill specified (because without structured concurrency,
  it's very hard to implement good timeout semantics). We tried to stay as close as possible to the previous semantics. That said, going forward, one `timeout` arguments should always be a trigger to take a step back and think about semantics: Is it supposed to be a timeout on the full operation? Then, *don't* put a `timeout` argument at all! Users are better served by wrapping
  *the whole operation* with `with anyio.fail_after`. If the timeout somehow applies to sub-parts,
  then be very careful in specifying to what they apply (and what is being done if timeouts fail).

## Limitations:
 - The Opentrons thermocycler USB backend is `asyncio`-only.

## Issues found during the refactor

### Unstructured start/stop behaviours that might be better off as context manager
- `shake` and `stop_shaking` on Agilent Biotek.

### Inconsistent "turn-off" behaviout of various machines.
Most machines seem to turn off any ongoing actions and go back to some form of "parking position", but other machines don't:
- Tecan EVO has a number of arms that one could park; currently, we don't.


## TODOs in the refactor

### References to `setup`
 - Developer docs
 - Many error messages
 - `.setup_done()` calls

### References to `unittest`
 - Async tests now *require* pytest - let's remove all calls to `unittest.main()`

### Check for other signs that are frowned upon with structured concurrency:
 - Anything involving `time.time()` or `time.monotonic()` - should at least be `anyio.current_time()`, but often is a sign for a busy-loop or manual timeout handling.
 - Check for use of `threading`.
 - Check for use of `asyncio` - avoid raw `asyncio` APIs, should all be converted to `anyio` or something else that is loop-agnostic.

### Verification checks for changes already made
 - `_enter_lifespan` extra arguments other than `stack` should be *keyword-only*!
 - Have a look at all `stack.push_async_callback`, especially for `cleanup()` functions - these could often in fact be sync.
 - Verify that all cleanup logic has cancellation-shielding in place where necessary.

### Things to watch out for
- We never ever catch a cancellation without re-raising. In basic `asyncio`, that might be ok, but in structured concurrency, it never is.