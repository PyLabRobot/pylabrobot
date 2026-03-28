"""Hardware test: TecanEVO v1b1 initialization.

Tests the full v1b1 Device lifecycle: Driver.setup() → capabilities._on_setup()

Usage:
  python keyser-testing/test_v1b1_init.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from pylabrobot.resources.tecan.tecan_decks import EVO150Deck
from pylabrobot.tecan.evo import TecanEVO

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


async def main():
  print("=" * 60)
  print("  TecanEVO v1b1 Init Test")
  print("=" * 60)

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

  print("\nSetting up (robot WILL move)...")
  try:
    await evo.setup()
    print("\nSetup complete!")
  except Exception as e:
    print(f"\nSetup FAILED: {type(e).__name__}: {e}")
    import traceback

    traceback.print_exc()

    # Try diagnostics
    try:
      resp = await evo._driver.send_command("C5", command="REE0")
      err = resp["data"][0] if resp and resp.get("data") else ""
      resp2 = await evo._driver.send_command("C5", command="REE1")
      cfg = resp2["data"][0] if resp2 and resp2.get("data") else ""
      if err and cfg:
        error_names = {0: "OK", 1: "Init failed", 7: "Not initialized"}
        for i, (axis, code_char) in enumerate(zip(cfg, err)):
          code = ord(code_char) - 0x40
          label = f"{axis}{i - 2}" if axis == "Z" else axis
          status = "OK" if code == 0 else f"ERR {code}: {error_names.get(code, '?')}"
          print(f"  {label} = {status}")
    except Exception:
      pass
    try:
      await evo._driver.io.stop()
    except Exception:
      pass
    return

  try:
    # Verify state
    print(f"\nChannels: {evo.pip.num_channels}")
    print(f"Setup finished: {evo.setup_finished}")

    # Check axis status
    resp = await evo._driver.send_command("C5", command="REE0")
    err = resp["data"][0] if resp and resp.get("data") else ""
    print(f"REE0: {err}")
    all_ok = err and all(c == "@" for c in err)
    print(f"All axes OK: {all_ok}")

    # Check ZaapMotion
    print("\nZaapMotion firmware:")
    for tip in range(8):
      resp = await evo._driver.send_command("C5", command=f"T2{tip}RFV0")
      fw = resp["data"][0] if resp and resp.get("data") else "?"
      print(f"  Tip {tip + 1}: {fw}")

    if all_ok:
      print("\n*** INIT TEST PASSED ***")
    else:
      print("\n*** INIT TEST FAILED ***")

  finally:
    print("\nStopping...")
    await evo.stop()
    print("Done.")


if __name__ == "__main__":
  asyncio.run(main())
