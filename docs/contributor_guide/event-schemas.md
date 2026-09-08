# Event Schema Registry

This registry defines canonical names and payload fields for EventBus records emitted by PLR.
It is the shared semantic contract between instrument frontends and event consumers.

The registry describes operation families, not required capabilities. A frontend implements only
the operations that its hardware and public API support. When it does implement an operation that
already appears here, it should use the canonical operation name and field meanings below.

For guidance on operation boundaries, instrumentation style, and testing, see the
[EventBus contributor guide](event-bus.md). For the exact frontend classes that currently emit
these operations, see [Current event coverage](../user_guide/machine-agnostic-features/event-bus.md#current-event-coverage).

## Extending the registry

When adding an event for a device or operation family that is not represented here:

1. Prefer a device-independent semantic name over a vendor-specific name when the operation has a
   clear cross-device meaning.
2. Reuse canonical fields from this registry when their meaning matches. Do not introduce a second
   name for an existing concept.
3. Add new fields only when they describe information the PLR operation actually knows.
4. Document the proposed operation and fields in this registry in the same contribution as the
   implementation.
5. Treat maintainer review of the new schema as establishing the convention for future frontends
   that implement that operation family.

Do not distort resource semantics or add presentation-only fields for a particular logger,
dashboard, or notification service.

## Record classes

### Semantic operation lifecycle

A semantic operation uses this lifecycle:

```text
<component>.<operation>.started
<component>.<operation>.completed
```

or, when it raises:

```text
<component>.<operation>.started
<component>.<operation>.failed
```

Lifecycle records share `context.operation` and `context.operation_id`. A failed record preserves
the operation's invocation data and adds:

| Field | Type | Meaning |
| --- | --- | --- |
| `error_type` | `str` | Exception class name. |
| `error_message` | `str` | String representation of the original exception. |

Tables in this guide describe fields in `event.data`. A field marked **completed only** is added
only after successful execution. All other listed fields describe invocation state and remain
stable across the lifecycle unless an operation-specific note says otherwise.

### State-transition records

State records describe an instantaneous PLR model transition and do not use the
`started`/`completed`/`failed` lifecycle. Current examples are `resource.assigned` and
`resource.unassigned`.

### Diagnostic records

Diagnostic events describe controller or transport activity. They may use a lifecycle when the
underlying command has a meaningful request and response, but they are not semantic frontend
operations. When emitted inside a semantic operation, they inherit its event context.

## Canonical common fields

| Field | Type | Meaning |
| --- | --- | --- |
| `device` | `DeviceReference` or `ResourceReference` | Device or controller issuing the operation. |
| `resources` | `list[ResourceReference]` | Direct PLR resources acted on by the operation. Omit or use an empty list when none are known. |
| `source` | `ResourceReference` or `CoordinateReference` | Physical origin of a resource transfer. |
| `destination` | `ResourceReference` or `CoordinateReference` | Physical destination of a resource transfer. |
| `duration` | `float` | Requested duration in PLR's default time unit. |
| `timeout` | `float` | Requested timeout in PLR's default time unit. |
| `target_temperature` | `float` | Configured or requested controller target temperature. |
| `current_temperature` | `float` | Controller sensor reading observed by the operation. It is not a resource-temperature measurement unless explicitly documented otherwise. |
| `target_humidity` | `float` | Requested relative-humidity setpoint as a fraction from 0 to 1. |
| `target_co2` | `float` | Requested CO2 concentration as a fraction from 0 to 1. |
| `target_o2` | `float` | Requested O2 concentration as a fraction from 0 to 1. |
| `tolerance` | `float` | Allowed temperature difference in PLR's default temperature unit. |
| `volume` | `float` | Requested liquid volume in PLR's default volume unit. |

Use `resource_reference()` for direct resources and resource endpoints. Its `ancestors` provide
structural context without replacing a `Well`, `TipSpot`, plate, or holder with a more convenient
display resource. Use `coordinate_reference()` for geometric endpoints and targets.

Quantitative fields use PLR's [default units](../user_guide/getting-started/units.md). Add a suffix
only when the value deliberately uses a different representation, such as `speed_rpm` or
`speed_pct`.

### Canonical vocabulary

Use these names consistently across operation families:

| Concept | Canonical field | Do not introduce aliases such as |
| --- | --- | --- |
| Requested elapsed time | `duration` | `time`, `duration_s`, `duration_sec`, `seconds` |
| Maximum wait | `timeout` | `timeout_s`, `wait_time` |
| Requested thermal setpoint | `target_temperature` | `temperature_target`, `set_temperature`, `target_temperature_c` |
| Requested relative humidity | `target_humidity` | `humidity`, `humidity_pct`, `relative_humidity` |
| Requested CO2 concentration | `target_co2` | `co2`, `co2_pct`, `co2_fraction` |
| Requested O2 concentration | `target_o2` | `o2`, `o2_pct`, `o2_fraction` |
| Observed controller temperature | `current_temperature` | `actual_temperature`, `measured_temperature`, `current_temperature_c` |
| Temperature acceptance range | `tolerance` | `temperature_tolerance`, `tolerance_c` |
| Relative centrifugal force | `relative_centrifugal_force` | `g`, `g_force`, `rcf` |
| Direct operated resources | `resources` | `plates`, `labware`, `items` |
| Transfer endpoints | `source`, `destination` | `from`, `to` |
| Liquid volume | `volume` | `vol`, `volume_ul` |
| Successful command result | `response` | `reply`, `result_data` |

This vocabulary is semantic, not merely stylistic. For example, `target_temperature` is a
controller setpoint, while `current_temperature` is an observed controller reading. Do not use one
as an alias for the other.

## Machine lifecycle

| Operation | Fields | Notes |
| --- | --- | --- |
| `machine.setup` | `device`, `backend` | Initializes a generic machine frontend. |
| `machine.stop` | `device`, `backend` | Stops a generic machine frontend. |

Vendor frontends may use their own component name, such as `precise_flex.setup`, while preserving
the same lifecycle meaning.

## Resource-model state

These are state-transition records rather than semantic operation lifecycles.

| Event | Fields | Notes |
| --- | --- | --- |
| `resource.assigned` | `resource`, `parent`, `location` | Emitted after assignment. `location` is the child's relative `CoordinateReference`, or `None`. |
| `resource.unassigned` | `resource`, `previous_parent`, `previous_location` | Emitted after unassignment while preserving the former parent and relative location. |

## Resource transfer

### Incubators

| Operation | Fields | Notes |
| --- | --- | --- |
| `incubator.fetch_plate` | `device`, `resources`, `source`, `destination` | `resources` contains the directly moved plate; endpoints describe the storage site and loading tray when known. |
| `incubator.take_in_plate` | `device`, `resources`, `source`, `destination` | Moves the loading-tray plate into storage. A requested selector such as `"random"` or `"smallest"` may identify an unresolved destination at invocation. |
| `incubator.transfer_plate` | `device`, `resources`, `source`, `destination` | Moves a plate directly between two transfer nests or other incubator endpoints. |

### Stackers

| Operation | Fields | Notes |
| --- | --- | --- |
| `benchcel.downstack` | `device`, `resources`, `source`, `destination` | Moves the accessible plate from a stack to the loading tray. |
| `benchcel.upstack` | `device`, `resources`, `source`, `destination` | Moves the loading-tray plate onto a stack. |
| `benchcel.move_plate_between_stacks` | `device`, `resources`, `source`, `destination` | Moves the accessible plate between two stacks. |

### Centrifuge loaders

| Operation | Fields | Notes |
| --- | --- | --- |
| `centrifuge_loader.load` | `device`, `resources`, `source`, `destination` | Transfers the staging plate into the selected centrifuge bucket. |
| `centrifuge_loader.unload` | `device`, `resources`, `source`, `destination` | Transfers the selected bucket plate onto the staging holder. |

### Liquid-handler resource movement

| Operation | Fields | Notes |
| --- | --- | --- |
| `liquid_handler.resource_pickup` | `device`, `resources`, optional `source` | `resources` contains the directly picked-up resource. Capture `source` before successful pickup unassigns it. |
| `liquid_handler.resource_move` | `device`, `resources` | Moves the currently held resource without assigning it to a destination. |
| `liquid_handler.resource_drop` | `device`, `resources`, `destination` | Drops the currently held resource at a resource or geometric destination. |

### Manual operator actions

Manual actions use the semantic lifecycle `manual_operator.<action>.*`, where `<action>` is a
stable, developer-defined action identifier such as `centrifuge.spin`, `plate_reader.read`, or
`quality_control.inspect`.

| Operation | Fields | Notes |
| --- | --- | --- |
| `manual_operator.<action>` | `device`, optional `resources`, `manual_action`, `title`, `instructions`, `confirmation_text`, `details`; **completed only:** optional `confirmed_by`, optional `result_message` | `device` is the `ManualOperator`; `details` contains action-specific request data. When the action has an automated counterpart, reuse its canonical field names and PLR default units inside `details`. |
| `manual_operator.resource.move` | `device`, `resources`, `source`, `destination`, `manual_action`, `title`, `instructions`, `confirmation_text`, optional `details`; **completed only:** optional `confirmed_by`, optional `result_message` | `resources` contains the directly moved resource. `source` and `destination` are its actual modeled transfer endpoints. When supplied, `details.destination_rotation` is the explicit local rotation relative to `destination`, not an absolute/world rotation. PLR composes its resulting absolute rotation with the destination's absolute rotation; use the local pose that an equivalent automated transfer would produce. It is never inferred from the destination. The subsequent model update emits normal `resource.unassigned` and `resource.assigned` state transitions. |

Manual action providers decide how an operator acknowledges the request. Cancellation,
provider-reported failure, invalid provider results, and provider exceptions produce the normal
failed lifecycle record with `error_type` and `error_message`.

## Liquid handling

### Channelized liquid operations

| Operation | Fields |
| --- | --- |
| `liquid_handler.aspirate` | `device`, `resources`, `liquid_operations` |
| `liquid_handler.dispense` | `device`, `resources`, `liquid_operations` |

`resources` contains the unique direct containers operated on. `liquid_operations` contains one
record per channel:

| Field | Type | Meaning |
| --- | --- | --- |
| `channel` | `int` | Liquid-handler channel index. |
| `resource` | `ResourceReference` | Direct operated container, normally a well or trough. |
| `plate` | `ResourceReference` | Owning plate when one exists; otherwise the direct container. |
| `volume` | `float` | Requested channel volume. |

### Channelized tip operations

| Operation | Fields |
| --- | --- |
| `liquid_handler.tip_pickup` | `device`, `resources`, `tip_operations` |
| `liquid_handler.tip_drop` | `device`, `resources`, `tip_operations` |

`resources` contains unique direct `TipSpot` or `Trash` resources. `tip_operations` contains one
record per channel with `channel` and direct `resource` fields.

### 96-head tip operations

| Operation | Fields | Notes |
| --- | --- | --- |
| `liquid_handler.tip_pickup_96` | `device`, `resources` | Direct resource is the operated `TipRack`. |
| `liquid_handler.tip_drop_96` | `device`, `resources` | Direct resource is the destination `TipRack` or `Trash`. |

## Plate reading and imaging

### Plate-reader lifecycle and measurements

| Operation | Fields | Notes |
| --- | --- | --- |
| `plate_reader.open` | `device`, `resources` | Opens the reader. `resources` contains the directly loaded plate when one is assigned. |
| `plate_reader.close` | `device`, `resources` | Closes the reader. `resources` contains the directly loaded plate when one is assigned. |
| `plate_reader.read_luminescence` | `device`, `resources`, `well_count`, `return_format`, `focal_height`; **completed only:** `record_count` | Reads luminescence from the selected wells. |
| `plate_reader.read_absorbance` | `device`, `resources`, `well_count`, `return_format`, `wavelength_nm`; **completed only:** `record_count` | Reads absorbance at the requested wavelength. |
| `plate_reader.read_fluorescence` | `device`, `resources`, `well_count`, `return_format`, `excitation_wavelength_nm`, `emission_wavelength_nm`, `focal_height`; **completed only:** `record_count` | Reads fluorescence at the requested wavelengths. |

For measurement operations, `resources` contains the direct selected `Well` references. Their
`ancestors` retain the owning plate; do not replace the wells with that plate. `well_count` is the
number of selected wells, while `record_count` is the number of records returned by the backend.
`return_format` is `"records"` or `"legacy_matrix"` and describes the public return projection.
Measurement values are not copied into events.

`focal_height` uses PLR's default length unit (millimeters). Wavelengths are deliberately expressed
in nanometers and therefore use the `_nm` suffix.

### Imaging

| Operation | Fields | Notes |
| --- | --- | --- |
| `imager.capture` | `device`, `resources`, optional `plate`, `target`, `mode`, `objective`, `exposure`, `focus`, `gain`; **completed only:** `image_count`, `reported_exposure_time_ms`, `reported_focal_height` | Captures one user-requested imaging result. Software auto-exposure or autofocus retries remain inside this single lifecycle. |

`target` contains integer `row` and `column` indices. `mode` and `objective` are stable enum member
names. When the caller supplies a `Well`, `resources` contains that direct well reference; a
row/column tuple has no direct resource and uses an empty list. `plate`, when known, identifies the
loaded plate that provides target context.

The three requested setting objects are JSON-ready and use these shapes:

| Setting | Modes and fields |
| --- | --- |
| `exposure` | Fixed: `mode="fixed"`, `time_ms`; machine auto: `mode="machine_auto"`; software auto: `mode="software_auto"`, `minimum_time_ms`, `maximum_time_ms`, optional `max_rounds`. |
| `focus` | Fixed: `mode="fixed"`, `height`; machine auto: `mode="machine_auto"`; software auto: `mode="software_auto"`, `minimum_height`, `maximum_height`, `tolerance`, `timeout`. |
| `gain` | Fixed: `mode="fixed"`, `value`; machine auto: `mode="machine_auto"`. |

Exposure values use milliseconds, as made explicit by `_ms`. Focus heights and focus tolerance use
PLR's default length unit; autofocus `timeout` uses the default time unit. The completed event
contains only bounded result metadata. Pixel arrays and other image data are never included.

`ImageReader` inherits the PlateReader and Imager public operations. It emits the inherited
canonical lifecycle directly and must not add a second wrapper lifecycle.

Backend-only keyword arguments are forwarded to the backend but are not part of these canonical
payloads.

## Thermocycling

Controllers that are `ResourceHolder`s include their directly loaded resource in `resources` when
one is assigned at operation start.

| Operation | Fields | Notes |
| --- | --- | --- |
| `thermocycler.open_lid` | `device`, optional `resources` | Opens the thermocycler lid. |
| `thermocycler.close_lid` | `device`, optional `resources` | Closes the thermocycler lid. |
| `thermocycler.set_block_temperature` | `device`, optional `resources`, `target_temperatures` | Sets one temperature per block zone. |
| `thermocycler.set_lid_temperature` | `device`, optional `resources`, `target_temperatures` | Sets one temperature per lid zone. |
| `thermocycler.deactivate_block` | `device`, optional `resources` | Turns off block temperature control. |
| `thermocycler.deactivate_lid` | `device`, optional `resources` | Turns off lid temperature control. |
| `thermocycler.run_protocol` | `device`, optional `resources`, `block_max_volume`, `stage_count`, `step_definition_count`, `step_execution_count`, optional `temperature_zone_count` | Submits a bounded summary of the requested protocol. Completion means the backend coroutine returned successfully, not that the physical temperature profile finished. |

`target_temperatures` is an ordered list in PLR's default temperature unit (degrees Celsius); it is
plural because the public API supports multiple thermal zones. `block_max_volume` uses PLR's
default volume unit (microliters). `step_definition_count` counts the distinct step definitions in
all stages, and `step_execution_count` includes stage repetition. `temperature_zone_count` is
included when it can be derived from the protocol.

The complete `Protocol`, individual temperatures and hold times, backend return value, and backend
keyword arguments are deliberately excluded from the event payload.

`run_pcr_profile` is a composite convenience method and has no separate parent lifecycle; its
instrumented primitive calls emit their normal events. Thermocycler status queries and wait helpers
are not yet instrumented because their polling and completion semantics need to be stabilized
before they can define canonical EventBus operations.

## Shaking and environmental control

Controllers that are `ResourceHolder`s include their directly loaded resource in `resources` when
one is assigned at operation start.

| Operation | Fields | Notes |
| --- | --- | --- |
| `shaker.shake` | `device`, optional `resources`, `speed_rpm`, optional `duration` | Omitted `duration` means shaking continues after the call returns. |
| `shaker.stop_shaking` | `device`, optional `resources` | Explicitly stops an indefinite shake. |
| `temperature_controller.set_temperature` | `device`, optional `resources`, `target_temperature`, `passive` | Records the requested target and cooling policy. |
| `temperature_controller.activate` | `device`, optional `resources` | Starts active temperature control at the configured setpoint. |
| `temperature_controller.wait_for_temperature` | `device`, optional `resources`, `target_temperature`, `timeout`, `tolerance`; **completed only:** `current_temperature` | `current_temperature` is the final controller reading that satisfied tolerance. |
| `temperature_controller.hold_temperature` | `device`, optional `resources`, `duration`, optional `target_temperature` | Records a requested dwell without reissuing a setpoint or asserting that a resource reached temperature. |
| `temperature_controller.deactivate` | `device`, optional `resources`, optional `target_temperature` | Stops active temperature control. |
| `humidity_controller.set_humidity` | `device`, optional `resources`, `target_humidity` | Records the requested relative-humidity fraction. |
| `humidity_controller.activate` | `device`, optional `resources` | Starts active humidity control at the configured setpoint. |
| `humidity_controller.deactivate` | `device`, optional `resources` | Stops active humidity control. |
| `co2_controller.set_co2` | `device`, optional `resources`, `target_co2` | Records the requested CO2 fraction. |
| `co2_controller.activate` | `device`, optional `resources` | Starts active CO2 control at the configured setpoint. |
| `co2_controller.deactivate` | `device`, optional `resources` | Stops active CO2 control. |
| `o2_controller.set_o2` | `device`, optional `resources`, `target_o2` | Records the requested O2 fraction. |
| `o2_controller.activate` | `device`, optional `resources` | Starts active O2 control at the configured setpoint. |
| `o2_controller.deactivate` | `device`, optional `resources` | Stops active O2 control. |

## Centrifugation

| Operation | Fields | Notes |
| --- | --- | --- |
| `centrifuge.spin` | `device`, `resources`, `bucket_resources`, `duration`; at least one of `relative_centrifugal_force` or `speed_rpm`; optional `acceleration_fraction`, `deceleration_fraction` | Describes one requested spin cycle using the frontend's requested force or speed. |

`resources` contains directly loaded resources only. Empty buckets are not represented.
`bucket_resources` preserves the association between each loaded resource and its holder:

```python
{
  "holder": resource_reference(bucket),
  "resource": resource_reference(plate),
}
```

A frontend without a PLR rotor-resource model emits empty `resources` and `bucket_resources`.
Hettich centrifuges report the requested `speed_rpm`; when a rotor catalog number is configured,
they also report the corresponding calculated `relative_centrifugal_force`. VSpin reports the
requested `relative_centrifugal_force`, `acceleration_fraction`, and `deceleration_fraction`.

`relative_centrifugal_force` is the dimensionless multiple of standard gravity conventionally
written as x g. Acceleration and deceleration are fractions of the device maximum.

## Brooks PreciseFlex

PreciseFlex currently exposes vendor-specific controller operations. These records describe the
controller command and geometric or joint target; a higher-level resource-aware integration should
emit separate resource-transfer operations when it knows the moved PLR resource.

### Lifecycle and controller state

| Operation | Fields |
| --- | --- |
| `precise_flex.setup` | `device`, `skip_home` |
| `precise_flex.stop` | `device` |
| `precise_flex.power_on` | `device` |
| `precise_flex.power_off` | `device` |
| `precise_flex.recover_from_fault` | `device` |
| `precise_flex.home` | `device` |
| `precise_flex.start_freedrive` | `device`, optional `free_axes` |
| `precise_flex.stop_freedrive` | `device` |
| `precise_flex.halt` | `device` |
| `precise_flex.park` | `device` |

### Motion

| Operation | Fields |
| --- | --- |
| `precise_flex.move_to_joint_position` | `device`, `target_joint_position`, optional `speed_pct` |
| `precise_flex.move_to_location` | `device`, `target`, optional `speed_pct` |
| `precise_flex.move_through_cartesian_poses` | `device`, `waypoint_count`, optional `start_target`, optional `end_target`, optional `speed_pct`, `blend` |
| `precise_flex.move_gripper` | `device`, `width`, `force_sensing` |
| `precise_flex.move_gripper_joint_position` | `device`, `gripper_joint_position`, `force_sensing` |
| `precise_flex.move_rail` | `device`, `rail_position` |
| `precise_flex.pick_up_at_joint_position` | `device`, `target_joint_position`, `resource_width`, `finger_speed_pct`, `grasp_force` |
| `precise_flex.drop_at_joint_position` | `device`, `target_joint_position`, `resource_width` |
| `precise_flex.pick_up_at_location` | `device`, `target`, `resource_width`, `finger_speed_pct`, `grasp_force` |
| `precise_flex.drop_at_location` | `device`, `target`, `resource_width` |

`target_joint_position` maps axis names to positions. A Cartesian `target` contains a serialized
`location`, approach `direction`, optional elbow `orientation`, optional `wrist`, and optional
`rail_position`. Lengths use PLR's default unit, `grasp_force` uses the default force unit, and
percentage values use the `_pct` suffix.

## Diagnostic transports and firmware

### Serial, USB, and FTDI

`io.read` and `io.write` are instantaneous diagnostic records:

| Field | Meaning |
| --- | --- |
| `transport` | `"serial"`, `"usb"`, or `"ftdi"`. |
| `device` | Human-readable transport device name. |
| `device_id` | Port, serial number, or transport-specific identifier. |
| `data` | Decoded or hexadecimal transport payload. |

### Hamilton firmware commands

`firmware.command.started`, `firmware.command.completed`, and `firmware.command.failed` use:

| Field | Lifecycle | Meaning |
| --- | --- | --- |
| `transport` | all | `"hamilton_usb"`. |
| `driver` | all | Hamilton driver class name. |
| `module` | all | Firmware module identifier. |
| `command` | all | Firmware command identifier. |
| `command_id` | all | Correlation identifier assigned by the driver. |
| `raw_command` | all | Full assembled command. |
| `response` | completed | Raw firmware response, if any. |
| `error_type`, `error_message` | failed | Original exception details. |

### PreciseFlex firmware commands

`precise_flex.firmware_command.started`, `precise_flex.firmware_command.completed`, and
`precise_flex.firmware_command.failed` use `device` and `command` in the full lifecycle, `response`
on completion, and `error_type` plus `error_message` on failure.

## Schema compatibility

Treat operation names and documented field meanings as public integration contracts:

- Adding an optional field is normally backward compatible.
- Adding completion-only information is normally backward compatible when invocation fields stay
  stable.
- Renaming a field, changing its units, replacing a direct resource with an ancestor, or changing
  the meaning of an existing field is a compatibility change and requires explicit review.
- Vendor-specific extensions should not silently redefine a canonical cross-device field.

When implementation and this registry diverge, update them together and add tests that assert the
canonical operation name, lifecycle, and payload fields.
