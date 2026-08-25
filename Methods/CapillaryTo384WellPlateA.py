"""Pick a capillary from Stack1Bottom, visit a 384-well (A–H), return.

Set ``WELL`` below to the row letter. Teach points used:

  ``384WellPlate{WELL}-Approach``
  ``384WellPlate{WELL}``

Reads Peak/KX2 ``TeachPoints.ini`` and ``GripperConfig.ini``, then:

  1. Opens the servo gripper fully (axis max travel)
  2. Moves to ``lift_mm`` above ``Stack1Bottom``
  3. Moves down to ``Stack1Bottom``
  4. Closes the gripper to ``CLOSED_MM`` (21.2 mm)
  5. Lifts to ``lift_mm`` above ``Stack1Bottom``
  6. Moves to ``Approach384WellPlate1``
  7. Moves to ``Approach384WellPlate2``
  8. Moves to ``384WellPlate{WELL}-Approach``
  9. Waits ``SETTLE_S`` seconds (capillary settle after the Approach2 swing)
  10. Moves to ``384WellPlate{WELL}``
  11. Waits ``dwell_s`` seconds
  12. Reverse: well approach -> ``Approach384WellPlate2`` ->
      ``Approach384WellPlate1``
  13. Moves to ``lift_mm`` above ``Stack1Bottom``
  14. Moves down to ``Stack1Bottom``
  15. Opens the gripper fully (drop)
  16. Retracts to ``lift_mm`` above ``Stack1Bottom``

Arm joint moves run at ``SPEED_PERCENT`` (10%) of firmware max velocity
*and* acceleration, with ``linear_joint=True``. Rotary teach values are
snapped onto the live 360° wrap so ``Approach384WellPlate2`` (~337°) ->
well approach (~4°) is a short +27° turn, not a 333° unwind.

Requires the PAA KX2 driver (``pylabrobot.paa.kx2``), available on the
``kx2-backend`` branch. Install CAN deps with::

    pip install "pylabrobot[canopen]"

Usage (Jupyter or async script)::

    from Methods.CapillaryTo384WellPlateA import run_capillary_to_384_well
    await run_capillary_to_384_well()  # uses WELL, or pass well="H"

Or from the command line::

    python Methods/CapillaryTo384WellPlateA.py

Step through each move with a keypress::

    python Methods/CapillaryTo384WellPlateA.py --step
    python Methods/CapillaryTo384WellPlateAStep.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Dict, Optional

from pylabrobot.paa.kx2 import Axis, KX2
from pylabrobot.paa.kx2 import kinematics as kx2_kinematics

try:
  from Methods.CapillaryToPanta import (  # type: ignore[import-not-found]
    CLOSE_FORCE_PERCENT,
    DEFAULT_PANTA_DOCS,
    GRIPPER_CONFIG_FILENAME,
    LIFT_MM,
    STACK1_BOTTOM,
    TEACH_POINTS_FILENAME,
    _with_z_lift,
    load_teach_points,
  )
except ImportError:
  from CapillaryToPanta import (  # type: ignore[no-redef]
    CLOSE_FORCE_PERCENT,
    DEFAULT_PANTA_DOCS,
    GRIPPER_CONFIG_FILENAME,
    LIFT_MM,
    STACK1_BOTTOM,
    TEACH_POINTS_FILENAME,
    _with_z_lift,
    load_teach_points,
  )

# Row letter on the 384-well plate (A–H). Teach points are
# ``384WellPlate{WELL}`` and ``384WellPlate{WELL}-Approach``.
WELL = "F"

CLOSED_MM = 21.3
APPROACH_384_1 = "Approach384WellPlate1"
APPROACH_384_2 = "Approach384WellPlate2"
WELL_LETTERS = "ABCDEFGH"
DWELL_S = 30.0
SETTLE_S = 1.0
SPEED_PERCENT = 10.0


def _wait_for_key() -> bool:
  """Block until Space/Enter (continue) or q (stop). Returns False to quit."""
  print("  [Space/Enter] next step    [q] quit")
  sys.stdout.flush()
  if sys.platform == "win32":
    import msvcrt
    while True:
      ch = msvcrt.getch()
      if ch in (b"\x00", b"\xe0"):
        msvcrt.getch()
        continue
      if ch in (b"q", b"Q"):
        return False
      if ch in (b"\r", b"\n", b" "):
        return True
  line = input().strip().lower()
  return line != "q"


def _normalize_well(letter: str) -> str:
  well = letter.strip().upper()
  if well not in WELL_LETTERS:
    raise ValueError(
      f"WELL must be a 384-well row A–H, got {letter!r}. "
      f"Valid: {', '.join(WELL_LETTERS)}"
    )
  return well


def _well_teach_names(letter: str) -> tuple[str, str]:
  well = _normalize_well(letter)
  return f"384WellPlate{well}", f"384WellPlate{well}-Approach"


def _is_untaught(joints: Dict) -> bool:
  return all(abs(float(v)) < 1e-6 for v in joints.values())


def _pause(label: str) -> None:
  print(f"\nNext: {label}")
  if not _wait_for_key():
    raise KeyboardInterrupt("stopped by user")


def _scale_arm_limits(arm: KX2, percent: float) -> Dict[Axis, tuple[float, float]]:
  """Set motion-axis max vel/accel to ``percent`` of firmware max. Returns originals."""
  scale = percent / 100.0
  originals: Dict[Axis, tuple[float, float]] = {}
  for ax, ax_cfg in arm._cfg.axes.items():
    if not ax.is_motion:
      continue
    originals[ax] = (ax_cfg.max_vel, ax_cfg.max_accel)
    ax_cfg.max_vel = ax_cfg.max_vel * scale
    ax_cfg.max_accel = ax_cfg.max_accel * scale
  return originals


def _restore_arm_limits(arm: KX2, originals: Dict[Axis, tuple[float, float]]) -> None:
  for ax, (vel, accel) in originals.items():
    arm._cfg.axes[ax].max_vel = vel
    arm._cfg.axes[ax].max_accel = accel


def _snap_teach(current: Dict[Axis, float], target: Dict) -> Dict[Axis, float]:
  """Map 0–360° teach values onto the live shoulder/wrist revolution."""
  joints = {Axis(int(k)): float(v) for k, v in target.items()}
  return kx2_kinematics.snap_to_current(joints, current)


async def _move_to_teach_point(arm: KX2, target: Dict, **move_kw) -> Dict[Axis, float]:
  current = {
    Axis(int(k)): float(v) for k, v in (await arm.request_joint_position()).items()
  }
  snapped = _snap_teach(current, target)
  await arm.move_to_joint_position(snapped, **move_kw)
  return snapped


async def run_capillary_to_384_well(
  *,
  well: str = WELL,
  panta_docs: Path = DEFAULT_PANTA_DOCS,
  lift_mm: float = LIFT_MM,
  dwell_s: float = DWELL_S,
  settle_s: float = SETTLE_S,
  closed_mm: float = CLOSED_MM,
  speed_percent: float = SPEED_PERCENT,
  check_plate_gripped: bool = False,
  max_gripper_speed: Optional[float] = None,
  max_gripper_acceleration: Optional[float] = None,
  kx2: Optional[KX2] = None,
  step: bool = False,
) -> None:
  """Pick at ``Stack1Bottom``, poke 384-well ``well`` (A–H), dwell, return, drop.

  Args:
    well: Row letter A–H. Selects ``384WellPlate{well}`` teach points.
    panta_docs: Directory containing ``TeachPoints.ini`` and ``GripperConfig.ini``.
    lift_mm: Z clearance used above ``Stack1Bottom`` for approach and retract.
    dwell_s: Seconds to wait at the well before reversing.
    settle_s: Seconds to wait at the well approach before the poke.
    closed_mm: Servo gripper width used to hold the capillary.
    speed_percent: Arm joint vel/accel as a percent of firmware max (default 10).
    check_plate_gripped: Pass through to :meth:`KX2.close_gripper`. Capillaries
      are not plates; default False avoids a false "no plate" fault.
    max_gripper_speed: Optional Cartesian speed cap (mm/s) for arm moves.
    max_gripper_acceleration: Optional Cartesian accel cap (mm/s^2).
    kx2: Existing connected arm; if None, construct one and call ``setup``/``stop``.
    step: If True, wait for Space/Enter before each move (q quits).
  """
  well = _normalize_well(well)
  well_name, well_approach_name = _well_teach_names(well)

  teach_path = Path(panta_docs) / TEACH_POINTS_FILENAME
  gripper_path = Path(panta_docs) / GRIPPER_CONFIG_FILENAME

  teach_points = load_teach_points(teach_path)

  required = (STACK1_BOTTOM, APPROACH_384_1, APPROACH_384_2, well_approach_name, well_name)
  for name in required:
    if name not in teach_points:
      raise KeyError(
        f"{teach_path}: missing teach point {name!r}. "
        f"Found: {sorted(teach_points)}"
      )
    if name in (well_name, well_approach_name) and _is_untaught(teach_points[name]):
      raise ValueError(
        f"{teach_path}: {name!r} is still 0,0,0,0 (not taught). "
        f"Teach it on the Peak pendant before running well {well}."
      )

  pick = teach_points[STACK1_BOTTOM]
  pick_above = _with_z_lift(pick, lift_mm)
  approach1 = teach_points[APPROACH_384_1]
  approach2 = teach_points[APPROACH_384_2]
  approach_well = teach_points[well_approach_name]
  well_pose = teach_points[well_name]

  owns_arm = kx2 is None
  arm = kx2 if kx2 is not None else KX2()

  print(f"Teach points: {teach_path}")
  print(f"Gripper config: {gripper_path}")
  print(f"  well={well}  ({well_name})")
  print(f"  closed={closed_mm} mm  close_force={CLOSE_FORCE_PERCENT}%")
  print(f"  pick above {STACK1_BOTTOM} (+{lift_mm} mm Z): {pick_above}")
  print(f"  pick {STACK1_BOTTOM}: {pick}")
  print(f"  {APPROACH_384_1}: {approach1}")
  print(f"  {APPROACH_384_2}: {approach2}")
  print(f"  {well_approach_name}: {approach_well}")
  print(f"  {well_name}: {well_pose}")
  print(f"  dwell at {well_name}: {dwell_s} s")
  print(f"  settle before poke: {settle_s:g} s")
  print(f"  arm vel/accel: {speed_percent:g}% of firmware max")
  if step:
    print("  STEP MODE: Space or Enter for each move, q to quit")

  original_limits: Optional[Dict[Axis, tuple[float, float]]] = None
  try:
    if owns_arm:
      await arm.setup()
      print("KX2 setup complete")

    original_limits = _scale_arm_limits(arm, speed_percent)

    move_kw = dict(
      max_gripper_speed=max_gripper_speed,
      max_gripper_acceleration=max_gripper_acceleration,
      linear_joint=True,
    )

    full_open_mm = arm._cfg.axes[Axis.SERVO_GRIPPER].max_travel

    def pause(label: str) -> None:
      if step:
        _pause(label)

    async def go(label: str, pose: Dict) -> None:
      pause(label)
      print(f"{label}...")
      await _move_to_teach_point(arm, pose, **move_kw)

    pause(f"Open gripper fully ({full_open_mm:.1f} mm)")
    print(f"Opening gripper fully ({full_open_mm} mm)...")
    await arm.open_gripper(full_open_mm)
    print(f"  gripper now {await arm.motor_get_current_position(Axis.SERVO_GRIPPER):.2f} mm")

    await go(f"Move to {lift_mm:g} mm above {STACK1_BOTTOM}", pick_above)
    await go(f"Move down to {STACK1_BOTTOM}", pick)

    pause(f"Close gripper to {closed_mm} mm")
    print(f"Closing gripper to {closed_mm} mm (force={CLOSE_FORCE_PERCENT}%)...")
    await arm.close_gripper(
      closed_mm,
      check_plate_gripped=check_plate_gripped,
      max_force_percent=CLOSE_FORCE_PERCENT,
    )
    print(f"  gripper now {await arm.motor_get_current_position(Axis.SERVO_GRIPPER):.2f} mm")

    await go(f"Lift to {lift_mm:g} mm above {STACK1_BOTTOM}", pick_above)
    await go(f"Move to {APPROACH_384_1}", approach1)
    await go(f"Move to {APPROACH_384_2}", approach2)
    await go(f"Move to {well_approach_name}", approach_well)
    print(f"Settling {settle_s:g} s before poke...")
    await asyncio.sleep(settle_s)
    await go(f"Move to {well_name}", well_pose)

    if step:
      pause("Retract from well (skipping timed dwell)")
    else:
      print(f"Waiting {dwell_s} s at {well_name}...")
      await asyncio.sleep(dwell_s)

    await go(f"Move to {well_approach_name}", approach_well)
    await go(f"Move to {APPROACH_384_2}", approach2)
    await go(f"Move to {APPROACH_384_1}", approach1)
    await go(f"Move to {lift_mm:g} mm above {STACK1_BOTTOM}", pick_above)
    await go(f"Move down to {STACK1_BOTTOM}", pick)

    pause(f"Open gripper fully ({full_open_mm:.1f} mm)")
    print(f"Opening gripper fully ({full_open_mm} mm)...")
    await arm.open_gripper(full_open_mm)

    await go(f"Retract to {lift_mm:g} mm above {STACK1_BOTTOM}", pick_above)
    print("Done.")
  finally:
    if original_limits is not None:
      _restore_arm_limits(arm, original_limits)
    if owns_arm:
      await arm.stop()


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--panta-docs",
    type=Path,
    default=DEFAULT_PANTA_DOCS,
    help=f"Folder with TeachPoints.ini and GripperConfig.ini (default: {DEFAULT_PANTA_DOCS})",
  )
  parser.add_argument(
    "--lift-mm",
    type=float,
    default=LIFT_MM,
    help=f"Z clearance above Stack1Bottom for approach and retract (default: {LIFT_MM})",
  )
  parser.add_argument(
    "--well",
    default=WELL,
    help=f"384-well row letter A–H (default: {WELL})",
  )
  parser.add_argument(
    "--dwell-s",
    type=float,
    default=DWELL_S,
    help=f"Seconds to wait at the well before reversing (default: {DWELL_S})",
  )
  parser.add_argument(
    "--settle-s",
    type=float,
    default=SETTLE_S,
    help=f"Seconds to wait at the well approach before the poke (default: {SETTLE_S})",
  )
  parser.add_argument(
    "--closed-mm",
    type=float,
    default=CLOSED_MM,
    help=f"Gripper width used to hold the capillary (default: {CLOSED_MM})",
  )
  parser.add_argument(
    "--speed-percent",
    type=float,
    default=SPEED_PERCENT,
    help=f"Arm joint vel/accel as percent of firmware max (default: {SPEED_PERCENT})",
  )
  parser.add_argument(
    "--step",
    action="store_true",
    help="Wait for Space/Enter before each move (q quits).",
  )
  parser.add_argument(
    "--check-plate-gripped",
    action="store_true",
    help="Enable KX2 plate-present check after close (off by default for capillaries).",
  )
  parser.add_argument(
    "--max-gripper-speed",
    type=float,
    default=None,
    help="Optional Cartesian speed cap at the gripper (mm/s).",
  )
  parser.add_argument(
    "--max-gripper-acceleration",
    type=float,
    default=None,
    help="Optional Cartesian acceleration cap at the gripper (mm/s^2).",
  )
  return parser.parse_args()


def main() -> None:
  args = _parse_args()
  asyncio.run(
    run_capillary_to_384_well(
      well=args.well,
      panta_docs=args.panta_docs,
      lift_mm=args.lift_mm,
      dwell_s=args.dwell_s,
      settle_s=args.settle_s,
      closed_mm=args.closed_mm,
      speed_percent=args.speed_percent,
      check_plate_gripped=args.check_plate_gripped,
      max_gripper_speed=args.max_gripper_speed,
      max_gripper_acceleration=args.max_gripper_acceleration,
      step=args.step,
    )
  )


# Back-compat name used by older notebooks / the step wrapper.
run_capillary_to_384_well_plate_a = run_capillary_to_384_well


if __name__ == "__main__":
  main()
