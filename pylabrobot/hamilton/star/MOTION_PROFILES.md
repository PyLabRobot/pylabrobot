# Motion profiles, by firmware command

What the viewer plays for each Hamilton STAR firmware command, and where every number and choice
comes from. Each command the v1 driver sends either has a motion profile (made of phases, played in
order) or has none. Each value is tagged with its source:

| Tag | Meaning |
|---|---|
| **FW** | Carried by the command itself: the page plays what the device was told. |
| **PLR** | A value or model in PyLabRobot's code: a driver constant, a configuration, or the resource model. Cited by file and symbol. |
| **SIM** | How PLR's STAR simulator records the command (`hamilton/star/driver/simulator.py`). This is PLR's own model of the motion, not a documented firmware sequence. |
| **FIT** | Fitted to a real STAR's own command timings: every firmware request and reply, millisecond-stamped, from Venus HxUsbComm traces. Reproducible with `tools/hxusbcomm_timing.py`; data and statistics in section 8. |
| **DOC** | External documentation or a forum post, linked to the post. labautomation.io posts by Hamilton staff are treated as authoritative. |
| **HEUR** | Our choice, with no documented reference. The justification is given. |

Files: the decoder is `pylabrobot/visualizer3D/motion.py` (Python, turns a command into targets);
the player is `static/motion_player.js` (the page, plays targets in phases); the profile is
`static/motion_profile.js`.

---

## 0. Applies to every profile

| Choice | Value | Tag | Source / justification |
|---|---|---|---|
| Speed profile of a single move | Symmetric trapezoid (a triangle if too short to cruise; constant speed with no acceleration), except the X-arm, which moves on a jerk-limited S-curve | SIM + FIT | Trapezoid: the simulator's timing model, `SimulatedPipettes._get_travel_time` (`simulator.py`). S-curve for X: the traces reject a trapezoid for the X-arm (section 8.1). Page: `motionProfile(distance, speed, acceleration, jerk)` in `static/motion_profile.js`. |
| Moves that happen at once | Drives in the same phase start together, and the phase ends with the slowest | SIM | `owe_motion_time`: "the drives move at once, so a command takes as long as its slowest axis" (`simulator.py`). |
| Smallest move played | 0.05 mm (`STILL`); anything smaller is skipped | HEUR | Below the 0.1 mm firmware resolution; avoids zero-length tweens. |
| Model arrivals | After each motion, the model's own update must find the page already there (0.11 mm / 0.11° tolerance in the harness) | PLR + HEUR | The model is the source of truth (PLR). The tolerance is ours: `ARRIVAL_MM` / `ARRIVAL_DEG` in `smoothness_page.mjs`, just above the firmware's 0.1 mm resolution. Enforced by `smoothness.py`'s `assert_smooth`. |
| Page hidden / background tab | Motions jump to their end | HEUR | Browsers throttle hidden tabs, and blocking the run on an unwatched page makes no sense. |
| Python waits for the page | Until every connected page reports `motion_done`, at most 120 s (`Viewer3D.MOTION_TIMEOUT_S`) | HEUR | Paces the simulated run to the drawing; the timeout keeps a stuck page from hanging a run. |
| Heights | Converted to where the channel's stop disc is; the lowest point of a mounted tip is the disc minus the tip's overhang | PLR + SIM | `_Frames.lowest_point_z`, with the overhang from `SimulatedPipettes._below_stop_disc`. The master reports the lowest point of what a channel carries (`simulator.py` `RZ`). For CO-RE grip tools this is the grip line (`HamiltonCoreGripperTool.grip_line_height`). **Gap:** `_below_stop_disc` exists only on the simulator. Driving a real STAR, the decoder takes the overhang as 0, so heights with a tip mounted would be off by the tip's length. It should come from the model (`TipMountingShaft.tip_bottom`) instead. |

### Drive speeds used when the command does not state one

| Drive | Speed | Acceleration | Tag | Source |
|---|---|---|---|---|
| X-arm | 600 mm/s | 1297 mm/s², jerk 3210 mm/s³ | FIT | `X_SPEED`, `X_ACCELERATION`, `X_JERK` (`motion.py`). The driver states no X rate; firmware X commands carry only an acceleration *level*. Section 8.1: S-curve within-group R² 0.976, RMS 23.4 ms, ΔAIC 72.7 over a trapezoid. Jerk is well determined; speed and acceleration less so. |
| Channel Y | 300 mm/s | 900 mm/s² | FIT | `CHANNEL_Y_SPEED`, `CHANNEL_Y_ACCELERATION` (`motion.py`), from single-channel jogs (section 8.2); PLR's `default_y_speed` is 250 mm/s. The firmware states Y acceleration only as a level (1–4, `default_y_acceleration_level = 3`). The channels set off 0.118 s apart (`CHANNEL_Y_STAGGER`, section 8.4). The simulator still times Y at constant speed. |
| Channel Z | 150 mm/s | 800 mm/s² | FIT + PLR | Speed: `CHANNEL_Z_SPEED`, fitted from dispense→aspirate pairs (section 8.4; loose, 150–200); PLR's `default_z_speed` is 125 mm/s. Acceleration: `Pipettes.default_z_acceleration`, which the traces agree with. |
| 96-head Y / Z | the head's defaults | the head's defaults | PLR | `HeadConfiguration.y_drive_speed_default` / `z_drive_speed_default` and their accelerations (`features/head.py`): the value the firmware reports when read, else the configured default increments. |
| iSWAP gripper, when a close states no speed | `gripper_close_speed_default_increments` (5000), converted | `gripper_acceleration_default_increments` (75), converted | PLR | `iSWAPConfiguration` (`features/iswap.py`). |

---

## 1. Channel commands

### `C0 TP` — tip pick-up (`Pipettes.pick_up_tips`)
Profile: **stroke with handover**.

| Phase | Target | Tag | Source |
|---|---|---|---|
| 1. Rise | Any channel lower than `th` rises to it (by lowest point) | FW + SIM | `th`. The simulator records the stroke "across at the height it starts from" (`_record_tip_command`). |
| 2. Across | The arm to the X of the last column (`xp`); the channels to `yp`; the channels not named pushed along the rail by the spacing rule | FW + PLR + SIM | Positions are FW. Pushing the others follows `Pipettes._plan_y_positions(make_space=True)`, as the simulator does ("one rail", `_record_tip_command`). Ported as `_Frames.planned_ys`. |
| 0. Fixed | 0.065 s of command handling before anything moves | HEUR | Section 8.6. |
| 3. Down | The lowest point to `tp` at the Z drive's speed, setting off when 82% of the crossing's time has passed | FW + FIT | `tp`; the overlap, section 8.6. |
| 3b. Press | On to `tz` at 11.5 mm/s | FW + FIT | `tz`; the speed, section 8.6. |
| 4. Handover and hold | Each spot's tip goes to the channel's shaft, placed as `TipMountingShaft.mount_tip` places it, then seated at the Z drive's pace; the channels hold 4.356 s, the rest of the fixed time | PLR + FIT + HEUR | The placement is PLR (`_mounted_location`). Seating the tip at the Z-drive pace instead of snapping it is ours (HEUR): the model's placement and the stroke's bottom differ by the fitting geometry. |
| 5. Up | The lowest point to `th`, now with the tip's overhang | FW + PLR | The overhang is read off the tip in the spot and where the shaft seats it (as `TipMountingShaft.tip_bottom` reads it); the simulator's `defined_tip_lengths` only when the model has no tip there. |

Also used: begin height `tp` = spot + collar height (PLR, legacy `STARBackend.pick_up_tips`, per `_pick_up_tips_in_one_move`).

### `C0 TR` — tip drop / return (`drop_tips`, `return_tips`, `discard_tips`)
Profile: **stroke with handover**.

| Phase | Target | Tag | Source |
|---|---|---|---|
| 1–2 | As `C0 TP` | FW + SIM | |
| 3. Down | `tz`. With `ti=1` (DROP) this is the stop disc's height; with PLACE_SHIFT it is where the tip's cone ends | PLR | `_unchecked_fw_drop_tips` docstring: "With `PLACE_SHIFT` the heights are where the tip's cone ends; with `DROP`, the stop disc's." |
| 4. Handover | The tip goes to the spot under the channel (placed by `resting_location`), or nowhere (waste) | PLR | `tip_rack.resting_location`. |
| 5. Up | To `te`, empty | FW | |

Default heights: DROP begins at spot + collar and ends a fitting depth lower; PLACE_SHIFT uses 59.9 / 49.9 mm (PLR, legacy "Empirical, from legacy: https://github.com/PyLabRobot/pylabrobot/pull/63").

### `C0 AS` — aspirate (`Pipettes.aspirate`)
Profile: **stroke with dwell and leave**.

| Phase | Target | Tag | Source |
|---|---|---|---|
| 1–2 | As `C0 TP` | FW + SIM | |
| 0. Fixed | 1.879 s + 6.761 × transport-air time, before anything moves | FIT + HEUR | Section 8.6; placement HEUR. |
| 3. Down | max(`zl` ∓ `ip`, `zx`): into the liquid by the immersion depth, or above it when `it` is 1; no lower than the minimum height | FW | Field meanings are in legacy's `STARBackend.aspirate_pip` docstring (after the firmware guide): `zl` "Liquid surface at function without LLD", `ip` "Immersion depth", `zx` "Minimum height (maximum immersion depth)". |
| 4. Dwell | Longest channel of (`av` / `as`) + `wt` + mixing; while the volume is drawn, each tip follows the surface down by `fp` at a steady pace (no lower than `zx`) | FW + FIT | Volume ÷ flow rate, settling time, mixing (section 8.6). `fp` is "the total distance the tip moves during aspiration, from the surface down to the new, reduced height" (PLR maintainer, [discuss.pylabrobot.org/t/304/6](https://discuss.pylabrobot.org/t/hamilton-surface-following-issues/304/6)). |
| 5. Leave | Up to the surface at the swap speed `de` | FW | `de` "Swap speed (on leaving liquid)" (legacy docstring; default 10 mm/s). |
| 5b. Pull-out | On up by `po` from the surface, at the swap speed, before the transport air is drawn | FW + FIT + HEUR | `po` "rise before drawing transport air" (PLR's `aspirate` docstring). The speed is FIT: Venus aspirations otherwise identical take 5.35 s (n 4 vs 802) and 4.85 s (n 12 vs 3,534) longer with a 10 mm pull-out at a 2 mm/s swap speed, i.e. 10 mm at 2 mm/s. Measured from the surface: HEUR. With PLR's defaults (`po` 10 mm, `de` 2 mm/s) every aspirate spends about 5 s on it. |
| 6. Up | To `te` | FW | |

Known divergences from the device (to do):
- The conical second section (`zu`, `zr`) is not played: how the firmware changes the following pace there isn't documented.
- In the demo's aspirate (PLR's defaults), PLR sent the minimum height `zx` equal to the surface `zl`, so the tip went no deeper than the surface and could follow nowhere, whatever `ip` and `fp` said. Worth checking how PLR sets `zx` upstream.
- In v1, LLD is **never** done inside `C0AS`: capacitive/pressure searches run first as their own commands (see `Px ZL` / `Px ZE`, section 5), and `C0AS` goes with LLD off (PR [#1362](https://github.com/PyLabRobot/pylabrobot/pull/1362)). So `C0AS` itself needs no search phase.

### `C0 JY` — channels to Y positions
Profile: **single axis (Y)**. Each channel to `yp` (FW), at the Y drive speed (section 0).

### `C0 JZ` — channels to Z positions
Profile: **single axis (Z)**. Each channel's lowest point to `zp` (FW), at the Z drive speed and acceleration (section 0).

### `C0 FY` — free the Y range (the iSWAP's `make_space`)
Profile: **single axis (Y), commanded target from the model**. The master packs the channels as far forward as they fit, each against the one in front. The target isn't in the command. The v1 driver reads the channels' Y back after the command, in a `finally` (PLR, `iSWAP.make_space`). The simulator writes the packed positions ahead, because its reads answer from the model (SIM, `_unchecked_fw_position_components_for_free_y_range`), and the page plays to them (`recorded_first`: state held back until played). This is the commanded outcome, not a confirmed one: a failure would be reconciled only by the read-back (planned).

### `C0 ZA` — all channels to Z safety
Profile: **single axis (Z), commanded target from the model**. Every channel up to the top of its Z travel, `Pipettes.configuration.z_range[1]`, where `probe_z_max` leaves them (PLR). The simulator writes this ahead of the command, since its reads answer from the model (SIM, `SimulatedPipettes.probe_z_max`); the driver's read-back is what confirms it.

### `C0 ZT` — pick up the CO-RE grip tools (`Pipettes.pick_up_core_gripper_tools`)
Profile: **stroke with handover**, on two adjacent channels.

| Phase | Target | Tag | Source |
|---|---|---|---|
| 1–2 | Both channels to the tools' pick-up points: X `xs`, Y `ya` (back) / `yb` (front) | FW + PLR | Points come from the model: each tool's `pick_up_location` (`resources/hamilton/core_gripper_tools.py`), in the holder at `channel_x_center` and its front/back channel Y centres (`core_grippers.py`). They match legacy's wire exactly (`xs07975 ya1250 yb1070` on a STARlet, legacy `STAR_tests.py`). |
| 3. Down | `tz` = tool top − 10 mm, by the stop disc (the channel is bare) | PLR + HEUR | Heights are legacy's (`pick_up_core_gripper_tools`: begin 235, end 225, against tools whose tops stand at 235.0, probed per `hamilton_core_gripper_1000ul_5ml_on_waste`). Reading `tz` as a stop-disc height is ours (HEUR): the firmware's reference point for `ZT` heights is not documented publicly. |
| 4. Handover | Each tool onto its shaft, placed by `mount_tip` (pick-up location on the shaft's axis) | PLR | `TipMountingShaft.mount_tip`. |
| 5. Up | `th`, with the tool hanging by its grip line (pick-up z − fitting depth − grip-line height) | PLR + SIM | Matches what the simulator reports for a channel carrying a tool. |

Travel height `th`: as high as a tool on a channel reaches (v1's convention for tip commands, `_tip_traverse_height`). Legacy sends the iSWAP traversal height, 280 mm (PLR, `_iswap_traversal_height`); `minimum_traverse_height=280` reproduces it.

### `C0 ZS` — return the CO-RE grip tools (`return_core_gripper_tools`)
Profile: **stroke with handover**, the reverse of `C0 ZT`. The channels go to where the tools were parked (remembered at pick-up). Down to `tz` = tool top − 30 mm (legacy: begin 215, end 205; PLR). Here `tz` is read as the lowest point *with* the tool mounted (HEUR, as for `ZT`). Each tool goes back to its parked location and rotation (PLR), then the channels rise to `te`.

---

## 2. X-arm

### `X0 XP` / `X0 SP` — arm to an X position
Profile: **single axis (X)**. `XP` is the left arm and `SP` the right. The target is the arm's `…a` field, in increments converted by `XArmConfiguration.x_increments_to_mm`, less `reference_point_from_left` (FW + PLR), at 400 mm/s and 500 mm/s² (HEUR, section 0). Everything on the arm (channels, 96-head, iSWAP) rides with it (PLR: they're children of the arm resource).

---

## 3. iSWAP

Joint conventions (DOC, Camillo Moschner, [discuss.pylabrobot.org/t/517/1](https://discuss.pylabrobot.org/t/intro-to-epic-tame-the-iswap/517/1)):
- Rotation (elbow) −90° / 0° / +90° = left / front / right.
- Wrist −135° / −45° / +45° / +135° = right / straight / left / reverse.

In PLR these are `iSWAPConfiguration`'s predefined increments, read at setup.

### `R0 YA` / `R0 ZA` — the head along Y / Z
Profile: **single axis**.
- Y: to `ya` at `yv`, converted by the arm's configuration (FW + PLR). No acceleration rate, since `R0 YA` carries only a level, so it plays at constant speed (HEUR, as channel Y).
- Z: to `za` + `elbow_z_offset_above_finger` at `zv`, with acceleration `zr` × 1000 increments/s² (FW + PLR).

### `R0 PA` — elbow and wrist together
Profile: **two joint turns, together**. Each joint to its own angle at its own speed and acceleration (`wa`/`wv`/`wr` for the elbow, `ta`/`tv`/`tr` for the wrist; FW), converted by `iSWAPConfiguration` (PLR). Each turns about the pivot the driver turns it about (`proximal_joint`; PLR). Both run on a straight line in joint space (HEUR; the controller's interpolation is not public).

### `R0 GA` — jaws to a width
Profile: **jaws**. The fingers move to the width `ga` at `gv` / `gr` (FW). Each finger travels half the width change in the same time, so at half the drive's speed (derived from symmetric jaws). If opening, the page lets go of what it held *before* moving; if closing, it takes hold *after* (HEUR: the ordering that avoids dragging or dropping the plate mid-motion).

### `C0 GC` — close onto an object
Profile: **jaws**. Closes to `gb` at the default close speed and acceleration (PLR, section 0), then takes hold (HEUR, as `R0 GA`).

### Plate and lid moves (`iSWAPTransport`, Python, from the primitives above)
The compound commands (`C0 PP` / `PR` / `PM`) are not used. Their internal motion isn't public, and the firmware "choos[es] among multiple valid poses unpredictably" (DOC, [discuss.pylabrobot.org/t/517/1](https://discuss.pylabrobot.org/t/intro-to-epic-tame-the-iswap/517/1)). A move is planned as primitives (`hamilton/star/driver/features/iswap_transport.py`):

| Choice | Value | Tag | Source / justification |
|---|---|---|---|
| Phases | open → rise → travel → descend → grip / release → rise | HEUR | Follows the phases `C0 PP`'s parameters describe (open width, traverse height, grip height, end height). |
| Travel height | `iswap.default_minimum_traverse_height` (284 mm), fixed, never computed from the deck | PLR | Legacy sends 280 mm. |
| Grip width, open margin, strength, tolerance, pick-up depth | width across the plate; +3 mm; 4; 2 mm; 5 mm below the top (or `preferred_pickup_location`) | PLR | Legacy `STARBackend.pick_up_resource`. |
| Grip below a lid | 2 mm below the lid's skirt (`nesting_z_height`) | HEUR | Jaws closing within the skirt would close on the lid. |
| Elbow configuration | Only the three elbow stops; the stop with the smallest joint turn from now, front on ties | HEUR | The stops are PLR/DOC; the ranking is ours. |
| Travel order | Y then turn, else turn then Y, else turn at a safe Y = max(y_min, min(now, target, y_max − link 1 − tool)) | HEUR | Chosen so the turn never sweeps the arm behind the rail. Checked by sampling each turn at 36 points against `_check_pose_reachable`. |
| X during travel | Runs concurrently with Y and the turn | HEUR | The X-arm is a separate drive. |

### Not decoded
- `C0 PG` park: close the jaws, lift to the traverse height, retract ("Sending no height leaves that to the master, which does not raise the arm"; PLR, `iSWAP.park` docstring). A rotation-drive calibration lists a parking position of ≈29500 increments (≈+91°) (DOC, [discuss.pylabrobot.org/t/517/4](https://discuss.pylabrobot.org/t/intro-to-epic-tame-the-iswap/517/4)). The retract path itself is not public.
- `R0 GB` close to an object, `R0 GS` relative jaw move: jaws profiles, not yet mapped.
- `C0 FI`, `R0 GI`: initialisation; no public profile.
- Venus 6.2 teaches custom Get/Place paths as waypoint poses (Transport Paths Editor), documented only in the non-public Venus 6.2 Programmer's Manual (DOC, [labautomation.io/t/5759](https://labautomation.io/t/transport-paths-editor/5759)).

---

## 4. 96-head

### `C0 EP` / `C0 ER` — tip pick-up / drop
Profile: **stroke with handover**, all 96 shafts. The head goes to `xs` (sign from `xd`) and `yh`, down and up as the tip commands (FW). Channel A1 goes to spot A1 (PLR: `Head96` / `NChannelPipette` layout). The head's drive defaults are in section 0. The simulator places the head after the command (`SimulatedHead96._place_after_tip_command`: arm X, head Y, z = ze + overhang; SIM).

### `H0 YA` / `H0 ZA` — the head along Y / Z
Profile: **single axis**, to the target at `yv`/`yr` or `zv`/`zr` (FW), converted by `HeadConfiguration` (PLR).

### Not decoded
- `H0 PA` / `H0 PB` aspirate / dispense (a stroke with dwell, like `C0 AS`).
- `C0 EM` move to a coordinate.
- `H0 ZL` cLLD probe (see section 5).
- `H0 DQ` dispensing drive (piston only, no visible motion).
- Touch-off is not available on the multi-probe head: "the MPH is a significantly heavier tool … making this feature impractical" (DOC, Hamilton, [labautomation.io/t/1788/2](https://labautomation.io/t/touch-off-with-mph/1788/2)).

---

## 5. Not decoded yet, with what is known

### Detection searches — `Px ZL` (cLLD), `Px ZE` (pLLD), `Px ZH` (Z-touch), `C0 XL`, `Px YL`
Profile to build: **search**. Each channel on its own, all concurrently:
1. approach to the start;
2. a slow constant-speed descent (or traverse, for X/Y) at the search speed, until detection or the end;
3. a small post-detection move;
4. then stay, or go to Z safety.

Channels stop at different heights at different moments.

| Parameter | Firmware field | Tag | Source |
|---|---|---|---|
| Start, end (stop-disc heights) | `zc`, `zh` (ZL/ZE); `zb`, `za` (ZH) | FW | `_unchecked_fw_probe_z_using_clld` / `_plld` / `_ztouch` docstrings (`pipettes.py`). |
| Search speed, acceleration | `zl`, `zr` (ZL); `zu`, `zr` (ZH); approach `zv` (ZH) | FW | Same. |
| After detection | `zj` (0 down, 1 up) by `zi` | FW | Same. |
| Z-touch force | detection limiter PWM `cg`, push-down PWM `cf` | FW | Same. Touch-off "relies on force feedback to determine when the channel is contacting the bottom surface" (DOC, Hamilton, [labautomation.io/t/1788/2](https://labautomation.io/t/touch-off-with-mph/1788/2)). Needs channel firmware from March 2022 on (DOC, [discuss.pylabrobot.org/t/543/3](https://discuss.pylabrobot.org/t/reason-behind-z-touch-only-being-available-to-8-channels-made-in-2022-and-onwards/543/3)). |
| Where searches start and stop | 5 mm above a container's top (2 mm for wells); 1 mm below the modelled cavity bottom | PLR | `Pipettes.search_start_clearance`, `well_search_start_clearance`, `search_limit_below_cavity_bottom`. |
| Sequence in an LLD aspirate | `Px DC` (blow-out air, in air) → approach → searches → `C0 RL` → `C0 AS` with LLD off | PLR | PR [#1362](https://github.com/PyLabRobot/pylabrobot/pull/1362). |
| **Where it stops** | not in the command; known only from the answer (`C0 RL`, or the model the simulator moves) | — | Needs post-answer targets: play the descent to where the model reports (exact in the simulator), or an open-ended descent at search speed that stops when the answer lands (on hardware). |

### Single-channel and arm moves
- `Px ZA`: one channel to a stop-disc Z. A single-axis Z move (FW).
- `C0 JE`: "the device spreads them itself, over the same band the initialization procedure uses" (PLR, `spread_channels`). The targets are the band spread evenly (PLR, `default_initialize_y_positions`).
- `C0 JP`: frees one channel as much as possible; "the device decides where the others go" (PLR, `make_max_space_for_channel`). Targets are known only after the answer.
- `C0 KX` / `KR`: "The master raises what the arm carries before it travels" to Z safety, then X (PLR, `_unchecked_fw_move_x_with_attached_components_at_z_safety`). Profile: Z-safety phase, then X.
- `Px DC`: blow-out air drawn in air. Piston only, no visible motion.

### Side touch (dispense; v1 has no dispense yet)
"The channel will go down to the touch-off z-height specified, then moves over to the right, dispense, and then moves up" (DOC, Hamilton, [labautomation.io/t/2255/6](https://labautomation.io/t/post-dispense-tip-touch-on-starlet/2255/6)). "It is restricted to a positive x-movement so to the right. Max distance is 4.5mm" (DOC, Hamilton, [labautomation.io/t/2255/9](https://labautomation.io/t/post-dispense-tip-touch-on-starlet/2255/9)). Legacy's `C0 DS` carries a side-touch distance.

### Autoload, initialisation
- Autoload (`I0 YA/ZA/YP/ZP`, `C0 IV`, the barcode scanner move, carrier load/unload): no public profile. Loading moves a carrier into the tree.
- Initialisation (`C0 DI`, `C0 FI`, `X0 XI`, the heads): no public profile.

---

## 6. Commands with no motion profile
Reads (`R*`, `Q*`, `VW`), pressure-monitoring and sensor setup (`AC`, `AF`, `AN`, `AQ`, `BG`, `BH`), drive parameters (`AA`), brakes (`R0 BA/BO`), drive power (`X0 XO`, `R0 GO`), cover (`C0 CO/HO/CE/CD`), tip-type definition (`C0 TT`), master setup (`C0 UA`, `C0 VI`), and autoload sensing and barcode setup (`CQ`, `CS`, `CT`, `CB`, `CP`, `AR`, `AF`).

---

## 8. Measured from firmware traces

**Source:** Venus `HxUsbComm*.trc` logs from the Chory lab's STAR (266 files, 447 MB, Nov 2023 – Apr 2025; not in the repo). Every request (`<`) and reply (`>`) carries a millisecond timestamp. Pairing them by id gives 2,559,959 answered commands and how long each took. Arm-X reads (`C0 RX`, 744,966 of them) give where the arm was before most moves.

**Reproduce:** `python tools/hxusbcomm_timing.py <trace folder> --max-mb 900 --out timing.json` on any Venus traces. It reads one file at a time, so memory stays flat.

**How moves are isolated:** within a group of otherwise identical commands, time = overhead + move(distance), with one overhead per group.
- Pure X jogs: `C0 JX`, and `C0 EM` when Y and Z are unchanged.
- `C0 AS`/`DS` identical except for X, with the channels' Y unchanged from the previous command.
- Single-channel Y and Z jogs: `C0 KY`, `C0 KZ`.
- A move is measured only from a previous command that answered without error.

### 8.1 The X-arm is jerk-limited
15 groups:

| Model | Parameters | RMS | Within-group R² | AIC | AICc | BIC |
|---|---|---|---|---|---|---|
| **S-curve, overhead per group** | v 600 mm/s, a 1297 mm/s², j 3210 mm/s³ | **23.4 ms** | **0.976** | **−639.6** | **−630.0** | **−594.6** |
| Trapezoid, overhead per group | v 595 mm/s, a 605 mm/s² | 35.5 ms | 0.945 | −566.9 | −558.4 | −524.4 |
| S-curve, one shared delay | — | 3818 ms | −635 | 249.1 | 249.6 | 259.1 |
| Trapezoid, one shared delay | — | 3880 ms | −656 | 250.1 | 250.3 | 257.6 |

- **Why a trapezoid can't fit:** in both pure jogs, going from 1 to 10 mm adds 0.30 s and from 10 to 100 mm adds 0.50 s (a ratio of 1.65). A trapezoid gives at least √10 ≈ 3.2 for any acceleration, even with a speed cap. A constant delay cancels in these differences, so it can't rescue the trapezoid.
- **Sensitivity:** jerk is well determined (halving or doubling it roughly doubles the RMS). Speed and acceleration are loosely determined above ≈450 mm/s and ≈1100 mm/s², because few recorded moves are long enough to cruise.
- **One shared delay is rejected:** the constant part of a command's time is specific to the command, not a single latency.

### 8.2 Channel Y and Z are trapezoids

| Axis | Jog data (distance: median time) | Fit |
|---|---|---|
| Channel Y (`C0 KY`) | 1 mm: 0.132 s (n20); 10 mm: 0.285 s (n63); 100 mm: 0.730 s (n1) | Trapezoid v ≈ 300 mm/s, a ≈ 900 mm/s², RMS 4.7 ms. Jerk improves it by < 1 ms (noise). |
| Channel Z (`C0 KZ`) | 1 mm: 0.185 s (n139); 10 mm: 0.338 s (n39) | PLR's `default_z_acceleration` 800 mm/s² reproduces the 1→10 mm step to 0.1 ms. |

Only three Y distances (one sample at 100 mm) and two Z distances exist, so mild jerk on Y or Z can't be ruled out.

### 8.3 Other factors (command overheads are now used by the page: 8.6)
- **Communication round trip:** read replies arrive in ≈9–21 ms (median `X0 RF` 9 ms, `H0 RH` 7 ms, `C0 RX` 21 ms). This is the only delay common to all commands.
- **Command overhead is command-specific.** For the same X move, the 96-head's `C0 EM` takes ≈0.36 s longer than a channel `C0 JX` (overheads ≈0.48 s vs ≈0.12 s after the fitted move time).
- **In tip commands, short X moves overlap the Z stroke.** A `C0 TP` after a `C0 TR` takes 6.331 s for 9 mm and 6.335 s for 18 mm, where the X-arm alone needs ≈0.1 s more for the longer move. Above ≈20 mm the X distance shows again. The firmware appears to move X while Z is still travelling. The player runs the phases in sequence, so tip strokes are drawn slightly long for short moves.
- **Reply times cluster on ≈50 ms steps** in some long commands (e.g. `C0 DS` at 4.10 / 4.15 / 4.20 s), which suggests the controller reports on a polling tick. It limits resolution for small effects.
- **Command durations** (medians over all traces; these depend on each command's parameters, so they describe, they don't model):

| Command | n | Median | Command | n | Median |
|---|---|---|---|---|---|
| `C0 AS` aspirate | 229,489 | 6.41 s | `C0 PP` iSWAP get plate | 50,365 | 6.76 s |
| `C0 DS` dispense | 218,050 | 5.00 s | `C0 PR` iSWAP put plate | 47,743 | 6.05 s |
| `C0 TP` tip pick-up | 55,084 | 6.18 s | `C0 PG` iSWAP park | 11,839 | 7.62 s |
| `C0 TR` tip drop | 55,088 | 8.02 s | `C0 EA` / `C0 ED` 96-head aspirate / dispense | 49,616 / 48,950 | 7.75 / 6.11 s |
| `C0 EP` / `C0 ER` 96-head tips | 5,965 / 5,967 | 8.82 / 7.46 s | `C0 RX` read arm X | 744,966 | 0.021 s |

### 8.4 Channel commands: a full model, and do the channels move in Y one after another?

**Reproduce:** `python tools/hxusbcomm_channels.py <trace folder>`.

**Method.** Each `C0 AS/DS/TP/TR` is paired with the channel command just before it, when nothing else moved in between, both answered without error, and the active channels share one X. The duration is fit as

d = a[group] + b_v · volume time + b_m · mix time + b_z · Tz(stroke) + b_xy · Txy(dx, dy₁…dy₈)

- A group is the previous command plus every categorical parameter; continuous parameters, and mixing parameters when there is no mixing, are removed.
- Volume time is volume ÷ flow for the slowest channel. The Z stroke runs from traverse height to the liquid surface or tip height.
- X uses the §8.1 S-curve and Y the §8.2 trapezoid. Nonlinear constants come from a grid search with the linear solve inside each grid point.
- A Y shift under 0.5 mm counts as no move (positions jitter by 0.1 mm between commands).

**`C0 AS`** (228,260 pairs, 706 groups; Y moves: 63,073 × 8 channels, 5,532 × 7, 95 × 2–4):

| Txy model | R² within | RMS | AIC | BIC |
|---|---|---|---|---|
| no XY | 0.9610 | 96.3 ms | −1,067,012 | −1,059,682 |
| X only | 0.9831 | 63.4 ms | −1,257,512 | −1,250,172 |
| Y together (max) | 0.9708 | 83.4 ms | −1,132,777 | −1,125,437 |
| Y sequential (sum) | 0.9708 | 83.2 ms | −1,133,447 | −1,126,107 |
| max(X, Y together) | 0.9885 | 52.4 ms | −1,345,185 | −1,337,845 |
| max(X, Y sequential) | 0.9756 | 76.2 ms | −1,173,739 | −1,166,398 |
| X then Y together | 0.9853 | 59.1 ms | −1,290,135 | −1,282,795 |
| X then Y sequential | 0.9740 | 78.6 ms | −1,159,627 | −1,152,287 |
| **max(X, Y together + 0.80 s when Y moves)** | **0.9950** | **34.5 ms** | **−1,536,170** | **−1,528,820** |
| max(X, Y together + 0.12 s per extra channel) | 0.9950 | 34.6 ms | −1,534,356 | −1,527,005 |
| max(X, Y together + 0.80 s + 0.00 s per extra channel) | 0.9950 | 34.5 ms | −1,536,168 | −1,528,807 |

**`C0 DS`** (216,436 pairs, 357 groups; Y moves: 67,759 × 8, 5,533 × 7, 71 × 4):

| Txy model | R² within | RMS | AIC | BIC |
|---|---|---|---|---|
| no XY | 0.8699 | 148.8 ms | −824,027 | −820,324 |
| X only | 0.8820 | 141.7 ms | −845,089 | −841,376 |
| Y together (max) | 0.9637 | 78.6 ms | −1,100,086 | −1,096,373 |
| Y sequential (sum) | 0.9639 | 78.4 ms | −1,101,521 | −1,097,808 |
| max(X, Y together) | 0.8941 | 134.3 ms | −868,499 | −864,786 |
| max(X, Y sequential) | 0.9730 | 67.8 ms | −1,164,167 | −1,160,454 |
| X then Y together | 0.9335 | 106.3 ms | −969,445 | −965,732 |
| X then Y sequential | 0.9632 | 79.1 ms | −1,097,515 | −1,093,802 |
| max(X, Y together + 0.75 s when Y moves) | 0.9885 | 44.3 ms | −1,348,629 | −1,344,906 |
| max(X, Y together + 0.12 s per extra channel) | 0.9884 | 44.4 ms | −1,347,722 | −1,343,999 |
| **max(X, Y together + 0.20 s + 0.08 s per extra channel)** | **0.9887** | **43.9 ms** | **−1,352,573** | **−1,348,840** |

**`C0 TP` / `C0 TR`:** within groups, a tip pick-up never changes Y. The best models reach R² 0.05 (TP, RMS 26 ms) and 0.007 (TR, RMS 123 ms). Nothing about Y can be read from them.

**What is established:**
- X and Y overlap. In an earlier pass with a coarser key, max(X, Y together + a fitted Y cost) beat X-then-Y-with-its-own-fitted-cost by ΔAIC ≈ 212,000 (AS) and ≈ 433,000 (DS).
- Any Y move costs ≈0.75–0.80 s on top of its travel time (FIT).
- Volume time enters with slope 0.99–1.02 (FIT).

**What is not established: ripple, i.e. whether the Y cost is a stagger per channel.** A flat 0.80 s and 0.12 s per extra channel (0.84 s for 8 channels) predict the same for the 8- and 7-channel moves that make up 99.9% of the data, so their AIC difference is small. For DS, the joint model's per-channel part rests on the 7-vs-8 split, which is also a split between protocols.
- The one independent test uses held-out rows with 2–6 channels moving, fitted on the rest.
- On 53 four-channel dispenses from one protocol, the flat 0.80 s overpredicts by 0.50 s (RMS 498 ms), while 0.12 s per extra channel misses by only −0.07 s (RMS 86 ms). This points to a stagger, but it comes from a single protocol.
- The aspirate "few-channel" rows are 0.1 mm jitter, not moves.
- On this data alone the ripple is suggestive. A run that moves 1–4 channels in Y inside otherwise identical commands would test it independently. Single-channel `C0 KY` jogs (overhead ≈65–75 ms beyond travel) are a different command and aren't used as evidence.

**The ripple's size, taken as given.** The ripple is known from the device (user; DOC to follow). Fitting one Y cost per count of channels moving (`C0 DS`, `ripple` in the tool's output; R² within 0.9883, RMS 42.5 ms, AIC −1,366,602, the best DS model):

| Channels moving in Y | Y cost beyond travel (FIT) | 95% profile interval | Rows | Source of the rows |
|---|---|---|---|---|
| 4 (channels 1–4) | 0.300 s | 0.295–0.315 s | 71 | one protocol, 15 groups, 12 with still rows |
| 7 | 0.690 s | 0.685–0.695 s | 5,533 | channel 8 idle |
| 8 | 0.760 s | 0.755–0.760 s | 67,759 | — |

- A line through the three gives **0.118 s per extra channel** with a fixed part of −0.05 s, i.e. about none.
- The page model: channel *i* (in the order they move) starts 0.12 s after the one before it, and the command waits for the last to arrive.
- The 7→8 step (0.07 s) is smaller than the line's slope. The order the firmware moves them in, and whether the stagger depends on distance, aren't resolved by these three counts.

**Pure Z,** from DS→AS pairs with no X or Y change and no mixing (32,968 pairs):
- Strokes span 12.9–57.9 mm in two clusters, 13 mm and 53–56 mm.
- Z is identified only when inert parameters are left out of the group key. With the mixing speed in the key, groups split by protocol and absorb the stroke: R² gains just 0.0002.

| Model (group key without inert parameters) | R² within | RMS | AIC | BIC |
|---|---|---|---|---|
| volume only | 0.7644 | 134.9 ms | −132,056 | −131,972 |
| volume + linear stroke (0.0142 s/mm) | 0.9858 | 33.2 ms | −224,555 | −224,463 |
| **volume + 2 × trapezoid (v 150 mm/s, a 800 mm/s²), slope fixed at 1** | **0.9858** | **33.1 ms** | **−224,741** | **−224,640** |
| volume + 2 × trapezoid (v 125, a 800) | 0.9812 | 38.1 ms | −215,344 | −215,260 |

The acceleration agrees with PLR's `default_z_acceleration` (800 mm/s²). The speed is loosely determined (150–200 mm/s across group keys), because the strokes form two clusters.

### 8.5 Next fits the same data supports
- A decisive Y-ripple run (above).
- iSWAP park (`C0 PG`), and the far-right iSWAP path (8.8).
- The X/Z overlap in tip commands, as a phase overlap in the player.

### 8.6 Fixed time per command (what a command takes beyond the drawn motion)

**Reproduce:** `python tools/hxusbcomm_fixed.py <trace folder>`.

**Method.** Each command is timed exactly as the page draws it, using the constants in `motion.py`:
- X on the S-curve;
- channel Y at 300 mm/s and 900 mm/s², the channels setting off 0.118 s apart;
- Z at 150 mm/s and 800 mm/s²;
- the aspiration dwell (volume ÷ flow + settling time), and the swap-speed exit.

The measured duration minus that drawn time is the command's fixed time. It's fitted as a linear model of the parameters that plausibly set it. The page plays it as a pause before the motion, except the tip commands', which hold at the bottom: a pick-up with the tips on, a drop while they are pushed off. Each hold is the measured duration less the drawn motion, so it assumes the tip commands' Z runs at the fitted speed. *Where* in a command the firmware spends it isn't measured (HEUR).

| Command | Model (FIT) | n | R² of the fixed time | RMS | AIC | BIC | Constant only: RMS / AIC | Whole-duration R², median miss |
|---|---|---|---|---|---|---|---|---|
| `C0 AS` | 1.879 s + 0.979 × mix volume time + 0.557 s × mix cycles + 6.761 × transport-air time | 228,477 | 0.641 | 521 ms | −297,806 | −297,765 | 870 ms / −63,563 | 0.912, 65 ms |
| `C0 TP` | 4.421 s, played as a 4.356 s hold at the bottom after 0.065 s of command handling (placement HEUR), **plus the press drawn as motion** (0.0871 s/mm of `tp − tz`) and the descent setting off at 82% of the crossing (both below) | 48,304 | 0.830 | 38 ms | −316,189 | −316,172 | 94 ms / −228,685 | ≈0.84, — |
| `C0 TR` | 5.009 s (mean; channels add 0.21 s each, R² 0.15, but 7 against 8 channels is also one protocol against the others: left out), played as a 4.944 s hold at the bottom while the tips are pushed off, after 0.065 s of command handling (placement HEUR) | 54,290 | 0.000 | 176 ms | −188,805 | −188,797 | same | ≈0.61, — |
| `C0 ZA` | 0.14 s (median of 1,783; the channels were mostly already up) | 1,783 | — | — | — | — | — |
| `C0 EP` (96-head tips on) | 4.938 s (median, against PLR's head drive defaults; IQR 4.934–4.947), played as a 4.873 s hold at the bottom after 0.065 s of handling (placement HEUR) | 155 | — | — | — | — | — |
| `C0 ER` (96-head tips off) | 4.490 s (median; IQR 4.281–4.499), played as a 4.425 s hold at the bottom after 0.065 s of handling (placement HEUR) | 5,867 | — | — | — | — | — |
| `C0 EA` / `C0 ED` (96-head aspirate / dispense) | `EA` 5.80 s; `ED` by mode (`da`): jet (0, 1) 1.41 s, surface / empty (2, 3, 4) 5.85 s. Held at the bottom with the pumping (volume ÷ flow) and settling time, after 0.065 s of handling (placement HEUR). From four local traces (`tools/hxusbcomm_head96_liquid.py`: travel timed as 8.7, less pumping and settling): `EA` median of 46 (IQR 5.2–6.9 s; 5.4–6.9 by protocol), `ED` without mixing at ≥ 100 µL/s: mode 1 median of 12 (1.40–1.44 s), mode 2 median of 8 (5.83–5.90 s), mode 3 a single 4.90 s. Modes 0 and 4 are unrecorded, and a few mode-3 or 10 µL/s dispenses took 11–13 s that these parameters don't explain; the Chory lab set (≈49,000 of each) would settle both | 46 / 21 | — | — | — | — | — |
| `H0 YA/ZA` (96-head moves) | 0.11 s: an `H0 YP` that travels nothing (10th percentile of 31); HEUR by analogy | — | — | — | — | — | — |
| `C0 JY/JZ/FY`, `X0 XP`, iSWAP `R0` steps | 0.065 s: a 1 mm `C0 KY` jog less its travel; HEUR by analogy | — | — | — | — | — | — |
| `C0 ZT`/`ZS` (CO-RE tools) | as `C0 TP`/`C0 TR`, held at the bottom; HEUR, no recorded counterpart | — | — | — | — | — | — |
| `C0 ZP`/`ZR` (CO-RE plate grip / release) | as `C0 ZT`/`ZS`, held at the bottom (`CORE_PLATE_GRIP_FIXED`, `CORE_PLATE_RELEASE_FIXED`); HEUR and likely long: a grip closes two channels on a plate, with no tip to clamp or check. No recorded counterpart; a trace of a protocol that uses the CO-RE gripper (KAPA does) would settle it | — | — | — | — | — | — |

- **Why these R² look modest:** they measure the fixed time, which is what's left after the drawn motion, and it spreads little (tip pick-up: 88 ms). Against the whole duration the page's timing reaches R² 0.91 (aspirate), ≈0.84 (tip pick-up) and ≈0.61 (tip drop; the whole-duration figures for the tip commands are 1 − RMS²/variance of the duration).
- **Channel count is left out.** Nearly every recorded command uses 7 or 8 channels, so its coefficient tracks protocols: +0.04 s in one extraction, −0.27 s in another differing by 0.1% of rows.
- **Aspirate:** the median |residual| is 65 ms; the RMS is inflated by a few protocols (liquid-level detection, 34 rows, which the page doesn't draw). Mixing happens at the bottom, so its two terms are added to the dwell, not the pause.
- **Tip press:** the fixed time of `C0 TP` grows 0.0871 s per mm of `tp − tz`. The page goes down to `tp` at the Z drive's speed and presses on to `tz` at 1 / 0.0871 = **11.5 mm/s**. Only two press lengths are recorded (8 and 10 mm).
- **Tip pick-up overlap:** the descent to `tp` sets off when 82% of the crossing's time has passed (R² of the fixed time 0.830 against 0.810 in sequence, ΔAIC 7,234). Going from 9 to 18 mm adds 116 ms of X travel but only 7 ms to the command; 27 mm adds 198 ms but 112 ms. Nearly every recorded pick-up moves 9 mm (48,024 of 48,172), so the share is loosely determined. Drops can't be checked: hardly any travel without a Y move.
- **Correction (2026-09-26):** a tip command sends `tp` and `tz` once for all channels. An earlier version of the tool read them as channel 0's only and timed the other channels' descents from Z = 0, which made the pick-up and drop fixed times about 3 s too small (1.46 and 3.06 s). The aspirate, whose heights are always per channel, was unaffected, as are the ripple and Z fits in 8.4.

### 8.7 The 96-head

`C0 EM/EP/ER` following another head command (7,182 pairs, 86 groups). Z heights are fixed within each group, so head Z can't be identified here. The head's X and Y overlap (ΔAIC ≈ 11,100 over X-then-Y at the same constants).

| Model | R² within | RMS | AIC | BIC |
|---|---|---|---|---|
| no travel terms | 0 | 268.7 ms | −18,705 | — |
| X then head Y, + Z, PLR defaults | −0.001 | 268.8 ms | −18,698 | −18,107 |
| **max(X, head Y) + Z, PLR defaults (Y 390.6 mm/s, 546.9 mm/s²; Z 85 mm/s, 400 mm/s²), no free parameter** | **0.787** | **124.1 ms** | **−29,803** | **−29,211** |
| max(X, head Y 300, 1500) + Z PLR, head Y fitted | 0.855 | 102.2 ms | −32,594 | −32,002 |

The fitted head constants slide along the grid edges (speed and acceleration trade off, and few moves reach cruise), so the page keeps **PLR's head defaults**. The data agrees with them (R² 0.79 with no free parameter); what it adds is the overlap of X and Y and the fixed times in 8.6.

### 8.8 The iSWAP

Venus sends whole moves (`C0 PP` get, `C0 PR` put); PLR sends the iSWAP's steps one by one (`R0` commands, each carrying its own speeds). So these fits describe the device, but don't map one-to-one onto PLR's steps.

| Command | Model (fixed effect per target site and grip parameters) | n | Groups | R² within | RMS | AIC | BIC |
|---|---|---|---|---|---|---|---|
| `C0 PP` | max(X, Y), Y 400 mm/s, 1200 mm/s² | 33,727 | 158 | 0.650 | 229 ms | −99,149 | −97,818 |
| `C0 PP` | **X then Y, Y 300 mm/s, 800 mm/s²** | 33,727 | 158 | **0.923** | **107 ms** | **−150,299** | **−148,967** |
| `C0 PR` | max(X, Y), Y 300 mm/s, 200 mm/s² | 47,384 | 201 | 0.507 | 120 ms | −200,479 | −198,717 |
| `C0 PR` | **X then Y, Y 300 mm/s, 800 mm/s²** | 47,384 | 201 | **0.815** | **74 ms** | **−246,796** | **−245,034** |

- **The iSWAP moves X first, then Y** (ΔAIC ≈ 51,000 for gets and ≈ 46,000 for puts). This is unlike the channels and the 96-head, which overlap X and Y.
- **iSWAP Y ≈ 300 mm/s, 800 mm/s² (FIT).** PLR sends 4,751 increments/s ≈ 220 mm/s, which it documents as 68% of the firmware's default (≈ 323 mm/s). Venus evidently runs near the documented default. PLR's plans carry their own speeds, so the page already plays PLR's slower Y; that is what PLR would do on the device.
- **Fixed times of the whole moves:** `C0 PP` 4.93 s and `C0 PR` 4.54 s for targets on the deck.
- **Far-right targets (X > 850 mm) take ≈ 8.5 s longer** (13.49 / 12.85 s fixed, 6,575 + 10,640 moves). They're all at X ≈ 890–910 mm, Y ≈ 244 or 421 mm, most likely off-deck or extended-reach positions where the firmware takes a longer arm path. Not modelled further.
- **Not yet known:** how a whole move's fixed time divides among PLR's `R0` steps (gripper open/close, wrist, Z). The page gives each step the generic 0.065 s.

### 8.9 The whole timing model, at a glance

| Factor | Value used by the page | Tag | Fit quality | PLR's value | Known for sure? |
|---|---|---|---|---|---|
| X-arm | S-curve 600 mm/s, 1297 mm/s², 3210 mm/s³ | FIT | R² 0.976, RMS 23 ms, AIC −640 (vs trapezoid −567) | none stated | Jerk yes; speed/acceleration loose above ≈450 / ≈1100 |
| Channel Y travel | trapezoid 300 mm/s, 900 mm/s² | FIT | RMS 4.7 ms on jogs; slope 1.01 in the full model | 250 mm/s | Yes for 1–10 mm; one 100 mm sample |
| Channel Y ripple | 0.118 s per channel after the first, channel order | FIT | best DS model: R² 0.9883, RMS 42.5 ms, AIC −1,366,602 | none (moves together) | Size yes (4/7/8 channels); order and distance dependence no |
| X and channel Y | overlap: max(X, Y) | FIT | AS, each with a fitted Y cost: R² 0.993 vs X-then-Y 0.983 (ΔAIC ≈ 212,000) | the simulator: at once | Yes |
| Channel Z | trapezoid 150 mm/s, 800 mm/s² | FIT (speed), PLR (acceleration) | R² 0.986, RMS 33 ms, AIC −224,741 | 125 mm/s, 800 mm/s² | Acceleration yes; speed loose, 150–200 |
| Aspiration dwell | volume ÷ flow + settle | FW | slope 0.99–1.02 | same | Yes |
| Mixing | 0.979 × 2·cycles·volume ÷ speed + 0.557 s per cycle | FIT | part of the AS model | not drawn before | Yes (20k mixes) |
| Tip press | 11.5 mm/s from `tp` to `tz` | FIT | TP fixed-time R² 0.83, RMS 38 ms | none (straight to `tz`) | Two press lengths only |
| Fixed times | per command, 8.6 | FIT / HEUR | 8.6 | none | Measured for AS, TP, TR, ZA, EP, ER; by analogy for the rest; when in the command it falls isn't known |
| 96-head X and Y | overlap: max(X, Y) | FIT | ΔAIC ≈ 11,100 over X-then-Y | the simulator: at once | Yes |
| 96-head Y, Z | PLR defaults (390.6 / 546.9; 85 / 400) | PLR | R² 0.79 with no free parameter | same | Not identified better by the traces |
| iSWAP X and Y | X then Y | FIT | ΔAIC ≈ 46,000–51,000 | PLR plans its own step order | Yes, for Venus's whole moves |
| iSWAP Y | PLR's own step speeds (≈220 mm/s) | PLR | Venus: 300 mm/s, 800 mm/s² (R² 0.92 / 0.82) | ≈220 mm/s | Yes for Venus; PLR deliberately slower |
| iSWAP step fixed time | 0.065 s per `R0` step | HEUR | — | none | No: whole moves measured (4.9 / 4.5 s), not their split

## 7. Liquid, as drawn (not a firmware command)
A vessel's cavity is tinted from white to orange by volume ÷ capacity, stepping to 35% for any liquid at all (PR #1378 page, `live.js` `refreshOverlays`; not ours). It changes when the model's state update arrives, after the command. Tips show their contents only in the 2D channel panel. Moving liquid (a surface lowered over the dwell, a column rising in the tip) is not drawn yet. The data for it are PLR's (`Container.compute_height_from_volume`) plus the `C0 AS` fields above.

---

## Sources
- PyLabRobot forum: [surface following, post 6](https://discuss.pylabrobot.org/t/hamilton-surface-following-issues/304/6); [z-touch firmware, posts 2–3](https://discuss.pylabrobot.org/t/reason-behind-z-touch-only-being-available-to-8-channels-made-in-2022-and-onwards/543/3); [Tame the iSWAP, post 1](https://discuss.pylabrobot.org/t/intro-to-epic-tame-the-iswap/517/1) and [post 4](https://discuss.pylabrobot.org/t/intro-to-epic-tame-the-iswap/517/4).
- labautomation.io: [side touch, post 6](https://labautomation.io/t/post-dispense-tip-touch-on-starlet/2255/6) and [post 9](https://labautomation.io/t/post-dispense-tip-touch-on-starlet/2255/9); [touch-off and the MPH, post 2](https://labautomation.io/t/touch-off-with-mph/1788/2); [Transport Paths Editor](https://labautomation.io/t/transport-paths-editor/5759).
- PyLabRobot PRs: [#1362 LLD aspirate](https://github.com/PyLabRobot/pylabrobot/pull/1362), [#1333 cLLD](https://github.com/PyLabRobot/pylabrobot/pull/1333), [#1334 pLLD](https://github.com/PyLabRobot/pylabrobot/pull/1334), [#1336 probing](https://github.com/PyLabRobot/pylabrobot/pull/1336), [#1352 96-head cLLD](https://github.com/PyLabRobot/pylabrobot/pull/1352), [#63 PLACE_SHIFT heights](https://github.com/PyLabRobot/pylabrobot/pull/63).
