"""Convert liquid classes from Venus into Python."""

import binascii
import csv
import struct
import sys
import textwrap
from typing import Dict

from pylabrobot.resources.liquid import Liquid


def liquid_class_to_tip_volume(liquid_class: str) -> float:
  if "10ul" in liquid_class or "Low" in liquid_class:
    return 10
  if "30ul" in liquid_class:
    return 30
  if "50ul" in liquid_class:
    return 50
  if "300ul" in liquid_class or "Standard" in liquid_class or "Slim" in liquid_class:
    return 300
  if "1000ul" in liquid_class or "High" in liquid_class:
    return 1000
  if "4ml" in liquid_class:
    return 4000
  if "5ml" in liquid_class:
    return 5000

  raise ValueError(f"Unknown liquid class: {liquid_class}")


def ieee_754_to_float(dat: bytes) -> float:
  return float(struct.unpack("<d", dat)[0])


def main():
  with open(sys.argv[1], "r", encoding="utf-8") as f:
    lines = f.readlines()
    lines = lines[1:]
    reader = csv.reader(lines)
    rows = list(reader)  # copy into rows, then close file.

  out_file = open("liquid_classes.py", "w", encoding="utf-8")

  rows.sort(key=lambda row: row[1])

  for row in rows:
    name = row[1]
    liquid_name = row[2]
    notes = row[5]
    if len(notes) > 1:
      notes = binascii.unhexlify(notes[2:]).decode("utf-16")
    dispense_mode = float(row[7])
    curve_data = row[9]
    aspiration_flow_rate = float(row[14])
    aspiration_mix_flow_rate = float(row[15])
    aspiration_air_transport_volume = float(row[16])
    aspiration_blow_out_volume = float(row[17])
    aspiration_swap_speed = float(row[18])
    aspiration_settling_time = float(row[19])
    aspiration_over_aspirate_volume = float(row[20])
    aspiration_clot_retract_height = float(row[21])
    dispense_flow_rate = float(row[22])
    dispense_mix_flow_rate = float(row[23])
    dispense_air_transport_volume = float(row[24])
    dispense_blow_out_volume = float(row[25])
    dispense_swap_speed = float(row[26])
    dispense_settling_time = float(row[27])
    dispense_stop_flow_rate = float(row[28])
    dispense_stop_back_volume = float(row[29])

    tip = "Needle" not in name  # "Tip" is not always in the name
    jet = "Jet" in name
    empty = "Empty" in name
    # slim tip probably aindicates a 96 head, they are only used in this combo, but not sure.
    core = "CORE" in name or "Core" in name or "96Head" in name or "Slim" in name
    has_filter = "Filter" in name

    try:
      tip_volume = liquid_class_to_tip_volume(name)
      liquid = Liquid.from_str(liquid_name)
    except ValueError as e:
      print(f"\n!!! Skipping, because: {e} !!!\n")
      continue

    dat = binascii.unhexlify(curve_data[2:])
    assert len(dat) % 16 == 0 and not len(dat) == 0, "invalid length for " + dat.decode()

    curve: Dict[float, float] = {}

    for i in range(len(dat) // 16):
      key_data, value_data = dat[i * 16 : i * 16 + 8], dat[i * 16 + 8 : i * 16 + 16]
      key, value = ieee_754_to_float(key_data), ieee_754_to_float(value_data)
      value = round(value, 3)  # mitigate floating point errors
      curve[key] = value

    notes = notes.replace("\x00", "")
    if notes != "":
      notes = "\n    # " + "\n    # ".join(notes.splitlines())

    if name[0] in {str(i) for i in range(11)}:  # python doesn't allow numbers as first character
      name = "_" + name

    out_file.write(
      textwrap.dedent(
        f"""\n
    {notes}
    mapping[({tip_volume}, {core}, {tip}, {has_filter}, Liquid.{liquid.name}, {jet}, {empty})] = \\
    {name} = HamiltonLiquidClass(
      curve={curve},
      aspiration_flow_rate={aspiration_flow_rate},
      aspiration_mix_flow_rate={aspiration_mix_flow_rate},
      aspiration_air_transport_volume={aspiration_air_transport_volume},
      aspiration_blow_out_volume={aspiration_blow_out_volume},
      aspiration_swap_speed={aspiration_swap_speed},
      aspiration_settling_time={aspiration_settling_time},
      aspiration_over_aspirate_volume={aspiration_over_aspirate_volume},
      aspiration_clot_retract_height={aspiration_clot_retract_height},
      dispense_flow_rate={dispense_flow_rate},
      dispense_mode={dispense_mode},
      dispense_mix_flow_rate={dispense_mix_flow_rate},
      dispense_air_transport_volume={dispense_air_transport_volume},
      dispense_blow_out_volume={dispense_blow_out_volume},
      dispense_swap_speed={dispense_swap_speed},
      dispense_settling_time={dispense_settling_time},
      dispense_stop_flow_rate={dispense_stop_flow_rate},
      dispense_stop_back_volume={dispense_stop_back_volume}
    )"""
      )
    )

    print(name)

  out_file.close()


if __name__ == "__main__":
  main()
