# Tecan EVO v1b1 — Firmware Feature Development Plan

**Status: COMPLETE** (Steps 1-6 implemented, Step 7 deferred as future work)
**Implemented:** 2026-03-30
**Branch:** `v1b1-tecan-evo` — commit `355c67361`

## Context

The EVO firmware supports many commands that are either not wrapped in our firmware layer or not used by the backends at all. This plan closes those gaps systematically: first cleaning up raw `send_command` calls, then adding new firmware wrappers, abstracting ZaapMotion commands, and finally implementing higher-level features (mixing, blow-out, tip touch, LLD config, tip presence).

**Branch:** `v1b1-tecan-evo` (based on `origin/v1b1`)

---

## Step 1: Firmware Layer Cleanup — Wrap Raw Commands ✅

Replace raw `send_command` string calls with typed firmware wrapper methods.

### 1a. Add to `arm_base.py`

| Method | Firmware Cmd | Purpose |
|--------|-------------|---------|
| `read_error_register(param=0) -> str` | `REE` | Read axis error state (`@`=ok, `G`=not init) |
| `position_init_all()` | `PIA` | Initialize all axes |
| `position_init_bus()` | `PIB` | Initialize bus (MCA) |
| `set_bus_mode(mode)` | `BMX` | Set bus mode (2=normal) |
| `bus_module_action(p1,p2,p3)` | `BMA` | Halt all axes (0,0,0) |

### 1b. Add to `liha.py`

| Method | Firmware Cmd | Purpose |
|--------|-------------|---------|
| `position_plunger_absolute(positions)` | `PPA` | Move plunger to absolute position |
| `set_disposable_tip_params(mode, z_discard, z_retract)` | `SDT` | DiTi discard parameters |

### 1c. Update backends to use new methods

- **`pip_backend.py` `_setup_arm()`** — replace raw `PIB`/`PIA`/`BMX` with `EVOArm` wrapper calls
- **`air_pip_backend.py` `_is_initialized()`** — replace raw `REE0` with `arm.read_error_register(0)`
- **`air_pip_backend.py` `drop_tips()`** — replace raw `PPA`/`SDT` strings with `self.liha.position_plunger_absolute()` / `self.liha.set_disposable_tip_params()`
- **`roma_backend.py` `_on_setup()`** — replace raw `REE`/`PIA`/`BMX` with `EVOArm` wrapper calls
- **`roma_backend.py` `halt()`** — replace raw `BMA` with `self.roma.bus_module_action(0,0,0)`

### Files modified
- `pylabrobot/tecan/evo/firmware/arm_base.py`
- `pylabrobot/tecan/evo/firmware/liha.py`
- `pylabrobot/tecan/evo/pip_backend.py`
- `pylabrobot/tecan/evo/air_pip_backend.py`
- `pylabrobot/tecan/evo/roma_backend.py`

---

## Step 2: New Firmware Commands ✅

Add wrappers for commands we haven't used yet.

### Add to `liha.py`

| Method | Firmware Cmd | Purpose |
|--------|-------------|---------|
| `read_plunger_positions() -> List[int]` | `RPP` | Query current plunger state |
| `read_z_after_liquid_detection() -> List[int]` | `RVZ` | Get detected liquid Z heights |
| `read_tip_status() -> List[bool]` | `RTS` | Which tips are mounted |
| `position_absolute_z_bulk(z) ` | `PAZ` | Fast bulk Z move (vs slow `MAZ`) |

**Note:** `RTS` response format needs hardware validation — implement defensively.

### Files modified
- `pylabrobot/tecan/evo/firmware/liha.py`

---

## Step 3: ZaapMotion Firmware Abstraction ✅

Create `firmware/zaapmotion.py` to replace ~50 raw `T2{tip}` commands in `air_pip_backend.py`.

### New class: `ZaapMotion`

| Method | Firmware Cmd | Purpose |
|--------|-------------|---------|
| `exit_boot_mode(tip)` | `T2{tip}X` | Exit bootloader |
| `read_firmware_version(tip) -> str` | `T2{tip}RFV` | Check boot/app mode |
| `read_config_status(tip)` | `T2{tip}RCS` | Check if configured |
| `set_force_ramp(tip, value)` | `T2{tip}SFR{v}` | Force ramp (133120 active, 3752 idle) |
| `set_force_mode(tip)` | `T2{tip}SFP1` | Enable force positioning |
| `set_default_position(tip, value)` | `T2{tip}SDP{v}` | Set idle position (1400 default) |
| `configure_motor(tip, cmd)` | `T2{tip}{cmd}` | Send one of 33 config commands |
| `set_sdo(param)` | `T23SDO{param}` | SDO object write |

### Refactor `air_pip_backend.py`
- Add `self.zaap: Optional[ZaapMotion]` attribute
- Refactor `_configure_zaapmotion()` to use `ZaapMotion` methods
- Refactor `_zaapmotion_force_on()` / `_zaapmotion_force_off()` — preserve exact command ordering (all SFR first, then all SFP/SDP)
- Refactor SDO call in `_on_setup()`

### Files created
- `pylabrobot/tecan/evo/firmware/zaapmotion.py`

### Files modified
- `pylabrobot/tecan/evo/firmware/__init__.py` — add `ZaapMotion` export
- `pylabrobot/tecan/evo/air_pip_backend.py` — refactor to use `ZaapMotion`

---

## Step 4: Mixing & Blow-out ✅

### 4a. Create `params.py` with EVO-specific BackendParams

```python
@dataclass(frozen=True)
class TecanPIPParams(BackendParams):
    liquid_detection_proc: Optional[int] = None   # SDM proc (default 7)
    liquid_detection_sense: Optional[int] = None   # SDM sense (default 1)
    tip_touch: bool = False
    tip_touch_offset_y: float = 1.0   # mm toward wall
```

```python
@dataclass(frozen=True)
class TecanRoMaParams(BackendParams):
    speed_x: Optional[int] = None    # 1/10 mm/s
    speed_y: Optional[int] = None
    speed_z: Optional[int] = None
    speed_r: Optional[int] = None    # 1/10 deg/s
    accel_y: Optional[int] = None    # 1/10 mm/s^2
    accel_r: Optional[int] = None
```

### 4b. Add mixing to `pip_backend.py`

Add `_perform_mix(mix, use_channels)`:
- Set valve to outlet (PVL=0), set plunger speed from `mix.flow_rate * SPEED_FACTOR`
- Loop `mix.repetitions` times: PPR(+steps), PPR(-steps) at current Z position
- Call after aspirate/dispense actions when `op.mix is not None`

Override in `air_pip_backend.py` to wrap with force mode.

### 4c. Add blow-out to `pip_backend.py`

Add `_perform_blow_out(ops, use_channels)`:
- For channels with `blow_out_air_volume > 0`: set valve outlet, push plunger by `-blow_out * STEPS_PER_UL`
- Call at end of `dispense()`

Override in `air_pip_backend.py` to wrap with force mode.

### Files created
- `pylabrobot/tecan/evo/params.py`

### Files modified
- `pylabrobot/tecan/evo/pip_backend.py` — add `_perform_mix`, `_perform_blow_out`
- `pylabrobot/tecan/evo/air_pip_backend.py` — override with force mode wrapping

---

## Step 5: Tip Touch, LLD Config, Tip Presence ✅

### 5a. Tip touch in `pip_backend.py` `dispense()`
- Check `backend_params` for `TecanPIPParams(tip_touch=True)`
- After dispense: brief PAA Y-offset move, then PAA back

### 5b. LLD config in `pip_backend.py` `aspirate()`
- If `TecanPIPParams.liquid_detection_proc` or `liquid_detection_sense` set, use those instead of liquid class defaults for `SDM` call

### 5c. `request_tip_presence()` in `pip_backend.py`
- Implement optional PIPBackend method using `self.liha.read_tip_status()` (from Step 2)

### Files modified
- `pylabrobot/tecan/evo/pip_backend.py`

---

## Step 6: RoMa Enhancements ✅ (partial — drop_at_carrier speeds deferred)

### 6a. Configurable speed profiles
- In `pick_up_from_carrier()` and `drop_at_carrier()`: check `backend_params` for `TecanRoMaParams`
- Apply speed overrides to SFX/SFY/SFZ/SFR calls, falling back to current hardcoded defaults

### 6b. Post-grip plate verification
- After `grip_plate()`: query `report_g_param(0)`, log warning if gripper didn't close

### 6c. Configurable park position
- Add `park_position` tuple to `__init__`, use in `park()` instead of hardcoded values

### Files modified
- `pylabrobot/tecan/evo/roma_backend.py`

---

## Step 7: System-Level — DEFERRED (see v1b1_migration_plan.md Phase 6d)

- Error recovery via `read_error_register()` + re-init
- Instrument status aggregation (`RPP` + `RTS` + `REE` → status dict)
- Safety module monitoring during operations

### Files modified
- `pylabrobot/tecan/evo/pip_backend.py`
- `pylabrobot/tecan/evo/evo.py`

---

## Implementation Order & Dependencies

```
Step 1 ──┬── Step 3 ── Step 4 ── Step 5
         │                         │
Step 2 ──┘                    Step 7
         │
         └── Step 6
```

Steps 1 and 2 can run in parallel. Step 6 only depends on Step 1.

---

## Verification

### Unit Tests
- Existing tests must pass after each step (commands sent are identical, just routed through wrappers)
- New tests for each firmware method: mock `send_command`, verify command string and params
- Mix tests: verify N pairs of PPR commands sent
- Blow-out tests: verify extra negative PPR after dispense MTR
- Tip presence: mock RTS response, verify boolean list

### Hardware Testing
- `RTS` response format needs validation on real EVO
- `PAZ` (fast bulk Z) — verify units match `MAZ`
- Mix timing: verify valve/plunger sequencing doesn't cause bubbles
- Blow-out plunger limits: verify plunger doesn't exceed range at 0 position

### Smoke Test Sequence
```python
evo = TecanEVO(deck=deck, diti_count=8, air_liha=True)
await evo.setup()
await evo.pip.pick_up_tips(tips, use_channels=[0,1])
await evo.pip.aspirate(wells[:2], [50, 50], mix=[Mix(20, 3, 100), Mix(20, 3, 100)])
await evo.pip.dispense(wells[2:4], [50, 50], blow_out_air_volume=[5.0, 5.0],
                       backend_params=TecanPIPParams(tip_touch=True))
await evo.pip.drop_tips(waste, use_channels=[0,1])
await evo.stop()
```
