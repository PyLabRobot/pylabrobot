# PyLabRobot - Lab Automation Integration

## Project Overview
PyLabRobot is a hardware-agnostic Python library for lab automation. We are integrating several instruments for automated liquid handling, bulk dispensing, and robotic plate movement.

## Our Lab Equipment

### Tecan EVO 150 (Liquid Handler)
- **Backend**: `EVOBackend` from `pylabrobot.liquid_handling.backends.tecan`
- **Frontend**: `LiquidHandler` from `pylabrobot.liquid_handling`
- **Deck**: `EVO150Deck` (45 rails, 1315 x 780 x 765 mm)
- **Connection**: USB (VID=0x0C47, PID=0x4000)
- **Install**: `pip install -e ".[usb]"`

#### Default Deck Components
- **Plate carrier**: `MP_3Pos` (Tecan part no. 10612604) - 3-position microplate carrier
  - Import: `from pylabrobot.resources.tecan.plate_carriers import MP_3Pos`
- **Tip carrier**: `DiTi_3Pos` (Tecan part no. 10613022) - 3-position DiTi carrier
  - Import: `from pylabrobot.resources.tecan.tip_carriers import DiTi_3Pos`
- **Tips**: `DiTi_50ul_SBS_LiHa` - 50uL disposable tips for LiHa
  - Import: `from pylabrobot.resources.tecan.tip_racks import DiTi_50ul_SBS_LiHa`
- **Plates**: `Eppendorf_96_wellplate_250ul_Vb` - Eppendorf twin.tec 96-well (250uL, V-bottom)
  - Import: `from pylabrobot.resources.eppendorf.plates import Eppendorf_96_wellplate_250ul_Vb`

### Thermo Scientific Multidrop Combi (Bulk Dispenser)
- **Backend**: `MultidropCombiBackend` from `pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi`
- **Frontend**: `BulkDispenser` from `pylabrobot.bulk_dispensers`
- **Connection**: RS232 via USB adapter (specify COM port explicitly)
- **Serial config**: 9600 baud, 8N1, XON/XOFF
- **Install**: `pip install -e ".[serial]"`
- **Plate helpers**: `plate_to_type_index()`, `plate_to_pla_params()` for PLR plate → Multidrop mapping
- **Protocol docs**: `C:\Users\keyser\source\repos\keyser-sila-testing\documentation\Multidrop Combi Remote Control Command Sets (1).pdf`

### UFACTORY xArm 6 (Robotic Arm)
- **Backend**: `XArm6Backend` from `pylabrobot.arms.xarm6.xarm6_backend`
- **Frontend**: `SixAxisArm` from `pylabrobot.arms.six_axis`
- **Connection**: Ethernet (IP address)
- **Install**: `pip install xarm-python-sdk`

## Architecture Patterns

### Legacy Architecture (main branch)
Each device category follows this pattern:
- **Frontend class** (`Machine` subclass) - thin delegation layer with `@need_setup_finished` guards
- **Abstract backend** (`MachineBackend` subclass with `ABCMeta`) - defines the device-type interface
- **Concrete backend** - implements the abstract backend for a specific instrument
- **Chatterbox backend** - prints operations for testing without hardware

Key base classes:
- `pylabrobot/machines/backend.py` - `MachineBackend(SerializableMixin, ABC)`
- `pylabrobot/machines/machine.py` - `Machine(SerializableMixin, ABC)` + `need_setup_finished` decorator

### v1b1 Architecture (v1b1 branch)
New capability-based architecture replacing the monolithic backend model:
- **Driver** (`pylabrobot/device.py`) - owns I/O, connection lifecycle (`setup()/stop()`)
- **CapabilityBackend** (`pylabrobot/capabilities/capability.py`) - protocol translation for one concern
- **Capability** - user-facing API with validation, tip tracking, etc.
- **Device** - owns Driver + list of Capabilities, orchestrates lifecycle
- **BackendParams** - typed dataclasses replacing `**kwargs`

Key interfaces for Tecan EVO migration:
- `PIPBackend` (`pylabrobot/capabilities/liquid_handling/pip_backend.py`) - independent channel pipetting
- `GripperArmBackend` (`pylabrobot/arms/backend.py`) - plate handling arms
- Reference implementation: Hamilton STAR at `pylabrobot/hamilton/liquid_handlers/star/`

## Branch Strategy

| Branch | Base | Purpose |
|--------|------|---------|
| `air-liha-backend` | `main` | Legacy Air LiHa backend (WIP, may PR to main) |
| `v1b1-tecan-evo` | `origin/v1b1` | Native v1b1 EVO backend (syringe + Air LiHa + RoMa) |
| `keyser-combined` | `main` | Combined xArm + Multidrop for testing |
| `keyser-multidrop-testing` | `main` | Multidrop Combi backend |
| `keyser-xarm-testing` | `main` | xArm 6 backend |

### v1b1 Tecan EVO File Structure
```
pylabrobot/tecan/evo/
  driver.py              # TecanEVODriver(Driver) — USB I/O + command protocol
  pip_backend.py         # EVOPIPBackend(PIPBackend) — syringe LiHa
  air_pip_backend.py     # AirEVOPIPBackend(EVOPIPBackend) — Air LiHa
  roma_backend.py        # EVORoMaBackend(GripperArmBackend) — RoMa plate handling
  evo.py                 # TecanEVO(Resource, Device) — composite device
  params.py              # BackendParams dataclasses
  errors.py              # TecanError
  firmware/               # Extracted firmware command wrappers (LiHa, RoMa, EVOArm)
```

Legacy EVO stays at: `pylabrobot/legacy/liquid_handling/backends/tecan/`

## Air LiHa (ZaapMotion) Key Facts
- ZaapMotion controllers boot into bootloader mode after power cycle
- Must send `T2{0-7}X` (exit boot) + 33 motor config commands per tip before PIA
- Plunger conversion: 106.4 steps/uL (vs 3 for syringe), 213 speed factor (vs 6)
- Force mode: `SFR133120`+`SFP1` before plunger ops, `SFR3752`+`SDP1400` after
- Investigation details: `keyser-testing/AirLiHa_Investigation.md`

## Development
- Venv: `.venv/` in project root
- Install all deps: `pip install -e ".[serial,usb]"`
- Tests: `python -m pytest pylabrobot/bulk_dispensers/ -v`
- Lint: `ruff check`, Format: `ruff format`, Types: `mypy pylabrobot --check-untyped-defs`
- Abstract interfaces use microliters (float); backends convert to instrument-specific units
- Tecan Z coordinates: 0 = deck surface, z_range (~2100) = top/home
