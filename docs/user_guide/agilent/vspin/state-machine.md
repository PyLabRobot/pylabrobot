# VSpin and Access2 state machine

The VSpin centrifuge and optional Access2 loader track which operation is in progress,
which positions have been confirmed, and whether a failure requires physical recovery.
Use this page to interpret their state snapshots and coordinate positioning, spins,
and plate transfers. Start with the [VSpin quickstart](hello-world.ipynb) for connection
and calibration instructions.

## VSpin activity overview

```{graphviz} images/state-machine.dot
:layout: neato
:alt: VSpin activity cycles sharing a single IDLE state. Interlocks, positioning, transfers, completed spins, and controlled stops all return to IDLE.
:caption: Every successful workflow returns to the same IDLE state. Blue arrows show controlled stops before the normal at-speed interval completes. Failure after actuation retains the activity and sets recovery_required.
```

{download}`Download the Graphviz source <images/state-machine.dot>`

`IDLE` is one activity, shared by all workflows. Ordinary commands leaving it require
[connection and readiness](#connection-and-readiness) plus their physical preconditions.
`load()` and `unload()` additionally require a presented bucket and a parked loader.

Returning to `IDLE` does not reset position information: bucket positioning confirms
`vspin.at_bucket`, a successful transfer preserves it, and rotor motion during a spin
clears it. `PARKED` combines Access2's `IDLE` operation with its confirmed park
teachpoint. `recovery_required` is a separate flag that can accompany any activity
or transfer phase.

## Read state and physical status

State snapshots describe what the driver knows about its current session. Physical
conditions such as door position, rotor motion, axis position, and plate contact come
from fresh controller queries.

```python
# With an existing, connected vspin and its paired loader:
print(vspin.state)
print(vspin.at_bucket)
print(loader.driver.state)

# Read current physical conditions; these calls do not move the devices.
door_open = await vspin.request_door_open()
door_locked = await vspin.request_door_locked()
bucket_locked = await vspin.request_bucket_locked()
servo = await vspin.request_positions_and_tachometer()
loader_status = await loader.driver.request_status()
```

| Information | Where to read it |
|---|---|
| VSpin connection, initialization, homing, activity, and recovery flag | `vspin.state` |
| Bucket confirmed at the load opening | `vspin.at_bucket`: `vspin.bucket1`, `vspin.bucket2`, or `None` |
| Access2 connection, operation or transfer progress, recovery flag, and last confirmed teachpoint | `loader.driver.state` |
| Access2 initialization, homing, faults, axis positions, and optical plate sensor | `await loader.driver.request_status()` |
| Recorded plate locations | `vspin.bucket1.resource`, `vspin.bucket2.resource`, and `loader.resource` |

A saved snapshot does not update as the device moves; read `.state` again to observe
progress. Recorded plate locations represent PLR's resource assignments. They do not
measure plate mass or prove that a manually placed plate is present.

## Connection and readiness

```{graphviz} images/lifecycle.dot
:alt: VSpin connection, initialization, and homing states, with prerequisite arrows from CONNECTED to INITIALIZING and INITIALIZED to HOMING. CONNECTED, INITIALIZED, and HOMED are all required for semantic readiness.
:caption: Dashed arrows are prerequisites. Initialization requires an open connection; homing requires completed initialization. Readiness requires CONNECTED, INITIALIZED, and HOMED together, plus IDLE and no recovery flag. Physical checks still apply.
```

{download}`Download the lifecycle diagram source <images/lifecycle.dot>`

`setup()` opens the transport, initializes the controller, and homes the device. VSpin
stores these as separate lifecycle dimensions: `CONNECTED` alone does not mean it is
ready to move. Ordinary VSpin commands require initialization and homing to be complete,
`activity == IDLE`, and `recovery_required == False`, followed by the command's physical
checks.

For Access2, initialization and homing are read from controller status. Its semantic
state records connection, operation, recovery, and the last confirmed teachpoint.

`stop()` closes the transport and invalidates session-scoped position knowledge.
Use `stop_spin()` to request a controlled rotor stop; `stop()` is the disconnect method.

## VSpin activities

| Activity | What is happening |
|---|---|
| `IDLE` | No VSpin workflow owns the device. Readiness and physical preconditions still apply. |
| `CHANGING_INTERLOCKS` | A door or bucket-lock command is waiting for its sensor confirmation. |
| `POSITIONING` | The rotor is moving to a requested position. |
| `TRANSFERRING` | Access2 has reserved the load opening for a plate transfer. |
| `PREPARING_TO_SPIN` | The driver is preparing the interlocks, amplifier, and spin trajectory. |
| `ACCELERATING` | The rotor is accelerating toward the requested speed. |
| `AT_SPEED` | Target speed has been confirmed and the timed interval is in progress. |
| `DECELERATING` | The driver is bringing the rotor to a verified stop. |

A completed operation normally returns to `IDLE`. A new VSpin workflow is rejected
while another owns the device, including while an Access2 transfer reserves it.
Status queries remain available during a workflow.

### Present a bucket

`go_to_bucket1()` and `go_to_bucket2()` coordinate the door and bucket interlocks,
position the rotor, verify its position, and leave the selected bucket locked at the
open load opening. After success, `vspin.at_bucket` identifies that bucket.

`go_to_position()` accepts an encoder position but does not establish a named bucket
presentation. Use a bucket method before transferring a plate. A completed spin also
clears the presented-bucket reference, so present a bucket again before the next transfer.

### Spin and stop

A full spin follows:

```text
IDLE → PREPARING_TO_SPIN → ACCELERATING → AT_SPEED → DECELERATING → IDLE
```

`spin()` coordinates the interlocks and waits for the cycle to finish. `stop_spin()`
can be called from another task during preparation, acceleration, the at-speed interval,
or deceleration. It asks the active spin workflow to stop and waits for that workflow
to finish. A stop requested during preparation can finish before any rotor motion;
a stop requested during acceleration can skip the at-speed interval. Calling
`stop_spin()` outside a spin returns without starting an operation.

When running a spin in a separate task, retain that task and await it as well so its
exception is observed. Task cancellation is treated as a failure after actuation, even
when the driver's cleanup stops the rotor successfully.

## Access2 plate transfers

Use the paired `Access2.load()` and `Access2.unload()` methods to coordinate the two
devices and update plate ownership. The loader driver performs the physical movement;
the paired wrapper reserves VSpin throughout the transfer.

Before starting a transfer:

- Present the intended bucket with `go_to_bucket1()` or `go_to_bucket2()`.
- Confirm the rotor is stopped, the door is open, and the bucket is locked.
- Have Access2 initialized, homed, fault-free, and confirmed parked.
- Keep PLR's resource assignments consistent with the physical plates. The source must
  contain a plate and the destination must be empty, including the loader stage for an unload.

Load and unload follow the same phases with opposite source and destination routes:

```{graphviz} images/access2-transfer.dot
:alt: A closed Access2 transfer cycle from PARKED through pickup, transport, release, and RETURNING TO PARK, with a confirmed return to the same PARKED state.
:caption: Both load and unload return to the same PARKED state after park confirmation. Follow the arrows across alternating rows. A failure after actuation retains the last phase and requires recovery.
```

{download}`Download the transfer diagram source <images/access2-transfer.dot>`

The driver checks source plate presence, grip, destination motion, gripper opening,
and return to park before completing the transfer. On success, Access2 returns to
`IDLE`, its last teachpoint is park, and PLR moves the plate's resource assignment to
the destination. The same VSpin bucket remains presented.

Your workflow must keep the loader clear before rotor motion, keep transfer destinations
empty, and provide compatible, balanced plates for spinning. The state model and resource
assignments do not establish those physical conditions by themselves.

## Rejections and recovery

A rejected precondition before actuation restores the previous operation state. For
example, attempting a paired transfer while the door is closed is rejected before the
loader starts moving. Correct the precondition before retrying.

A failure or cancellation after actuation sets `recovery_required` and retains the
activity or transfer phase for diagnosis. An uncertain rotor position clears
`vspin.at_bucket`; an uncertain loader position clears its last teachpoint. If a
paired transfer fails after loader actuation, VSpin also requires recovery and blocks
subsequent motion. Resource assignments are updated only after successful transfers,
so after a failure the physical plate may no longer be where its resource is recorded.

Stop the workflow and establish the actual arm, plate, rotor, and interlock positions
before recovery. Neither `setup()` nor reconnecting the same driver clears the recovery
flag, and there is no public reset method that makes an interrupted transfer safe.

See the {ref}`VSpin and Access2 API reference <vspin-api>`
for method signatures and the [events page](events.md) for structured operation records.
