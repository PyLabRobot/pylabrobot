"""Interactive jog tool for Tecan EVO Air LiHa.

Move the LiHa channels in X, Y, Z using keyboard commands.
Record positions for use in labware definitions.

Commands:
  x+/x-  Move X by step size (mm)
  y+/y-  Move Y by step size
  z+/z-  Move Z by step size (z+ = toward deck, z- = away from deck)
  s1/s5/s10/s50  Set step size (mm)
  p      Print current position (absolute Tecan coordinates)
  r      Record current position with a label
  h      Home (move to init position)
  q      Quit

Usage:
  python keyser-testing/jog_liha.py
"""

import asyncio
import logging
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan import AirEVOBackend
from pylabrobot.liquid_handling.backends.tecan.errors import TecanError
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck

logging.basicConfig(level=logging.WARNING)

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "taught_positions.json")


async def get_position(backend):
  """Read current X, Y, Z positions from the LiHa."""
  resp_x = await backend.send_command("C5", command="RPX0")
  resp_y = await backend.send_command("C5", command="RPY0")
  resp_z = await backend.send_command("C5", command="RPZ0")
  x = resp_x["data"][0] if resp_x and resp_x.get("data") else 0
  y = resp_y["data"][0] if resp_y and resp_y.get("data") else 0
  # Y has two values (Y and Yspace)
  if isinstance(y, list):
    y = y[0]
  z_vals = resp_z["data"] if resp_z and resp_z.get("data") else [0]
  return x, y, z_vals


async def main():
  print("=" * 60)
  print("  Tecan EVO Air LiHa Jog Tool")
  print("=" * 60)

  backend = AirEVOBackend(diti_count=8)
  deck = EVO150Deck()
  lh = LiquidHandler(backend=backend, deck=deck)

  print("\nInitializing...")
  try:
    await lh.setup()
  except Exception as e:
    print(f"Init failed: {e}")
    return

  step = 5.0  # mm
  recorded = {}

  try:
    x, y, z_vals = await get_position(backend)
    print(f"\nCurrent position: X={x} Y={y} Z={z_vals} (1/10mm)")
    print(f"Step size: {step}mm")
    print()
    print("Commands: x+/x- y+/y- z+/z-  s1/s5/s10/s50  p r h q")
    print("  z+ = toward deck, z- = away from deck")
    print()

    while True:
      cmd = input("jog> ").strip().lower()

      if cmd == "q":
        break

      elif cmd == "p":
        x, y, z_vals = await get_position(backend)
        print(f"  X={x}  Y={y}  Z={z_vals}  (1/10mm)")
        print(f"  X={x/10:.1f}mm  Y={y/10:.1f}mm  Z1={z_vals[0]/10:.1f}mm")

      elif cmd == "r":
        x, y, z_vals = await get_position(backend)
        label = input("  Label for this position: ").strip()
        if label:
          recorded[label] = {"x": x, "y": y, "z": z_vals, "step_mm": step}
          print(f"  Recorded '{label}': X={x} Y={y} Z={z_vals}")
          with open(POSITIONS_FILE, "w") as f:
            json.dump(recorded, f, indent=2)
          print(f"  Saved to {POSITIONS_FILE}")

      elif cmd == "h":
        print("  Homing...")
        await backend.liha.set_z_travel_height([backend._z_range] * backend.num_channels)
        await backend.liha.position_absolute_all_axis(
          45, 1031, 90, [backend._z_range] * backend.num_channels
        )
        x, y, z_vals = await get_position(backend)
        print(f"  Position: X={x} Y={y} Z={z_vals}")

      elif cmd.startswith("s"):
        try:
          step = float(cmd[1:])
          print(f"  Step size: {step}mm")
        except ValueError:
          print("  Usage: s1, s5, s10, s50")

      elif cmd in ("x+", "x-"):
        delta = int(step * 10) if cmd == "x+" else -int(step * 10)
        try:
          await backend.send_command("C5", command=f"PRX{delta}")
          x, y, z_vals = await get_position(backend)
          print(f"  X={x} ({x/10:.1f}mm)")
        except TecanError as e:
          print(f"  Move failed: {e}")

      elif cmd in ("y+", "y-"):
        delta = int(step * 10) if cmd == "y+" else -int(step * 10)
        try:
          await backend.send_command("C5", command=f"PRY{delta}")
          x, y, z_vals = await get_position(backend)
          print(f"  Y={y} ({y/10:.1f}mm)")
        except TecanError as e:
          print(f"  Move failed: {e}")

      elif cmd in ("z+", "z-"):
        # z+ = toward deck (increase Z value), z- = away from deck
        delta = int(step * 10) if cmd == "z+" else -int(step * 10)
        # Move all Z axes together
        z_params = ",".join([str(delta)] * backend.num_channels)
        try:
          await backend.send_command("C5", command=f"PRZ{z_params}")
          x, y, z_vals = await get_position(backend)
          print(f"  Z={z_vals} (Z1={z_vals[0]/10:.1f}mm)")
        except TecanError as e:
          print(f"  Move failed: {e}")

      elif cmd == "":
        continue

      else:
        print("  Commands: x+/x- y+/y- z+/z-  s1/s5/s10/s50  p r h q")

  finally:
    print("\nStopping...")
    await lh.stop()
    print("Done.")

    if recorded:
      print(f"\nRecorded positions saved to {POSITIONS_FILE}:")
      for label, pos in recorded.items():
        print(f"  {label}: X={pos['x']} Y={pos['y']} Z={pos['z']}")


if __name__ == "__main__":
  asyncio.run(main())
