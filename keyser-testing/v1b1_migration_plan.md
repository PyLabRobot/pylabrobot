# Plan: Migrate Tecan EVO Backend to v1b1 Architecture

## Context

The pylabrobot v1b1 branch introduces a new **Driver + Capability** architecture replacing the legacy monolithic `MachineBackend` pattern. Hamilton backends have already been migrated. The Tecan EVO backend (both syringe LiHa and Air LiHa) currently exists only in legacy form. We need to:

1. Keep `air-liha-backend` branch as legacy (potential merge to main)
2. Create `v1b1-tecan-evo` branch off `origin/v1b1` on our fork
3. Build a native v1b1 Tecan EVO implementation
4. Support both syringe and Air LiHa variants

## v1b1 Architecture Summary

```
Device (frontend)
  ├── _driver: Driver (USB I/O, connection lifecycle)
  └── _capabilities: [Capability, ...]
        └── backend: CapabilityBackend (protocol translation)
```

- `Driver` → owns I/O, `setup()/stop()` open/close connection
- `CapabilityBackend` → translates operations into driver commands, has `_on_setup()/_on_stop()`
- `Capability` → user-facing API with tip tracking, validation
- `Device` → orchestrates lifecycle: driver.setup() → cap._on_setup() in order
- `BackendParams` dataclasses replace `**kwargs`
- Legacy code lives in `pylabrobot/legacy/`, wrapped by adapters

## File Structure

```
pylabrobot/tecan/                        # NEW vendor namespace
  __init__.py
  evo/
    __init__.py                          # exports TecanEVO, TecanEVODriver, etc.
    driver.py                            # TecanEVODriver(Driver)
    pip_backend.py                       # EVOPIPBackend(PIPBackend) — syringe LiHa
    air_pip_backend.py                   # AirEVOPIPBackend(EVOPIPBackend)
    roma_backend.py                      # EVORoMaBackend(GripperArmBackend)
    evo.py                               # TecanEVO(Resource, Device)
    params.py                            # BackendParams dataclasses
    errors.py                            # TecanError (moved from legacy)
    firmware/
      __init__.py
      arm_base.py                        # EVOArm base (collision cache)
      liha.py                            # LiHa firmware commands
      roma.py                            # RoMa firmware commands
    tests/
      driver_tests.py
      pip_backend_tests.py
      air_pip_backend_tests.py
      roma_backend_tests.py
      evo_tests.py
```

**Unchanged files** (no edits needed):
- `pylabrobot/resources/tecan/` — plates, tip racks, carriers, decks
- `pylabrobot/io/usb.py` — USB I/O class
- `pylabrobot/device.py` — Driver/Device base classes
- `pylabrobot/capabilities/` — PIPBackend, GripperArmBackend interfaces
- `pylabrobot/legacy/liquid_handling/backends/tecan/` — legacy backend stays

**Liquid classes**: Import `TecanLiquidClass` and `get_liquid_class` from `pylabrobot.legacy.liquid_handling.liquid_classes.tecan`. No duplication — this works until liquid classes are refactored upstream.

## Class Hierarchy

### TecanEVODriver (extracted from TecanLiquidHandler)
```python
class TecanEVODriver(Driver):
    def __init__(self, packet_read_timeout=12, read_timeout=60, write_timeout=60)
    async def setup()          # opens USB
    async def stop()           # closes USB
    async def send_command(module, command, params=None, ...) -> dict
    def _assemble_command(module, command, params) -> str
    def parse_response(resp) -> dict
```
Source: extract from `TecanLiquidHandler` (lines 56-174 of EVO_backend.py)

### EVOPIPBackend (syringe LiHa)
```python
class EVOPIPBackend(PIPBackend):
    def __init__(self, driver: TecanEVODriver, deck: Resource, diti_count=0)
    STEPS_PER_UL = 3
    SPEED_FACTOR = 6

    async def _on_setup()      # PIA, init plungers, query ranges
    async def pick_up_tips(ops, use_channels, backend_params=None)
    async def drop_tips(ops, use_channels, backend_params=None)
    async def aspirate(ops, use_channels, backend_params=None)
    async def dispense(ops, use_channels, backend_params=None)
    def can_pick_up_tip(channel_idx, tip) -> bool
```
Source: port from `EVOBackend` methods, adapting `SingleChannelAspiration` → `Aspiration` etc.

### AirEVOPIPBackend (Air LiHa / ZaapMotion)
```python
class AirEVOPIPBackend(EVOPIPBackend):
    STEPS_PER_UL = 106.4
    SPEED_FACTOR = 213.0

    async def _on_setup()      # ZaapMotion config + safety + super()._on_setup()
    async def aspirate(...)     # wraps with force mode
    async def dispense(...)     # wraps with force mode
```
Source: port from `AirEVOBackend`

### EVORoMaBackend (plate handling)
```python
class EVORoMaBackend(GripperArmBackend):
    def __init__(self, driver: TecanEVODriver, deck: Resource)

    async def _on_setup()                    # PIA for RoMa, park
    async def pick_up_at_location(location, resource_width, backend_params=None)
    async def drop_at_location(location, resource_width, backend_params=None)
    async def move_to_location(location, backend_params=None)
    async def halt(backend_params=None)
    async def park(backend_params=None)
    async def open_gripper(gripper_width, backend_params=None)
    async def close_gripper(gripper_width, backend_params=None)
    async def is_gripper_closed(backend_params=None) -> bool
    async def get_gripper_location(backend_params=None) -> GripperLocation
```
Source: port from `EVOBackend.pick_up_resource/drop_resource` + `RoMa` class

### TecanEVO (composite device)
```python
class TecanEVO(Resource, Device):
    def __init__(self, name, deck, diti_count=0, air_liha=False, has_roma=True, ...)
    # Creates driver + capabilities based on config
    # self._capabilities = [arm, pip] (arm first for init ordering)
```

## Key Design Decisions

### Syringe vs Air LiHa → Subclass
`AirEVOPIPBackend(EVOPIPBackend)` with overridden constants and `_on_setup`. Same pattern as current `AirEVOBackend(EVOBackend)`. Config flag on `TecanEVO(air_liha=True)` selects the backend class.

### Arm Init Ordering
RoMa must park before LiHa initializes (clears X-axis path). Set `self._capabilities = [arm, pip]` so arm's `_on_setup()` runs first.

### Firmware Wrappers → Shared via Driver
`LiHa` and `RoMa` classes are extracted to `firmware/` and accept a duck-typed interface (anything with `send_command`). Both PIP backend and RoMa backend instantiate their own firmware wrapper with a reference to the shared driver. The collision cache (`EVOArm._pos_cache`) remains a class variable.

### Deck Reference
PIP and RoMa backends need the deck for coordinate calculations. Passed via constructor, stored as `self._deck`. The deck is also a child resource of `TecanEVO`.

### Operation Type Mapping
| Legacy | v1b1 |
|--------|------|
| `SingleChannelAspiration` | `Aspiration` |
| `SingleChannelDispense` | `Dispense` |
| `Pickup` | `Pickup` (same name, different module) |
| `Drop` | `TipDrop` |

Fields are nearly identical. The v1b1 types add `liquid_height`, `blow_out_air_volume`, `mix` — ignored by Tecan backend (uses liquid class values instead).

## Implementation Phases

### Phase 0: Documentation & Project Setup ✅ COMPLETE
- [x] Updated CLAUDE.md with v1b1 architecture, branch strategy, Air LiHa facts
- [x] Created `keyser-testing/v1b1_migration_plan.md` (this file)
- [x] Created branch `v1b1-tecan-evo` off `origin/v1b1` on fork
- [x] Verified v1b1 imports: Driver, Device, PIPBackend, GripperArmBackend
- [x] Verified legacy imports: EVOBackend, TecanLiquidClass
- [x] Verified Tecan resources unchanged between main and v1b1

### Phase 1: Firmware Extraction ✅ COMPLETE
- [x] Created `pylabrobot/tecan/evo/` directory structure
- [x] Extracted `EVOArm`, `LiHa`, `RoMa` into `firmware/` with `CommandInterface` Protocol
- [x] Moved `errors.py` (TecanError, error_code_to_exception)
- [x] Renamed `self.backend` → `self.interface` in firmware wrappers
- [x] Renamed `LiHa._drop_disposable_tip` → `drop_disposable_tip` (no leading _)
- [x] Verified imports, no circular deps

### Phase 2: Driver ✅ COMPLETE
- [x] Created `TecanEVODriver(Driver)` from `TecanLiquidHandler`
- [x] USB connection, command assembly, response parsing, SET caching
- [x] Driver satisfies `CommandInterface` Protocol
- [x] 16 unit tests (command assembly, response parsing, caching, serialization)

### Phase 3: Syringe PIP Backend ✅ COMPLETE
- [x] Created `EVOPIPBackend(PIPBackend)` with all operations
- [x] Ported `_liha_positions`, `_aspirate_action`, `_dispense_action`, `_aspirate_airgap`, `_liquid_detection`
- [x] Ported `pick_up_tips`, `drop_tips`, `aspirate`, `dispense`
- [x] Adapted from legacy types (SingleChannelAspiration → Aspiration, Drop → TipDrop)
- [x] Liquid class lookup imported from `pylabrobot.legacy`
- [x] Y-spacing fix: uses `plate.item_dy` (well pitch) not `well.size_y`

### Phase 4: Air PIP Backend ✅ COMPLETE
- [x] Created `AirEVOPIPBackend(EVOPIPBackend)` with ZaapMotion support
- [x] ZaapMotion boot exit (`T2xX`) and motor config (33 commands × 8 tips)
- [x] Safety module (SPN/SPS3)
- [x] T23SDO11,1 before PIA
- [x] Init-skip via REE0 check (`_is_initialized` / `_setup_quick`)
- [x] Conversion factors: 106.4 steps/µL, 213 speed factor
- [x] Force mode wrapping (SFR/SFP/SDP) around all plunger ops
- [x] Direct z_start from tip rack for AGT
- [x] SDT + PPA0 before tip discard

### Phase 5: RoMa Backend ✅ COMPLETE
- [x] Created `EVORoMaBackend(GripperArmBackend)`
- [x] Ported `_roma_positions`, vector coordinate table, gripper commands
- [x] `pick_up_from_carrier` / `drop_at_carrier` with carrier-aware coordinates
- [x] `halt`, `park`, `move_to_location`, `get_gripper_location`
- [x] `open_gripper`, `close_gripper`, `is_gripper_closed`

### Phase 6: Device Composition ✅ COMPLETE
- [x] Created `TecanEVO(Resource, Device)` composing Driver + PIP + GripperArm
- [x] Constructor flags: `air_liha=True`, `has_roma=True`
- [x] Capability ordering: arm first (must park before LiHa X-init)
- [x] Deck assigned as child resource
- [x] Exports from `pylabrobot.tecan.evo.__init__`
- [x] Verified construction for all config combinations

### Phase 6b: Tooling ✅ COMPLETE
- [x] Created `keyser-testing/hardware_testing_checklist.md` (8 test scenarios)
- [x] Created `keyser-testing/test_v1b1_init.py` (init test script)
- [x] Created `keyser-testing/test_v1b1_pipette.py` (pipetting test script)
- [x] Created `keyser-testing/jog_and_teach.py` (CLI jog/teach/labware editor)
- [x] Created `keyser-testing/jog_ui.py` (web-based jog UI with keyboard controls)
- [x] Ported `keyser-testing/labware_library.py` from air-liha-backend
- [x] Added ZaapDiTi 50µL liquid class entries to legacy liquid classes

### Phase 6c: Firmware Feature Enhancements ✅ COMPLETE
- [x] Wrapped raw `send_command` calls (REE, PIA, PIB, BMX, BMA, PPA, SDT) in typed firmware methods
- [x] Added new firmware commands: RPP, RVZ, RTS, PAZ
- [x] Created `firmware/zaapmotion.py` — ZaapMotion class replacing ~50 raw T2{tip} string commands
- [x] Created `params.py` — `TecanPIPParams` (tip touch, LLD config) and `TecanRoMaParams` (speed profiles)
- [x] Implemented mixing (`_perform_mix`) and blow-out (`_perform_blow_out`) with Air LiHa force mode overrides
- [x] Implemented `request_tip_presence()` via RTS firmware command
- [x] Added tip touch (via `TecanPIPParams`) and LLD config override in aspirate
- [x] Added configurable RoMa speed profiles, post-grip plate verification, configurable park position
- [x] See `completed-plans/v1b1_firmware_feature_plan.md` for full plan

### Phase 6d: System-Level Features — FUTURE
These items are deferred until after hardware validation (Phase 7). They build on the firmware wrappers from Phase 6c:
- [ ] **Error recovery**: Use `read_error_register()` to detect axis errors, attempt re-init (PIA + BMX) for recoverable states (G=not initialized), surface unrecoverable errors to the user
- [ ] **Instrument status API**: Aggregate `read_error_register()` + `read_tip_status()` + `read_plunger_positions()` into a status dict on `TecanEVO`, useful for dashboard/monitoring
- [ ] **Safety module monitoring**: Periodic `SPN`/`SPS` checks during long operations (currently only sent during Air LiHa setup)
- [ ] **RoMa drop_at_carrier speed profiles**: Wire `TecanRoMaParams` through `drop_at_carrier()` (currently only `pick_up_from_carrier` uses configurable speeds)
- [ ] **Legacy branch kwargs wiring**: On `air-liha-backend`, tip touch and LLD config require `**backend_kwargs` to be plumbed through the `LiquidHandlerBackend` abstract interface — deferred as it touches shared base classes

### Phase 7: Hardware Testing — TODO
- [ ] Test init from cold boot (ZaapMotion config + PIA)
- [ ] Test init-skip on warm reconnect
- [ ] Calibrate Z positions using jog tool:
  - [ ] Tip rack z_start / z_max
  - [ ] Source plate z_start / z_dispense / z_max
  - [ ] Dest plate z_start / z_dispense / z_max
- [ ] Test tip pickup (8 channels)
- [ ] Test aspirate (25µL water)
- [ ] Test dispense (25µL water)
- [ ] Test tip drop
- [ ] Test full cycle (pickup → aspirate → dispense → drop)
- [ ] Test RoMa plate handling (if applicable)
- [ ] Investigate and fix X offset (currently hardcoded +60 on air-liha-backend)

### Phase 8: PR Cleanup — TODO
- [ ] Remove debug print statements from backends
- [ ] Add remaining unit tests (pip_backend_tests, air_pip_backend_tests, roma_backend_tests)
- [ ] Add docstrings where missing
- [ ] Run full lint/format/typecheck suite
- [ ] Verify legacy backend still works unmodified
- [ ] Create clean PR branches (strip keyser-testing/, claude.md, .claude/)
- [ ] See `keyser-testing/PR_cleanup_checklist.md` for detailed steps
- [ ] Submit PR to upstream v1b1 branch

## BackendParams Dataclasses

```python
@dataclass
class EVOAspirateParams(BackendParams):
    liquid: Liquid = Liquid.WATER
    lld_mode: Optional[int] = None

@dataclass
class EVODispenseParams(BackendParams):
    liquid: Liquid = Liquid.WATER

@dataclass
class EVOPickUpTipParams(BackendParams):
    z_start_override: Optional[int] = None
    z_search_override: Optional[int] = None

@dataclass
class EVODropTipParams(BackendParams):
    discard_height: int = 0

@dataclass
class EVORoMaParams(BackendParams):
    speed_x: int = 10000
    grip_speed: int = 100
    grip_pwm: int = 75
```

## Testing Strategy

### Unit Tests (mocked driver)
- `driver_tests.py` — command assembly, response parsing, error codes
- `pip_backend_tests.py` — verify firmware command sequences for each operation
- `air_pip_backend_tests.py` — verify ZaapMotion config, force mode wrapping
- `roma_backend_tests.py` — verify RoMa command sequences
- `evo_tests.py` — lifecycle, capability ordering

### Hardware Tests
- `test_air_evo_init.py` — ZaapMotion boot exit + PIA (already exists)
- `test_air_evo_pipette.py` — full pipetting cycle (already exists, needs v1b1 adaptation)
- `test_evo_roma.py` — plate pickup and placement (new)

### Regression
- Verify legacy backend still works via `pylabrobot/legacy/` imports
- Run existing `EVO_tests.py` in legacy unchanged

## CLAUDE.md Updates

Add to CLAUDE.md:
```
### v1b1 Architecture
- Branch: `v1b1-tecan-evo` (off origin/v1b1)
- Pattern: Driver + CapabilityBackend (see pylabrobot/device.py)
- Tecan EVO native: `pylabrobot/tecan/evo/`
- Legacy EVO: `pylabrobot/legacy/liquid_handling/backends/tecan/`
- Reference: Hamilton STAR at `pylabrobot/hamilton/liquid_handlers/star/`
- Key interfaces: PIPBackend, GripperArmBackend, BackendParams
```

## Project Checklist

- [x] Phase 0: Update CLAUDE.md and create migration docs
- [x] Phase 0: Create branch `v1b1-tecan-evo` off `origin/v1b1`
- [x] Phase 0: Verify v1b1 branch builds
- [x] Phase 1: Firmware extraction (LiHa, RoMa, EVOArm)
- [x] Phase 2: TecanEVODriver + 16 unit tests
- [x] Phase 3: EVOPIPBackend (syringe)
- [x] Phase 4: AirEVOPIPBackend (Air LiHa)
- [x] Phase 5: EVORoMaBackend
- [x] Phase 6: TecanEVO device composition
- [x] Phase 6b: Test scripts, jog tools, labware library
- [ ] Phase 7: Hardware testing (init, tips, aspirate, dispense)
- [ ] Phase 8: PR cleanup (see `PR_cleanup_checklist.md`)

## Encapsulation Verification

Both branches verified as fully encapsulated (2026-03-28):

**`air-liha-backend` → PR to `main`:**
- Only modifies: `backends/tecan/__init__.py` (1 import), `liquid_classes/tecan.py` (8 entries appended)
- All other files are NEW (no existing code modified)
- No changes to: machines/, resources/, io/, base classes

**`v1b1-tecan-evo` → PR to `v1b1`:**
- Only modifies: `legacy/.../liquid_classes/tecan.py` (8 entries appended), `claude.md`
- All other files are NEW under `pylabrobot/tecan/` (entirely new vendor namespace)
- No changes to: device.py, capabilities/, resources/, io/, arms/, base classes

PR cleanup procedure documented in `keyser-testing/PR_cleanup_checklist.md`.

## Estimated Effort
- Phase 0: ~0.5 session ✅
- Phases 1-2: ~1 session ✅
- Phases 3-4: ~1 session ✅ (faster than estimated)
- Phases 5-6: ~0.5 session ✅ (faster than estimated)
- Phase 6b: ~0.5 session ✅ (tooling)
- Phase 7: ~1 session (hardware testing + Z calibration) — **NEXT**
- Phase 8: ~0.5 session (cleanup + PR)
- **Total: ~5 sessions, ~3.5 complete**
