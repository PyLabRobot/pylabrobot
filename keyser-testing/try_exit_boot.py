"""Try exiting ZaapMotion boot mode with the 'X' command.

The RoMa manual documents an 'X' boot command that exits bootloader
and jumps to application firmware (if valid in flash). This script
tests whether the same command works on ZaapMotion controllers.

Run on a freshly rebooted EVO (ZaapMotion in boot mode).

Usage:
  python keyser-testing/try_exit_boot.py
"""

import asyncio

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan import EVOBackend
from pylabrobot.liquid_handling.backends.tecan.errors import TecanError
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck


async def send(backend, module, raw_cmd, desc=""):
  """Send a raw command and print result."""
  try:
    resp = await backend.send_command(module, command=raw_cmd)
    data = resp.get("data", []) if resp else []
    print(f"  > {module},{raw_cmd:30s} -> OK  {data}  ({desc})")
    return data
  except TecanError as e:
    print(f"  > {module},{raw_cmd:30s} -> ERR {e.error_code}: {e.message}  ({desc})")
    return None
  except Exception as e:
    print(f"  > {module},{raw_cmd:30s} -> {e}  ({desc})")
    return None


async def main():
  print("=" * 70)
  print("  ZaapMotion Boot Mode Exit Test")
  print("=" * 70)

  backend = EVOBackend(diti_count=0, packet_read_timeout=30, read_timeout=120, write_timeout=120)
  deck = EVO150Deck()
  lh = LiquidHandler(backend=backend, deck=deck)

  try:
    await backend.io.setup()
    print("  USB connected!\n")
  except Exception as e:
    print(f"  USB failed: {e}")
    return

  try:
    # --- Step 1: Check current state ---
    print("--- Step 1: Current ZaapMotion State ---")
    for tip in range(8):
      await send(backend, "C5", f"T2{tip}RFV0", f"Tip {tip+1} firmware")
    print()

    # --- Step 2: Try boot mode commands ---
    print("--- Step 2: Boot Mode Probe ---")
    await send(backend, "C5", "T20L", "Tip 1: read equipment type")
    await send(backend, "C5", "T20N", "Tip 1: read number of nodes")
    print()

    # --- Step 3: Exit boot mode on all tips ---
    print("--- Step 3: Send 'X' (Exit Boot Mode) to All Tips ---")
    input("  Press Enter to send exit boot command...")
    for tip in range(8):
      await send(backend, "C5", f"T2{tip}X", f"Tip {tip+1}: exit boot mode")

    # Give controllers time to boot into application mode
    print("\n  Waiting 3s for application firmware to start...")
    await asyncio.sleep(3)

    # --- Step 4: Check state after exit ---
    print("\n--- Step 4: ZaapMotion State After Exit ---")
    all_app_mode = True
    for tip in range(8):
      data = await send(backend, "C5", f"T2{tip}RFV0", f"Tip {tip+1} firmware")
      if data and isinstance(data[0], str) and "BOOT" in data[0]:
        all_app_mode = False

    if all_app_mode:
      print("\n  All tips in application mode!")
    else:
      print("\n  Some tips still in boot mode.")
      print("  Trying alternate: longer wait + recheck...")
      await asyncio.sleep(5)
      for tip in range(8):
        await send(backend, "C5", f"T2{tip}RFV0", f"Tip {tip+1} firmware (retry)")

    # --- Step 5: Try PIA ---
    print("\n--- Step 5: Try PIA Init ---")
    input("  Press Enter to try PIA (robot will move)...")

    # Safety module setup (from EVOware sequence)
    await send(backend, "O1", "SPN", "Power on")
    await send(backend, "O1", "SPS3", "Set power state 3")
    await send(backend, "C5", "T23SDO11,1", "ZaapMotion config")

    await send(backend, "C5", "PIA", "*** Init all axes ***")

    # --- Step 6: Check final state ---
    print("\n--- Step 6: Final State ---")
    err = await send(backend, "C5", "REE0", "Extended error codes")
    cfg = await send(backend, "C5", "REE1", "Axis config")

    if err and cfg and isinstance(err[0], str) and isinstance(cfg[0], str):
      error_names = {0: "OK", 1: "Init failed", 7: "Not initialized"}
      for i, (axis, code_char) in enumerate(zip(cfg[0], err[0])):
        code = ord(code_char) - 0x40
        desc = error_names.get(code, f"Unknown({code})")
        label = f"{axis}{i-2}" if axis == "Z" else axis
        status = "OK" if code == 0 else f"ERR {code}: {desc}"
        print(f"    {label} = {status}")

    print("\n--- Done ---")

  finally:
    print("\n  Disconnecting...")
    try:
      await backend.io.stop()
    except Exception:
      pass


if __name__ == "__main__":
  asyncio.run(main())
