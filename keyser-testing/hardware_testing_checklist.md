# Tecan EVO Hardware Testing Checklist

## Pre-Test Setup

### Equipment Required
- [ ] EVO 150 powered on
- [ ] USB cable connected to pylabrobot PC
- [ ] EVOware PC disconnected from USB (only one client at a time)
- [ ] DiTi 50uL SBS tips loaded (position 3 on MP_3Pos at rail 16)
- [ ] Eppendorf 96-well plate with water in column 1 (position 1)
- [ ] Empty Eppendorf 96-well plate (position 2)
- [ ] `.venv` activated, `pip install -e ".[usb]"` done

### Software
- [ ] `v1b1-tecan-evo` branch checked out
- [ ] `keyser-testing/labware_library.py` has correct Z values (taught from jog tool)

---

## Test 1: Initialization (Cold Boot)

**Script:** `keyser-testing/test_v1b1_init.py`

### Steps
1. Power cycle the EVO
2. Run the init test script
3. Verify each phase completes:

| Step | Expected | Pass? |
|------|----------|-------|
| USB connection | "USB connected" | [ ] |
| ZaapMotion boot exit | All 8 tips XP2000/ZMA | [ ] |
| ZaapMotion motor config | 33 commands × 8 tips OK | [ ] |
| Safety module (SPN/SPS3) | OK | [ ] |
| PIA (all axes) | REE0 = `@@@@@@@@@@@` | [ ] |
| RoMa init + park | OK | [ ] |
| LiHa range queries | num_channels=8, z_range~2100 | [ ] |
| Plunger init | PID, PVL, PPR sequence completes | [ ] |

### Failure Actions
- ZaapMotion boot exit fails → check USB connection, retry
- PIA fails → check REE0 for which axis, use jog tool to investigate
- RoMa fails with error 5 → RoMa not present, set `has_roma=False`

---

## Test 2: Initialization (Warm Reconnect)

### Steps
1. Run Test 1 successfully
2. Stop the device (`evo.stop()`)
3. Run the init test again WITHOUT power cycling

| Step | Expected | Pass? |
|------|----------|-------|
| REE0 check | Not "A" or "G" → skip full init | [ ] |
| Quick setup | Channel count + ranges loaded fast | [ ] |
| Total time | < 5 seconds (vs ~45s for full init) | [ ] |

---

## Test 3: Tip Pickup

**Script:** `keyser-testing/test_v1b1_tips.py`

### Steps
1. Initialize EVO
2. Pick up 8 tips from column 1

| Step | Expected | Pass? |
|------|----------|-------|
| X/Y positioning | Channels aligned over tip column | [ ] |
| Z approach | Channels descend to tips | [ ] |
| Tip engagement | Force feedback engages all 8 tips | [ ] |
| Z retract | Channels lift with tips mounted | [ ] |
| RTS check | Tip status = 255 (all mounted) | [ ] |

### Calibration Notes
- If X is off by > 2mm: adjust X offset in labware or jog tool
- If Z doesn't reach tips: adjust z_start in tip rack definition
- If some tips don't engage: check individual channel alignment

---

## Test 4: Tip Drop

### Steps
1. With tips mounted, drop all 8 back to the rack

| Step | Expected | Pass? |
|------|----------|-------|
| Move to drop position | Same X/Y as pickup | [ ] |
| Plunger empty (PPA0) | Plunger returns to zero | [ ] |
| SDT + AST | Tips ejected cleanly | [ ] |
| RTS check | Tip status = 0 (none mounted) | [ ] |

---

## Test 5: Aspirate

### Steps
1. Pick up tips
2. Move to source plate (position 1)
3. Aspirate 25µL from column 1

| Step | Expected | Pass? |
|------|----------|-------|
| X/Y positioning | Channels over well column 1 | [ ] |
| Y-spacing (ys) | 90 (9mm well pitch) | [ ] |
| Leading airgap | PVL + SEP + PPR with force mode | [ ] |
| LLD detection | MDT finds liquid surface | [ ] |
| Aspirate tracking | MTR with correct steps (~2660 for 25µL) | [ ] |
| Z retract | Channels lift after aspiration | [ ] |
| Trailing airgap | PPR with force mode | [ ] |
| Visual check | No dripping, liquid in tips | [ ] |

### Known Issues
- Z-start may be too high — use jog tool to teach correct plate Z
- If error 3 on PAA: check ys value (must be 90-380)

---

## Test 6: Dispense

### Steps
1. After aspirating, move to destination plate (position 2)
2. Dispense 25µL into column 1

| Step | Expected | Pass? |
|------|----------|-------|
| X/Y positioning | Channels over dest well column 1 | [ ] |
| Dispense tracking | MTR with negative steps (~-2660) | [ ] |
| Visual check | Liquid dispensed into wells | [ ] |

---

## Test 7: Full Cycle

### Steps
1. Initialize
2. Pick up 8 tips
3. Aspirate 25µL from source column 1
4. Dispense 25µL to dest column 1
5. Drop tips

| Step | Pass? | Notes |
|------|-------|-------|
| Init | [ ] | |
| Tip pickup | [ ] | |
| Aspirate | [ ] | |
| Dispense | [ ] | |
| Tip drop | [ ] | |
| Clean stop | [ ] | |

---

## Test 8: RoMa Plate Handling (if applicable)

### Steps
1. Initialize with `has_roma=True`
2. Pick up plate from position 1
3. Drop plate at position 2

| Step | Expected | Pass? |
|------|----------|-------|
| RoMa init + park | Arm moves to park position | [ ] |
| Move to plate | Arm positions over carrier | [ ] |
| Gripper open | Gripper opens to plate width | [ ] |
| Descend + grip | Gripper closes on plate | [ ] |
| Lift + transfer | Plate moved safely | [ ] |
| Place + release | Plate placed at destination | [ ] |
| Arm park | Arm returns to park | [ ] |

---

## Z-Calibration Procedure

Use `keyser-testing/jog_liha.py` to teach positions:

1. **Tip rack z_start**: Jog to just above tip tops, record Z → set as `z_start` in labware
2. **Tip rack z_max**: Jog to bottom of tip search range, record Z → set as `z_max`
3. **Plate z_start**: Jog tip into well liquid surface, record Z → set as plate `z_start`
4. **Plate z_dispense**: Jog to dispense height (above well bottom), record Z → set as `z_dispense`
5. **Plate z_max**: Jog to maximum depth, record Z → set as `z_max`

Save all taught positions in `keyser-testing/taught_positions.json` for reference.
