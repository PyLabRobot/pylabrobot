from pylabrobot.resources.tube_rack import TubeRack
from pylabrobot.resources.opentrons.load import load_ot_tube_rack
from pylabrobot.resources.tube import Tube
from pylabrobot.resources.utils import create_ordered_items_2d


def opentrons_24_tuberack_generic_1point5ml_snapcap_short(name: str) -> TubeRack:
  """
  OpenTrons 24 well rack with the shorter stand; 30mm shorter than default stand.
  3D print available here: https://www.thingiverse.com/thing:3405002
  Spec sheet (json):
  https://raw.githubusercontent.com/Opentrons/opentrons/edge/shared-data/labware/definitions/2/opentrons_24_tuberack_nest_1.5ml_screwcap/1.json
  """

  INNER_WELL_WIDTH  = 9.2 # measured
  INNER_WELL_LENGTH = 9.2 # measured
  WELL_DEPTH        = 37.40 # measured
  TUBE_MAX_VOL      = 1750  # µL  (generic 1.75 mL snap-cap)

  tube_kwargs = {
    "size_x": INNER_WELL_WIDTH,
    "size_y": INNER_WELL_LENGTH,
    "size_z": WELL_DEPTH,
    "max_volume": TUBE_MAX_VOL,
    "material_z_thickness": 0.80
  }

  return TubeRack(
    name=name,
    size_x=127.75, # spec
    size_y=85.50, # spec
    size_z=48.5, # measured. This is on the short 3D printed rack and not in the sheet
    model=opentrons_24_tuberack_generic_1point5ml_snapcap_short.__name__,
    ordered_items=create_ordered_items_2d(
        Tube,
        num_items_x=6,
        num_items_y=4,
        dx=13.5, # measured
        dy=13.5, # measured
        dz=12, # measured
        item_dx=19.89, # spec
        item_dy=19.28, # spec
        **tube_kwargs,
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
