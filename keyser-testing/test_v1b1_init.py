"""Hardware test: TecanEVO v1b1 initialization with detailed timing.

Usage:
  python keyser-testing/test_v1b1_init.py
"""

import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


async def main():
  print("=" * 60)
  print("  TecanEVO v1b1 Init Test (with timing)")
  print("=" * 60)

  t_start = time.time()

  deck = EVO150Deck()
  evo = TecanEVO(
    name="evo",
    deck=deck,
    diti_count=8,
    air_liha=True,
    has_roma=True,
    packet_read_timeout=30,
    read_timeout=120,
    write_timeout=120,
  )

  print(f"\nDevice: {evo.name}")
  print(f"PIP backend: {type(evo.pip.backend).__name__}")
  print(f"Arm: {type(evo.arm).__name__ if evo.arm else 'None'}")
  print(f"Capabilities: {[type(c).__name__ for c in evo._capabilities]}")
  print(f"  (order: arm first, then pip)")

  # --- Manual setup with timing per step ---
  print("\n--- Step 1: USB Connection ---")
  t1 = time.time()
  try:
    await evo._driver.setup()
    print(f"  USB connected ({time.time() - t1:.1f}s)")
  except Exception as e:
    print(f"  FAILED: {e}")
    return

  # --- Check init status before running capabilities ---
  print("\n--- Step 2: Pre-init check ---")
  t2 = time.time()
  try:
    resp = await evo._driver.send_command("C5", command="REE0")
    err = resp["data"][0] if resp and resp.get("data") else ""
    print(f"  LiHa REE0: {err} ({time.time() - t2:.1f}s)")
    if err and not any(c in ("A", "G") for c in err):
      print("  → Axes already initialized")
    else:
      print("  → Axes need initialization")
  except Exception as e:
    print(f"  REE0 check failed: {e} ({time.time() - t2:.1f}s)")

  try:
    resp = await evo._driver.send_command("C1", command="REE")
    roma_err = resp["data"][0] if resp and resp.get("data") else ""
    print(f"  RoMa REE: {roma_err}")
  except Exception as e:
    print(f"  RoMa REE failed: {e} (RoMa may not be present)")

  # --- Run capability setup ---
  print("\n--- Step 3: Capability setup ---")
  for cap in evo._capabilities:
    cap_name = type(cap).__name__
    print(f"\n  Starting {cap_name}._on_setup()...")
    t3 = time.time()
    try:
      await cap._on_setup()
      print(f"  {cap_name} setup OK ({time.time() - t3:.1f}s)")
    except Exception as e:
      print(f"  {cap_name} FAILED ({time.time() - t3:.1f}s): {type(e).__name__}: {e}")
      import traceback
      traceback.print_exc()

  evo._setup_finished = True
  total = time.time() - t_start
  print(f"\n--- Total setup time: {total:.1f}s ---")

  # --- Verify ---
  try:
    print(f"\nChannels: {evo.pip.num_channels}")

    resp = await evo._driver.send_command("C5", command="REE0")
    err = resp["data"][0] if resp and resp.get("data") else ""
    print(f"REE0: {err}")
    all_ok = err and all(c == "@" for c in err)

    # ZaapMotion check
    print("\nZaapMotion firmware:")
    for tip in range(8):
      resp = await evo._driver.send_command("C5", command=f"T2{tip}RFV0")
      fw = resp["data"][0] if resp and resp.get("data") else "?"
      print(f"  Tip {tip + 1}: {fw}")

    if all_ok:
      print("\n*** INIT TEST PASSED ***")
    else:
      print("\n*** INIT TEST FAILED ***")
      error_names = {0: "OK", 1: "Init failed", 7: "Not initialized"}
      resp2 = await evo._driver.send_command("C5", command="REE1")
      cfg = resp2["data"][0] if resp2 and resp2.get("data") else ""
      for i, (axis, code_char) in enumerate(zip(cfg, err)):
        code = ord(code_char) - 0x40
        label = f"{axis}{i - 2}" if axis == "Z" else axis
        status = "OK" if code == 0 else f"ERR {code}: {error_names.get(code, '?')}"
        print(f"  {label} = {status}")

  finally:
    print("\nStopping...")
    await evo._driver.stop()
    print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
