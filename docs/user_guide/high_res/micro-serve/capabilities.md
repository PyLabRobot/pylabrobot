# Capability coverage

This audit covers the 64 TCP command names in firmware 2.7.0.756's `info all` reply and the
additional functions advertised by its REST catalog. Aliases and older commands often implement
the same capability, so command counts alone do not establish the registry's ≥90% threshold for
`full` support. The registry remains **WIP** while physical validation and maintenance coverage
are incomplete.

## Implemented operations

Methods below belong to `HighResMicroServe` unless prefixed with `stackers[i]`.
“Simulated” means the framing and control sequence have tests, but the operation has not passed a
physical test on this device. The API PDF's measurement examples also serve as parser fixtures.

| Firmware command | Python interface | Verification |
| --- | --- | --- |
| `status` | `request_status()` | Hardware capture |
| `varstatus` | `request_variable_status()` | Hardware capture; compact report has no sensors |
| `version` | `request_version()` | Hardware capture |
| `firmwareversion` | `request_firmware_version()` | Hardware capture |
| `detailedversion` | `request_detailed_version()` | Hardware capture |
| `copleyserial` | `request_motor_information()` | Hardware capture |
| `commandstat` | `request_command_status(id)` | Hardware capture; reconciliation scenarios simulated |
| `history` | `request_history(count)` | Hardware capture |
| `errors` | `request_errors(count)` | Hardware capture |
| `settings` | `request_settings(search)` | Hardware capture; read only |
| `info`, `help` | `request_command_catalog()`, `request_command_help(command)` | Hardware capture |
| `limits` | `request_limits()` | Hardware capture; values are not usable as travel limits |
| `queryhomeoffset` | `request_home_offset(address)` | Hardware capture; motor label and native unit retained |
| `getangle` | `request_plate_angle()` | Hardware capture; cached report, unit unspecified |
| `microserveready` | `is_ready()` | Hardware capture; plate-presentation readiness |
| `getplatecounts` | `request_plate_counts()`, `stackers[i].request_plate_count()` | Hardware capture; approximate cached counts |
| `dimstatus` | `request_dimensions()` | Hardware capture |
| `setstackerdimensions` | `stackers[i].set_dimensions(d)` | Existing geometry written and verified on hardware |
| `home` | `home()` | Hardware captures, including return from manual access |
| `spin` | `stackers[i].move_to()` | All fourteen positions verified |
| `load` | `stackers[i].prepare_for_load(d)` | Empty and single-plate preparation/retraction captured; external placement unverified |
| `unload` | `stackers[i].prepare_for_unload(d)` | Single-plate preparation/retraction captured; external pickup unverified |
| `unloadangle` | `stackers[i].prepare_for_unload_with_angle(d)` | Single-plate preparation/retraction captured; controller supplied no angle report, returned as `None` |
| `retract` | `retract()` | Empty loader verified |
| `readbarcodestacker` | `stackers[i].scan_barcodes(d)` | Empty and single-plate scans and geometry-error path captured; multi-plate scans unverified |
| `countplates` | `stackers[i].count_plates(d)` | Empty and repeated single-plate counts captured; multi-plate counting unverified |
| `measurestacker` | `stackers[i].measure_height()` | Empty and single-plate hardware captures; returns millimeters; accuracy not independently checked |
| `calculateplatedimensions` | `calculate_plate_dimensions(count)` | API example and simulated sequence |
| `setplatecount` | `stackers[i].set_plate_count(count)` | Hardware capture of 1 → 0 → 1 and repeated target; verified readback |
| `manual` | `enter_manual_mode()` | Hardware capture, including repeated owned request; requires retraction before access |
| `clearabort` | `clear_abort()` | Command acknowledgements captured with no active fault; actual abort recovery unverified |
| `estoprecover` | `recover_from_estop()` | Already-ready no-op captured; physical recovery remains simulated and may move axes |
| `laser` | `set_barcode_laser(enabled)` | Hardware on/on/off command capture; no firmware optical-state readback |

## Equivalent functions

These commands do not need separate public wrappers to provide their underlying capability.

| Firmware command | Existing interface |
| --- | --- |
| `list` | `request_command_catalog()` includes names and parameter descriptions |
| `disconnect` | `stop()` closes the transport |
| `loadl`, `unloadl` | Prepare the explicitly selected `stackers[i]` |
| `measure` | Measure the explicitly selected `stackers[i]` |
| `readbarcodeall` | Iterate the fourteen stackers with each stacker's geometry and collect scan results |
| `setplatedimensions`, `setplateheights`, `setplatethickness` | Set complete geometry per stacker; iterate for a machine-wide update |

Per-stacker iteration is not an atomic machine-wide operation. Inspect status and retract if a
measurement or scan leaves the loader extended before starting the next stacker. Failed
measurements and scans block subsequent motion until inspected retraction.

## Unimplemented maintenance and calibration

These are coverage gaps, not silently excluded capabilities. They require protocol clarification,
calibrated travel bounds, or a dedicated commissioning procedure before implementation.

| Firmware command | Remaining work |
| --- | --- |
| `jogb`, `jogc`, `joge`, `jogs` | Resolve the live firmware's position units and validate axis interlocks and travel bounds before exposing absolute targets |
| `jogbrel`, `jogcrel`, `jogerel`, `jogsrel` | Provide the corresponding motion capability through absolute targets; public relative jogs would violate the idempotent API guide |
| `homesel` | Resolve conflicting axis-number documentation and verify each axis's homing preconditions |
| `local`, `locall` | Verify directional carousel alignment and how it differs from positioning a selected stacker |
| `measureflag`, `measurestackerwidth` | Establish result format, calibration effects, and safe carousel alignment procedure |
| `beambreakheight` | Establish the returned unit and whether the TCP command applies or only measures the offset |
| `callaser` | Replace relative adjustments with a verified absolute calibration interface |
| `change`, `save`, `revert` | Define typed setting-specific APIs, readback, persistence, and recovery behavior |
| `savecvm` | Establish motor-program persistence and backup behavior |
| `reboot` | Establish reconnect behavior and invalidation of command IDs and handoff records |
| `sleep` | Firmware queue-testing utility; no public wrapper |

The REST catalog additionally advertises **Download Copley Files** and **Update Beam Break Height**.
Neither has a verified Python implementation. The latter must be distinguished from the TCP
measurement before writing calibration. Firmware installation and network administration pages
are also outside the implemented driver interface.

### Conflicting controller information

The TCP `homesel` help assigns 1 to the effectuator and 2 to the spatula. The REST catalog assigns
1 to the spatula and 2 to the effectuator. Read-only `queryhomeoffset` calls report address 1 as
Spatula and address 2 as Effectuator, but Copley addresses do not prove the `homesel` mapping.
The driver exposes the offset query by address and does not guess an axis homing map.

The `limits` reply contains a negative spatula maximum and zero effectuator maximum on the tested
machine. Those values are preserved for diagnosis and are not accepted as permissible travel.
Jog help describes micrometers for some axes, while the web commissioning controls and live
settings use different scales. Public physical units cannot be established from that help alone.

The `manual` command clears homing and the selected stacker but can leave mode Ready in the status
reply. The driver checks the acknowledged transition and resulting sensors, and retains ownership
for repeated manual requests. An arbitrary unhomed machine is not assumed to be in manual mode.

The documented `unloadangle` example includes a numeric reply, but the tested controller completed
the operation without supplying one. The method returns `None` in this case. A separate
`getangle` query returned `0.0000`; its freshness and unit are unverified, so it is not substituted
for the missing report. Repeated owned preparations preserve the original result.

A scan of stacker 5 completed with stacker 12 selected and the loader retracted. Scanning does
not guarantee that the scanned stacker remains at the transfer position. Subsequent motion uses
fresh status and an explicit target rather than assuming the scan's final position.

## Completing hardware validation

See the [hardware validation procedure](validation.md) for the supervised sequence. To justify
`full`, complete that validation, resolve the maintenance gaps above, and review coverage by
distinct capabilities against the registry definition. Documentation and simulated tests alone
do not establish that plate handoffs or fault recovery work on hardware.
