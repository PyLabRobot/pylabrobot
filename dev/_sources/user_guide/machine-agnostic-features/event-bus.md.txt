# EventBus

PLR's EventBus is an optional, in-process source of structured execution events. It is useful
when an application needs to persist an execution log, display progress, or correlate a failed
operation with device diagnostics without parsing human-readable logs.

Event observation is opt-in. Creating or subscribing to a bus does not change protocol control
flow, and a subscriber exception is isolated from the instrument operation that emitted the
event.

## Subscribe to events

Install a bus for the scope that should produce events, then subscribe a fast callback. The
callback should enqueue or persist the event rather than perform slow work synchronously.

```python
from pylabrobot.events import EventBus, use_event_bus

event_bus = EventBus()
event_bus.subscribe(lambda event: print(event.as_dict()))

with use_event_bus(event_bus):
  await machine.setup()
  # Other instrumented PLR operations emit events in this scope.
```

Use `set_default_event_bus()` when one process-wide bus is appropriate. `use_event_bus()` is
preferred for a bounded protocol or task because it is context-local and composes safely with
async tasks.

## Event shape

Every event has the following JSON-ready representation:

```python
{
  "sequence": 42,
  "name": "incubator.fetch_plate.completed",
  "timestamp": "2026-08-10T12:34:56.789012+00:00",
  "context": {
    "operation": "incubator.fetch_plate",
    "operation_id": "...",
  },
  "data": {
    "device": {"name": "incubator", "type": "Incubator"},
    "resources": [{"name": "plate_1", "type": "Plate"}],
  },
}
```

Semantic operations emit a correlated lifecycle pair:

```text
<component>.<operation>.started
<component>.<operation>.completed
```

If the operation raises, the second record is `<component>.<operation>.failed`, with
`error_type` and `error_message`. All records for the operation share `context.operation_id`.

Resource fields describe the direct PLR resource involved in the call. Each reference can include
structural `ancestors`; applications that want to display an owning plate or rack should derive
that view from the reference rather than replacing the operated resource in the event.

Geometric targets use PLR's serialized `Coordinate` representation, for example
`{"x": 12.5, "y": 8.0, "z": 42.0, "type": "Coordinate"}`.

Quantitative fields use PLR's [default units](../getting-started/units.md) without repeating the
unit in the field name. A suffix is used only when a value deliberately differs from the default,
such as rotational `speed_rpm` instead of PLR's default linear speed.

## Add application context

Applications may attach their own execution context around PLR calls. This is useful for run or
batch identifiers that PLR itself cannot know.

```python
from pylabrobot.events import event_context

with use_event_bus(event_bus), event_context(run_id="run-42", batch_id="batch-2"):
  await incubator.fetch_plate_to_loading_tray(site)
```

The values are inherited by nested PLR events. Keep this context application-specific; device
events continue to describe only what the PLR frontend actually did.

## Semantic and diagnostic events

Instrumented frontend operations emit semantic events such as
`liquid_handler.aspirate.completed` or `precise_flex.move_to_location.completed`. Some transports
also emit lower-level diagnostic events:

- `io.read` and `io.write` from the serial, USB, and FTDI transports
- `firmware.command.started`, `.completed`, and `.failed` from the Hamilton USB driver
- `precise_flex.firmware_command.*` from the PreciseFlex controller

Both event classes can be subscribed to. Semantic events preserve PLR operation meaning;
diagnostic events preserve controller and transport activity for debugging.

## Current event coverage

EventBus adoption is incremental. The initial implementation instruments the following public
frontends. Each listed semantic operation emits `started`, `completed`, and `failed` lifecycle
events.

| Frontend | Operations |
| --- | --- |
| `legacy.machines.Machine` | `machine.setup`, `machine.stop` |
| `legacy.storage.Incubator` | `incubator.fetch_plate`, `incubator.take_in_plate` |
| `legacy.liquid_handling.LiquidHandler` | resource pickup/move/drop; tip pickup/drop; 96-head tip pickup/drop; aspirate; dispense |
| `legacy.shaking.Shaker` | `shaker.shake`, `shaker.stop_shaking` |
| `legacy.temperature_controlling.TemperatureController` | set temperature, wait for temperature, deactivate |
| `agilent.vspin.VSpin` | `centrifuge.spin` |
| `agilent.vspin.Access2` | `centrifuge_loader.load`, `centrifuge_loader.unload` |
| `brooks.precise_flex.PreciseFlex` | lifecycle, fault/home/freedrive, joint/cartesian/rail/gripper motion, pick/drop, park |

Detailed operation references:

- [Machine lifecycle](event-bus/machine-lifecycle.md)
- [Incubator](event-bus/incubator.md)
- [LiquidHandler](event-bus/liquid-handler.md)
- [Shaker and temperature controller](event-bus/thermal-and-shaking.md)
- [Agilent VSpin and Access2](event-bus/vspin.md)
- [Brooks PreciseFlex](event-bus/precise-flex.md)
- [Diagnostic transports](event-bus/diagnostic-transports.md)

```{toctree}
:hidden:

event-bus/machine-lifecycle
event-bus/incubator
event-bus/liquid-handler
event-bus/thermal-and-shaking
event-bus/vspin
event-bus/precise-flex
event-bus/diagnostic-transports
```

## Operation reference

### Incubator

`incubator.fetch_plate` and `incubator.take_in_plate` include `device`, the moved plate in
`resources`, and physical `source` and `destination` resource references.

### LiquidHandler

Resource pickup events include the source holder when the moved resource is assigned at invocation
time; resource-drop events include their destination. Aspirate and dispense events include direct
operated resources plus `liquid_operations`, one record per channel, with `channel`,
`resource`, optional owning `plate`, and `volume`. Tip events similarly include direct tip
locations and per-channel `tip_operations`.

### Shaker and TemperatureController

Shaker events include `speed_rpm` and optional `duration`. Temperature-controller events include
`target_temperature` where applicable. Both frontends are `ResourceHolder`s: when a
resource is loaded at operation start, it is included as the direct resource in `resources`.

### Brooks PreciseFlex

PreciseFlex motion events identify the controller in `device`. Cartesian target payloads use a
serialized `Coordinate` in `target.location`; joint targets use axis-name-to-value mappings.
`pick` and `drop` describe controller actions. A resource-aware wrapper should emit the separate
resource-transfer event when it has PLR resource context.

## More detail

The [EventBus contributor guide](../../contributor_guide/event-bus.md) defines the stable naming,
resource, and test conventions for driver authors adding coverage.
