"""Interactive jog, teach, and labware editor for Tecan EVO Air LiHa.

Features:
  - Jog X/Y/Z in configurable step sizes
  - Teach and record positions (tip z_start, plate z_start, etc.)
  - Display current deck layout with carriers, plates, tip racks
  - Edit labware Z definitions (z_start, z_dispense, z_max) from taught positions
  - Save/load taught positions and edited labware to JSON

Commands:
  Movement:
    x+/x-    Jog X axis
    y+/y-    Jog Y axis
    z+/z-    Jog Z axis (z+ = toward deck)
    s<n>     Set step size in mm (s1, s5, s10, s0.5)

  Position:
    p        Print current position
    r        Record position with label
    goto <label>  Move to a previously recorded position

  Labware:
    deck     Show deck layout (carriers, plates, tips)
    show <name>   Show labware details (z_start, z_max, etc.)
    teach z_start <name>  Set z_start from current Z position
    teach z_dispense <name>  Set z_dispense from current Z position
    teach z_max <name>  Set z_max from current Z position
    save     Save taught positions and edited labware to JSON

  System:
    h        Home (move to init position)
    tips     Check tip status (RTS)
    ree      Show axis error codes
    q        Quit

Usage:
  python keyser-testing/jog_and_teach.py
"""

import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from labware_library import DiTi_50ul_SBS_LiHa_Air, Eppendorf_96_wellplate_250ul_Vb_skirted, MP_3Pos_Corrected
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO
from pylabrobot.tecan.evo.errors import TecanError

logging.basicConfig(level=logging.WARNING)

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "taught_positions.json")
LABWARE_FILE = os.path.join(os.path.dirname(__file__), "labware_edits.json")


async def get_position(driver):
  """Read current X, Y, Z positions."""
  resp_x = await driver.send_command("C5", command="RPX0")
  resp_y = await driver.send_command("C5", command="RPY0")
  resp_z = await driver.send_command("C5", command="RPZ0")
  x = resp_x["data"][0] if resp_x and resp_x.get("data") else 0
  y_data = resp_y["data"] if resp_y and resp_y.get("data") else [0]
  y = y_data[0] if isinstance(y_data, list) else y_data
  z_vals = resp_z["data"] if resp_z and resp_z.get("data") else [0]
  return x, y, z_vals


def load_json(path):
  if os.path.exists(path):
    with open(path, "r") as f:
      return json.load(f)
  return {}


def save_json(path, data):
  with open(path, "w") as f:
    json.dump(data, f, indent=2)
  print(f"  Saved to {path}")


async def main():
  print("=" * 60)
  print("  Tecan EVO Jog, Teach & Labware Editor")
  print("=" * 60)

  # --- Deck setup ---
  deck = EVO150Deck()
  evo = TecanEVO(
    name="evo",
    deck=deck,
    diti_count=8,
    air_liha=True,
    has_roma=False,
    packet_read_timeout=30,
    read_timeout=120,
    write_timeout=120,
  )

  carrier = MP_3Pos_Corrected("carrier")
  deck.assign_child_resource(carrier, rails=16)

  source_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("source")
  dest_plate = Eppendorf_96_wellplate_250ul_Vb_skirted("dest")
  tip_rack = DiTi_50ul_SBS_LiHa_Air("tips")
  carrier[0] = source_plate
  carrier[1] = dest_plate
  carrier[2] = tip_rack

  # Named labware for editing
  labware = {
    "source": source_plate,
    "dest": dest_plate,
    "tips": tip_rack,
  }

  print("\nInitializing...")
  try:
    await evo.setup()
    print("Ready!")
  except Exception as e:
    print(f"Init failed: {e}")
    import traceback

    traceback.print_exc()
    return

  step = 5.0
  recorded = load_json(POSITIONS_FILE)
  labware_edits = load_json(LABWARE_FILE)
  driver = evo._driver
  num_ch = evo.pip.num_channels
  z_range = evo.pip.backend._z_range

  try:
    x, y, z_vals = await get_position(driver)
    print(f"\nPosition: X={x} Y={y} Z1={z_vals[0]} (1/10mm)")
    print(f"Step: {step}mm | z_range: {z_range}")
    print("\nType 'help' for commands\n")

    while True:
      try:
        cmd = input("jog> ").strip()
      except EOFError:
        break

      if not cmd:
        continue

      parts = cmd.split()
      action = parts[0].lower()

      # --- Quit ---
      if action == "q":
        break

      # --- Help ---
      elif action == "help":
        print("  Movement: x+/x- y+/y- z+/z-  s<mm>")
        print("  Position: p  r  goto <label>")
        print("  Labware:  deck  show <name>  teach <field> <name>  save")
        print("  System:   h  tips  ree  q")

      # --- Print position ---
      elif action == "p":
        x, y, z_vals = await get_position(driver)
        print(f"  X={x} ({x / 10:.1f}mm)  Y={y} ({y / 10:.1f}mm)")
        print(f"  Z={z_vals}")
        print(f"  Z1={z_vals[0]} ({z_vals[0] / 10:.1f}mm from deck)")

      # --- Record ---
      elif action == "r":
        x, y, z_vals = await get_position(driver)
        label = input("  Label: ").strip()
        if label:
          recorded[label] = {"x": x, "y": y, "z": z_vals, "step_mm": step}
          save_json(POSITIONS_FILE, recorded)
          print(f"  Recorded '{label}': X={x} Y={y} Z1={z_vals[0]}")

      # --- Goto ---
      elif action == "goto" and len(parts) > 1:
        label = parts[1]
        if label in recorded:
          pos = recorded[label]
          print(f"  Moving to '{label}': X={pos['x']} Y={pos['y']} Z={pos['z']}")
          z_params = ",".join(str(z) for z in pos["z"])
          try:
            await driver.send_command(
              "C5", command=f"PAA{pos['x']},{pos['y']},90,{z_params}"
            )
            print("  Moved!")
          except TecanError as e:
            print(f"  Move failed: {e}")
        else:
          print(f"  Unknown position '{label}'. Known: {list(recorded.keys())}")

      # --- Home ---
      elif action == "h":
        print("  Homing...")
        pip_be = evo.pip.backend
        await pip_be.liha.set_z_travel_height([z_range] * num_ch)
        await pip_be.liha.position_absolute_all_axis(45, 1031, 90, [z_range] * num_ch)
        x, y, z_vals = await get_position(driver)
        print(f"  Position: X={x} Y={y} Z1={z_vals[0]}")

      # --- Step size ---
      elif action.startswith("s") and len(action) > 1:
        try:
          step = float(action[1:])
          print(f"  Step: {step}mm")
        except ValueError:
          print("  Usage: s1, s5, s10, s0.5")

      # --- Jog X ---
      elif action in ("x+", "x-"):
        delta = int(step * 10) if action == "x+" else -int(step * 10)
        try:
          await driver.send_command("C5", command=f"PRX{delta}")
          x, _, _ = await get_position(driver)
          print(f"  X={x} ({x / 10:.1f}mm)")
        except TecanError as e:
          print(f"  {e}")

      # --- Jog Y ---
      elif action in ("y+", "y-"):
        delta = int(step * 10) if action == "y+" else -int(step * 10)
        try:
          await driver.send_command("C5", command=f"PRY{delta}")
          _, y, _ = await get_position(driver)
          print(f"  Y={y} ({y / 10:.1f}mm)")
        except TecanError as e:
          print(f"  {e}")

      # --- Jog Z ---
      elif action in ("z+", "z-"):
        delta = int(step * 10) if action == "z+" else -int(step * 10)
        z_params = ",".join([str(delta)] * num_ch)
        try:
          await driver.send_command("C5", command=f"PRZ{z_params}")
          _, _, z_vals = await get_position(driver)
          print(f"  Z1={z_vals[0]} ({z_vals[0] / 10:.1f}mm)")
        except TecanError as e:
          print(f"  {e}")

      # --- Tips status ---
      elif action == "tips":
        try:
          resp = await driver.send_command("C5", command="RTS")
          status = resp["data"][0] if resp and resp.get("data") else "?"
          print(f"  Tip status: {status} (0=none, 255=all)")
        except TecanError as e:
          print(f"  {e}")

      # --- REE ---
      elif action == "ree":
        try:
          resp = await driver.send_command("C5", command="REE0")
          err = resp["data"][0] if resp and resp.get("data") else ""
          resp2 = await driver.send_command("C5", command="REE1")
          cfg = resp2["data"][0] if resp2 and resp2.get("data") else ""
          names = {0: "OK", 1: "Init failed", 7: "Not init", 25: "Tip not fetched"}
          if err and cfg:
            for i, (ax, ec) in enumerate(zip(cfg, err)):
              code = ord(ec) - 0x40
              label = f"{ax}{i - 2}" if ax == "Z" else ax
              print(f"  {label} = {names.get(code, f'err {code}')}")
        except TecanError as e:
          print(f"  {e}")

      # --- Deck layout ---
      elif action == "deck":
        print("\n  Deck Layout:")
        print(f"  {'─' * 50}")
        for child in deck.children:
          loc = child.get_location_wrt(deck)
          print(f"  {child.name} ({type(child).__name__})")
          print(f"    Location: x={loc.x:.1f} y={loc.y:.1f} z={loc.z:.1f} mm")
          if hasattr(child, "sites"):
            for j, site in enumerate(child.sites):
              site_loc = site.get_location_wrt(deck)
              contents = site.children[0].name if site.children else "(empty)"
              print(f"    Site {j + 1}: {contents}")
              print(f"      Location: x={site_loc.x:.1f} y={site_loc.y:.1f} z={site_loc.z:.1f}")
        print()

      # --- Show labware ---
      elif action == "show" and len(parts) > 1:
        name = parts[1]
        if name in labware:
          lw = labware[name]
          print(f"\n  {name} ({type(lw).__name__})")
          print(f"    size: {lw.get_size_x():.1f} x {lw.get_size_y():.1f} x {lw.get_size_z():.1f} mm")
          loc = lw.get_location_wrt(deck)
          print(f"    location: x={loc.x:.1f} y={loc.y:.1f} z={loc.z:.1f}")
          if hasattr(lw, "z_start"):
            print(f"    z_start: {lw.z_start}")
          if hasattr(lw, "z_dispense"):
            print(f"    z_dispense: {lw.z_dispense}")
          if hasattr(lw, "z_max"):
            print(f"    z_max: {lw.z_max}")
          if hasattr(lw, "area"):
            print(f"    area: {lw.area}")
          if hasattr(lw, "item_dy"):
            print(f"    well pitch (item_dy): {lw.item_dy:.1f}mm")
          # Show edits if any
          if name in labware_edits:
            print(f"    [EDITED]: {labware_edits[name]}")
          print()
        else:
          print(f"  Unknown: '{name}'. Known: {list(labware.keys())}")

      # --- Teach Z values ---
      elif action == "teach" and len(parts) >= 3:
        field = parts[1]
        name = parts[2]
        if name not in labware:
          print(f"  Unknown labware '{name}'. Known: {list(labware.keys())}")
          continue
        if field not in ("z_start", "z_dispense", "z_max"):
          print(f"  Unknown field '{field}'. Use: z_start, z_dispense, z_max")
          continue

        _, _, z_vals = await get_position(driver)
        z_val = z_vals[0]

        lw = labware[name]
        old_val = getattr(lw, field, None)
        setattr(lw, field, float(z_val))

        if name not in labware_edits:
          labware_edits[name] = {}
        labware_edits[name][field] = z_val

        print(f"  {name}.{field}: {old_val} → {z_val} (Z1={z_val}, {z_val / 10:.1f}mm)")
        save_json(LABWARE_FILE, labware_edits)

      # --- Save ---
      elif action == "save":
        save_json(POSITIONS_FILE, recorded)
        save_json(LABWARE_FILE, labware_edits)
        print("  All saved.")

      else:
        print(f"  Unknown command: '{cmd}'. Type 'help' for commands.")

  finally:
    print("\nStopping...")
    await evo.stop()
    print("Done.")

    if recorded:
      print(f"\nTaught positions ({POSITIONS_FILE}):")
      for label, pos in recorded.items():
        print(f"  {label}: X={pos['x']} Y={pos['y']} Z1={pos['z'][0]}")

    if labware_edits:
      print(f"\nLabware edits ({LABWARE_FILE}):")
      for name, edits in labware_edits.items():
        for field, val in edits.items():
          print(f"  {name}.{field} = {val}")


if __name__ == "__main__":
  asyncio.run(main())
