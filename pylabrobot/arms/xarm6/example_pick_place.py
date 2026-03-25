"""xArm 6 Pick & Place Walkthrough

Interactive script that steps through:
1. Connecting and initializing the xArm 6
2. Homing the robot
3. Teaching pick, place, and safe positions via freedrive mode
4. Saving/loading taught positions to a local JSON file
5. Running a pick-and-place cycle with vertical access

On first run, you teach positions by hand. On subsequent runs, you can
reuse saved positions or re-teach them.

Usage:
  python -m pylabrobot.arms.xarm6.example_pick_place

Before running:
  - Install the xArm SDK: pip install xarm-python-sdk
  - Update ROBOT_IP below to match your xArm's IP address
  - Adjust TCP_OFFSET for your bio-gripper mount
  - Ensure the workspace is clear and safe
"""

import asyncio
import json
import os

from pylabrobot.arms.backend import VerticalAccess
from pylabrobot.arms.six_axis import SixAxisArm
from pylabrobot.arms.standard import CartesianCoords
from pylabrobot.arms.xarm6.xarm6_backend import XArm6Backend
from pylabrobot.resources import Coordinate, Rotation

# ── Configuration ──────────────────────────────────────────────
ROBOT_IP = "192.168.1.220"  # Change to your xArm's IP
TCP_OFFSET = (0, 0, 0, 0, 0, 0)  # Adjust for bio-gripper mount (x, y, z, roll, pitch, yaw)
POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "taught_positions.json")


def coords_to_dict(coords: CartesianCoords) -> dict:
  """Serialize a CartesianCoords to a JSON-safe dict."""
  return {
    "x": coords.location.x,
    "y": coords.location.y,
    "z": coords.location.z,
    "roll": coords.rotation.x,
    "pitch": coords.rotation.y,
    "yaw": coords.rotation.z,
  }


def dict_to_coords(d: dict) -> CartesianCoords:
  """Deserialize a dict back to CartesianCoords."""
  return CartesianCoords(
    location=Coordinate(x=d["x"], y=d["y"], z=d["z"]),
    rotation=Rotation(x=d["roll"], y=d["pitch"], z=d["yaw"]),
  )


def save_positions(positions: dict[str, CartesianCoords]) -> None:
  """Save taught positions to a JSON file."""
  data = {name: coords_to_dict(coords) for name, coords in positions.items()}
  with open(POSITIONS_FILE, "w") as f:
    json.dump(data, f, indent=2)
  print(f"  Positions saved to {POSITIONS_FILE}")


def load_positions() -> dict[str, CartesianCoords] | None:
  """Load taught positions from JSON file. Returns None if file doesn't exist."""
  if not os.path.exists(POSITIONS_FILE):
    return None
  with open(POSITIONS_FILE, "r") as f:
    data = json.load(f)
  return {name: dict_to_coords(d) for name, d in data.items()}


def print_position(name: str, coords: CartesianCoords) -> None:
  """Print a position nicely."""
  loc = coords.location
  rot = coords.rotation
  print(f"  {name}:")
  print(f"    Location: x={loc.x:.1f}, y={loc.y:.1f}, z={loc.z:.1f}")
  print(f"    Rotation: roll={rot.x:.1f}, pitch={rot.y:.1f}, yaw={rot.z:.1f}")


async def teach_positions(arm: SixAxisArm) -> dict[str, CartesianCoords]:
  """Enter freedrive mode and teach pick, place, and safe positions."""
  positions = {}

  print("\n=== Teach Positions (Freedrive) ===")
  print("The robot will enter freedrive mode. Gently guide it by hand.")
  input("Press Enter to enable freedrive mode...")
  await arm.freedrive_mode()
  print("Freedrive enabled.\n")

  # Teach pick position
  input("Move the robot to the PICK position, then press Enter...")
  pos = await arm.get_cartesian_position()
  positions["pick"] = pos
  print_position("Pick position saved", pos)
  print()

  # Teach place position
  input("Move the robot to the PLACE position, then press Enter...")
  pos = await arm.get_cartesian_position()
  positions["place"] = pos
  print_position("Place position saved", pos)
  print()

  # Teach safe position
  input("Move the robot to the SAFE position, then press Enter...")
  pos = await arm.get_cartesian_position()
  positions["safe"] = pos
  print_position("Safe position saved", pos)
  print()

  await arm.end_freedrive_mode()
  print("Freedrive disabled.")

  save_positions(positions)
  return positions


async def main():
  print("=" * 60)
  print("  xArm 6 Pick & Place Walkthrough")
  print("=" * 60)
  print()
  print(f"Robot IP:       {ROBOT_IP}")
  print(f"TCP Offset:     {TCP_OFFSET}")
  print(f"Positions file: {POSITIONS_FILE}")
  print()

  # ── Check for saved positions ─────────────────────────────
  saved = load_positions()
  need_teach = True

  if saved is not None:
    print("Found saved positions:")
    for name, coords in saved.items():
      print_position(name, coords)
    print()
    choice = input("Use saved positions? (y = use saved, n = re-teach): ").strip().lower()
    if choice == "y":
      need_teach = False
      positions = saved
    print()

  # ── Connect ───────────────────────────────────────────────
  backend = XArm6Backend(
    ip=ROBOT_IP,
    default_speed=100,
    default_mvacc=2000,
    tcp_offset=TCP_OFFSET,
  )
  arm = SixAxisArm(backend=backend)

  print("=== Connect & Initialize ===")
  input("Press Enter to connect to the xArm 6...")
  await arm.setup()
  print("Connected and initialized.\n")

  try:
    # ── Home ──────────────────────────────────────────────────
    print("=== Home Robot ===")
    print("The robot will move to its zero/home position.")
    input("Press Enter to home the robot (it WILL move)...")
    await arm.home()
    pos = await arm.get_cartesian_position()
    print(f"  Home position: {pos.location}\n")

    # ── Teach or load positions ───────────────────────────────
    if need_teach:
      positions = await teach_positions(arm)

    # Set the safe position on the backend
    backend._safe_position = positions["safe"]

    # ── Pick & Place cycle ────────────────────────────────────
    print("\n=== Pick & Place (Vertical Access) ===")
    access = VerticalAccess(approach_height_mm=80, clearance_mm=80, gripper_offset_mm=10)
    print(
      f"  Access: approach={access.approach_height_mm}mm, "
      f"clearance={access.clearance_mm}mm, "
      f"offset={access.gripper_offset_mm}mm"
    )

    pick_pos = positions["pick"]
    place_pos = positions["place"]
    print_position("Pick from", pick_pos)
    print_position("Place at", place_pos)
    print()

    # Move to safe
    input("Press Enter to move to safe position...")
    await arm.move_to_safe()
    print("  At safe position.")

    # Pick
    input("\nPress Enter to start PICK sequence...")
    print("  Approaching pick position...")
    await arm.approach(pick_pos, access=access)
    print("  Picking up resource...")
    await arm.pick_up_resource(pick_pos, access=access)
    print("  Pick complete!")

    # Safe transit
    input("\nPress Enter to move to safe position (carrying resource)...")
    await arm.move_to_safe()
    print("  At safe position.")

    # Place
    input("\nPress Enter to start PLACE sequence...")
    print("  Approaching place position...")
    await arm.approach(place_pos, access=access)
    print("  Placing resource...")
    await arm.drop_resource(place_pos, access=access)
    print("  Place complete!")

    # ── Current state readout ─────────────────────────────────
    print("\n=== Current State ===")
    joints = await arm.get_joint_position()
    cartesian = await arm.get_cartesian_position()
    print("  Joint angles (degrees):")
    for j, angle in joints.items():
      print(f"    J{j}: {angle:.2f}")
    print(f"  Cartesian: {cartesian.location}")
    print(
      f"  Rotation:  roll={cartesian.rotation.x:.1f}, "
      f"pitch={cartesian.rotation.y:.1f}, "
      f"yaw={cartesian.rotation.z:.1f}"
    )

    # ── Cleanup ───────────────────────────────────────────────
    print("\n=== Cleanup ===")
    input("Press Enter to return to safe position and disconnect...")
    await arm.move_to_safe()
    print("  At safe position.")

  finally:
    await arm.stop()
    print("  Disconnected. Done!")


if __name__ == "__main__":
  asyncio.run(main())
