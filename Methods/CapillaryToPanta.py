"""Pick a capillary from Stack1Bottom and place it on the Panta teach point.

Reads Peak/KX2 ``TeachPoints.ini`` and ``GripperConfig.ini``, then:

  1. Opens the servo gripper fully (axis max travel)
  2. Moves to ``lift_mm`` above ``Stack1Bottom``
  3. Moves down to ``Stack1Bottom``
  4. Closes the gripper to ``CLOSED_MM``
  5. Lifts to ``lift_mm`` above ``Stack1Bottom``
  6. Moves to ``lift_mm`` above ``Panta``
  7. Moves down to ``Panta``
  8. Opens the gripper fully
  9. Retracts to ``lift_mm`` above ``Panta``

Requires the PAA KX2 driver (``pylabrobot.paa.kx2``), available on the
``kx2-backend`` branch. Install CAN deps with::

    pip install "pylabrobot[canopen]"

Usage (Jupyter or async script)::

    from Methods.CapillaryToPanta import run_capillary_to_panta
    await run_capillary_to_panta()

Or from the command line::

    python Methods/CapillaryToPanta.py
"""

from __future__ import annotations

import argparse
import asyncio
import configparser
from pathlib import Path
from typing import Dict, Mapping, Optional, Union

from pylabrobot.paa.kx2 import Axis, KX2

DEFAULT_PANTA_DOCS = Path(r"C:\Program Files (x86)\PAA\Overlord3\KX2")
TEACH_POINTS_FILENAME = "TeachPoints.ini"
GRIPPER_CONFIG_FILENAME = "GripperConfig.ini"

STACK1_BOTTOM = "Stack1Bottom"
PANTA = "Panta"
LIFT_MM = 50.0
CLOSED_MM = 21.5
# Peak's ServoGripperForcePercent (10%) is a grip squeeze limit, not a
# transit current. Closing 26 mm -> 2 mm at 10% leaves the jaws stuck open.
CLOSE_FORCE_PERCENT = 30

# TeachPoints.ini Axis1..Axis4 <-> KX2 motion axes (see GripperConfig
# DriveParamCache ShoulderAx/ZAx/ElbowAx/WristAx).
_TEACH_AXIS_MAP = {
  "Axis1": Axis.SHOULDER,
  "Axis2": Axis.Z,
  "Axis3": Axis.ELBOW,
  "Axis4": Axis.WRIST,
}

JointPosition = Dict[Union[Axis, int], float]


def _read_ini(path: Path) -> configparser.ConfigParser:
  if not path.is_file():
    raise FileNotFoundError(f"INI file not found: {path}")
  parser = configparser.ConfigParser()
  # Peak writes keys with inconsistent casing / spacing; keep names as-is.
  parser.optionxform = str  # type: ignore[assignment]
  parser.read(path)
  return parser


def load_teach_points(path: Path) -> Dict[str, JointPosition]:
  """Parse ``TeachPoints.ini`` into ``{name: {Axis: value}}`` joint poses."""
  parser = _read_ini(path)
  points: Dict[str, JointPosition] = {}

  for section in parser.sections():
    if not section.startswith("Point "):
      continue
    name = parser.get(section, "Name", fallback="").strip()
    if not name:
      continue
    joints: JointPosition = {}
    for key, axis in _TEACH_AXIS_MAP.items():
      if not parser.has_option(section, key):
        raise KeyError(f"{path}: [{section}] ({name}) missing {key}")
      joints[axis] = parser.getfloat(section, key)
    points[name] = joints

  return points


def load_gripper_widths(path: Path) -> tuple[float, float, int]:
  """Return ``(open_mm, closed_mm, force_percent)`` from ``GripperConfig.ini``."""
  parser = _read_ini(path)
  if "Settings" not in parser:
    raise KeyError(f"{path}: missing [Settings] section")

  settings = parser["Settings"]
  open_mm = float(settings["ServoGripperOpenPosition"])
  closed_mm = float(settings["ServoGripperClosedPosition"])
  force_percent = int(float(settings.get("ServoGripperForcePercent", "10")))
  return open_mm, closed_mm, force_percent


def _with_z_lift(joints: Mapping[Union[Axis, int], float], lift_mm: float) -> JointPosition:
  """Copy a joint pose and raise the Z axis by ``lift_mm``."""
  lifted = {Axis(int(k)): float(v) for k, v in joints.items()}
  lifted[Axis.Z] = lifted[Axis.Z] + lift_mm
  return lifted


async def run_capillary_to_panta(
  *,
  panta_docs: Path = DEFAULT_PANTA_DOCS,
  lift_mm: float = LIFT_MM,
  check_plate_gripped: bool = False,
  max_gripper_speed: Optional[float] = None,
  max_gripper_acceleration: Optional[float] = None,
  kx2: Optional[KX2] = None,
) -> None:
  """Approach/pick/place with ``lift_mm`` clearance above stack and Panta.

  Args:
    panta_docs: Directory containing ``TeachPoints.ini`` and ``GripperConfig.ini``.
    lift_mm: Z clearance used above ``Stack1Bottom`` and ``Panta`` for approach
      and retract moves.
    check_plate_gripped: Pass through to :meth:`KX2.close_gripper`. Capillaries
      are not plates; default False avoids a false "no plate" fault.
    max_gripper_speed: Optional Cartesian speed cap (mm/s) for arm moves.
    max_gripper_acceleration: Optional Cartesian accel cap (mm/s^2).
    kx2: Existing connected arm; if None, construct one and call ``setup``/``stop``.
  """
  teach_path = Path(panta_docs) / TEACH_POINTS_FILENAME
  gripper_path = Path(panta_docs) / GRIPPER_CONFIG_FILENAME

  teach_points = load_teach_points(teach_path)
  closed_mm = CLOSED_MM

  for required in (STACK1_BOTTOM, PANTA):
    if required not in teach_points:
      raise KeyError(
        f"{teach_path}: missing teach point {required!r}. "
        f"Found: {sorted(teach_points)}"
      )

  pick = teach_points[STACK1_BOTTOM]
  pick_above = _with_z_lift(pick, lift_mm)
  place = teach_points[PANTA]
  place_above = _with_z_lift(place, lift_mm)

  owns_arm = kx2 is None
  arm = kx2 if kx2 is not None else KX2()

  print(f"Teach points: {teach_path}")
  print(f"Gripper config: {gripper_path}")
  print(f"  closed={closed_mm} mm  close_force={CLOSE_FORCE_PERCENT}%")
  print(f"  pick above {STACK1_BOTTOM} (+{lift_mm} mm Z): {pick_above}")
  print(f"  pick {STACK1_BOTTOM}: {pick}")
  print(f"  place above {PANTA} (+{lift_mm} mm Z): {place_above}")
  print(f"  place {PANTA}: {place}")

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

    print(f"Moving to {lift_mm} mm above {PANTA}...")
    await arm.move_to_joint_position(place_above, **move_kw)

    print(f"Moving down to {PANTA}...")
    await arm.move_to_joint_position(place, **move_kw)

    print(f"Opening gripper fully ({full_open_mm} mm)...")
    await arm.open_gripper(full_open_mm)

    print(f"Retracting to {lift_mm} mm above {PANTA}...")
    await arm.move_to_joint_position(place_above, **move_kw)
    print("Done.")
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
    help=f"Z clearance above stack/Panta for approach and retract (default: {LIFT_MM})",
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
    run_capillary_to_panta(
      panta_docs=args.panta_docs,
      lift_mm=args.lift_mm,
      check_plate_gripped=args.check_plate_gripped,
      max_gripper_speed=args.max_gripper_speed,
      max_gripper_acceleration=args.max_gripper_acceleration,
    )
  )


if __name__ == "__main__":
  main()
