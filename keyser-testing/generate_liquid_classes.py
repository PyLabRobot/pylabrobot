"""Generate TecanLiquidClass mapping entries for ZaapDiTi from DefaultLCs.XML."""
import re
import xml.etree.ElementTree as ET

with open("keyser-testing/multidispense pro/DefaultLCs.XML", "r", encoding="utf-8") as f:
    content = f.read()
content = re.sub(
    r"&#x([0-9a-fA-F]+);",
    lambda m: chr(int(m.group(1), 16)) if int(m.group(1), 16) > 31 else "",
    content,
)
root = ET.fromstring(content)

target_lcs = [
    "Water free dispense DiTi 50",
    "Water wet contact DiTi 50",
    "DMSO free dispense DiTi 50",
    "DMSO wet contact DiTi 50",
]


def g(elem, attr, default=0):
    if elem is None:
        return default
    v = elem.get(attr)
    if v is None:
        return default
    if v in ("True", "true"):
        return True
    if v in ("False", "false"):
        return False
    try:
        return float(v)
    except ValueError:
        return default


for lc in root.findall("LiquidClass"):
    name = lc.get("name", "")
    if name not in target_lcs:
        continue

    liquid = "Liquid.WATER" if "Water" in name else "Liquid.DMSO"

    glob = lc.find("Global")
    lld_elem = glob.find("LLD") if glob is not None else None
    clot_elem = glob.find("Clot") if glob is not None else None
    pmp_elem = glob.find("PMP") if glob is not None else None
    lac_elem = glob.find("LAC") if glob is not None else None

    for sc in lc.findall(".//SubClass"):
        if sc.get("tipType") != "ZaapDiTi":
            continue

        mn = float(sc.get("min", 0))
        mx = float(sc.get("max", 0))

        asp = sc.find("Aspirate")
        asp_s = asp.find("Single") if asp is not None else None
        asp_lag = asp.find(".//LAG") if asp is not None else None
        asp_tag = asp.find(".//TAG") if asp is not None else None
        asp_stag = asp.find(".//STAG") if asp is not None else None
        asp_exc = asp.find(".//Excess") if asp is not None else None
        asp_cond = asp.find(".//Conditioning") if asp is not None else None
        asp_lld = asp.find("LLD") if asp is not None else None
        asp_mix = asp.find("Mix") if asp is not None else None
        asp_ret = asp.find("Retract") if asp is not None else None

        disp = sc.find("Dispense")
        disp_s = disp.find("Single") if disp is not None else None
        disp_lld = disp.find("LLD") if disp is not None else None
        disp_touch = disp.find("TipTouching") if disp is not None else None
        disp_mix = disp.find("Mix") if disp is not None else None
        disp_ret = disp.find("Retract") if disp is not None else None

        cal = sc.find("Calibration")
        cal_s = cal.find("Single") if cal is not None else None

        print(f"mapping[({mn}, {mx}, {liquid}, TipType.AIRDITI)] = TecanLiquidClass(")
        print(f"  lld_mode={int(g(lld_elem, 'mode', 7))},")
        print(f"  lld_conductivity={int(g(lld_elem, 'conductivity', 2))},")
        print(f"  lld_speed={g(lld_elem, 'speed', 60)},")
        print(f"  lld_distance={g(lld_elem, 'doubleDist', 4)},")
        print(f"  clot_speed={g(clot_elem, 'speed', 50)},")
        print(f"  clot_limit={g(clot_elem, 'limit', 4)},")
        print(f"  pmp_sensitivity={int(g(pmp_elem, 'sensitivity', 1))},")
        print(f"  pmp_viscosity={g(pmp_elem, 'viscosity', 1)},")
        print(f"  pmp_character={int(g(pmp_elem, 'character', 0))},")
        print(f"  density={g(lac_elem, 'density', 1)},")
        print(f"  calibration_factor={g(cal_s, 'factor', 1)},")
        print(f"  calibration_offset={g(cal_s, 'offset', 0)},")
        print(f"  aspirate_speed={g(asp_s, 'speed', 50)},")
        print(f"  aspirate_delay={g(asp_s, 'delay', 200)},")
        print(f"  aspirate_stag_volume={g(asp_stag, 'volume', 0)},")
        print(f"  aspirate_stag_speed={g(asp_stag, 'speed', 20)},")
        print(f"  aspirate_lag_volume={g(asp_lag, 'volume', 10)},")
        print(f"  aspirate_lag_speed={g(asp_lag, 'speed', 70)},")
        print(f"  aspirate_tag_volume={g(asp_tag, 'volume', 5)},")
        print(f"  aspirate_tag_speed={g(asp_tag, 'speed', 20)},")
        print(f"  aspirate_excess={g(asp_exc, 'volume', 0)},")
        print(f"  aspirate_conditioning={g(asp_cond, 'volume', 0)},")
        print(f"  aspirate_pinch_valve={g(asp_s, 'pinchValve', False)},")
        print(f"  aspirate_lld={g(asp_lld, 'detect', True)},")
        print(f"  aspirate_lld_position={int(g(asp_lld, 'position', 3))},")
        print(f"  aspirate_lld_offset={g(asp_lld, 'offset', 0)},")
        print(f"  aspirate_mix={g(asp_mix, 'enabled', False)},")
        print(f"  aspirate_mix_volume={g(asp_mix, 'volume', 100)},")
        print(f"  aspirate_mix_cycles={int(g(asp_mix, 'cycles', 1))},")
        print(f"  aspirate_retract_position={int(g(asp_ret, 'position', 4))},")
        print(f"  aspirate_retract_speed={g(asp_ret, 'speed', 5)},")
        print(f"  aspirate_retract_offset={g(asp_ret, 'offset', -5)},")
        print(f"  dispense_speed={g(disp_s, 'speed', 600)},")
        print(f"  dispense_breakoff={g(disp_s, 'breakoff', 400)},")
        print(f"  dispense_delay={g(disp_s, 'delay', 0)},")
        print(f"  dispense_tag={g(disp_s, 'tag', False)},")
        print(f"  dispense_pinch_valve={g(disp_s, 'pinchValve', False)},")
        print(f"  dispense_lld={g(disp_lld, 'detect', False)},")
        print(f"  dispense_lld_position={int(g(disp_lld, 'position', 7))},")
        print(f"  dispense_lld_offset={g(disp_lld, 'offset', 0)},")
        print(f"  dispense_touching_direction={int(g(disp_touch, 'direction', 0))},")
        print(f"  dispense_touching_speed={g(disp_touch, 'speed', 10)},")
        print(f"  dispense_touching_delay={g(disp_touch, 'delay', 100)},")
        print(f"  dispense_mix={g(disp_mix, 'enabled', False)},")
        print(f"  dispense_mix_volume={g(disp_mix, 'volume', 100)},")
        print(f"  dispense_mix_cycles={int(g(disp_mix, 'cycles', 1))},")
        print(f"  dispense_retract_position={int(g(disp_ret, 'position', 1))},")
        print(f"  dispense_retract_speed={g(disp_ret, 'speed', 50)},")
        print(f"  dispense_retract_offset={g(disp_ret, 'offset', 0)},")
        print(f")  # {name} [{mn}-{mx} uL]")
        print()
