# Tecan EVO Legacy — Firmware Feature Development Plan

**Status: MOSTLY COMPLETE** (Steps 1-4, 5c, 6b, 6c done; Steps 5a, 5b, 6a deferred — require LiquidHandlerBackend interface changes)
**Implemented:** 2026-03-30
**Branch:** `air-liha-backend` — commit `7b1c48a29`

## Context

Parallel to the v1b1 plan, this covers the same firmware feature gaps for the legacy `air-liha-backend` branch. The legacy architecture differs: arm firmware classes (LiHa, RoMa, EVOArm) live inside `EVO_backend.py`, the abstract interface is `LiquidHandlerBackend`, and vendor-specific params use `**backend_kwargs` rather than `BackendParams` dataclasses.

**Branch:** `air-liha-backend` (based on `main`)

### Key Architectural Differences from v1b1

| Aspect | v1b1 | Legacy |
|--------|------|--------|
| Firmware wrappers | `firmware/liha.py`, `firmware/roma.py`, `firmware/arm_base.py` | Inline in `EVO_backend.py` (lines 892-1393) |
| Backend base class | `PIPBackend` + `GripperArmBackend` | `LiquidHandlerBackend` (monolithic) |
| Operation types | `Aspiration`, `Dispense` | `SingleChannelAspiration`, `SingleChannelDispense` |
| Backend params | `BackendParams` dataclass | `**backend_kwargs` dict |
| Backend files | `pip_backend.py`, `air_pip_backend.py`, `roma_backend.py` | `EVO_backend.py`, `air_evo_backend.py` |
| Resource handling | Separate `GripperArmBackend` | Same backend: `pick_up_resource()`, `drop_resource()` |

---

## Step 1: Firmware Layer Cleanup — Wrap Raw Commands ✅

Same commands as v1b1, but added as methods on the inline arm classes.

### 1a. Add to `EVOArm` class (in `EVO_backend.py`)

| Method | Firmware Cmd | Purpose |
|--------|-------------|---------|
| `read_error_register(param=0) -> str` | `REE` | Read axis error state |
| `position_init_all()` | `PIA` | Initialize all axes |
| `position_init_bus()` | `PIB` | Initialize bus (MCA) |
| `set_bus_mode(mode)` | `BMX` | Set bus mode (2=normal) |
| `bus_module_action(p1,p2,p3)` | `BMA` | Halt all axes |

### 1b. Add to `LiHa` class (in `EVO_backend.py`)

| Method | Firmware Cmd | Purpose |
|--------|-------------|---------|
| `position_plunger_absolute(positions)` | `PPA` | Move plunger to absolute position |
| `set_disposable_tip_params(mode, z_discard, z_retract)` | `SDT` | DiTi discard parameters |

### 1c. Update backends to use new methods

- **`EVOBackend.setup()`** — replace raw `PIA`/`PIB`/`BMX` calls with `EVOArm` methods
- **`AirEVOBackend._is_initialized()`** — replace raw `REE0` with `arm.read_error_register(0)`
- **`AirEVOBackend.drop_tips()`** — replace raw `PPA`/`SDT` strings with `self.liha.position_plunger_absolute()` / `self.liha.set_disposable_tip_params()`
- **`EVOBackend` RoMa setup** — replace raw `REE`/`PIA`/`BMX` with `EVOArm` calls

### Files modified
- `pylabrobot/liquid_handling/backends/tecan/EVO_backend.py` (EVOArm, LiHa classes + setup methods)
- `pylabrobot/liquid_handling/backends/tecan/air_evo_backend.py`

---

## Step 2: New Firmware Commands ✅

### Add to `LiHa` class (in `EVO_backend.py`)

| Method | Firmware Cmd | Purpose |
|--------|-------------|---------|
| `read_plunger_positions() -> List[int]` | `RPP` | Query current plunger state |
| `read_z_after_liquid_detection() -> List[int]` | `RVZ` | Get detected liquid Z heights |
| `read_tip_status() -> List[bool]` | `RTS` | Which tips are mounted |
| `position_absolute_z_bulk(z)` | `PAZ` | Fast bulk Z move (vs slow `MAZ`) |

**Note:** `RTS` response format needs hardware validation.

### Files modified
- `pylabrobot/liquid_handling/backends/tecan/EVO_backend.py` (LiHa class)

---

## Step 3: ZaapMotion Firmware Abstraction ✅

### New class: `ZaapMotion` (in `air_evo_backend.py` or separate file)

Unlike v1b1 where we create `firmware/zaapmotion.py`, in the legacy branch this class can either:
- **(Option A)** Live in `air_evo_backend.py` alongside `AirEVOBackend` — simpler, keeps everything together
- **(Option B)** Create `pylabrobot/liquid_handling/backends/tecan/zaapmotion.py` — cleaner separation

**Recommended: Option A** (matches the legacy pattern of keeping firmware code in backend files)

| Method | Firmware Cmd | Purpose |
|--------|-------------|---------|
| `exit_boot_mode(tip)` | `T2{tip}X` | Exit bootloader |
| `read_firmware_version(tip) -> str` | `T2{tip}RFV` | Check boot/app mode |
| `read_config_status(tip)` | `T2{tip}RCS` | Check if configured |
| `set_force_ramp(tip, value)` | `T2{tip}SFR{v}` | Force ramp control |
| `set_force_mode(tip)` | `T2{tip}SFP1` | Enable force positioning |
| `set_default_position(tip, value)` | `T2{tip}SDP{v}` | Set idle position |
| `configure_motor(tip, cmd)` | `T2{tip}{cmd}` | Send config command |
| `set_sdo(param)` | `T23SDO{param}` | SDO object write |

### Refactor `air_evo_backend.py`
- Add `self.zaap: Optional[ZaapMotion]` attribute
- Refactor `_configure_zaapmotion()` to use `ZaapMotion` methods
- Refactor `_zaapmotion_force_on()` / `_zaapmotion_force_off()`
- Refactor SDO call in `setup()`

### Files modified
- `pylabrobot/liquid_handling/backends/tecan/air_evo_backend.py`

---

## Step 4: Mixing & Blow-out ✅

### 4a. No `params.py` needed — use `**backend_kwargs`

Legacy uses `**backend_kwargs` for vendor-specific params. Tecan-specific options will be passed as keyword arguments:

```python
# Usage example:
await lh.aspirate(
    wells, [50], use_channels=[0],
    mix=[Mix(20, 3, 100)],
    liquid_detection_proc=3,       # via **backend_kwargs
    liquid_detection_sense=0,      # via **backend_kwargs
)

await lh.dispense(
    wells, [50], use_channels=[0],
    blow_out_air_volume=[5.0],
    tip_touch=True,                # via **backend_kwargs
    tip_touch_offset_y=1.0,        # via **backend_kwargs
)
```

### 4b. Add mixing to `EVOBackend`

Add `_perform_mix(mix, use_channels)` to `EVOBackend`:
- Set valve to outlet (PVL=0), set plunger speed from `mix.flow_rate * SPEED_FACTOR`
- Loop `mix.repetitions` times: PPR(+steps), PPR(-steps) at current Z position
- Call after aspirate/dispense actions when `op.mix is not None`

In `EVOBackend.aspirate()` — add after trailing airgap:
```python
for i, (op, ch) in enumerate(zip(ops, use_channels)):
    if op.mix is not None:
        await self._perform_mix(op.mix, [ch for ch, o in zip(use_channels, ops) if o.mix])
        break
```

Same in `EVOBackend.dispense()`.

Override in `AirEVOBackend` to wrap with force mode:
```python
async def _perform_mix(self, mix, use_channels):
    await self._zaapmotion_force_on()
    await super()._perform_mix(mix, use_channels)
    await self._zaapmotion_force_off()
```

### 4c. Add blow-out to `EVOBackend`

Add `_perform_blow_out(ops, use_channels)`:
- For channels with `blow_out_air_volume > 0`: set valve outlet, push plunger negative
- Call at end of `dispense()`

Override in `AirEVOBackend` to wrap with force mode.

### Files modified
- `pylabrobot/liquid_handling/backends/tecan/EVO_backend.py` — add `_perform_mix`, `_perform_blow_out`
- `pylabrobot/liquid_handling/backends/tecan/air_evo_backend.py` — override with force mode

---

## Step 5: Tip Touch, LLD Config, Tip Presence — PARTIAL (5c done, 5a/5b deferred)

### 5a. Tip touch in `EVOBackend.dispense()` — DEFERRED
- Requires `**backend_kwargs` to be plumbed through `LiquidHandlerBackend.dispense()` abstract interface
- The base class does not currently pass kwargs to backend methods
- Deferred: would require changes to shared base classes in `pylabrobot/liquid_handling/backends/backend.py`

### 5b. LLD config in `EVOBackend.aspirate()` — DEFERRED
- Same issue: requires `**backend_kwargs` plumbing through `LiquidHandlerBackend.aspirate()`

### 5c. `request_tip_presence()` in `EVOBackend` ✅
- Implemented using `self.liha.read_tip_status()` (from Step 2)

### Files modified
- `pylabrobot/liquid_handling/backends/tecan/EVO_backend.py`

---

## Step 6: RoMa Enhancements — PARTIAL (6b/6c done, 6a deferred)

### 6a. Configurable speed profiles — DEFERRED
- Same `**backend_kwargs` plumbing issue as Step 5a/5b
- `pick_up_resource()` / `drop_resource()` don't receive kwargs from `LiquidHandler`

### 6b. Post-grip plate verification ✅
- After `grip_plate()`: query `report_g_param(0)`, log warning if gripper didn't close

### 6c. Configurable park position ✅
- Added `_roma_park_position` class attribute, used in `_park_roma()`

### Files modified
- `pylabrobot/liquid_handling/backends/tecan/EVO_backend.py`

---

## Step 7: System-Level — DEFERRED (see v1b1_migration_plan.md Phase 6d)

- Error recovery via `read_error_register()` + re-init
- Instrument status reporting
- Safety module monitoring

### Files modified
- `pylabrobot/liquid_handling/backends/tecan/EVO_backend.py`
- `pylabrobot/liquid_handling/backends/tecan/air_evo_backend.py`

---

## Implementation Order & Dependencies

```
Step 1 ──┬── Step 3 ── Step 4 ── Step 5
         │                         │
Step 2 ──┘                    Step 7
         │
         └── Step 6
```

Same as v1b1. Steps 1 and 2 can run in parallel. Step 6 only depends on Step 1.

---

## Key Difference: Fewer Files to Touch

Unlike v1b1 (8+ files), the legacy architecture concentrates changes in just 2 files:
- `EVO_backend.py` — firmware classes + EVOBackend + RoMa handling
- `air_evo_backend.py` — AirEVOBackend + ZaapMotion

This means less structural work but more careful editing within large files.

---

## Verification

### Unit Tests
- Update `EVO_tests.py`: verify firmware methods send correct command strings
- Update `air_evo_tests.py`: verify ZaapMotion refactor, force mode wrapping
- Add mix/blow-out tests: verify PPR command sequences
- All existing tests must pass unchanged

### Hardware Testing
- Same as v1b1: `RTS` format, `PAZ` units, mix timing, blow-out limits

### Smoke Test Sequence
```python
lh = LiquidHandler(backend=AirEVOBackend(), deck=deck)
await lh.setup()
await lh.pick_up_tips(tips)
await lh.aspirate(wells[:2], [50, 50], mix=[Mix(20, 3, 100), Mix(20, 3, 100)])
await lh.dispense(wells[2:4], [50, 50], blow_out_air_volume=[5.0, 5.0],
                  tip_touch=True, tip_touch_offset_y=1.0)
await lh.drop_tips(waste)
await lh.stop()
```

---

## Cross-Branch Sync Notes

When implementing, keep the firmware command sequences identical between branches:
- Same method names on LiHa/RoMa/ZaapMotion classes
- Same command strings and parameter ordering
- Same force mode wrapping pattern for Air LiHa
- Differences are only in how the backends consume them (BackendParams vs **backend_kwargs, separate files vs inline)
