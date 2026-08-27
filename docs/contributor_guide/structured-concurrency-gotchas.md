# Structured concurrency: design and migration gotchas

This is a practical checklist for designing or converting a custom backend or driver. Start with the
[resource lifecycle contract](structured-concurrency.md) for API details. A coding agent can follow
the repo-local skill at
[`.agents/skills/convert-backend-structured-concurrency/SKILL.md`](https://github.com/PyLabRobot/pylabrobot/blob/main/.agents/skills/convert-backend-structured-concurrency/SKILL.md).
For a *new* v1 device driver, also read the [device-driver guide](device-driver-guide.md). Legacy
`MachineBackend` implementations live under `pylabrobot/legacy/`; top-level compatibility imports
are not where a legacy backend's lifecycle is implemented.

## Search checklist

Search the driver, owned children, callers, and tests, then inspect each match in context:

- `import asyncio`, `from asyncio`, `asyncio.` calls, and `AsyncMock`: replace direct tasks, locks,
  and sleeps with AnyIO; inherently asyncio-only dependencies need a guard before resource
  acquisition and asyncio-only integration tests. In test reader loops, use
  [MockIO](structured-concurrency.md#mock-io) rather than assuming `AsyncMock` behaves like a transport.
- `import time`, `from time`, and `time.` calls: use `await anyio.sleep()` for async waits and
  `anyio.current_time()` for elapsed durations. Keep wall-clock timestamps exposed to users as
  wall-clock time.
- `create_task`, `Thread`, `to_thread`, `setup(`, `stop(`, and `timeout`: trace who owns each
  worker, blocking call, lifecycle entry, and deadline. A search hit is a review prompt, not
  automatically a defect.
- Explicit `.aclose()`, `.close()`, `.__aexit__()`, or `.__exit__()` calls (and direct entry-method
  calls) are a sign of unidiomatic ownership and may be a bug. Require thorough justification and
  review: check exactly-once exit, cancel-scope nesting, and exception propagation. Usually
  `async with`, `with`, or an owned exit stack is better.

## Inventory ownership before changing code

For each object, write down which resources it **owns** (transport, SDK handle, task, thread,
server, child driver) and which it **borrows** from a caller or parent. The owner enters and closes
the resource; a borrower requires it to be active, but must not enter or close it. Delegating
frontends should not create a second owner for the same operation. Trace entry, every operation
that mutates device state, reconnect, and exit—not just `setup()` and `stop()`.

If a driver relies on one uninterrupted connection, require a `ContinuousConnectionIO` rather
than assuming every `IOBase` has that guarantee. Determine independently whether the device
excludes other controllers (perhaps only on one claimed interface) and what state can change
without this driver. A reconnectable transport cannot establish connection continuity for its
whole lifespan; consumers sharing it must coordinate connection-identity checks and
resynchronization with their complete transactions. A lost reply does not justify replaying a
possibly committed command.

During a lifecycle-only conversion, preserve observable behavior: setup options, protocol bytes
and command order, finite versus continuous reads, results, events, and documented safety actions.
Converting a per-command reader into a daemon or adding a new stop command needs separate
device-specific design and validation. Software doubles cannot tell you that a new command is safe
on hardware.

## Make the lifecycle native in one change

`HasLifespan` owns `__aenter__`, `__aexit__`, `setup`, and `stop`. Its metaclass rejects custom
definitions, aliases, or inherited replacements. A concrete legacy `MachineBackend` must implement
`_enter_lifespan()` or `lifespan()`. Migrate its entry and cleanup together; do not leave another
public entry route.
The inherited `setup()`/`stop()` bridge is deprecated and asyncio-only. It enters a lifespan in an
application-global manager task, outside the caller's lexical scope. Only application-level
interactive code may use it (for example, a labelled notebook that balances `setup()` with
`stop()`); library code must not call it on an owned child, even when running on asyncio. Enter the
child's lifespan through `async with` or the parent's exit stack instead. When overriding
`_enter_lifespan`, call `await super()._enter_lifespan(stack)` so an intermediate parent can acquire
its resources. The terminal hooks on `HasLifespan` and `IOBase` are no-ops; `MachineBackend`
inherits that hook without satisfying the requirement for a lifespan extension. Pass
only options accepted by the parent, and declare lifespan-specific options as explicit keyword-only
parameters; do not accept `**kwargs` solely to reject them. Validators, simulators, and
chatterboxes that intentionally *replace* a parent's hardware-owning entry must omit `super()` and
document which ownership or acquisition they substitute. They must register their own cleanup.

Enter owned child lifespans with `await stack.enter_async_context(child.lifespan())`. Register
cleanup as soon as ownership is established: synchronous cleanup with `stack.callback(...)`,
asynchronous safety work with `stack.push_shielded_async_callback(..., shield_timeout=...)` when
it needs a finite bound. Reverse registration order is exit order: enter the transport first,
then register a stop action, so the stop runs while the transport is still open. Register every
independent cleanup action; an earlier failure must not prevent later safety actions or close.
If a cleanup raises, it takes precedence over an earlier body error or cancellation, which remains
reachable through the exception's `__context__` chain. Do not assert that the body error must
always be the outermost exception.

## Treat every await as a cancellation boundary

An acquisition or write may have changed external state *before* its await returns. For each
command, distinguish: waiting for a lock; pre-write cancellable checkpoint; write possibly
committed; finite response or recovery; and publication of known state. Cancellation before
mutation should remain effective. After mutation may have committed, keep the handle open and
reconcile its finite reply, abort, or existing safety path before propagating cancellation. A
timeout does not prove the write had no effect; retain active or unknown state until a supported
recovery or later teardown establishes otherwise. Register rollback at the earliest reliable
dispatch/commit point, including when the mutation await itself can be interrupted. Shield the
necessary reconciliation but give finite waits a justified bound. Do not replace a public operation
deadline with a short internal transport polling timeout, or change the protocol merely to make
cancellation easier.

An AnyIO lock must span the **whole** write, response, parse, and state commit, not just the write.
Use `anyio.current_time()` for elapsed time, `await anyio.sleep()` for async waits, and
`with anyio.fail_after(timeout):` for caller-owned deadlines. Propagate cancellation; if catching
it to perform local cleanup, re-raise it. Preserve an earlier failure when wrapping errors.

### Design timeout boundaries around the caller's work

For new functionality or an intentional API redesign, prefer a caller-owned cancellation scope to
adding a `timeout` argument to every operation. Function boundaries often do not match the work
the caller needs to bound: `with anyio.fail_after(total_timeout):` can cover several calls, while
`with anyio.move_on_after(total_timeout):` can cancel them without raising on expiry. Both are
synchronous context managers, even inside async code. Keep per-operation timeout parameters when
they express a real device or protocol requirement; a lifecycle-only conversion preserves existing
public timeout behavior rather than silently redesigning it.

Separate the caller's overall deadline from internal transport polling or SDK timeouts. An AnyIO
timeout cannot preempt a blocking call running in a non-abandoning `anyio.to_thread.run_sync` worker:
use finite underlying I/O timeouts or polling where supported and wait for any committed mutation
to reconcile before releasing its handle. Do not abandon a stateful worker just to make an outer
deadline return sooner; do not mistake an internal polling timeout for the public operation deadline.

For stream-like I/O, scope the *whole reply*, not each chunk. A positive-size `IOBase.read()` waits
for at least one byte (up to the requested size), raises `anyio.EndOfStream` at EOF, and retains
unreturned bytes on cancellation. Use a protocol-level framing predicate:

```python
with anyio.fail_after(reply_timeout):
  while not frame_complete(buffer):
    buffer.extend(await io.read(remaining_capacity))
```

Explicit legacy public read timeouts remain effective during migration, but warn on use. Omitted
timeouts do not impose a public deadline; finite internal polling bounds for blocking libraries
remain separate from public deadlines. Once a write is dispatched, the transport joins it even if
its caller is cancelled; a write error or timeout does not prove that no data was sent.

For a protocol framed by physical HID reports or supported USB bulk/interrupt IN packets,
use the transport's explicit `read_to_boundary()` instead of interpreting a short `read()`
as a complete report. For a USB protocol that uses short packets or ZLPs as message ends:

```python
with anyio.fail_after(reply_timeout):
  result = await io.read_to_boundary(boundary="short_packet_end")
if not result.started_at_boundary or not result.ended_at_boundary:
  raise ValueError("Incomplete device reply")
reader = Reader(result.data)
```

`Reader` decodes fields in already-delimited bytes; it cannot certify report completeness.
Use `discard_to_boundary()` to skip an unwanted frame. HID does not return a report that
exceeds its configured maximum as valid data: reads fail until an explicit unbounded
discard acknowledges the damaged report, whose discarded byte count may be unknown.
USB `packet` marks denote supported physical packet completions; a short packet (including
a ZLP) also gives a `short_packet_end` mark, but a full packet does not. Unsupported
endpoint/backend policies raise `UnsupportedOperation`. Legacy capture marks inferred
from caller-supplied profiles carry assumptions; they are not physical observations.

For a timeout that applies only while entering an owned lifespan, use new timeout scope for the
*full* lifespan. Register the timeout scope *before* entering the child, then disarm the deadline
after entry succeeds.

```python
entry_scope = stack.enter_context(anyio.fail_after(connect_timeout))
await stack.enter_async_context(transport.lifespan())
entry_scope.deadline = float("inf")
```

An external `with anyio.fail_after(...):` around `stack.enter_async_context(...)` exits too early:
the child may retain its own cancel scope until teardown, violating AnyIO's scope nesting.

## Keep workers inside their owner's lifespan

Enter task groups with `await stack.enter_task_group()`: this repo's boundary preserves the
original body failure rather than unnecessarily turning it into a task-group exception. Start
workers only after their dependencies are acquired, and cancel and join them before closing those
dependencies. On **normal exit**, an AnyIO task group does not cancel an endless reader for you:
register `stack.callback(group.cancel_scope.cancel)` after entering the group, so reverse unwind
cancels it before joining the group and closing its transport. If safety cleanup needs the reader,
register that cleanup *after* the cancel callback so the reader stays alive for it. A failed worker
must be visible to its owning lifespan. A task that starts a blocking worker but abandons it on
cancellation can still hold a socket or mutate hardware *after*
the listener or transport closes; ownership includes accepted requests and in-flight calls, not
just the listening socket. For unavoidable blocking I/O or SDK calls, use non-abandoning
`anyio.to_thread.run_sync` and a finite underlying polling/timeout strategy where supported.
HID's dedicated executor is an intentional thread-affinity exception, not a template for other
transports. Preserve independent USB read and write progress when full duplex is required.

Hamilton's reply router serves heater-shaker safety cleanup after an external body cancellation.
Its reader is shielded from that cancellation while cleanup uses it; `stop()` cancels the reader's
own scope so a blocked transport read is still joined before transport close.

For reconnects within one parent lifespan, use a nested exit stack to close the current owned
connection and enter its replacement; keep both under parent ownership. Reset only
connection-local state at the right boundary, without erasing unresolved old-run obligations.
Never use the deprecated `setup()`/`stop()` bridge to reconnect, and never tear down a borrowed
interface as part of reconnection.

## Check temporary operation ownership separately

Some long-lived physical states have their own nested scopes, such as `shaking(...)`,
`freedrive(...)`, `objective_maintenance(...)`, or `acquisition()`. These scopes run *inside* an
active resource lifespan; they do not enter that lifespan. They must reject overlapping ownership
and conflicting public aliases before mutation, and shield exit cleanup while the parent handle
is still open. Durable start/stop methods keep their existing behavior outside a scope. Do not
infer a scope from every pair of method names: a finite shake, one-shot trigger, or environmental
regulation is not automatically a temporary scoped operation. See the
[included inventory and exclusions](structured-concurrency.md#scope-temporary-operations) before
changing one. If cleanup fails, keep uncertain state for a supported retry; prevent a late scope
exit from acting on an already-closed parent.

## Verify behavior without a device

Use [AnyioTestBase](structured-concurrency.md#test-asynchronous-code) and software transport or
SDK doubles. For intrinsically asyncio-only libraries, reject Trio before acquiring a resource
and restrict only those integration tests to asyncio; otherwise run both asyncio and Trio. Include
these cases in focused tests for the backend being migrated:

| Scenario | Evidence to inspect |
| --- | --- |
| Normal use, body error, and partial entry | Existing output/events and reverse cleanup; no leaked handle. |
| Cancellation before and after mutation | No pre-dispatch command; committed command reconciled before close; cancellation propagates. |
| Timeout or cleanup failure | Finite wait, every independent cleanup attempted, original failure reachable. |
| Worker failure or concurrent calls | Failure reaches owner; whole transactions serialize; workers finish before close. |
| Repeat entry or reconnect | No stale locks, task groups, caches, or unowned replacement handle. |

Run relevant tests, lint, format, type, and documentation checks after the change. These tests
establish software behavior, not physical-device behavior. Real-device verification is a
separate community activity; [record and share findings](structured-concurrency.md#community-device-verification)
when you have a device. Never drive hardware without explicit approval for that run.
