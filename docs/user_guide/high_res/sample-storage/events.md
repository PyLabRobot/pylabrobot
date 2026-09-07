# Sample-storage events

HighRes sample stores emit structured events for plate transfers and changes to environmental
control. Event observation is optional and does not change device behavior.

Every operation listed below emits one of these correlated lifecycle sequences when an active
EventBus has a subscriber:

```text
<operation>.started -> <operation>.completed
<operation>.started -> <operation>.failed
```

The two records share `context.operation_id`. A failed record also contains `error_type` and
`error_message`.

## Quickstart

Subscribe after setting up the store, then wrap the operations that should be observed:

```python
from pylabrobot.events import EventBus, use_event_bus

event_bus = EventBus()
event_bus.subscribe(lambda event: print(event.as_dict()))

await store.setup()

with use_event_bus(event_bus):
  await store.fetch_plate_to_loading_tray("plate_1", tray_index=0)
  await store.environment.set_temperature(37)
```

Use `set_default_event_bus()` instead when one process-wide bus should observe every instrumented
operation. See the general [EventBus guide](../../machine-agnostic-features/event-bus.md) for event
shape, application context, and subscriber behavior.

## Transfer events

| Operation | Emitted by | Fields |
| --- | --- | --- |
| `incubator.fetch_plate` | `fetch_plate_to_loading_tray()` | `device`, `resources`, `source`, `destination` |
| `incubator.take_in_plate` | `take_in_plate()` and `store_plate()` | `device`, `resources`, `source`, `destination` |
| `incubator.transfer_plate` | `transfer_plate_between_nests()` | `device`, `resources`, `source`, `destination` |

`resources` contains the plate being moved. `source` and `destination` identify the actual
`PlateHolder` resources: a stacker slot and a device-reported transfer nest.

The transfer event encloses both the hardware command and PLR bookkeeping. A successful transfer
therefore produces this sequence, all with the same `operation_id`:

```text
incubator.fetch_plate.started
resource.unassigned
resource.assigned
incubator.fetch_plate.completed
```

For `take_in_plate()` and `store_plate()`, the equivalent outer event is
`incubator.take_in_plate`; nest-to-nest moves use `incubator.transfer_plate`. If validation or
hardware motion fails after the operation begins, the plate remains at its original PLR location
and the outer operation emits `.failed`.

Example invocation data:

```python
{
  "device": {"name": "steristore", "type": "SteriStore", "model": "SteriStore"},
  "resources": [{"name": "plate_1", "type": "Plate"}],
  "source": {"name": "rack_1_slot_3", "type": "PlateHolder"},
  "destination": {"name": "steristore_nest_1", "type": "PlateHolder"},
}
```

Resource references may also contain rotation and ancestor information.

## Environmental-control events

Environmental events identify the sample store in `device` and use an empty `resources` list.
Humidity and gas targets are fractions, matching the Python API: `0.90` means 90% RH and `0.05`
means 5% gas concentration.

| Control | Models |
| --- | --- |
| Temperature | SteriStore and TundraStore |
| Relative humidity | SteriStore and TundraStore |
| CO2 | SteriStore |
| O2 | SteriStore when the optional controllable channel is installed |

AmbiStore does not expose `store.environment`, so it emits only transfer events.

| Operation | Emitted by | Additional invocation fields |
| --- | --- | --- |
| `temperature_controller.set_temperature` | `set_temperature()` | `target_temperature`, `passive=False` |
| `temperature_controller.activate` | `start_temperature_control()` | — |
| `temperature_controller.deactivate` | `stop_temperature_control()` | — |
| `humidity_controller.set_humidity` | `set_humidity()` | `target_humidity` |
| `humidity_controller.activate` | `start_humidity_control()` | — |
| `humidity_controller.deactivate` | `stop_humidity_control()` | — |
| `co2_controller.set_co2` | `set_co2()` | `target_co2` |
| `co2_controller.activate` | `start_co2_control()` | — |
| `co2_controller.deactivate` | `stop_co2_control()` | — |
| `o2_controller.set_o2` | `set_o2()` | `target_o2` |
| `o2_controller.activate` | `start_o2_control()` | — |
| `o2_controller.deactivate` | `stop_o2_control()` | — |

For example, `await store.environment.set_co2(0.05)` emits
`co2_controller.set_co2.started` followed by either `co2_controller.set_co2.completed` or
`co2_controller.set_co2.failed`:

```python
{
  "device": {"name": "steristore", "type": "SteriStore", "model": "SteriStore"},
  "resources": [],
  "target_co2": 0.05,
}
```

Calling an unavailable channel is observable as a failed operation. For example, attempting O2
control on a store without an installed controllable O2 channel emits
`o2_controller.set_o2.failed` with `error_type="NotImplementedError"`.

## Operations without semantic events

Read-only status methods do not emit semantic operation events. This includes environmental
reads, tank-pressure reads, version and status requests, and inventory queries. `setup()`,
`stop()`, homing, recovery, door control, and clear-abort are also not currently
instrumented as semantic operations.

Generic `resource.assigned` and `resource.unassigned` state events can still be emitted whenever
PLR resource bookkeeping changes, including nest creation during initial setup when an EventBus is
active.
