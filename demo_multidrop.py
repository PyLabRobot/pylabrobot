"""Demo script for the Multidrop Combi bulk dispenser.

Usage:
  python demo_multidrop.py COM3         # specify port explicitly (recommended)
  python demo_multidrop.py              # auto-detect by USB VID/PID (native USB only)
"""

import asyncio
import sys

from pylabrobot.bulk_dispensers import BulkDispenser
from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi import (
  CassetteType,
  DispensingOrder,
  MultidropCombiBackend,
  MultidropCombiInstrumentError,
  plate_to_pla_params,
  plate_to_type_index,
)
from pylabrobot.resources.eppendorf.plates import Eppendorf_96_wellplate_250ul_Vb


def list_serial_ports():
  """List available serial ports to help the user find the right one."""
  try:
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())
    if not ports:
      print("  No serial ports found.")
    else:
      print("  Available ports:")
      for p in ports:
        print(f"    {p.device} - {p.description} (hwid: {p.hwid})")
  except ImportError:
    print("  (pyserial not installed, cannot list ports)")


async def run_step(name: str, coro):
  """Run an async operation with error handling. Returns True on success."""
  try:
    await coro
    print(f"  {name}: OK")
    return True
  except MultidropCombiInstrumentError as e:
    print(f"  {name}: INSTRUMENT ERROR (status {e.status_code}): {e.description}")
    return False
  except Exception as e:
    print(f"  {name}: ERROR: {type(e).__name__}: {e}")
    return False


async def main():
  port = sys.argv[1] if len(sys.argv) > 1 else None

  if port is None:
    print("No COM port specified. Attempting VID/PID auto-discovery...")
    print("(This only works with native USB, not RS232-to-USB adapters)\n")

  # --- Create and connect ---
  backend = MultidropCombiBackend(port=port, timeout=30.0)
  dispenser = BulkDispenser(backend=backend)

  try:
    await dispenser.setup()
  except Exception as e:
    print(f"Connection failed: {e}\n")
    list_serial_ports()
    print(f"\nUsage: python {sys.argv[0]} <COM_PORT>")
    return

  try:
    # Connection info
    info = backend.get_version()
    print(f"Connected: {info['instrument_name']} "
          f"FW {info['firmware_version']} SN {info['serial_number']}")

    # --- Query instrument parameters ---
    print("\n--- Instrument Parameters ---")
    try:
      params = await backend.report_parameters()
      for line in params[:10]:
        print(f"  {line}")
      if len(params) > 10:
        print(f"  ... ({len(params)} lines total)")
    except Exception as e:
      print(f"  REP query failed: {type(e).__name__}: {e}")

    # --- Configure using Eppendorf twin.tec 96-well plate ---
    print("\n--- Plate Configuration ---")
    plate = Eppendorf_96_wellplate_250ul_Vb("demo_plate")
    print(f"  Plate: {plate.model}")
    print(f"    Wells: {plate.num_items} ({plate.num_items_y}x{plate.num_items_x})")
    print(f"    Height: {plate.get_size_z()} mm")

    # Map to factory type, fall back to PLA remote definition
    try:
      type_idx = plate_to_type_index(plate)
      print(f"  Matched factory plate type: {type_idx}")
      await run_step("Set plate type (SPL)", dispenser.set_plate_type(plate_type=type_idx))
    except ValueError:
      pla_params = plate_to_pla_params(plate)
      print(f"  No factory match, using remote plate definition: {pla_params}")
      await run_step("Define plate (PLA)", backend.define_plate(**pla_params))

    await run_step("Set cassette type (SCT)", dispenser.set_cassette_type(
      cassette_type=CassetteType.STANDARD))
    await run_step("Set column volume 10 uL (SCV)", dispenser.set_column_volume(
      column=0, volume=10.0))

    # Dispensing height must be above the plate. Add 3mm clearance.
    dispense_height = round(plate.get_size_z() * 100) + 300
    dispense_height = max(500, min(5500, dispense_height))  # clamp to valid range
    print(f"  Dispensing height: {dispense_height} (plate {plate.get_size_z()}mm + 3mm clearance)")
    await run_step(f"Set dispensing height {dispense_height} (SDH)",
      dispenser.set_dispensing_height(height=dispense_height))
    await run_step("Set pump speed 50% (SPS)", dispenser.set_pump_speed(speed=50))
    await run_step("Set dispensing order row-wise (SDO)", dispenser.set_dispensing_order(
      order=DispensingOrder.ROW_WISE))

    # --- Prime ---
    print("\n--- Prime ---")
    input("  Press Enter to prime (500 uL)...")
    await run_step("Prime 500 uL (PRI)", dispenser.prime(volume=500.0))

    # --- Dispense ---
    print("\n--- Dispense ---")
    input("  Press Enter to dispense...")
    await run_step("Dispense (DIS)", dispenser.dispense())

    # --- Shake ---
    print("\n--- Shake ---")
    input("  Press Enter to shake (3s, 2mm, 10Hz)...")
    await run_step("Shake (SHA)", dispenser.shake(time=3.0, distance=2, speed=10))

    # --- Move plate out ---
    print("\n--- Move Plate Out ---")
    input("  Press Enter to move plate out...")
    await run_step("Move plate out (POU)", dispenser.move_plate_out())

    # --- Empty ---
    print("\n--- Empty ---")
    input("  Press Enter to empty hoses (500 uL)...")
    await run_step("Empty 500 uL (EMP)", dispenser.empty(volume=500.0))

    print("\n--- Done! Disconnecting. ---")

  finally:
    try:
      await dispenser.stop()
    except Exception as e:
      print(f"  Disconnect error: {e}")


if __name__ == "__main__":
  asyncio.run(main())
