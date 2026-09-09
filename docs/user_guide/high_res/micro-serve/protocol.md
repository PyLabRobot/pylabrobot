# MicroServe protocol and validation

See the [state machine](state-machine.md) for connection, plate-handoff, and recovery diagrams.
The [capability audit](capabilities.md) covers every advertised TCP command and remaining
maintenance gaps; the [hardware procedure](validation.md) describes pending supervised tests.

The implementation follows documentation served by the MicroServe itself. To consult these sources,
connect to the controller as described in the [hello-world guide](hello-world.ipynb), then open its
address in a browser. These documents are available on the device and are not hosted by PyLabRobot.

| Source | Location on the connected controller |
| --- | --- |
| MicroServe API, revision 756, May 29, 2020 | Web interface: Support Files → `MicroServe_API.pdf` (`/support_files/files/MicroServe_API.pdf`, HTTP port 80) |
| Operation notes and illustrated plate dimensions | Web interface home page (`/`, HTTP port 80) |
| Firmware 2.7.0.756 command help | `info all` and `help` over TCP port 1000 |
| REST command catalog | `/MicroServe/AvailableCommands`, HTTP port 9095 |
| Commissioning instructions | `/commissioning.html`, HTTP port 80 |

The factory's API and firmware help define the wire commands; no vendor DLL is needed to run the
driver.

## Framing

TCP port 1000 accepts an ASCII command and arguments separated by spaces, terminated with LF.
API section 4 documents the following exchange, captured here from the controller:

```text
version
ACK! version 2
Product Name: MicroServe
Serial Number: HRB-2008-10558
libcommon Version:    1.1.0.756
libts7600 Version:    1.0.0.756
Firmware Version:     2.7.0.756
Firmware Build: A620467A
OK! version 2
```

The ACK and completion must echo the entire command and the same identifier. Status data also
starts with `OK! status`, followed by comma-separated fields, and is **not** a completion frame.
The driver retains that data until the exact command-and-ID completion arrives.

The documented completion statuses are `OK`, `ERROR`, `ABORTED`, and `WARNING`. Non-OK completions
raise `MicroServeError`, retaining the command, identifier, and diagnostic lines. The sequential
numbers in `Error <n>: ...` messages are log entry numbers, not stable error codes.

An incomplete exchange closes the transport. Commands are never automatically replayed. A socket
close does not stop an executing command. The command timeout applies to the entire completion
phase, rather than restarting on each data line.

## Operation mapping

| Python operation | Device command | Device documentation | Hardware validation |
| --- | --- | --- | --- |
| `request_status()` | `status` | API §5.23 | Read-only query verified |
| `request_version()` | `version` | API §5.27 | Read-only query verified |
| `request_firmware_version()` | `firmwareversion` | API §5.8 | Read-only query verified |
| `request_detailed_version()` | `detailedversion` | API §5.4 | Read-only query verified |
| `request_motor_information()` | `copleyserial` | Firmware help | Read-only query verified |
| `request_command_status(id)` | `commandstat id` | API §5.3; firmware spelling | Read-only query verified |
| `request_history(count)` | `history count` | Firmware help | Read-only query verified |
| `request_errors(count)` | `errors count` | API §5.7 | Read-only query verified |
| `request_settings(search)` | `settings [search]` | Firmware help | Read-only query verified |
| `is_ready()` | `microserveready` | API §5.16 | False when retracted, true at the empty receiving position |
| `request_plate_counts()` | `getplatecounts` | Firmware help | Read-only query verified |
| `stackers[i].request_plate_count()` | `getplatecounts i` | Firmware help | Read-only query verified |
| `request_dimensions()` | `dimstatus` | API §5.5 | Read-only query verified |
| `home()` | `home` | API §5.11; operation notes for manual mode | Homing verified; manual-mode recovery simulated |
| `stackers[i].move_to()` | `spin i` | API §5.22 | All fourteen positions and repeated targets verified |
| `stackers[i].set_dimensions(d)` | `setstackerdimensions i PH SH PT` | API §5.21 | Existing geometry written and read back at stacker 2 |
| `stackers[i].prepare_for_load(d)` | geometry, then `load i` | API §5.14, §5.21; commissioning page | Empty stacker 2 and repeated preparation verified |
| `stackers[i].prepare_for_unload(d)` | geometry, then `unload i` | API §5.24, §5.21; commissioning page | Simulated only |
| `retract()` | `retract` | API §5.17 | Empty loader and repeated retraction verified using home sensors |
| `stackers[i].scan_barcodes(d)` | geometry, then `readbarcodestacker i` | Firmware help; operation notes | Simulated only; returns raw data lines |
| `reconcile_preparation()` | `commandstat id`, `status` | API §5.3, §5.23 | Query primitives verified; recovery scenario simulated |

Stacker indices are 0–13. Geometry is provided in millimeters and encoded as whole micrometers in
the order plate height, stack pitch, plate thickness. Height includes the lid when plates are lidded.
Cached counts do not measure the physical stack. `countplates`, `measurestacker`, and barcode scans
perform motion and must not be included in a read-only verification run.

The public API does not expose relative jogs, toggles, arbitrary settings changes, or arbitrary commands.
The firmware also advertises maintenance operations; listing them does not establish a validated
physical sequence or calibrated travel limits for the Python driver.

## Transfer state

The operation notes describe a **virtual transfer position**, determined by plate geometry and a
plate-detection beam. `MicroServeStatus.plate_sensor_blocked` is that beam's signal. It is not a
reliable inventory count or a guarantee that a plate occupies the robot's transfer position.
The beam was blocked at the prepared receiving position even with no plates loaded.

Firmware 2.7.0.756 leaves `loaderStatus Extended` set after successful `retract`, even when both
loader axes report zero, their home sensors are on, and the stacker lock is released. The stale
field persists through later queries and carousel rotation. `MicroServeStatus.loader` preserves
that firmware field. `loader_retracted` checks both home sensors and the released lock;
`loader_extended` requires the extended field, both home sensors off, and the lock engaged.
Motion guards use these sensor checks, and a status missing any required sensor is rejected.

`is_ready()` reports readiness to present a plate to the robot, as described in API §5.16. It
returned false while the machine was homed, idle, and retracted, then true after preparing the
empty receiving position, and false again after retraction. Use `request_status()` for general
homing and busy state; a false `is_ready()` alone does not indicate a fault.

The driver records a confirmed load/unload preparation and verifies the selected stacker and
extended loader. Repeating the same preparation before retraction does not move another plate,
even if the beam changes when the robot acts. An already-extended loader without a preparation
owned by this driver is not assumed to represent the requested operation. Plate occupancy at the
robot interface remains the application's responsibility until physical transfer validation.

After an interrupted preparation, `unresolved_preparation` retains its direction, stacker, and ACK
identifier. Another preparation is blocked. If the controller has not rebooted and another client
has not moved it, `reconcile_preparation()` can confirm the original command completed successfully
and the loader is in the expected position. It does not resend the command. A failed, running,
missing, or mismatched record stays unresolved. Explicit retraction ends the handoff after the
operator has inspected the machine and the robot is clear.

Only this driver should control the MicroServe during a handoff. Command IDs reset on controller
restart. Reconnecting does not home, clear an abort, or infer success from the beam signal.

Measurements and barcode scans verify homed, idle, ready-mode status after completion. They do
not promise a retracted loader: `measurestacker` leaves it extended on the tested firmware.
Inspect the result and explicitly retract before another stacker operation. Failed or interrupted
measurements and scans retain `unresolved_operation`, which survives reconnection and blocks new
motion until inspected retraction. Read-only diagnostics remain available.

`enter_manual_mode()` requires a clear, retracted loader and verifies the manual transition before returning.
On firmware that leaves mode Ready stale, this means an acknowledged manual command followed by
unhomed, unselected, idle, retracted status. That owned transition makes repeated requests idempotent.
`home()` returns from manual access. `clear_abort()` clears the abort latch and checks status;
`recover_from_estop()` invokes firmware recovery only when the machine is not already homed and
ready. Recovery can move axes and never resolves an uncertain plate handoff by itself. Physical
fault transitions and their mode spellings still require validation.

The controller exposes axis jogs, but its documentation does not specify the complete sensor-driven
transfer sequence needed to reconstruct load/unload from those jogs. The driver uses the documented
load/unload operations for that sequence, while managing geometry, command ownership, state checks,
and reconciliation in Python.

## Verification limits

Queries and operator-approved empty-machine motion were captured on MicroServe HRB-2008-10558,
firmware 2.7.0.756, on September 8, 2026. Hardware checks covered homing, all fourteen carousel
positions, repeated targets, reconnecting, preparation of empty stacker 2, retraction, and rotation
after retraction. Existing geometry (11 mm height, 10 mm stack pitch, 10 mm thickness) was written
and read back; geometry was unchanged at the end. The controller reported no errors.

Exact transport exchanges are replayed through `SocketValidator`, including the stale loader flag
that caused the initial retraction check to fail. The captures verify that repeated target,
preparation, and retraction requests do not send additional motion commands. Simulated tests cover
timeout, cancellation, reply mismatch, unresolved preparation, and sensor disagreement.

**Plate transfer, unloading, barcode scans, and physical fault recovery remain unverified.** Setup
warns about these limits. Empty-machine tests do not establish pickup height with a plate, gripper
clearance, plate compatibility, or E-stop recovery. Those need a separate physical validation with
known plates and robot clearance.

On September 9, 2026, measurements of stackers 0–4 returned 0.059–0.086 mm and a user-loaded
single plate was located in stacker 5 with a 13.629 mm measurement. Measurements left the loader
extended; explicit retraction was verified between stackers. These readings were not compared
with an independent dimensional measurement.

A barcode scan with the saved 11/10/10 mm geometry failed with `Invalid plate count, check plate
dimensions`. The driver retained an unresolved operation and the diagnostic reply. That capture
verifies error handling, not successful barcode support. No transfer geometry was inferred from
this failure.

Manual access, repeated manual requests, return to homed/ready operation, and laser on/on/off
command exchanges were also captured. No additional errors appeared and plate geometry remained
unchanged. There is no laser-state readback, so the command capture does not independently verify
optical output.
