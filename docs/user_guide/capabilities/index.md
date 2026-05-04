# Capabilities

Capabilities are the building blocks of device functionality in PyLabRobot. Each capability defines a standard interface for a specific type of lab operation (e.g. temperature control, shaking, plate reading), decoupled from any particular hardware.

A single device can expose multiple capabilities -- and capabilities can be optional (`None` if the hardware doesn't support it) or even duplicated (e.g. a device with two independent arms). For example, a heater-shaker exposes both a {class}`~pylabrobot.capabilities.temperature_controlling.temperature_controller.TemperatureController` and a {class}`~pylabrobot.capabilities.shaking.shaking.Shaker`.

## Architecture

```
Device
 ├── driver (hardware communication)
 └── capabilities
      ├── TemperatureController (backend)
      ├── Shaker (backend)
      └── ...
```

Each capability has two layers: a **frontend** and a **backend**.

### Frontend (the capability class)

The frontend is what you interact with as a user. It provides:

- A **stable, hardware-agnostic API** -- the same `set_temperature(37.0)` call works regardless of whether you're using an Inheco ThermoShake or a Hamilton Heater Cooler.
- **Validation** -- checking that arguments are in range, that the device is ready, that preconditions are met (e.g. you can't `wait_for_temperature` without first setting a target).
- **State tracking** -- keeping track of which tips are mounted, what the current tilt angle is, whether the door is open, etc.
- **Convenience methods** -- higher-level operations like `stamp` (aspirate + dispense) or `transfer` (one-to-many) built on top of the primitive backend calls.

The frontend is the same across all hardware that supports the capability.

### Backend (the hardware-specific implementation)

The backend is what talks to the actual hardware. Each capability defines an abstract backend class (e.g. {class}`~pylabrobot.capabilities.temperature_controlling.temperature_controller.TemperatureControllerBackend`, {class}`~pylabrobot.capabilities.shaking.shaking.ShakerBackend`) that specifies the methods a hardware driver must implement.

Backend methods are lower-level and closer to the wire protocol. For example, where the frontend {class}`~pylabrobot.capabilities.shaking.shaking.Shaker`.`shake(speed, duration)` handles timing and auto-stop, the backend only needs to implement `start_shaking(speed)` and `stop_shaking()`.

To add support for a new piece of hardware, you implement the backend interface for the capabilities it supports. The frontend takes care of the rest.

All capability methods are `async` and require the parent device to be set up before use (enforced by the `@need_capability_ready` decorator).

## Available capabilities

```{toctree}
:maxdepth: 1

temperature-control
shaking
fan-control
humidity-control
centrifuging
sealing
peeling
tilting
loading-tray
pumping
weighing
barcode-scanning
plate-access
microscopy
automated-retrieval
absorbance
fluorescence
luminescence
dispensing/index
pip
head96
arms
```
