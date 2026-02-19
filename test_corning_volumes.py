"""Verification script for Corning plate volume/height functions."""

import math
import sys

from pylabrobot.resources.corning.plates import (
    Cor_96_wellplate_360ul_Fb,
    Cor_96_wellplate_320ul_Vb,
)

PASS = 0
FAIL = 0


def check(label, actual, expected, tol=1.0):
    global PASS, FAIL
    ok = abs(actual - expected) <= tol
    status = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{status}] {label}: got {actual:.4f}, expected {expected:.4f} (tol={tol})")
    return ok


def main():
    global PASS, FAIL

    # ---- Instantiate plates ----
    fb_plate = Cor_96_wellplate_360ul_Fb("test_fb")
    vb_plate = Cor_96_wellplate_320ul_Vb("test_vb")

    fb_well = fb_plate.get_well("A1")
    vb_well = vb_plate.get_well("A1")

    print("=" * 70)
    print("Cor_96_wellplate_360ul_Fb (flat-bottom, conical frustum)")
    print("=" * 70)
    fb_depth = fb_well.get_size_z()
    print(f"  Well depth: {fb_depth} mm")
    print(f"  Bottom type: {fb_well.bottom_type}")
    print(f"  Cross section: {fb_well.cross_section_type}")
    print()

    # 1. Max volume at full well depth should match Corning's 360 uL
    print("--- Test 1: Max volume at full depth ---")
    fb_vol_at_max = fb_well.compute_volume_from_height(fb_depth)
    check("Flat-bottom max volume vs 360 uL", fb_vol_at_max, 360.0, tol=6.0)
    print()

    # 2. Volume at several heights
    print("--- Test 2: Volume at various heights ---")
    for h in [0.0, 1.0, 2.0, 5.0, 8.0, 10.0, 10.67]:
        vol = fb_well.compute_volume_from_height(h)
        print(f"  h={h:6.2f} mm  ->  vol={vol:8.3f} uL")
    print()

    # 3. Round-trip: height -> volume -> height
    print("--- Test 3: Round-trip (height -> vol -> height) ---")
    for h_in in [0.5, 2.0, 5.0, 8.0, 10.0]:
        vol = fb_well.compute_volume_from_height(h_in)
        h_out = fb_well.compute_height_from_volume(vol)
        check(f"h={h_in} mm round-trip", h_out, h_in, tol=0.01)
    print()

    # 4. Round-trip: volume -> height -> volume
    print("--- Test 4: Round-trip (vol -> height -> vol) ---")
    for v_in in [10.0, 50.0, 100.0, 200.0, 350.0]:
        h = fb_well.compute_height_from_volume(v_in)
        v_out = fb_well.compute_volume_from_height(h)
        check(f"v={v_in} uL round-trip", v_out, v_in, tol=0.01)
    print()

    print("=" * 70)
    print("Cor_96_wellplate_320ul_Vb (V-bottom, cone + cylinder)")
    print("=" * 70)
    vb_depth = vb_well.get_size_z()
    print(f"  Well depth: {vb_depth} mm")
    print(f"  Bottom type: {vb_well.bottom_type}")
    print(f"  Cross section: {vb_well.cross_section_type}")
    print()

    # 5. Max volume at full well depth should match Corning's 320 uL
    print("--- Test 5: Max volume at full depth ---")
    vb_vol_at_max = vb_well.compute_volume_from_height(vb_depth)
    check("V-bottom max volume vs 320 uL", vb_vol_at_max, 320.0, tol=5.0)
    print()

    # 6. Volume at several heights
    print("--- Test 6: Volume at various heights ---")
    for h in [0.0, 0.5, 1.0, 1.62, 3.0, 6.0, 9.0, 11.12]:
        vol = vb_well.compute_volume_from_height(h)
        print(f"  h={h:6.2f} mm  ->  vol={vol:8.3f} uL")
    print()

    # 7. Round-trip: height -> volume -> height
    print("--- Test 7: Round-trip (height -> vol -> height) ---")
    for h_in in [0.5, 1.0, 1.62, 3.0, 6.0, 9.0, 11.0]:
        vol = vb_well.compute_volume_from_height(h_in)
        h_out = vb_well.compute_height_from_volume(vol)
        check(f"h={h_in} mm round-trip", h_out, h_in, tol=0.01)
    print()

    # 8. Round-trip: volume -> height -> volume
    print("--- Test 8: Round-trip (vol -> height -> vol) ---")
    for v_in in [5.0, 20.0, 50.0, 100.0, 200.0, 310.0]:
        h = vb_well.compute_height_from_volume(v_in)
        v_out = vb_well.compute_volume_from_height(h)
        check(f"v={v_in} uL round-trip", v_out, v_in, tol=0.01)
    print()

    # ---- Summary ----
    print("=" * 70)
    total = PASS + FAIL
    print(f"RESULTS: {PASS}/{total} passed, {FAIL}/{total} failed")
    if FAIL > 0:
        print("SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
