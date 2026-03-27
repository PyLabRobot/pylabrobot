"""Query ZaapMotion bootloader state and firmware info.

Gathers diagnostic info to understand the ZaapMotion firmware upload requirements.

Usage:
  python keyser-testing/query_zaapmotion.py
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
    print(f"  > {module},{raw_cmd:35s} -> {data}  ({desc})")
    return data
  except TecanError as e:
    print(f"  > {module},{raw_cmd:35s} -> ERR {e.error_code}: {e.message}  ({desc})")
    return None
  except Exception as e:
    print(f"  > {module},{raw_cmd:35s} -> {e}  ({desc})")
    return None


async def main():
  print("=" * 70)
  print("  ZaapMotion Bootloader Diagnostics")
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
    # --- LiHa firmware info ---
    print("--- LiHa Firmware ---")
    await send(backend, "C5", "RFV0", "LiHa firmware version")
    await send(backend, "C5", "RFV2", "LiHa serial number")
    await send(backend, "C5", "RFV8", "LiHa app switch")
    await send(backend, "C5", "RFV15", "LiHa expected HEX filename")
    await send(backend, "C5", "RFV16", "LiHa expected DC-Servo2 firmware version")
    await send(backend, "C5", "RFV9", "LiHa node address count")

    # --- ZaapMotion per-tip queries ---
    # T2x = transparent pipeline, layer 2, device x (0-7 = tips 1-8)
    print("\n--- ZaapMotion Per-Tip Status ---")
    for tip in range(8):
      print(f"\n  Tip {tip + 1} (T2{tip}):")
      await send(backend, "C5", f"T2{tip}RFV0", "Firmware version")
      await send(backend, "C5", f"T2{tip}RFV2", "Serial number")
      await send(backend, "C5", f"T2{tip}RFV15", "Expected HEX filename")
      await send(backend, "C5", f"T2{tip}RFV9", "Node address count")

    # --- Try bootloader-specific commands on tip 0 ---
    # The LiHa manual says bootloader accepts: A (check filename), S (start), E (erase), H (hex data)
    # These are single-char commands via the transparent pipeline
    print("\n--- Bootloader Probe (Tip 1 / T20) ---")

    # Try querying the bootloader with 'A' + a test filename
    await send(backend, "C5", "T20AXYZ", "Boot: check filename 'XYZ'")
    await send(backend, "C5", "T20AXPZ", "Boot: check filename 'XPZ'")
    await send(backend, "C5", "T20AXPM", "Boot: check filename 'XPM'")
    await send(backend, "C5", "T20AZMA", "Boot: check filename 'ZMA'")
    await send(backend, "C5", "T20AZMB", "Boot: check filename 'ZMB'")
    await send(backend, "C5", "T20AZMO", "Boot: check filename 'ZMO'")

    # --- LiHa dilutor report commands ---
    print("\n--- LiHa Dilutor Reports ---")
    await send(backend, "C5", "RDA2,0", "Device allocation array")
    await send(backend, "C5", "RSD", "Report second LiHa")
    await send(backend, "C5", "RGD3", "Global data 3")
    await send(backend, "C5", "RGD58", "Global data 58")
    await send(backend, "C5", "RGD59", "Global data 59")

    # --- Check what EVOware's ZaapMotion scan queries ---
    # From the EVOware log, it sends T2xRPP7 and T2xRPP10
    print("\n--- ZaapMotion Config (from EVOware scan) ---")
    for tip in range(8):
      await send(backend, "C5", f"T2{tip}RPP7", f"Tip {tip+1}: plunger param 7 (syringe vol)")
      await send(backend, "C5", f"T2{tip}RPP10", f"Tip {tip+1}: plunger param 10")

    # --- Try the ZaapMotion RMV commands EVOware uses ---
    print("\n--- ZaapMotion Module Verification ---")
    for tip in range(8):
      await send(backend, "C5", f"T2{tip}RMV1", f"Tip {tip+1}: module verify 1")

    print("\n--- Done ---")

  finally:
    print("\n  Disconnecting...")
    try:
      await backend.io.stop()
    except Exception:
      pass


if __name__ == "__main__":
  asyncio.run(main())
