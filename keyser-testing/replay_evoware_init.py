"""Replay the exact EVOware init sequence from the captured log.

This sends every command EVOware sent (to C5, O1, C1) in the same order,
to determine if pylabrobot's PIA failure is a command/sequencing issue
or a USB transport issue.

Usage:
  python keyser-testing/replay_evoware_init.py
"""

import asyncio

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan import EVOBackend
from pylabrobot.liquid_handling.backends.tecan.errors import TecanError
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck


async def send(backend, module, raw_cmd, desc=""):
  """Send a raw command and print result. raw_cmd is everything after the module prefix."""
  try:
    resp = await backend.send_command(module, command=raw_cmd)
    data = resp.get("data", []) if resp else []
    print(f"  > {module},{raw_cmd:30s} -> OK  {data}  {desc}")
    return resp
  except TecanError as e:
    print(f"  > {module},{raw_cmd:30s} -> ERR {e.error_code}: {e.message}  {desc}")
    return None
  except Exception as e:
    print(f"  > {module},{raw_cmd:30s} -> {e}  {desc}")
    return None


async def main():
  print("=" * 70)
  print("  Replay EVOware Init Sequence")
  print("=" * 70)

  backend = EVOBackend(diti_count=0, packet_read_timeout=30, read_timeout=120, write_timeout=120)
  deck = EVO150Deck()
  lh = LiquidHandler(backend=backend, deck=deck)

  print("\n--- USB Connection ---")
  try:
    await backend.io.setup()
    print("  Connected!")
  except Exception as e:
    print(f"  Failed: {e}")
    return

  try:
    # === Phase 1: Initial queries (EVOware log lines 22-28) ===
    print("\n--- Phase 1: TeCU Queries ---")
    await send(backend, "M1", "RFV2", "TeCU serial")
    await send(backend, "M1", "RFV0", "TeCU firmware")
    await send(backend, "M1", "RSS", "Subsystem list")

    # === Phase 2: RoMa queries (log lines 52-120) ===
    print("\n--- Phase 2: RoMa Queries ---")
    await send(backend, "C1", "RFV", "RoMa firmware")
    await send(backend, "C1", "REE", "RoMa error state")

    # === Phase 3: LiHa queries (log lines 123-270) ===
    print("\n--- Phase 3: LiHa Queries ---")
    await send(backend, "C5", "RFV", "LiHa firmware")
    await send(backend, "C5", "RNT0", "Tip count (binary)")
    await send(backend, "C5", "RNT1", "Tip count (decimal)")
    await send(backend, "C5", "REE", "Error state before init")
    await send(backend, "C5", "RPZ5", "Z-axis range")
    await send(backend, "C5", "RPX5", "X-axis range")
    await send(backend, "C5", "RPY5", "Y-axis range")
    await send(backend, "C5", "RPZ4", "Z-axis init offsets")
    await send(backend, "C5", "SFP0", "Set force param 0")

    # ZaapMotion queries (log lines 137-211)
    print("\n--- Phase 3b: ZaapMotion Queries ---")
    for tip in range(8):
      await send(backend, "C5", f"T2{tip}RFV0", f"ZaapMotion tip {tip} firmware")

    # === Phase 4: Safety module setup (log lines 280-298) ===
    print("\n--- Phase 4: Safety Module Pre-Config ---")
    await send(backend, "O1", "RFV", "Safety firmware")
    await send(backend, "O1", "RSL1", "Report safety level 1")
    await send(backend, "O1", "RSL2", "Report safety level 2")
    await send(backend, "O1", "RLO1", "Report lock-out 1")
    await send(backend, "O1", "RLS", "Report lock status")
    await send(backend, "O1", "SLO1,0", "Set lock-out 1 = 0")
    await send(backend, "O1", "SLO2,0", "Set lock-out 2 = 0")
    await send(backend, "O1", "SLO3,0", "Set lock-out 3 = 0")
    await send(backend, "O1", "SLO4,0", "Set lock-out 4 = 0")

    # === Phase 5: Init trigger (log lines 309-350) ===
    print("\n--- Phase 5: Init Sequence ---")
    print("  This is the exact EVOware init sequence.")
    input("  Press Enter (robot WILL move)...")

    await send(backend, "O1", "ALO1,1", "Activate lock-out 1")
    await send(backend, "O1", "ALO2,1", "Activate lock-out 2")
    await send(backend, "O1", "RSL2", "Check safety level 2")
    await send(backend, "O1", "SSL1,1", "Set safety level 1")

    # Small delay — EVOware has Named Pipe traffic here
    await asyncio.sleep(0.5)

    await send(backend, "O1", "SPN", "Power on")
    await send(backend, "O1", "SPS3", "Set power state 3")
    await send(backend, "C5", "T23SDO11,1", "ZaapMotion config")

    print("\n  Sending PIA (this takes ~24 seconds)...")
    await send(backend, "C5", "PIA", "*** INIT ALL AXES ***")

    # Check result
    print("\n--- Init Result ---")
    await send(backend, "C5", "REE0", "Extended error codes")
    await send(backend, "C5", "REE1", "Axis config")

    # === If PIA succeeded, continue with EVOware post-init ===
    print("\n--- Post-Init (EVOware sequence) ---")
    await send(backend, "C5", "BMX2", "Stop X")
    await send(backend, "C1", "PIA", "RoMa init")
    await send(backend, "C1", "PIX", "RoMa X-axis init")

    print("\n--- Done ---")

  finally:
    print("\n  Disconnecting...")
    try:
      await backend.io.stop()
    except Exception:
      pass


if __name__ == "__main__":
  asyncio.run(main())
