"""Tecan EVO 150 connection test script.

Tests USB connection, initialization, and basic queries.
Includes multiple init strategies to diagnose and work around LiHa Z-axis init failures.

Known issue: LiHa Z-axis init (PIZ/PIA) fails inconsistently.
EVOware succeeds, suggesting a sequencing/state difference.

Prerequisites:
  pip install -e ".[usb]"

Usage:
  python keyser-testing/demo_tecan_evo.py
"""

import asyncio

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.tecan import EVOBackend
from pylabrobot.liquid_handling.backends.tecan.errors import TecanError
from pylabrobot.resources.tecan.tecan_decks import EVO150Deck


ERROR_NAMES = {
  0: "OK",
  1: "Init failed",
  2: "Invalid command",
  3: "Invalid operand",
  5: "Not implemented",
  6: "CAN timeout",
  7: "Not initialized",
  10: "Drive no load",
}


def decode_ree(error_string: str, config_string: str) -> dict[int, int]:
  """Decode REE codes. Returns {z_tip_number: error_code} for Z-axes."""
  z_results = {}
  for i, (axis_char, err_char) in enumerate(zip(config_string, error_string)):
    code = ord(err_char) - 0x40
    desc = ERROR_NAMES.get(code, f"Unknown({code})")
    status = "OK" if code == 0 else f"ERR {code}: {desc}"
    label = f"{axis_char}{i-2}" if axis_char == "Z" else axis_char
    print(f"    {label} = {status}")
    if axis_char == "Z":
      z_results[i - 2] = code
  return z_results


async def get_ree(backend: EVOBackend) -> tuple[str, str]:
  """Get REE error and config strings."""
  err = cfg = ""
  try:
    resp = await backend.send_command("C5", command="REE", params=[0])
    err = resp["data"][0] if resp["data"] else ""
  except Exception:
    pass
  try:
    resp = await backend.send_command("C5", command="REE", params=[1])
    cfg = resp["data"][0] if resp["data"] else ""
  except Exception:
    pass
  return err, cfg


async def show_status(backend: EVOBackend):
  """Print per-axis status."""
  err, cfg = await get_ree(backend)
  if err and cfg:
    print("  Axis status:")
    decode_ree(err, cfg)


async def query_z_params(backend: EVOBackend):
  """Query Z-axis configuration for debugging."""
  print("\n  --- Z-Axis Parameters ---")
  queries = [
    ("RYB", [], "Y-backlash / Z-overdrive / PWM limit"),
    ("RVZ", [0], "Z-axis current positions"),
    ("RGZ", [0], "Global Z-axis values"),
    ("RDZ", [0], "Z-axis move counters"),
    ("RDZ", [2], "Z-axis crash counters"),
  ]
  for cmd, params, desc in queries:
    try:
      resp = await backend.send_command("C5", command=cmd, params=params)
      print(f"  {cmd} {params}: {resp['data']}  ({desc})")
    except Exception as e:
      print(f"  {cmd} {params}: {e}")


async def try_init_strategy_srs_then_pia(backend: EVOBackend) -> bool:
  """Strategy: Full system reset (SRS) then PIA.
  SRS resets the DCU to power-on state — this is what EVOware likely does."""
  print("\n  Strategy: SRS (system reset) → PIA")
  print("  SRS resets firmware to power-on state (like EVOware connecting).")
  input("  Press Enter (robot will reset and re-init)...")

  print("  Sending SRS (system reset)...")
  try:
    await backend.send_command("C5", command="SRS")
  except Exception as e:
    print(f"  SRS response: {e}")
    # SRS may not return a normal response since the device resets

  # Give the firmware time to reboot
  print("  Waiting 5s for firmware reboot...")
  await asyncio.sleep(5)

  print("  Sending PIA...")
  try:
    await backend.send_command("C5", command="PIA")
    print("  PIA: OK!")
    return True
  except TecanError as e:
    print(f"  PIA FAILED: error {e.error_code} - {e.message}")
    await show_status(backend)
    return False


async def try_init_strategy_broadcast_reset(backend: EVOBackend) -> bool:
  """Strategy: SBC 0 (broadcast SW reset to all subdevices) then PIA."""
  print("\n  Strategy: SBC 0 (broadcast reset) → PIA")
  input("  Press Enter...")

  print("  Sending SBC 0 (SW reset all subdevices)...")
  try:
    await backend.send_command("C5", command="SBC", params=[0])
  except Exception as e:
    print(f"  SBC response: {e}")

  print("  Waiting 3s for subdevice reset...")
  await asyncio.sleep(3)

  print("  Sending PIA...")
  try:
    await backend.send_command("C5", command="PIA")
    print("  PIA: OK!")
    return True
  except TecanError as e:
    print(f"  PIA FAILED: error {e.error_code} - {e.message}")
    await show_status(backend)
    return False


async def try_init_strategy_pertip(backend: EVOBackend) -> bool:
  """Strategy: Init each Z individually with retries, then Y, then X.

  For each Z-axis, tries multiple speeds with multiple attempts per speed.
  A short delay between retries gives the drive time to settle.
  """
  print("\n  Strategy: Per-tip Z init (with retries) → PIY → PIX")
  input("  Press Enter...")

  MAX_ROUNDS = 3  # retry the full speed ladder this many times
  SPEEDS = [270, 150, 80, 50]  # 27, 15, 8, 5 mm/s

  ok_tips = []
  failed_tips = list(range(1, 9))  # start with all pending

  for round_num in range(1, MAX_ROUNDS + 1):
    if not failed_tips:
      break
    if round_num > 1:
      print(f"\n    --- Retry round {round_num} for Z{failed_tips} ---")
      await asyncio.sleep(1)  # settle time

    still_failed = []
    for tip in failed_tips:
      mask = 1 << (tip - 1)
      success = False
      for speed in SPEEDS:
        try:
          await backend.send_command("C5", command="PIZ", params=[mask, speed])
          print(f"    Z{tip}: OK (speed={speed/10}mm/s, round {round_num})")
          ok_tips.append(tip)
          success = True
          break
        except TecanError:
          await asyncio.sleep(0.5)  # brief pause between speed attempts
          continue
      if not success:
        still_failed.append(tip)
    failed_tips = still_failed

  if failed_tips:
    print(f"    Still failed after {MAX_ROUNDS} rounds: Z{failed_tips}")

  for cmd, name in [("PIY", "Y"), ("PIX", "X")]:
    try:
      await backend.send_command("C5", command=cmd)
      print(f"    {name}: OK")
    except TecanError as e:
      print(f"    {name}: FAILED ({e.message})")

  print(f"\n  Result: {len(ok_tips)}/8 Z-axes OK")
  if failed_tips:
    print(f"  Failed: Z{failed_tips}")
    choice = input("  Use PIF to fake-init failed axes? (y/n): ").strip().lower()
    if choice == "y":
      try:
        await backend.send_command("C5", command="PIF")
        print("  PIF: OK (all axes marked initialized)")
      except TecanError as e:
        print(f"  PIF: {e.message}")

  await show_status(backend)
  return len(failed_tips) == 0


async def try_init_strategy_srs_pertip(backend: EVOBackend) -> bool:
  """Strategy: SRS reset, then per-tip Z init."""
  print("\n  Strategy: SRS → per-tip Z init → PIY → PIX")
  print("  Combines system reset with individual Z init.")
  input("  Press Enter...")

  print("  Sending SRS...")
  try:
    await backend.send_command("C5", command="SRS")
  except Exception:
    pass

  print("  Waiting 5s for reboot...")
  await asyncio.sleep(5)

  return await try_init_strategy_pertip(backend)


async def main():
  print("=" * 60)
  print("  Tecan EVO 150 - Init Diagnostic")
  print("=" * 60)

  backend = EVOBackend(diti_count=0, read_timeout=120, write_timeout=120)
  deck = EVO150Deck()
  lh = LiquidHandler(backend=backend, deck=deck)

  # --- USB Connection ---
  print("\n--- USB Connection ---")
  try:
    await backend.io.setup()
    print("  Connected!")
  except Exception as e:
    print(f"  Failed: {e}")
    return

  try:
    # --- Firmware Info ---
    print("\n--- Firmware ---")
    for sel, label in [(0, "Version"), (2, "Serial")]:
      try:
        resp = await backend.send_command("C5", command="RFV", params=[sel])
        print(f"  LiHa {label}: {resp['data']}")
      except Exception as e:
        print(f"  {label}: {e}")

    try:
      resp = await backend.send_command("C5", command="RNT")
      print(f"  Tips: {resp['data']}")
    except Exception as e:
      print(f"  Tips: {e}")

    print("\n  Current state:")
    await show_status(backend)
    await query_z_params(backend)

    # --- Init Menu ---
    print("\n--- LiHa Init ---")
    print("  1) EVOware sequence (T23SDO11,1 → PIA) [RECOMMENDED]")
    print("  2) PIA only (standard)")
    print("  3) SRS system reset → EVOware sequence")
    print("  4) Per-tip Z init with retries → PIY → PIX")
    print("  5) SRS → per-tip Z init (combines 3 + 4)")
    print("  6) PIF (fake init, no movement)")
    print("  7) Skip LiHa, just query params")
    choice = input("  Choice [1-7]: ").strip()

    if choice == "1":
      print("  Running full EVOware init sequence...")
      print("  Step 1: TeCU init (M0/M1)")
      print("  Step 2: Safety module (O1) — enable locks and motor power")
      print("  Step 3: ZaapMotion config (T23SDO11,1)")
      print("  Step 4: PIA (init all axes)")
      input("  Press Enter (robot will move)...")

      # TeCU initialization (M0 = USB interface, M1 = TeCU controller)
      tecu_cmds = [
        ("M0", "@RO", [], "USB/TeCU reset"),
        ("M1", "_Cache_on", [], "Enable TeCU caching"),
      ]
      for module, cmd, params, desc in tecu_cmds:
        try:
          await backend.send_command(module, command=cmd, params=params)
          print(f"  {module},{cmd}: OK ({desc})")
        except Exception as e:
          print(f"  {module},{cmd}: {e} ({desc})")

      # Safety module commands (O1 = SAFY firmware)
      safety_cmds = [
        ("O1", "ALO", [1, 1], "Activate lock-out 1"),
        ("O1", "ALO", [2, 1], "Activate lock-out 2"),
        ("O1", "SSL", [1, 1], "Set safety level 1"),
        ("O1", "SPN", [], "Power on"),
        ("O1", "SPS", [3], "Set power state 3"),
      ]
      for module, cmd, params, desc in safety_cmds:
        try:
          await backend.send_command(module, command=cmd, params=params)
          print(f"  {module},{cmd}: OK ({desc})")
        except TecanError as e:
          print(f"  {module},{cmd}: error {e.error_code} ({desc})")

      # ZaapMotion subdevice config
      try:
        await backend.send_command("C5", command="T23SDO11,1")
        print("  C5,T23SDO11,1: OK (ZaapMotion config)")
      except TecanError as e:
        print(f"  C5,T23SDO11,1: error {e.error_code}")

      # PIA
      print("  Sending PIA...")
      try:
        await backend.send_command("C5", command="PIA")
        print("  PIA: OK — all axes initialized!")
      except TecanError as e:
        print(f"  PIA FAILED: {e.error_code} - {e.message}")
        await show_status(backend)

    elif choice == "2":
      print("  Running PIA...")
      input("  Press Enter...")
      try:
        await backend.send_command("C5", command="PIA")
        print("  PIA: OK!")
      except TecanError as e:
        print(f"  PIA FAILED: {e.error_code} - {e.message}")
        await show_status(backend)

    elif choice == "3":
      print("  SRS + EVOware sequence...")
      input("  Press Enter...")
      try:
        await backend.send_command("C5", command="SRS")
      except Exception:
        pass
      print("  Waiting 5s for reboot...")
      await asyncio.sleep(5)
      try:
        await backend.send_command("C5", command="T23SDO11,1")
        print("  T23SDO11,1: OK")
      except TecanError as e:
        print(f"  T23SDO11,1: error {e.error_code} - {e.message}")
      try:
        await backend.send_command("C5", command="PIA")
        print("  PIA: OK!")
      except TecanError as e:
        print(f"  PIA FAILED: {e.error_code} - {e.message}")
        await show_status(backend)

    elif choice == "4":
      await try_init_strategy_pertip(backend)

    elif choice == "5":
      await try_init_strategy_srs_pertip(backend)

    elif choice == "6":
      try:
        await backend.send_command("C5", command="PIF")
        print("  PIF: OK")
      except TecanError as e:
        print(f"  PIF: {e.message}")

    elif choice == "7":
      pass

    # --- RoMa ---
    print("\n--- RoMa ---")
    choice = input("  Initialize RoMa? (y/n): ").strip().lower()
    if choice == "y":
      try:
        ok = await backend.setup_arm(EVOBackend.ROMA)
        print(f"  RoMa: {'OK' if ok else 'not present'}")
      except TecanError as e:
        print(f"  RoMa FAILED: {e.error_code} ({e.message})")

    # --- MCA ---
    print("\n--- MCA ---")
    choice = input("  Initialize MCA? (y/n): ").strip().lower()
    if choice == "y":
      try:
        ok = await backend.setup_arm(EVOBackend.MCA)
        print(f"  MCA: {'OK' if ok else 'not present'}")
      except TecanError as e:
        print(f"  MCA FAILED: {e.error_code} ({e.message})")

    # --- Final state ---
    print("\n--- Final State ---")
    await show_status(backend)

    print("\n--- Done ---")

  finally:
    print("  Disconnecting...")
    try:
      await backend.io.stop()
    except Exception:
      pass


if __name__ == "__main__":
  asyncio.run(main())
