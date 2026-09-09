# MicroServe state machine

The MicroServe coordinates one carousel, fourteen stackers, and a shared plate-transfer
position. These diagrams show the driver's workflows and the conditions that confirm each
transition. Start with the [hello-world guide](hello-world.ipynb) for connection and plate geometry.

The phase names below describe workflows; the driver does not expose a VSpin-style `.state`
object. It combines fresh `request_status()` replies with a recorded handoff and, after an
interrupted preparation, `unresolved_preparation`.

## Activity overview

```{graphviz} images/state-machine.dot
:layout: neato
:alt: MicroServe workflows share a retracted, idle state. Carousel positioning returns after verification. Load and unload preparation lead to separate owned handoffs; the external robot transfers a plate before explicit retraction returns to idle.
:caption: Purple nodes are operations; green nodes are confirmed resting or handoff conditions. Dashed blue loops are actions by the external robot. Barcode completion returns data without a final position check, so inspect status before further motion. Failure paths are shown in the recovery diagram.
```

{download}`Download the activity diagram source <images/state-machine.dot>`

The central state requires a connection, homing, ready mode, no active operation, no unresolved
preparation, and a retracted loader. Rotation, preparation, and scanning also require a clear
plate-detection beam. Each public operation holds the device lock for its entire sequence;
another operation or status query waits for that lock.

`prepare_for_load(dimensions)` presents a receiving position. `prepare_for_unload(dimensions)`
presents a plate for pickup. Both apply the supplied geometry, run the firmware operation, and
confirm the stacker and loader sensors. Python records the direction and stacker of the resulting
handoff. The separate robot must place or pick the plate and withdraw before `retract()`.

Repeated requests preserve the intended state:

- `move_to()` at the selected stacker checks the motion preconditions and sends no `spin` command.
- Repeating the same owned preparation with unchanged geometry sends no additional load/unload.
- `retract()` with both loader home sensors on and the lock released sends no retraction command.

The robot's action does not clear ownership, and a change in the plate-detection beam does not
authorize another plate fetch. Retraction ends the handoff. An extended loader whose preparation
is not owned by this driver cannot be adopted by calling prepare again.

## Connection and homing

```{graphviz} images/lifecycle.dot
:alt: Setup opens a connection without motion. A status query determines whether homing is needed. Explicit home verifies homed and idle status. Stop closes the connection without stopping or retracting the machine.
:caption: Connection and physical state are separate. Homed and ready mode do not establish loader retraction or plate-transfer readiness. Physical motion checks still apply.
```

{download}`Download the lifecycle diagram source <images/lifecycle.dot>`

`setup()` opens the TCP connection. `home()` is an explicit motion operation that verifies homed,
idle status. A machine already reporting homed and ready mode does not receive another home
command. An unresolved preparation must be handled before homing.

`stop()` closes the connection and discards confirmed handoff ownership. It does not abort a
running command, retract the loader, or clear an unresolved preparation. Reconnecting does not
recover ownership of a previously confirmed handoff; inspect the machine before proceeding.

## Read the physical state

```python
status = await microserve.request_status()
print(status.homed, status.busy, status.mode, status.stacker)
print(status.loader_retracted, status.loader_extended)
print(microserve.unresolved_preparation)
```

| Condition | How the driver determines it |
|---|---|
| Homed and idle | `status.homed`, `not status.busy`, and `status.mode == "ready"` |
| Loader retracted | Both loader home sensors on and stacker lock released: `status.loader_retracted` |
| Loader extended | Firmware reports extension, both loader home sensors off, and lock engaged: `status.loader_extended` |
| Readiness to present a plate | `await microserve.is_ready()`; false while retracted, true at the tested receiving position |
| Interrupted load/unload | `microserve.unresolved_preparation` retains direction, stacker, and the ACK identifier when available |
| Plate-detection beam | `status.plate_sensor_blocked`; this does not establish occupancy or plate count |

Firmware 2.7.0.756 can retain `loaderStatus Extended` after successful retraction. The driver
preserves that field in `status.loader`, but uses the home and lock sensors for motion guards.
Neither `status.loader` nor `is_ready()` alone is a general machine-readiness check.

## Interrupted preparation and recovery

```{graphviz} images/recovery.dot
:alt: A failed or interrupted load/unload retains an unresolved preparation. An incomplete exchange closes the connection. After inspection and reconnection, read-only reconciliation can restore the exact successful handoff; explicit retraction can instead clear it after confirming the home sensors.
:caption: No path automatically retries load or unload. Reconnection preserves the pending record. Reconciliation requires the same controller boot and no intervening moves by another client; unsuccessful evidence leaves the preparation unresolved.
```

{download}`Download the recovery diagram source <images/recovery.dot>`

A precondition rejection prevents the requested motion. Geometry is written and checked before
load/unload; a failure during that step does not create an attempted transfer. Once load/unload
has been attempted, a failure or an unconfirmed final state retains an unresolved preparation.

A timeout, cancellation, or broken exchange closes the connection because completion is uncertain.
A complete `ERROR`, `WARNING`, or `ABORTED` reply raises `MicroServeError` and retains diagnostics;
it does not by itself close the transport. A device operation may continue after disconnection.

`reconcile_preparation()` reads the original command record and the current physical state. It
only restores ownership when the exact command succeeded and the expected stacker is extended,
homed, idle, and in ready mode. An unknown ACK identifier, unsuccessful record, or mismatched
state leaves the pending preparation in place. There is no automatic resend.

Alternatively, after inspecting the actual machine and clearing the robot, `retract()` can end
the pending handoff. It requires homed, idle, ready-mode status, and only clears the record after
the home and lock sensors confirm retraction. If those prerequisites are unavailable, resolve the
physical condition before attempting another driver operation.

These recovery records specifically protect load/unload. Other failed operations still require
inspection and fresh status; the driver does not maintain a general recovery flag for every axis.

## Hardware verification

Homing, all fourteen carousel positions, repeated requests, empty receiving-position preparation,
retraction, and subsequent rotation were verified on firmware 2.7.0.756. The diagrams also include
unloading, robot transfers, barcode scanning, and recovery paths that remain unverified on hardware.
See [protocol and validation](protocol.md) for the captures and exact limits.
