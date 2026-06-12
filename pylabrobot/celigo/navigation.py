"""Plate / well navigation for the Celigo.

Ties the coordinate systems (:mod:`pylabrobot.celigo.coordinates`) and per-axis
encoder math (:mod:`pylabrobot.celigo.transforms`) together to answer the practical
questions the backend asks:

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
class PlateGeometry:
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

  def well_center_sample_mm(self, row: int, col: int) -> Vec:
    """Sample(plate) mm of the center of well ``(row, col)`` (0-indexed)."""
    if not (0 <= row < self.num_rows and 0 <= col < self.num_cols):
      raise ValueError(f"Well ({row},{col}) out of range for {self.name}")
    return (self.a1_x_mm + col * self.pitch_x_mm, self.a1_y_mm + row * self.pitch_y_mm)

  @staticmethod
  def well_name(row: int, col: int) -> str:
    return f"{chr(ord('A') + row)}{col + 1}"

  @staticmethod
  def parse_well(name: str) -> "Tuple[int, int]":
    name = name.strip().upper()
    return ord(name[0]) - ord("A"), int(name[1:]) - 1


# Standard SBS 96-well (e.g. Corning 3603): A1 center 14.38/11.24 mm from corner, 9 mm pitch.
CORNING_3603_96 = PlateGeometry(
  name="Corning 3603 96-well",
  num_rows=8,
  num_cols=12,
  a1_x_mm=14.38,
  a1_y_mm=11.24,
  pitch_x_mm=9.0,
  pitch_y_mm=9.0,
)


def well_to_stage_mm(plate: PlateGeometry, well: str, coords: CoordinateSystems) -> Vec:
  """Stage mm for the center of a named well (e.g. ``"A1"``)."""
  row, col = plate.parse_well(well)
  sx, sy = plate.well_center_sample_mm(row, col)
  return coords.sample_mm_to_stage_mm(sx, sy)


def well_to_encoder_ticks(
  plate: PlateGeometry,
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
