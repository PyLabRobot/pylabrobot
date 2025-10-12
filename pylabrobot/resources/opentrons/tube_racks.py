from pylabrobot.resources.opentrons.load import load_ot_tube_rack
from pylabrobot.resources.tube_rack import TubeRack


def opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical(name: str) -> TubeRack:
  return load_ot_tube_rack(
    "opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical", plr_resource_name=name
  )


def opentrons_10_tuberack_nest_4x50ml_6x15ml_conical(name: str) -> TubeRack:
  return load_ot_tube_rack(
    "opentrons_10_tuberack_nest_4x50ml_6x15ml_conical", plr_resource_name=name
  )


def opentrons_15_tuberack_falcon_15ml_conical(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_15_tuberack_falcon_15ml_conical", plr_resource_name=name)


def opentrons_15_tuberack_nest_15ml_conical(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_15_tuberack_nest_15ml_conical", plr_resource_name=name)


def opentrons_24_tuberack_eppendorf_1_5ml_safelock_snapcap(name: str) -> TubeRack:
  return load_ot_tube_rack(
    "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap", plr_resource_name=name
  )


def opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap(name: str) -> TubeRack:
  return load_ot_tube_rack(
    "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap", plr_resource_name=name
  )


def opentrons_24_tuberack_generic_2ml_screwcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_tuberack_generic_2ml_screwcap", plr_resource_name=name)


def opentrons_24_tuberack_nest_0_5ml_screwcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_tuberack_nest_0_5ml_screwcap", plr_resource_name=name)


def opentrons_24_tuberack_nest_1_5ml_screwcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_tuberack_nest_1.5ml_screwcap", plr_resource_name=name)


def opentrons_24_tuberack_nest_1_5ml_snapcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_tuberack_nest_1.5ml_snapcap", plr_resource_name=name)


def opentrons_24_tuberack_nest_2ml_screwcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_tuberack_nest_2ml_screwcap", plr_resource_name=name)


def opentrons_24_tuberack_nest_2ml_snapcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_tuberack_nest_2ml_snapcap", plr_resource_name=name)


def opentrons_6_tuberack_falcon_50ml_conical(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_6_tuberack_falcon_50ml_conical", plr_resource_name=name)


def opentrons_6_tuberack_nest_50ml_conical(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_6_tuberack_nest_50ml_conical", plr_resource_name=name)


def opentrons_24_aluminumblock_generic_2ml_screwcap(name: str) -> TubeRack:
  return load_ot_tube_rack(
    "opentrons_24_aluminumblock_generic_2ml_screwcap", plr_resource_name=name
  )


def opentrons_24_aluminumblock_nest_0_5ml_screwcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_aluminumblock_nest_0.5ml_screwcap", plr_resource_name=name)


def opentrons_24_aluminumblock_nest_1_5ml_screwcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_aluminumblock_nest_1_5ml_screwcap", plr_resource_name=name)


def opentrons_24_aluminumblock_nest_1_5ml_snapcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_aluminumblock_nest_1.5ml_snapcap", plr_resource_name=name)


def opentrons_24_aluminumblock_nest_2ml_screwcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_aluminumblock_nest_2ml_screwcap", plr_resource_name=name)


def opentrons_24_aluminumblock_nest_2ml_snapcap(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_24_aluminumblock_nest_2ml_snapcap", plr_resource_name=name)


def opentrons_96_aluminumblock_biorad_wellplate_200ul(name: str) -> TubeRack:
  return load_ot_tube_rack(
    "opentrons_96_aluminumblock_biorad_wellplate_200ul", plr_resource_name=name
  )


def opentrons_96_aluminumblock_generic_pcr_strip_200ul(name: str) -> TubeRack:
  return load_ot_tube_rack(
    "opentrons_96_aluminumblock_generic_pcr_strip_200ul", plr_resource_name=name
  )


def opentrons_96_aluminumblock_nest_wellplate_100ul(name: str) -> TubeRack:
  return load_ot_tube_rack(
    "opentrons_96_aluminumblock_nest_wellplate_100ul", plr_resource_name=name
  )


def opentrons_96_well_aluminum_block(name: str) -> TubeRack:
  return load_ot_tube_rack("opentrons_96_well_aluminum_block", plr_resource_name=name)
