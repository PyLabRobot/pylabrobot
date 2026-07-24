"""Plate / well navigation for the Celigo.

Ties the coordinate systems (:mod:`pylabrobot.celigo.coordinates`) and per-axis
encoder math (:mod:`pylabrobot.celigo.transforms`) together to answer the practical
questions the device asks:

* where (in stage mm / encoder ticks) is the center of well ``(row, col)``?
* within a stage position, what galvo FOV grid covers the scan area?

The stage makes a coarse move to a Field-Of-Reference (FOR); the galvo sweeps a
serpentine grid of Fields-Of-View (FOV) within its deflection reach before the stage
must step. Effective FOV = frame size minus overlap; FOVs per FOR per axis =
``floor(2*MaxGalvoDeflection / EffectiveFOV)``.
"""

from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Tuple

from pylabrobot.celigo.config import (
  AxisConfig,
  CalibrationConfig,
  _all_leaf_scalars,
  _FromXmlMixin,
)
from pylabrobot.celigo.coordinates import CoordinateSystems
from pylabrobot.celigo.transforms import mm_to_encoder_ticks
from pylabrobot.resources.plate import Plate

Vec = Tuple[float, float]


@dataclass
class NavigationConfig(_FromXmlMixin):
  """Galvo reach + frame overlap (``NavigationConfig.xml``)."""

  frame_overlap_x_mm: float = 0.0
  frame_overlap_y_mm: float = 0.0
  max_galvo_deflection_x_mm: float = 0.0
  max_galvo_deflection_y_mm: float = 0.0
  source_path: "str | None" = None
  extra: Dict[str, str] = field(default_factory=dict)

  _FIELD_MAP: ClassVar[Dict[str, "tuple[str, type]"]] = {
    "FrameOverlapXMM": ("frame_overlap_x_mm", float),
    "FrameOverlapYMM": ("frame_overlap_y_mm", float),
    "MaxGalvoDeflectionXMM": ("max_galvo_deflection_x_mm", float),
    "MaxGalvoDeflectionYMM": ("max_galvo_deflection_y_mm", float),
  }

  @classmethod
  def from_xml(cls, path: str) -> "NavigationConfig":
    obj = cls.from_scalars(_all_leaf_scalars(ET.parse(path).getroot()))
    obj.source_path = os.path.abspath(path)
    return obj


def load_navigation(path: str) -> NavigationConfig:
  """Parse ``NavigationConfig.xml`` into a :class:`NavigationConfig`."""
  return NavigationConfig.from_xml(path)


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


def _well_center_sample_mm(plate: Plate, well: str) -> Vec:
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
    registered.a1_x_mm
    + (sample_x - nominal_a1_x) * registered.pitch_x_mm / nominal_pitch_x,
    registered.a1_y_mm
    + (sample_y - nominal_a1_y) * registered.pitch_y_mm / nominal_pitch_y,
  )


def well_to_stage_mm(plate: Plate, well: str, coords: CoordinateSystems) -> Vec:
  """Stage mm for the center of a named well (e.g. ``"A1"``)."""
  sx, sy = _well_center_sample_mm(plate, well)
  return coords.sample_mm_to_stage_mm(sx, sy)


def well_to_encoder_ticks(
  plate: Plate,
  well: str,
  coords: CoordinateSystems,
  x_axis: AxisConfig,
  y_axis: AxisConfig,
) -> "Tuple[int, int]":
  """(x_ticks, y_ticks) EZStepper targets for the center of a named well."""
  stage_x, stage_y = well_to_stage_mm(plate, well, coords)
  return mm_to_encoder_ticks(stage_x, x_axis), mm_to_encoder_ticks(stage_y, y_axis)


def effective_fov_mm(calibration: CalibrationConfig, nav: NavigationConfig) -> Vec:
  """Frame size minus overlap, per axis (``EffectiveFOVMM``)."""
  frame_x = calibration.image_width_pixels * calibration.microns_per_pixel_x / 1000.0
  frame_y = calibration.image_height_pixels * calibration.microns_per_pixel_y / 1000.0
  return (
    frame_x - 2 * nav.frame_overlap_x_mm,
    frame_y - 2 * nav.frame_overlap_y_mm,
  )


def fovs_per_for(calibration: CalibrationConfig, nav: NavigationConfig) -> "Tuple[int, int]":
  """How many FOVs fit per FOR per axis within the galvo's reach."""
  eff_x, eff_y = effective_fov_mm(calibration, nav)
  nx = int(math.floor(2 * nav.max_galvo_deflection_x_mm / eff_x)) if eff_x > 0 else 1
  ny = int(math.floor(2 * nav.max_galvo_deflection_y_mm / eff_y)) if eff_y > 0 else 1
  return max(1, nx), max(1, ny)


def galvo_fov_offsets_mm(calibration: CalibrationConfig, nav: NavigationConfig) -> List[Vec]:
  """Galvo FOV-center offsets (mm, relative to FOR center) in serpentine order.

  These feed :func:`pylabrobot.celigo.transforms.galvo_mm_to_dac` to produce the galvo
  command for each FOV imaged at one stage position.
  """
  nx, ny = fovs_per_for(calibration, nav)
  eff_x, eff_y = effective_fov_mm(calibration, nav)
  # center the grid about (0, 0)
  x0 = -(nx - 1) / 2.0
  y0 = -(ny - 1) / 2.0
  offsets: List[Vec] = []
  for j in range(ny):
    cols = range(nx) if j % 2 == 0 else range(nx - 1, -1, -1)  # serpentine
    for i in cols:
      offsets.append(((x0 + i) * eff_x, (y0 + j) * eff_y))
  return offsets
