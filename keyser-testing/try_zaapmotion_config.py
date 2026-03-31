"""Try configuring ZaapMotion axes after exiting boot mode.

After 'X' exits boot mode, the ZaapMotion controllers need motor
configuration (SEI, PID) before PIA can init the Z-axes.

This script tries various configuration commands from the DLL analysis:
- SEI (motor initialization with speed/current params)
- PID controller setup
- Controller configuration

Run on a freshly rebooted EVO.

Usage:
  python keyser-testing/try_zaapmotion_config.py
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
    print(f"  > {module},{raw_cmd:35s} -> OK  {data}  ({desc})")
    return data
  except TecanError as e:
    print(f"  > {module},{raw_cmd:35s} -> ERR {e.error_code}: {e.message}  ({desc})")
    return None
  except Exception as e:
    print(f"  > {module},{raw_cmd:35s} -> {e}  ({desc})")
    return None


async def exit_boot_mode(backend):
  """Send X to all tips to exit boot mode."""
  print("  Exiting boot mode on all tips...")
  for tip in range(8):
    await send(backend, "C5", f"T2{tip}X", f"Tip {tip+1}: exit boot")
  print("  Waiting 3s...")
  await asyncio.sleep(3)

  # Verify
  all_app = True
  for tip in range(8):
    data = await send(backend, "C5", f"T2{tip}RFV0", f"Tip {tip+1}")
    if data and isinstance(data[0], str) and "BOOT" in data[0]:
      all_app = False
  return all_app


async def try_pia(backend):
  """Try PIA and show results."""
  print("\n  Sending PIA...")
  result = await send(backend, "C5", "PIA", "Init all axes")

  err = await send(backend, "C5", "REE0", "Error codes")
  cfg = await send(backend, "C5", "REE1", "Axis config")
  if err and cfg and isinstance(err[0], str) and isinstance(cfg[0], str):
    error_names = {0: "OK", 1: "Init failed", 7: "Not init'd"}
    for i, (axis, code_char) in enumerate(zip(cfg[0], err[0])):
      code = ord(code_char) - 0x40
      desc = error_names.get(code, f"?({code})")
      label = f"{axis}{i-2}" if axis == "Z" else axis
      status = "OK" if code == 0 else f"ERR: {desc}"
      print(f"    {label} = {status}")

  return err and isinstance(err[0], str) and "@" * 11 == err[0][:11]


async def main():
  print("=" * 70)
  print("  ZaapMotion Configuration Test")
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
    # --- Exit boot mode ---
    print("--- Step 1: Exit Boot Mode ---")
    data = await send(backend, "C5", "T20RFV0", "Check current mode")
    if data and isinstance(data[0], str) and "BOOT" in data[0]:
      if not await exit_boot_mode(backend):
        print("  Failed to exit boot mode!")
        return
    else:
      print("  Already in application mode.")

    # --- Query available commands ---
    print("\n--- Step 2: Probe ZaapMotion App Commands ---")
    # Try various commands to understand what's available
    probe_cmds = [
      # Report commands
      ("T20RFV0", "Firmware version"),
      ("T20RFV2", "Serial number"),
      ("T20RPP0", "Plunger param 0"),
      ("T20RPP7", "Plunger param 7 (syringe vol)"),
      ("T20RPP10", "Plunger param 10"),
      ("T20REE", "Extended error codes"),
      ("T20RDS", "Dilutor error codes"),
      # Try SEI with no params
      ("T20SEI", "Motor init (SEI) - no params"),
      # Try PIY (position init) on subdevice
      ("T20PIY", "Position init Y (subdevice)"),
      # Try PIA on subdevice
      ("T20PIA", "Position init A (subdevice)"),
    ]
    for cmd, desc in probe_cmds:
      await send(backend, "C5", cmd, desc)

    # --- Try sending SEI with params to each tip ---
    print("\n--- Step 3: Try SEI Motor Init ---")
    print("  Trying SEI (motor init) with various param formats...")
    input("  Press Enter...")

    # Try SEI without params
    await send(backend, "C5", "T20SEI", "SEI no params")
    # Try SEI with speed param
    await send(backend, "C5", "T20SEI270", "SEI speed=270")
    # Try SEI with speed,current
    await send(backend, "C5", "T20SEI270,500", "SEI speed=270,current=500")
    # Try SEI with comma-separated
    await send(backend, "C5", "T20SEI27,50", "SEI 27,50")

    # --- Try initializing dilutor on each tip ---
    print("\n--- Step 4: Try Dilutor Init (PID) ---")
    # PID command initializes plunger and valve - sent through LiHa
    await send(backend, "C5", "PID255", "Init all dilutors")

    # Check dilutor status
    await send(backend, "C5", "RDS", "Dilutor error status")

    # --- Try PIA ---
    print("\n--- Step 5: Try PIA ---")
    input("  Press Enter (robot will move)...")
    await send(backend, "O1", "SPN", "Power on")
    await send(backend, "O1", "SPS3", "Power state 3")
    await try_pia(backend)

    # --- If PIA failed, try per-tip PIZ ---
    print("\n--- Step 6: Try Per-Tip PIZ ---")
    input("  Press Enter...")
    ok = 0
    for tip in range(1, 9):
      mask = 1 << (tip - 1)
      for speed in [270, 150, 80, 50]:
        data = await send(backend, "C5", f"PIZ{mask},{speed}", f"Z{tip} speed={speed/10}mm/s")
        if data is not None:
          ok += 1
          break
    print(f"\n  {ok}/8 Z-axes initialized")

    # Try Y and X
    await send(backend, "C5", "PIY", "Y-axis init")
    await send(backend, "C5", "PIX", "X-axis init")

    # Final state
    print("\n--- Final State ---")
    err = await send(backend, "C5", "REE0", "Error codes")
    cfg = await send(backend, "C5", "REE1", "Axis config")
    if err and cfg and isinstance(err[0], str) and isinstance(cfg[0], str):
      error_names = {0: "OK", 1: "Init failed", 7: "Not init'd"}
      for i, (axis, code_char) in enumerate(zip(cfg[0], err[0])):
        code = ord(code_char) - 0x40
        desc = error_names.get(code, f"?({code})")
        label = f"{axis}{i-2}" if axis == "Z" else axis
        print(f"    {label} = {'OK' if code == 0 else f'ERR: {desc}'}")

    print("\n--- Done ---")

  finally:
    print("\n  Disconnecting...")
    try:
      await backend.io.stop()
    except Exception:
      pass


if __name__ == "__main__":
  asyncio.run(main())
