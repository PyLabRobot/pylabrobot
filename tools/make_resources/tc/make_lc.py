"""Convert liquid classes from EVOware into Python."""

from xml.etree.ElementTree import parse

from pylabrobot.liquid_handling.liquid_classes.tecan import from_str
from pylabrobot.resources.tecan import TipType

path = "DefaultLCs.xml"


def main(lc):
  et = parse(path)

  for liquid_class in et.getroot():
    liquid = from_str(liquid_class.attrib["name"])
    if liquid is None:
      continue

    lld = liquid_class[0][0].attrib
    clot = liquid_class[0][1].attrib
    pmp = liquid_class[0][3].attrib
    glb = {
      "lld_mode": lld["mode"],
      "lld_conductivity": lld["conductivity"],
      "lld_speed": lld["speed"],
      "lld_distance": lld["doubleDist"],
      "clot_speed": clot["speed"],
      "clot_limit": clot["limit"],
      "pmp_sensitivity": pmp["sensitivity"],
      "pmp_viscosity": pmp["viscosity"],
      "pmp_character": pmp["character"],
      "density": liquid_class[0][2].attrib["density"],
    }

    for subclass in liquid_class[1]:
      mnv = subclass.attrib["min"]
      mxv = subclass.attrib["max"]
      tip = TipType(subclass.attrib["tipType"])

      asp = subclass[0]
      disp = subclass[1]
      sub = {
        "calibration_factor": subclass[2][0].attrib["factor"],
        "calibration_offset": subclass[2][0].attrib["offset"],
        "aspirate_speed": asp[0].attrib["speed"],
        "aspirate_delay": asp[0].attrib["delay"],
        "aspirate_stag_volume": asp[0][0].attrib["volume"],
        "aspirate_stag_speed": asp[0][0].attrib["speed"],
        "aspirate_lag_volume": asp[0][1].attrib["volume"],
        "aspirate_lag_speed": asp[0][1].attrib["speed"],
        "aspirate_tag_volume": asp[0][2].attrib["volume"],
        "aspirate_tag_speed": asp[0][2].attrib["speed"],
        "aspirate_excess": asp[0][3].attrib["volume"],
        "aspirate_conditioning": asp[0][4].attrib["volume"],
        "aspirate_pinch_valve": asp[0].attrib["pinchValve"],
        "aspirate_lld": asp[2].attrib["detect"],
        "aspirate_lld_position": asp[2].attrib["position"],
        "aspirate_lld_offset": asp[2].attrib["offset"],
        "aspirate_mix": asp[4].attrib["enabled"],
        "aspirate_mix_volume": asp[4].attrib["volume"],
        "aspirate_mix_cycles": asp[4].attrib["cycles"],
        "aspirate_retract_position": asp[5].attrib["position"],
        "aspirate_retract_speed": asp[5].attrib["speed"],
        "aspirate_retract_offset": asp[5].attrib["offset"],
        "dispense_speed": disp[0].attrib["speed"],
        "dispense_breakoff": disp[0].attrib["breakoff"],
        "dispense_delay": disp[0].attrib["delay"],
        "dispense_tag": disp[0].attrib["tag"],
        "dispense_pinch_valve": disp[0].attrib["pinchValve"],
        "dispense_lld": disp[2].attrib["detect"],
        "dispense_lld_position": disp[2].attrib["position"],
        "dispense_lld_offset": disp[2].attrib["offset"],
        "dispense_touching_direction": disp[3].attrib["direction"],
        "dispense_touching_speed": disp[3].attrib["speed"],
        "dispense_touching_delay": disp[3].attrib["delay"],
        "dispense_mix": disp[4].attrib["enabled"],
        "dispense_mix_volume": disp[4].attrib["volume"],
        "dispense_mix_cycles": disp[4].attrib["cycles"],
        "dispense_retract_position": disp[5].attrib["position"],
        "dispense_retract_speed": disp[5].attrib["speed"],
        "dispense_retract_offset": disp[5].attrib["offset"],
      }

      lc.write(f"\n\n")
      lc.write(
        f"mapping[({mnv}, {mxv}, LiquidClass.{liquid.name}, TipType.{tip.name})] = TecanLiquidClass(\n"
      )
      for k, v in glb.items():
        lc.write(f"  {k}={v},\n")
      for k, v in sub.items():
        lc.write(f"  {k}={v},\n")
      lc.write(f")\n")


if __name__ == "__main__":
  with open("liquid_classes.py", "w") as liquid_classes:
    main(liquid_classes)
