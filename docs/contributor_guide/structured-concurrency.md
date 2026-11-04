# Structured concurrency

PyLabRobot uses [AnyIO](https://anyio.readthedocs.io/) to give asynchronous devices and transports a
lexically scoped lifecycle. Library code that owns asynchronous state must keep acquisition,
child tasks, cancellation, and cleanup inside that lifecycle. All existing code does that by
inheriting from `HasLifespan`, which provides `setup()` and `stop()` methods for
application-level interactive use; these defeat structured concurrency for interactive convenience,
and can only be used at global application scope, never in the library. Furthermore,
`HasLifespan` provides an extension point `_enter_lifespan()` for easy composition
of child lifespans and cleanup actions.

For migrating an existing custom backend, or any new code involving structured concurrency
in general, use the [structured concurrency gotchas checklist](structured-concurrency-gotchas.md)
alongside this API guide.

## Main semantics of a `HasLifespan` instance

Use `HasLifespan` instances with `async with`:

```python
async with device:
  await device.run_operation()
```

Structured concurrency offers clearly lexicographical "states":
- While inside the scope, the device is "active", and the driver does its best to uphold any
  guarantees it may make with respect to that device. If it fails to uphold them,
  it will usually raise an error on any operation that requires those guarantees,
  quickly unwinding the stack.
- After leaving the scope, the device is "inactive": The driver will not interact
  with the device in any form.
- Scope exit will attempt to perform all needed actions to make the device safe for
  inactivity.

Because the states of the device are matched with the lexical scoping,
these guarantees are even preserved under error conditions and cancellation.
This aligns with "level-triggered cancellation": One can cancel any operation
inside the scope by wrapping it in a local `anyio.CancelScope` (such as applying
a timeout with `anyio.fail_after`), and are guaranteed that it won't affect
any other operation in the scope. One can also cancel the whole scope, thereby
requesting any and all activity to be stopped as soon as possible, while still
preserving all the guarantees made by the driver.

`HasLifespan.setup(**lifespan_kwargs)` and `HasLifespan.stop()` are a deprecated, asyncio-only
bridge for application-level interactive use (such as a notebook keeping a device open between
cells). `setup()` transfers lifespan ownership to an application-global manager task, outside the
caller's lexical scope; `stop()` asks that manager to release it. A library caller cannot make this
an owned child of its lifespan, even when the application runs on asyncio. Library code must enter
owned children with `async with child.lifespan()` or the parent's exit stack, and must never call the
bridge. Notebook examples using the bridge must label it as asyncio-only and balance every `setup()`
with `stop()`.

## Implement a lifespan

Most resources implement this extension point:

```python
async def _enter_lifespan(
  self,
  stack: AsyncExitStackWithShielding,
  *,
  option: str = "default",
) -> None:
  ...
```

Options come from `resource.lifespan(**kwargs)` and must be declared explicitly as keyword-only
parameters on `_enter_lifespan`; omit them when there are none. Call
`await super()._enter_lifespan(stack)` when overriding to preserve any parent acquisition. The
terminal hooks on `HasLifespan` and `IOBase` are no-ops, so this works for direct subclasses too.
Concrete `MachineBackend` subclasses must implement either `_enter_lifespan()` or `lifespan()`.
Forward only options the parent hook accepts. An override that *replaces* rather than extends parent
acquisition must deliberately omit `super()`—for example, a
validator or simulator must not open the real device through its parent hook. Document such a
substitution and register its own cleanup.

Register cleanup as soon as the corresponding resource is acquired. Use `stack.callback()` for
synchronous cleanup and `stack.push_shielded_async_callback()` for asynchronous cleanup:

```python
import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan


class Connection(HasLifespan):
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
  ) -> None:
    await super()._enter_lifespan(stack)
    # We need a working transport alive throughout our lifecycle
    await stack.enter_async_context(self._transport.lifespan())
    # we also want a background task (such as a reader loop)
    # to be running throught the lifespan; relying on the live transport.
    # To achieve that, the background task is owned by a nested
    # task group, which guarantees the transport lifespan will
    # not be exited before the background task has finished.
    tg = await stack.enter_task_group()
    # We want the background task to be shielded from outside
    # cancellation: When our own lifespan gets cancelled,
    # anyio will cancel *all* tasks nested within. But
    # we do want the background task to stay alive until
    # we're actually exiting the body of our lifespan,
    # which happens only after the cancellation has been
    # completed on the body.
    background_cancel_scope = anyio.CancelScope(shield=True)
    # to finally stop the background task once it is time, we can
    # just cancel it through that cancel scope. To guarantee that
    # this is triggered on all exit paths, we just ask the exit
    # stack to do that. It needs to trigger the cancellation
    # *before* the owning task group is exited, because
    # the task group exit itself blocks until all tasks have exited.
    # Therefore, we push the cancellation *after* the task group
    # onto the stack.
    stack.callback(background_cancel_scope.cancel)
    # now that we have the cancel scope properly linked,
    # we can proceed starting the background task
    tg.start_soon(self._background_runner, background_cancel_scope)
    # with all machinery in place and guaranteed to remain alive for anything
    # we want to do between now and the end of the lifespan, we
    # can schedule the *first* thing to do at the *end* of the lifespan,
    # sending a goodbye message. Again, this should be shielded from
    # outside cancellation so that we send the goodbye even if
    # our whole lifespan has been requested to abort. Give it a reasonable
    # timeout, so that we don't block forever on exit in case anything
    # goes wrong in there.
    stack.push_shielded_async_callback(self._goodbye_message, shield_timeout=5)
    # Finally, before completing the enter into the lifespan, we also
    # the *last* thing at the *start* of the lifespan, sending a hello.
    await self._handshake()
    # Now, as we exit this function, we are fully inside the our own lifespan.
    # The exit stack manages unrolling everything as specified above.
    # In fact, it even does the correct partial unrolling if we only
    # partially entered, e.g. because of a failure within this startup code.

  async def _goodbye_message(self) -> None:
    await self._transport.send_goodbye()

  async def _background_runner(self, cancel_scope: anyio.CancelScope) -> None:
    # wrap everything so that the runner can be stopped just by cancelling
    # this scope.
    with cancel_scope:
      # infinite loop - not a problem. cancellation takes us out without
      # complicated stop-flag tracking
      while True:
        await self._some_status_poll()
        await anyio.sleep(self._poll_delay)
```

`push_shielded_async_callback(callback, *args, shield_timeout=None)` protects cleanup from
surrounding cancellation. Set `shield_timeout` when cleanup needs a finite safety bound.
When a child lifespan owns an AnyIO task group, do not close that child inside a newly entered
shield or timeout: the task group's cancellation scope must exit in nesting order. Shield the
individual device safety actions before child teardown instead. For resources that emit setup/stop
events, use `_lifespan_event_info()` as described in the [EventBus contributor guide](event-bus.md#lifespan-transition-events),
rather than manually entering and exiting the child to wrap teardown in an event operation.

The exit stack attempts every registered cleanup, in reverse registration order, even after an
earlier one fails. Cleanup failures propagate to the lifespan caller: the caller receives the last
failure, and reaches the earlier ones by walking its `__context__` chain. That holds for every
cleanup registered through `callback()`, `push_async_callback()`,
`push_shielded_async_callback()`, `enter_context()`, and `enter_async_context()`, whether or not
the lifespan body itself failed.

Last failure wins, including against cancellation: when cleanup fails while unwinding a cancelled
scope, the caller receives the cleanup failure and the cancellation remains reachable as its
context. The surrounding scope stays cancelled either way.

Own child resources and task groups with the same stack so they cannot outlive the parent:

```python
async def _enter_lifespan(
  self,
  stack: AsyncExitStackWithShielding,
) -> None:
  await super()._enter_lifespan(stack)
  await stack.enter_async_context(self.transport.lifespan())
  self.task_group = await stack.enter_task_group()
  stack.callback(self.task_group.cancel_scope.cancel)
  self.task_group.start_soon(self._reader)
```

`enter_task_group()` preserves the lifespan body's original failure identity when the group exits.
Registering cancellation after entering the group ensures a long-running reader stops on normal
exit before the transport closes. If safety cleanup needs the reader, register that cleanup after
the cancellation callback so it runs while the reader is still active.

For a finite set of independent actions that must all be attempted even if one fails, use
`non_cancelling_task_group()` instead. Submit async callables with `group.start_soon(fn, *args)`;
they start while the group body is active. Ordinary child failures do not cancel siblings; after
all children finish, the group raises `SiblingTaskErrors` (an `ExceptionGroup`) containing the
failures in submission order. A body failure or external cancellation cancels unfinished children;
if child failures were already collected when the body fails, both are reported. Like a regular
task group, this is for scoped work, not an unowned background task.

Implement a complete `lifespan()` async context manager only when stack-based entry is
insufficient. Its signature is:

```python
@asynccontextmanager
async def lifespan(self, **kwargs: Any) -> AsyncIterator[HasLifespan]:
  ...
```

A custom `lifespan()` replaces the base implementation without an automatic wrapper. It must own
its cleanup and any desired protection against overlapping entry. The inherited `is_active` property
tracks only the base lifespan; a custom implementation must supply its own activity reporting if
needed. `super().lifespan()` is ordinary context-manager composition, not an automatically managed
parent layer; if it resolves to the base implementation, it calls `self._enter_lifespan()` by dynamic
dispatch. For shared parent acquisition logic, compose `_enter_lifespan(stack)` instead.

Subclasses must not override `__aenter__`, `__aexit__`, `setup`, or `stop`. `HasLifespan`
manages those methods and delegates entry to `lifespan()`; a custom override supplies its own
state and cleanup guarantees.

Native migration is atomic. In the same change, a class must gain its native `_enter_lifespan()`
or `lifespan()` implementation and remove every custom definition, wrapper, or alias of `setup()`,
`stop()`, `__aenter__()`, and `__aexit__()`. Do not split lifecycle ownership across tasks or leave
an alternate public entry path after migration.

When a driver must replace an owned child connection while its parent lifespan remains active,
enter one reusable nested `AsyncExitStackWithShielding` in `_enter_lifespan()`, enter the current
child lifespan into that nested stack, and close and reuse the nested stack before opening a
replacement connection. Reconnect must never call the compatibility `setup()` or `stop()` bridge or
leave a replacement handle outside parent ownership.

Composition does not imply ownership. When a driver borrows an already-active shared interface or
collaborating device supplied by the caller, only the owner enters and closes that resource; the
borrower must not enter, stop, or close it during its own teardown.

## Cancellation and blocking work

- Use AnyIO task groups rather than unowned tasks or long-running background threads.
- Do not swallow cancellation. If cancellation is caught for local cleanup, re-raise it.
- Use `await anyio.sleep()` and `anyio.current_time()` for asynchronous sleeps and duration
  tracking.
- Use `with anyio.fail_after(timeout):` for public operation deadlines. It is a synchronous context
  manager.
- Run unavoidable blocking calls with `anyio.to_thread.run_sync`. Do not abandon a hardware call
  on cancellation: cleanup must wait until in-flight resource mutation is reconciled.
- Keep production code loop-agnostic. Direct asyncio use is reserved for guarded integrations that
  inherently require asyncio.

Before completing any device or service migration, audit every operational await as well as entry
and exit. Cancellation must not abandon protocol or hardware mutation, and every partially acquired
resource must have cancellation-safe rollback registered before the next await. This audit is
mandatory even when managed-method identity and normal-exit tests already pass.

## Scope temporary operations

An operation scope is nested inside an already-active resource lifespan; it never enters or exits
that lifespan itself. Four public domain-noun context-manager names are supported:
`shaking(...)`, `freedrive(...)`, `objective_maintenance(...)`, and `acquisition()`. The matching
start, stop, enter, and exit methods remain the compatible durable-state API. Outside a scope they
retain their signatures, return values, idempotence, commands, and events.

A successful operation scope has ensure-state priority until it exits. While it owns an operation,
every public alias that could start, stop, exit, or reconfigure the same physical state raises
`RuntimeError` before hardware mutation. Nested and concurrent scopes for that identity are also
rejected. The exception is identity granularity, not the ownership rule: BigBear shaking is keyed by
`(driver, device_id)`, so different nests can be scoped concurrently while the same nest and
`stop_all()` remain guarded. Locks serialize finite transitions but are not held across the scope
body. Emergency interruption, such as an arm's `halt()`, remains available.

A scope does not adopt state established by a durable method, an unknown prior transition, another
process, or an external controller. Local tracking is authoritative only for commands issued
through that driver; scopes do not query the protocol merely to discover externally changed state.
Consequently, users must prevent another controller from mutating the same operation while a scope
is active.

Cancellation before mutation remains effective. After mutation may have committed, entry
reconciles the finite transition and rolls it back before propagating cancellation. Exit cleanup is
shielded. A cleanup failure takes precedence over a body failure or cancellation, with the original
exception retained through `__context__`; independent cleanup steps are still attempted. Failed
cleanup retains active or unknown state so a durable stop or parent teardown can retry. Parent
teardown attempts that retry before transport or child-resource closure and invalidates scope
ownership so a late scope exit cannot issue I/O against a closed or reused resource. Existing
operation events remain attached to the actual shared transition implementation; scopes emit no
wrapper-only duplicate events.

### Included operation inventory

The supported operation-scope inventory is explicit rather than inferred from method names:

- `shaking(...)` is available on the legacy `Shaker` frontend, including `HeaterShaker` use; on
  `BigBearOrbitalShaker`, `BioShake`, and `InhecoThermoShake`; and on the BioTek reader owner used by
  `Cytation1`, `Cytation5`, and `SynergyH1`.
- Legacy BioTek `shaking(...)` is inherited by `CytationBackend`, `Cytation5Backend`, and
  `SynergyH1Backend`. The v1 SpectraMax inventory is `SpectraMaxM5` and `SpectraMax384Plus`; the
  separate legacy inventory is `MolecularDevicesSpectraMaxM5Backend`,
  `MolecularDevicesSpectraMax384PlusBackend`, and `MolecularDevicesSpectraMaxGeminiEMBackend`.
- Legacy incubator shaking is owned by `IncubatorBackend` and exposed through `Incubator`. Every
  concrete backend is included: `IncubatorChatterboxBackend`, `ExperimentalLiconicBackend`,
  `HeraeusCytomatBackend`, `CytomatBackend`, and its concrete `CytomatChatterbox` subclass. Cytomat
  retains its global physical identity across selected shaker indexes. The separate Inheco stack
  inventory is `InhecoIncubatorShakerUnit` and `IncubatorShakerStack`, keyed by physical stack unit.
- `freedrive(...)` is available on legacy `ExperimentalSCARA`, including its
  `PreciseFlex3400Backend` and `PreciseFlex400Backend` configurations through the frontend, and on
  v1 `PreciseFlex` and `XArm6`.
- `objective_maintenance(...)` is available on v1 `Pico` and legacy `ExperimentalPicoBackend`.
- `acquisition()` is owned by v1 `AravisCamera` and delegated by `CytationMicroscope`. Legacy
  acquisition is available on `CytationBackend` and `Cytation5Backend` around their existing PySpin
  state. Capture borrows an acquisition already owned by the surrounding scope.

`InhecoThermoShake` is a borrowed-resource case: its scope requires an already-active
`InhecoTECControlBox`, must be lexically contained by that parent's lifespan, and does not enter,
stop, close, or register ownership of the control box. Likewise, a delegating frontend or microscope
does not create a second operation identity beside its authoritative backend or camera owner.

### Explicit operation exclusions

Operation scopes do not cover pumping; doors or trays; standalone locks or clamps; temperature,
humidity, CO2, O2, or other environmental regulation; brakes; vacuum or purge; persistent
configuration; finite scans; or one-shot commands. Those APIs need their own safety and ownership
design rather than an inferred start/stop wrapper.

In particular, the v1 EL406 and legacy EL406 washer shake/soak commands are single commands with
bounded durations; GenoGrinder and Tecan Infinite shaking are finite, self-bounded operations. The
Spark movement control exposes a start command but no matching supported stop operation. Direct
methods on legacy BioShake, Hamilton HeaterShaker, and Inheco Thermoshake backends remain durable
and outside the frontend-owned scope guarantee. Their native environmental lifecycles do not create
another shaking-operation owner. Protocol shake
configuration, finite capture and trigger calls, arm queries, independent gripper movement, and
emergency stops also remain their existing non-scope operations.

## Use asyncio-only services

These service integrations are explicit asyncio-only dependency boundaries:

- `Visualizer` uses the asyncio implementation of `websockets` and owns its websocket server,
  static-file listener and handlers, resource callbacks, bounded outbox, and outbox worker inside
  one lifespan.
- `Viewer3D` uses the same `websockets` implementation and owns its websocket server, static-file
  listener and its accepted connections' workers, resource callbacks, and scene flushes inside one
  lifespan.
- `MicroSpinMockServer` uses asyncio streams and owns its listener, accepted clients, handlers, and
  simulated motions inside its lifespan.
- The RPC `DeviceServer` and `DeviceClient` use `grpc.aio`. The server's lifespan accepts RPCs and
  the client's holds one channel. `CeligoSession` owns the Celigo service's hardware lock, job
  store, accepted commands, job executions, and the driver's lifespan in one task group, so a
  command or job outlives the RPC that accepted it but not the session. `CeligoServer` enters the
  session before it accepts RPCs.

Each boundary must reject non-asyncio backends before acquisition, and its tests must be explicitly
limited to asyncio except for a test that verifies the rejection. Direct asyncio primitives inside
these boundaries implement dependency integration; they must not create detached ownership.

Agrow's `AgrowPumpArray` is a separate asyncio-only driver boundary. Its pymodbus
`AsyncModbusSerialClient` requires asyncio, so the lifespan rejects other AnyIO backends before it
constructs the client. The driver owns its keep-alive in an AnyIO task group; exit cancels and joins
that worker, sets every discovered pump to zero under shielding, and only then closes Modbus.

## Use legacy Machines in a scope

Legacy Machine frontends are strict `HasLifespan` implementations. Enter them with `async with`
or `machine.lifespan(**backend_kwargs)` so the frontend owns its backend lifespan and safety cleanup.
Scoped use preserves the existing `machine.setup.*` and `machine.stop.*` events and
`setup_finished` transitions.

Every concrete `MachineBackend` implements `_enter_lifespan()` or `lifespan()`. Backends inherit
`__aenter__()`, `__aexit__()`, `setup()`, and `stop()` directly from `HasLifespan`; defining,
aliasing, or inheriting a replacement managed method is rejected at class creation. Backend test
doubles follow the same rule. Cleanup used by a native lifespan belongs in private, purpose-named
callbacks that are registered immediately after acquisition.

Class-definition validation is the first line of defense: the `HasLifespan` metaclass rejects any
class whose `setup()`, `stop()`, `__aenter__()`, or `__aexit__()` does not resolve directly to
`HasLifespan`. After deterministic production imports, the pytest collection audit recursively
checks every concrete `MachineBackend` descendant and separately reports production and test classes
that violate that contract. Abstract classes are allowed.

## Documentation and notebook examples

Primary single-run documentation and doctest examples use `async with`. The notebook lifecycle test
suite (`docs/notebook_lifecycle_tests.py`) parses a closed JSON/AST inventory without importing or
executing notebook code; cross-cell `setup()`/`stop()` use is permitted only in explicitly labelled,
balanced deprecated asyncio/Jupyter compatibility sessions.

## Use shared I/O transports

Enter Serial, FTDI, USB, HID, Socket, and command-line transports with `async with`. A driver that
owns a transport must enter its lifespan through the driver's exit stack so the transport cannot
outlive the driver:

```python
async def _enter_lifespan(
  self,
  stack: AsyncExitStackWithShielding,
  **kwargs: Any,
) -> None:
  await stack.enter_async_context(self.transport.lifespan())
```

`IOBase` provides scoped I/O ownership and ordering for nonoverlapping calls, not connection
continuity or device exclusivity. `ContinuousConnectionIO` promises stable connection identity
between explicit API calls documented to change it: ordinary I/O never silently replaces the
handle, and an **observed** hard disconnect invalidates further I/O. Its
`ReconnectableConnectionIO` subtype provides an explicit `reconnect()` to replace that handle
without leaving the transport lifespan. Failed or cancelled reconnect leaves the connection
unusable until a successful retry or a new lifespan. A finite read timeout does not by itself
prove the connection was lost. Loss may only be observable at the next operation; a stable
host:port, USB ID, or Python object does not identify an uninterrupted connection.

Positive-size `IOBase.read()` calls wait for at least one byte, return up to the requested size,
and raise `anyio.EndOfStream` at EOF. USB reads yield one packet at a time; the driver assembles
complete replies from successive reads. Put one deadline around the full protocol reply rather
than setting a short deadline on each transport read:

```python
with anyio.fail_after(reply_timeout):
  while not frame_complete(buffer):
    buffer.extend(await io.read(remaining_capacity))
```

Cancelled reads retain unreturned bytes for the next read, unless the input is purged or the
transport is closed or reconnected. A write remains cancellable before dispatch; once dispatched,
the transport waits for completion before reporting cancellation. Legacy explicit public read
timeouts still work but warn; default reads have no public deadline. Internal finite polls for
blocking devices are separate from these caller-owned deadlines.

| Transport | Lifespan identity and detected loss | Overlapping calls |
| --- | --- | --- |
| `Serial` (`ContinuousConnectionIO`) | One opened serial handle; hard I/O failure invalidates the current entry. | Individual calls are serialized by its operation lock; multi-call transactions belong to the driver. |
| `USB` (`ReconnectableConnectionIO`) | One selected device handle and endpoints between explicit reconnects; hard transfer failures invalidate it. Packet polling timeouts do not. | Reads and writes have independent locks and can progress concurrently; calls in each direction serialize. |
| `HID` (`ContinuousConnectionIO`) | One HID handle; hard report failure invalidates the entry. | HIDAPI calls share one affinity-preserving worker. |
| `Socket` (`ReconnectableConnectionIO`) | One TCP/TLS stream between explicit reconnects; observed EOF or stream failure blocks I/O until reconnect or a new lifespan. | Per-direction locking does not make a driver's state coherent across reconnection. |
| `FTDI` (`ReconnectableConnectionIO`) | One FTDI handle between explicit reconnects; observed hard I/O failure blocks further operations until reconnect or a new lifespan. `usb_reset()` and buffer purges can disrupt device state or observed events without replacing the handle. | FTDI worker calls are serialized; multi-call protocol transactions belong to the driver. |
| `HTTP`, `CommandLineTransport` | These are `HasLifespan` objects, not `IOBase`: HTTP connects per request, and the command-line transport launches a process per invocation. | HTTP serializes requests; command-line invocations serialize. No connection identity spans calls. |
| SiLA/gRPC and vendor SDK integrations | Their sessions, callbacks, and possible remote locks have protocol-specific contracts. UDP/mDNS in this repository is discovery, not a lasting connection. | Determine ordering at the integration or driver boundary. |

The guarantee assumes the driver exclusively controls the transport's public API: another caller
invoking `reconnect()` can invalidate a driver's assumptions without changing its lifespan.
Connection identity also does not imply that all device events were observed, particularly after
a buffer purge or device reset. On some devices an exclusively
claimed, sole control interface may establish device-level exclusivity; claiming one of several
interfaces only excludes users of that interface. A TCP connection is exclusive to its client, but
only the **device implementation**, not the host OS, can promise that it admits no other control
connections. Document any stronger device-specific guarantee separately. For a shared reconnectable
transport, comparing a connection identity outside the same serialized transaction is racy; do
not automatically replay a write whose effect is uncertain.

Blocking transport calls use non-abandoning workers. Cancellation therefore waits for in-flight
resource mutation to finish before cleanup closes the underlying handle. USB uses independent read
and write capacity limits so a blocked read does not prevent write progress. HID is the exception
to the shared-worker approach: its dedicated single-worker executor preserves HIDAPI thread
affinity and serializes initialization, enumeration, I/O, and close.

Finite polling deadlines inside transports bound cancellation latency for blocking library calls.
They are internal implementation bounds, not public operation deadlines, and must not be exposed as
such or removed in favor of abandoning an in-flight call.

## Community device verification

Automated tests in the repository use software transports, SDK doubles, and loopback services;
they do not exercise physical Serial, FTDI, USB, or HID devices. Contributors with access to
hardware can verify driver behavior in their own environments and report findings.

Choose a device and its hello-world guide. Record the model, firmware, transport, operating system,
library versions, permissions, and local connection setup. Run the relevant software tests without
hardware first. When you have authorized a device session in your own environment, follow the
device's documented commands and safety procedure to check scoped open/close, one command and its
response, cancellation or interruption with finite response reconciliation, and disconnect/cleanup
where supported. The applicable checks and their order depend on the device; there is no universal
hardware command sequence.

If you find a difference, record the expected and observed command/response order, exceptions,
cleanup behavior, deviations from the guide, and enough device/software context to reproduce it.
Share a minimal reproducer and relevant logs or capture through a PyLabRobot issue or the
[forum](https://discuss.pylabrobot.org), excluding credentials and sensitive connection identifiers.
A protocol or lifecycle fix should include a focused software-double regression test. See the
[device-driver guide](device-driver-guide.md) for driver and hello-world conventions.

## Test asynchronous code

Subclass `pylabrobot.testing.concurrency.AnyioTestBase`. Each test runs in a fresh class lifespan
under both asyncio and Trio by default:

```python
from typing import Any

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.testing.concurrency import AnyioTestBase


class TestConnection(AnyioTestBase):
  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    **kwargs: Any,
  ) -> None:
    self.connection = Connection()
    await stack.enter_async_context(self.connection.lifespan())

  async def test_operation(self) -> None:
    assert await self.connection.read() == b"ready"
```

Use normal pytest assertions and fixtures. Do not derive asynchronous tests from
`unittest.IsolatedAsyncioTestCase`, and do not define `setUp`, `asyncSetUp`, `tearDown`, or
`asyncTearDown`; put fixture acquisition and cleanup in the test class lifecycle.

Every base of a test class must itself be an `AnyioTestBase` subclass. Share test behavior through
an intermediate `AnyioTestBase` subclass rather than an ordinary mixin, so inherited tests keep
their backend parametrization and lifespan:

```python
class SharedBehavior(AnyioTestBase):
  async def test_shared(self) -> None:
    ...


class TestFirst(SharedBehavior):
  ...
```

An integration that inherently requires asyncio may restrict its own tests:

```python
class TestAsyncioIntegration(AnyioTestBase):
  _anyio_backends = ["asyncio"]
```

Backend options may instead be a mapping from backend names to option dictionaries. Use
`@lifespan_kwargs(name=value)` on an individual test method when its values must be forwarded to
the test class lifespan.

## Mock I/O

`pylabrobot.testing.mock_io.MockIO` provides loop-agnostic asynchronous `read` and `write` test
doubles. Both record calls in `.calls`, accept a value, exception, callable, awaitable callable, or
list through `.side_effect`, and reset with `.reset_mock()`:

```python
from pylabrobot.testing.mock_io import MockIO


io = MockIO()
io.read.side_effect = [b"first", b"second"]

assert await io.read(5, timeout=1) == b"first"
assert io.read.calls == [((5,), {"timeout": 1})]
```

Use these doubles for asynchronous I/O tests that run on both supported backends. Their presence
alone is not evidence of a driver's lifecycle conversion or of physical-device behavior.
