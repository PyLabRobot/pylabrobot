"""Hardware test: AirEVOBackend initialization.

Tests that AirEVOBackend can:
1. Connect via USB
2. Exit ZaapMotion boot mode
3. Configure ZaapMotion motor controllers
4. Initialize all axes (PIA)
5. Report correct axis status

Run on a Tecan EVO 150 with Air LiHa after a fresh power cycle.

Usage:
  python keyser-testing/test_air_evo_init.py
"""

import asyncio
import logging

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan import AirEVOBackend
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck

# Enable logging so we can see what AirEVOBackend is doing
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


async def main():
  print("=" * 60)
  print("  AirEVOBackend Init Test")
  print("=" * 60)

  backend = AirEVOBackend(diti_count=8)
  deck = EVO150Deck()
  lh = LiquidHandler(backend=backend, deck=deck)

  print("\nSetting up (ZaapMotion config + PIA)...")
  print("Robot WILL move.\n")

  try:
    await lh.setup()
    print("\nSetup complete!")
  except Exception as e:
    print(f"\nSetup FAILED: {type(e).__name__}: {e}")
    import traceback

    traceback.print_exc()

    # Try to get diagnostics even after failure
    print("\n--- Post-failure diagnostics ---")
    try:
      resp = await backend.send_command("C5", command="REE0")
      err = resp["data"][0] if resp and resp.get("data") else ""
      resp = await backend.send_command("C5", command="REE1")
      cfg = resp["data"][0] if resp and resp.get("data") else ""
      if err and cfg:
        error_names = {0: "OK", 1: "Init failed", 7: "Not initialized"}
        for i, (axis, code_char) in enumerate(zip(cfg, err)):
          code = ord(code_char) - 0x40
          label = f"{axis}{i-2}" if axis == "Z" else axis
          status = "OK" if code == 0 else f"ERR {code}: {error_names.get(code, '?')}"
          print(f"  {label} = {status}")
    except Exception:
      pass

    try:
      for tip in range(8):
        resp = await backend.send_command("C5", command=f"T2{tip}RFV0")
        fw = resp["data"][0] if resp and resp.get("data") else "?"
        print(f"  Tip {tip+1}: {fw}")
    except Exception:
      pass

    try:
      await backend.io.stop()
    except Exception:
      pass
    return

  try:
    # Verify axis status
    resp = await backend.send_command("C5", command="REE0")
    err = resp["data"][0] if resp and resp.get("data") else ""
    resp = await backend.send_command("C5", command="REE1")
    cfg = resp["data"][0] if resp and resp.get("data") else ""

    print("\nAxis status:")
    error_names = {0: "OK", 1: "Init failed", 7: "Not initialized"}
    all_ok = True
    for i, (axis, code_char) in enumerate(zip(cfg, err)):
      code = ord(code_char) - 0x40
      label = f"{axis}{i-2}" if axis == "Z" else axis
      status = "OK" if code == 0 else f"ERR {code}: {error_names.get(code, '?')}"
      print(f"  {label} = {status}")
      if code != 0:
        all_ok = False

    print(f"\nChannels: {backend.num_channels}")
    print(f"LiHa: {backend.liha_connected}")
    print(f"RoMa: {backend.roma_connected}")

    # Verify ZaapMotion is in app mode
    print("\nZaapMotion firmware:")
    for tip in range(8):
      resp = await backend.send_command("C5", command=f"T2{tip}RFV0")
      fw = resp["data"][0] if resp and resp.get("data") else "?"
      print(f"  Tip {tip+1}: {fw}")

    if all_ok:
      print("\n*** INIT TEST PASSED ***")
    else:
      print("\n*** INIT TEST FAILED — some axes not initialized ***")

  finally:
    print("\nStopping...")
    await lh.stop()
    print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
