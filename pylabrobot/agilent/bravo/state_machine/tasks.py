"""Bravo operations as ordered, resumable state-machine tasks.

Each task defines an ordered sequence of named async steps that
:class:`~.engine.StateMachineEngine` executes. All motion goes through the
:class:`~..controllers.base.BravoController` interface, so the same task
runs unchanged against real hardware or a simulated one.

This module implements: :class:`InitializeTask`, :class:`HomeTask`,
:class:`DockGripperTask`, :class:`MoveToLocationTask`,
:class:`AspirateTask`, :class:`DispenseTask`, :class:`MixTask`,
:class:`TipsOnTask`, :class:`TipsOffTask`, :class:`PickPlaceTask`,
:class:`GripperTeachMoveTask`, :class:`DelidPlateTask`,
:class:`RelidPlateTask`, and :class:`ScanStackHeightTask`.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from ..config import BravoMachineConfig
from ..controllers.agile_7612 import Agile7612Controller, _home_reg_register
from ..controllers.base import AxisMoveInfo, BravoController, JogParams
from ..controllers.simulation import SimulationController
from ..darwin.controller import DarwinController
from ..deck.geometry import well_center_offset_from_teachpoint_mm
from ..deck.labware import (
  DeckState,
  Labware,
  generated_lid_metadata,
  lid_gripper_offset_mm,
  lid_thickness_mm,
  synthesize_lid_labware,
)
from ..deck.layout import DeckLayout
from ..deck.teachpoints import Teachpoints
from ..head_mode import (
  HeadMode,
  PlateSelection,
  TipSelection,
  head_anchor_cell,
  head_geometry_for_type,
  head_mode_offsets_mm,
  head_selected_ranges,
  selected_anchor_ranges,
  tipbox_anchor_cell,
)
from ..protocol.commands import CommandID, LightCommandData
from ..tip_offsets import ResolvedTipOffsets
from ..tips import get_tip_length_mm
from ..types import (
  ALL_AXES,
  AXIS_EPSILON,
  GRIPPER_THICKNESS,
  GRIPPER_TO_BASE_OF_HEAD_GAP,
  LT_TIP_CURRENT_TABLE,
  OPEN_GRIPPER_POSITION,
  ST_TIP_CURRENT_TABLE,
  TIPBOX_JOG_TOLERANCE,
  Z_CLEARANCE,
  Z_SAFE_POSITION_DEFAULT,
  Axis,
  DeviceStateFlag,
  GripperDetectionState,
  HeadType,
  LightColor,
  SpeedLevel,
  axis_display_name,
  axis_label,
  head_type_is_assaymap,
  head_type_is_disposable,
  head_type_is_fixed,
  head_type_is_pintool,
  interpolate_tip_current,
  safe_home_order,
)
from .engine import ErrorAction, StateMachineTask, TaskStatus

logger = logging.getLogger(__name__)

Z_SAFE = Z_SAFE_POSITION_DEFAULT
"""Default safe Z position, in millimetres, used when a task's caller does
not supply one explicitly."""

_GRIPPER_RECESS_DEPTH = -20.0
"""The Zg position, in millimetres, that nests the gripper clear of the deck."""

_GRIPPER_OPEN_TOLERANCE_MM = 0.2
"""Position tolerance, in millimetres, for verifying the gripper reached its
open target."""

_LENGTH_DIFFERENCE_96_TO_384 = 0.7
"""Millimetres added to a fixed-tip head's pick/place Z-solve to correct for
the physical length difference between the 96- and 384-channel fixed-tip
heads."""

_PLATE_HANDLING_ZG_MAX = 100.0
"""The highest Zg a pick/place/scan Z-solve will target, regardless of the
axis's full configured travel."""

_SCAN_SENSOR_STANDOFF_MM = 0.0
"""How far above a plate's top face the gripper's plate sensor fires.

A property of the gripper, not of the labware, so it is a single constant --
but it has to be measured on hardware. Scan a location holding a known
number of plates and read ``raw_measured_height_mm`` from the result, which
is the trigger height above the plate pad::

    standoff = raw - (plate_height + (N - 1) * stack_height)

Zero means the sensor fires level with the top face.
"""

_PICK_PLACE_GRIP_TARGET = 9.0
"""The G-axis position, in millimetres, the gripper closes to when gripping
a plate or lid."""

_NO_TIPS_HEAD_PROTRUSION_MM = 15.0
"""How far the head body protrudes below the gripper's jaws when no tips are
mounted, for head-clearance checking during a carry."""

_PICKUP_FAILURE_G_THRESHOLD_MM = 10.0
"""The G position, in millimetres, at or beyond which a just-completed grip
is judged to have closed on nothing rather than a plate."""


def _stacking_support_height_for_count(count: int, stacking_thickness_mm: float) -> float:
  """Return the support height a stack of *count* identical plates presents.

  Args:
    count: Number of plates in the stack.
    stacking_thickness_mm: How much height one plate adds to the next when
      stacked.

  Returns:
    ``0.0`` for zero or one plate (nothing is supported below the top
    plate); otherwise ``(count - 1) * stacking_thickness_mm``.
  """
  count = max(0, int(count))
  if count <= 1:
    return 0.0
  return max(0.0, float(count - 1) * float(stacking_thickness_mm))


def _stack_total_height_for_count(
  count: int, top_plate_height_mm: float, stacking_thickness_mm: float
) -> float:
  """Return the total height of a stack of *count* identical plates.

  Args:
    count: Number of plates in the stack.
    top_plate_height_mm: The height of one plate.
    stacking_thickness_mm: How much height one plate adds to the next when
      stacked.

  Returns:
    ``0.0`` for zero plates; otherwise the top plate's own height plus the
    support height of every plate below it.
  """
  count = max(0, int(count))
  if count <= 0:
    return 0.0
  return max(
    0.0,
    float(top_plate_height_mm) + _stacking_support_height_for_count(count, stacking_thickness_mm),
  )


def _infer_stack_count_from_scan_height(
  scan_height_mm: float,
  stacking_thickness_mm: float,
  top_plate_height_mm: float = 0.0,
) -> int:
  """Infer how many plates are stacked from the scanned top-of-stack height.

  ``scan_height_mm`` is the height of the TOP of the stack above the
  support surface -- i.e. the gripper's descent distance during the scan --
  so it already includes the top plate's own height. The support height
  *under* the top plate is therefore ``scan_height_mm - top_plate_height_mm``;
  for ``N`` identical plates that equals ``(N - 1) * stacking_thickness``.
  Hence::

      N = round((scan_height - top_plate_height) / stacking_thickness) + 1

  Subtracting the top plate's height is what makes the count
  plate-height-independent: a single plate of any height leaves ~0 support
  and resolves to 1.

  Args:
    scan_height_mm: The measured top-of-stack height above the support
      surface.
    stacking_thickness_mm: How much height one plate adds to the next when
      stacked.
    top_plate_height_mm: The height of the top plate, subtracted before
      inferring the count. Defaults to ``0`` for a caller that already
      passes a support height rather than a top-of-stack height.

  Returns:
    The inferred plate count, at least 1.
  """
  if stacking_thickness_mm <= 0.0:
    return 1
  support_mm = max(0.0, float(scan_height_mm) - float(top_plate_height_mm))
  return max(1, int(round(support_mm / float(stacking_thickness_mm))) + 1)


def _gripper_head_offsets(head_type: HeadType) -> Tuple[float, float]:
  """Return the (x, y) gripper-to-head origin offset for a head type.

  Args:
    head_type: The installed head type.

  Returns:
    ``(-2.25, -2.25)`` for a 384-pitch or 16-channel head, where the
    gripper's jaw center does not coincide with the head's nozzle-array
    origin; ``(0.0, 0.0)`` for every other head type.
  """
  if head_type in {"384_d_70", "384_d_70_s2", "384_f_50", "16_d_st", "384_pintool"}:
    return (-2.25, -2.25)
  return (0.0, 0.0)


@dataclass(frozen=True)
class PickPlacePositions:
  """The solved Z/Zg targets for one pick-and-place operation.

  Attributes:
    pick_z: Head Z at the source location, during the grip.
    pick_zg: Gripper Zg at the source location, during the grip.
    carry_z: Head Z while transiting between locations.
    carry_zg: Gripper Zg while transiting between locations.
    place_z: Head Z at the destination location, during release.
    place_zg: Gripper Zg at the destination location, during release.
  """

  pick_z: float
  pick_zg: float
  carry_z: float
  carry_zg: float
  place_z: float
  place_zg: float


_NEIGHBOR_CLEARANCE_SAFETY_MM = 2.0
"""Millimetres of margin subtracted from a neighbor-clearance check's allowed
top plane, so a move is rejected before it would just barely clip a neighbor."""

_DECK_OVERLAP_EPSILON_MM = 1e-6
"""Millimetre tolerance for treating two footprints as touching rather than
overlapping."""

# Subset-collision checks model occupied locations from the taught A1
# reference to the platepad edges, and head overlap from the head body's
# A1-based envelope rather than just the active nozzle array.
_PLATEPAD_A1_TO_FRONT_MM = 17.12
_PLATEPAD_A1_TO_LEFT_MM = 13.97
_PLATEPAD_A1_TO_BACK_MM = 116.10
_PLATEPAD_A1_TO_RIGHT_MM = 76.96
_HEAD_BODY_EXTRA_FRONT_MM = _PLATEPAD_A1_TO_FRONT_MM - 2.25
_HEAD_BODY_EXTRA_BACK_MM = _PLATEPAD_A1_TO_BACK_MM - 105.75
_HEAD_BODY_EXTRA_LEFT_MM = _PLATEPAD_A1_TO_LEFT_MM - 2.25
_HEAD_BODY_EXTRA_RIGHT_MM = _PLATEPAD_A1_TO_RIGHT_MM - 69.75
_HEAD_GRIPPER_BACK_OVERHANG_MM = 44.75


@dataclass(frozen=True)
class LiquidZGeometry:
  """The Z-axis geometry of one liquid-handling target: teachpoint, tip, and well.

  Attributes:
    teachpoint_z: The location's taught head Z, in millimetres.
    teach_tip_length_mm: The tip length the teachpoint was taught with, or
      ``None`` for a fixed-tip head.
    attached_tip_length_mm: The currently attached tip's measured length,
      or ``None`` for a fixed-tip head.
    tip_delta_mm: ``teach_tip_length_mm - attached_tip_length_mm``, the
      correction applied between a tip-relative Z and a head-relative Z.
      ``0`` for a fixed-tip head.
    labware_height_mm: The target labware's height, in millimetres.
    well_depth_mm: The target well's depth, in millimetres.
    top_plane_tip_z: The tip's Z when it is level with the labware's top face.
    well_bottom_tip_z: The tip's Z when it is level with the well bottom.
    target_tip_z: The tip's Z at the requested distance from the well bottom.
    top_plane_head_z: The head's Z when the (implied) tip is level with the
      labware's top face.
    target_head_z: The head's Z at the requested distance from the well
      bottom.
    distance_from_bottom_mm: The requested clearance above the well bottom.
  """

  teachpoint_z: float
  teach_tip_length_mm: Optional[float]
  attached_tip_length_mm: Optional[float]
  tip_delta_mm: float
  labware_height_mm: float
  well_depth_mm: float
  top_plane_tip_z: float
  well_bottom_tip_z: float
  target_tip_z: float
  top_plane_head_z: float
  target_head_z: float
  distance_from_bottom_mm: float


def _liquid_labware_height_mm(labware: Optional[Labware]) -> float:
  return float(labware.height if labware is not None else 0.0)


def _liquid_well_depth_mm(labware: Optional[Labware]) -> float:
  if labware is None:
    return 0.0
  return float((labware.metadata or {}).get("well_depth_mm") or 0.0)


def _build_liquid_z_geometry(
  *,
  teachpoints: Teachpoints,
  location: int,
  labware: Optional[Labware],
  head_type: HeadType,
  teach_tip_length_mm: Optional[float],
  attached_tip_length_mm: Optional[float],
  tips_on_head: bool,
  distance_from_bottom_mm: float,
) -> LiquidZGeometry:
  """Compute the Z geometry for a liquid-handling operation at a location.

  Args:
    teachpoints: The deck teachpoints to read the location's Z from.
    location: The deck location to build geometry for.
    labware: The labware at the location, or ``None`` if unknown.
    head_type: The installed head type.
    teach_tip_length_mm: The tip length the teachpoint was taught with.
    attached_tip_length_mm: The currently attached tip's measured length.
    tips_on_head: Whether tips are currently on the head.
    distance_from_bottom_mm: Requested clearance above the well bottom.

  Returns:
    The computed Z geometry.

  Raises:
    RuntimeError: For a disposable-tip head, if tips are not on the head or
      either tip length is unknown.
  """
  teachpoint_z = float(teachpoints.get_teachpoint(location, "z"))
  labware_height_mm = _liquid_labware_height_mm(labware)
  well_depth_mm = _liquid_well_depth_mm(labware)
  attached_length = None if attached_tip_length_mm is None else float(attached_tip_length_mm)
  teach_length = None if teach_tip_length_mm is None else float(teach_tip_length_mm)

  tip_delta_mm = 0.0
  if head_type_is_disposable(head_type):
    if not tips_on_head:
      raise RuntimeError(
        f"Liquid handling with disposable head {head_type} requires tips on the head"
      )
    if attached_length is None:
      raise RuntimeError(
        f"Liquid handling with disposable head {head_type} requires a known attached tip length"
      )
    if teach_length is None:
      raise RuntimeError(
        f"Liquid handling with disposable head {head_type} requires a taught tip length"
      )
    tip_delta_mm = teach_length - attached_length

  top_plane_tip_z = teachpoint_z - labware_height_mm
  well_bottom_tip_z = top_plane_tip_z + well_depth_mm
  target_tip_z = well_bottom_tip_z - float(distance_from_bottom_mm)
  top_plane_head_z = top_plane_tip_z + tip_delta_mm
  target_head_z = target_tip_z + tip_delta_mm
  return LiquidZGeometry(
    teachpoint_z=teachpoint_z,
    teach_tip_length_mm=teach_length,
    attached_tip_length_mm=attached_length,
    tip_delta_mm=tip_delta_mm,
    labware_height_mm=labware_height_mm,
    well_depth_mm=well_depth_mm,
    top_plane_tip_z=top_plane_tip_z,
    well_bottom_tip_z=well_bottom_tip_z,
    target_tip_z=target_tip_z,
    top_plane_head_z=top_plane_head_z,
    target_head_z=target_head_z,
    distance_from_bottom_mm=float(distance_from_bottom_mm),
  )


def _liquid_geometry_status_payload(geometry: LiquidZGeometry) -> Dict[str, Optional[float]]:
  return {
    "teachpoint_z": geometry.teachpoint_z,
    "teach_tip_length_mm": geometry.teach_tip_length_mm,
    "attached_tip_length_mm": geometry.attached_tip_length_mm,
    "tip_delta_mm": geometry.tip_delta_mm,
    "labware_height_mm": geometry.labware_height_mm,
    "well_depth_mm": geometry.well_depth_mm,
    "top_plane_tip_z": geometry.top_plane_tip_z,
    "well_bottom_tip_z": geometry.well_bottom_tip_z,
    "target_tip_z": geometry.target_tip_z,
    "top_plane_head_z": geometry.top_plane_head_z,
    "target_head_z": geometry.target_head_z,
    "distance_from_bottom_mm": geometry.distance_from_bottom_mm,
  }


def _move_liquid_z_profiled(
  ctrl: BravoController,
  *,
  top_plane_head_z: float,
  target_z: float,
  velocity: float,
  acceleration: float,
  phase: str,
) -> None:
  """Lower or raise Z through the labware top plane, in two phases.

  Entering (``phase="enter"``) always passes through ``top_plane_head_z``
  first at the controller's default speed, then makes the final approach to
  ``target_z`` at the requested (liquid-class) velocity/acceleration --
  the profiled speed is for the controlled final approach into liquid, not
  for clearing the labware on the way down. Exiting (``phase="exit"``) is
  the mirror: the profiled speed carries the head back up through the top
  plane, then a final unprofiled move completes the retract. Skipping
  either phase would apply the wrong speed to the wrong segment of travel.

  Args:
    ctrl: The controller to move.
    top_plane_head_z: The head's Z when level with the labware's top face.
    target_z: The final Z target.
    velocity: Z velocity for the profiled segment, in mm/s. ``0`` uses the
      controller's current setting.
    acceleration: Z acceleration for the profiled segment, in mm/s^2.
    phase: ``"enter"`` to lower toward the target, ``"exit"`` to raise away
      from it.
  """
  current_z = float(ctrl.get_position("z"))
  if phase == "enter":
    if current_z < top_plane_head_z - AXIS_EPSILON:
      ctrl.move([_axis_move(ctrl, "z", top_plane_head_z)], wait=True)
      current_z = top_plane_head_z
    if abs(target_z - current_z) > AXIS_EPSILON:
      ctrl.move(
        [_axis_move(ctrl, "z", target_z, velocity=velocity, acceleration=acceleration)],
        wait=True,
      )
    return
  if current_z > top_plane_head_z + AXIS_EPSILON:
    ctrl.move(
      [_axis_move(ctrl, "z", top_plane_head_z, velocity=velocity, acceleration=acceleration)],
      wait=True,
    )
    current_z = top_plane_head_z
  if abs(target_z - current_z) > AXIS_EPSILON:
    ctrl.move([_axis_move(ctrl, "z", target_z)], wait=True)


def _evaluate_volume_polynomial(coefficients: List[float], volume: float) -> float:
  total = 0.0
  for exponent, coefficient in enumerate(coefficients):
    total += float(coefficient) * (float(volume) ** exponent)
  return total


def _interpolate_control_points(points: List[Dict[str, float]], desired_volume: float) -> float:
  if not points:
    return float(desired_volume)
  desired = float(desired_volume)
  ordered = sorted(
    (
      {
        "desired_ul": float(item.get("desired_ul") or 0.0),
        "commanded_ul": float(item.get("commanded_ul") or 0.0),
      }
      for item in points
    ),
    key=lambda item: item["desired_ul"],
  )
  if desired <= ordered[0]["desired_ul"]:
    return ordered[0]["commanded_ul"]
  for left, right in zip(ordered, ordered[1:]):
    if desired <= right["desired_ul"]:
      span = right["desired_ul"] - left["desired_ul"]
      if span <= 1e-9:
        return right["commanded_ul"]
      fraction = (desired - left["desired_ul"]) / span
      return left["commanded_ul"] + fraction * (right["commanded_ul"] - left["commanded_ul"])
  return ordered[-1]["commanded_ul"]


def _simulation_motion_delay(controller: BravoController, segments: int = 1) -> float:
  """Return an artificial per-segment delay for a simulated swirl move.

  Args:
    controller: The controller executing the move.
    segments: The number of segments the caller is about to move through.

  Returns:
    A short delay in seconds for a :class:`~..controllers.simulation.SimulationController`,
    so a multi-segment swirl is observable rather than instantaneous; ``0``
    for every other controller, which already takes real time to move.
  """
  if not isinstance(controller, SimulationController):
    return 0.0
  return max(0.0, 0.02 * max(1, int(segments)))


def _w_axis_motion_value(controller: BravoController, value_ul: float) -> float:
  """Convert a W-axis quantity from microlitres to the controller's native unit.

  Args:
    controller: The controller the value is destined for.
    value_ul: The quantity, in microlitres (a volume, or a per-second
      volume rate for a velocity/acceleration).

  Returns:
    ``controller.ul_to_mm(value_ul)``, or ``value_ul`` unconverted if the
    controller cannot perform the conversion (e.g. no head type has been
    set yet).
  """
  try:
    return float(controller.ul_to_mm(float(value_ul)))
  except Exception:
    return float(value_ul)


def _axis_move(
  controller: BravoController,
  axis: Axis,
  position: float,
  *,
  velocity: float = 0.0,
  acceleration: float = 0.0,
  absolute: bool = True,
) -> AxisMoveInfo:
  """Build an :class:`AxisMoveInfo`, converting W-axis rates for the controller.

  ``position`` is never converted here, for any axis: a caller that means a
  W-axis position in microlitres converts it itself, at the point that
  value is known to be a volume (see callers of ``_w_axis_motion_value``
  below) -- this function cannot tell a volume from a millimetre park
  position, and guessing wrong silently sends the wrong distance to the
  plunger. ``velocity``/``acceleration`` are different: every caller here
  supplies them from a liquid class's ``w_velocity_ul_s``/
  ``w_acceleration_ul_s2`` entries, which are always volume rates
  regardless of controller generation, so converting them at this single
  boundary is unambiguous.

  Args:
    controller: The controller the move is destined for.
    axis: The axis to move.
    position: Target position, already in the controller's native unit.
    velocity: Move velocity. For the W axis, in microlitres/s.
    acceleration: Move acceleration. For the W axis, in microlitres/s^2.
    absolute: Whether ``position`` is an absolute target.

  Returns:
    The move, with a W-axis velocity/acceleration already converted to the
    controller's native unit.
  """
  move_velocity = float(velocity)
  move_acceleration = float(acceleration)
  if axis == "w":
    move_velocity = _w_axis_motion_value(controller, move_velocity)
    move_acceleration = _w_axis_motion_value(controller, move_acceleration)
  return AxisMoveInfo(
    axis=axis,
    position=float(position),
    velocity=move_velocity,
    acceleration=move_acceleration,
    absolute=absolute,
  )


def _rectangles_overlap(
  a: Tuple[float, float, float, float],
  b: Tuple[float, float, float, float],
  *,
  epsilon: float = _DECK_OVERLAP_EPSILON_MM,
) -> bool:
  ax_min, ax_max, ay_min, ay_max = a
  bx_min, bx_max, by_min, by_max = b
  return not (
    ax_max <= bx_min + epsilon
    or bx_max <= ax_min + epsilon
    or ay_max <= by_min + epsilon
    or by_max <= ay_min + epsilon
  )


def _a1_reference_bounds_mm(
  origin_x: float,
  origin_y: float,
  *,
  front_mm: float,
  back_mm: float,
  left_mm: float,
  right_mm: float,
) -> Tuple[float, float, float, float]:
  return (
    origin_x - front_mm,
    origin_x + back_mm,
    origin_y - left_mm,
    origin_y + right_mm,
  )


def _union_bounds_mm(
  a: Tuple[float, float, float, float],
  b: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
  return (
    min(a[0], b[0]),
    max(a[1], b[1]),
    min(a[2], b[2]),
    max(a[3], b[3]),
  )


def _occupied_labware_bounds_mm(
  teachpoints: Teachpoints,
  location: int,
  labware: Labware,
) -> Optional[Tuple[float, float, float, float]]:
  try:
    teach_x = teachpoints.get_teachpoint(location, "x")
    teach_y = teachpoints.get_teachpoint(location, "y")
  except KeyError:
    return None

  # Matches the subset-collision model: the occupied XY area is the
  # location platepad/accessory footprint relative to the taught A1
  # reference, not just the placed labware's own body dimensions.
  location_bounds = _a1_reference_bounds_mm(
    teach_x,
    teach_y,
    front_mm=_PLATEPAD_A1_TO_FRONT_MM,
    back_mm=_PLATEPAD_A1_TO_BACK_MM,
    left_mm=_PLATEPAD_A1_TO_LEFT_MM,
    right_mm=_PLATEPAD_A1_TO_RIGHT_MM,
  )

  metadata = labware.metadata or {}
  length_mm = float(metadata.get("length_mm") or metadata.get("length") or labware.length or 0.0)
  width_mm = float(metadata.get("width_mm") or metadata.get("width") or labware.width or 0.0)
  offset_x_mm = float(metadata.get("offset_x_mm") or 0.0)
  offset_y_mm = float(metadata.get("offset_y_mm") or 0.0)
  if length_mm > 0.0 and width_mm > 0.0:
    labware_bounds = (
      teach_x - offset_x_mm,
      teach_x + max(0.0, length_mm - offset_x_mm),
      teach_y - offset_y_mm,
      teach_y + max(0.0, width_mm - offset_y_mm),
    )
    return _union_bounds_mm(location_bounds, labware_bounds)
  return location_bounds


def _full_head_footprint_bounds_mm(
  head_type: Optional[HeadType],
  origin_x: float,
  origin_y: float,
  *,
  gripper_present: bool = True,
) -> Tuple[float, float, float, float]:
  geometry = head_geometry_for_type(head_type or "96_d_70")
  nozzle_front_mm = geometry.pitch_x_mm / 2.0
  nozzle_back_mm = (geometry.columns - 0.5) * geometry.pitch_x_mm
  nozzle_left_mm = geometry.pitch_y_mm / 2.0
  nozzle_right_mm = (geometry.rows - 0.5) * geometry.pitch_y_mm
  return _a1_reference_bounds_mm(
    origin_x,
    origin_y,
    front_mm=nozzle_front_mm + _HEAD_BODY_EXTRA_FRONT_MM,
    back_mm=nozzle_back_mm
    + _HEAD_BODY_EXTRA_BACK_MM
    + (_HEAD_GRIPPER_BACK_OVERHANG_MM if gripper_present else 0.0),
    left_mm=nozzle_left_mm + _HEAD_BODY_EXTRA_LEFT_MM,
    right_mm=nozzle_right_mm + _HEAD_BODY_EXTRA_RIGHT_MM,
  )


def _collision_footprint_bounds_mm(
  head_type: Optional[HeadType],
  head_mode: Optional[HeadMode],
  origin_x: float,
  origin_y: float,
  *,
  gripper_present: bool = True,
) -> Optional[Tuple[float, float, float, float]]:
  if head_mode is None:
    return _full_head_footprint_bounds_mm(
      head_type,
      origin_x,
      origin_y,
      gripper_present=gripper_present,
    )
  if str(head_mode.subset_type or "all_barrels") == "all_barrels":
    return None
  return _full_head_footprint_bounds_mm(
    head_type,
    origin_x,
    origin_y,
    gripper_present=gripper_present,
  )


def _assert_neighbor_clearance(
  *,
  command_name: str,
  teachpoints: Teachpoints,
  deck: Optional[DeckState],
  head_type: Optional[HeadType],
  head_mode: Optional[HeadMode],
  target_location: int,
  target_x: float,
  target_y: float,
  allowed_top_plane_mm: float,
  gripper_present: bool = True,
) -> List[Dict[str, Union[float, int, str]]]:
  """Reject a move whose head footprint overlaps a neighbor taller than allowed.

  A full-barrel-array move never triggers this check (its footprint is
  ``None``, meaning "already accounted for by teachpoint spacing"); it only
  applies once the head is running a row/column/rectangle/single-barrel
  subset, whose narrower footprint can legally sit closer to a tall
  neighbor than a full head could.

  Args:
    command_name: The operation name, for the raised message.
    teachpoints: The deck teachpoints, for each candidate neighbor's XY.
    deck: The deck state to check neighbors against. ``None`` skips the
      check entirely (no deck model available).
    head_type: The installed head type.
    head_mode: The active head mode/subset.
    target_location: The location being moved to (excluded from the check).
    target_x: The move's target X, in millimetres.
    target_y: The move's target Y, in millimetres.
    allowed_top_plane_mm: The highest neighboring top-of-stack the move
      tolerates.
    gripper_present: Whether a gripper is installed (widens the footprint's
      back overhang).

  Returns:
    Every neighbor whose occupied footprint overlaps the move's footprint,
    whether or not it was tall enough to block the move.

  Raises:
    RuntimeError: If any overlapping neighbor's height meets or exceeds
      ``allowed_top_plane_mm``.
  """
  if deck is None:
    return []

  footprint = _collision_footprint_bounds_mm(
    head_type,
    head_mode,
    target_x,
    target_y,
    gripper_present=gripper_present,
  )
  if footprint is None:
    return []
  overlaps: List[Dict[str, Union[float, int, str]]] = []
  blocking: List[Dict[str, Union[float, int, str]]] = []
  for location in range(1, 10):
    if location == target_location:
      continue
    top_labware = deck.get_stack(location).top
    if top_labware is None:
      continue
    slot_bounds = _occupied_labware_bounds_mm(teachpoints, location, top_labware)
    if slot_bounds is None or not _rectangles_overlap(footprint, slot_bounds):
      continue
    height_mm = float(deck.get_height(location))
    overlap: Dict[str, Union[float, int, str]] = {
      "location": location,
      "height_mm": height_mm,
    }
    overlaps.append(overlap)
    if height_mm >= allowed_top_plane_mm - _DECK_OVERLAP_EPSILON_MM:
      blocking.append(overlap)

  if blocking:
    mode_text = "unknown"
    if head_mode is not None:
      mode_text = (
        f"{head_mode.subset_type} {head_mode.subset_config} "
        f"({head_mode.row_count}x{head_mode.column_count})"
      )
    details = ", ".join(
      f"location {int(item['location'])} top {float(item['height_mm']):.1f} mm" for item in blocking
    )
    raise RuntimeError(
      f"{command_name} at location {target_location} is blocked: head footprint overlaps "
      f"{details}, which meets or exceeds the allowed top plane {allowed_top_plane_mm:.1f} mm "
      f"for head mode {mode_text}."
    )
  return overlaps


def _tip_offsets_or_default(
  config: BravoMachineConfig, tip_offsets: Optional[ResolvedTipOffsets]
) -> ResolvedTipOffsets:
  """Return the supplied resolved tip offsets, or build them from the safety config.

  Args:
    config: The machine configuration to build defaults from.
    tip_offsets: An already-resolved (head, tip box) override, or ``None``
      to build one from the configuration's global ``safety.*`` values.

  Returns:
    ``tip_offsets`` unchanged if supplied, otherwise a
    :class:`~..tip_offsets.ResolvedTipOffsets` built from
    ``config.safety.tips_off_z_offset``/``tips_off_w_position`` and the
    default press tolerance, with ``matched=False``.
  """
  if tip_offsets is not None:
    return tip_offsets
  safety = config.safety
  return ResolvedTipOffsets(
    tips_off_z_offset=float(safety.tips_off_z_offset),
    tips_off_w_position=float(safety.tips_off_w_position),
    tips_on_jog_tolerance=float(TIPBOX_JOG_TOLERANCE),
    tips_on_z_offset=0.0,
    matched=False,
    source="profile defaults",
  )


def _normalize_tip_current_table(raw: Optional[Dict[str, Any]]) -> List[Tuple[int, float]]:
  """Parse a ``{"<n> tips": amps}``-shaped override into sorted ``(count, amps)`` pairs.

  Args:
    raw: The override mapping, or ``None``.

  Returns:
    ``(tip_count, current_amps)`` pairs sorted by tip count. A key with no
    digits, or a value that cannot convert to a number, is skipped.
  """
  table: List[Tuple[int, float]] = []
  for key, value in (raw or {}).items():
    digits = "".join(ch for ch in str(key) if ch.isdigit())
    if not digits:
      continue
    try:
      table.append((int(digits), float(value)))
    except (TypeError, ValueError):
      continue
  table.sort(key=lambda item: item[0])
  return table


def _tipbox_rows_cols(metadata: Dict[str, Any]) -> Tuple[int, int]:
  """Return a tip box's (rows, cols) grid from its labware metadata.

  Args:
    metadata: The tip box labware's metadata dict.

  Returns:
    The explicit ``rows``/``cols`` metadata fields if both are positive;
    otherwise the standard grid for a 96/384/1536-well box inferred from
    ``wells``; otherwise ``(0, 0)``.
  """
  rows = int(metadata.get("rows") or 0)
  cols = int(metadata.get("cols") or 0)
  if rows > 0 and cols > 0:
    return rows, cols
  wells = int(metadata.get("wells") or 0)
  if wells == 96:
    return 8, 12
  if wells == 384:
    return 16, 24
  if wells == 1536:
    return 32, 48
  return rows, cols


class InitializeTask(StateMachineTask):
  """Cold-start initialization sequence for a Bravo.

  Pings the device, detects the gripper and head, clears faults, and homes
  every axis that is not already homed on entry, in the order that keeps
  the head and gripper clear of the deck (vertical clearance before any
  lateral motion).
  """

  def __init__(
    self, controller: BravoController, config: Optional[BravoMachineConfig] = None
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      config: The machine configuration to initialize against. A task
        constructed without one (e.g. a bench script or test) gets a
        default configuration instead.
    """
    super().__init__("Initialize")
    self._ctrl = controller
    self._config = config if config is not None else BravoMachineConfig()
    self._operator_prompt: Optional[dict] = None
    self._gripper_present = "g" in self._config.axes and "zg" in self._config.axes
    self._force_gripper_present = False
    self._w_prompt_acknowledged = False
    self._skip_w_home = False
    self._plate_in_gripper_ignored = False
    self._homed_on_entry: Dict[Axis, bool] = {axis: False for axis in ALL_AXES}

  def _check_head_on_init(self) -> bool:
    return bool(self._config.head.check_on_init)

  def _head_type(self) -> HeadType:
    return self._config.head.head_type

  def _should_home_w_axis(self) -> bool:
    if bool(self._config.safety.ignore_w_axis):
      return False
    return not head_type_is_pintool(self._head_type())

  def _should_prompt_home_w_axis(self) -> bool:
    return self._should_home_w_axis() and bool(self._config.safety.prompt_home_w)

  def _widest_gripper_open_position(self) -> float:
    g_cfg = self._config.axes.get("g")
    if g_cfg is None:
      return OPEN_GRIPPER_POSITION
    return min(float(g_cfg.range.min_pos), OPEN_GRIPPER_POSITION)

  def _gripper_expected(self) -> bool:
    return "g" in self._config.axes and "zg" in self._config.axes

  def _axis_needs_home(self, axis: Axis) -> bool:
    return not self._homed_on_entry.get(axis, False)

  def _gripper_axes_need_home(self) -> bool:
    if not self._gripper_present:
      return False
    return self._axis_needs_home("g") or self._axis_needs_home("zg")

  def _infer_gripper_present_from_homed_axes(self) -> bool:
    axes: Tuple[Axis, ...] = ("g", "zg")
    homed_results: Dict[Axis, bool] = {}
    for axis in axes:
      try:
        homed = bool(self._ctrl.is_axis_homed(axis))
      except Exception as exc:
        logger.debug(
          "Could not read %s homed state while inferring gripper presence: %s",
          axis_display_name(axis),
          exc,
        )
        continue
      self._homed_on_entry[axis] = homed
      homed_results[axis] = homed
    inferred_homed = all(homed_results.get(axis, False) for axis in axes)
    if inferred_homed:
      logger.warning(
        "Gripper detect returned not detected, but both G and Zg already report homed; "
        "treating gripper as present."
      )
    return inferred_homed

  def _any_axes_need_home(self) -> bool:
    axes_to_check: List[Axis] = ["x", "y", "z"]
    if self._should_home_w_axis():
      axes_to_check.append("w")
    if self._gripper_present:
      axes_to_check.extend(["g", "zg"])
    return any(self._axis_needs_home(axis) for axis in axes_to_check)

  def status_payload(self) -> dict:
    """Return the task's status, including an initialize-specific tag.

    Returns:
      A dict with ``"task": "initialize"`` and an ``"operator_prompt"``
      entry that is ``None`` unless the task is currently failed.
    """
    return {
      "task": "initialize",
      "operator_prompt": None if self.status != TaskStatus.FAILED else self._operator_prompt,
    }

  def on_error_action(self, action: ErrorAction) -> None:
    """Apply the operator's choice for the step that just failed.

    Args:
      action: The action the operator chose.
    """
    if self.error is not None and self.error.step_name == "prompt_home_w":
      if action == ErrorAction.RETRY:
        self._w_prompt_acknowledged = True
        self._skip_w_home = False
      elif action == ErrorAction.IGNORE:
        self._skip_w_home = True
    if self.error is not None and self.error.step_name == "handle_plate_in_gripper":
      self._plate_in_gripper_ignored = action == ErrorAction.IGNORE
    if self.error is not None and self.error.step_name == "detect_gripper":
      if action == ErrorAction.IGNORE:
        self._force_gripper_present = True
        self._gripper_present = True
    self._operator_prompt = None

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("ping_device", self._ping_device),
      ("set_light_initializing", self._set_light_initializing),
      ("query_firmware", self._query_firmware),
      ("detect_gripper", self._detect_gripper),
      ("get_unique_value", self._get_unique_value),
      ("detect_head", self._detect_head),
      ("read_home_registers", self._read_home_registers),
      ("check_interlock", self._check_interlock),
      ("clear_motor_power_fault", self._clear_motor_power_fault),
      ("reset_faults", self._reset_faults),
      ("move_z_to_safe_position", self._move_z_to_safe_position),
      ("home_z", self._home_z),
      ("handle_plate_in_gripper", self._handle_plate_in_gripper),
      ("home_g", self._home_g),
      ("home_zg", self._home_zg),
      ("move_zg_to_nesting", self._move_zg_to_nesting),
      ("prompt_home_w", self._prompt_home_w),
      ("home_w", self._home_w),
      ("home_xy", self._home_xy),
      ("set_light_idle", self._set_light_idle),
      ("finish", self._finish),
    ]

  async def _ping_device(self) -> None:
    logger.info("Pinging device...")
    if not self._ctrl.ping():
      raise RuntimeError("Device did not respond to ping")

  async def _query_firmware(self) -> None:
    fw = self._ctrl.get_firmware_version()
    logger.info("Firmware: master=%s sub1=%s sub2=%s", fw.master, fw.sub1, fw.sub2)

  async def _set_light_initializing(self) -> None:
    self._ctrl.clear_lights()
    self._ctrl.set_light(
      LightCommandData(
        light=LightColor.YELLOW,
        period_ms=1000,
        duty_cycle=0.8,
      )
    )

  async def _check_interlock(self) -> None:
    """Check the robot-disable interlock before proceeding.

    The firmware checks the E-stop bit before any motion.
    """
    state = self._ctrl.query_state()
    if state & DeviceStateFlag.ROBOT_DISABLE:
      raise RuntimeError(
        "Robot safety interlock is active (E-stop). Release the interlock and retry."
      )
    logger.info("Safety interlock OK")

  async def _clear_motor_power_fault(self) -> None:
    """Clear any existing motor power fault.

    ``CLEAR_MOTOR_POWER_FAULT`` is sent during initialization.
    """
    try:
      self._ctrl.send_command(CommandID.CLEAR_MOTOR_POWER_FAULT)
      logger.info("Motor power fault cleared")
    except Exception as exc:
      logger.warning("Could not clear motor power fault: %s", exc)

  async def _detect_gripper(self) -> None:
    if self._force_gripper_present:
      self._gripper_present = True
      logger.warning("Proceeding with gripper initialization after operator override")
      return

    expected = self._gripper_expected()
    max_attempts = 3 if expected else 1
    last_error: Optional[Exception] = None
    state = GripperDetectionState.NOT_YET_DETECTED

    for attempt in range(1, max_attempts + 1):
      try:
        state = self._ctrl.detect_gripper()
        last_error = None
      except Exception as exc:
        last_error = exc
        logger.warning(
          "Gripper-detect attempt %d/%d failed during initialize: %s",
          attempt,
          max_attempts,
          exc,
        )
        if attempt < max_attempts:
          await asyncio.sleep(0.5)
          continue
        break

      if state == GripperDetectionState.DETECTED:
        break
      if state == GripperDetectionState.NOT_DETECTED and expected and attempt < max_attempts:
        logger.warning(
          "Gripper-detect attempt %d/%d returned not detected during initialize; retrying.",
          attempt,
          max_attempts,
        )
        await asyncio.sleep(0.5)
        continue
      break

    self._gripper_present = state != GripperDetectionState.NOT_DETECTED
    if state == GripperDetectionState.DETECTED:
      logger.info("Gripper detected")
    elif state == GripperDetectionState.NOT_DETECTED:
      if not expected:
        logger.info("No gripper detected")
        return
      if self._infer_gripper_present_from_homed_axes():
        self._gripper_present = True
        return
      message = (
        "The gripper was not detected on the robot during initialization.\n\n"
        "Retry checks for the gripper again.\n"
        "Ignore continues assuming the gripper is installed and homes G and Zg anyway.\n"
        "Abort cancels initialization."
      )
      self._operator_prompt = {
        "kind": "initialize_detect_gripper",
        "title": "Confirm Gripper Detection",
        "message": message,
        "choices": ["retry", "ignore", "abort"],
      }
      raise RuntimeError(message)
    else:
      if last_error is not None and expected:
        message = (
          "The gripper could not be detected reliably during initialization.\n\n"
          "Retry checks for the gripper again.\n"
          "Ignore continues assuming the gripper is installed and homes G and Zg anyway.\n"
          "Abort cancels initialization."
        )
        self._operator_prompt = {
          "kind": "initialize_detect_gripper",
          "title": "Confirm Gripper Detection",
          "message": message,
          "choices": ["retry", "ignore", "abort"],
        }
        raise RuntimeError(message)
      logger.warning("Gripper detection inconclusive")

  async def _get_unique_value(self) -> None:
    logger.info("Skipping unique-value check; on Darwin it is a connectivity validation only")

  async def _detect_head(self) -> None:
    if not self._check_head_on_init():
      logger.info("Skipping head detection on init per profile setting")
      return
    temporarily_reenable_w = False
    is_darwin = isinstance(self._ctrl, DarwinController)
    should_manage_w = is_darwin and not head_type_is_pintool(self._head_type())
    try:
      if should_manage_w:
        assert isinstance(self._ctrl, DarwinController)
        try:
          if self._ctrl.is_motor_enabled("w"):
            self._ctrl.disable_motor("w")
            temporarily_reenable_w = True
            await asyncio.sleep(1.0)
        except Exception as exc:
          logger.debug("Skipping Darwin W-axis disable during head detection: %s", exc)

      if self._ctrl.detect_smart_head():
        head_code = self._ctrl.read_smart_head_type()
        logger.info("Smart head detected, type code=%d", head_code)
      else:
        adc_value = self._ctrl.read_head_adc()
        logger.info("Resistor-based head detection, ADC=%d", adc_value)
    finally:
      if temporarily_reenable_w:
        try:
          self._ctrl.enable_motor("w")
        except Exception as exc:
          logger.warning("Could not re-enable Darwin W axis after head detection: %s", exc)

  async def _read_home_registers(self) -> None:
    logger.info("Reading axis homed state before initialize...")

    # The firmware reads config registers (0x03 x5, 0x00) and all
    # home_complete registers from the firmware before any homing. These
    # reads may be required to put the firmware into a "ready for homing"
    # state.
    if isinstance(self._ctrl, Agile7612Controller):
      for _ in range(5):
        try:
          self._ctrl._agile_7612_ext_read(0x03, "x")
        except Exception:
          pass
      try:
        self._ctrl._agile_7612_ext_read(0x00, "x")
      except Exception:
        pass

      # Read home_complete registers from firmware (all 6)
      for axis in ("x", "y", "z", "w", "g", "zg"):
        try:
          reg = _home_reg_register(axis)
          self._ctrl._agile_7612_agile_read(reg, axis)
        except Exception:
          pass

    axes_to_check: List[Axis] = ["x", "y", "z", "w"]
    if self._gripper_present:
      axes_to_check.extend(["g", "zg"])
    for axis in axes_to_check:
      try:
        self._homed_on_entry[axis] = bool(self._ctrl.is_axis_homed(axis))
      except Exception as exc:
        logger.debug(
          "Could not read homed state for %s during initialize: %s", axis_display_name(axis), exc
        )
        self._homed_on_entry[axis] = False

  async def _reset_faults(self) -> None:
    """Reset faults on all axes before homing.

    The firmware resets X, Y, Z, G, and Zg during init. G/Zg faults must be
    cleared before gripper-axis motion or homing will be rejected.
    """
    all_axes: List[Axis] = ["x", "y", "z"]
    if self._should_home_w_axis():
      all_axes.append("w")
    if self._gripper_present:
      all_axes.extend(["g", "zg"])
    logger.info("Resetting faults on axes: %s", [axis_display_name(a) for a in all_axes])
    self._ctrl.reset_faults(all_axes)

  async def _move_z_to_safe_position(self) -> None:
    if not self._any_axes_need_home():
      logger.info("All initialize axes were already homed on entry; skipping safe-Z retract")
      return
    if not self._homed_on_entry.get("z", False):
      return
    safe_z = float(self._config.safety.z_safe_position)
    current_z = float(self._ctrl.get_position("z"))
    if current_z <= safe_z:
      return
    logger.info("Z already homed; moving to safe position %.3f mm before initialize...", safe_z)
    self._ctrl.move([AxisMoveInfo(axis="z", position=safe_z)], wait=True)

  async def _home_z(self) -> None:
    if self._homed_on_entry.get("z", False):
      logger.info("Skipping Z homing because Z was already homed on entry")
      return
    logger.info("Homing Z axis...")
    self._ctrl.home_axes(["z"])

  async def _handle_plate_in_gripper(self) -> None:
    if not self._gripper_axes_need_home():
      return
    has_plate = bool(self._ctrl.is_plate_in_gripper())
    if not has_plate:
      return
    warning = (
      "There appears to be a plate present in, or in front of, the gripper plate sensor.\n\n"
      "Retry checks the sensor again.\n"
      "Ignore continues to gripper-axis homing.\n"
      "Abort cancels initialization."
    )
    if isinstance(self._ctrl, DarwinController):
      try:
        if self._axis_needs_home("g"):
          warning += (
            "\n\nIf a plate is currently held by the gripper, remove it before G homing. "
            "On DARWIN, failed G commutation can clamp harder on the plate."
          )
        else:
          warning += "\n\nAny plate currently held by the gripper will be dropped."
      except Exception:
        warning += "\n\nIf a plate is currently held by the gripper, remove it before continuing."
    self._operator_prompt = {
      "kind": "initialize_plate_in_gripper",
      "title": "Plate Detected In Gripper",
      "message": warning,
      "choices": ["retry", "ignore", "abort"],
    }
    raise RuntimeError(warning)

  async def _home_g(self) -> None:
    if not self._gripper_present:
      logger.info("Skipping G-axis homing because no gripper is detected")
      return
    if not self._axis_needs_home("g"):
      logger.info("Skipping G-axis homing because G was already homed on entry")
      return
    if isinstance(self._ctrl, DarwinController):
      widest_open = self._widest_gripper_open_position()
      logger.info(
        "Moving G to the furthest-open initialize position (%.3f mm) before G home...",
        widest_open,
      )
      try:
        self._ctrl.move([AxisMoveInfo(axis="g", position=widest_open)], wait=True)
      except Exception as exc:
        logger.warning(
          "Could not move G to the furthest-open initialize position before homing: %s", exc
        )
        try:
          logger.info("Retrying G pre-home move at the standard open position (0.000 mm)...")
          self._ctrl.move([AxisMoveInfo(axis="g", position=OPEN_GRIPPER_POSITION)], wait=True)
        except Exception as fallback_exc:
          if self._plate_in_gripper_ignored:
            logger.warning(
              "Could not move G open before G home after operator ignored the "
              "plate-sensor warning; continuing to DARWIN G homing anyway: %s",
              fallback_exc,
            )
          else:
            raise RuntimeError(
              "Could not open gripper wide enough to finish initialization"
            ) from fallback_exc
    else:
      logger.info("Skipping pre-home G open move (controller handles pre-move internally)")
    logger.info("Homing G axis...")
    self._ctrl.home_axes(["g"])
    try:
      self._ctrl.disable_motor("g")
    except Exception as exc:
      logger.debug("Ignoring G-axis disable failure after homing: %s", exc)

  async def _home_zg(self) -> None:
    if not self._gripper_present:
      logger.info("Skipping Zg-axis homing because no gripper is detected")
      return
    if self._homed_on_entry.get("zg", False):
      logger.info("Skipping Zg-axis homing because Zg was already homed on entry")
      return
    logger.info("Homing Zg axis...")
    self._ctrl.home_axes(["zg"])

  async def _move_zg_to_nesting(self) -> None:
    if not self._gripper_present:
      return
    if not self._gripper_axes_need_home():
      logger.info("Skipping Zg nesting move because gripper axes were already homed on entry")
      return
    logger.info("Moving Zg to nesting position (%.3f mm)...", _GRIPPER_RECESS_DEPTH)
    self._ctrl.move([AxisMoveInfo(axis="zg", position=_GRIPPER_RECESS_DEPTH)], wait=True)

  async def _prompt_home_w(self) -> None:
    if (
      not self._should_prompt_home_w_axis()
      or self._w_prompt_acknowledged
      or not self._axis_needs_home("w")
    ):
      return
    message = (
      "Please verify that it is safe to home the W-axis (the aspirate/dispense axis).\n\n"
      "If there is fluid in the tips, you may want to home W manually over a waste position.\n\n"
      "Retry continues with W homing.\n"
      "Ignore leaves W unhomed.\n"
      "Abort cancels initialization."
    )
    self._operator_prompt = {
      "kind": "initialize_home_w_axis",
      "title": "Confirm W-Axis Home",
      "message": message,
      "choices": ["retry", "ignore", "abort"],
    }
    raise RuntimeError(message)

  async def _home_xy(self) -> None:
    xy_axes: Tuple[Axis, Axis] = ("x", "y")
    axes_to_home: List[Axis] = [axis for axis in xy_axes if self._axis_needs_home(axis)]
    if not axes_to_home:
      logger.info("Skipping X/Y homing because both axes were already homed on entry")
      return
    logger.info("Homing %s...", " and ".join(axis_label(axis) for axis in axes_to_home))
    self._ctrl.home_axes(axes_to_home)

  async def _home_w(self) -> None:
    if self._skip_w_home:
      logger.info("Skipping W-axis homing per operator choice")
      return
    if not self._should_home_w_axis():
      logger.info("Skipping W-axis homing per profile setting")
      return
    if self._homed_on_entry.get("w", False):
      logger.info("Skipping W-axis homing because W was already homed on entry")
      return
    logger.info("Homing W axis...")
    self._ctrl.home_axes(["w"])
    current_w = float(self._ctrl.get_position("w"))
    if abs(current_w) > AXIS_EPSILON:
      logger.info(
        "Parking W at 0.0 uL after homing (current %.3f uL)...",
        current_w,
      )
      self._ctrl.move([AxisMoveInfo(axis="w", position=0.0)], wait=True)

  async def _set_light_idle(self) -> None:
    self._ctrl.set_light(
      LightCommandData(
        light=LightColor.GREEN,
        period_ms=0,
        duty_cycle=1.0,
      )
    )

  async def _finish(self) -> None:
    logger.info("Initialization complete")


class HomeTask(StateMachineTask):
  """Home one or more axes with a safe Z retract first."""

  def __init__(
    self,
    controller: BravoController,
    config: BravoMachineConfig,
    axes: "list[Axis]",
    safe_z_position: float = Z_SAFE,
    force: bool = False,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      config: The machine configuration to home against.
      axes: The axes to home.
      safe_z_position: The Z position to retract to before homing.
      force: Re-home an axis that already reports itself homed.
    """
    super().__init__("Home")
    self._force = force
    self._ctrl = controller
    self._config = config
    self._axes = axes
    self._safe_z_position = safe_z_position
    self._use_gripper_safe_state = "g" in axes or "zg" in axes

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("safe_z_retract", self._safe_z_retract),
      ("prepare_gripper_safe_state", self._prepare_gripper_safe_state),
      ("home_requested_axes", self._home_requested_axes),
      ("park_homed_axes", self._park_homed_axes),
      ("finalize_gripper_safe_state", self._finalize_gripper_safe_state),
      ("verify_homed", self._verify_homed),
    ]

  async def _safe_z_retract(self) -> None:
    if not self._ctrl.is_axis_homed("z"):
      logger.info("Z not homed -- skipping safe Z retract")
      return
    logger.info("Retracting Z to safe position (%.1f mm)...", self._safe_z_position)
    self._ctrl.move(
      [AxisMoveInfo(axis="z", position=self._safe_z_position)],
      wait=True,
    )

  async def _prepare_gripper_safe_state(self) -> None:
    if not self._use_gripper_safe_state:
      logger.info("Skipping pre-home gripper safe state; gripper axes are not part of this home")
      return
    if not self._ctrl.is_axis_homed("g") or not self._ctrl.is_axis_homed("zg"):
      logger.info("G/Zg not homed -- skipping pre-home gripper safe state")
      return
    task = DockGripperTask(
      self._ctrl, self._config, force_if_plate_detected=True, task_name="HomeDock"
    )
    await task._check_plate_sensor()
    await task._open_gripper()
    await task._move_zg_to_nesting()

  async def _home_requested_axes(self) -> None:
    # Vertical clearance before lateral motion. The pre-home Z retract and
    # gripper dock above are skipped when those axes are not yet homed --
    # i.e. on a cold start, precisely when the head could be anywhere --
    # so the ordering here is the actual guarantee, not a nicety.
    ordered = safe_home_order(self._axes)
    names = ", ".join(axis_label(a) for a in ordered)
    logger.info("Homing axes: %s", names)
    self._ctrl.home_axes(ordered, force=self._force)

  async def _park_homed_axes(self) -> None:
    if not self._axes:
      return
    park_moves: "list[AxisMoveInfo]" = []
    for axis in self._axes:
      target = float(self._ctrl.get_park_position(axis))
      park_moves.append(AxisMoveInfo(axis=axis, position=target))
    logger.info(
      "Moving homed axes to park positions: %s",
      ", ".join(f"{axis_display_name(move.axis)}={move.position:.3f}" for move in park_moves),
    )
    self._ctrl.move(park_moves, wait=True)

  async def _finalize_gripper_safe_state(self) -> None:
    if not self._use_gripper_safe_state:
      return
    task = DockGripperTask(
      self._ctrl, self._config, force_if_plate_detected=True, task_name="HomeDock"
    )
    await task._check_plate_sensor()
    await task._open_gripper()
    await task._move_zg_to_nesting()

  async def _verify_homed(self) -> None:
    for axis in self._axes:
      if not self._ctrl.is_axis_homed(axis):
        raise RuntimeError(f"{axis_label(axis)} failed to home")
    logger.info("All requested axes verified homed")


class DockGripperTask(StateMachineTask):
  """Open the gripper and move Zg to the recessed nesting position."""

  def __init__(
    self,
    controller: BravoController,
    config: BravoMachineConfig,
    *,
    force_if_plate_detected: bool = True,
    task_name: str = "DockGripper",
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      config: The machine configuration to dock against.
      force_if_plate_detected: Dock even if the plate sensor reports a
        plate present, rather than raising.
      task_name: The task's display name.
    """
    super().__init__(task_name)
    self._ctrl = controller
    self._config = config
    self._force_if_plate_detected = force_if_plate_detected
    self._plate_detected = False
    self._g_target = OPEN_GRIPPER_POSITION
    self._zg_target = self._resolve_zg_target()

  def _resolve_zg_target(self) -> float:
    # Docking/nesting uses the absolute recessed Zg position even when the
    # configured axis range does not include that negative value. Do not
    # clamp to the configured min/max here.
    return _GRIPPER_RECESS_DEPTH

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("check_plate_sensor", self._check_plate_sensor),
      ("open_gripper", self._open_gripper),
      ("move_zg_to_nesting", self._move_zg_to_nesting),
      ("verify_gripper_docked", self._verify_gripper_docked),
    ]

  async def _check_plate_sensor(self) -> None:
    try:
      self._plate_detected = bool(self._ctrl.is_plate_in_gripper())
    except Exception as exc:
      logger.warning("Failed to read gripper plate sensor during dock: %s", exc)
      self._plate_detected = False
      return
    if self._plate_detected and not self._force_if_plate_detected:
      raise RuntimeError("Cannot dock gripper while a plate is detected in the gripper")
    if self._plate_detected:
      logger.warning("Dock Gripper: plate sensor active, forcing dock per configuration")

  async def _open_gripper(self) -> None:
    logger.info("Dock Gripper: opening gripper to G=%.3f...", self._g_target)
    self._ctrl.open_gripper()

  async def _move_zg_to_nesting(self) -> None:
    zg_velocity = 0.0
    zg_acceleration = 0.0
    zg_cfg = self._config.axes.get("zg")
    if zg_cfg is not None and "safe" in zg_cfg.speeds:
      speed = zg_cfg.speeds["safe"]
      zg_velocity = float(speed.velocity)
      zg_acceleration = float(speed.acceleration)
    logger.info("Dock Gripper: moving Zg to %.3f...", self._zg_target)
    self._ctrl.move(
      [
        AxisMoveInfo(
          axis="zg", position=self._zg_target, velocity=zg_velocity, acceleration=zg_acceleration
        )
      ],
      wait=True,
    )

  async def _verify_gripper_docked(self) -> None:
    g_actual = float(self._ctrl.get_position("g"))
    zg_actual = float(self._ctrl.get_position("zg"))
    if abs(g_actual - self._g_target) > _GRIPPER_OPEN_TOLERANCE_MM:
      raise RuntimeError(
        "Gripper failed to open to safe position. "
        f"Target was {self._g_target:.3f} and actual position was {g_actual:.3f}."
      )
    if abs(zg_actual - self._zg_target) > 0.5:
      raise RuntimeError(
        "Gripper failed to reach nesting position. "
        f"Target was {self._zg_target:.3f} and actual position was {zg_actual:.3f}."
      )
    logger.info("Dock Gripper complete: G=%.3f Zg=%.3f", g_actual, zg_actual)


class MoveToLocationTask(StateMachineTask):
  """Move the head to a deck location using teachpoints."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    location: int,
    safe_z_position: float = Z_SAFE,
    approach_height: float = 0.0,
    only_move_z: bool = False,
    speed_profiles: Optional[Dict[Axis, Tuple[float, float]]] = None,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      location: The deck location to move to.
      safe_z_position: The Z position to retract to before any lateral move.
      approach_height: Millimetres to stop above the teachpoint's Z before
        the final lowering move. ``0`` lowers straight to the teachpoint.
      only_move_z: Move only Z (to ``safe_z_position``), skipping the
        lateral move and the teachpoint-Z lowering.
      speed_profiles: Per-axis ``(velocity, acceleration)`` overrides. An
        axis not present uses the controller's current speed setting.
    """
    super().__init__(f"MoveToLocation_{location}")
    self._ctrl = controller
    self._tp = teachpoints
    self._location = location
    self._safe_z_position = safe_z_position
    self._approach_height = approach_height
    self._only_move_z = only_move_z
    self._speed_profiles = speed_profiles or {}

  def _move_info(self, axis: Axis, position: float) -> AxisMoveInfo:
    velocity, acceleration = self._speed_profiles.get(axis, (0.0, 0.0))
    return AxisMoveInfo(
      axis=axis,
      position=position,
      velocity=velocity,
      acceleration=acceleration,
    )

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    steps: "list[tuple[str, Callable[[], Awaitable[None]]]]" = [
      ("safe_z_retract", self._safe_z_retract),
    ]
    if not self._only_move_z:
      steps.append(("move_xy_to_teachpoint", self._move_xy))
    if not self._only_move_z or self._safe_z_position != self._target_z():
      steps.append(("lower_z_to_teachpoint", self._lower_z))
    return steps

  async def _safe_z_retract(self) -> None:
    logger.info("Retracting Z to safe position...")
    self._ctrl.move(
      [self._move_info("z", self._safe_z_position)],
      wait=True,
    )

  async def _move_xy(self) -> None:
    x = self._tp.get_teachpoint(self._location, "x")
    y = self._tp.get_teachpoint(self._location, "y")
    logger.info("Moving XY to location %d (%.2f, %.2f)...", self._location, x, y)
    self._ctrl.move(
      [
        self._move_info("x", x),
        self._move_info("y", y),
      ],
      wait=True,
    )

  async def _lower_z(self) -> None:
    z = self._target_z()
    logger.info("Lowering Z to %.2f mm...", z)
    self._ctrl.move(
      [self._move_info("z", z)],
      wait=True,
    )

  def _target_z(self) -> float:
    if self._only_move_z:
      return self._safe_z_position
    z = self._tp.get_teachpoint(self._location, "z")
    return z - self._approach_height if self._approach_height > 0 else z


class AspirateTask(StateMachineTask):
  """Aspirate a volume at a deck location."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    location: int,
    volume: float,
    pre_aspirate_volume: float = 0.0,
    post_aspirate_volume: float = 0.0,
    distance_from_bottom: float = 1.0,
    safe_z_position: float = Z_SAFE,
    labware: Optional[Labware] = None,
    head_type: Optional[HeadType] = None,
    head_mode: Optional[HeadMode] = None,
    plate_selection: Optional[PlateSelection] = None,
    dynamic_tip_extension: float = 0.0,
    tip_touch: bool = False,
    liquid_class: Optional[Dict[str, Any]] = None,
    pipette_technique: Optional[Dict[str, Any]] = None,
    deck: Optional[DeckState] = None,
    teach_tip_length_mm: Optional[float] = None,
    attached_tip_length_mm: Optional[float] = None,
    tips_on_head: bool = False,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      location: The deck location to aspirate at.
      volume: The volume to aspirate, in microlitres.
      pre_aspirate_volume: An air-gap volume drawn above the labware's top
        face before descending into liquid, in microlitres. ``0`` skips
        the pre-aspirate phase entirely.
      post_aspirate_volume: An air-gap volume drawn after retracting above
        the labware's top face, in microlitres. ``0`` skips the
        post-aspirate phase entirely.
      distance_from_bottom: Clearance above the well bottom to aspirate
        at, in millimetres.
      safe_z_position: The Z position to retract to before and after the
        operation.
      labware: The labware at the target location.
      head_type: The installed head type. Falls back to the controller's
        tracked head type when omitted.
      head_mode: The active head mode/subset.
      plate_selection: The target well's anchor cell.
      dynamic_tip_extension: Millimetres the head lowers per corrected
        microlitre aspirated, applied alongside the aspirate move. ``0``
        disables it.
      tip_touch: Whether to touch the tip against the well wall at four
        points after aspirating.
      liquid_class: Per-operation motion parameters (velocities, volume
        correction, post-delay), keyed by operation name.
      pipette_technique: Swirl-technique parameters applied while
        entering/exiting the well.
      deck: The deck state, for neighbor-clearance checking. ``None``
        skips that check.
      teach_tip_length_mm: The tip length the location's teachpoint was
        taught with.
      attached_tip_length_mm: The currently attached tip's measured length.
      tips_on_head: Whether tips are currently on the head.
    """
    super().__init__(f"Aspirate_{location}")
    self._ctrl = controller
    self._tp = teachpoints
    self._location = location
    self._volume = volume
    self._pre_aspirate = pre_aspirate_volume
    self._post_aspirate = post_aspirate_volume
    self._distance_from_bottom = distance_from_bottom
    self._safe_z_position = safe_z_position
    self._labware = labware
    self._head_type = head_type
    self._head_mode = head_mode
    self._plate_selection = plate_selection
    self._dynamic_tip_extension = max(0.0, float(dynamic_tip_extension))
    self._tip_touch = bool(tip_touch)
    self._liquid_class = liquid_class
    self._pipette_technique = pipette_technique
    self._deck = deck
    self._teach_tip_length_mm = teach_tip_length_mm
    self._attached_tip_length_mm = attached_tip_length_mm
    self._tips_on_head = bool(tips_on_head)
    self._live_status: Dict[str, Any] = {
      "task": "aspirate",
      "location": self._location,
    }
    self._geometry_cache: Optional[LiquidZGeometry] = None

  def status_payload(self) -> dict:
    """Return the task's live status, including its current Z geometry."""
    payload = dict(self._live_status)
    try:
      payload.update(_liquid_geometry_status_payload(self._geometry()))
    except Exception as exc:
      payload.setdefault("geometry_error", str(exc))
    base = super().status_payload()
    if "operator_prompt" not in payload and base.get("operator_prompt"):
      payload["operator_prompt"] = base["operator_prompt"]
    return payload

  def _effective_head_type(self) -> HeadType:
    return self._head_type or self._ctrl.get_head_type()

  def _geometry(self) -> LiquidZGeometry:
    if self._geometry_cache is None:
      self._geometry_cache = _build_liquid_z_geometry(
        teachpoints=self._tp,
        location=self._location,
        labware=self._labware,
        head_type=self._effective_head_type(),
        teach_tip_length_mm=self._teach_tip_length_mm,
        attached_tip_length_mm=self._attached_tip_length_mm,
        tips_on_head=self._tips_on_head,
        distance_from_bottom_mm=self._distance_from_bottom,
      )
    return self._geometry_cache

  def _update_status(self, step_name: str, **extra: Any) -> None:
    payload: Dict[str, Any] = {
      "task": "aspirate",
      "location": self._location,
      "step_name": step_name,
    }
    payload.update(_liquid_geometry_status_payload(self._geometry()))
    payload.update(extra)
    self._live_status = payload

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("safe_z_retract", self._safe_z_retract),
      ("move_to_location", self._move_to_location),
      ("lower_to_plate_top", self._lower_to_plate_top),
      ("pre_aspirate", self._pre_aspirate_step),
      ("lower_to_liquid", self._lower_to_liquid),
      ("aspirate_volume", self._aspirate_volume),
      ("raise_to_plate_top", self._raise_to_plate_top),
      ("post_aspirate", self._post_aspirate_step),
      ("tip_touch", self._tip_touch_step),
      ("retract_z", self._retract_z),
    ]

  async def _safe_z_retract(self) -> None:
    self._update_status("safe_z_retract")
    self._ctrl.move(
      [_axis_move(self._ctrl, "z", self._safe_z_position)],
      wait=True,
    )

  async def _move_to_location(self) -> None:
    self._update_status("move_to_location")
    x, y = self._well_xy()
    _assert_neighbor_clearance(
      command_name="Aspirate",
      teachpoints=self._tp,
      deck=self._deck,
      head_type=self._head_type,
      head_mode=self._head_mode,
      target_location=self._location,
      target_x=x,
      target_y=y,
      allowed_top_plane_mm=self._target_top_plane(),
    )
    logger.info("Moving to location %d for aspiration...", self._location)
    self._ctrl.move(
      [
        _axis_move(self._ctrl, "x", x),
        _axis_move(self._ctrl, "y", y),
      ],
      wait=True,
    )

  async def _lower_to_plate_top(self) -> None:
    if self._pre_aspirate <= 0:
      return
    self._update_status("lower_to_plate_top")
    await self._z_move(self._geometry().top_plane_head_z, phase="enter")

  async def _pre_aspirate_step(self) -> None:
    if self._pre_aspirate <= 0:
      return
    self._update_status("pre_aspirate", pre_aspirate_volume_ul=self._pre_aspirate)
    logger.info("Pre-aspirating %.2f uL (air)...", self._pre_aspirate)
    current_w = self._ctrl.get_position("w")
    self._ctrl.move(
      [
        self._w_move(
          current_w + _w_axis_motion_value(self._ctrl, self._corrected_volume(self._pre_aspirate)),
          operation="aspirate",
        )
      ],
      wait=True,
    )

  async def _lower_to_liquid(self) -> None:
    self._update_status("lower_to_liquid")
    await self._z_move(self._target_z(), phase="enter")

  async def _aspirate_volume(self) -> None:
    volume = self._corrected_volume(self._volume)
    self._update_status("aspirate_volume", commanded_volume_ul=volume)
    logger.info("Aspirating %.2f uL...", self._volume)
    current_w = self._ctrl.get_position("w")
    z_moves: List[AxisMoveInfo] = []
    if self._dynamic_tip_extension > 0 and volume > 0:
      current_z = self._ctrl.get_position("z")
      z_moves.append(_axis_move(self._ctrl, "z", current_z - self._dynamic_tip_extension))
    self._ctrl.move(
      [
        self._w_move(current_w + _w_axis_motion_value(self._ctrl, volume), operation="aspirate"),
        *z_moves,
      ],
      wait=True,
    )
    await self._post_delay("aspirate")

  async def _raise_to_plate_top(self) -> None:
    if self._post_aspirate <= 0:
      return
    self._update_status("raise_to_plate_top")
    await self._z_move(self._geometry().top_plane_head_z, phase="exit")

  async def _post_aspirate_step(self) -> None:
    if self._post_aspirate <= 0:
      return
    self._update_status("post_aspirate", post_aspirate_volume_ul=self._post_aspirate)
    logger.info("Post-aspirating %.2f uL (air)...", self._post_aspirate)
    current_w = self._ctrl.get_position("w")
    self._ctrl.move(
      [
        self._w_move(
          current_w + _w_axis_motion_value(self._ctrl, self._corrected_volume(self._post_aspirate)),
          operation="aspirate",
        )
      ],
      wait=True,
    )

  async def _tip_touch_step(self) -> None:
    if not self._tip_touch:
      return
    self._update_status("tip_touch")
    await self._perform_tip_touch()

  async def _retract_z(self) -> None:
    self._update_status("retract_z")
    await self._z_move(self._safe_z_position, phase="exit")

  def _target_z(self) -> float:
    return self._geometry().target_head_z

  def _target_top_plane(self) -> float:
    tip_length = self._attached_tip_length_mm or 0.0
    if self._deck is not None:
      return (
        float(self._deck.get_height(self._location)) + tip_length - _NEIGHBOR_CLEARANCE_SAFETY_MM
      )
    return (
      float(self._labware.height if self._labware is not None else 0.0)
      + tip_length
      - _NEIGHBOR_CLEARANCE_SAFETY_MM
    )

  def _well_xy(self) -> Tuple[float, float]:
    teach_x = self._tp.get_teachpoint(self._location, "x")
    teach_y = self._tp.get_teachpoint(self._location, "y")
    if self._labware is None or self._plate_selection is None or self._head_mode is None:
      return teach_x, teach_y
    offset_x, offset_y = well_center_offset_from_teachpoint_mm(
      self._labware.metadata,
      row=int(self._plate_selection.row),
      col=int(self._plate_selection.col),
    )
    # Distinct from _effective_head_type(): the well-position offset uses
    # the 96-channel head geometry whenever no head type is supplied here,
    # regardless of which head the controller actually has installed.
    head_type = self._head_type or "96_d_70"
    head_offset_x, head_offset_y = head_mode_offsets_mm(head_type, self._head_mode)
    return teach_x + offset_x - head_offset_x, teach_y + offset_y - head_offset_y

  def _operation_config(self, operation: str) -> Dict[str, Any]:
    if not self._liquid_class:
      return {}
    return dict(self._liquid_class.get(operation, {}) or {})

  def _corrected_volume(self, volume: float) -> float:
    equation = dict((self._liquid_class or {}).get("equation", {}) or {})
    control_points = list(equation.get("control_points") or [])
    if control_points:
      return max(0.0, _interpolate_control_points(control_points, volume))
    coefficients = list(equation.get("coefficients") or [0.0, 1.0])
    return max(0.0, _evaluate_volume_polynomial(coefficients, volume))

  def _w_move(self, position_ul: float, *, operation: str) -> AxisMoveInfo:
    cfg = self._operation_config(operation)
    return _axis_move(
      self._ctrl,
      "w",
      position_ul,
      velocity=float(cfg.get("w_velocity_ul_s") or 0.0),
      acceleration=float(cfg.get("w_acceleration_ul_s2") or 0.0),
    )

  async def _post_delay(self, operation: str) -> None:
    cfg = self._operation_config(operation)
    delay = float(cfg.get("post_delay_ms") or 0) / 1000.0
    if delay > 0:
      await asyncio.sleep(delay)

  async def _z_move(self, target_z: float, *, phase: str) -> None:
    cfg = self._operation_config("aspirate")
    key_phase = "in" if phase == "enter" else "out"
    velocity = float(cfg.get(f"z_{key_phase}_velocity_mm_s") or 0.0)
    acceleration = float(cfg.get(f"z_{key_phase}_acceleration_mm_s2") or 0.0)
    swirl_before = (
      phase == "enter"
      and self._technique_enabled("aspirate")
      and self._technique_phase_allows("enter")
    )
    swirl_after = (
      phase == "exit"
      and self._technique_enabled("aspirate")
      and self._technique_phase_allows("exit")
    )
    if swirl_before:
      await self._execute_swirl(target_z, phase=phase)
      return
    if swirl_after:
      await self._execute_swirl(float(self._ctrl.get_position("z")), phase=phase)
    self._move_z_profiled(target_z, velocity=velocity, acceleration=acceleration, phase=phase)

  def _move_z_profiled(
    self, target_z: float, *, velocity: float, acceleration: float, phase: str
  ) -> None:
    _move_liquid_z_profiled(
      self._ctrl,
      top_plane_head_z=self._geometry().top_plane_head_z,
      target_z=target_z,
      velocity=velocity,
      acceleration=acceleration,
      phase=phase,
    )

  def _technique_enabled(self, operation: str) -> bool:
    if not self._pipette_technique:
      return False
    return bool(self._pipette_technique.get(f"apply_on_{operation}", False))

  def _technique_phase_allows(self, phase: str) -> bool:
    z_phase = str((self._pipette_technique or {}).get("z_phase") or "both")
    return z_phase == "both" or z_phase == phase

  def _safe_swirl_radius_mm(self) -> float:
    requested = float((self._pipette_technique or {}).get("radius_mm") or 0.0)
    if requested <= 0:
      return 0.0
    diameter = (
      float((self._labware.metadata or {}).get("well_diameter_mm") or 0.0) if self._labware else 0.0
    )
    if diameter > 0:
      return max(0.0, min(requested, max(0.0, diameter / 2.0 - 0.25)))
    return min(requested, 0.5)

  async def _execute_swirl(self, target_z: float, *, phase: str) -> None:
    radius = self._safe_swirl_radius_mm()
    cfg = self._operation_config("aspirate")
    key_phase = "in" if phase == "enter" else "out"
    velocity = float(cfg.get(f"z_{key_phase}_velocity_mm_s") or 0.0)
    acceleration = float(cfg.get(f"z_{key_phase}_acceleration_mm_s2") or 0.0)
    self._move_z_profiled(target_z, velocity=velocity, acceleration=acceleration, phase=phase)
    if radius <= 0:
      return
    x_center, y_center = self._well_xy()
    segments = max(4, int((self._pipette_technique or {}).get("segments") or 12))
    clockwise = bool((self._pipette_technique or {}).get("clockwise", True))
    for segment in range(segments):
      fraction = (segment + 1) / segments
      angle = (2.0 * math.pi * fraction) * (-1.0 if clockwise else 1.0)
      self._ctrl.move(
        [
          _axis_move(self._ctrl, "x", x_center + math.cos(angle) * radius),
          _axis_move(self._ctrl, "y", y_center + math.sin(angle) * radius),
        ],
        wait=True,
      )
      delay = _simulation_motion_delay(self._ctrl)
      if delay > 0:
        await asyncio.sleep(delay)
    self._ctrl.move(
      [
        _axis_move(self._ctrl, "x", x_center),
        _axis_move(self._ctrl, "y", y_center),
      ],
      wait=True,
    )

  def _tip_touch_radius_mm(self) -> float:
    diameter = (
      float((self._labware.metadata or {}).get("well_diameter_mm") or 0.0) if self._labware else 0.0
    )
    if diameter > 0:
      return max(0.25, diameter / 2.0 - 0.5)
    return 0.5

  async def _perform_tip_touch(self) -> None:
    x_center, y_center = self._well_xy()
    radius = self._tip_touch_radius_mm()
    for dx, dy in ((radius, 0.0), (0.0, radius), (-radius, 0.0), (0.0, -radius)):
      self._ctrl.move(
        [
          _axis_move(self._ctrl, "x", x_center + dx),
          _axis_move(self._ctrl, "y", y_center + dy),
        ],
        wait=True,
      )
    self._ctrl.move(
      [
        _axis_move(self._ctrl, "x", x_center),
        _axis_move(self._ctrl, "y", y_center),
      ],
      wait=True,
    )


class DispenseTask(StateMachineTask):
  """Dispense a volume at a deck location."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    location: int,
    volume: float,
    blowout_volume: float = 0.0,
    distance_from_bottom: float = 1.0,
    safe_z_position: float = Z_SAFE,
    labware: Optional[Labware] = None,
    head_type: Optional[HeadType] = None,
    head_mode: Optional[HeadMode] = None,
    plate_selection: Optional[PlateSelection] = None,
    empty_tips: bool = False,
    dynamic_tip_retraction: float = 0.0,
    tip_touch: bool = False,
    liquid_class: Optional[Dict[str, Any]] = None,
    pipette_technique: Optional[Dict[str, Any]] = None,
    deck: Optional[DeckState] = None,
    teach_tip_length_mm: Optional[float] = None,
    attached_tip_length_mm: Optional[float] = None,
    tips_on_head: bool = False,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      location: The deck location to dispense at.
      volume: The volume to dispense, in microlitres.
      blowout_volume: An extra volume dispensed beyond ``volume`` to clear
        the tip, in microlitres.
      distance_from_bottom: Clearance above the well bottom to dispense
        at, in millimetres.
      safe_z_position: The Z position to retract to before and after the
        operation.
      labware: The labware at the target location.
      head_type: The installed head type. Falls back to the controller's
        tracked head type when omitted.
      head_mode: The active head mode/subset.
      plate_selection: The target well's anchor cell.
      empty_tips: Dispense the tip's entire remaining contents (drives W
        to 0) instead of a fixed volume.
      dynamic_tip_retraction: Millimetres the head raises per corrected
        microlitre dispensed, applied alongside the dispense move. ``0``
        disables it.
      tip_touch: Whether to touch the tip against the well wall at four
        points after dispensing.
      liquid_class: Per-operation motion parameters (velocities, volume
        correction, post-delay), keyed by operation name.
      pipette_technique: Swirl-technique parameters applied while
        entering/exiting the well.
      deck: The deck state, for neighbor-clearance checking. ``None``
        skips that check.
      teach_tip_length_mm: The tip length the location's teachpoint was
        taught with.
      attached_tip_length_mm: The currently attached tip's measured length.
      tips_on_head: Whether tips are currently on the head.
    """
    super().__init__(f"Dispense_{location}")
    self._ctrl = controller
    self._tp = teachpoints
    self._location = location
    self._volume = volume
    self._blowout = blowout_volume
    self._distance_from_bottom = distance_from_bottom
    self._safe_z_position = safe_z_position
    self._labware = labware
    self._head_type = head_type
    self._head_mode = head_mode
    self._plate_selection = plate_selection
    self._empty_tips = bool(empty_tips)
    self._dynamic_tip_retraction = max(0.0, float(dynamic_tip_retraction))
    self._tip_touch = bool(tip_touch)
    self._liquid_class = liquid_class
    self._pipette_technique = pipette_technique
    self._deck = deck
    self._teach_tip_length_mm = teach_tip_length_mm
    self._attached_tip_length_mm = attached_tip_length_mm
    self._tips_on_head = bool(tips_on_head)
    self._live_status: Dict[str, Any] = {
      "task": "dispense",
      "location": self._location,
    }
    self._geometry_cache: Optional[LiquidZGeometry] = None

  def status_payload(self) -> dict:
    """Return the task's live status, including its current Z geometry."""
    payload = dict(self._live_status)
    try:
      payload.update(_liquid_geometry_status_payload(self._geometry()))
    except Exception as exc:
      payload.setdefault("geometry_error", str(exc))
    base = super().status_payload()
    if "operator_prompt" not in payload and base.get("operator_prompt"):
      payload["operator_prompt"] = base["operator_prompt"]
    return payload

  def _effective_head_type(self) -> HeadType:
    return self._head_type or self._ctrl.get_head_type()

  def _geometry(self) -> LiquidZGeometry:
    if self._geometry_cache is None:
      self._geometry_cache = _build_liquid_z_geometry(
        teachpoints=self._tp,
        location=self._location,
        labware=self._labware,
        head_type=self._effective_head_type(),
        teach_tip_length_mm=self._teach_tip_length_mm,
        attached_tip_length_mm=self._attached_tip_length_mm,
        tips_on_head=self._tips_on_head,
        distance_from_bottom_mm=self._distance_from_bottom,
      )
    return self._geometry_cache

  def _update_status(self, step_name: str, **extra: Any) -> None:
    payload: Dict[str, Any] = {
      "task": "dispense",
      "location": self._location,
      "step_name": step_name,
    }
    payload.update(_liquid_geometry_status_payload(self._geometry()))
    payload.update(extra)
    self._live_status = payload

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("safe_z_retract", self._safe_z_retract),
      ("move_to_location", self._move_to_location),
      ("lower_to_liquid", self._lower_to_liquid),
      ("dispense_volume", self._dispense_volume),
      ("tip_touch", self._tip_touch_step),
      ("retract_z", self._retract_z),
    ]

  async def _safe_z_retract(self) -> None:
    self._update_status("safe_z_retract")
    self._ctrl.move(
      [_axis_move(self._ctrl, "z", self._safe_z_position)],
      wait=True,
    )

  async def _move_to_location(self) -> None:
    self._update_status("move_to_location")
    x, y = self._well_xy()
    _assert_neighbor_clearance(
      command_name="Dispense",
      teachpoints=self._tp,
      deck=self._deck,
      head_type=self._head_type,
      head_mode=self._head_mode,
      target_location=self._location,
      target_x=x,
      target_y=y,
      allowed_top_plane_mm=self._target_top_plane(),
    )
    logger.info("Moving to location %d for dispensing...", self._location)
    self._ctrl.move(
      [
        _axis_move(self._ctrl, "x", x),
        _axis_move(self._ctrl, "y", y),
      ],
      wait=True,
    )

  async def _lower_to_liquid(self) -> None:
    self._update_status("lower_to_liquid")
    await self._z_move(self._target_z(), phase="enter")

  async def _dispense_volume(self) -> None:
    current_w = self._ctrl.get_position("w")
    target_w = self._target_w_after_dispense(current_w)
    dispensed_ul = max(0.0, current_w - target_w)
    self._update_status("dispense_volume", dispensed_volume_ul=dispensed_ul)
    logger.info(
      "Emptying tips by dispensing %.2f uL..." if self._empty_tips else "Dispensing %.2f uL...",
      dispensed_ul,
    )
    z_moves: List[AxisMoveInfo] = []
    if self._dynamic_tip_retraction > 0 and dispensed_ul > 0:
      current_z = self._ctrl.get_position("z")
      z_moves.append(
        _axis_move(self._ctrl, "z", current_z + self._dynamic_tip_retraction * dispensed_ul)
      )
    self._ctrl.move([self._w_move(target_w, operation="dispense"), *z_moves], wait=True)
    await self._post_delay("dispense")

  async def _tip_touch_step(self) -> None:
    if not self._tip_touch:
      return
    self._update_status("tip_touch")
    await self._perform_tip_touch()

  async def _retract_z(self) -> None:
    self._update_status("retract_z")
    await self._z_move(self._safe_z_position, phase="exit")

  def _target_z(self) -> float:
    return self._geometry().target_head_z

  def _target_top_plane(self) -> float:
    tip_length = self._attached_tip_length_mm or 0.0
    if self._deck is not None:
      return (
        float(self._deck.get_height(self._location)) + tip_length - _NEIGHBOR_CLEARANCE_SAFETY_MM
      )
    return (
      float(self._labware.height if self._labware is not None else 0.0)
      + tip_length
      - _NEIGHBOR_CLEARANCE_SAFETY_MM
    )

  def _well_xy(self) -> Tuple[float, float]:
    teach_x = self._tp.get_teachpoint(self._location, "x")
    teach_y = self._tp.get_teachpoint(self._location, "y")
    if self._labware is None or self._plate_selection is None or self._head_mode is None:
      return teach_x, teach_y
    offset_x, offset_y = well_center_offset_from_teachpoint_mm(
      self._labware.metadata,
      row=int(self._plate_selection.row),
      col=int(self._plate_selection.col),
    )
    # Distinct from _effective_head_type(): the well-position offset uses
    # the 96-channel head geometry whenever no head type is supplied here,
    # regardless of which head the controller actually has installed.
    head_type = self._head_type or "96_d_70"
    head_offset_x, head_offset_y = head_mode_offsets_mm(head_type, self._head_mode)
    return teach_x + offset_x - head_offset_x, teach_y + offset_y - head_offset_y

  def _operation_config(self, operation: str) -> Dict[str, Any]:
    if not self._liquid_class:
      return {}
    return dict(self._liquid_class.get(operation, {}) or {})

  def _corrected_volume(self, volume: float) -> float:
    equation = dict((self._liquid_class or {}).get("equation", {}) or {})
    control_points = list(equation.get("control_points") or [])
    if control_points:
      return max(0.0, _interpolate_control_points(control_points, volume))
    coefficients = list(equation.get("coefficients") or [0.0, 1.0])
    return max(0.0, _evaluate_volume_polynomial(coefficients, volume))

  def _w_move(self, position_ul: float, *, operation: str) -> AxisMoveInfo:
    cfg = self._operation_config(operation)
    return _axis_move(
      self._ctrl,
      "w",
      position_ul,
      velocity=float(cfg.get("w_velocity_ul_s") or 0.0),
      acceleration=float(cfg.get("w_acceleration_ul_s2") or 0.0),
    )

  async def _post_delay(self, operation: str) -> None:
    cfg = self._operation_config(operation)
    delay = float(cfg.get("post_delay_ms") or 0) / 1000.0
    if delay > 0:
      await asyncio.sleep(delay)

  async def _z_move(self, target_z: float, *, phase: str) -> None:
    cfg = self._operation_config("dispense")
    key_phase = "in" if phase == "enter" else "out"
    velocity = float(cfg.get(f"z_{key_phase}_velocity_mm_s") or 0.0)
    acceleration = float(cfg.get(f"z_{key_phase}_acceleration_mm_s2") or 0.0)
    swirl_before = (
      phase == "enter"
      and self._technique_enabled("dispense")
      and self._technique_phase_allows("enter")
    )
    swirl_after = (
      phase == "exit"
      and self._technique_enabled("dispense")
      and self._technique_phase_allows("exit")
    )
    if swirl_before:
      await self._execute_swirl(target_z, phase=phase)
      return
    if swirl_after:
      await self._execute_swirl(float(self._ctrl.get_position("z")), phase=phase)
    self._move_z_profiled(target_z, velocity=velocity, acceleration=acceleration, phase=phase)

  def _move_z_profiled(
    self, target_z: float, *, velocity: float, acceleration: float, phase: str
  ) -> None:
    _move_liquid_z_profiled(
      self._ctrl,
      top_plane_head_z=self._geometry().top_plane_head_z,
      target_z=target_z,
      velocity=velocity,
      acceleration=acceleration,
      phase=phase,
    )

  def _technique_enabled(self, operation: str) -> bool:
    if not self._pipette_technique:
      return False
    return bool(self._pipette_technique.get(f"apply_on_{operation}", False))

  def _technique_phase_allows(self, phase: str) -> bool:
    z_phase = str((self._pipette_technique or {}).get("z_phase") or "both")
    return z_phase == "both" or z_phase == phase

  def _safe_swirl_radius_mm(self) -> float:
    requested = float((self._pipette_technique or {}).get("radius_mm") or 0.0)
    if requested <= 0:
      return 0.0
    diameter = (
      float((self._labware.metadata or {}).get("well_diameter_mm") or 0.0) if self._labware else 0.0
    )
    if diameter > 0:
      return max(0.0, min(requested, max(0.0, diameter / 2.0 - 0.25)))
    return min(requested, 0.5)

  async def _execute_swirl(self, target_z: float, *, phase: str) -> None:
    radius = self._safe_swirl_radius_mm()
    cfg = self._operation_config("dispense")
    key_phase = "in" if phase == "enter" else "out"
    velocity = float(cfg.get(f"z_{key_phase}_velocity_mm_s") or 0.0)
    acceleration = float(cfg.get(f"z_{key_phase}_acceleration_mm_s2") or 0.0)
    self._move_z_profiled(target_z, velocity=velocity, acceleration=acceleration, phase=phase)
    if radius <= 0:
      return
    x_center, y_center = self._well_xy()
    segments = max(4, int((self._pipette_technique or {}).get("segments") or 12))
    clockwise = bool((self._pipette_technique or {}).get("clockwise", True))
    for segment in range(segments):
      fraction = (segment + 1) / segments
      angle = (2.0 * math.pi * fraction) * (-1.0 if clockwise else 1.0)
      self._ctrl.move(
        [
          _axis_move(self._ctrl, "x", x_center + math.cos(angle) * radius),
          _axis_move(self._ctrl, "y", y_center + math.sin(angle) * radius),
        ],
        wait=True,
      )
      delay = _simulation_motion_delay(self._ctrl)
      if delay > 0:
        await asyncio.sleep(delay)
    self._ctrl.move(
      [
        _axis_move(self._ctrl, "x", x_center),
        _axis_move(self._ctrl, "y", y_center),
      ],
      wait=True,
    )

  def _target_w_after_dispense(self, current_w: float) -> float:
    if self._empty_tips:
      return 0.0
    total = self._corrected_volume(self._volume + self._blowout)
    return max(0.0, current_w - _w_axis_motion_value(self._ctrl, total))

  def _tip_touch_radius_mm(self) -> float:
    diameter = (
      float((self._labware.metadata or {}).get("well_diameter_mm") or 0.0) if self._labware else 0.0
    )
    if diameter > 0:
      return max(0.25, diameter / 2.0 - 0.5)
    return 0.5

  async def _perform_tip_touch(self) -> None:
    x_center, y_center = self._well_xy()
    radius = self._tip_touch_radius_mm()
    for dx, dy in ((radius, 0.0), (0.0, radius), (-radius, 0.0), (0.0, -radius)):
      self._ctrl.move(
        [
          _axis_move(self._ctrl, "x", x_center + dx),
          _axis_move(self._ctrl, "y", y_center + dy),
        ],
        wait=True,
      )
    self._ctrl.move(
      [
        _axis_move(self._ctrl, "x", x_center),
        _axis_move(self._ctrl, "y", y_center),
      ],
      wait=True,
    )


class MixTask(StateMachineTask):
  """Mix liquid at a deck location using repeated aspirate and dispense strokes."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    location: int,
    volume: float,
    pre_aspirate_volume: float = 0.0,
    blowout_volume: float = 0.0,
    mix_cycles: int = 3,
    aspirate_distance: float = 1.0,
    dispense_distance: float = 1.0,
    dispense_at_different_distance: bool = False,
    safe_z_position: float = Z_SAFE,
    labware: Optional[Labware] = None,
    head_type: Optional[HeadType] = None,
    head_mode: Optional[HeadMode] = None,
    plate_selection: Optional[PlateSelection] = None,
    dynamic_tip_extension: float = 0.0,
    tip_touch: bool = False,
    liquid_class: Optional[Dict[str, Any]] = None,
    pipette_technique: Optional[Dict[str, Any]] = None,
    deck: Optional[DeckState] = None,
    teach_tip_length_mm: Optional[float] = None,
    attached_tip_length_mm: Optional[float] = None,
    tips_on_head: bool = False,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      location: The deck location to mix at.
      volume: The volume drawn and expelled on each mix stroke, in
        microlitres.
      pre_aspirate_volume: An extra air-gap volume drawn along with
        ``volume`` on every aspirate stroke, in microlitres.
      blowout_volume: An extra volume dispensed beyond ``volume`` (plus
        ``pre_aspirate_volume``) on every dispense stroke, in microlitres.
      mix_cycles: Number of aspirate/dispense strokes to perform.
      aspirate_distance: Clearance above the well bottom for the aspirate
        stroke, in millimetres.
      dispense_distance: Clearance above the well bottom for the dispense
        stroke, in millimetres, when ``dispense_at_different_distance``.
      dispense_at_different_distance: Move to ``dispense_distance`` before
        each dispense stroke instead of staying at ``aspirate_distance``.
      safe_z_position: The Z position to retract to before and after the
        operation.
      labware: The labware at the target location.
      head_type: The installed head type. Falls back to the controller's
        tracked head type when omitted.
      head_mode: The active head mode/subset.
      plate_selection: The target well's anchor cell.
      dynamic_tip_extension: Millimetres the head lowers per corrected
        microlitre aspirated on each stroke. ``0`` disables it.
      tip_touch: Whether to touch the tip against the well wall at four
        points after the final stroke.
      liquid_class: Per-operation motion parameters (velocities, volume
        correction, post-delay), keyed by operation name.
      pipette_technique: Swirl-technique parameters (not applied to mix
        strokes themselves; retained for parity with Aspirate/Dispense's
        radius/well-diameter tip-touch sizing).
      deck: The deck state, for neighbor-clearance checking. ``None``
        skips that check.
      teach_tip_length_mm: The tip length the location's teachpoint was
        taught with.
      attached_tip_length_mm: The currently attached tip's measured length.
      tips_on_head: Whether tips are currently on the head.
    """
    super().__init__(f"Mix_{location}")
    self._ctrl = controller
    self._tp = teachpoints
    self._location = location
    self._volume = float(volume)
    self._pre_aspirate = float(pre_aspirate_volume)
    self._blowout = float(blowout_volume)
    self._mix_cycles = max(1, int(mix_cycles))
    self._aspirate_distance = float(aspirate_distance)
    self._dispense_distance = float(dispense_distance)
    self._dispense_at_different_distance = bool(dispense_at_different_distance)
    self._safe_z_position = safe_z_position
    self._labware = labware
    self._head_type = head_type
    self._head_mode = head_mode
    self._plate_selection = plate_selection
    self._dynamic_tip_extension = max(0.0, float(dynamic_tip_extension))
    self._tip_touch = bool(tip_touch)
    self._liquid_class = liquid_class
    self._pipette_technique = pipette_technique
    self._deck = deck
    self._teach_tip_length_mm = teach_tip_length_mm
    self._attached_tip_length_mm = attached_tip_length_mm
    self._tips_on_head = bool(tips_on_head)
    self._live_status: Dict[str, Any] = {
      "task": "mix",
      "location": self._location,
    }
    self._geometry_cache: Dict[float, LiquidZGeometry] = {}

  def status_payload(self) -> dict:
    """Return the task's live status, including its current Z geometry."""
    payload = dict(self._live_status)
    try:
      distance = float(payload.get("distance_from_bottom_mm", self._aspirate_distance))
      payload.update(_liquid_geometry_status_payload(self._geometry(distance)))
    except Exception as exc:
      payload.setdefault("geometry_error", str(exc))
    base = super().status_payload()
    if "operator_prompt" not in payload and base.get("operator_prompt"):
      payload["operator_prompt"] = base["operator_prompt"]
    return payload

  def _effective_head_type(self) -> HeadType:
    return self._head_type or self._ctrl.get_head_type()

  def _geometry(self, distance_from_bottom: float) -> LiquidZGeometry:
    key = float(distance_from_bottom)
    geometry = self._geometry_cache.get(key)
    if geometry is None:
      geometry = _build_liquid_z_geometry(
        teachpoints=self._tp,
        location=self._location,
        labware=self._labware,
        head_type=self._effective_head_type(),
        teach_tip_length_mm=self._teach_tip_length_mm,
        attached_tip_length_mm=self._attached_tip_length_mm,
        tips_on_head=self._tips_on_head,
        distance_from_bottom_mm=key,
      )
      self._geometry_cache[key] = geometry
    return geometry

  def _top_plane_head_z(self) -> float:
    return self._geometry(self._aspirate_distance).top_plane_head_z

  def _update_status(
    self, step_name: str, *, distance_from_bottom_mm: Optional[float] = None, **extra: Any
  ) -> None:
    distance = float(
      self._aspirate_distance if distance_from_bottom_mm is None else distance_from_bottom_mm
    )
    payload: Dict[str, Any] = {
      "task": "mix",
      "location": self._location,
      "step_name": step_name,
      "distance_from_bottom_mm": distance,
    }
    payload.update(_liquid_geometry_status_payload(self._geometry(distance)))
    payload.update(extra)
    self._live_status = payload

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("safe_z_retract", self._safe_z_retract),
      ("move_to_location", self._move_to_location),
      ("mix_cycles", self._mix_cycles_step),
      ("tip_touch", self._tip_touch_step),
      ("retract_z", self._retract_z),
    ]

  async def _safe_z_retract(self) -> None:
    self._update_status("safe_z_retract")
    self._ctrl.move([_axis_move(self._ctrl, "z", self._safe_z_position)], wait=True)

  async def _move_to_location(self) -> None:
    self._update_status("move_to_location")
    x, y = self._well_xy()
    _assert_neighbor_clearance(
      command_name="Mix",
      teachpoints=self._tp,
      deck=self._deck,
      head_type=self._head_type,
      head_mode=self._head_mode,
      target_location=self._location,
      target_x=x,
      target_y=y,
      allowed_top_plane_mm=self._target_top_plane(),
    )
    logger.info("Moving to location %d for mixing...", self._location)
    self._ctrl.move(
      [_axis_move(self._ctrl, "x", x), _axis_move(self._ctrl, "y", y)],
      wait=True,
    )

  async def _mix_cycles_step(self) -> None:
    for cycle_index in range(self._mix_cycles):
      logger.info("Mix cycle %d/%d...", cycle_index + 1, self._mix_cycles)
      aspirate_z = self._target_z(self._aspirate_distance)
      self._update_status(
        "mix_cycle",
        cycle_index=cycle_index + 1,
        cycle_count=self._mix_cycles,
        operation="aspirate",
        distance_from_bottom_mm=self._aspirate_distance,
      )
      await self._z_move(
        aspirate_z,
        operation="aspirate",
        phase="enter",
        distance_from_bottom=self._aspirate_distance,
      )
      await self._aspirate_once()
      dispense_z = self._target_z(
        self._dispense_distance if self._dispense_at_different_distance else self._aspirate_distance
      )
      if abs(dispense_z - self._ctrl.get_position("z")) > 1e-6:
        dispense_distance = (
          self._dispense_distance
          if self._dispense_at_different_distance
          else self._aspirate_distance
        )
        self._update_status(
          "mix_cycle",
          cycle_index=cycle_index + 1,
          cycle_count=self._mix_cycles,
          operation="dispense",
          distance_from_bottom_mm=dispense_distance,
        )
        await self._z_move(
          dispense_z,
          operation="dispense",
          phase="enter",
          distance_from_bottom=dispense_distance,
        )
      await self._dispense_once()

  async def _aspirate_once(self) -> None:
    total = self._corrected_volume(self._pre_aspirate + self._volume)
    current_w = self._ctrl.get_position("w")
    z_moves: List[AxisMoveInfo] = []
    if self._dynamic_tip_extension > 0 and total > 0:
      current_z = self._ctrl.get_position("z")
      z_moves.append(_axis_move(self._ctrl, "z", current_z - self._dynamic_tip_extension * total))
    self._ctrl.move(
      [
        self._w_move(current_w + _w_axis_motion_value(self._ctrl, total), operation="aspirate"),
        *z_moves,
      ],
      wait=True,
    )
    await self._post_delay("aspirate")

  async def _dispense_once(self) -> None:
    current_w = self._ctrl.get_position("w")
    total = self._corrected_volume(self._pre_aspirate + self._volume + self._blowout)
    target_w = max(0.0, current_w - _w_axis_motion_value(self._ctrl, total))
    self._ctrl.move([self._w_move(target_w, operation="dispense")], wait=True)
    await self._post_delay("dispense")

  async def _tip_touch_step(self) -> None:
    if not self._tip_touch:
      return
    self._update_status("tip_touch")
    await self._perform_tip_touch()

  async def _retract_z(self) -> None:
    retract_distance = (
      self._dispense_distance if self._dispense_at_different_distance else self._aspirate_distance
    )
    self._update_status("retract_z", distance_from_bottom_mm=retract_distance)
    await self._z_move(
      self._safe_z_position,
      operation="dispense",
      phase="exit",
      distance_from_bottom=retract_distance,
    )

  def _target_z(self, distance_from_bottom: float) -> float:
    return self._geometry(distance_from_bottom).target_head_z

  def _target_top_plane(self) -> float:
    tip_length = self._attached_tip_length_mm or 0.0
    if self._deck is not None:
      return (
        float(self._deck.get_height(self._location)) + tip_length - _NEIGHBOR_CLEARANCE_SAFETY_MM
      )
    return (
      float(self._labware.height if self._labware is not None else 0.0)
      + tip_length
      - _NEIGHBOR_CLEARANCE_SAFETY_MM
    )

  def _well_xy(self) -> Tuple[float, float]:
    teach_x = self._tp.get_teachpoint(self._location, "x")
    teach_y = self._tp.get_teachpoint(self._location, "y")
    if self._labware is None or self._plate_selection is None or self._head_mode is None:
      return teach_x, teach_y
    offset_x, offset_y = well_center_offset_from_teachpoint_mm(
      self._labware.metadata,
      row=int(self._plate_selection.row),
      col=int(self._plate_selection.col),
    )
    # Distinct from _effective_head_type(): the well-position offset uses
    # the 96-channel head geometry whenever no head type is supplied here,
    # regardless of which head the controller actually has installed.
    head_type = self._head_type or "96_d_70"
    head_offset_x, head_offset_y = head_mode_offsets_mm(head_type, self._head_mode)
    return teach_x + offset_x - head_offset_x, teach_y + offset_y - head_offset_y

  def _operation_config(self, operation: str) -> Dict[str, Any]:
    if not self._liquid_class:
      return {}
    return dict(self._liquid_class.get(operation, {}) or {})

  def _corrected_volume(self, volume: float) -> float:
    equation = dict((self._liquid_class or {}).get("equation", {}) or {})
    control_points = list(equation.get("control_points") or [])
    if control_points:
      return max(0.0, _interpolate_control_points(control_points, volume))
    coefficients = list(equation.get("coefficients") or [0.0, 1.0])
    return max(0.0, _evaluate_volume_polynomial(coefficients, volume))

  def _w_move(self, position_ul: float, *, operation: str) -> AxisMoveInfo:
    cfg = self._operation_config(operation)
    return _axis_move(
      self._ctrl,
      "w",
      position_ul,
      velocity=float(cfg.get("w_velocity_ul_s") or 0.0),
      acceleration=float(cfg.get("w_acceleration_ul_s2") or 0.0),
    )

  async def _post_delay(self, operation: str) -> None:
    cfg = self._operation_config(operation)
    delay = float(cfg.get("post_delay_ms") or 0) / 1000.0
    if delay > 0:
      await asyncio.sleep(delay)

  async def _z_move(
    self,
    target_z: float,
    *,
    operation: str,
    phase: str,
    distance_from_bottom: float,
  ) -> None:
    cfg = self._operation_config(operation)
    key_phase = "in" if phase == "enter" else "out"
    velocity = float(cfg.get(f"z_{key_phase}_velocity_mm_s") or 0.0)
    acceleration = float(cfg.get(f"z_{key_phase}_acceleration_mm_s2") or 0.0)
    _move_liquid_z_profiled(
      self._ctrl,
      top_plane_head_z=self._geometry(distance_from_bottom).top_plane_head_z,
      target_z=target_z,
      velocity=velocity,
      acceleration=acceleration,
      phase=phase,
    )

  def _tip_touch_radius_mm(self) -> float:
    diameter = (
      float((self._labware.metadata or {}).get("well_diameter_mm") or 0.0) if self._labware else 0.0
    )
    if diameter > 0:
      return max(0.25, diameter / 2.0 - 0.5)
    return 0.5

  async def _perform_tip_touch(self) -> None:
    x_center, y_center = self._well_xy()
    radius = self._tip_touch_radius_mm()
    for dx, dy in ((radius, 0.0), (0.0, radius), (-radius, 0.0), (0.0, -radius)):
      self._ctrl.move(
        [
          _axis_move(self._ctrl, "x", x_center + dx),
          _axis_move(self._ctrl, "y", y_center + dy),
        ],
        wait=True,
      )
    self._ctrl.move(
      [
        _axis_move(self._ctrl, "x", x_center),
        _axis_move(self._ctrl, "y", y_center),
      ],
      wait=True,
    )


class TipsOnTask(StateMachineTask):
  """Pick up tips from a tip box location using the standard Z math."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    labware: Labware,
    head_mode: HeadMode,
    tip_selection: TipSelection,
    tip_location: int,
    tip_length_mm: float,
    safe_z_position: float = Z_SAFE,
    deck: Optional[DeckState] = None,
    tip_offsets: Optional[ResolvedTipOffsets] = None,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to pick tips up against.
      labware: The tip box labware at the target location.
      head_mode: The active head mode/subset picking up tips.
      tip_selection: The tip box anchor cell to pick up from.
      tip_location: The deck location of the tip box.
      tip_length_mm: The length of the tip being picked up, in millimetres.
      safe_z_position: The Z position to retract to before and after the
        operation.
      deck: The deck state, for neighbor-clearance checking. ``None``
        skips that check.
      tip_offsets: Resolved (head, tip box) Tips On overrides. Falls back
        to the machine configuration's safety defaults when omitted.
    """
    super().__init__(f"TipsOn_{tip_location}")
    self._ctrl = controller
    self._tp = teachpoints
    self._config = config
    self._labware = labware
    self._head_mode = head_mode
    self._tip_selection = tip_selection
    self._tip_location = tip_location
    self._safe_z_position = safe_z_position
    self._tip_length = float(tip_length_mm)
    self._tip_offsets = _tip_offsets_or_default(config, tip_offsets)
    self._live_status: Dict[str, object] = {}
    self._deck = deck
    self._operator_prompt: Optional[dict] = None

  def status_payload(self) -> dict:
    """Return the task's live status, including any pending operator prompt."""
    payload = dict(self._live_status)
    if self.status == TaskStatus.FAILED and self._operator_prompt:
      payload["operator_prompt"] = dict(self._operator_prompt)
    return payload

  def on_error_action(self, action: ErrorAction) -> None:
    """Clear the pending operator prompt once the operator has responded."""
    self._operator_prompt = None

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("safe_z_retract", self._safe_z_retract),
      ("ensure_w_zero", self._ensure_w_zero),
      ("move_to_tip_location", self._move_to_tip_location),
      ("clear_axis_faults", self._clear_axis_faults),
      ("lower_z_to_tips", self._lower_z_to_tips),
      ("tip_press_dwell", self._tip_press_dwell),
      ("retract_z", self._retract_z),
    ]

  async def _safe_z_retract(self) -> None:
    self._log_step("safe_z_retract", transfer_stage="source")
    self._ctrl.move(
      [AxisMoveInfo(axis="z", position=self._safe_z_position)],
      wait=True,
    )

  async def _ensure_w_zero(self) -> None:
    self._log_step("ensure_w_zero", transfer_stage="source")
    current_w = float(self._ctrl.get_position("w"))
    if abs(current_w) <= 1e-6:
      logger.info("W already at 0.0 uL before tips on.")
      return
    logger.warning("Resetting W to 0.0 uL before tips on (current %.3f)...", current_w)
    self._ctrl.move(
      [AxisMoveInfo(axis="w", position=0.0, velocity=0.0, acceleration=0.0)],
      wait=True,
    )

  async def _move_to_tip_location(self) -> None:
    self._log_step("move_to_tip_location", transfer_stage="source")
    x, y = self._tip_xy()
    _assert_neighbor_clearance(
      command_name="Tips On",
      teachpoints=self._tp,
      deck=self._deck,
      head_type=self._config.head.head_type,
      head_mode=self._head_mode,
      target_location=self._tip_location,
      target_x=x,
      target_y=y,
      allowed_top_plane_mm=self._target_top_plane(),
    )
    self._log_tip_geometry(x, y)
    logger.info(
      "Moving to tip location %d at subset-adjusted XY (%.3f, %.3f)...",
      self._tip_location,
      x,
      y,
    )
    self._ctrl.move(
      [
        AxisMoveInfo(axis="x", position=x),
        AxisMoveInfo(axis="y", position=y),
      ],
      wait=True,
    )

  async def _clear_axis_faults(self) -> None:
    self._log_step("clear_axis_faults", transfer_stage="source")
    try:
      axes: List[Axis] = ["x", "y", "z", "w"]
      logger.warning(
        "Clearing axis faults before tips on press: %s",
        ", ".join(axis_display_name(a) for a in axes),
      )
      self._ctrl.reset_faults(axes)
    except Exception as exc:
      logger.warning("Could not clear axis faults before tips on: %s", exc)
    if isinstance(self._ctrl, DarwinController):
      try:
        logger.warning(
          "Cycling W motor enable on DARWIN before tips on press to clear any "
          "lingering Z/W node fault."
        )
        enabled_before = None
        try:
          enabled_before = self._ctrl.is_motor_enabled("w")
        except Exception as exc:
          logger.debug("Could not query W motor state before DARWIN tips on press: %s", exc)
        try:
          self._ctrl.disable_motor("w")
          await asyncio.sleep(0.05)
        except Exception as exc:
          logger.debug(
            "Could not disable W before DARWIN tips on press (enabled_before=%s): %s",
            enabled_before,
            exc,
          )
        self._ctrl.enable_motor("w")
      except Exception as exc:
        logger.warning("Could not recycle W motor enable before DARWIN tips on press: %s", exc)

  async def _lower_z_to_tips(self) -> None:
    self._log_step("lower_z_to_tips", transfer_stage="source")
    z = self._tips_on_position() + self._tip_offsets.tips_on_z_offset
    tolerance = self._tip_offsets.tips_on_jog_tolerance
    try:
      peak_current = self._tip_press_current()
      logger.warning(
        "Tips On: %d channels, peak_current=%.3fA, Z target=%.3f (z_offset=%.3f, "
        "press tolerance=%.3f mm via %s, head_mode=%s)",
        self._num_channels(),
        peak_current,
        z,
        self._tip_offsets.tips_on_z_offset,
        tolerance,
        self._tip_offsets.source,
        self._head_mode.subset_type,
      )
      if isinstance(self._ctrl, Agile7612Controller):
        self._ctrl.tip_force_jog("z", peak_current, z)
      else:
        self._ctrl.jog(
          JogParams(
            axis="z",
            velocity=25.0,
            acceleration=250.0,
            max_position=z,
            tolerance=tolerance,
            peak_current=peak_current,
          )
        )
    except Exception as exc:
      await self._recover_to_safe_z_after_press_failure()
      message = self._tips_on_failure_message(exc)
      self._operator_prompt = {
        "kind": "tips_on_no_resistance",
        "title": "Tips On failed",
        "message": (
          message + "\n\nRetry re-attempts the press.\n"
          "Ignore marks tips as on and continues "
          "(useful when testing without hardware).\n"
          "Abort stops the workflow."
        ),
        "choices": ["retry", "ignore", "abort"],
        "location": self._tip_location,
      }
      raise RuntimeError(message) from exc

  async def _tip_press_dwell(self) -> None:
    self._log_step("tip_press_dwell", transfer_stage="source")
    dwell = float(self._config.safety.tip_press_dwell)
    if dwell > 0:
      logger.info("Dwelling after tips on for %.3f s...", dwell)
      await asyncio.sleep(dwell)

  async def _retract_z(self) -> None:
    self._log_step("retract_z", transfer_stage="mounted")
    logger.info("Retracting Z after tip pickup...")
    self._ctrl.move(
      [AxisMoveInfo(axis="z", position=self._safe_z_position)],
      wait=True,
    )

  async def _recover_to_safe_z_after_press_failure(self) -> None:
    try:
      logger.warning(
        "Tips On press failed; retracting Z to safe position %.3f at current X/Y...",
        self._safe_z_position,
      )
      self._ctrl.move(
        [AxisMoveInfo(axis="z", position=self._safe_z_position)],
        wait=True,
      )
    except Exception as retract_exc:
      logger.error("Could not retract Z after Tips On failure: %s", retract_exc)

  @staticmethod
  def _tips_on_failure_message(exc: Exception) -> str:
    message = str(exc)
    # Matches the wording emitted by both the force-jog sequence
    # ("Exceeded destination on Z." / "Unable to reach destination on Z
    # within tolerance.") and the older "on the Z axis ..." phrasing.
    indicators = (
      "Exceeded destination",
      "within tolerance",
    )
    if any(indicator in message for indicator in indicators):
      return (
        "Tips On did not encounter the expected tip resistance before reaching the press "
        "limit. The tipbox may be missing, the selected tips may be absent, or the "
        "teachpoint/box height is incorrect. The head was retracted to safe Z."
      )
    return message

  def _log_step(self, name: str, *, transfer_stage: str) -> None:
    self._live_status = {
      "task": "tips_on",
      "step": name,
      "transfer_stage": transfer_stage,
      "location": self._tip_location,
      "head_mode": self._head_mode.to_dict(),
      "tip_selection": self._tip_selection.to_dict(),
    }

  def _tips_on_position(self) -> float:
    return self._deck_surface_z() - self._labware.height

  def _target_top_plane(self) -> float:
    if self._deck is not None:
      return float(self._deck.get_height(self._tip_location))
    return float(self._labware.height)

  def _deck_surface_z(self) -> float:
    teach_z = self._tp.get_teachpoint(self._tip_location, "z")
    teach_tip_length = float(self._config.head.teach_tip_length_mm or 0.0)
    return teach_z + teach_tip_length

  def _num_channels(self) -> int:
    return max(1, int(self._head_mode.row_count) * int(self._head_mode.column_count))

  def _tip_press_current(self) -> float:
    profile_limits = self._config.current_limits or {}
    table_key = (
      "LT" if self._config.head.head_type in {"8_d_lt", "96_d_200", "96_d_200_s2"} else "ST"
    )
    table = _normalize_tip_current_table(profile_limits.get(table_key))
    if not table:
      table = LT_TIP_CURRENT_TABLE if table_key == "LT" else ST_TIP_CURRENT_TABLE
    return float(interpolate_tip_current(table, self._num_channels()))

  def _tip_xy(self) -> Tuple[float, float]:
    teach_x = self._tp.get_teachpoint(self._tip_location, "x")
    teach_y = self._tp.get_teachpoint(self._tip_location, "y")
    head_offset_x, head_offset_y = head_mode_offsets_mm(
      self._config.head.head_type, self._head_mode
    )
    tipbox_offset_x, tipbox_offset_y = self._tipbox_selection_anchor_offset()
    return teach_x + tipbox_offset_x - head_offset_x, teach_y + tipbox_offset_y - head_offset_y

  def _tipbox_selection_anchor_offset(self) -> Tuple[float, float]:
    rows, cols = _tipbox_rows_cols(self._labware.metadata or {})
    if rows <= 0 or cols <= 0:
      return 0.0, 0.0
    return well_center_offset_from_teachpoint_mm(
      self._labware.metadata,
      row=int(self._tip_selection.row),
      col=int(self._tip_selection.col),
    )

  def _log_tip_geometry(self, target_x: float, target_y: float) -> None:
    rows, cols = _tipbox_rows_cols(self._labware.metadata or {})
    (head_row_start, head_row_stop), (head_col_start, head_col_stop) = head_selected_ranges(
      self._config.head.head_type,
      self._head_mode,
    )
    head_anchor_row, head_anchor_col = head_anchor_cell(
      self._config.head.head_type, self._head_mode
    )
    (tip_row_start, tip_row_stop), (tip_col_start, tip_col_stop) = selected_anchor_ranges(
      rows,
      cols,
      self._tip_selection,
    )
    tip_anchor_row, tip_anchor_col = tipbox_anchor_cell(self._tip_selection)
    head_offset_x, head_offset_y = head_mode_offsets_mm(
      self._config.head.head_type, self._head_mode
    )
    tipbox_offset_x, tipbox_offset_y = self._tipbox_selection_anchor_offset()
    logger.warning(
      (
        "Tips On geometry: head subset=%s %dx%d config=%s head_rows=%d-%d head_cols=%d-%d "
        "head_anchor=(r%d,c%d) head_offset=(%.3f, %.3f) | "
        "tipbox_rows=%d-%d tipbox_cols=%d-%d mirror=%s tipbox_anchor=(r%d,c%d) "
        "tipbox_offset=(%.3f, %.3f) | target_xy=(%.3f, %.3f)"
      ),
      self._head_mode.subset_type,
      self._head_mode.row_count,
      self._head_mode.column_count,
      self._head_mode.subset_config,
      head_row_start + 1,
      head_row_stop,
      head_col_start + 1,
      head_col_stop,
      head_anchor_row + 1,
      head_anchor_col + 1,
      head_offset_x,
      head_offset_y,
      tip_row_start + 1,
      tip_row_stop,
      tip_col_start + 1,
      tip_col_stop,
      self._tip_selection.mirror_corner,
      tip_anchor_row + 1,
      tip_anchor_col + 1,
      tipbox_offset_x,
      tipbox_offset_y,
      target_x,
      target_y,
    )


class TipsOffTask(StateMachineTask):
  """Eject tips at a tip box or tip trash location using the standard offsets."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    labware: Labware,
    head_mode: HeadMode,
    tip_selection: Optional[TipSelection],
    tip_location: int,
    attached_tip_length_mm: float,
    safe_z_position: float = Z_SAFE,
    deck: Optional[DeckState] = None,
    tips_are_tracked: bool = True,
    tip_offsets: Optional[ResolvedTipOffsets] = None,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to eject tips against.
      labware: The tip box or trash labware at the target location.
      head_mode: The active head mode/subset ejecting tips.
      tip_selection: The tip box anchor cell to return tips to, or ``None``
        for a trash location.
      tip_location: The deck location to eject at.
      attached_tip_length_mm: The currently attached tip's measured length.
      safe_z_position: The Z position to retract to before and after the
        operation.
      deck: The deck state, for neighbor-clearance checking. ``None``
        skips that check.
      tips_are_tracked: Whether the software currently believes tips are on
        the head. ``False`` prompts for operator confirmation before
        ejecting.
      tip_offsets: Resolved (head, tip box) Tips Off overrides. Falls back
        to the machine configuration's safety defaults when omitted.
    """
    super().__init__("TipsOff")
    self._ctrl = controller
    self._tp = teachpoints
    self._config = config
    self._labware = labware
    self._head_mode = head_mode
    self._tip_selection = tip_selection
    self._tip_location = tip_location
    self._attached_tip_length_mm = attached_tip_length_mm
    self._safe_z_position = safe_z_position
    self._tip_offsets = _tip_offsets_or_default(config, tip_offsets)
    self._live_status: Dict[str, object] = {}
    self._deck = deck
    self._tips_are_tracked = tips_are_tracked

  def status_payload(self) -> dict:
    """Return the task's live status, including any pending operator prompt."""
    payload = dict(self._live_status)
    base = super().status_payload()
    if "operator_prompt" not in payload and base.get("operator_prompt"):
      payload["operator_prompt"] = base["operator_prompt"]
    return payload

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("validate_tips", self._validate_tips),
      ("safe_z_retract", self._safe_z_retract),
      ("move_to_eject_location", self._move_to_eject_location),
      ("tip_touch", self._tip_touch),
      ("eject_tips", self._eject_tips),
      ("retract_z", self._retract_z),
    ]

  async def _validate_tips(self) -> None:
    if self._tips_are_tracked:
      return
    self._operator_prompt = {
      "kind": "tips_off_no_tips_tracked",
      "title": "No tips detected",
      "message": (
        "The software does not think tips are currently on the head. "
        "This can happen after a power cycle.\n\n"
        "Retry re-checks tip state.\n"
        "Ignore proceeds with tip ejection anyway.\n"
        "Abort stops the workflow."
      ),
      "choices": ["retry", "ignore", "abort"],
      "location": self._tip_location,
    }
    raise RuntimeError("No tips are currently tracked on the head")

  async def _tip_touch(self) -> None:
    """Bump the tips sideways against the tip box wall to knock off droplets.

    Moves X left by the configured distance, then back to the original
    position.
    """
    if not self._config.safety.enable_tips_off_tip_touch:
      return
    self._log_step("tip_touch", transfer_stage="mounted")
    current_x = self._ctrl.get_position("x")
    x_cfg = self._config.axes.get("x")
    ticks_per_mm = float(x_cfg.ticks_per_eng_unit) if x_cfg is not None else 314.96
    bump_mm = (
      float(self._config.safety.tips_off_tip_touch_distance) / ticks_per_mm
      if ticks_per_mm > 0
      else 1.0
    )
    bump_target = current_x - bump_mm
    logger.info(
      "Tips-off tip touch: X %.3f -> %.3f (bump %.3f mm) -> %.3f",
      current_x,
      bump_target,
      bump_mm,
      current_x,
    )
    self._ctrl.move([AxisMoveInfo(axis="x", position=bump_target)], wait=True)
    self._ctrl.move([AxisMoveInfo(axis="x", position=current_x)], wait=True)

  async def _safe_z_retract(self) -> None:
    self._log_step("safe_z_retract", transfer_stage="mounted")
    self._ctrl.move(
      [AxisMoveInfo(axis="z", position=self._safe_z_position)],
      wait=True,
    )

  async def _move_to_eject_location(self) -> None:
    self._log_step("move_to_eject_location", transfer_stage="mounted")
    x, y = self._tip_xy()
    _assert_neighbor_clearance(
      command_name="Tips Off",
      teachpoints=self._tp,
      deck=self._deck,
      head_type=self._config.head.head_type,
      head_mode=self._head_mode,
      target_location=self._tip_location,
      target_x=x,
      target_y=y,
      allowed_top_plane_mm=self._target_top_plane(),
    )
    logger.info("Moving to tip-eject position (%.2f, %.2f)...", x, y)
    self._ctrl.move(
      [
        AxisMoveInfo(axis="x", position=x),
        AxisMoveInfo(axis="y", position=y),
      ],
      wait=True,
    )

  async def _eject_tips(self) -> None:
    self._log_step("eject_tips", transfer_stage="mounted")
    teach_z = self._tp.get_teachpoint(self._tip_location, "z")
    teach_tip_length = float(self._config.head.teach_tip_length_mm or 0.0)
    deck_surface_z = self._deck_surface_z()
    box_top_z = self._tips_on_position()
    z = self._tips_off_position()
    w_position = self._tip_offsets.tips_off_w_position
    # AxisConfig carries no separate "safe" velocity/acceleration fields;
    # this move always runs at the controller's current Z setting.
    z_velocity = 0.0
    z_acceleration = 0.0
    logger.info(
      "Moving Z for tips off: teach Z %.3f, teach tip %.3f, deck surface %.3f, box top "
      "%.3f, eject offset %.3f (via %s), target %.3f, safe vel %.3f, safe acc %.3f...",
      teach_z,
      teach_tip_length,
      deck_surface_z,
      box_top_z,
      self._tip_offsets.tips_off_z_offset,
      self._tip_offsets.source,
      z,
      z_velocity,
      z_acceleration,
    )
    logger.warning("Calculated Tips Off Z target: %.3f", z)
    self._ctrl.move(
      [AxisMoveInfo(axis="z", position=z, velocity=z_velocity, acceleration=z_acceleration)],
      wait=True,
    )
    logger.info("Ejecting tips (W -> %.1f)...", w_position)
    self._ctrl.move(
      [AxisMoveInfo(axis="w", position=w_position)],
      wait=True,
    )
    logger.info("Resetting W after tips off...")
    self._ctrl.move(
      [AxisMoveInfo(axis="w", position=0.0)],
      wait=True,
    )

  async def _retract_z(self) -> None:
    self._log_step(
      "retract_z", transfer_stage="returned" if self._tip_selection is not None else "discarded"
    )
    logger.info("Retracting Z after tip ejection...")
    self._ctrl.move(
      [AxisMoveInfo(axis="z", position=self._safe_z_position)],
      wait=True,
    )

  def _log_step(self, name: str, *, transfer_stage: str) -> None:
    self._live_status = {
      "task": "tips_off",
      "step": name,
      "transfer_stage": transfer_stage,
      "location": self._tip_location,
      "head_mode": self._head_mode.to_dict(),
      "tip_selection": None if self._tip_selection is None else self._tip_selection.to_dict(),
    }

  def _tips_on_position(self) -> float:
    return self._deck_surface_z() - self._labware.height

  def _target_top_plane(self) -> float:
    tip_length = self._attached_tip_length_mm or 0.0
    if self._deck is not None:
      return (
        float(self._deck.get_height(self._tip_location))
        + tip_length
        - _NEIGHBOR_CLEARANCE_SAFETY_MM
      )
    return float(self._labware.height) + tip_length - _NEIGHBOR_CLEARANCE_SAFETY_MM

  def _deck_surface_z(self) -> float:
    teach_z = self._tp.get_teachpoint(self._tip_location, "z")
    teach_tip_length = float(self._config.head.teach_tip_length_mm or 0.0)
    return teach_z + teach_tip_length

  def _tips_off_position(self) -> float:
    tips_on_position = self._tips_on_position()
    return tips_on_position - float(self._tip_offsets.tips_off_z_offset)

  def _tip_xy(self) -> Tuple[float, float]:
    teach_x = self._tp.get_teachpoint(self._tip_location, "x")
    teach_y = self._tp.get_teachpoint(self._tip_location, "y")
    head_offset_x, head_offset_y = head_mode_offsets_mm(
      self._config.head.head_type, self._head_mode
    )
    tipbox_offset_x, tipbox_offset_y = self._tipbox_selection_anchor_offset()
    return teach_x + tipbox_offset_x - head_offset_x, teach_y + tipbox_offset_y - head_offset_y

  def _tipbox_selection_anchor_offset(self) -> Tuple[float, float]:
    if self._tip_selection is None:
      return 0.0, 0.0
    rows, cols = _tipbox_rows_cols(self._labware.metadata or {})
    if rows <= 0 or cols <= 0:
      return 0.0, 0.0
    return well_center_offset_from_teachpoint_mm(
      self._labware.metadata,
      row=int(self._tip_selection.row),
      col=int(self._tip_selection.col),
    )


class PickPlaceTask(StateMachineTask):
  """Move a plate from one deck location to another using the gripper."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    deck: DeckState,
    from_location: int,
    to_location: int,
    speed: SpeedLevel = "med",
    plate_already_gripped: bool = False,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to move against.
      deck: The deck state, updated on a successful place.
      from_location: The deck location to pick up from.
      to_location: The deck location to place at.
      speed: The speed profile for XYZ/Zg motion.
      plate_already_gripped: Construct the task as if its own grip step had
        already run successfully. For a caller that split a pick and a
        place across two separate task instances (see
        :mod:`~..bravo`'s gripper_pick/gripper_place), the plate was
        physically gripped by the earlier instance; this lets the later
        one's place-side steps (which otherwise skip themselves absent a
        verified grip) run without re-gripping an already-held plate.
    """
    super().__init__(f"PickPlace_{from_location}_{to_location}")
    self._ctrl = controller
    self._tp = teachpoints
    self._config = config
    self._deck = deck
    self._from_location = from_location
    self._to_location = to_location
    self._speed = speed
    self._live_status: Dict[str, Any] = {}
    self._grip_attempts = 1 if plate_already_gripped else 0
    self._plate_pick_verified = plate_already_gripped
    self._force_continue_after_pickup_failure = False
    self._source_labware = self._get_source_labware()
    # The gripper physically engages ONE plate's flanges. For an ordinary
    # pickup this is the top plate (same as _source_labware). For a
    # mounted group -- a filter plate locked onto a collection plate via
    # can_mount/can_be_mounted flags -- the fingers must engage the
    # BOTTOM plate's flanges so the whole locked unit lifts together.
    # Gripping the top plate lifts only the top, leaving the bottom
    # behind (the failure mode the mount feature is designed to avoid).
    self._engage_plate = self._get_engage_plate()
    self._positions = self._calculate_positions()
    self._log_plan()

  def _get_source_labware(self) -> Labware:
    source = self._deck.get_stack(self._from_location).top
    if source is None:
      raise RuntimeError(f"No labware assigned to location {self._from_location}")
    return source

  def _get_engage_plate(self) -> Labware:
    """Return which plate's flanges the gripper physically grips.

    Defaults to :attr:`_source_labware` (top of stack) for ordinary
    pickups. If the top is flagged :attr:`~..deck.labware.Labware.is_mounted`,
    returns the BOTTOM of the mounted group -- the plate that the gripper
    must engage in order to lift the whole locked unit.
    """
    stack = self._deck.get_stack(self._from_location)
    group = stack.mounted_group_from_top()
    return group[-1] if group else self._source_labware

  def _move_info(self, axis: Axis, position: float) -> AxisMoveInfo:
    cfg = self._config.axes.get(axis)
    if cfg and self._speed in cfg.speeds:
      speed = cfg.speeds[self._speed]
      return AxisMoveInfo(
        axis=axis, position=position, velocity=speed.velocity, acceleration=speed.acceleration
      )
    return AxisMoveInfo(axis=axis, position=position)

  def _gripper_y_offset(self) -> float:
    _, head_y_offset = _gripper_head_offsets(self._config.head.head_type)
    return self._config.gripper.y_offset + head_y_offset

  def _position_or_none(self, axis: Axis) -> Optional[float]:
    try:
      return float(self._ctrl.get_position(axis))
    except Exception:
      return None

  def _gripper_is_open(self) -> bool:
    g_pos = self._position_or_none("g")
    return g_pos is not None and abs(g_pos - OPEN_GRIPPER_POSITION) <= _GRIPPER_OPEN_TOLERANCE_MM

  def _snapshot(self) -> Dict[str, Any]:
    """Return the axis positions and telemetry a pickup-verification check reads.

    Telemetry is always empty here: only a controller-specific rich state
    read could populate it, and this reads only the standard interface's
    ``get_position``. Pickup verification depends solely on the G-axis
    position, which this always reports correctly.
    """
    return {
      "positions": {
        "X": self._position_or_none("x"),
        "Y": self._position_or_none("y"),
        "Z": self._position_or_none("z"),
        "Zg": self._position_or_none("zg"),
        "G": self._position_or_none("g"),
      },
      "telemetry": {},
    }

  def _live_snapshot(self, *, force_refresh: bool = False) -> Dict[str, Any]:
    return self._snapshot()

  def _fmt_snapshot(self, values: Dict[str, Any]) -> str:
    parts = []
    for axis in ("X", "Y", "Z", "Zg", "G"):
      value = values[axis]
      parts.append(f"{axis}={value:.3f}" if value is not None else f"{axis}=n/a")
    return " ".join(parts)

  def _fmt_telemetry(self, telemetry: Dict[str, Any]) -> str:
    parts: List[str] = []
    for axis in ("X", "Y", "Z", "W", "G", "Zg"):
      axis_data = telemetry.get(axis)
      if not axis_data:
        continue
      axis_parts: List[str] = []
      for key in (
        "measured_current",
        "peak_current",
        "last_peak_current_percent",
        "last_force_percent",
        "current_position_error",
        "position_error_max",
        "velocity_limit",
        "acceleration_limit",
      ):
        value = axis_data.get(key)
        if isinstance(value, (int, float)):
          axis_parts.append(f"{key}={float(value):.3f}")
      for key in ("enabled", "initialized", "is_moving"):
        value = axis_data.get(key)
        if isinstance(value, bool):
          axis_parts.append(f"{key}={str(value).lower()}")
      last_command = axis_data.get("last_command")
      if isinstance(last_command, dict):
        mode = last_command.get("mode")
        if isinstance(mode, str):
          axis_parts.append(f"cmd={mode}")
        for key in ("position", "target_position"):
          value = last_command.get(key)
          if isinstance(value, (int, float)):
            axis_parts.append(f"{key}={float(value):.3f}")
      if axis_parts:
        parts.append(f"{axis}[{' '.join(axis_parts)}]")
    return " ".join(parts)

  def _log_step(self, name: str, *, targets: Optional[Dict[str, float]] = None) -> None:
    snapshot = self._snapshot()
    positions = snapshot["positions"]
    telemetry = snapshot["telemetry"]
    self._live_status = {
      "task": "pick_place",
      "step": name,
      "from_location": self._from_location,
      "to_location": self._to_location,
      "labware": self._source_labware.metadata or {"name": self._source_labware.name},
      "positions": positions,
      "telemetry": telemetry,
      "targets": dict(targets or {}),
    }
    logger.info("PickPlace %s current=%s", name, self._fmt_snapshot(positions))
    telemetry_text = self._fmt_telemetry(telemetry)
    if telemetry_text:
      logger.info("PickPlace %s telemetry=%s", name, telemetry_text)
    if targets:
      target_text = " ".join(f"{axis}={value:.3f}" for axis, value in targets.items())
      logger.info("PickPlace %s target=%s", name, target_text)

  def status_payload(self) -> dict:
    """Return the task's live status, merging in any pending operator prompt."""
    payload = dict(self._live_status)
    # Merges the base-class operator_prompt (set either by a step or
    # synthesized by the engine on failure) so any step failure in a
    # PickPlace surfaces the generic Retry/Ignore/Abort modal, even if
    # the step didn't build one into _live_status itself.
    base = super().status_payload()
    if "operator_prompt" not in payload and base.get("operator_prompt"):
      payload["operator_prompt"] = base["operator_prompt"]
    return payload

  def _axis_telemetry_value(
    self, telemetry: Dict[str, Any], axis: str, key: str
  ) -> Optional[float]:
    axis_data = telemetry.get(axis)
    if not isinstance(axis_data, dict):
      return None
    value = axis_data.get(key)
    if isinstance(value, (int, float)):
      return float(value)
    return None

  def _verify_plate_pickup(
    self,
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
  ) -> Tuple[bool, Dict[str, object]]:
    grip_position_after = after_snapshot["positions"].get("G")
    g_rule_failed = (
      isinstance(grip_position_after, (int, float))
      and float(grip_position_after) >= _PICKUP_FAILURE_G_THRESHOLD_MM
    )
    sensor_detected: Optional[bool] = None
    if not bool(self._config.safety.ignore_plate_sensor):
      try:
        sensor_detected = bool(self._ctrl.is_plate_in_gripper())
      except Exception as exc:
        logger.debug("Plate-present sensor unavailable during pick verification: %s", exc)

    measured_before = self._axis_telemetry_value(
      before_snapshot["telemetry"], "G", "measured_current"
    )
    measured_after = self._axis_telemetry_value(
      after_snapshot["telemetry"], "G", "measured_current"
    )
    peak_after = self._axis_telemetry_value(
      after_snapshot["telemetry"], "G", "last_peak_current_percent"
    )
    force_after = self._axis_telemetry_value(after_snapshot["telemetry"], "G", "last_force_percent")

    current_delta = None
    if measured_before is not None and measured_after is not None:
      current_delta = abs(measured_after - measured_before)

    current_detected = any(
      (
        peak_after is not None and peak_after >= 0.05,
        force_after is not None and force_after >= 5.0,
        current_delta is not None and current_delta >= 0.02,
      )
    )
    success = not g_rule_failed
    details = {
      "sensor_detected": sensor_detected,
      "current_detected": current_detected,
      "g_rule_failed": g_rule_failed,
      "grip_position_after": grip_position_after,
      "measured_current_before": measured_before,
      "measured_current_after": measured_after,
      "current_delta": current_delta,
      "peak_current_after": peak_after,
      "force_percent_after": force_after,
      "attempt": self._grip_attempts,
    }
    return success, details

  def on_error_action(self, action: ErrorAction) -> None:
    """Apply the operator's choice for the step that just failed."""
    if (
      action == ErrorAction.IGNORE
      and self.error is not None
      and self.error.step_name == "grip_plate"
    ):
      self._force_continue_after_pickup_failure = True
      self._plate_pick_verified = True
      self._live_status.update(
        {
          "pickup_verification": {
            **dict(self._live_status.get("pickup_verification") or {}),
            "forced_continue": True,
          },
          "operator_prompt": None,
        }
      )
    elif action == ErrorAction.RETRY:
      self._force_continue_after_pickup_failure = False
      self._plate_pick_verified = False
    elif action == ErrorAction.ABORT:
      self._force_continue_after_pickup_failure = False

  def _log_plan(self) -> None:
    plan = self.debug_plan()
    logger.info(
      "PickPlace plan from=%d to=%d head=%s teach_tip_capacity=%.1f teach_tip_length=%.3f "
      "labware=%s plate_height=%.3f stack_height=%.3f gripper_offset=%.3f "
      "source_teach_z=%.3f source_top_z=%.3f source_grip_plane_z=%.3f "
      "dest_teach_z=%.3f dest_top_z=%.3f",
      plan["from_location"],
      plan["to_location"],
      plan["head_type"],
      plan["teach_tip_capacity"],
      plan["teach_tip_length_mm"],
      plan["labware_name"],
      plan["plate_height_mm"],
      plan["stack_height_mm"],
      plan["gripper_offset_mm"],
      plan["source_teach_z"],
      plan["source_top_z"],
      plan["source_grip_plane_z"],
      plan["dest_teach_z"],
      plan["dest_top_z"],
    )
    logger.info(
      "PickPlace solved pick(Z=%.3f,Zg=%.3f) carry(Z=%.3f,Zg=%.3f) place(Z=%.3f,Zg=%.3f)",
      self._positions.pick_z,
      self._positions.pick_zg,
      self._positions.carry_z,
      self._positions.carry_zg,
      self._positions.place_z,
      self._positions.place_zg,
    )

  def debug_plan(self) -> Dict[str, Union[float, int, str]]:
    """Return the geometry inputs and solved targets behind this task's plan.

    Returns:
      A dict of every measurement and intermediate value
      :meth:`_calculate_positions` used, for diagnostics.
    """
    tip_length = self._tip_length_for_pick_place()
    source_tp_z = self._tp.get_teachpoint(self._from_location, "z")
    dest_tp_z = self._tp.get_teachpoint(self._to_location, "z")
    source_complete_height = self._current_complete_height(self._from_location)
    dest_complete_height = self._current_complete_height(self._to_location)
    source_support_height = self._source_pick_support_height()
    dest_support_height = self._destination_place_support_height()
    return {
      "from_location": self._from_location,
      "to_location": self._to_location,
      "head_type": self._config.head.head_type,
      "teach_tip_id": str(self._config.head.teach_tip_id or ""),
      "teach_tip_capacity": float(self._config.head.teach_tip_capacity or 0.0),
      "teach_tip_length_mm": tip_length,
      "labware_name": self._source_labware.name,
      "plate_height_mm": self._source_labware.height,
      "stack_height_mm": self._source_labware.stack_height,
      # The gripper engages _engage_plate's flanges -- for an ordinary
      # pickup this IS _source_labware, but for a mounted-group pickup
      # the engage plate is the BOTTOM of the group and its
      # gripper_offset is what drives pick_z. Logs both so mount-pair
      # diagnostics are readable.
      "gripper_offset_mm": self._engage_plate.gripper_offset,
      "engage_plate_name": self._engage_plate.name,
      "mounted_group_size": len(self._deck.get_stack(self._from_location).mounted_group_from_top()),
      "source_pick_height_mm": source_complete_height,
      "dest_stack_height_mm": dest_complete_height,
      "source_support_height_mm": source_support_height,
      "dest_support_height_mm": dest_support_height,
      "source_teach_z": source_tp_z,
      "source_top_z": source_tp_z - source_complete_height,
      # Gripper offset semantics are bottom-up: it is the distance from
      # the labware bottom up to the gripper contact plane.
      "source_grip_plane_z": source_tp_z
      - (source_support_height + self._engage_plate.gripper_offset),
      "dest_teach_z": dest_tp_z,
      "dest_top_z": dest_tp_z - dest_complete_height,
      "pick_z": self._positions.pick_z,
      "pick_zg": self._positions.pick_zg,
      "carry_z": self._positions.carry_z,
      "carry_zg": self._positions.carry_zg,
      "place_z": self._positions.place_z,
      "place_zg": self._positions.place_zg,
    }

  def _axis_range(self, axis: Axis) -> Tuple[float, float]:
    cfg = self._config.axes.get(axis)
    if cfg is None:
      raise RuntimeError(f"Missing axis config for {axis_display_name(axis)}")
    return cfg.range.min_pos, cfg.range.max_pos

  def _clamp(self, value: float, axis: Axis) -> float:
    min_pos, max_pos = self._axis_range(axis)
    return max(min_pos, min(max_pos, value))

  def _get_current_z(self) -> float:
    z_min, _ = self._axis_range("z")
    try:
      current = self._ctrl.get_position("z")
    except Exception:
      current = z_min
    return max(current, z_min)

  def _tip_length_for_pick_place(self) -> float:
    head_type = self._config.head.head_type
    stored_length = self._config.head.teach_tip_length_mm
    if stored_length is not None:
      return float(stored_length)
    # Pick/place solves against the default taught tip reference for the
    # active head, not against the currently-mounted physical tips.
    if head_type_is_fixed(head_type):
      return 35.78 + 26.1
    if head_type_is_assaymap(head_type):
      return 4.71
    default_capacity = float(
      self._config.head.teach_tip_capacity or self._config.head.default_tip_capacity or 0.0
    )
    tip_ref = self._config.head.teach_tip_id or self._config.head.default_tip_id or default_capacity
    tip_length = get_tip_length_mm(head_type, tip_ref)
    if tip_length is None:
      raise RuntimeError(f"Teach tip length is not configured for {head_type} with {tip_ref}.")
    return tip_length

  def _gripper_pad_reference_zg(self, tip_length: float) -> float:
    """Return the Zg at which the gripper bottom sits in this location's plate-pad plane.

    The configuration stores one bench measurement -- Zg when the pad is
    touching, and the length of the tip that was installed for it. Any
    other taught tip shifts that reference by the length delta, keeping
    the same physical plane. The two calibration values are a pair;
    neither is meaningful on its own, which is why they are not derived
    from the head's current tip.
    """
    g = self._config.gripper
    return g.pad_zg_reference_mm + (tip_length - g.pad_reference_tip_length_mm)

  def _solve_pick_or_place(
    self, location: int, stack_height: float, gripper_offset: float
  ) -> Tuple[float, float]:
    z_min, _ = self._axis_range("z")
    _, zg_max = self._axis_range("zg")
    zg_max = min(zg_max, _PLATE_HANDLING_ZG_MAX)
    z_current = self._get_current_z()
    z_teachpoint = self._tp.get_teachpoint(location, "z")
    tip_length = self._tip_length_for_pick_place()
    if head_type_is_disposable(self._config.head.head_type):
      new_zg = (
        z_teachpoint
        - z_current
        + self._gripper_pad_reference_zg(tip_length)
        - gripper_offset
        - stack_height
      )
    else:
      new_zg = (
        z_teachpoint
        - z_current
        + tip_length
        - GRIPPER_THICKNESS
        - GRIPPER_TO_BASE_OF_HEAD_GAP
        - gripper_offset
        - stack_height
        + _LENGTH_DIFFERENCE_96_TO_384
      )
    safe_zg = self._clamp(self._config.axes["zg"].range.min_pos, "zg")

    if new_zg > zg_max:
      z = z_current + new_zg - zg_max
      zg = zg_max
    elif new_zg < safe_zg:
      z = z_current + new_zg - safe_zg
      if z < z_min and (safe_zg + z) >= safe_zg:
        safe_zg += z
        z = z_min
      zg = safe_zg
    else:
      z = z_current
      zg = new_zg

    return self._clamp(z, "z"), self._clamp(zg, "zg")

  def _safe_carry_zg(self, labware: Labware) -> float:
    safe = 5.0 + labware.height - labware.gripper_offset - GRIPPER_THICKNESS
    return self._clamp(safe, "zg")

  def _head_protrusion_below_head(self) -> float:
    # Mirrors behavior for gripper moves with no tips mounted.
    return _NO_TIPS_HEAD_PROTRUSION_MM

  def _adjust_for_head_clearance(
    self,
    safe_z: float,
    safe_zg: float,
    labware: Labware,
    *,
    effective_height: Optional[float] = None,
  ) -> Tuple[float, float]:
    # ``effective_height`` lets callers override labware.height when the
    # carried assembly is taller than a single plate -- notably a
    # mounted group where the gripper engages the bottom plate but a
    # whole second plate rides on top. Defaults to labware.height so
    # existing callers (Delid/Relid/single-plate pickups) keep their
    # prior behavior.
    carried_top_height = labware.height if effective_height is None else float(effective_height)
    interference = (
      carried_top_height
      + self._head_protrusion_below_head()
      + Z_CLEARANCE
      - safe_zg
      - labware.gripper_offset
      - GRIPPER_THICKNESS
    )
    if interference <= 0:
      return self._clamp(safe_z, "z"), self._clamp(safe_zg, "zg")

    z_min, _ = self._axis_range("z")
    z_and_zg = safe_z + safe_zg
    adjusted_z = safe_z - interference
    if adjusted_z < z_min:
      adjusted_z = z_min
    adjusted_zg = z_and_zg - adjusted_z
    return self._clamp(adjusted_z, "z"), self._clamp(adjusted_zg, "zg")

  def _current_location_height(self, location: int) -> float:
    return self._deck.get_location_height(location)

  def _current_complete_height(self, location: int) -> float:
    return self._deck.get_height(location)

  def _source_pick_support_height(self) -> float:
    # For a mounted-group pickup, the gripper engages the BOTTOM of the
    # group, so "support" is whatever is below that bottom plate -- not
    # what's below the visible top. Degrades to get_location_height for
    # ordinary single-plate pickups.
    return self._deck.get_stack(self._from_location).get_support_height_below_group()

  def _carried_assembly_effective_height(self) -> float:
    """Return the distance from the engage plate's base to the top of the carried assembly.

    This is what drives head-clearance during carry. For a single-plate
    pickup: just the engage plate's own ``height``. For a mounted group:
    the engage plate contributes its ``stack_height`` (how much it
    supports the next level), every mid-layer contributes its
    ``stack_height``, and the topmost plate contributes its full
    ``height``. Matches the geometry the plate stack would present to
    the head as it travels.
    """
    stack = self._deck.get_stack(self._from_location)
    group = stack.mounted_group_from_top()
    if len(group) <= 1:
      return self._engage_plate.height
    # group is top-first; iterate bottom-to-top so the final (top) plate
    # contributes its full height, everything else its
    # stacking-surface-to-next-layer height.
    reversed_group = list(reversed(group))
    total = 0.0
    for i, plate in enumerate(reversed_group):
      if i == len(reversed_group) - 1:
        total += float(plate.height)
      else:
        total += float(plate.stack_height or plate.height)
    return total

  def _destination_place_support_height(self) -> float:
    return self._deck.get_stacking_height(self._to_location)

  def _obstacle_height_between_locations(self) -> float:
    region = DeckLayout.get_region([self._from_location], [self._to_location])
    blockers = [loc for loc in region if loc not in {self._from_location, self._to_location}]
    if not blockers:
      return 0.0
    return max(self._deck.get_height(loc) for loc in blockers)

  def _calculate_positions(self) -> PickPlacePositions:
    source_height = self._source_pick_support_height()
    dest_height = self._destination_place_support_height()
    final_place_height = dest_height
    # All pick/place/carry Z positions are computed relative to the plate
    # the gripper actually grips (_engage_plate). For ordinary stacks
    # this is the top plate; for a mounted pair it's the bottom plate of
    # the locked group. Using the engage plate's gripper_offset puts the
    # fingers at the right flange height regardless of how tall the
    # mounted group above it is.
    engage_offset = self._engage_plate.gripper_offset
    pick_z, pick_zg = self._solve_pick_or_place(
      self._from_location,
      source_height,
      engage_offset,
    )
    place_z, place_zg = self._solve_pick_or_place(
      self._to_location,
      final_place_height,
      engage_offset,
    )

    obstacle_height = self._obstacle_height_between_locations()
    carry_stack = max(source_height, final_place_height, obstacle_height) + Z_CLEARANCE
    carry_z, carry_zg = self._solve_pick_or_place(
      self._from_location,
      carry_stack,
      engage_offset,
    )
    carry_z = self._clamp(carry_z, "z")
    carry_zg = self._clamp(carry_zg, "zg")

    # For a mounted-pair carry, the gripper engages the bottom plate but
    # a whole second plate rides on top -- passes the combined group
    # height so the head-clearance check accounts for the full assembly
    # instead of just the engage plate.
    carry_z, carry_zg = self._adjust_for_head_clearance(
      carry_z,
      carry_zg,
      self._engage_plate,
      effective_height=self._carried_assembly_effective_height(),
    )

    return PickPlacePositions(
      pick_z=pick_z,
      pick_zg=pick_zg,
      carry_z=carry_z,
      carry_zg=carry_zg,
      place_z=place_z,
      place_zg=place_zg,
    )

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("move_to_safe_pick_start", self._move_to_safe_pick_start),
      ("move_gripper_to_nesting", self._move_gripper_to_nesting),
      ("move_xy_to_pick", self._move_xy_to_pick),
      ("move_to_pick_height", self._move_to_pick_height),
      ("grip_plate", self._grip_plate),
      ("move_to_carry_height", self._move_to_carry_height),
      ("move_xy_to_place", self._move_xy_to_place),
      ("move_to_place_height", self._move_to_place_height),
      ("release_plate", self._release_plate),
      ("return_gripper_to_nesting", self._return_gripper_to_nesting),
    ]

  async def _move_to_safe_pick_start(self) -> None:
    self._log_step("move_to_safe_pick_start", targets={"Z": self._config.safety.z_safe_position})
    await asyncio.to_thread(
      self._ctrl.move,
      [self._move_info("z", self._config.safety.z_safe_position)],
      True,
    )
    if self._gripper_is_open():
      self._log_step(
        "move_to_safe_pick_start_open_gripper_skipped", targets={"G": OPEN_GRIPPER_POSITION}
      )
    else:
      self._log_step("move_to_safe_pick_start_open_gripper", targets={"G": OPEN_GRIPPER_POSITION})
      await asyncio.to_thread(self._ctrl.open_gripper)
    self._log_step("move_to_safe_pick_start_complete")

  async def _move_gripper_to_nesting(self) -> None:
    self._log_step("move_gripper_to_nesting", targets={"Zg": _GRIPPER_RECESS_DEPTH})
    await asyncio.to_thread(
      self._ctrl.move,
      [self._move_info("zg", _GRIPPER_RECESS_DEPTH)],
      True,
    )
    self._log_step("move_gripper_to_nesting_complete")

  async def _move_xy_to_pick(self) -> None:
    x = self._tp.get_teachpoint(self._from_location, "x")
    y = self._tp.get_teachpoint(self._from_location, "y") + self._gripper_y_offset()
    self._log_step("move_xy_to_pick", targets={"X": x, "Y": y})
    await asyncio.to_thread(
      self._ctrl.move,
      [
        self._move_info("x", x),
        self._move_info("y", y),
      ],
      True,
    )
    self._log_step("move_xy_to_pick_complete")

  async def _move_to_pick_height(self) -> None:
    self._log_step(
      "move_to_pick_height",
      targets={"Z": self._positions.pick_z, "Zg": self._positions.pick_zg},
    )
    moves = [
      self._move_info("z", self._positions.pick_z),
      self._move_info("zg", self._positions.pick_zg),
    ]
    await asyncio.to_thread(self._ctrl.move, moves, True)
    self._log_step("move_to_pick_height_complete")

  async def _grip_plate(self) -> None:
    grip_speed = self._speed if self._speed != "slow" else "med"
    if self._grip_attempts > 0:
      self._log_step("reopen_gripper_for_retry", targets={"G": OPEN_GRIPPER_POSITION})
      await asyncio.to_thread(self._ctrl.open_gripper)
    before_snapshot = self._snapshot()
    self._plate_pick_verified = False
    self._log_step("grip_plate", targets={"G": _PICK_PLACE_GRIP_TARGET})
    await asyncio.to_thread(self._ctrl.grip, grip_speed, _PICK_PLACE_GRIP_TARGET)
    self._grip_attempts += 1
    after_snapshot = self._snapshot()
    verified, verification = self._verify_plate_pickup(before_snapshot, after_snapshot)
    if not verified:
      before_snapshot = self._live_snapshot(force_refresh=False)
      after_snapshot = self._live_snapshot(force_refresh=True)
      verified, verification = self._verify_plate_pickup(before_snapshot, after_snapshot)
    self._live_status.update(
      {
        "pickup_verification": verification,
        "operator_prompt": None
        if verified
        else {
          "kind": "pickup_verification_failed",
          "title": "Plate pickup not detected",
          "message": (
            "The gripper closed, but the post-grip G position indicates the plate was not "
            "picked up."
          ),
          "choices": ["retry", "ignore", "abort"],
        },
      }
    )
    if not verified:
      raise RuntimeError("Plate pickup not detected after gripper close")
    self._plate_pick_verified = True
    self._force_continue_after_pickup_failure = False
    self._log_step("grip_plate_complete")

  async def _move_to_carry_height(self) -> None:
    self._log_step(
      "move_to_carry_height",
      targets={"Z": self._positions.carry_z, "Zg": self._positions.carry_zg},
    )
    await asyncio.to_thread(
      self._ctrl.move,
      [
        self._move_info("z", self._positions.carry_z),
        self._move_info("zg", self._positions.carry_zg),
      ],
      True,
    )
    self._log_step("move_to_carry_height_complete")

  async def _move_xy_to_place(self) -> None:
    if not self._plate_pick_verified and not self._force_continue_after_pickup_failure:
      self._log_step("move_xy_to_place_skipped_missing_plate")
      return
    x = self._tp.get_teachpoint(self._to_location, "x")
    y = self._tp.get_teachpoint(self._to_location, "y") + self._gripper_y_offset()
    self._log_step("move_xy_to_place", targets={"X": x, "Y": y})
    await asyncio.to_thread(
      self._ctrl.move,
      [
        self._move_info("x", x),
        self._move_info("y", y),
      ],
      True,
    )
    self._log_step("move_xy_to_place_complete")

  async def _move_to_place_height(self) -> None:
    if not self._plate_pick_verified and not self._force_continue_after_pickup_failure:
      self._log_step("move_to_place_height_skipped_missing_plate")
      return
    current_z = await asyncio.to_thread(self._ctrl.get_position, "z")
    current_zg = await asyncio.to_thread(self._ctrl.get_position, "zg")
    target_z = self._positions.place_z
    target_zg = self._positions.place_zg
    z_min, _ = self._axis_range("z")
    move_z_first = (current_z - target_z) > (z_min - AXIS_EPSILON) and (current_zg - target_zg) < 0
    self._log_step("move_to_place_height", targets={"Z": target_z, "Zg": target_zg})
    if abs(target_z - z_min) <= AXIS_EPSILON or move_z_first:
      await asyncio.to_thread(self._ctrl.move, [self._move_info("z", target_z)], True)
    await asyncio.to_thread(
      self._ctrl.move,
      [
        self._move_info("z", target_z),
        self._move_info("zg", target_zg),
      ],
      True,
    )
    self._log_step("move_to_place_height_complete")

  async def _release_plate(self) -> None:
    if not self._plate_pick_verified and not self._force_continue_after_pickup_failure:
      self._log_step("release_plate_skipped_missing_plate", targets={"G": OPEN_GRIPPER_POSITION})
      await asyncio.to_thread(self._ctrl.open_gripper)
      self._log_step("release_plate_skipped_missing_plate_complete")
      return
    self._log_step("release_plate", targets={"G": OPEN_GRIPPER_POSITION})
    await asyncio.to_thread(self._ctrl.open_gripper)
    self._log_step("release_plate_complete")
    # DeckState update respects mount semantics: if the top plate at the
    # source was flagged ``is_mounted``, the physically locked plate
    # beneath it moves with us. For a plain stack this degrades to a
    # single-item move (identical to a single-remove/single-add).
    group = self._deck.remove_mounted_group(self._from_location)
    self._deck.add_mounted_group(self._to_location, group)
    # Diagnostic only on multi-plate moves. Logged directly via
    # logger.info rather than _log_step because the latter expects
    # targets to be axis-number pairs (it formats every value with
    # :.3f) and any non-numeric field there crashes the step.
    if len(group) > 1:
      logger.info(
        "PickPlace mounted-group move: %d plates from %d->%d (%s)",
        len(group),
        self._from_location,
        self._to_location,
        ", ".join(lw.name for lw in group),
      )

  async def _return_gripper_to_nesting(self) -> None:
    self._log_step("return_gripper_to_nesting", targets={"Zg": _GRIPPER_RECESS_DEPTH})
    await asyncio.to_thread(
      self._ctrl.move,
      [self._move_info("zg", _GRIPPER_RECESS_DEPTH)],
      True,
    )
    self._log_step("return_gripper_to_nesting_complete")


class GripperTeachMoveTask(PickPlaceTask):
  """Position the gripper over a location so its Y alignment can be judged.

  Subclasses :class:`PickPlaceTask` deliberately: the Y offset, grip plane,
  and Z/Zg solve are the same arithmetic a real pick uses, so teaching
  against a different calculation would calibrate against something the
  robot never does. This runs only the approach half of a pick -- it
  positions and stops. It never closes the gripper, so no plate is lifted.

  ``approach_height`` backs the gripper off above the grip plane. Zg extends
  downward, so clearance means a smaller Zg.
  """

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    deck: DeckState,
    location: int,
    approach_height: float = 0.0,
    speed: SpeedLevel = "med",
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to move against.
      deck: The deck state, read to find the plate to align over.
      location: The deck location to teach.
      approach_height: Millimetres to stop above the grip plane.
      speed: The speed profile for XYZ/Zg motion.
    """
    # from == to: only ever runs the pick-side steps.
    PickPlaceTask.__init__(
      self,
      controller,
      teachpoints,
      config,
      deck,
      from_location=location,
      to_location=location,
      speed=speed,
    )
    self.name = f"GripperTeachMove_{location}"
    self._approach_height = max(0.0, float(approach_height or 0.0))

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("move_to_safe_pick_start", self._move_to_safe_pick_start),
      ("move_gripper_to_nesting", self._move_gripper_to_nesting),
      ("move_xy_to_pick", self._move_xy_to_pick),
      ("move_to_teach_height", self._move_to_teach_height),
    ]

  async def _move_to_teach_height(self) -> None:
    """Descend to the grip plane, held short by ``approach_height``."""
    zg = self._positions.pick_zg - self._approach_height
    self._log_step(
      "move_to_teach_height",
      targets={"Z": self._positions.pick_z, "Zg": zg},
    )
    logger.info(
      "Gripper teach: location %d at Z=%.3f Zg=%.3f (grip plane %.3f, "
      "clearance %.2f mm), gripper Y offset in use %.3f mm",
      self._from_location,
      self._positions.pick_z,
      zg,
      self._positions.pick_zg,
      self._approach_height,
      self._gripper_y_offset(),
    )
    await asyncio.to_thread(
      self._ctrl.move,
      [
        self._move_info("z", self._positions.pick_z),
        self._move_info("zg", zg),
      ],
      True,
    )
    self._log_step("move_to_teach_height_complete")


class DelidPlateTask(PickPlaceTask):
  """Remove a lid from a lidded plate and place that lid at another location."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    deck: DeckState,
    plate_location: int,
    lid_destination: int,
    speed: SpeedLevel = "med",
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to move against.
      deck: The deck state, updated on a successful delid.
      plate_location: The deck location of the lidded plate.
      lid_destination: The deck location to place the removed lid at.
      speed: The speed profile for XYZ/Zg motion.

    Raises:
      RuntimeError: If the plate at ``plate_location`` has no lid.
    """
    StateMachineTask.__init__(self, f"DelidPlate_{plate_location}_{lid_destination}")
    self._ctrl = controller
    self._tp = teachpoints
    self._config = config
    self._deck = deck
    self._from_location = plate_location
    self._to_location = lid_destination
    self._speed = speed
    self._live_status: Dict[str, Any] = {}
    self._grip_attempts = 0
    self._plate_pick_verified = False
    self._force_continue_after_pickup_failure = False
    self._source_plate = self._get_source_labware()
    if not self._source_plate.is_lidded:
      raise RuntimeError(f"No lid is present on the plate at location {plate_location}")
    self._source_labware = self._build_lid_labware(self._source_plate)
    self._engage_plate = self._source_labware
    self._lid_gripper_offset = self._resolve_lid_gripper_offset(self._source_plate)
    self._pick_gripper_offset = self._lid_gripper_offset + self._lid_resting_height(
      self._source_plate
    )
    self._place_gripper_offset = self._lid_gripper_offset
    self._positions = self._calculate_positions()
    self._log_plan()

  @staticmethod
  def _lid_resting_height(plate: Labware) -> float:
    return float((plate.metadata or {}).get("lid_resting_height_mm") or 0.0)

  @staticmethod
  def _lid_height(plate: Labware) -> float:
    return lid_thickness_mm(plate.metadata)

  @classmethod
  def _resolve_lid_gripper_offset(cls, plate: Labware) -> float:
    return lid_gripper_offset_mm(
      plate.metadata,
      fallback_gripper_offset_mm=float(plate.gripper_offset or 0.0),
      label=plate.name,
    )

  def _build_lid_labware(self, plate: Labware) -> Labware:
    lid = synthesize_lid_labware(plate)
    lid.metadata["lid_height_mm"] = lid.height
    return lid

  def _build_unlidded_plate(self) -> Labware:
    metadata = dict(self._source_plate.metadata or {})
    metadata["is_lidded"] = False
    metadata.pop("generated_lid", None)
    height = float(
      metadata.get("base_height_mm") or metadata.get("height_mm") or self._source_plate.height
    )
    metadata["height_mm"] = height
    metadata["total_height_mm"] = height
    stack_height = float(metadata.get("stack_height_mm") or height)
    return Labware(
      id=self._source_plate.id,
      definition_id=self._source_plate.definition_id,
      name=self._source_plate.name,
      height=height,
      width=self._source_plate.width,
      length=self._source_plate.length,
      labware_type=self._source_plate.labware_type,
      gripper_offset=self._source_plate.gripper_offset,
      stack_height=stack_height,
      is_lidded=False,
      is_sealed=self._source_plate.is_sealed,
      wells=self._source_plate.wells,
      metadata=metadata,
    )

  def _calculate_positions(self) -> PickPlacePositions:
    source_support_height = self._source_pick_support_height()
    destination_support_height = self._destination_place_support_height()

    pick_z, pick_zg = self._solve_pick_or_place(
      self._from_location,
      source_support_height,
      self._pick_gripper_offset,
    )
    place_z, place_zg = self._solve_pick_or_place(
      self._to_location,
      destination_support_height,
      self._place_gripper_offset,
    )

    obstacle_height = self._obstacle_height_between_locations()
    carry_stack = (
      max(
        self._current_complete_height(self._from_location),
        destination_support_height,
        obstacle_height,
      )
      + Z_CLEARANCE
    )
    carry_z, carry_zg = self._solve_pick_or_place(
      self._from_location,
      carry_stack,
      self._place_gripper_offset,
    )
    carry_z = self._clamp(carry_z, "z")
    carry_zg = self._clamp(carry_zg, "zg")
    carry_z, carry_zg = self._adjust_for_head_clearance(carry_z, carry_zg, self._source_labware)

    return PickPlacePositions(
      pick_z=pick_z,
      pick_zg=pick_zg,
      carry_z=carry_z,
      carry_zg=carry_zg,
      place_z=place_z,
      place_zg=place_zg,
    )

  async def _grip_plate(self) -> None:
    grip_speed = self._speed if self._speed != "slow" else "med"
    if self._grip_attempts > 0:
      self._log_step("reopen_gripper_for_retry", targets={"G": OPEN_GRIPPER_POSITION})
      await asyncio.to_thread(self._ctrl.open_gripper)
    before_snapshot = self._snapshot()
    self._plate_pick_verified = False
    self._log_step("grip_lid", targets={"G": _PICK_PLACE_GRIP_TARGET})
    await asyncio.to_thread(self._ctrl.grip, grip_speed, _PICK_PLACE_GRIP_TARGET, True)
    self._grip_attempts += 1
    after_snapshot = self._snapshot()
    verified, verification = self._verify_plate_pickup(before_snapshot, after_snapshot)
    if not verified:
      before_snapshot = self._live_snapshot(force_refresh=False)
      after_snapshot = self._live_snapshot(force_refresh=True)
      verified, verification = self._verify_plate_pickup(before_snapshot, after_snapshot)
    self._live_status.update(
      {
        "pickup_verification": verification,
        "operator_prompt": None
        if verified
        else {
          "kind": "pickup_verification_failed",
          "title": "Lid pickup not detected",
          "message": (
            "The gripper closed, but the post-grip G position indicates the lid was not picked up."
          ),
          "choices": ["retry", "ignore", "abort"],
        },
      }
    )
    if not verified:
      raise RuntimeError("Lid pickup not detected after gripper close")
    self._plate_pick_verified = True
    self._force_continue_after_pickup_failure = False
    self._log_step("grip_lid_complete")

  async def _release_plate(self) -> None:
    if not self._plate_pick_verified and not self._force_continue_after_pickup_failure:
      self._log_step("release_lid_skipped_missing_lid", targets={"G": OPEN_GRIPPER_POSITION})
      await asyncio.to_thread(self._ctrl.open_gripper)
      self._log_step("release_lid_skipped_missing_lid_complete")
      return
    self._log_step("release_lid", targets={"G": OPEN_GRIPPER_POSITION})
    await asyncio.to_thread(self._ctrl.open_gripper)
    self._log_step("release_lid_complete")
    removed = self._deck.remove(self._from_location)
    if removed is not self._source_plate:
      logger.warning("Delid source stack changed during task; using runtime top labware state")
    self._deck.add(self._from_location, self._build_unlidded_plate())
    self._deck.add(self._to_location, self._build_lid_labware(self._source_plate))


class RelidPlateTask(PickPlaceTask):
  """Pick up a standalone lid and place it back onto a compatible plate."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    deck: DeckState,
    lid_location: int,
    plate_location: int,
    speed: SpeedLevel = "med",
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to move against.
      deck: The deck state, updated on a successful relid.
      lid_location: The deck location of the standalone lid.
      plate_location: The deck location of the plate to relid.
      speed: The speed profile for XYZ/Zg motion.

    Raises:
      RuntimeError: If the source is not a standalone lid, the destination
        already has a lid, is sealed, or does not support a lid.
    """
    StateMachineTask.__init__(self, f"RelidPlate_{lid_location}_{plate_location}")
    self._ctrl = controller
    self._tp = teachpoints
    self._config = config
    self._deck = deck
    self._from_location = lid_location
    self._to_location = plate_location
    self._speed = speed
    self._live_status: Dict[str, Any] = {}
    self._grip_attempts = 0
    self._plate_pick_verified = False
    self._force_continue_after_pickup_failure = False
    self._source_lid = self._get_source_labware()
    self._destination_plate = self._get_destination_plate()
    if not self._is_lid(self._source_lid):
      raise RuntimeError(f"No standalone lid is present at location {lid_location}")
    if self._destination_plate.is_lidded:
      raise RuntimeError(f"Plate at location {plate_location} already has a lid")
    if self._destination_plate.is_sealed:
      raise RuntimeError(f"Plate at location {plate_location} is sealed and cannot be relidded")
    can_have_lid = bool((self._destination_plate.metadata or {}).get("can_have_lid", False))
    if not can_have_lid:
      raise RuntimeError(
        f"Labware at location {plate_location} does not support lids: "
        f"{self._destination_plate.name}"
      )
    self._source_labware = self._source_lid
    self._engage_plate = self._source_lid
    self._pick_gripper_offset = max(0.0, float(self._source_lid.gripper_offset or 0.0))
    self._place_gripper_offset = DelidPlateTask._resolve_lid_gripper_offset(
      self._destination_plate
    ) + DelidPlateTask._lid_resting_height(self._destination_plate)
    self._positions = self._calculate_positions()
    self._log_plan()

  @staticmethod
  def _is_lid(labware: Labware) -> bool:
    metadata = labware.metadata or {}
    base_class = str(metadata.get("base_class") or "").lower()
    kind = str(metadata.get("kind") or labware.labware_type or "").lower()
    return base_class == "lid" or kind == "lid"

  def _get_destination_plate(self) -> Labware:
    top = self._deck.get_stack(self._to_location).top
    if top is None:
      raise RuntimeError(f"No labware is present at location {self._to_location}")
    if self._is_lid(top):
      raise RuntimeError(
        f"Destination location {self._to_location} must contain a plate, not a lid"
      )
    return top

  def _destination_place_support_height(self) -> float:
    # Relid geometry places relative to the visible top plate at the
    # destination, then adds lid_resting_height into the gripper offset.
    return self._current_location_height(self._to_location)

  def _calculate_positions(self) -> PickPlacePositions:
    source_support_height = self._source_pick_support_height()
    destination_support_height = self._destination_place_support_height()

    pick_z, pick_zg = self._solve_pick_or_place(
      self._from_location,
      source_support_height,
      self._pick_gripper_offset,
    )
    place_z, place_zg = self._solve_pick_or_place(
      self._to_location,
      destination_support_height,
      self._place_gripper_offset,
    )

    obstacle_height = self._obstacle_height_between_locations()
    # Relid carries a lid over the visible top of the destination plate, so
    # the carry height must clear the destination's full top height, not
    # just the support plane under that plate.
    carry_stack = (
      max(
        self._current_complete_height(self._from_location),
        self._current_complete_height(self._to_location),
        obstacle_height,
      )
      + Z_CLEARANCE
    )
    carry_z, carry_zg = self._solve_pick_or_place(
      self._from_location,
      carry_stack,
      self._place_gripper_offset,
    )
    carry_z = self._clamp(carry_z, "z")
    carry_zg = self._clamp(carry_zg, "zg")
    carry_z, carry_zg = self._adjust_for_head_clearance(carry_z, carry_zg, self._source_labware)

    return PickPlacePositions(
      pick_z=pick_z,
      pick_zg=pick_zg,
      carry_z=carry_z,
      carry_zg=carry_zg,
      place_z=place_z,
      place_zg=place_zg,
    )

  def _build_lidded_plate(self, plate: Labware) -> Labware:
    metadata = dict(plate.metadata or {})
    metadata["is_lidded"] = True
    metadata["is_sealed"] = bool(plate.is_sealed)
    height = float(
      metadata.get("lidded_height_mm") or metadata.get("total_height_mm") or plate.height
    )
    stack_height = float(
      metadata.get("lidded_stack_height_mm") or metadata.get("stack_height_mm") or height
    )
    metadata["height_mm"] = height
    metadata["stack_height_mm"] = stack_height
    metadata["total_height_mm"] = height
    generated_lid = generated_lid_metadata(metadata)
    if generated_lid is not None:
      metadata["generated_lid"] = generated_lid
    return Labware(
      id=plate.id,
      definition_id=plate.definition_id,
      name=plate.name,
      height=height,
      width=plate.width,
      length=plate.length,
      labware_type=plate.labware_type,
      gripper_offset=plate.gripper_offset,
      stack_height=stack_height,
      is_lidded=True,
      is_sealed=plate.is_sealed,
      wells=plate.wells,
      metadata=metadata,
    )

  async def _grip_plate(self) -> None:
    grip_speed = self._speed if self._speed != "slow" else "med"
    if self._grip_attempts > 0:
      self._log_step("reopen_gripper_for_retry", targets={"G": OPEN_GRIPPER_POSITION})
      await asyncio.to_thread(self._ctrl.open_gripper)
    before_snapshot = self._snapshot()
    self._plate_pick_verified = False
    self._log_step("grip_lid", targets={"G": _PICK_PLACE_GRIP_TARGET})
    await asyncio.to_thread(self._ctrl.grip, grip_speed, _PICK_PLACE_GRIP_TARGET, True)
    self._grip_attempts += 1
    after_snapshot = self._snapshot()
    verified, verification = self._verify_plate_pickup(before_snapshot, after_snapshot)
    if not verified:
      before_snapshot = self._live_snapshot(force_refresh=False)
      after_snapshot = self._live_snapshot(force_refresh=True)
      verified, verification = self._verify_plate_pickup(before_snapshot, after_snapshot)
    self._live_status.update(
      {
        "pickup_verification": verification,
        "operator_prompt": None
        if verified
        else {
          "kind": "pickup_verification_failed",
          "title": "Lid pickup not detected",
          "message": (
            "The gripper closed, but the post-grip G position indicates the lid was not picked up."
          ),
          "choices": ["retry", "ignore", "abort"],
        },
      }
    )
    if not verified:
      raise RuntimeError("Lid pickup not detected after gripper close")
    self._plate_pick_verified = True
    self._force_continue_after_pickup_failure = False
    self._log_step("grip_lid_complete")

  async def _release_plate(self) -> None:
    if not self._plate_pick_verified and not self._force_continue_after_pickup_failure:
      self._log_step("release_lid_skipped_missing_lid", targets={"G": OPEN_GRIPPER_POSITION})
      await asyncio.to_thread(self._ctrl.open_gripper)
      self._log_step("release_lid_skipped_missing_lid_complete")
      return
    self._log_step("release_lid", targets={"G": OPEN_GRIPPER_POSITION})
    await asyncio.to_thread(self._ctrl.open_gripper)
    self._log_step("release_lid_complete")
    removed_lid = self._deck.remove(self._from_location)
    if removed_lid is not self._source_lid:
      logger.warning("Relid source stack changed during task; using runtime top lid state")
    removed_plate = self._deck.remove(self._to_location)
    if removed_plate is not self._destination_plate:
      logger.warning("Relid destination stack changed during task; using runtime top plate state")
    self._deck.add(self._to_location, self._build_lidded_plate(removed_plate))


class ScanStackHeightTask(StateMachineTask):
  """Scan a deck location with the gripper plate sensor to infer stack count."""

  def __init__(
    self,
    controller: BravoController,
    teachpoints: Teachpoints,
    config: BravoMachineConfig,
    deck: DeckState,
    *,
    location: int,
    template_labware: Labware,
    expected_count: Optional[int] = None,
  ) -> None:
    """Initialize the task.

    Args:
      controller: The controller to operate.
      teachpoints: The deck teachpoints to move against.
      config: The machine configuration to scan against.
      deck: The deck state, used only by the simulation short-circuit.
      location: The deck location to scan.
      template_labware: The labware type to measure the stack against
        (its ``height``/``stack_height`` set the per-plate increment).
      expected_count: If given, the scan raises an operator prompt when
        the inferred count differs from this.
    """
    super().__init__(name=f"ScanStackHeight_{location}")
    self._ctrl = controller
    self._tp = teachpoints
    self._config = config
    self._deck = deck
    self._location = int(location)
    self._template = template_labware
    self._expected_count: Optional[int] = (
      int(expected_count) if expected_count is not None else None
    )
    self._result: Dict[str, Any] = {
      "status": "pending",
      "location": self._location,
      "configured_labware": self._template.name,
      "used_manual_override": False,
    }
    self._scan_xy: Optional[Tuple[float, float]] = None
    self._baseline_sum: Optional[float] = None
    self._start_zg: Optional[float] = None
    self._end_zg: Optional[float] = None
    self._operator_prompt: Optional[Dict[str, Any]] = None
    # Per-step status surfaced for a live viewport so it has motion
    # waypoints to tween between during the scan. _live_status mirrors
    # PickPlaceTask's pattern: {"task": ..., "step": ..., "targets": {...}}.
    # Pure metadata -- no hardware impact.
    self._live_status: Dict[str, Any] = {
      "task": "scan_stack_height",
      "step": None,
      "location": self._location,
      "targets": {},
    }
    # Index of the scan step so on_error_action(RETRY) can re-scan rather
    # than just re-run the validation with the same cached measurement.
    self._scan_step_index = 3

  def result_payload(self) -> Dict[str, Any]:
    """Return the scan's result payload."""
    return dict(self._result)

  def status_payload(self) -> dict:
    """Return the task's live status, merged with the current step/targets."""
    payload = dict(self._result)
    # Merges in live step/targets so a live viewport can be driven from
    # this even though Zg readback is frozen during the firmware-level
    # scan command.
    payload.update(
      {
        "task": self._live_status.get("task"),
        "step": self._live_status.get("step"),
        "targets": dict(self._live_status.get("targets") or {}),
      }
    )
    if self.status == TaskStatus.FAILED and self._operator_prompt:
      payload["operator_prompt"] = dict(self._operator_prompt)
    return payload

  def on_error_action(self, action: ErrorAction) -> None:
    """Apply the operator's choice for the step that just failed.

    On RETRY, rewinds to the scan step so the measurement is retaken
    before re-validating. Clears the prompt so a fresh mismatch can
    populate it.
    """
    if action == ErrorAction.RETRY:
      self._current_step_index = self._scan_step_index
      self._operator_prompt = None
    elif action == ErrorAction.IGNORE:
      self._operator_prompt = None

  def get_steps(self) -> "list[tuple[str, Callable[[], Awaitable[None]]]]":
    """Return this task's steps, in execution order."""
    return [
      ("move_to_safe_start", self._move_to_safe_start),
      ("move_xy_to_scan", self._move_xy_to_scan),
      ("move_to_scan_start", self._move_to_scan_start),
      ("scan_with_plate_sensor", self._scan_with_plate_sensor),
      ("validate_expected_count", self._validate_expected_count),
      ("return_gripper_to_nesting", self._return_gripper_to_nesting),
    ]

  def _log(self, message: str) -> None:
    logger.info("ScanStack %s", message)

  def _log_step(
    self,
    name: str,
    *,
    targets: Optional[Dict[str, float]] = None,
    message: Optional[str] = None,
  ) -> None:
    """Record a scan step's motion targets for a live viewport to consume.

    Mirrors PickPlaceTask._log_step: a viewport lerps its joints toward
    whatever axes appear in ``targets``. This is pure metadata -- the
    actual hardware moves are still issued by the step body.
    """
    self._live_status["step"] = name
    self._live_status["targets"] = dict(targets or {})
    if message:
      logger.info("ScanStack %s %s", name, message)
    else:
      logger.info("ScanStack %s", name)
    if targets:
      target_text = " ".join(f"{axis}={value:.3f}" for axis, value in targets.items())
      logger.info("ScanStack %s target=%s", name, target_text)

  def _gripper_y_offset(self) -> float:
    return float(self._config.gripper.y_offset or 0.0)

  def _axis_range(self, axis: Axis) -> Tuple[float, float]:
    cfg = self._config.axes.get(axis)
    if cfg is None:
      raise RuntimeError(f"Missing axis config for {axis_display_name(axis)}")
    return cfg.range.min_pos, cfg.range.max_pos

  def _clamp(self, value: float, axis: Axis) -> float:
    min_pos, max_pos = self._axis_range(axis)
    return max(min_pos, min(max_pos, value))

  def _get_current_z(self) -> float:
    try:
      return float(self._ctrl.get_position("z"))
    except Exception:
      return float(self._config.safety.z_safe_position)

  def _tip_length_for_pick_place(self) -> float:
    stored_length = self._config.head.teach_tip_length_mm
    if stored_length is not None:
      return float(stored_length)
    default_capacity = float(
      self._config.head.teach_tip_capacity or self._config.head.default_tip_capacity or 0.0
    )
    tip_ref = self._config.head.teach_tip_id or self._config.head.default_tip_id or default_capacity
    tip_length = get_tip_length_mm(self._config.head.head_type, tip_ref)
    if tip_length is None:
      raise RuntimeError(f"Teach tip length is not configured for {self._config.head.head_type}")
    return tip_length

  def _gripper_pad_reference_zg(self, tip_length: float) -> float:
    """Return the Zg for the plate-pad plane; see PickPlaceTask._gripper_pad_reference_zg."""
    g = self._config.gripper
    return g.pad_zg_reference_mm + (tip_length - g.pad_reference_tip_length_mm)

  def _pad_plane_sum(self) -> float:
    """Return the ``Z + Zg`` sum at the plate-pad plane -- the datum the scan measures against.

    Height above the pad is a function of ``Z + Zg`` alone, so this stays
    valid wherever the gripper actually ends up. It deliberately does not
    go through :meth:`_solve_pick_or_place`, which clamps both axes into
    their travel ranges: right for a move target, but it distorts a
    datum whenever the geometry saturates, and the distortion is silent.

    No gripper offset appears here. The scan senses where the top of the
    stack is; where on a plate the jaws would grab it is a different
    question and must not shift the measurement.
    """
    z_teachpoint = self._tp.get_teachpoint(self._location, "z")
    tip_length = self._tip_length_for_pick_place()
    if head_type_is_disposable(self._config.head.head_type):
      return z_teachpoint + self._gripper_pad_reference_zg(tip_length)
    return (
      z_teachpoint
      + tip_length
      - GRIPPER_THICKNESS
      - GRIPPER_TO_BASE_OF_HEAD_GAP
      + _LENGTH_DIFFERENCE_96_TO_384
    )

  def _solve_pick_or_place(self, stack_height: float, gripper_offset: float) -> Tuple[float, float]:
    z_min, _ = self._axis_range("z")
    _, zg_max = self._axis_range("zg")
    zg_max = min(zg_max, _PLATE_HANDLING_ZG_MAX)
    z_current = self._get_current_z()
    z_teachpoint = self._tp.get_teachpoint(self._location, "z")
    tip_length = self._tip_length_for_pick_place()
    if head_type_is_disposable(self._config.head.head_type):
      new_zg = (
        z_teachpoint
        - z_current
        + self._gripper_pad_reference_zg(tip_length)
        - gripper_offset
        - stack_height
      )
    else:
      new_zg = (
        z_teachpoint
        - z_current
        + tip_length
        - GRIPPER_THICKNESS
        - GRIPPER_TO_BASE_OF_HEAD_GAP
        - gripper_offset
        - stack_height
        + _LENGTH_DIFFERENCE_96_TO_384
      )
    safe_zg = self._clamp(self._config.axes["zg"].range.min_pos, "zg")
    if new_zg > zg_max:
      z = z_current + new_zg - zg_max
      zg = zg_max
    elif new_zg < safe_zg:
      z = z_current + new_zg - safe_zg
      if z < z_min and (safe_zg + z) >= safe_zg:
        safe_zg += z
        z = z_min
      zg = safe_zg
    else:
      z = z_current
      zg = new_zg
    return self._clamp(z, "z"), self._clamp(zg, "zg")

  async def _move_to_safe_start(self) -> None:
    safe_z = float(self._config.safety.z_safe_position)
    self._log_step(
      "move_to_safe_start",
      targets={"Z": safe_z, "Zg": _GRIPPER_RECESS_DEPTH, "G": OPEN_GRIPPER_POSITION},
      message=f"Z={safe_z:.3f}",
    )
    await asyncio.to_thread(self._ctrl.move, [_axis_move(self._ctrl, "z", safe_z)], True)
    await asyncio.to_thread(self._ctrl.open_gripper)
    await asyncio.to_thread(
      self._ctrl.move, [_axis_move(self._ctrl, "zg", _GRIPPER_RECESS_DEPTH)], True
    )

  async def _move_xy_to_scan(self) -> None:
    x = self._tp.get_teachpoint(self._location, "x")
    y = self._tp.get_teachpoint(self._location, "y") + self._gripper_y_offset()
    self._scan_xy = (x, y)
    self._log_step(
      "move_xy_to_scan",
      targets={"X": x, "Y": y},
      message=f"X={x:.3f} Y={y:.3f}",
    )
    await asyncio.to_thread(
      self._ctrl.move,
      [_axis_move(self._ctrl, "x", x), _axis_move(self._ctrl, "y", y)],
      True,
    )

  async def _move_to_scan_start(self) -> None:
    # Two different things, kept deliberately separate.
    #
    # Where to PARK the gripper for the scan: the ordinary pick geometry,
    # which is gripper-offset aware so the jaws start clear of the stack.
    baseline_z, baseline_zg = self._solve_pick_or_place(0.0, float(self._template.gripper_offset))
    # What the reading is MEASURED AGAINST: the plate-pad plane. A
    # gripper-offset-aware solve here would make every reading short by
    # the offset and bias the inferred count differently for each
    # labware, since the offset is labware-specific but the pad plane is
    # not.
    self._baseline_sum = self._pad_plane_sum()
    self._start_zg = max(
      _GRIPPER_RECESS_DEPTH, baseline_zg - float(self._config.safety.approach_height or 10.0)
    )
    self._end_zg = min(self._axis_range("zg")[1], baseline_zg + 120.0)
    self._log_step(
      "move_to_scan_start",
      targets={"Z": baseline_z, "Zg": float(self._start_zg)},
      message=(
        f"Z={baseline_z:.3f} Zg={self._start_zg:.3f} "
        f"pad_datum_sum={self._baseline_sum:.3f} end_zg={self._end_zg:.3f}"
      ),
    )
    await asyncio.to_thread(
      self._ctrl.move,
      [
        _axis_move(self._ctrl, "z", baseline_z),
        _axis_move(self._ctrl, "zg", float(self._start_zg)),
      ],
      True,
    )

  async def _scan_with_plate_sensor(self) -> None:
    # Simulation short-circuit: no real plate sensor, so relies on the
    # virtual deck as ground truth. The physical scan path would see
    # zero height (nothing in the air to trigger the sensor) and fall
    # into manual_count_required, defeating the whole point of running a
    # simulation. Synthesizes a scan result from the current stack depth
    # instead.
    if isinstance(self._ctrl, SimulationController):
      stack = self._deck.get_stack(self._location)
      live_count = len(stack)
      stack_height = float(self._template.stack_height or self._template.height or 0.0)
      plate_height = float(self._template.height or 0.0)
      if stack_height <= 0.0:
        raise RuntimeError(
          f"Configured labware at location {self._location} has no stacking thickness"
        )
      # Reconstructs measured_height the same way the physical path
      # would have for this count, so downstream consumers see
      # consistent units.
      theoretical_height = _stacking_support_height_for_count(live_count, stack_height)
      estimated_total_height = _stack_total_height_for_count(live_count, plate_height, stack_height)
      self._result = {
        "status": "completed",
        "location": self._location,
        "configured_labware": self._template.name,
        # measured_height_mm is the top-of-stack height (includes the
        # top plate's own height) to match the real-hardware path's
        # contract; the support height is reported as
        # theoretical_height_mm.
        "measured_height_mm": estimated_total_height,
        "raw_measured_height_mm": estimated_total_height,
        "height_offset_mm": 0.0,
        "stack_height_mm": stack_height,
        "plate_height_mm": plate_height,
        "inferred_count": live_count,
        "theoretical_height_mm": theoretical_height,
        "estimated_total_height_mm": estimated_total_height,
        "rounded_stack_height_mm": round(theoretical_height),
        "used_manual_override": False,
        "baseline_sum_mm": self._baseline_sum,
        "scan_start_zg_mm": self._start_zg,
        "scan_end_zg_mm": self._end_zg,
        "trigger_z_mm": None,
        "trigger_zg_mm": None,
        "simulated": True,
        "message": (
          f"[simulation] Deck state reports {live_count} plate(s) at "
          f"location {self._location}; stacking thickness "
          f"{stack_height:.3f} mm."
        ),
      }
      return

    # Publishes the scan's end waypoint so a live viewport can animate
    # toward it during the firmware-blocking scan call. Zg readback is
    # frozen during scan_stack_with_gripper on real hardware, so without
    # this motion target the viewport sits still for the full scan
    # duration.
    # Always set by _move_to_scan_start, which runs immediately before
    # this step in get_steps().
    assert self._start_zg is not None
    assert self._end_zg is not None
    self._log_step(
      "scan_with_plate_sensor",
      targets={"Zg": float(self._end_zg)},
      message=f"start_zg={self._start_zg:.3f} end_zg={self._end_zg:.3f}",
    )
    transient = float(self._config.safety.plate_sensor_transient)
    result = await asyncio.to_thread(
      self._ctrl.scan_stack_with_gripper,
      start_zg=float(self._start_zg),
      end_zg=float(self._end_zg),
      speed="slow",
      transient=transient,
    )
    scan_debug = {
      "scan_mode": result.get("scan_mode"),
      "scan_stop_strategy": result.get("stop_strategy"),
      "scan_elapsed_ms": result.get("elapsed_ms"),
      "scan_poll_count": result.get("poll_count"),
      "scan_sensor_reads": result.get("sensor_reads"),
      "scan_sensor_read_failures": result.get("sensor_read_failures"),
      "scan_transient_ms": transient * 1000.0,
    }
    detected = bool(result.get("detected", False))
    if not detected:
      self._result = {
        "status": "manual_count_required",
        "location": self._location,
        "configured_labware": self._template.name,
        "used_manual_override": False,
        "message": (
          f"No plate detected during scan at location {self._location}. "
          "Enter the number of stacked plates."
        ),
      }
      self._result.update({key: value for key, value in scan_debug.items() if value is not None})
      return

    measured_height_raw = result.get("measured_height_mm")
    if measured_height_raw is not None:
      # The controller reported a height above the support surface
      # directly, which is already the datum the count model wants.
      measured_height_unadjusted = max(0.0, float(measured_height_raw))
      current_z = None
      current_zg = None
      sensor_correction = 0.0
      measured_height = measured_height_unadjusted
    else:
      current_z = float(self._ctrl.get_position("z"))
      current_zg = float(self._ctrl.get_position("zg"))
      # _baseline_sum is the plate-pad plane, so this is already the
      # height of the sensor trigger point above the pad. The only
      # thing left to remove is how far above the plate's top face the
      # sensor fires, which is a fixed property of the gripper.
      measured_height_unadjusted = max(
        0.0, float(self._baseline_sum or 0.0) - (current_z + current_zg)
      )
      sensor_correction = -_SCAN_SENSOR_STANDOFF_MM
      measured_height = max(0.0, measured_height_unadjusted + sensor_correction)
    stack_height = float(self._template.stack_height or self._template.height or 0.0)
    plate_height = float(self._template.height or 0.0)
    if stack_height <= 0.0:
      raise RuntimeError(
        f"Configured labware at location {self._location} has no stacking thickness"
      )
    # measured_height is the top-of-stack height above the support
    # surface, so it includes the top plate's own height. Subtracting it
    # recovers the support height before inferring the count -- otherwise
    # a single tall plate (whose height ~ its stacking thickness) reads
    # as a phantom 2nd plate. See _infer_stack_count_from_scan_height.
    inferred_count = _infer_stack_count_from_scan_height(
      measured_height, stack_height, plate_height
    )
    theoretical_height = _stacking_support_height_for_count(inferred_count, stack_height)
    estimated_total_height = _stack_total_height_for_count(
      inferred_count, plate_height, stack_height
    )
    rounded_stack_height = round(theoretical_height)
    self._result = {
      "status": "completed",
      "location": self._location,
      "configured_labware": self._template.name,
      "measured_height_mm": measured_height,
      "raw_measured_height_mm": measured_height_unadjusted,
      "height_offset_mm": sensor_correction,
      "sensor_standoff_mm": _SCAN_SENSOR_STANDOFF_MM,
      "stack_height_mm": stack_height,
      "plate_height_mm": plate_height,
      "inferred_count": inferred_count,
      "theoretical_height_mm": theoretical_height,
      "estimated_total_height_mm": estimated_total_height,
      "rounded_stack_height_mm": rounded_stack_height,
      "used_manual_override": False,
      "baseline_sum_mm": self._baseline_sum,
      "scan_start_zg_mm": self._start_zg,
      "scan_end_zg_mm": self._end_zg,
      "trigger_z_mm": current_z,
      "trigger_zg_mm": current_zg,
      "message": (
        f"Measured scan height {measured_height:.3f} mm "
        f"(raw {measured_height_unadjusted:.3f} mm above the pad, sensor "
        f"standoff {_SCAN_SENSOR_STANDOFF_MM:.3f} mm); "
        f"stacking thickness {stack_height:.3f} mm; "
        f"inferred {inferred_count} plates; rounded stacking height {rounded_stack_height:.0f} mm."
      ),
    }
    self._result.update({key: value for key, value in scan_debug.items() if value is not None})

  async def _validate_expected_count(self) -> None:
    # Skips entirely if no expectation was set on this task -- the
    # caller left it unset, meaning "just report whatever was measured."
    if self._expected_count is None:
      return
    inferred = int(self._result.get("inferred_count") or 0)
    expected = int(self._expected_count)
    if inferred == expected:
      return
    measured = float(self._result.get("measured_height_mm") or 0.0)
    stack_height = float(self._result.get("stack_height_mm") or 0.0)
    message = (
      f"Stack-count mismatch at location {self._location}.\n\n"
      f"Expected {expected} plate(s) but measured {inferred} plate(s) "
      f"(measured height {measured:.2f} mm, stacking thickness "
      f"{stack_height:.2f} mm).\n\n"
      "Retry re-scans the stack.\n"
      "Ignore continues with the measured count.\n"
      "Abort stops the workflow."
    )
    self._operator_prompt = {
      "kind": "scan_stack_height_mismatch",
      "title": "Stack count mismatch",
      "message": message,
      "choices": ["retry", "ignore", "abort"],
      "expected_count": expected,
      "inferred_count": inferred,
      "location": self._location,
    }
    # Also surfaces the mismatch on the result payload so downstream
    # consumers can see it even after IGNORE.
    self._result["expected_count"] = expected
    self._result["count_mismatch"] = True
    raise RuntimeError(message)

  async def _return_gripper_to_nesting(self) -> None:
    safe_z = float(self._config.safety.z_safe_position)
    self._log_step(
      "return_gripper_to_nesting",
      targets={"Zg": _GRIPPER_RECESS_DEPTH, "Z": safe_z},
    )
    await asyncio.to_thread(
      self._ctrl.move, [_axis_move(self._ctrl, "zg", _GRIPPER_RECESS_DEPTH)], True
    )
    await asyncio.to_thread(self._ctrl.move, [_axis_move(self._ctrl, "z", safe_z)], True)
