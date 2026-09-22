"""Resolving a labware resource to the plate an instrument works it as.

An instrument is told what sits on its carrier as one of a fixed set of formats, so a
:class:`~pylabrobot.resources.Plate` has to be matched to one of them before anything can run. The
match is made on the resource's own geometry -- how many columns and rows it has, and how deep its
wells are -- and never on a nearest fit: labware that does not land on exactly one format is an
error naming the formats it could have been, so that the caller says which one it is rather than
this module deciding for them.
"""

from __future__ import annotations

from typing import Iterable

from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.plate_geometry.plates import find, plates_for
from pylabrobot.resources import Plate

DEEP_WELL_DEPTH = 20.0
"""From how deep, in mm, a well counts as a deep well.

This separates the two formats that differ in nothing else: a plate of a given number of wells has
both a standard and a deep-well format, and a well twice as deep as any standard plate's is what
tells them apart. Nothing else is decided by a threshold.
"""

SELECTION_ONLY: frozenset[PlateType] = frozenset(
  {
    PlateType.PLATE_96_HALF_WELL,
    PlateType.PLATE_96_MINI_TUBES,
    PlateType.PLATE_384_WELL_PCR,
    PlateType.PLATE_1536_FLANGE,
    PlateType.TUBES_20_12X75,
    PlateType.TUBES_20_13X100,
    PlateType.TEST_PLATE_96_WELL_MB,
    PlateType.TEST_PLATE_96_WELL_BD,
  }
)
"""The formats that are never resolved from a resource, only named outright.

Each of them shares its column and row count with an ordinary plate and differs in something the
resource does not carry -- a well shape, a flange, a tube, or that it is calibration labware -- so
resolving one would be a guess.
"""


def resolve(
  plate: Plate,
  family: InstrumentFamily,
  plate_type: PlateType | None = None,
) -> PlateRecord:
  """The format an instrument works a plate as.

  Args:
    plate: The labware on the carrier.
    family: Which instrument model this is, which decides the formats on offer and the heights they
      are worked at.
    plate_type: The format to use, for labware this cannot resolve on its own or that is to be
      worked as something other than what it resolves to. Checked against the formats the family
      offers, and otherwise used as given.

  Returns:
    The record the instrument works that format by.

  Raises:
    ValueError: If ``plate_type`` is a format the family does not offer, if the plate's columns and
      rows match no format it offers, or if they match more than one. The message names the
      candidates, which are what ``plate_type`` may be set to.
  """
  offered = plates_for(family)
  if plate_type is not None:
    named = find(plate_type, family)
    if named is None:
      raise ValueError(
        f"{family.name} does not offer {plate_type.name}; it offers "
        f"{_names(record.plate_type for record in offered)}"
      )
    return named

  columns, rows = plate.num_items_x, plate.num_items_y
  shaped = [record for record in offered if record.columns == columns and record.rows == rows]
  candidates = [record for record in shaped if record.plate_type not in SELECTION_ONLY]
  if len(candidates) > 1:
    candidates = _by_depth(candidates, _well_depth(plate))

  if len(candidates) == 1:
    return candidates[0]
  if not shaped:
    raise ValueError(
      f"{family.name} works no {columns}x{rows} plate; it offers "
      f"{_names(record.plate_type for record in offered)}"
    )
  raise ValueError(
    f"a {columns}x{rows} plate {plate.get_item(0).get_size_z():g} mm deep could be any of "
    f"{_names(record.plate_type for record in shaped)} on a {family.name}; name one as plate_type"
  )


def _by_depth(candidates: list[PlateRecord], depth: float) -> list[PlateRecord]:
  """Narrow candidates of one shape to the ones matching how deep the wells are.

  Args:
    candidates: The records of one column and row count.
    depth: How deep a well is, in mm.

  Returns:
    The candidates whose format is a deep-well one when the wells are deep, and the rest when they
    are not. Unchanged when that leaves nothing, so the caller reports an ambiguity rather than an
    absence.
  """
  deep = depth >= DEEP_WELL_DEPTH
  narrowed = [record for record in candidates if _is_deep_well(record.plate_type) == deep]
  return narrowed if narrowed else candidates


def _is_deep_well(plate_type: PlateType) -> bool:
  """Whether a format is a deep-well one.

  Args:
    plate_type: The format to ask about.

  Returns:
    Whether it is.
  """
  return plate_type in (PlateType.PLATE_96_DEEP_WELL, PlateType.PLATE_384_DEEP_WELL)


def _well_depth(plate: Plate) -> float:
  """How deep a plate's wells are, in mm.

  Args:
    plate: The labware to measure.

  Returns:
    The depth of its first well, or the plate's own height for labware carrying no wells.
  """
  wells = plate.get_all_items()
  return wells[0].get_size_z() if wells else plate.get_size_z()


def _names(plate_types: Iterable[PlateType]) -> str:
  """Format types for an error message.

  Args:
    plate_types: The types to name, in the order they should be read.

  Returns:
    Their names, comma separated.
  """
  return ", ".join(plate_type.name for plate_type in plate_types)
