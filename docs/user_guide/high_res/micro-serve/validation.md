# Hardware validation procedure

The implemented operations and their evidence are listed in the [capability audit](capabilities.md).
The sequence below is pending physical validation. Run it with an operator present, the robot
clear, known plate geometry, and suitable empty plates. Record the commands and replies with
PyLabRobot's I/O capture facilities. Stop on an unexpected state or measurement; inspect before
choosing recovery, and never automatically repeat an interrupted plate operation.

## Empty-machine checks

1. Query identity, status, errors, dimensions, and cached counts. Confirm the intended controller,
   a retracted loader, and no plates in the selected stacker or transfer position. Save geometry
   and cached counts for comparison after testing.
2. Enter manual mode and verify the carousel is released. Repeat the call and confirm there is
   no second `manual` command. Clear the machine, call `home()`, and verify homed/idle/ready.
3. Measure the empty selected stacker three times. Record the actual reply format and convert
   micrometers to millimeters. Results should be close to zero within the commissioned tolerance.
   Inspect loader sensors after each operation and retract if necessary. A discrepancy requires
   calibration investigation, not an automatic offset adjustment.
4. With known geometry, actively count that empty stacker and verify the cached result is zero.
   Repeat to check consistency. Verify an unchanged cached-count target sends no bookkeeping write.
5. Command the barcode laser on, on again, then off while observing its state without looking
   into the beam. No scanner-axis motion should occur.

## Plate handoffs, measurement, and barcodes

Use at least three compatible empty plates with known, readable barcodes. Measure plate height,
stack pitch, and well thickness as described in the hello-world guide. Do not substitute the
11/10/10 mm geometry from the empty-machine captures for the actual plates.

1. Prepare an empty receiving position using the plate's geometry. Verify extension and selected
   stacker. Repeat the preparation before placing a plate and confirm no second load command.
2. The operator places one plate at the documented receiving position and withdraws. Explicitly
   retract and inspect that the plate is supported in the stacker. Repeat this handoff to build
   the test stack, observing each placement and retraction.
3. Measure and actively count the stack. Compare the result against the physical plate count
   and measured dimensions; repeated measurements should agree within the commissioned tolerance.
4. Scan that stacker, record the exact barcode reply format, and compare every returned barcode
   against the physical stack. Do not interpret a missing barcode as proof of an empty position.
5. Prepare one plate for unloading, inspect its support and pickup position, then repeat the
   preparation and confirm no second plate is fetched. The operator removes the presented plate,
   withdraws, and explicitly retracts the loader.
6. Repeat using angle-reporting preparation. Record the raw angle; establish its unit before
   using it to command a robot rotation. A repeated preparation must only query the cached angle.
7. Repeat until the test stack is empty. Compare counts, sensors, errors, and geometry against
   the expected end state. Verify a final carousel move with the loader retracted.

## Four-stacker dimension calculation

This is a separate setup. The firmware procedure specifies stacker 1 empty, one upside-down plate
in stacker 2, one upright plate in stacker 3, and at least three plates in stacker 4. Confirm the
physical labeling against command indices before placing plates. Run `calculate_plate_dimensions`
and compare every returned dimension with independent measurements. Query settings and geometry
before and after to establish firmware calibration side effects; do not save or apply new values
without reviewing them.

## Fault recovery

Fault-state spelling, E-stop auto-recovery, and physical plate support during an interruption
remain unverified. Validate them separately, beginning with an empty machine:

1. Confirm the manufacturer's E-stop procedure and observe the stopped device's status. Check
   whether firmware starts recovery automatically when the E-stop is released.
2. Once the cause is resolved and the robot is clear, validate explicit abort clearance or
   E-stop recovery as appropriate. Verify homed/idle/ready and actual loader sensor state;
   neither call should erase an unresolved plate handoff.
3. Validate a deliberately interrupted measurement with a controlled connection loss. Reconnect
   and confirm the uncertainty blocks fresh motion. After inspection, explicit retraction must
   clear it without repeating the measurement.
4. Only after empty-machine recovery is established, assess an interrupted loaded handoff with
   known plate support. Validate exact-command reconciliation or inspected retraction, ensuring
   that no path fetches a second plate.

Do not induce a jam or collision to test an error handler. Simulated transport failures cover
those software paths while the physical recovery procedure is being established.
