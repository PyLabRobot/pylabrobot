from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.opentrons.load import load_ot_plate_holder


def opentrons_96_deep_well_temp_mod_adapter(name: str) -> PlateHolder:
  z_offset = 5.1
  return load_ot_plate_holder(
    "opentrons_96_well_aluminum_block", z_offset=z_offset, plr_resource_name=name
  )
