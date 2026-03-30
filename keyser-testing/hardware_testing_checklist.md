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
- [x] `v1b1-tecan-evo` branch checked out
- [x] `keyser-testing/labware_library.py` has taught Z values from jog tool

---

## Test 1: Initialization (Cold Boot) ✅ PASSED

**Script:** `keyser-testing/test_v1b1_init.py`
**Date:** 2026-03-30

| Step | Expected | Pass? |
|------|----------|-------|
| USB connection | "USB connected" | [x] ~3s |
| ZaapMotion boot exit | All 8 tips XP2000/ZMA | [x] |
| ZaapMotion motor config | 33 commands × 8 tips OK | [x] |
| Safety module (SPN/SPS3) | OK | [x] |
| PIA (all axes) | REE0 = `@@@@@@@@@@@` | [x] |
| RoMa init + park | OK (~56s first time) | [x] |
| LiHa range queries | num_channels=8, z_range~2100 | [x] |
| Plunger init | PID, PVL, PPR sequence completes | [x] |

---

## Test 2: Initialization (Warm Reconnect) ✅ PASSED

**Date:** 2026-03-30

| Step | Expected | Pass? |
|------|----------|-------|
| REE0 check | Not "A" or "G" → skip full init | [x] |
| RoMa REE check | `@@@@@` → skip RoMa PIA | [x] |
| Quick setup | Channel count + ranges loaded fast | [x] |
| Total time | **3.4 seconds** (vs ~60s for full init) | [x] |

### Notes
- Fixed RoMa warm reconnect: now checks REE before PIA (was 56s, now 0.0s)
- Fixed USB buffer drain: uses 1s packet timeout instead of 30s
- Fixed `_is_initialized`: REE0 response cast to str (was int for some states)

---

## Test 3: Tip Pickup — IN PROGRESS

**Script:** `keyser-testing/test_v1b1_pipette.py`

### Z-Calibration Done
- Taught positions recorded via `jog_ui.py`:
  - tip top: X=3893, Y=146, Z=780
  - plate top-dest: X=3883, Y=1087, Z=260
  - plate top-source: X=3878, Y=2047, Z=295
- Labware library updated with taught Z values
- Tip rack: z_start=850, z_max=550 (search range 300)

### X/Y Calibration Done
- Per-labware calibration offsets added:
  - Tips: x_offset=+62 (+6.2mm), y_offset=+18 (+1.8mm)
  - Plates: x_offset=+103 (+10.3mm), y_offset=-6 (-0.6mm)
- Applied via `_apply_calibration_offsets()` in all operations

### Current Status
- Channels move to approximately correct X/Y position
- AGT (tip search) executes but returns error 26 "Tip not mounted"
- Likely issue: Z search range or tip engagement depth needs tuning
- **TODO**: Fine-tune X/Y offset and Z search parameters

| Step | Expected | Pass? |
|------|----------|-------|
| X/Y positioning | Channels aligned over tip column | [~] Close but needs fine-tuning |
| Z approach | Channels descend to tips | [x] AGT executes |
| Tip engagement | Force feedback engages all 8 tips | [ ] Error 26 |
| Z retract | Channels lift with tips mounted | [ ] |
| RTS check | Tip status = 255 (all mounted) | [ ] |

---

## Test 4: Tip Drop — NOT STARTED

Blocked by Test 3 (need tips mounted first).

---

## Test 5: Aspirate — NOT STARTED

Blocked by Test 3.

### Preparation Done
- Aspirate/dispense Z adjusted for mounted tip length
  (tip_ext = total_tip_length * 10 - 50 nesting = 531 units)
- Plate z_start=300, z_dispense=200 (taught from bare channel positions)
- Y-spacing fix in place (uses plate.item_dy not well.size_y)

---

## Test 6: Dispense — NOT STARTED

Blocked by Test 5.

---

## Test 7: Full Cycle — NOT STARTED

| Step | Pass? | Notes |
|------|-------|-------|
| Init | [x] | Cold + warm both work |
| Tip pickup | [ ] | Error 26, needs Z/X tuning |
| Aspirate | [ ] | |
| Dispense | [ ] | |
| Tip drop | [ ] | |
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

Taught positions saved in `keyser-testing/taught_positions.json`.
Labware edits saved in `keyser-testing/labware_edits.json`.

### Important Z Notes
- Tecan Z coordinate system: 0 = deck surface, z_range (~2100) = top/home
- Taught positions are measured with **bare channels** (no tip mounted)
- For aspirate/dispense with tips: Z target = plate.z_start + tip_extension
  - tip_extension = total_tip_length * 10 - nesting_depth (50 units / 5mm)
- AGT z_start/z_max are used directly (no tip extension needed — tips not yet mounted)

---

## Tools Available

| Tool | Purpose |
|------|---------|
| `keyser-testing/jog_ui.py` | Web UI for jogging, teaching, labware inspection |
| `keyser-testing/jog_and_teach.py` | CLI jog/teach tool |
| `keyser-testing/tips_off.py` | Emergency tip removal |
| `keyser-testing/test_v1b1_init.py` | Init test with timing |
| `keyser-testing/test_v1b1_pipette.py` | Full pipetting cycle test |

---

## Known Issues / TODO

1. **Tip pickup X/Y still slightly off** — calibration offsets close but may need further refinement
2. **Tip pickup Z** — AGT returns error 26 "Tip not mounted", search range or depth needs adjustment
3. **Aspirate/Dispense Z** — not yet tested, tip extension calculation needs hardware validation
4. **X offset root cause** — systematic 6-10mm offset between resource model and physical positions, currently compensated with per-labware offsets. Root cause may be deck origin definition or carrier off_x interpretation.
