"""The plates an instrument works with, and the geometry it works them by."""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.plate_geometry.plates import (
  DISPENSER_PLATES,
  WASHER_PLATES,
  find,
  plates_for,
)
from pylabrobot.agilent.biotek.lhc.plate_geometry.resolution import (
  DEEP_WELL_DEPTH,
  SELECTION_ONLY,
  resolve,
)

__all__ = [
  "DEEP_WELL_DEPTH",
  "DISPENSER_PLATES",
  "SELECTION_ONLY",
  "WASHER_PLATES",
  "PlateRecord",
  "find",
  "plates_for",
  "resolve",
]
