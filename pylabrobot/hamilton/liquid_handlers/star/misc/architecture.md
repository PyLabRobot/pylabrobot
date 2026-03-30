# STAR Architecture

## Overview

Split the monolithic `STARBackend` (~12k lines) into the new Driver + CapabilityBackend + Capability + Device architecture.

**Migrated so far:**
- PIP and Head96 (capability backends)
- iSWAP and CoRe gripper (arm backends)
- AutoLoad, Cover, X-Arms, Wash Station (plain helper classes on the driver)
- ~44 generic driver infrastructure methods (firmware queries, EEPROM, area reservation, configuration)

## Layers

```
STAR (Device) — only exposes Capabilities
  _driver ──────────► STARDriver (Driver)
  │                     │  Owns: USB I/O, firmware protocol, machine config
  │                     │
  │                     ├─ pip: STARPIPBackend (PIPBackend)
  │                     ├─ head96: STARHead96Backend (Head96Backend)     [optional]
  │                     ├─ iswap: iSWAP (OrientableGripperArmBackend)   [optional]
  │                     ├─ autoload: STARAutoload                       [optional]
  │                     ├─ left_x_arm: STARXArm
  │                     ├─ right_x_arm: STARXArm
  │                     ├─ cover: STARCover
  │                     └─ wash_station: STARWashStation                [optional]
  │
  pip: PIP ──────────► pip backend (above)
  head96: Head96 ────► head96 backend (above)
  iswap: OrientableArm ► iswap backend (above)
```

The STAR device only exposes Capabilities (PIP, Head96, iSWAP). Subsystems (autoload, x-arms, cover, wash station) and generic driver methods live on `star._driver`.

User code:

```python
star = STAR(deck=deck)
await star.setup()

# Capabilities — on the device
await star.pip.pick_up_tips(...)
await star.pip.aspirate(...)
await star.head96.aspirate96(...)
await star.iswap.move_resource(plate, destination)

# Subsystems — on the driver
await star._driver.autoload.load_carrier(carrier_end_rail=10)
await star._driver.left_x_arm.move_to(500.0)   # mm
await star._driver.cover.lock()
await star._driver.wash_station.drain(station=1)

# Generic driver methods
await star._driver.request_firmware_version()
await star._driver.halt()
```

## STARDriver

Subclass of `HamiltonLiquidHandler` (which extends `Driver`). Owns the USB connection and all firmware protocol logic.

```python
class STARDriver(HamiltonLiquidHandler):
    # Capability backends
    pip: STARPIPBackend                          # always present
    head96: Optional[STARHead96Backend] = None   # if 96-head installed
    iswap: Optional[iSWAP] = None               # if iSWAP installed

    # Plain subsystems
    autoload: Optional[STARAutoload] = None      # if autoload installed
    left_x_arm: Optional[STARXArm] = None        # always present
    right_x_arm: Optional[STARXArm] = None       # always present
    cover: Optional[STARCover] = None             # always present
    wash_station: Optional[STARWashStation] = None # always present
```

### Responsibilities

- **USB I/O**: Connect/disconnect via `pylabrobot.io.usb.USB`. Background reading thread for async command/response matching.
- **Firmware protocol**: `send_command(module, command, **params)` assembles the STAR text protocol, sends it, waits for matching response, parses it.
- **Machine configuration**: On `setup()`, queries `RM` (machine config) and `QM` (extended config) to discover installed hardware. Stores as `self.machine_conf` and `self.extended_conf`.
- **Backend/subsystem creation**: During `setup()`, creates backends based on discovered config. Conditional for autoload (needs `auto_load_installed`), Head96 (needs `core_96_head_installed`), iSWAP (needs `iswap_installed`). Unconditional for PIP, X-arms, cover, wash station.
- **Generic instrument operations**: Firmware queries, EEPROM read/write, runtime control (halt, single-step), area reservation, instrument configuration. ~44 methods directly on the driver.
- **Tip type registration**: `_tth2tti` mapping shared across PIP and Head96.
- **Error parsing**: Firmware error codes → Python exceptions.

### Generic driver methods (directly on STARDriver)

These are machine-level operations not specific to any capability:

- **Firmware queries**: `request_firmware_version`, `request_error_code`, `request_master_status`, `request_parameter_value`, `request_eeprom_data_correctness`, `request_electronic_board_type`, `request_supply_voltage`, `request_number_of_presence_sensors_installed`
- **Init/diagnostics**: `request_instrument_initialization_status`, `request_name_of_last_faulty_parameter`, `pre_initialize_instrument`
- **Runtime control**: `set_single_step_mode`, `trigger_next_step`, `halt`, `set_not_stop`, `save_all_cycle_counters`
- **EEPROM write**: `store_installation_data`, `store_verification_data`, `additional_time_stamp`, `save_download_date`, `save_technical_status_of_assemblies`, `set_x_offset_x_axis_*`, `save_pip_channel_validation_status`, `save_xl_channel_validation_status`, `configure_node_names`, `set_deck_data`, `set_instrument_configuration`
- **EEPROM read**: `request_technical_status_of_assemblies`, `request_installation_data`, `request_device_serial_number`, `request_download_date`, `request_verification_data`, `request_additional_timestamp_data`, `request_pip_channel_validation_status`, `request_xl_channel_validation_status`, `request_node_names`, `request_deck_data`
- **X-drive queries**: `request_maximal_ranges_of_x_drives`, `request_present_wrap_size_of_installed_arms`
- **Area reservation**: `occupy_and_provide_area_for_external_access`, `release_occupied_area`, `release_all_occupied_areas`

## Plain subsystem classes

These are NOT CapabilityBackends — they're plain helper classes that encapsulate firmware protocol for a subsystem and delegate I/O to the driver via `self._driver.send_command(...)`.

### STARAutoload

Controls the autoload module (carrier loading/unloading, barcode scanning, presence detection).

```python
class STARAutoload:
    def __init__(self, driver: STARDriver, instrument_size_slots: int = 54):
        self._driver = driver
        self._instrument_size_slots = instrument_size_slots
```

Key methods: `initialize`, `park`, `move_to_track`, `load_carrier`, `unload_carrier`, `request_presence_of_carriers_on_deck`, `request_presence_of_carriers_on_loading_tray`, `set_loading_indicators`, `verify_and_wait_for_carriers`, barcode operations.

Methods take `carrier_end_rail: int` instead of `Carrier` objects — the caller computes the rail from carrier geometry. This keeps the class free of deck/resource dependencies.

### STARXArm

Controls one X-arm (left or right). One class, parameterized by side — picks the correct firmware command inline.

```python
class STARXArm:
    def __init__(self, driver: STARDriver, side: Literal["left", "right"]):
        self._driver = driver
        self._side = side
```

Methods: `move_to(x_position)` (mm), `move_to_safe(x_position)` (mm), `request_position() -> float` (mm), `last_collision_type() -> bool`.

Command mapping:
| Operation | Left | Right |
|---|---|---|
| Position (collision risk) | C0:JX | C0:JS |
| Move safe (Z-safety) | C0:KX | C0:KR |
| Request position | C0:RX | C0:QX |
| Last collision type | C0:XX | C0:XR |

### STARCover

Controls the front cover.

```python
class STARCover:
    def __init__(self, driver: STARDriver):
        self._driver = driver
```

Methods: `lock`, `unlock`, `disable`, `enable`, `is_open`, `set_output`, `reset_output`.

### STARWashStation

Controls dual-chamber wash/pump stations.

```python
class STARWashStation:
    def __init__(self, driver: STARDriver):
        self._driver = driver
```

Methods: `request_settings(station)`, `initialize_valves(station)`, `fill_chamber(station, wash_fluid, chamber, ...)`, `drain(station)`.

## Capability backends

### STARPIPBackend

Implements `PIPBackend`. Translates PIP operations into STAR firmware commands.

Key methods: `pick_up_tips`, `drop_tips`, `aspirate`, `dispense`.

### STARHead96Backend

Implements `Head96Backend`. Translates 96-head operations into STAR firmware commands.

Key methods: `pick_up_tips96`, `drop_tips96`, `aspirate96`, `dispense96`.

### iSWAP

Implements `OrientableGripperArmBackend`. Controls the iSWAP plate gripper arm.

Key methods: `pick_up_at_location`, `drop_at_location`, `park`, `open_gripper`, `close_gripper`.

### CoreGripper

Implements `GripperArmBackend`. Uses two PIP channels as a Y-axis gripper. Managed through a context manager on the STAR device.

```python
async with star.core_grippers(front_channel=7) as arm:
    await arm.move_resource(plate, destination)
```

## STAR Device

The user-facing class. Wires driver backends to capability frontends during `setup()`.

```python
class STAR(Device):
    def __init__(self, deck: HamiltonDeck, chatterbox: bool = False):
        driver = STARChatterboxDriver() if chatterbox else STARDriver()
        super().__init__(driver=driver)
        self.deck = deck
```

### setup() flow

```
await star.setup()
  │
  ├─ await STARDriver.setup()
  │    1. Open USB, start background reading thread
  │    2. Query RM → MachineConfiguration
  │    3. Query QM → ExtendedConfiguration
  │    4. Create backends: pip (always), head96 (if installed), iswap (if installed)
  │    5. Create subsystems: autoload (if installed), x_arms, cover, wash_station (if installed)
  │
  ├─ Wire capability frontends to backends (on STAR device)
  │    self.pip = PIP(backend=driver.pip)
  │    self.head96 = Head96Capability(backend=driver.head96)   # if installed
  │    self.iswap = OrientableArm(backend=driver.iswap)        # if installed
  │
  └─ Call _on_setup() for each Capability
```

Subsystems (autoload, x_arms, cover, wash_station) stay on the driver — the STAR device does NOT re-expose them. Access via `star._driver.autoload`, etc.

### Optional hardware

On the device:
```python
star.head96        # None if no 96-head
star.iswap         # None if no iSWAP
```

On the driver:
```python
star._driver.autoload       # None if no autoload module
star._driver.wash_station   # None if no wash station
star._driver.left_x_arm     # always present
star._driver.right_x_arm    # always present
star._driver.cover          # always present
```

## File structure

```
pylabrobot/hamilton/liquid_handlers/star/
  __init__.py              # exports STAR, STARAutoload, STARCover, STARXArm, STARWashStation
  star.py                  # STAR(Device) — only capabilities
  driver.py                # STARDriver + config dataclasses + generic driver methods
  chatterbox.py            # STARChatterboxDriver (mock for testing)
  pip_backend.py           # STARPIPBackend(PIPBackend)
  head96_backend.py        # STARHead96Backend(Head96Backend)
  iswap.py                 # iSWAP(OrientableGripperArmBackend)
  core.py                  # CoreGripper(GripperArmBackend)
  autoload.py              # STARAutoload (plain class on driver)
  cover.py                 # STARCover (plain class on driver)
  x_arm.py                 # STARXArm (plain class on driver)
  wash_station.py          # STARWashStation (plain class on driver)
  tests/
    autoload_tests.py      # 41 tests
    cover_tests.py         # 11 tests
    x_arm_tests.py         # 16 tests
    wash_station_tests.py  # 27 tests
    iswap_tests.py         # 14 tests
    core_tests.py          # 7 tests
    legacy_parity_tests.py # PIP/Head96 parity tests
```

## Legacy compatibility

The legacy `STARBackend` in `pylabrobot/legacy/liquid_handling/backends/hamilton/STAR_backend.py` creates instances of the new classes in its `__init__`:

```python
self._new_pip = STARPIPBackend(self)
self._new_head96 = STARHead96Backend(self)
self._new_autoload = STARAutoload(driver=self)
self._new_cover = STARCover(driver=self)
self._new_left_x_arm = STARXArm(driver=self, side="left")
self._new_right_x_arm = STARXArm(driver=self, side="right")
self._new_wash_station = STARWashStation(driver=self)
```

Migrated methods delegate to these instances (with Carrier→int conversion where needed). All delegating methods have one-line deprecation docstrings:

```python
async def park_autoload(self):
    """Deprecated: use ``star.autoload.park()``."""
    return await self._new_autoload.park()
```

Generic driver methods (firmware queries, EEPROM, etc.) exist on both `STARDriver` and the legacy `STARBackend` — the legacy versions have deprecation docstrings but keep their original implementation bodies unchanged.

## What stays in legacy

- Probing/LLD: `probe_liquid_heights`, CLLD/PLLD methods
- Foil piercing: `pierce_foil`, `step_off_foil`
- Hotel mode: `put_in_hotel`, `get_from_hotel`
- Heater-shaker: HHC temperature control methods
- Some lower-level PIP/Head96/iSWAP firmware commands not yet migrated to backends
