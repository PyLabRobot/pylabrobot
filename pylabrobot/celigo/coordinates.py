"""Affine coordinate frames for the Celigo (pixel <-> sample-mm <-> stage-mm).

Two coordinate frames are constructed:

* ``sample_to_stage``: plate (sample) mm -> stage mm. Applies scale (``stage_x_scale``,
  ``stage_y_scale``), shear offset (``stage_x_shear_offset``, ``stage_y_shear_offset``),
  X-shear (``stage_shear``), and rotation (``calibrated_plate_to_stage_theta_radians``).
  Reference point is the plate corner in stage coordinates.

* ``image_to_stage``: image pixels -> stage mm. Applies pixel scale
  (``microns_per_pixel_x`` / ``microns_per_pixel_y``) about the image center, rotation
  (``image_to_stage_theta_radians``), and chains through ``sample_to_stage``. Reference
  point is the current FOR+FOV center in sample mm.

Methods provide conversions between coordinate spaces: sample mm <-> stage mm, image
pixels <-> sample mm, image pixels <-> stage mm, etc.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from pylabrobot.celigo.config import CalibrationConfig, HardwareDefaultConfig

Coordinate2D = Tuple[float, float]
Matrix2x2 = Tuple[float, float, float, float]


class _CoordinateFrame:
  """A rotated frame relative to an optional base frame."""

  def __init__(
    self,
    reference_point: Coordinate2D,
    rotation_radians: float,
    parent: Optional["_CoordinateFrame"],
  ):
    self.parent = parent
    # The reference point is expressed in parent units. Remove the parent's scale before
    # chaining coordinate transforms.
    unscaled_reference = (
      parent.to_unscaled_coordinates(reference_point) if parent is not None else reference_point
    )
    self.reference_point = unscaled_reference

    if rotation_radians == 0.0:
      self._rotation_from_parent: Matrix2x2 = (1.0, 0.0, 0.0, 1.0)
      self._rotation_to_parent: Matrix2x2 = (1.0, 0.0, 0.0, 1.0)
    else:
      cosine = math.cos(rotation_radians)
      sine = math.sin(rotation_radians)
      self._rotation_from_parent = (cosine, sine, -sine, cosine)
      self._rotation_to_parent = (cosine, -sine, sine, cosine)

    if parent is None:
      self._cumulative_root_offset = unscaled_reference
      self._cumulative_rotation_to_root = self._rotation_to_parent
    else:
      self._cumulative_root_offset = _add(
        parent._cumulative_root_offset,
        _transform_coordinate(
          parent._cumulative_rotation_to_root,
          unscaled_reference,
        ),
      )
      self._cumulative_rotation_to_root = _multiply_matrices(
        self._rotation_to_parent,
        parent._cumulative_rotation_to_root,
      )

  # subclasses override these (identity here)
  def to_unscaled_coordinates(self, coordinate: Coordinate2D) -> Coordinate2D:
    return coordinate

  def from_unscaled_coordinates(self, coordinate: Coordinate2D) -> Coordinate2D:
    return coordinate

  def to_parent_coordinates(self, local: Coordinate2D) -> Coordinate2D:
    unscaled_local = self.to_unscaled_coordinates(local)
    parent_coordinate = _add(
      _transform_coordinate(self._rotation_to_parent, unscaled_local),
      self.reference_point,
    )
    if self.parent is not None:
      parent_coordinate = self.parent.from_unscaled_coordinates(parent_coordinate)
    return parent_coordinate

  def from_parent_coordinates(self, parent_coordinate: Coordinate2D) -> Coordinate2D:
    unscaled_parent = (
      self.parent.to_unscaled_coordinates(parent_coordinate)
      if self.parent is not None
      else parent_coordinate
    )
    local = _transform_coordinate(
      self._rotation_from_parent,
      _subtract(unscaled_parent, self.reference_point),
    )
    return self.from_unscaled_coordinates(local)

  def to_root_coordinates(self, local: Coordinate2D) -> Coordinate2D:
    unscaled_local = self.to_unscaled_coordinates(local)
    return _add(
      _transform_coordinate(self._cumulative_rotation_to_root, unscaled_local),
      self._cumulative_root_offset,
    )


class _ScaledCoordinateFrame(_CoordinateFrame):
  """A frame with linear scale and offset applied (e.g., pixel <-> mm)."""

  def __init__(
    self,
    units: Coordinate2D,
    offset: Coordinate2D,
    reference_point: Coordinate2D,
    rotation_radians: float,
    parent: Optional[_CoordinateFrame],
  ):
    self._units = units
    self._offset = offset
    super().__init__(reference_point, rotation_radians, parent)

  def from_unscaled_coordinates(self, coordinate: Coordinate2D) -> Coordinate2D:
    return _add(_multiply_coordinates(coordinate, self._units), self._offset)

  def to_unscaled_coordinates(self, coordinate: Coordinate2D) -> Coordinate2D:
    return _divide_coordinates(_subtract(coordinate, self._offset), self._units)


class _ScaledShearCoordinateFrame(_CoordinateFrame):
  """A frame with scale, offset, and X-shear applied (e.g., sample <-> stage)."""

  def __init__(
    self,
    units: Coordinate2D,
    offset: Coordinate2D,
    shear: float,
    reference_point: Coordinate2D,
    rotation_radians: float,
    parent: Optional[_CoordinateFrame],
  ):
    self._units = units
    self._offset = offset
    self._shear = shear
    super().__init__(reference_point, rotation_radians, parent)

  def from_unscaled_coordinates(self, coordinate: Coordinate2D) -> Coordinate2D:
    scaled = _add(_multiply_coordinates(coordinate, self._units), self._offset)
    return _add(scaled, (scaled[1] * self._shear, 0.0))

  def to_unscaled_coordinates(self, coordinate: Coordinate2D) -> Coordinate2D:
    shear_offset = (coordinate[1] * self._shear, 0.0)
    return _divide_coordinates(
      _subtract(_subtract(coordinate, shear_offset), self._offset),
      self._units,
    )


class CoordinateSystems:
  """Affine coordinate frames and conversion methods.

  Build with :meth:`from_config`. ``reference_point_mm`` is the current field center in
  sample coordinates; pass it per FOV, or leave it at the sample origin for plate-relative
  conversions.
  """

  def __init__(self, sample_to_stage: _CoordinateFrame, image_to_stage: _CoordinateFrame):
    self._sample_to_stage = sample_to_stage
    self._image_to_stage = image_to_stage

  @classmethod
  def from_config(
    cls,
    calibration: CalibrationConfig,
    hardware_defaults: HardwareDefaultConfig,
    reference_point_mm: Coordinate2D = (0.0, 0.0),
    binning_divisor: float = 1.0,
  ) -> "CoordinateSystems":
    plate_corner = (
      calibration.calibrated_plate_corner_x
      + hardware_defaults.default_plate_x_corner_stage_coordinate,
      calibration.calibrated_plate_corner_y
      + hardware_defaults.default_plate_y_corner_stage_coordinate,
    )
    sample_to_stage = _ScaledShearCoordinateFrame(
      units=(calibration.stage_x_scale, calibration.stage_y_scale),
      offset=(calibration.stage_x_shear_offset, calibration.stage_y_shear_offset),
      shear=calibration.stage_shear,
      reference_point=plate_corner,
      rotation_radians=calibration.calibrated_plate_to_stage_theta_radians,
      parent=None,
    )
    pixels_per_mm = (
      1000.0 / calibration.microns_per_pixel_x / binning_divisor,
      1000.0 / calibration.microns_per_pixel_y / binning_divisor,
    )
    center_pixel = (
      calibration.image_width_pixels / 2.0,
      calibration.image_height_pixels / 2.0,
    )
    image_to_stage = _ScaledCoordinateFrame(
      units=pixels_per_mm,
      offset=center_pixel,
      reference_point=reference_point_mm,
      rotation_radians=calibration.image_to_stage_theta_radians,
      parent=sample_to_stage,
    )
    return cls(sample_to_stage, image_to_stage)

  # coordinate conversion API ------------------------------------------------

  def sample_mm_to_stage_mm(self, x: float, y: float) -> Coordinate2D:
    return self._sample_to_stage.to_parent_coordinates((x, y))

  def stage_mm_to_sample_mm(self, x: float, y: float) -> Coordinate2D:
    return self._sample_to_stage.from_parent_coordinates((x, y))

  def image_pixel_to_sample_mm(self, px: float, py: float) -> Coordinate2D:
    return self._image_to_stage.to_parent_coordinates((px, py))

  def image_pixel_to_stage_mm(self, px: float, py: float) -> Coordinate2D:
    return self._image_to_stage.to_root_coordinates((px, py))

  def sample_mm_to_image_pixel(self, x: float, y: float) -> Coordinate2D:
    return self._image_to_stage.from_parent_coordinates((x, y))


# -- tiny vector / 2x2-matrix helpers (matrices as (a, b, c, d) = [[a,b],[c,d]]) --


def _add(left: Coordinate2D, right: Coordinate2D) -> Coordinate2D:
  return (left[0] + right[0], left[1] + right[1])


def _subtract(left: Coordinate2D, right: Coordinate2D) -> Coordinate2D:
  return (left[0] - right[0], left[1] - right[1])


def _multiply_coordinates(left: Coordinate2D, right: Coordinate2D) -> Coordinate2D:
  return (left[0] * right[0], left[1] * right[1])


def _divide_coordinates(numerator: Coordinate2D, denominator: Coordinate2D) -> Coordinate2D:
  return (numerator[0] / denominator[0], numerator[1] / denominator[1])


def _transform_coordinate(matrix: Matrix2x2, coordinate: Coordinate2D) -> Coordinate2D:
  return (
    matrix[0] * coordinate[0] + matrix[1] * coordinate[1],
    matrix[2] * coordinate[0] + matrix[3] * coordinate[1],
  )


def _multiply_matrices(left: Matrix2x2, right: Matrix2x2) -> Matrix2x2:
  return (
    left[0] * right[0] + left[1] * right[2],
    left[0] * right[1] + left[1] * right[3],
    left[2] * right[0] + left[3] * right[2],
    left[2] * right[1] + left[3] * right[3],
  )
