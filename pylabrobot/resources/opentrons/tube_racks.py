from pylabrobot.resources.opentrons.load import load_ot_tube_rack
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.tube_rack import TubeRack
from pylabrobot.resources.utils import create_ordered_items_2d


def opentrons_24_tuberack_generic_1point5ml_snapcap_short(name: str) -> TubeRack:
  """
  OpenTrons 24 well rack with the shorter stand; 30mm shorter than default stand.
  3D print available here: https://www.thingiverse.com/thing:3405002
  Spec sheet (json):
  https://raw.githubusercontent.com/Opentrons/opentrons/edge/shared-data/labware/definitions/2/opentrons_24_tuberack_nest_1.5ml_screwcap/1.json
  """

  WELL_DIAMETER = 9.2  # measured (circular -> inscribed square sizing is used elsewhere; see below)
  WELL_DEPTH = 37.40  # measured

  # PLR's OT loader converts circular diameter to a square footprint using diameter / sqrt(2).
  # Your earlier code already used inner well width/length of 9.2; if 9.2 is the *square* size,
  # keep it. If 9.2 is the *diameter*, convert like load_ot_tube_rack does.
  #
  # If 9.2 is "inner square width", use:
  well_size_x = well_size_y = WELL_DIAMETER
  #
  # If instead 9.2 is a measured *diameter*, use this:
  # import math
  # well_size_x = well_size_y = round(WELL_DIAMETER / math.sqrt(2), 3)

  return TubeRack(
    name=name,
    size_x=127.75,  # spec
    size_y=85.50,  # spec
    size_z=48.5,  # measured (short stand)
    model=opentrons_24_tuberack_generic_1point5ml_snapcap_short.__name__,
    ordered_items=create_ordered_items_2d(
      ResourceHolder,
      num_items_x=6,
      num_items_y=4,
      dx=13.5,  # measured
      dy=13.5,  # measured
      dz=12,  # measured
      item_dx=19.89,  # spec
      item_dy=19.28,  # spec
      size_x=well_size_x,
      size_y=well_size_y,
      size_z=WELL_DEPTH,
    ),
  )


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
