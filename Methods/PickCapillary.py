"""Pick a capillary from Stack1Bottom and stop 50 mm above it.

Short worklist based on ``CapillaryToPanta.py``. Stops after:

  1. Opens the servo gripper fully
  2. Moves to ``lift_mm`` above ``Stack1Bottom``
  3. Moves down to ``Stack1Bottom``
  4. Closes the gripper to ``CLOSED_MM``
  5. Lifts to ``lift_mm`` above ``Stack1Bottom``

Usage (Jupyter or async script)::

    from Methods.PickCapillary import run_pick_capillary
    await run_pick_capillary()

Or from the command line::

    python Methods/PickCapillary.py
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Optional

from pylabrobot.paa.kx2 import Axis, KX2

try:
  from Methods.CapillaryToPanta import (  # type: ignore[import-not-found]
    CLOSED_MM,
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
    CLOSED_MM,
    CLOSE_FORCE_PERCENT,
    DEFAULT_PANTA_DOCS,
    GRIPPER_CONFIG_FILENAME,
    LIFT_MM,
    STACK1_BOTTOM,
    TEACH_POINTS_FILENAME,
    _with_z_lift,
    load_teach_points,
  )


async def run_pick_capillary(
  *,
  panta_docs: Path = DEFAULT_PANTA_DOCS,
  lift_mm: float = LIFT_MM,
  check_plate_gripped: bool = False,
  max_gripper_speed: Optional[float] = None,
  max_gripper_acceleration: Optional[float] = None,
  kx2: Optional[KX2] = None,
) -> None:
  """Open gripper, pick at ``Stack1Bottom``, lift ``lift_mm``, then stop."""
  teach_path = Path(panta_docs) / TEACH_POINTS_FILENAME
  gripper_path = Path(panta_docs) / GRIPPER_CONFIG_FILENAME

  teach_points = load_teach_points(teach_path)
  closed_mm = CLOSED_MM

  if STACK1_BOTTOM not in teach_points:
    raise KeyError(
      f"{teach_path}: missing teach point {STACK1_BOTTOM!r}. "
      f"Found: {sorted(teach_points)}"
    )

  pick = teach_points[STACK1_BOTTOM]
  pick_above = _with_z_lift(pick, lift_mm)

  owns_arm = kx2 is None
  arm = kx2 if kx2 is not None else KX2()

  print(f"Teach points: {teach_path}")
  print(f"Gripper config: {gripper_path}")
  print(f"  closed={closed_mm} mm  close_force={CLOSE_FORCE_PERCENT}%")
  print(f"  pick above {STACK1_BOTTOM} (+{lift_mm} mm Z): {pick_above}")
  print(f"  pick {STACK1_BOTTOM}: {pick}")

  try:
    if owns_arm:
      await arm.setup()
      print("KX2 setup complete")

    move_kw = dict(
      max_gripper_speed=max_gripper_speed,
      max_gripper_acceleration=max_gripper_acceleration,
    )

    full_open_mm = arm._cfg.axes[Axis.SERVO_GRIPPER].max_travel

    print(f"Opening gripper fully ({full_open_mm} mm)...")
    await arm.open_gripper(full_open_mm)
    print(f"  gripper now {await arm.motor_get_current_position(Axis.SERVO_GRIPPER):.2f} mm")

    print(f"Moving to {lift_mm} mm above {STACK1_BOTTOM}...")
    await arm.move_to_joint_position(pick_above, **move_kw)

    print(f"Moving down to {STACK1_BOTTOM}...")
    await arm.move_to_joint_position(pick, **move_kw)

    print(f"Closing gripper to {closed_mm} mm (force={CLOSE_FORCE_PERCENT}%)...")
    await arm.close_gripper(
      closed_mm,
      check_plate_gripped=check_plate_gripped,
      max_force_percent=CLOSE_FORCE_PERCENT,
    )
    print(f"  gripper now {await arm.motor_get_current_position(Axis.SERVO_GRIPPER):.2f} mm")

    print(f"Lifting to {lift_mm} mm above {STACK1_BOTTOM}...")
    await arm.move_to_joint_position(pick_above, **move_kw)
    print("Done (holding capillary above stack).")
  finally:
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
    help=f"Z clearance above stack after pick (default: {LIFT_MM})",
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
    run_pick_capillary(
      panta_docs=args.panta_docs,
      lift_mm=args.lift_mm,
      check_plate_gripped=args.check_plate_gripped,
      max_gripper_speed=args.max_gripper_speed,
      max_gripper_acceleration=args.max_gripper_acceleration,
    )
  )


if __name__ == "__main__":
  main()
