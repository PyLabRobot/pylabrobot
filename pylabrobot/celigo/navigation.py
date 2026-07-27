"""Plate / well navigation for the Celigo.

Uses the coordinate systems from :mod:`pylabrobot.celigo.coordinates` to answer the
practical navigation questions the device asks:

* where in stage millimeters is the center of well ``(row, col)``?
* within a stage position, what galvo FOV grid covers the scan area?

The stage makes a coarse move to a Field-Of-Reference (FOR); the galvo sweeps a
serpentine grid of Fields-Of-View (FOV) within its deflection reach before the stage
must step. Effective FOV = frame size minus overlap; FOVs per FOR per axis =
``floor(2*MaxGalvoDeflection / EffectiveFOV)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

from pylabrobot.celigo.config import (
  CalibrationConfig,
  NavigationConfig,
)
from pylabrobot.celigo.coordinates import Coordinate2D, CoordinateSystems
from pylabrobot.resources.plate import Plate


@dataclass(frozen=True)
class _PlateGeometry:
  """SBS microplate geometry, in plate(sample) mm relative to the plate corner.

  ``a1_x_mm`` / ``a1_y_mm`` are the A1 well-center offset from the corner the Celigo
  calibrates to; ``pitch`` is the center-to-center well spacing.
  """

  name: str
  num_rows: int
  num_cols: int
  a1_x_mm: float
  a1_y_mm: float
  pitch_x_mm: float
  pitch_y_mm: float


# Exact installed NEX Corning 3603 profile values (instance origin + nonzero grid start).
_CORNING_3603_96 = _PlateGeometry(
  name="Corning 3603 96-well",
  num_rows=8,
  num_cols=12,
  a1_x_mm=14.196530815027272,
  a1_y_mm=11.113591166551164,
  pitch_x_mm=9.023312301578526,
  pitch_y_mm=9.012954100880759,
)


# The PLR resource describes the nominal physical plate. These small corrections are
# instrument/profile registration values that belong to the Celigo integration, not in
# the user's plate definition.
_CELIGO_GEOMETRY_BY_PLR_MODEL = {
  "Cor_96_wellplate_360ul_Fb": _CORNING_3603_96,
}


def _well_center_sample_mm(plate: Plate, well: str) -> Coordinate2D:
  """Return a PLR well center in the Celigo's top-left plate coordinate frame."""
  if not isinstance(plate, Plate):
    raise TypeError("plate must be a PyLabRobot Plate")
  try:
    item = plate.get_well(well.strip().upper())
  except (IndexError, ValueError) as exc:
    raise ValueError(f"Well {well!r} does not exist on plate {plate.name!r}") from exc
  if item.location is None:
    raise ValueError(f"Well {well!r} on plate {plate.name!r} has no location")
  sample_x = item.location.x + item.get_size_x() / 2
  sample_y = plate.get_size_y() - (item.location.y + item.get_size_y() / 2)

  registered = _CELIGO_GEOMETRY_BY_PLR_MODEL.get(plate.model or "")
  if registered is None:
    return sample_x, sample_y
  if (plate.num_items_y, plate.num_items_x) != (registered.num_rows, registered.num_cols):
    raise ValueError(
      f"Plate model {plate.model!r} has an unexpected "
      f"{plate.num_items_y}x{plate.num_items_x} well grid"
    )

  a1 = plate.get_well("A1")
  a2 = plate.get_well("A2")
  b1 = plate.get_well("B1")
  if a1.location is None or a2.location is None or b1.location is None:
    raise ValueError(f"Plate model {plate.model!r} has incomplete registration wells")
  nominal_a1_x = a1.location.x + a1.get_size_x() / 2
  nominal_a1_y = plate.get_size_y() - (a1.location.y + a1.get_size_y() / 2)
  nominal_pitch_x = a2.location.x - a1.location.x
  nominal_pitch_y = a1.location.y - b1.location.y
  if nominal_pitch_x == 0 or nominal_pitch_y == 0:
    raise ValueError(f"Plate model {plate.model!r} has invalid well pitch")
  return (
    registered.a1_x_mm + (sample_x - nominal_a1_x) * registered.pitch_x_mm / nominal_pitch_x,
    registered.a1_y_mm + (sample_y - nominal_a1_y) * registered.pitch_y_mm / nominal_pitch_y,
  )


def well_to_stage_mm(
  plate: Plate,
  well: str,
  coordinate_systems: CoordinateSystems,
) -> Coordinate2D:
  """Stage mm for the center of a named well (e.g. ``"A1"``)."""
  sample_x_mm, sample_y_mm = _well_center_sample_mm(plate, well)
  return coordinate_systems.sample_mm_to_stage_mm(sample_x_mm, sample_y_mm)


def effective_fov_mm(
  calibration: CalibrationConfig,
  navigation: NavigationConfig,
) -> Coordinate2D:
  """Frame size minus overlap, per axis (``EffectiveFOVMM``)."""
  frame_x = calibration.image_width_pixels * calibration.microns_per_pixel_x / 1000.0
  frame_y = calibration.image_height_pixels * calibration.microns_per_pixel_y / 1000.0
  return (
    frame_x - 2 * navigation.frame_overlap_x_mm,
    frame_y - 2 * navigation.frame_overlap_y_mm,
  )


def fields_of_view_per_field_of_reference(
  calibration: CalibrationConfig,
  navigation: NavigationConfig,
) -> "Tuple[int, int]":
  """How many FOVs fit per FOR per axis within the galvo's reach."""
  effective_x_mm, effective_y_mm = effective_fov_mm(calibration, navigation)
  columns = (
    math.floor(2 * navigation.max_galvo_deflection_x_mm / effective_x_mm)
    if effective_x_mm > 0
    else 1
  )
  rows = (
    math.floor(2 * navigation.max_galvo_deflection_y_mm / effective_y_mm)
    if effective_y_mm > 0
    else 1
  )
  return max(1, columns), max(1, rows)


def galvo_field_of_view_offsets_mm(
  calibration: CalibrationConfig,
  navigation: NavigationConfig,
) -> List[Coordinate2D]:
  """Galvo FOV-center offsets (mm, relative to FOR center) in serpentine order.

  :meth:`pylabrobot.celigo.galvo.Galvo.voltages_for_offset` combines each offset with
  the calibrated imaging center and logical-filter correction.
  """
  columns, rows = fields_of_view_per_field_of_reference(calibration, navigation)
  effective_x_mm, effective_y_mm = effective_fov_mm(calibration, navigation)
  # center the grid about (0, 0)
  first_column = -(columns - 1) / 2.0
  first_row = -(rows - 1) / 2.0
  offsets: List[Coordinate2D] = []
  for row in range(rows):
    column_indices = range(columns) if row % 2 == 0 else range(columns - 1, -1, -1)
    offsets.extend(
      (
        (
          (first_column + column) * effective_x_mm,
          (first_row + row) * effective_y_mm,
        )
        for column in column_indices
      )
    )
  return offsets
