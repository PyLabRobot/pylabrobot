# Transfers and recovery

`StoreX` owns a resource tree containing calibrated cassettes, a loading tray, and a
shovel. Cassette mapping keys are physical numbers starting at 1. Slot indices start
at 0. Inventory is based on the plates assigned to those resources; connecting to
the PLC does not discover the contents of the cassettes.

## Transfer stages

A fetch picks from the cassette onto the shovel, then places the plate on the tray.
A store picks from the tray onto the shovel, then places it in the selected cassette.
The driver waits for handling completion, checks available plate sensors, and
updates the resource assignment after each stage. Transfers between cassettes use
the same shovel and program each cassette's calibrated pitch separately.

An operation lock covers validation, motion, and inventory updates. A command lock
protects each serial exchange. Failed or cancelled writes are never automatically
repeated. A lost reply closes the serial connection to prevent a late response from
being mistaken for the reply to a subsequent command.

```{graphviz} state.dot
:alt: StoreX transfer and recovery states
```

## Inspect an interrupted transfer

After an ambiguous stage, `store.unresolved_transfer` identifies the plate, its last
confirmed support, and the intended destination. The resource tree retains the last
confirmed assignment unless a resource callback itself interrupted the update.
The driver blocks further transfers, gate movement, initialization, swap movement,
and barcode positioning until the transfer is reconciled.

1. Inspect the instrument and locate the plate. A timeout or Python cancellation
   does not stop the mechanism. `stop()` closes communication; it is not an emergency stop.
2. If disconnected, call `await store.setup()` to reconnect without homing. The
   unresolved transfer survives both stop and setup on this object.
3. If the device remains faulted or moving, use the manufacturer's recovery
   procedure before reconciliation. The driver will not reset or home an unresolved
   transfer. Cassette occupancy has no general nondestructive inventory query.
4. Call `await store.resolve_unresolved_transfer("source")`, `"destination"`, or
   `"unassigned"` to record the observed outcome. Choosing a cassette endpoint is
   operator confirmation of that slot. Tray and shovel occupancy must also agree
   with live sensors. `"unassigned"` means the plate was removed from both endpoints.

Reconciliation issues no motion. If the confirmed location is the shovel, continue
with `fetch_plate_to_loading_tray(plate)` or `store_plate(plate, slot)`. A failed
carousel-positioning operation can also leave a plate confirmed on the shovel:
inspect it, call `recover()`, then resume the intended transfer.

`recover()` clears a local recovery requirement without moving when handling is
already ready and initialized. Otherwise it resets and initializes only when the
shovel is empty and there is no unresolved transfer. This is an explicit operation
after inspection, not an automatic retry.

Inventory and recovery records are in memory. Device serialization is not supported;
restarting Python requires reconciling the inventory with the physical instrument.

## Configuration boundaries

The tracked transfer route is the primary station with the swap station at home.
Reversed stations, barcode positioning left enabled, and external handshake modes
are rejected before plate movement. A swap station requires the measured home end
switch (`swap_home_sensor=100` or `101`): relay 1912 indicates the commanded
destination, while timers T16/T17 indicate debounced physical arrival. The second
transfer-station sensor is readable, but a second independently tracked transfer
route is not implemented.

`StoreXCassette` carries `pitch`, `site_height`, and calibrated `pitch_steps` explicitly.
The nominal V0711 lookup table gives 3769 steps for 110 mm pitch. Instrument-specific
calibration takes precedence: the manual warns that third-generation lift drives
use a different reduction ratio. No calibration is inferred from a resource name.
Slot height includes lid clearance and is not expanded for the top slot.

Climate setpoints use degrees Celsius and fractional humidity/gas concentration.
Shaking uses the vendor's integer setting in 1..50; its physical frequency has not
been established. A value of 25 writes 25 to DM39. Optional controls must be declared
as installed; a model name alone does not establish those options.

## Protocol evidence and verification

The implementation follows *StoreX Series Remote Operation V0711*, sections 1.2–1.4
(command framing, readiness, commands, cassette configuration, and handling errors),
the *STX Driver* description (DM202 status and shaker settings), and LiCONiC FAQs
67/138 (debounced swap sensors) and 96 (end access). Serial requests end in CR and
responses in CRLF. Operations wait more than 200 ms before their first ready query
and check handling errors during polling. Early plate readiness uses relay 1815;
full handler readiness uses 1915.

Offline tests cover protocol framing, failures, cancellation, resource commits,
concurrent transfers, swap/gate completion, barcode cleanup, units, and cassette
calibration. This implementation has not been validated on hardware. Validate the
serial handshake and status queries first, then measured cassette positions, empty
mechanism movements, and a supervised plate transfer on the specific instrument.
