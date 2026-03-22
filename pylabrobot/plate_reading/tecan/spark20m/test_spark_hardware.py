"""Interactive hardware test for SparkBackend on real Tecan Spark 20M.

Tests: setup, open, close, read_absorbance, read_fluorescence.
Run with: python -m pylabrobot.plate_reading.tecan.spark20m.test_spark_hardware
"""

import asyncio
import json
import logging
import traceback

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("spark_hw_test")


async def main():
  from pylabrobot.plate_reading.plate_reader import PlateReader
  from pylabrobot.plate_reading.tecan.spark20m.spark_backend import SparkBackend
  from pylabrobot.resources import Coordinate
  from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

  backend = SparkBackend()
  pr = PlateReader(
    name="spark",
    size_x=200,
    size_y=200,
    size_z=100,
    backend=backend,
  )

  plate = Cor_96_wellplate_360ul_Fb(name="test_plate")
  pr.assign_child_resource(plate, location=Coordinate.zero())

  # ── 1. SETUP ──────────────────────────────────────────────
  print("\n" + "=" * 60)
  print("TEST 1: setup()")
  print("=" * 60)
  input("Press Enter to connect to the Spark...")
  try:
    await pr.setup()
    print("✅  setup() succeeded")
  except Exception:
    traceback.print_exc()
    print("❌  setup() FAILED — aborting.")
    return

  try:
    # ── 2. OPEN ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("TEST 2: open()  (plate carrier moves out)")
    print("=" * 60)
    input("Press Enter to open...")
    try:
      await pr.open()
      print("✅  open() succeeded — carrier should be out")
    except Exception:
      traceback.print_exc()
      print("❌  open() FAILED")

    # ── 3. CLOSE ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("TEST 3: close()  (plate carrier moves in)")
    print("=" * 60)
    input("Press Enter to close (make sure a plate is loaded)...")
    try:
      await pr.close()
      print("✅  close() succeeded — carrier should be in")
    except Exception:
      traceback.print_exc()
      print("❌  close() FAILED")

    # ── 4. READ ABSORBANCE ────────────────────────────────────
    print("\n" + "=" * 60)
    print("TEST 4: read_absorbance(wavelength=450)")
    print("=" * 60)
    input("Press Enter to read absorbance at 450 nm...")
    try:
      abs_result = await pr.read_absorbance(
        wavelength=450,
        use_new_return_type=True,
      )
      print("✅  read_absorbance() succeeded")
      print(f"   Wavelength : {abs_result[0]['wavelength']} nm")
      print(f"   Temperature: {abs_result[0]['temperature']} °C")
      print(
        f"   Data shape : {len(abs_result[0]['data'])} rows x {len(abs_result[0]['data'][0]) if abs_result[0]['data'] else 0} cols"
      )
      print(f"   First row  : {abs_result[0]['data'][0][:6]}...")
      # Save full result
      with open("/tmp/spark_abs_result.json", "w") as f:
        json.dump(abs_result, f, indent=2, default=str)
      print("   Full result saved to /tmp/spark_abs_result.json")
    except Exception:
      traceback.print_exc()
      print("❌  read_absorbance() FAILED")

    # ── 5. READ FLUORESCENCE ─────────────────────────────────
    print("\n" + "=" * 60)
    print("TEST 5: read_fluorescence(ex=485, em=535)")
    print("=" * 60)
    input("Press Enter to read fluorescence (ex=485nm, em=535nm)...")
    try:
      fluo_result = await pr.read_fluorescence(
        excitation_wavelength=485,
        emission_wavelength=535,
        focal_height=20000,
        use_new_return_type=True,
      )
      print("✅  read_fluorescence() succeeded")
      print(f"   Ex wavelength: {fluo_result[0]['ex_wavelength']} nm")
      print(f"   Em wavelength: {fluo_result[0]['em_wavelength']} nm")
      print(f"   Temperature  : {fluo_result[0]['temperature']} °C")
      print(
        f"   Data shape   : {len(fluo_result[0]['data'])} rows x {len(fluo_result[0]['data'][0]) if fluo_result[0]['data'] else 0} cols"
      )
      print(f"   First row    : {fluo_result[0]['data'][0][:6]}...")
      # Save full result
      with open("/tmp/spark_fluo_result.json", "w") as f:
        json.dump(fluo_result, f, indent=2, default=str)
      print("   Full result saved to /tmp/spark_fluo_result.json")
    except Exception:
      traceback.print_exc()
      print("❌  read_fluorescence() FAILED")

  finally:
    # ── TEARDOWN ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("TEARDOWN: stop()")
    print("=" * 60)
    try:
      await pr.stop()
      print("✅  stop() succeeded — connection closed")
    except Exception:
      traceback.print_exc()
      print("❌  stop() FAILED")

  print("\n" + "=" * 60)
  print("ALL TESTS COMPLETE")
  print("=" * 60)


if __name__ == "__main__":
  asyncio.run(main())
