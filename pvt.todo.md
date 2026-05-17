# PVT mode — interpolated motion for KX2

## Why bother

`move_to_location` today is joint-linear: `_cart_to_joints(target)` then a single
`motors_move_joint`. Cart-space path is curved; |J(q)·q_dot| varies along it.
Capping peak gripper speed = cap leaves mean ≈ 0.5–0.7·cap (peak/mean ratio of
the path). Validator confirms peak ≤ cap is hit exactly, but avg/cap stays at
0.1–0.7 even on long moves. There is no fix inside a single trapezoidal profile.

**Goal**: avg gripper speed ≈ peak ≈ cap during cruise. Two ways:
1. Cartesian-linear interpolation in `move_to_location` (gripper traces a cart
   straight line; |J·q_dot| ~ constant = cap).
2. Joint-linear path but time-varying joint velocity in PVT.

Either way, we need PVT (or PP-on-the-fly) on the drive — single-trapezoid PP
mode can't vary speed mid-move.

## What we already have

`pylabrobot/paa/kx2/driver.py` is more set up than I expected:

- `pvt_select_mode(enable)` — switches mode object `0x6060` between PP (1) and
  IP (7), and toggles the interpolation-buffer enable (`0x60C4 sub 6`).
  Currently called with `False` before every PP move (driver.py:842).
- `setup()` configures Elmo vendor objects for PVT (driver.py:325–332):
  - `24768.0 = -1`  — interpolation submode (Elmo-specific)
  - `24772.2 = 16`  — buffer size (16 deep)
  - `24772.5 = 8`   — points-per-block? (verify in Elmo docs)
  - `24770.2 = -3`  — time-base exponent (10⁻³ s = ms ticks)
  - `24669.0 = 1`   — enable interpolation
- RPDO3 is already mapped to `(0x60C1.01 TargetPositionIP, 0x60C1.02 TargetVelocityIP)`
  with EventDrivenDev transmission — so we can shove a new PVT point with one
  PDO write per axis.
- TPDO mapping exists for `PVTHeadPointer (0x2F11)` and `PVTTailPointer (0x2F12)`
  — buffer occupancy is observable.

So the protocol-level scaffolding is in place. The missing pieces are
**trajectory generation**, **buffer-fill loop**, and **planner integration**.

## Two implementation flavors

### Flavor A — Cartesian-linear `move_to_location`

Scope: `move_to_location` only. `move_to_joint_position` stays joint-linear
(joint-linear is the right thing for a joint-space move).

1. Sample the cart line at fine resolution (every ~5 mm or so).
2. IK each sample, snapping wrist to the previous so we don't flip mid-move.
3. Compute joint velocities at each sample by finite-difference of consecutive
   joint positions divided by segment time. Time per segment = segment cart
   length / cap.
4. Stream `(joint_pos, joint_vel, dt)` per axis into the PVT FIFO.

avg/cap ≈ 1 modulo accel ramps at the endpoints, which we still pay (start at
v=0, end at v=0). For long moves this overhead is negligible.

### Flavor B — joint-linear with constant-cart-speed PVT

Scope: cap helper. The path is the same joint-linear path the planner already
uses; just vary joint velocity over time so |J·q_dot| stays at cap.

1. Sample the joint-linear path at high resolution.
2. At each sample, compute the joint velocity scale that pins gripper speed at
   cap: `q_dot(α) = q_dot_unit · cap / |J(q(α)) · q_dot_unit|`, capped by
   firmware ceiling per axis.
3. Convert to (joint_pos, joint_vel, dt) tuples (dt = arc-length-in-joint /
   |q_dot|), stream into PVT.

Easier in some ways (no extra IK calls — the trajectory IS the joint line),
but the gripper's cart path is still curved, so this only fixes avg vs peak,
not "the gripper moves in a straight line." Probably less useful for actual
plate handling than A.

**Recommended default: A.** Plate handling cares about predictable cart
trajectories. B is theoretically interesting but doesn't change *where* the
gripper goes, only how fast it goes there.

## Implementation sketch

### `KX2Driver` (driver.py)

New methods:

```python
async def pvt_push_point(
  self, node_id: int, position: int, velocity: int, dt_ms: int,
) -> None: ...

async def pvt_buffer_free(self, node_id: int) -> int: ...
  # Read head - tail (modulo buffer size) from TPDO cache.

async def pvt_start(self, node_ids: List[int]) -> None: ...
  # Switch to mode 7, enable IP buffer (0x60C4 sub 6 = 1), set CW = enable.

async def pvt_wait_until_drained(self, node_ids: List[int], timeout: float): ...
  # Done = head == tail and motion complete.

async def pvt_stop(self, node_ids: List[int]) -> None: ...
  # Disable buffer, switch back to mode 1 (PP).
```

Buffer fill: tight async loop that watches `pvt_buffer_free()` per axis, pushes
points whenever there's headroom, sleeps a few ms otherwise. PVT FIFO is 16
deep per axis — refill should comfortably keep up at the planner-side rates.

### `KX2ArmBackend` (arm_backend.py)

New planner sibling to `calculate_move_abs_all_axes`:

```python
async def calculate_move_pvt_cartesian(
  self,
  start_pose: KX2GripperLocation,
  end_pose: KX2GripperLocation,
  cap: float,
) -> _PVTPlan: ...
```

Returns per-axis lists of (pos, vel, t) tuples in encoder/firmware units. The
existing `_profile`-based planner is untouched.

`move_to_location` decision tree:
- `backend_params.linear_cart_motion = True` (new flag, default True): use PVT.
- Else: existing joint-linear PP.

`move_to_joint_position` always uses PP. (Joint moves don't have a "straight
line" semantic.)

### Tests

- New unit test for trajectory generator: feed a known cart line, check
  resulting joint waypoints reproject (FK) within tolerance to the line.
- Integration test on real arm: random A→B Cartesian moves at random caps,
  validator measures peak via fine-resolution FK sweep over the actual
  PVT joint trajectory; expect avg/cap ≥ 0.9 on moves >100 mm.

## Gotchas / unknowns

- **Segment time quantum.** Vendor object `24770.2 = -3` sets time exponent to
  10⁻³ s, so segment dt is in milliseconds. Verify the resolution and the
  max representable dt in one PDO before designing the segmentation step.
- **Velocity unit.** `0x60C1.02 TargetVelocityIP` units may differ from
  `0x60FF Target Velocity` in PV mode (Elmo sometimes uses
  position-units-per-IP-period vs counts/sec). Check before scaling.
- **First/last segment.** PVT cubic-Hermite interp wants v at both ends. First
  segment must start at v=0 (drive is at rest). Last segment must end at v=0.
  In between, velocity is whatever the cart-speed-at-this-config calls for.
- **IK continuity.** Wrist sign and J1/J4 unwrapping must stay consistent
  across consecutive cart samples. `snap_to_current` handles single-call
  snapping; for streamed points we'd snap each to the *previous PVT sample*,
  not to current measured joint pos.
- **Buffer underrun.** If we push slow and the FIFO drains, the drive faults
  (Elmo MF for IP underflow). Fill aggressively — point preparation is much
  faster than CAN throughput so we should be safe, but worth instrumenting.
- **Buffer overrun.** Drive ignores writes past `head + buffer_size`. Must
  read-back head/tail before each push.
- **Mid-move halt.** `halt()` must work in PVT mode too — switch back to PP
  with current commanded position as the new target, or just rely on CW=quick-
  stop. Verify on hardware.
- **Sync across axes.** Each axis has its own PVT FIFO; they advance on the
  drive's IP-period clock. With per-axis EventDrivenDev RPDOs, alignment
  between axes is best-effort. May need SynchronousCyclic with SYNC frames if
  jitter shows up. Prototype event-driven first; only switch to SYNC if cart
  path drifts from the intended line.

## Out of scope / future

- Blending across multiple `move_to_location` calls (so a pick→approach→drop
  sequence becomes one continuous PVT stream with no stop between sub-moves).
  Big UX win for assays but a lot more state to manage.
- Speed/accel limits beyond the gripper-speed cap (per-axis torque/jerk caps).
- Online replanning if the path is blocked (collision avoidance) — way out of
  scope, but PVT does enable it because we control velocity in real time.

## References

- CiA 402 §10.4 (Interpolated Position Mode) — defines `0x60C0`, `0x60C1`,
  `0x60C4`.
- Elmo Application Note "Interpolated Position Mode (PVT)" — vendor
  objects 0x21C0–0x21C7 documenting the buffer protocol and mode submodes.
- Existing PP planner: `KX2ArmBackend.calculate_move_abs_all_axes`
  (arm_backend.py:759).
- Existing PVT scaffolding: `pvt_select_mode` (driver.py:842), setup at
  driver.py:325–347.
