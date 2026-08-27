---
name: convert-backend-structured-concurrency
description: Use when migrating a custom PyLabRobot backend or device driver with setup/stop, background tasks, threads, or owned I/O to an AnyIO structured-concurrency lifespan.
---

# Convert a PyLabRobot backend to structured concurrency

One owner, one lifespan: no I/O, worker, or safety cleanup outlives its owner. Before editing, read
`docs/contributor_guide/structured-concurrency.md`,
`docs/contributor_guide/structured-concurrency-gotchas.md`, and
`docs/contributor_guide/contributing.md`.

## Workflow

1. **Inventory.** Find the implementation (legacy backends live under `pylabrobot/legacy/`,
   not top-level compatibility imports). Read its frontend, transport, callers, and tests. Record
   setup options, wire order, results, events, durable state, and **owned versus borrowed** resources.
2. **Map cancellation.** Inspect entry, every operational await, reconnect, and exit. For each
   mutation, locate the last cancellable point, possible dispatch/commit, finite reconciliation,
   and rollback owner. A cancelled write may already have committed. Preserve the protocol and
   existing read strategy; do not invent safety commands.
3. **Characterize first.** Write failing software-double regressions for wire behavior and
   lifecycle: partial entry, normal/body-failure exit, cancellation before/after mutation,
   cleanup failure, and sequential reuse as applicable.
4. **Migrate atomically.** Implement native
   `_enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs)`; concrete
   `MachineBackend` classes need it even when customizing `lifespan()`. Remove custom or aliased
   `setup`, `stop`, `__aenter__`, and `__aexit__` from the hierarchy in the same change. Enter
   owned children through the stack; never enter/close borrowed ones. Register private shielded
   safety cleanup as soon as mutation may commit, after the transport so it runs before close.
   Enter task groups with `await stack.enter_task_group()`; register cancellation after entry to
   stop endless workers on normal exit, then join before closing dependencies. If safety needs a
   reader, register safety after the cancel callback. Reconcile finite committed/uncertain commands
   before propagating cancellation; let failures reach the owner.
5. **Verify.** Use `AnyioTestBase` under asyncio and Trio; restrict to asyncio only for a guarded
   asyncio-only dependency. Check timeouts, worker failure, exception context, cleanup order,
   and relevant lint/format/type/docs gates. Report software results separately from device
   results. Never operate real hardware without explicit approval **for that run**.

## Search checklist

Search the implementation, owned children, callers, and tests; review each match in context:

- `import asyncio`, `from asyncio`, `asyncio.` calls, and `AsyncMock`: use AnyIO for tasks, locks,
  sleeps, and transport doubles unless an integration inherently requires asyncio; guard that
  boundary before acquisition.
- `import time`, `from time`, and `time.` calls: use `anyio.sleep()` and `anyio.current_time()` for
  async waits and elapsed time, but keep wall-clock timestamps when they are part of the API.
- `create_task`, `Thread`, `to_thread`, `setup(`, `stop(`, and `timeout`: find detached work,
  blocking calls, alternate lifecycle paths, and deadlines that need ownership review.
- Explicit `.aclose()`, `.close()`, `.__aexit__()`, or `.__exit__()` (and direct entry calls):
  demand thorough justification and review of ownership, scope nesting, and exactly-once cleanup.
  Prefer `async with`, `with`, or an owned exit stack; see the gotchas for entry-timeout examples.

## Quick reference

| Situation | Check |
| --- | --- |
| Transport opened, then initialization fails | Stack closes transport; safety cleanup precedes close if state may have changed. |
| Write may have committed before cancellation | Serialize whole transaction; finish finite response/abort under a bound, then propagate cancellation. |
| Reader or blocking SDK call | Task group owns reader; `anyio.to_thread.run_sync` does not abandon stateful work. |
| Reconnect or shared interface | Reuse a nested owned exit stack for replacement; never close a borrowed interface. |

Example: a backend owns Serial but borrows an active control box. Enter `Serial.lifespan()` in its
stack; register shielded stop while Serial is open. Never enter or close the control box. A failed
initialization test must observe Serial closure without closing the box.

## Common mistakes

- Production `setup()`/`stop()` calls: deprecated asyncio-only interactive bridge.
- `stack.enter_async_context(anyio.create_task_group())`: use `stack.enter_task_group()` to
  preserve body-failure identity.
- Timed-out write treated as harmless; unknown state discarded; stateful worker abandoned;
  cancellation swallowed; software doubles claimed as physical validation.
