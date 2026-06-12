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
from typing import List, Tuple

from pylabrobot.celigo.config import (
  CalibrationConfig,
  NavigationConfig,
)
from pylabrobot.celigo.coordinates import Coordinate2D, CoordinateSystems
from pylabrobot.resources.plate import Plate


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
  return (
    item.location.x + item.get_size_x() / 2,
    plate.get_size_y() - (item.location.y + item.get_size_y() / 2),
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
