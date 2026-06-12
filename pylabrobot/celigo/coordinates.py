"""Affine coordinate frames for the Celigo (pixel <-> sample-mm <-> stage-mm).

Two coordinate frames are constructed:

* ``sample_to_stage``: plate (sample) mm -> stage mm. Applies scale (``stage_x_scale``,
  ``stage_y_scale``), shear offset (``stage_x_shear_offset``, ``stage_y_shear_offset``),
  X-shear (``stage_shear``), and rotation (``calibrated_plate_to_stage_theta_radians``).
  Reference point is the plate corner in stage coordinates.

* ``image_to_stage``: image pixels -> stage mm. Applies pixel scale
  (``microns_per_pixel_x`` / ``microns_per_pixel_y``) about the image center, rotation
  (``image_to_stage_theta_radians``), and chains through ``sample_to_stage``. Reference
  point is the current FOR+FOV position in stage mm.

Methods provide conversions between coordinate spaces: sample mm <-> stage mm, image
pixels <-> sample mm, image pixels <-> stage mm, etc.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from pylabrobot.celigo.config import CalibrationConfig, HardwareDefaultConfig

Vec = Tuple[float, float]


class _Frame:
  """A rotated frame relative to an optional base frame."""

  def __init__(self, ref_point: Vec, theta: float, base: Optional["_Frame"]):
    self.base = base
    # refPoint is expressed in base units; if chained, the base removes its scale first.
    ref = base.remove_scale(ref_point) if base is not None else ref_point
    self.ref_point = ref

    if theta == 0.0:
      self._rot = (1.0, 0.0, 0.0, 1.0)
      self._rot_inv = (1.0, 0.0, 0.0, 1.0)
    else:
      c, s = math.cos(theta), math.sin(theta)
      self._rot = (c, s, -s, c)  # R
      self._rot_inv = (c, -s, s, c)  # R^-1

    if base is None:
      self._ref_inv_cumulative = ref
      self._rot_inv_cumulative = self._rot_inv
    else:
      self._ref_inv_cumulative = _add(
        base._ref_inv_cumulative, _matvec(base._rot_inv_cumulative, ref)
      )
      self._rot_inv_cumulative = _matmul(self._rot_inv, base._rot_inv_cumulative)

  # subclasses override these (identity here)
  def remove_scale(self, v: Vec) -> Vec:
    return v

  def apply_scale(self, v: Vec) -> Vec:
    return v

  def get_base_coord(self, local: Vec) -> Vec:
    v = self.remove_scale(local)
    base_coord = _add(_matvec(self._rot_inv, v), self.ref_point)
    if self.base is not None:
      base_coord = self.base.apply_scale(base_coord)
    return base_coord

  def get_local_coord(self, base_coord: Vec) -> Vec:
    b = self.base.remove_scale(base_coord) if self.base is not None else base_coord
    local = _matvec(self._rot, _sub(b, self.ref_point))
    return self.apply_scale(local)

  def get_lowest_base_coord(self, local: Vec) -> Vec:
    v = self.remove_scale(local)
    return _add(_matvec(self._rot_inv_cumulative, v), self._ref_inv_cumulative)


class _ScaledFrame(_Frame):
  """A frame with linear scale and offset applied (e.g., pixel <-> mm)."""

  def __init__(self, units: Vec, offset: Vec, ref_point: Vec, theta: float, base):
    self._units = units
    self._offset = offset
    super().__init__(ref_point, theta, base)

  def apply_scale(self, v: Vec) -> Vec:
    return _add(_hadamard(v, self._units), self._offset)

  def remove_scale(self, v: Vec) -> Vec:
    return _hdiv(_sub(v, self._offset), self._units)


class _ScaledShearFrame(_Frame):
  """A frame with scale, offset, and X-shear applied (e.g., sample <-> stage)."""

  def __init__(self, units: Vec, offset: Vec, shear: float, ref_point: Vec, theta: float, base):
    self._units = units
    self._offset = offset
    self._shear = shear
    super().__init__(ref_point, theta, base)

  def apply_scale(self, v: Vec) -> Vec:
    w = _add(_hadamard(v, self._units), self._offset)
    return _add(w, (w[1] * self._shear, 0.0))

  def remove_scale(self, v: Vec) -> Vec:
    sheared = (v[1] * self._shear, 0.0)
    return _hdiv(_sub(_sub(v, sheared), self._offset), self._units)


class CoordinateSystems:
  """Affine coordinate frames and conversion methods.

  Build with :meth:`from_config`. ``reference_point_mm`` is the current stage position
  the image is taken at; pass it per FOV, or leave at the origin for plate-relative
  conversions.
  """

  def __init__(self, sample_to_stage: _Frame, image_to_stage: _Frame):
    self._sample_to_stage = sample_to_stage
    self._image_to_stage = image_to_stage

  @classmethod
  def from_config(
    cls,
    calibration: CalibrationConfig,
    hardware_defaults: HardwareDefaultConfig,
    reference_point_mm: Vec = (0.0, 0.0),
    binning_divisor: float = 1.0,
  ) -> "CoordinateSystems":
    plate_corner = (
      calibration.calibrated_plate_corner_x
      + hardware_defaults.default_plate_x_corner_stage_coordinate,
      calibration.calibrated_plate_corner_y
      + hardware_defaults.default_plate_y_corner_stage_coordinate,
    )
    sample_to_stage = _ScaledShearFrame(
      units=(calibration.stage_x_scale, calibration.stage_y_scale),
      offset=(calibration.stage_x_shear_offset, calibration.stage_y_shear_offset),
      shear=calibration.stage_shear,
      ref_point=plate_corner,
      theta=calibration.calibrated_plate_to_stage_theta_radians,
      base=None,
    )
    pixels_per_mm = (
      1000.0 / calibration.microns_per_pixel_x / binning_divisor,
      1000.0 / calibration.microns_per_pixel_y / binning_divisor,
    )
    center_pixel = (
      calibration.image_width_pixels / 2.0,
      calibration.image_height_pixels / 2.0,
    )
    image_to_stage = _ScaledFrame(
      units=pixels_per_mm,
      offset=center_pixel,
      ref_point=reference_point_mm,
      theta=calibration.image_to_stage_theta_radians,
      base=sample_to_stage,
    )
    return cls(sample_to_stage, image_to_stage)

  # coordinate conversion API ------------------------------------------------

  def sample_mm_to_stage_mm(self, x: float, y: float) -> Vec:
    return self._sample_to_stage.get_base_coord((x, y))

  def stage_mm_to_sample_mm(self, x: float, y: float) -> Vec:
    return self._sample_to_stage.get_local_coord((x, y))

  def image_pixel_to_sample_mm(self, px: float, py: float) -> Vec:
    return self._image_to_stage.get_base_coord((px, py))

  def image_pixel_to_stage_mm(self, px: float, py: float) -> Vec:
    return self._image_to_stage.get_lowest_base_coord((px, py))

  def sample_mm_to_image_pixel(self, x: float, y: float) -> Vec:
    return self._image_to_stage.get_local_coord((x, y))


# -- tiny vector / 2x2-matrix helpers (matrices as (a, b, c, d) = [[a,b],[c,d]]) --


def _add(u: Vec, v: Vec) -> Vec:
  return (u[0] + v[0], u[1] + v[1])


def _sub(u: Vec, v: Vec) -> Vec:
  return (u[0] - v[0], u[1] - v[1])


def _hadamard(u: Vec, v: Vec) -> Vec:
  return (u[0] * v[0], u[1] * v[1])


def _hdiv(u: Vec, v: Vec) -> Vec:
  return (u[0] / v[0], u[1] / v[1])


def _matvec(m: "Tuple[float, float, float, float]", v: Vec) -> Vec:
  return (m[0] * v[0] + m[1] * v[1], m[2] * v[0] + m[3] * v[1])


def _matmul(
  m: "Tuple[float, float, float, float]", n: "Tuple[float, float, float, float]"
) -> "Tuple[float, float, float, float]":
  return (
    m[0] * n[0] + m[1] * n[2],
    m[0] * n[1] + m[1] * n[3],
    m[2] * n[0] + m[3] * n[2],
    m[2] * n[1] + m[3] * n[3],
  )
