# Tecan EVO Hardware Testing Checklist

## Pre-Test Setup

### Equipment Required
- [x] EVO 150 powered on
- [x] USB cable connected to pylabrobot PC
- [x] EVOware PC disconnected from USB (only one client at a time)
- [x] DiTi 50uL SBS tips loaded (position 3 on MP_3Pos at rail 16)
- [x] Eppendorf 96-well plate with water in column 1 (position 1)
- [x] Empty Eppendorf 96-well plate (position 2)
- [x] `.venv` activated, `pip install -e ".[usb]"` done

### Software
- [x] `liquid-handling-testing` branch checked out (off `v1b1-tecan-evo`)
- [x] `keyser-testing/labware_library.py` has corrected carrier + plate definitions

---

## Test 1: Initialization (Cold Boot) PASSED

**Script:** `keyser-testing/test_v1b1_init.py`
**Date:** 2026-03-30

| Step | Expected | Pass? |
|------|----------|-------|
| USB connection | "USB connected" | [x] ~3s |
| ZaapMotion boot exit | All 8 tips XP2000/ZMA | [x] |
| ZaapMotion motor config | 33 commands x 8 tips OK | [x] |
| Safety module (SPN/SPS3) | OK | [x] |
| PIA (all axes) | REE0 = `@@@@@@@@@@@` | [x] |
| RoMa init + park | OK (~56s first time) | [x] |
| LiHa range queries | num_channels=8, z_range~2100 | [x] |
| Plunger init | PID, PVL, PPR sequence completes | [x] |

---

## Test 2: Initialization (Warm Reconnect) PASSED

**Date:** 2026-03-30

| Step | Expected | Pass? |
|------|----------|-------|
| REE0 check | Not "A" or "G" -> skip full init | [x] |
| RoMa REE check | `@@@@@` -> skip RoMa PIA | [x] |
| Quick setup | Channel count + ranges loaded fast | [x] |
| Total time | **3.4 seconds** (vs ~60s for full init) | [x] |

### Notes
- Fixed RoMa warm reconnect: now checks REE before PIA (was 56s, now 0.0s)
- Fixed USB buffer drain: uses 1s packet timeout instead of 30s
- Fixed `_is_initialized`: REE0 response cast to str (was int for some states)

---

## Test 3: Tip Pickup PASSED

**Script:** `keyser-testing/test_v1b1_pipette.py`
**Date:** 2026-04-02

| Step | Expected | Pass? |
|------|----------|-------|
| X/Y positioning | Channels aligned over tip column | [x] |
| Z approach | Channels descend to tips | [x] AGT executes |
| Tip engagement | Force feedback engages all 8 tips | [x] |
| Z retract | Channels lift with tips mounted | [x] |
| RTS check | Tip status = 255 (all mounted) | [x] |

### Fixes Applied
- **Carrier site locations corrected**: X 5.5->11.0, Y +0.9, Z +1.2 (from EVOware measurements)
- **Plate dx corrected**: 6.76->11.64 (SBS/SLAS P1=14.38mm standard)
- **Per-labware x_offset/y_offset removed** — no longer needed
- Residuals within 0.7mm (manual teaching precision)

---

## Test 4: Tip Drop PASSED

**Date:** 2026-04-02

| Step | Expected | Pass? |
|------|----------|-------|
| Move to tip box column 1 | Channels aligned over tips | [x] |
| Plunger empty | PPA0 completes | [x] |
| SDT + ADT | Tips released | [x] |
| RTS check | Tip status = 0 (none mounted) | [x] |
| Z retract | Channels raised to Z max | [x] |

---

## Test 5: Aspirate — PASSED

**Date:** 2026-04-07

### Fixes Applied
- `set_search_z_start` (STL) was sending syringe-transformed Z coordinates (~2381)
  instead of absolute Tecan Z. Fixed to use `z_asp` / `z_asp_max` computed with
  plate z_start/z_max + tip_extension. Error was "Invalid operand" (code 3).
- Replaced hardcoded nesting depth (50) with `tip.fitting_depth` for accuracy.
- 50uL tip: total_tip_length=58.0mm, fitting_depth=4.9mm, tip_ext=531 units (53.1mm).

| Step | Expected | Pass? |
|------|----------|-------|
| Move to source plate | Channels over column 1 | [x] |
| Leading airgap | Force mode + plunger move | [x] |
| LLD (if enabled) | Liquid detected | [x] |
| Aspirate 25uL | Tracking move completes | [x] |
| Trailing airgap | Force mode + plunger move | [x] |
| Z retract | Channels lift to z_start | [x] |

---

## Test 6: Dispense — PASSED

**Date:** 2026-04-07

### Fixes Applied
- Updated z_dispense from 200 to 99 (taught well bottom) for small volume dispensing.
- Dispense Z target = z_dispense(99) + tip_ext(531) = 630 — tips at well bottom.

| Step | Expected | Pass? |
|------|----------|-------|
| Move to dest plate | Channels over column 1 | [x] |
| Dispense 25uL | Tracking move completes | [x] |
| Z retract | Channels lift | [x] |

---

## Test 7: Full Cycle — NOT STARTED

| Step | Pass? | Notes |
|------|-------|-------|
| Init | [x] | Cold + warm both work |
| Tip pickup | [x] | Working with corrected coordinates |
| Aspirate | [x] | STL fix + fitting_depth validated |
| Dispense | [x] | z_dispense=99 (well bottom) |
| Tip drop | [x] | Working |
| Clean stop | [x] | |

---

## Test 8: RoMa Plate Handling — NOT STARTED

RoMa init works (cold + warm). Plate handling not yet tested.

---

## Z-Calibration Procedure

Use `keyser-testing/jog_ui.py` (web UI at http://localhost:5050):

1. **Tip rack z_start**: Jog to just above tip tops, teach `z_start` for `tips`
2. **Tip rack z_max**: Jog to bottom of tip search range, teach `z_max` for `tips`
3. **Plate z_start**: Jog bare channel to plate top surface, teach `z_start` for plate
4. **Plate z_dispense**: Jog to dispense height, teach `z_dispense` for plate
5. **Plate z_max**: Jog to maximum depth, teach `z_max` for plate

**With tips mounted**: Select the tip type in the "Mounted" dropdown before teaching.
The UI automatically subtracts tip extension to store bare-channel Z values.

Taught positions saved in `keyser-testing/taught_positions.json`.
Labware edits saved in `keyser-testing/labware_edits.json`.

### Important Z Notes
- Tecan Z coordinate system: 0 = deck surface, z_range (~2100) = top/home
- Taught positions are measured with **bare channels** (no tip mounted) unless
  the tip type dropdown is set in the jog UI
- For aspirate/dispense with tips: Z target = plate.z_start + tip_extension
  - tip_extension = total_tip_length * 10 - nesting_depth (50 units / 5mm)
- AGT z_start/z_max are used directly (no tip extension needed — tips not yet mounted)

---

## Tools Available

| Tool | Purpose |
|------|---------|
| `keyser-testing/jog_ui.py` | Web UI for jogging, teaching, labware inspection |
| `keyser-testing/jog_and_teach.py` | CLI jog/teach tool |
| `keyser-testing/load_tips.py` | Pick up tips from selected column (1-12) |
| `keyser-testing/tips_off.py` | Emergency tip removal (raw firmware commands) |
| `keyser-testing/tips_off_tipbox.py` | Drop tips back into tip box column 1 |
| `keyser-testing/test_v1b1_init.py` | Init test with timing |
| `keyser-testing/test_v1b1_pipette.py` | Full pipetting cycle test |

---

## Coordinate Fix Summary (2026-04-02)

Root cause of systematic X/Y offset identified and fixed:

1. **Plate well dx was wrong**: 6.76mm placed A1 center at 9.50mm from plate edge.
   SBS/SLAS 4-2004 standard P1=14.38mm. Fixed dx to 11.64mm.

2. **Carrier site X offset was wrong**: Upstream MP_3Pos had site X=5.5mm.
   EVOware carrier editor shows 11.0mm. Fixed in `MP_3Pos_Corrected`.

3. **Per-labware x_offset/y_offset hacks removed** — no longer needed.

| Parameter | Upstream | Corrected |
|-----------|----------|-----------|
| Plate dx | 6.76 | 11.64 |
| Site X | 5.5 | 11.0 |
| Site Y | 13.5 / 109.5 / 205.5 | 14.4 / 109.4 / 205.4 |
| Site Z | 62.5 | 63.7 |
| Carrier off_x | 12.0 | 12.0 (unchanged) |
| Carrier off_y | 24.7 | 24.7 (unchanged) |

---

## Known Issues / TODO

1. ~~Aspirate STL fix~~ — validated 2026-04-07
2. ~~Dispense~~ — validated 2026-04-07 with z_dispense=99
3. **Init ordering changed** — PIP before RoMa in evo.py, needs cold boot validation
4. **200uL / 1000uL tips** — definitions added to labware library, tip lengths need caliper measurement, Z values need teaching
5. **RoMa plate handling** — init works, pick/place not tested
