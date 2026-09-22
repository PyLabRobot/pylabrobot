"""Which plates each instrument family works with, and how it works them.

The same plates appear twice, with different heights. An instrument with a wash manifold measures
a dispense and an aspirate from different positions; a dispenser-only instrument has no wash
manifold, so its two dispensing heights are the same number.
"""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord

WASHER_PLATES: tuple[PlateRecord, ...] = (
  PlateRecord(PlateType.PLATE_1536_WELL, "1536 Well Plate", 250, 94, 42, 48, 32),
  PlateRecord(PlateType.PLATE_1536_FLANGE, "1536 Flanged Plate", 196, 93, 13, 48, 32),
  PlateRecord(PlateType.PLATE_384_WELL, "384 Well Plate", 333, 120, 22, 24, 16),
  PlateRecord(PlateType.PLATE_384_WELL_PCR, "384 Well PCR Plate", 230, 83, 2, 24, 16),
  PlateRecord(PlateType.PLATE_384_DEEP_WELL, "384 Deep Well Plate", 986, 355, 20, 24, 16),
  PlateRecord(PlateType.PLATE_96_WELL, "96 Well Plate", 336, 121, 29, 12, 8),
  PlateRecord(PlateType.PLATE_96_DEEP_WELL, "96 Deep Well Plate", 929, 335, 26, 12, 8),
  PlateRecord(PlateType.PLATE_96_HALF_WELL, "96 Half Well Plate", 332, 120, 26, 12, 8),
  PlateRecord(PlateType.PLATE_96_MINI_TUBES, "96 Mini Tubes", 1105, 398, 30, 12, 8),
  PlateRecord(PlateType.PLATE_48_WELL, "48 Well Plate", 461, 166, 21, 8, 6),
  PlateRecord(PlateType.PLATE_24_WELL, "24 Well Plate", 470, 169, 23, 6, 4),
  PlateRecord(PlateType.PLATE_12_WELL, "12 Well Plate", 464, 167, 30, 4, 3),
  PlateRecord(PlateType.PLATE_6_WELL, "6 Well Plate", 464, 167, 22, 3, 2),
  PlateRecord(PlateType.TEST_PLATE_96_WELL_MB, "96 Well MB Test Plate", 327, 121, 19, 12, 8),
  PlateRecord(PlateType.TEST_PLATE_96_WELL_BD, "96 Well BD Test Plate", 336, 121, 24, 12, 8),
)
"""The plates an instrument with a wash manifold works with."""

DISPENSER_PLATES: tuple[PlateRecord, ...] = (
  PlateRecord(PlateType.PLATE_1536_WELL, "1536 Well Plate", 250, 250, 131, 48, 32),
  PlateRecord(PlateType.PLATE_1536_FLANGE, "1536 Flanged Plate", 196, 196, 41, 48, 32),
  PlateRecord(PlateType.PLATE_384_WELL, "384 Well Plate", 333, 333, 64, 24, 16),
  PlateRecord(PlateType.PLATE_384_WELL_PCR, "384 Well PCR Plate", 230, 230, 6, 24, 16),
  PlateRecord(PlateType.PLATE_384_DEEP_WELL, "384 Deep Well Plate", 986, 986, 64, 24, 16),
  PlateRecord(PlateType.PLATE_96_WELL, "96 Well Plate", 336, 336, 84, 12, 8),
  PlateRecord(PlateType.PLATE_96_DEEP_WELL, "96 Deep Well Plate", 929, 929, 84, 12, 8),
  PlateRecord(PlateType.PLATE_96_HALF_WELL, "96 Half Well Plate", 332, 332, 84, 12, 8),
  PlateRecord(PlateType.PLATE_96_MINI_TUBES, "96 Mini Tubes", 1105, 1105, 84, 12, 8),
  PlateRecord(PlateType.PLATE_48_WELL, "48 Well Plate", 460, 460, 51, 8, 6),
  PlateRecord(PlateType.PLATE_24_WELL, "24 Well Plate", 452, 452, 51, 6, 4),
  PlateRecord(PlateType.PLATE_12_WELL, "12 Well Plate", 460, 460, 51, 4, 3),
  PlateRecord(PlateType.PLATE_6_WELL, "6 Well Plate", 465, 465, 51, 3, 2),
)
"""The plates a dispenser-only instrument works with."""

_OFFERED: dict[InstrumentFamily, tuple[PlateType, ...]] = {
  InstrumentFamily.EL406: (
    PlateType.PLATE_96_WELL,
    PlateType.PLATE_384_WELL,
    PlateType.PLATE_384_WELL_PCR,
    PlateType.PLATE_1536_WELL,
    PlateType.PLATE_1536_FLANGE,
  ),
  InstrumentFamily.MODEL_405_TS: (
    PlateType.PLATE_96_WELL,
    PlateType.PLATE_384_WELL,
    PlateType.PLATE_384_WELL_PCR,
  ),
  InstrumentFamily.MULTIFLO: (
    PlateType.PLATE_1536_WELL,
    PlateType.PLATE_1536_FLANGE,
    PlateType.PLATE_384_WELL,
    PlateType.PLATE_384_WELL_PCR,
    PlateType.PLATE_384_DEEP_WELL,
    PlateType.PLATE_96_WELL,
    PlateType.PLATE_96_DEEP_WELL,
    PlateType.PLATE_96_HALF_WELL,
    PlateType.PLATE_96_MINI_TUBES,
  ),
  InstrumentFamily.MULTIFLO_FX: tuple(record.plate_type for record in DISPENSER_PLATES),
}
"""Which plates each family offers, in the order it offers them."""


def plates_for(family: InstrumentFamily) -> list[PlateRecord]:
  """The plates an instrument family works with.

  Args:
    family: Which instrument model this is.

  Returns:
    The records, in the order the instrument offers them.
  """
  records = DISPENSER_PLATES if family is InstrumentFamily.MULTIFLO_FX else WASHER_PLATES
  by_type = {record.plate_type: record for record in records}
  return [by_type[plate_type] for plate_type in _OFFERED[family]]


def find(plate_type: PlateType, family: InstrumentFamily) -> PlateRecord | None:
  """The record an instrument family uses for a plate.

  Args:
    plate_type: The plate to look up.
    family: Which instrument model this is.

  Returns:
    The record, or None when the family does not offer that plate.
  """
  for record in plates_for(family):
    if record.plate_type is plate_type:
      return record
  return None
