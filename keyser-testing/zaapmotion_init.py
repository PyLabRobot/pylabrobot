"""ZaapMotion initialization — replays the exact EVOware USB config sequence.

Exits boot mode, configures motor controllers (PID, encoder, current limits),
then runs PIA to init all axes.

Derived from USB capture of EVOware's zaapmotiondriver.dll scan phase.

Usage:
  python keyser-testing/zaapmotion_init.py
"""

import asyncio

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan import EVOBackend
from pylabrobot.liquid_handling.backends.tecan.errors import TecanError
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck

# ZaapMotion motor configuration sequence (same for all 8 tips).
# Captured from EVOware USB traffic — sent via transparent pipeline T2x.
ZAAPMOTION_CONFIG = [
  "CFE 255,500",
  "CAD ADCA,0,12.5",
  "CAD ADCB,1,12.5",
  "EDF1",
  "EDF4",
  "CDO 11",
  "EDF5",
  "SIC 10,5",
  "SEA ADD,H,4,STOP,1,0,0",
  "CMTBLDC,1",
  "CETQEP2,256,R",
  "CECPOS,QEP2",
  "CECCUR,QEP2",
  "CEE OFF",
  "STL80",
  "SVL12,8,16",
  "SVL24,20,28",
  "SCL1,900,3.5",
  "SCE HOLD,500",
  "SCE MOVE,500",
  "CIR0",
  "PIDHOLD,D,1.2,1,-1,0.003,0,0,OFF",
  "PIDMOVE,D,0.8,1,-1,0.004,0,0,OFF",
  "PIDHOLD,Q,1.2,1,-1,0.003,0,0,OFF",
  "PIDMOVE,Q,0.8,1,-1,0.004,0,0,OFF",
  "PIDHOLD,POS,0.2,1,-1,0.02,4,0,OFF",
  "PIDMOVE,POS,0.35,1,-1,0.1,3,0,OFF",
  "PIDSPDELAY,0",
  "SFF 0.045,0.4,0.041",
  "SES 0",
  "SPO0",
  "SIA 0.01, 0.28, 0.0",
  "WRP",
]


async def send(backend, module, raw_cmd, desc="", quiet=False):
  """Send a raw command and print result."""
  try:
    resp = await backend.send_command(module, command=raw_cmd)
    data = resp.get("data", []) if resp else []
    if not quiet:
      print(f"  > {module},{raw_cmd:40s} -> OK  {data}  ({desc})")
    return data
  except TecanError as e:
    if not quiet:
      print(f"  > {module},{raw_cmd:40s} -> ERR {e.error_code}: {e.message}  ({desc})")
    return None
  except Exception as e:
    if not quiet:
      print(f"  > {module},{raw_cmd:40s} -> {e}  ({desc})")
    return None


async def configure_zaapmotion_tip(backend, tip: int, quiet: bool = False):
  """Configure a single ZaapMotion tip (0-7).

  Sequence: exit boot → verify app mode → send motor config → write params.
  """
  prefix = f"T2{tip}"
  label = f"Tip {tip + 1}"

  # Check current mode
  data = await send(backend, "C5", f"{prefix}RFV", f"{label}: check mode", quiet=True)

  # Exit boot if needed
  if data and isinstance(data[0], str) and "BOOT" in data[0]:
    await send(backend, "C5", f"{prefix}X", f"{label}: exit boot", quiet=quiet)
    await asyncio.sleep(1)
    # Verify transition
    data = await send(backend, "C5", f"{prefix}RFV", f"{label}: verify app", quiet=True)
    if data and isinstance(data[0], str) and "BOOT" in data[0]:
      # Retry once with longer wait
      await asyncio.sleep(1)
      data = await send(backend, "C5", f"{prefix}RFV", f"{label}: verify app (retry)", quiet=True)
      if data and isinstance(data[0], str) and "BOOT" in data[0]:
        print(f"  {label}: FAILED to exit boot mode!")
        return False

  if not quiet:
    mode = data[0] if data and data[0] else "unknown"
    print(f"  {label}: {mode}")

  # Send motor configuration
  errors = 0
  for cmd in ZAAPMOTION_CONFIG:
    result = await send(backend, "C5", f"{prefix}{cmd}", f"{label}", quiet=True)
    if result is None:
      errors += 1
      if not quiet:
        print(f"    FAILED: {prefix}{cmd}")

  # EDF1 takes ~1s (EVOware shows 1.08s gap after it)
  # WRP also takes time — wait handled by response

  if not quiet:
    if errors == 0:
      print(f"  {label}: configured ({len(ZAAPMOTION_CONFIG)} commands OK)")
    else:
      print(f"  {label}: {errors}/{len(ZAAPMOTION_CONFIG)} commands failed")

  return errors == 0


async def main():
  print("=" * 70)
  print("  ZaapMotion Full Init (from USB capture)")
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
    # --- Step 1: Configure all 8 ZaapMotion tips ---
    print("--- Step 1: ZaapMotion Motor Configuration ---")
    print("  Configuring 8 tips (exit boot + motor/PID/encoder config)...")
    input("  Press Enter to start...")

    all_ok = True
    for tip in range(8):
      ok = await configure_zaapmotion_tip(backend, tip)
      if not ok:
        all_ok = False

    if not all_ok:
      print("\n  WARNING: Some tips failed configuration!")

    # --- Step 2: Verify all tips in app mode ---
    print("\n--- Step 2: Verify ZaapMotion State ---")
    for tip in range(8):
      await send(backend, "C5", f"T2{tip}RFV0", f"Tip {tip+1}")

    # --- Step 3: Safety module + PIA ---
    print("\n--- Step 3: Safety + PIA Init ---")
    input("  Press Enter (robot WILL move)...")

    await send(backend, "O1", "SPN", "Power on")
    await send(backend, "O1", "SPS3", "Power state 3")
    await send(backend, "C5", "T23SDO11,1", "ZaapMotion SDO config")

    print("\n  Sending PIA (takes ~24 seconds)...")
    await send(backend, "C5", "PIA", "*** INIT ALL AXES ***")

    # --- Step 4: Check result ---
    print("\n--- Step 4: Init Result ---")
    err = await send(backend, "C5", "REE0", "Error codes")
    cfg = await send(backend, "C5", "REE1", "Axis config")
    if err and cfg and isinstance(err[0], str) and isinstance(cfg[0], str):
      all_init = True
      error_names = {0: "OK", 1: "Init failed", 7: "Not initialized"}
      for i, (axis, code_char) in enumerate(zip(cfg[0], err[0])):
        code = ord(code_char) - 0x40
        desc = error_names.get(code, f"Unknown({code})")
        label = f"{axis}{i-2}" if axis == "Z" else axis
        status = "OK" if code == 0 else f"ERR {code}: {desc}"
        print(f"    {label} = {status}")
        if code != 0:
          all_init = False

      if all_init:
        print("\n  *** ALL AXES INITIALIZED SUCCESSFULLY! ***")

        # Post-init sequence (from EVOware)
        print("\n--- Step 5: Post-Init ---")
        await send(backend, "C5", "BMX2", "Stop X")

        # Init RoMa
        choice = input("  Initialize RoMa? (y/n): ").strip().lower()
        if choice == "y":
          await send(backend, "C1", "PIA", "RoMa init")
          await send(backend, "C1", "PIX", "RoMa X-axis")

        # Dilutor init
        await send(backend, "C5",
                    "SHZ2100,2100,2100,2100,2100,2100,2100,2100",
                    "Set Z travel height")
        await send(backend, "C5",
                    "PAA132,2744,90,2100,2100,2100,2100,2100,2100,2100,2100",
                    "Move to wash station")
        await send(backend, "C5", "PID255", "Init all dilutors")
        await send(backend, "C5", "RDS", "Dilutor status")

        print("\n  *** FULLY INITIALIZED! ***")
      else:
        print("\n  Init incomplete — some axes failed.")

    print("\n--- Done ---")

  finally:
    print("\n  Disconnecting...")
    try:
      await backend.io.stop()
    except Exception:
      pass


if __name__ == "__main__":
  asyncio.run(main())
