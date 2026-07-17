# Structured Concurrency in PyLabRobot

This guide outlines the principles of structured concurrency as applied in PyLabRobot (PLR). All new features and refactorings must adhere to these guidelines to ensure robust resource management, error handling, and cancellation semantics.

## 1. Background: What is Structured Concurrency?

Structured concurrency is a programming paradigm that treats concurrent paths of execution as nested scopes, ensuring that child tasks are guaranteed to complete (or be cancelled) before their parent scope exits.

For a detailed explanation of the concept and its benefits over unstructured concurrency (e.g., raw threads or raw `asyncio.create_task` - sometimes termed `go` statements), read the seminal blog post:
[Notes on structured concurrency, or: Go statement considered harmful](https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/) by Nathaniel J. Smith.

In short, structured concurrency brings the same safety guarantees to concurrent programming that structured programming (if/then, loops) brought over `goto` statements. It guarantees that:
*   Tasks do not outlive their scope (no orphan background tasks).
*   Errors are reliably propagated up the task tree.
*   Cancellation is consistent and propagates down to all child tasks.

---

## 2. How Structured Concurrency is Implemented in PLR

In PyLabRobot, structured concurrency is implemented on top of [Anyio](https://anyio.readthedocs.io/), a loop-agnostic asynchronous concurrency library.

### The `AsyncResource` API
All asynchronous resources (backends, decks, readers, etc.) must expose the `pylabrobot.concurrency.AsyncResource` API.
Resources are usable **only** within the body of an `async with` block:

```python
# `resource` is any AsyncResource (a backend, deck, reader, a LiquidHandler, ...)
async with resource:
    await resource.do_something(...)
    # resource is guaranteed to be initialized here and cleaned up when exiting the block
```

### Implementing `AsyncResource`
When implementing a new resource, do not write `__aenter__` and `__aexit__` directly. Instead, implement the `lifespan` async context manager.

Most resources should use `_enter_lifespan(stack)` to register their setup and cleanup steps. We use `AsyncExitStackWithShielding` (a subclass of `AsyncExitStack` defined in `pylabrobot.concurrency`) which provides a helper for shielding async cleanups:

1.  Inherit from `AsyncResource`.
2.  Implement `async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:`, decorate it with `@override` (from `typing_extensions`), and call `await super()._enter_lifespan(stack)` first so the whole class chain is entered.
3.  Register sync cleanup actions with the `stack` using `stack.callback(sync_func)`.
4.  Register async cleanup actions with the `stack` using `stack.push_shielded_async_callback(async_func)`.
    *   *Note*: **Always** prefer `push_shielded_async_callback` over `push_async_callback` for cleanup to ensure it runs to completion even if the enclosing scope was cancelled (see [3h. Cancellation Shielding](#3h-cancellation-shielding-in-cleanup-actions)).
5.  **No `_exit_lifespan` exists**: All cleanup must be registered dynamically with the stack during entry.
6.  **Extra parameters must be keyword-only**: if `_enter_lifespan` needs arguments beyond `stack`, they must be keyword-only, because they are forwarded through `lifespan(**kwargs)`.

Example:
```python
class MyBackend(AsyncResource):
    @override
    async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
        await super()._enter_lifespan(stack)
        await self._connect()
        # Register async cleanup with shielding (private disconnect method)
        stack.push_shielded_async_callback(self._disconnect)
```

### Testing Async Code
We use `pylabrobot.testing.concurrency.AnyioTestBase` for testing asynchronous code.
*   **Do not use** `unittest.IsolatedAsyncioTestCase` as it is incompatible with structured scopes.
*   `AnyioTestBase` is *not* a `unittest.TestCase` to avoid triggering pytest's legacy compatibility modes.
*   **Prefer `pytest` assertions**: Write normal Python `assert a == b` statements instead of using legacy `unittest` assertion helpers (like `self.assertEqual`). The helpers are only kept for backwards compatibility during migration.
*   Test classes themselves act as `AsyncResource`s. Test setup and teardown should be implemented via `_enter_lifespan` (or a `lifespan` context manager), **not** `setUp`/`tearDown` (which raise).
*   **Tests run under both `asyncio` and `trio`** by default, which enforces that your code is loop-agnostic. Pin an inherently asyncio-only component to a single backend with `_anyio_backends = ["asyncio"]` on the test class (e.g. anything built on `websockets`).

---

## 3. Rules and Gotchas

When writing new code or merging upstream changes, strictly adhere to the following rules. **You are encouraged to override upstream design decisions if they violate these rules.**

### 3a. No `setup()` and/or `stop()` Implementation or Use
*   **Rule**: Never implement or manually call `setup()` or `stop()` methods on resources.
*   **Rationale**: These methods create unstructured lifespans where resources can be left open if an error occurs.
*   **Resolution**: Subclasses of `AsyncResource` are strictly forbidden from implementing `setup()` and/or `stop()`. Use `_enter_lifespan` and register cleanup callbacks instead.
*   **Exception**: Legacy `setup`/`stop` APIs may exist on some classes for interactive/notebook use cases, but they must not be used in library code or production scripts, and are hard to use correctly anyway.

### 3b. Expose Context Managers, Not Start/End Methods
*   **Rule**: Avoid exposing separate start/end methods in the public API (e.g., `start_shaking()` and `stop_shaking()`).
*   **Rationale**: Separate calls are prone to leaks if the code in between raises an exception.
*   **Resolution**: Expose a context manager that defines the active scope of the operation:
    ```python
    # Bad
    await incubator.start_shaking()
    await do_something()
    await incubator.stop_shaking()

    # Good
    async with incubator.shaking():
        await do_something()
    ```

### 3c. No Manual Timeout Loops or API `timeout` Arguments
*   **Rule**: Do not use manual timeout loops (polling `time.time()` in a loop). Do not add `timeout` arguments to public APIs.
*   **Rationale**: Anyio provides structured timeout context managers which are safer and more flexible. Adding `timeout` to every function clutters the API.
*   **Resolution**:
    *   Use `with anyio.fail_after(timeout):` to bound operations. Note that `fail_after` (and `move_on_after`) are *synchronous* context managers, so use plain `with`, not `async with`.
    *   Only use internal timeouts if the implementation requires specific hardware-level timeouts that the user cannot configure.
    *   **Caveat**: `fail_after` does **not** interrupt a blocking call already running inside `anyio.to_thread.run_sync` — the worker thread is shielded by default. Wrapping `to_thread.run_sync` in `fail_after` will not bound wall-clock time unless you also pass `abandon_on_cancel=True`.

### 3d. No `asyncio`
*   **Rule**: Do not import or use `asyncio` APIs directly (e.g., `asyncio.sleep`, `asyncio.create_task`, `unittest.mock.AsyncMock`).
*   **Rationale**: Code must remain loop-agnostic by using `anyio`.
*   **Resolution**: Use `anyio` equivalents. Use `pylabrobot.testing.mock_io.MockIO` instead of `AsyncMock` to avoid deadlocks in mock reader loops.
*   **Exception**: A few components are inherently `asyncio`-only (e.g. those built on `websockets`, or the legacy `global_manager`). These must guard their entry with a `sniffio.current_async_library()` check that raises on non-`asyncio` backends, rather than silently misbehaving.

### 3e. No `time.sleep` or `time.monotonic`
*   **Rule**: Do not use blocking time functions.
*   **Resolution**:
    *   Replace `time.sleep(t)` with `await anyio.sleep(t)`.
    *   Replace `time.monotonic()` or `time.time()` (for duration tracking) with `anyio.current_time()`.
    *   **Exception**: Do not blindly convert *API-facing* `time.time()` calls (e.g. a wall-clock timestamp returned to the user) — that is a different clock. Only convert `time.*` calls used for internal duration tracking or sleeps.

### 3f. Avoid Background Threads
*   **Rule**: Avoid long-running background threads. PLR concurrency should be I/O-bound and run on the async event loop.
*   **Resolution**: Use Anyio task groups (`async with anyio.create_task_group()`) for concurrent async tasks. If you must call a blocking synchronous API, wrap it in `anyio.to_thread.run_sync`, keeping the sync function stateless and minimal.

### 3g. Never Swallow Cancellation Exceptions
*   **Rule**: Never catch a cancellation exception without re-raising it.
*   **Rationale**: Swallowing cancellation prevents the task group from tearing down properly and leads to hung tasks.
*   **Resolution**: If you must catch `anyio.get_cancelled_exc_class()` to perform emergency local cleanup, you **must** re-raise it:
    ```python
    try:
        await do_something()
    except anyio.get_cancelled_exc_class():
        # perform local non-async cleanup if needed
        raise # MUST re-raise
    ```

### 3h. Cancellation Shielding in Cleanup Actions
*   **Rule**: Async cleanup actions registered in `_enter_lifespan` must be shielded from cancellation.
*   **Rationale**: If a scope is cancelled (e.g., via Ctrl-C or timeout), the cancellation propagates to all active tasks. If a cleanup function performs an `await` (e.g., sending a shutdown command to a robot), that `await` would immediately raise a `CancelledError` and abort the cleanup, leaving the hardware in an unsafe state.
*   **Resolution**: Use `stack.push_shielded_async_callback` to register async cleanups. This wraps the cleanup in a shielded cancellation scope (`with anyio.CancelScope(shield=True):`), ensuring it runs to completion even if the main task was cancelled.