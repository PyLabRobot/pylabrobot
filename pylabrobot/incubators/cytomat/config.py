from .constants import CytomatRack, CytomatType

EXTRA_DEEP_WELL_RACK = CytomatRack(8, 60)
DW_RACK = CytomatRack(10, 50)
MT_RACK = CytomatRack(21, 23)
MEDIUM_WELL_RACK = CytomatRack(16, 23)
SHAKER_RACK = CytomatRack(8, 44)

CYTOMAT_CONFIG = {
  CytomatType.C6000.value: {"racks": [MT_RACK]},
  CytomatType.C6002.value: {"racks": [DW_RACK, MT_RACK]},
  CytomatType.C2C_425.value: {
    "racks": [DW_RACK, MT_RACK],
  },
  CytomatType.C2C_450_SHAKE.value: {
    "racks": [SHAKER_RACK],
  },
  CytomatType.C5C.value: {
    "racks": [MT_RACK, MT_RACK, MT_RACK, MT_RACK, MT_RACK],
  },
}
