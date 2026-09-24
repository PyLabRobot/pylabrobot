"""Offline scan specifications, plans, and results for the Celigo."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, List, Literal, Optional, Sequence, Tuple, Union

from pylabrobot.resources.plate import Plate
from pylabrobot.revvity.celigo.camera import CameraFrame
from pylabrobot.revvity.celigo.config import CeligoConfig
from pylabrobot.revvity.celigo.coordinates import CoordinateSystems
from pylabrobot.revvity.celigo.navigation import effective_fov_mm, well_to_sample_mm

if TYPE_CHECKING:
  from pylabrobot.revvity.celigo.celigo import FocusResult


CoordinateMM = Tuple[float, float]
BlockShape = Tuple[int, int]
AutofocusMethod = Literal["image"]

_EXACT_ROUTE_LIMIT = 14


def _validate_finite(value: float, name: str) -> None:
  if not math.isfinite(value):
    raise ValueError(f"{name} must be finite")


def _validate_positive_integer(value: int, name: str) -> None:
  if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
    raise ValueError(f"{name} must be a positive integer")


def _validate_block_shape(block_shape: BlockShape) -> BlockShape:
  if not isinstance(block_shape, tuple) or len(block_shape) != 2:
    raise ValueError("block_shape must be a (columns, rows) tuple")
  columns, rows = block_shape
  _validate_positive_integer(columns, "block columns")
  _validate_positive_integer(rows, "block rows")
  return columns, rows


def _validate_scan_region(region: "ScanRegion") -> "ScanRegion":
  if not isinstance(region, ScanRegion):
    raise TypeError("bounds must be a ScanRegion")
  return region


def _validate_autofocus(autofocus: Optional[AutofocusMethod]) -> None:
  if autofocus not in (None, "image"):
    raise ValueError("autofocus must be None or 'image'")


def _configuration_fingerprint(config: CeligoConfig) -> str:
  """Return a stable fingerprint of the configuration used to compile a scan plan."""
  serialized = json.dumps(
    asdict(config),
    allow_nan=False,
    separators=(",", ":"),
    sort_keys=True,
  )
  return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScanRegion:
  """Axis-aligned sample bounds in millimeters from the sample's top-left corner."""

  left: float
  top: float
  right: float
  bottom: float

  def __post_init__(self) -> None:
    for value, name in (
      (self.left, "left"),
      (self.top, "top"),
      (self.right, "right"),
      (self.bottom, "bottom"),
    ):
      _validate_finite(value, name)
    if self.right <= self.left:
      raise ValueError("right must be greater than left")
    if self.bottom <= self.top:
      raise ValueError("bottom must be greater than top")

  @classmethod
  def from_bounds_mm(
    cls,
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
  ) -> "ScanRegion":
    """Create a sample-relative physical region."""
    return cls(left=left, top=top, right=right, bottom=bottom)

  @property
  def width_mm(self) -> float:
    return self.right - self.left

  @property
  def height_mm(self) -> float:
    return self.bottom - self.top

  @property
  def area_mm2(self) -> float:
    return self.width_mm * self.height_mm


@dataclass(frozen=True)
class ScanEstimateModel:
  """Explicit throughput assumptions used for offline estimates."""

  seconds_per_frame: float = 0.35
  seconds_per_stage_position: float = 2.0
  seconds_per_autofocus: float = 5.0
  bytes_per_pixel: int = 1

  def __post_init__(self) -> None:
    for value, name in (
      (self.seconds_per_frame, "seconds_per_frame"),
      (self.seconds_per_stage_position, "seconds_per_stage_position"),
      (self.seconds_per_autofocus, "seconds_per_autofocus"),
    ):
      _validate_finite(value, name)
      if value < 0:
        raise ValueError(f"{name} must be non-negative")
    _validate_positive_integer(self.bytes_per_pixel, "bytes_per_pixel")


@dataclass(frozen=True)
class Capture:
  """One channel and its camera settings."""

  channel: str
  exposure_ms: Optional[float] = None
  gain: Optional[float] = None

  def __post_init__(self) -> None:
    if not isinstance(self.channel, str) or not self.channel.strip():
      raise ValueError("channel must be a non-empty string")
    object.__setattr__(self, "channel", self.channel.strip())
    if self.exposure_ms is not None:
      _validate_finite(self.exposure_ms, "exposure_ms")
      if self.exposure_ms <= 0:
        raise ValueError("exposure_ms must be positive")
    if self.gain is not None:
      _validate_finite(self.gain, "gain")
      if self.gain < 0:
        raise ValueError("gain must be non-negative")


@dataclass(frozen=True)
class _PointGeometry:
  centers_mm: Tuple[CoordinateMM, ...]
  labels: Tuple[Optional[str], ...]
  block_shape: BlockShape


@dataclass(frozen=True)
class _RandomGeometry:
  bounds: ScanRegion
  count: int
  block_shape: BlockShape
  seed: int
  non_overlapping: bool


@dataclass(frozen=True)
class _FullCoverageGeometry:
  bounds: ScanRegion


_SpecGeometry = Union[_PointGeometry, _RandomGeometry, _FullCoverageGeometry]


@dataclass(frozen=True)
class ScanSpec:
  """A complete, reusable description of scan geometry and acquisition settings."""

  geometry: _SpecGeometry
  captures: Tuple[Capture, ...]
  autofocus: Optional[AutofocusMethod]

  def __post_init__(self) -> None:
    if not isinstance(
      self.geometry,
      (_PointGeometry, _RandomGeometry, _FullCoverageGeometry),
    ):
      raise TypeError("geometry must be created by a ScanSpec constructor")
    if not self.captures or any(not isinstance(capture, Capture) for capture in self.captures):
      raise ValueError("captures must contain at least one Capture")
    _validate_autofocus(self.autofocus)

  @classmethod
  def wells(
    cls,
    plate: Plate,
    wells: Sequence[str],
    *,
    block_shape: BlockShape = (1, 1),
    channel: Optional[str] = None,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    captures: Optional[Sequence[Capture]] = None,
    autofocus: Optional[AutofocusMethod] = None,
  ) -> "ScanSpec":
    """Create a scan at named well centers without retaining the plate."""
    if not isinstance(plate, Plate):
      raise TypeError("plate must be a PyLabRobot Plate")
    if isinstance(wells, str):
      raise ValueError("wells must be a sequence of well names")
    normalized_wells = tuple(well.strip().upper() for well in wells)
    if not normalized_wells:
      raise ValueError("wells must contain at least one well name")
    if any(not well for well in normalized_wells):
      raise ValueError("well names must not be empty")
    centers = tuple(well_to_sample_mm(plate, well) for well in normalized_wells)
    return cls(
      geometry=_point_geometry(centers, block_shape, normalized_wells),
      captures=_normalize_captures(channel, exposure_ms, gain, captures),
      autofocus=autofocus,
    )

  @classmethod
  def points(
    cls,
    centers_mm: Sequence[CoordinateMM],
    *,
    block_shape: BlockShape = (1, 1),
    channel: Optional[str] = None,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    captures: Optional[Sequence[Capture]] = None,
    autofocus: Optional[AutofocusMethod] = None,
  ) -> "ScanSpec":
    """Create an anonymous scan centered at explicit sample-relative points."""
    normalized_centers = _normalize_centers(centers_mm)
    return cls(
      geometry=_point_geometry(normalized_centers, block_shape),
      captures=_normalize_captures(channel, exposure_ms, gain, captures),
      autofocus=autofocus,
    )

  @classmethod
  def random(
    cls,
    bounds: ScanRegion,
    *,
    count: int,
    block_shape: BlockShape,
    seed: int = 0,
    non_overlapping: bool = True,
    channel: Optional[str] = None,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    captures: Optional[Sequence[Capture]] = None,
    autofocus: Optional[AutofocusMethod] = None,
  ) -> "ScanSpec":
    """Create a reproducible random sample of blocks within physical bounds."""
    _validate_scan_region(bounds)
    _validate_positive_integer(count, "count")
    validated_shape = _validate_block_shape(block_shape)
    if isinstance(seed, bool) or not isinstance(seed, int):
      raise ValueError("seed must be an integer")
    if not isinstance(non_overlapping, bool):
      raise ValueError("non_overlapping must be a boolean")
    return cls(
      geometry=_RandomGeometry(
        bounds=bounds,
        count=count,
        block_shape=validated_shape,
        seed=seed,
        non_overlapping=non_overlapping,
      ),
      captures=_normalize_captures(channel, exposure_ms, gain, captures),
      autofocus=autofocus,
    )

  @classmethod
  def full_coverage(
    cls,
    bounds: ScanRegion,
    *,
    channel: Optional[str] = None,
    exposure_ms: Optional[float] = None,
    gain: Optional[float] = None,
    captures: Optional[Sequence[Capture]] = None,
    autofocus: Optional[AutofocusMethod] = None,
  ) -> "ScanSpec":
    """Create a scan that covers all physical bounds."""
    _validate_scan_region(bounds)
    return cls(
      geometry=_FullCoverageGeometry(bounds=bounds),
      captures=_normalize_captures(channel, exposure_ms, gain, captures),
      autofocus=autofocus,
    )


@dataclass(frozen=True)
class ScanPosition:
  """One camera frame target relative to a coarse-stage block."""

  index: int
  block_index: int
  tile_row: int
  tile_column: int
  sample_x_mm: float
  sample_y_mm: float
  galvo_offset_x_mm: float
  galvo_offset_y_mm: float


@dataclass(frozen=True)
class ScanBlock:
  """One stationary coarse-stage position and the frames acquired there."""

  index: int
  center_x_mm: float
  center_y_mm: float
  stage_x_mm: float
  stage_y_mm: float
  bounds: ScanRegion
  block_shape: BlockShape
  label: Optional[str] = None

  @property
  def tile_columns(self) -> int:
    return self.block_shape[0]

  @property
  def tile_rows(self) -> int:
    return self.block_shape[1]


@dataclass(frozen=True)
class PlannedFrame:
  """One exact frame operation in a compiled scan plan."""

  index: int
  block: ScanBlock
  position: ScanPosition
  capture: Capture


@dataclass(frozen=True)
class ScanPlan:
  """An inspectable, hardware-free sequence of validated frame operations."""

  spec: ScanSpec
  configuration_fingerprint: str = field(repr=False)
  blocks: Tuple[ScanBlock, ...]
  frames: Tuple[PlannedFrame, ...]
  estimate_model: ScanEstimateModel
  frame_count: int
  stage_position_count: int
  autofocus_count: int
  sampled_area_mm2: float
  estimated_duration: timedelta
  estimated_storage_bytes: int

  @property
  def positions(self) -> Tuple[ScanPosition, ...]:
    return tuple(frame.position for frame in self.frames)

  @property
  def stage_positions_mm(self) -> Tuple[CoordinateMM, ...]:
    return tuple((block.stage_x_mm, block.stage_y_mm) for block in self.blocks)

  def matches_configuration(self, config: CeligoConfig) -> bool:
    """Whether this plan was compiled from the supplied configuration state."""
    return self.configuration_fingerprint == _configuration_fingerprint(config)

  def __str__(self) -> str:
    channels = ", ".join(capture.channel for capture in self.spec.captures)
    return (
      "ScanPlan\n"
      f"  geometry: {_describe_geometry(self.spec.geometry)}\n"
      f"  channels: {channels}\n"
      f"  blocks: {len(self.blocks)}\n"
      f"  frames: {self.frame_count}\n"
      f"  stage positions: {self.stage_position_count}\n"
      f"  autofocus operations: {self.autofocus_count}\n"
      f"  estimated duration: {_format_duration(self.estimated_duration)}\n"
      f"  estimated storage: {_format_bytes(self.estimated_storage_bytes)}\n"
      f"  sampled area: {self.sampled_area_mm2:.3f} mm²"
    )


@dataclass(frozen=True)
class FrameResult:
  """A captured frame linked to the exact operation that produced it."""

  planned: PlannedFrame
  frame: CameraFrame
  actual_stage_mm: CoordinateMM
  actual_z_mm: float
  galvo_hardware_voltages: Tuple[float, float]
  focus: Optional["FocusResult"]


@dataclass(frozen=True)
class ScanResult:
  """All frames and elapsed time from executing one plan."""

  plan: ScanPlan
  frames: Tuple[FrameResult, ...]
  elapsed: timedelta


@dataclass(frozen=True)
class _CaptureGeometry:
  frame_x_mm: float
  frame_y_mm: float
  step_x_mm: float
  step_y_mm: float
  max_block_columns: int
  max_block_rows: int

  def block_size_mm(self, block_shape: BlockShape) -> CoordinateMM:
    columns, rows = _validate_block_shape(block_shape)
    return (
      self.frame_x_mm + (columns - 1) * self.step_x_mm,
      self.frame_y_mm + (rows - 1) * self.step_y_mm,
    )


@dataclass(frozen=True)
class _BlockSelection:
  x_centers_mm: Tuple[float, ...]
  y_centers_mm: Tuple[float, ...]


@dataclass(frozen=True)
class _BlockLayout:
  selections: Tuple[_BlockSelection, ...]
  label: Optional[str]
  bounds: Optional[ScanRegion]


def build_scan_plan(
  config: CeligoConfig,
  spec: ScanSpec,
  *,
  estimate_model: Optional[ScanEstimateModel] = None,
) -> ScanPlan:
  """Compile a complete scan specification without communicating with hardware."""
  if not isinstance(config, CeligoConfig):
    raise TypeError("config must be a CeligoConfig")
  if not isinstance(spec, ScanSpec):
    raise TypeError("spec must be a ScanSpec")
  estimates = ScanEstimateModel() if estimate_model is None else estimate_model
  if not isinstance(estimates, ScanEstimateModel):
    raise TypeError("estimate_model must be a ScanEstimateModel")

  capture_geometries = tuple(_capture_geometry(config, capture) for capture in spec.captures)
  layouts = _build_layouts(spec.geometry, capture_geometries)
  coordinate_systems = CoordinateSystems.from_config(
    config.calibration,
    config.hardware_defaults,
  )

  blocks: List[ScanBlock] = []
  frames: List[PlannedFrame] = []
  footprints: List[ScanRegion] = []
  next_position_index = 0
  for block_index, layout in enumerate(layouts):
    block, positions, block_footprints, next_position_index = _compile_block(
      config=config,
      coordinate_systems=coordinate_systems,
      captures=spec.captures,
      capture_geometries=capture_geometries,
      block_index=block_index,
      first_position_index=next_position_index,
      layout=layout,
    )
    blocks.append(block)
    footprints.extend(block_footprints)
    for position, capture in positions:
      frames.append(
        PlannedFrame(
          index=len(frames),
          block=block,
          position=position,
          capture=capture,
        )
      )

  frame_count = len(frames)
  stage_position_count = len(blocks)
  autofocus_count = stage_position_count if spec.autofocus is not None else 0
  exposure_seconds = sum(
    0.0 if frame.capture.exposure_ms is None else frame.capture.exposure_ms / 1000.0
    for frame in frames
  )
  duration_seconds = (
    frame_count * estimates.seconds_per_frame
    + exposure_seconds
    + stage_position_count * estimates.seconds_per_stage_position
    + autofocus_count * estimates.seconds_per_autofocus
  )
  calibration = config.calibration
  storage_bytes = (
    frame_count
    * calibration.image_width_pixels
    * calibration.image_height_pixels
    * estimates.bytes_per_pixel
  )
  return ScanPlan(
    spec=spec,
    configuration_fingerprint=_configuration_fingerprint(config),
    blocks=tuple(blocks),
    frames=tuple(frames),
    estimate_model=estimates,
    frame_count=frame_count,
    stage_position_count=stage_position_count,
    autofocus_count=autofocus_count,
    sampled_area_mm2=_union_area(footprints),
    estimated_duration=timedelta(seconds=duration_seconds),
    estimated_storage_bytes=storage_bytes,
  )


def _normalize_captures(
  channel: Optional[str],
  exposure_ms: Optional[float],
  gain: Optional[float],
  captures: Optional[Sequence[Capture]],
) -> Tuple[Capture, ...]:
  if captures is not None:
    if channel is not None or exposure_ms is not None or gain is not None:
      raise ValueError("captures cannot be combined with channel, exposure_ms, or gain")
    normalized = tuple(captures)
    if not normalized or any(not isinstance(capture, Capture) for capture in normalized):
      raise ValueError("captures must contain at least one Capture")
    return normalized
  if channel is None:
    raise ValueError("provide channel or captures")
  return (Capture(channel=channel, exposure_ms=exposure_ms, gain=gain),)


def _normalize_centers(centers_mm: Sequence[CoordinateMM]) -> Tuple[CoordinateMM, ...]:
  if isinstance(centers_mm, (str, bytes)):
    raise ValueError("centers_mm must be a sequence of (x, y) coordinates")
  try:
    centers = tuple((float(x), float(y)) for x, y in centers_mm)
  except (TypeError, ValueError) as exc:
    raise ValueError("centers_mm must contain (x, y) coordinates") from exc
  if not centers:
    raise ValueError("centers_mm must contain at least one coordinate")
  for index, (x_mm, y_mm) in enumerate(centers):
    _validate_finite(x_mm, f"centers_mm[{index}].x")
    _validate_finite(y_mm, f"centers_mm[{index}].y")
  return centers


def _point_geometry(
  centers_mm: Sequence[CoordinateMM],
  block_shape: BlockShape,
  labels: Optional[Sequence[str]] = None,
) -> _PointGeometry:
  centers = _normalize_centers(centers_mm)
  shape = _validate_block_shape(block_shape)
  if labels is None:
    normalized_labels: Tuple[Optional[str], ...] = (None,) * len(centers)
  else:
    values = tuple(labels)
    if len(values) != len(centers):
      raise ValueError("labels must have the same length as centers_mm")
    if any(not isinstance(label, str) or not label for label in values):
      raise ValueError("labels must contain non-empty strings")
    normalized_labels = tuple(values)
  return _PointGeometry(
    centers_mm=centers,
    labels=normalized_labels,
    block_shape=shape,
  )


def _capture_geometry(config: CeligoConfig, capture: Capture) -> _CaptureGeometry:
  try:
    channel = config.channels[capture.channel]
  except KeyError as exc:
    available = ", ".join(sorted(config.channels)) or "none"
    raise ValueError(
      f"Unknown channel {capture.channel!r}; available channels: {available}"
    ) from exc

  x_correction = channel.mm_per_pixel_x_correction_to_brightfield
  y_correction = channel.mm_per_pixel_y_correction_to_brightfield
  for value, name in (
    (x_correction, f"{capture.channel} X pixel-scale correction"),
    (y_correction, f"{capture.channel} Y pixel-scale correction"),
  ):
    _validate_finite(value, name)
    if value <= 0:
      raise ValueError(f"{name} must be positive")

  calibration = config.calibration
  base_step_x_mm, base_step_y_mm = effective_fov_mm(
    calibration,
    config.navigation,
  )
  for value, name in (
    (base_step_x_mm, "frame X step"),
    (base_step_y_mm, "frame Y step"),
  ):
    _validate_finite(value, name)
    if value <= 0:
      raise ValueError(f"{name} must be positive")
  geometry = _CaptureGeometry(
    frame_x_mm=(
      calibration.image_width_pixels * calibration.microns_per_pixel_x / 1000.0 * x_correction
    ),
    frame_y_mm=(
      calibration.image_height_pixels * calibration.microns_per_pixel_y / 1000.0 * y_correction
    ),
    step_x_mm=base_step_x_mm * x_correction,
    step_y_mm=base_step_y_mm * y_correction,
    max_block_columns=max(
      1,
      math.floor(2 * config.navigation.max_galvo_deflection_x_mm / (base_step_x_mm * x_correction)),
    ),
    max_block_rows=max(
      1,
      math.floor(2 * config.navigation.max_galvo_deflection_y_mm / (base_step_y_mm * y_correction)),
    ),
  )
  for value, name in (
    (geometry.frame_x_mm, "frame width"),
    (geometry.frame_y_mm, "frame height"),
    (geometry.step_x_mm, "frame X step"),
    (geometry.step_y_mm, "frame Y step"),
  ):
    _validate_finite(value, name)
    if value <= 0:
      raise ValueError(f"{name} must be positive")
  return geometry


def _build_layouts(
  spec_geometry: _SpecGeometry,
  capture_geometries: Tuple[_CaptureGeometry, ...],
) -> List[_BlockLayout]:
  if isinstance(spec_geometry, _PointGeometry):
    return _point_layouts(spec_geometry, capture_geometries)
  if isinstance(spec_geometry, _RandomGeometry):
    return _random_layouts(spec_geometry, capture_geometries)
  return _full_coverage_layouts(spec_geometry, capture_geometries)


def _point_layouts(
  geometry: _PointGeometry,
  capture_geometries: Tuple[_CaptureGeometry, ...],
) -> List[_BlockLayout]:
  _validate_shape_for_captures(geometry.block_shape, capture_geometries)
  return [
    _BlockLayout(
      selections=tuple(
        _selection_from_center(x_mm, y_mm, geometry.block_shape, capture_geometry)
        for capture_geometry in capture_geometries
      ),
      label=geometry.labels[index],
      bounds=None,
    )
    for index, (x_mm, y_mm) in enumerate(geometry.centers_mm)
  ]


def _random_layouts(
  geometry: _RandomGeometry,
  capture_geometries: Tuple[_CaptureGeometry, ...],
) -> List[_BlockLayout]:
  _validate_shape_for_captures(geometry.block_shape, capture_geometries)
  block_width_mm = max(item.block_size_mm(geometry.block_shape)[0] for item in capture_geometries)
  block_height_mm = max(item.block_size_mm(geometry.block_shape)[1] for item in capture_geometries)
  if geometry.non_overlapping:
    x_candidates = _non_overlapping_block_centers(
      geometry.bounds.left,
      geometry.bounds.right,
      block_width_mm,
    )
    y_candidates = _non_overlapping_block_centers(
      geometry.bounds.top,
      geometry.bounds.bottom,
      block_height_mm,
    )
  else:
    x_candidates = _candidate_block_centers(
      geometry.bounds.left,
      geometry.bounds.right,
      block_width_mm,
      min(item.step_x_mm for item in capture_geometries),
    )
    y_candidates = _candidate_block_centers(
      geometry.bounds.top,
      geometry.bounds.bottom,
      block_height_mm,
      min(item.step_y_mm for item in capture_geometries),
    )
  candidates = [(x_mm, y_mm) for y_mm in y_candidates for x_mm in x_candidates]
  if geometry.count > len(candidates):
    qualifier = " non-overlapping" if geometry.non_overlapping else ""
    raise ValueError(
      f"Requested {geometry.count}{qualifier} blocks, but only {len(candidates)} "
      "fit within the scan bounds"
    )
  selected = random.Random(geometry.seed).sample(candidates, geometry.count)
  ordered = _shortest_travel_order(selected)
  return [
    _BlockLayout(
      selections=tuple(
        _selection_from_center(x_mm, y_mm, geometry.block_shape, capture_geometry)
        for capture_geometry in capture_geometries
      ),
      label=None,
      bounds=geometry.bounds,
    )
    for x_mm, y_mm in ordered
  ]


def _shortest_travel_order(points: Sequence[CoordinateMM]) -> List[CoordinateMM]:
  """Order selected points along a short open path without changing the selection."""
  if len(points) <= 1:
    return list(points)
  distances = [[math.dist(start, end) for end in points] for start in points]
  if len(points) <= _EXACT_ROUTE_LIMIT:
    order = _exact_open_path(distances)
  else:
    order = _heuristic_open_path(points, distances)
  return [points[index] for index in order]


def _exact_open_path(distances: Sequence[Sequence[float]]) -> List[int]:
  point_count = len(distances)
  state_count = 1 << point_count
  costs = [[math.inf] * point_count for _ in range(state_count)]
  parents = [[-1] * point_count for _ in range(state_count)]
  for index in range(point_count):
    costs[1 << index][index] = 0.0

  for mask in range(1, state_count):
    for end in range(point_count):
      end_bit = 1 << end
      if mask & end_bit == 0:
        continue
      previous_mask = mask ^ end_bit
      if previous_mask == 0:
        continue
      best_cost = math.inf
      best_previous = -1
      for previous in range(point_count):
        if previous_mask & (1 << previous) == 0:
          continue
        cost = costs[previous_mask][previous] + distances[previous][end]
        if cost < best_cost:
          best_cost = cost
          best_previous = previous
      costs[mask][end] = best_cost
      parents[mask][end] = best_previous

  mask = state_count - 1
  end = min(range(point_count), key=lambda index: (costs[mask][index], index))
  reversed_order = []
  while end >= 0:
    reversed_order.append(end)
    previous = parents[mask][end]
    mask ^= 1 << end
    end = previous
  return list(reversed(reversed_order))


def _heuristic_open_path(
  points: Sequence[CoordinateMM],
  distances: Sequence[Sequence[float]],
) -> List[int]:
  point_count = len(points)
  starts = {
    0,
    min(range(point_count), key=lambda index: points[index][0]),
    max(range(point_count), key=lambda index: points[index][0]),
    min(range(point_count), key=lambda index: points[index][1]),
    max(range(point_count), key=lambda index: points[index][1]),
  }
  routes = [_nearest_neighbor_path(start, distances) for start in starts]
  order = min(routes, key=lambda route: _path_length(route, distances))
  return _improve_open_path(order, distances)


def _nearest_neighbor_path(
  start: int,
  distances: Sequence[Sequence[float]],
) -> List[int]:
  unvisited = set(range(len(distances)))
  unvisited.remove(start)
  order = [start]
  while unvisited:
    current = order[-1]
    next_index = min(unvisited, key=lambda index: (distances[current][index], index))
    order.append(next_index)
    unvisited.remove(next_index)
  return order


def _improve_open_path(
  order: List[int],
  distances: Sequence[Sequence[float]],
) -> List[int]:
  improved = True
  while improved:
    improved = False
    for start in range(len(order) - 1):
      for end in range(start + 1, len(order)):
        current = 0.0
        replacement = 0.0
        if start > 0:
          current += distances[order[start - 1]][order[start]]
          replacement += distances[order[start - 1]][order[end]]
        if end + 1 < len(order):
          current += distances[order[end]][order[end + 1]]
          replacement += distances[order[start]][order[end + 1]]
        if replacement + 1e-12 < current:
          order[start : end + 1] = reversed(order[start : end + 1])
          improved = True
  return order


def _path_length(
  order: Sequence[int],
  distances: Sequence[Sequence[float]],
) -> float:
  return sum(distances[start][end] for start, end in zip(order, order[1:]))


def _full_coverage_layouts(
  geometry: _FullCoverageGeometry,
  capture_geometries: Tuple[_CaptureGeometry, ...],
) -> List[_BlockLayout]:
  x_counts = [
    len(
      _coverage_axis_centers(
        geometry.bounds.left,
        geometry.bounds.right,
        item.frame_x_mm,
        item.step_x_mm,
      )
    )
    for item in capture_geometries
  ]
  y_counts = [
    len(
      _coverage_axis_centers(
        geometry.bounds.top,
        geometry.bounds.bottom,
        item.frame_y_mm,
        item.step_y_mm,
      )
    )
    for item in capture_geometries
  ]
  column_count = max(x_counts)
  row_count = max(y_counts)
  x_centers_by_capture = tuple(
    _coverage_axis_centers_with_count(
      geometry.bounds.left,
      geometry.bounds.right,
      item.frame_x_mm,
      column_count,
    )
    for item in capture_geometries
  )
  y_centers_by_capture = tuple(
    _coverage_axis_centers_with_count(
      geometry.bounds.top,
      geometry.bounds.bottom,
      item.frame_y_mm,
      row_count,
    )
    for item in capture_geometries
  )

  columns_per_block = min(item.max_block_columns for item in capture_geometries)
  rows_per_block = min(item.max_block_rows for item in capture_geometries)
  x_groups = _chunks(tuple(range(column_count)), columns_per_block)
  y_groups = _chunks(tuple(range(row_count)), rows_per_block)
  layouts: List[_BlockLayout] = []
  for block_row, y_indices in enumerate(y_groups):
    x_group_indices: Sequence[int]
    if block_row % 2 == 0:
      x_group_indices = range(len(x_groups))
    else:
      x_group_indices = range(len(x_groups) - 1, -1, -1)
    for x_group_index in x_group_indices:
      x_indices = x_groups[x_group_index]
      layouts.append(
        _BlockLayout(
          selections=tuple(
            _BlockSelection(
              x_centers_mm=tuple(x_centers_by_capture[capture_index][index] for index in x_indices),
              y_centers_mm=tuple(y_centers_by_capture[capture_index][index] for index in y_indices),
            )
            for capture_index in range(len(capture_geometries))
          ),
          label=None,
          bounds=geometry.bounds,
        )
      )
  return layouts


def _validate_shape_for_captures(
  block_shape: BlockShape,
  capture_geometries: Sequence[_CaptureGeometry],
) -> None:
  columns, rows = _validate_block_shape(block_shape)
  for geometry in capture_geometries:
    if columns > geometry.max_block_columns or rows > geometry.max_block_rows:
      raise ValueError(
        f"Requested {columns}x{rows} block_shape, but the calibrated galvo limit is "
        f"{geometry.max_block_columns}x{geometry.max_block_rows}"
      )


def _compile_block(
  *,
  config: CeligoConfig,
  coordinate_systems: CoordinateSystems,
  captures: Tuple[Capture, ...],
  capture_geometries: Tuple[_CaptureGeometry, ...],
  block_index: int,
  first_position_index: int,
  layout: _BlockLayout,
) -> Tuple[
  ScanBlock,
  List[Tuple[ScanPosition, Capture]],
  List[ScanRegion],
  int,
]:
  selection_bounds = tuple(
    _selection_bounds(selection, geometry)
    for selection, geometry in zip(layout.selections, capture_geometries)
  )
  if layout.bounds is not None:
    for bounds in selection_bounds:
      if not _contains(layout.bounds, bounds):
        raise ValueError(f"Planned block {block_index} extends outside the scan bounds")

  all_x_centers = [center for selection in layout.selections for center in selection.x_centers_mm]
  all_y_centers = [center for selection in layout.selections for center in selection.y_centers_mm]
  center_x_mm = (min(all_x_centers) + max(all_x_centers)) / 2.0
  center_y_mm = (min(all_y_centers) + max(all_y_centers)) / 2.0
  stage_x_mm, stage_y_mm = coordinate_systems.sample_mm_to_stage_mm(
    center_x_mm,
    center_y_mm,
  )
  _validate_stage_target(config, block_index, stage_x_mm, stage_y_mm)

  combined_bounds = ScanRegion(
    left=min(bounds.left for bounds in selection_bounds),
    top=min(bounds.top for bounds in selection_bounds),
    right=max(bounds.right for bounds in selection_bounds),
    bottom=max(bounds.bottom for bounds in selection_bounds),
  )
  first_selection = layout.selections[0]
  block = ScanBlock(
    index=block_index,
    center_x_mm=center_x_mm,
    center_y_mm=center_y_mm,
    stage_x_mm=stage_x_mm,
    stage_y_mm=stage_y_mm,
    bounds=combined_bounds,
    block_shape=(
      len(first_selection.x_centers_mm),
      len(first_selection.y_centers_mm),
    ),
    label=layout.label,
  )

  positions: List[Tuple[ScanPosition, Capture]] = []
  footprints: List[ScanRegion] = []
  position_index = first_position_index
  for capture, capture_geometry, selection in zip(
    captures,
    capture_geometries,
    layout.selections,
  ):
    for tile_row, sample_y_mm in enumerate(selection.y_centers_mm):
      columns: Sequence[int]
      if tile_row % 2 == 0:
        columns = range(len(selection.x_centers_mm))
      else:
        columns = range(len(selection.x_centers_mm) - 1, -1, -1)
      for tile_column in columns:
        sample_x_mm = selection.x_centers_mm[tile_column]
        offset_x_mm = sample_x_mm - center_x_mm
        offset_y_mm = sample_y_mm - center_y_mm
        if abs(offset_x_mm) > config.navigation.max_galvo_deflection_x_mm + 1e-9:
          raise ValueError(f"Block {block_index} exceeds calibrated X galvo reach")
        if abs(offset_y_mm) > config.navigation.max_galvo_deflection_y_mm + 1e-9:
          raise ValueError(f"Block {block_index} exceeds calibrated Y galvo reach")
        position = ScanPosition(
          index=position_index,
          block_index=block_index,
          tile_row=tile_row,
          tile_column=tile_column,
          sample_x_mm=sample_x_mm,
          sample_y_mm=sample_y_mm,
          galvo_offset_x_mm=offset_x_mm,
          galvo_offset_y_mm=offset_y_mm,
        )
        positions.append((position, capture))
        footprints.append(
          ScanRegion(
            left=sample_x_mm - capture_geometry.frame_x_mm / 2.0,
            top=sample_y_mm - capture_geometry.frame_y_mm / 2.0,
            right=sample_x_mm + capture_geometry.frame_x_mm / 2.0,
            bottom=sample_y_mm + capture_geometry.frame_y_mm / 2.0,
          )
        )
        position_index += 1
  return block, positions, footprints, position_index


def _validate_stage_target(
  config: CeligoConfig,
  block_index: int,
  stage_x_mm: float,
  stage_y_mm: float,
) -> None:
  x_axis = config.hardware.x_axis
  y_axis = config.hardware.y_axis
  if x_axis is not None and not x_axis.min_position <= stage_x_mm <= x_axis.max_position:
    raise ValueError(
      f"Block {block_index} X stage target {stage_x_mm:g} mm is outside "
      f"{x_axis.min_position:g}..{x_axis.max_position:g} mm"
    )
  if y_axis is not None and not y_axis.min_position <= stage_y_mm <= y_axis.max_position:
    raise ValueError(
      f"Block {block_index} Y stage target {stage_y_mm:g} mm is outside "
      f"{y_axis.min_position:g}..{y_axis.max_position:g} mm"
    )


def _selection_from_center(
  center_x_mm: float,
  center_y_mm: float,
  block_shape: BlockShape,
  geometry: _CaptureGeometry,
) -> _BlockSelection:
  columns, rows = block_shape
  first_x_mm = center_x_mm - (columns - 1) * geometry.step_x_mm / 2.0
  first_y_mm = center_y_mm - (rows - 1) * geometry.step_y_mm / 2.0
  return _BlockSelection(
    x_centers_mm=tuple(first_x_mm + index * geometry.step_x_mm for index in range(columns)),
    y_centers_mm=tuple(first_y_mm + index * geometry.step_y_mm for index in range(rows)),
  )


def _selection_bounds(
  selection: _BlockSelection,
  geometry: _CaptureGeometry,
) -> ScanRegion:
  return ScanRegion(
    left=selection.x_centers_mm[0] - geometry.frame_x_mm / 2.0,
    top=selection.y_centers_mm[0] - geometry.frame_y_mm / 2.0,
    right=selection.x_centers_mm[-1] + geometry.frame_x_mm / 2.0,
    bottom=selection.y_centers_mm[-1] + geometry.frame_y_mm / 2.0,
  )


def _coverage_axis_centers(
  start_mm: float,
  end_mm: float,
  frame_mm: float,
  maximum_step_mm: float,
) -> Tuple[float, ...]:
  minimum_center = start_mm + frame_mm / 2.0
  maximum_center = end_mm - frame_mm / 2.0
  center_span = maximum_center - minimum_center
  if center_span < -1e-9:
    raise ValueError(
      f"Scan bound length {end_mm - start_mm:g} mm is smaller than the {frame_mm:g} mm camera frame"
    )
  if center_span <= 1e-9:
    return ((start_mm + end_mm) / 2.0,)
  interval_count = max(1, math.ceil(center_span / maximum_step_mm - 1e-12))
  return _coverage_axis_centers_with_count(
    start_mm,
    end_mm,
    frame_mm,
    interval_count + 1,
  )


def _coverage_axis_centers_with_count(
  start_mm: float,
  end_mm: float,
  frame_mm: float,
  count: int,
) -> Tuple[float, ...]:
  minimum_center = start_mm + frame_mm / 2.0
  maximum_center = end_mm - frame_mm / 2.0
  if maximum_center < minimum_center - 1e-9:
    raise ValueError(
      f"Scan bound length {end_mm - start_mm:g} mm is smaller than the {frame_mm:g} mm camera frame"
    )
  if count == 1:
    return ((start_mm + end_mm) / 2.0,)
  step_mm = (maximum_center - minimum_center) / (count - 1)
  centers = [minimum_center + index * step_mm for index in range(count)]
  centers[-1] = maximum_center
  return tuple(centers)


def _chunks(values: Tuple[int, ...], size: int) -> List[Tuple[int, ...]]:
  return [values[start : start + size] for start in range(0, len(values), size)]


def _non_overlapping_block_centers(
  start_mm: float,
  end_mm: float,
  block_mm: float,
) -> Tuple[float, ...]:
  available_mm = end_mm - start_mm
  if block_mm > available_mm + 1e-9:
    raise ValueError(
      f"Imaging block length {block_mm:g} mm does not fit within scan bound "
      f"length {available_mm:g} mm"
    )
  count = max(1, math.floor(available_mm / block_mm + 1e-12))
  occupied_mm = count * block_mm
  first_center_mm = start_mm + (available_mm - occupied_mm) / 2.0 + block_mm / 2.0
  return tuple(first_center_mm + index * block_mm for index in range(count))


def _candidate_block_centers(
  start_mm: float,
  end_mm: float,
  block_mm: float,
  step_mm: float,
) -> Tuple[float, ...]:
  minimum_center = start_mm + block_mm / 2.0
  maximum_center = end_mm - block_mm / 2.0
  if maximum_center < minimum_center - 1e-9:
    raise ValueError(
      f"Imaging block length {block_mm:g} mm does not fit within scan bound "
      f"length {end_mm - start_mm:g} mm"
    )
  if maximum_center <= minimum_center + 1e-9:
    return ((start_mm + end_mm) / 2.0,)
  count = math.floor((maximum_center - minimum_center) / step_mm + 1e-12) + 1
  candidates = [minimum_center + index * step_mm for index in range(count)]
  if maximum_center - candidates[-1] > 1e-9:
    candidates.append(maximum_center)
  return tuple(candidates)


def _contains(outer: ScanRegion, inner: ScanRegion) -> bool:
  tolerance = 1e-9
  return (
    inner.left >= outer.left - tolerance
    and inner.top >= outer.top - tolerance
    and inner.right <= outer.right + tolerance
    and inner.bottom <= outer.bottom + tolerance
  )


def _union_area(regions: Sequence[ScanRegion]) -> float:
  if not regions:
    return 0.0
  x_edges = sorted({edge for region in regions for edge in (region.left, region.right)})
  area = 0.0
  for left, right in zip(x_edges, x_edges[1:]):
    intervals = sorted(
      (region.top, region.bottom)
      for region in regions
      if region.left < right and region.right > left
    )
    covered_y = 0.0
    if intervals:
      current_top, current_bottom = intervals[0]
      for top, bottom in intervals[1:]:
        if top <= current_bottom:
          current_bottom = max(current_bottom, bottom)
        else:
          covered_y += current_bottom - current_top
          current_top, current_bottom = top, bottom
      covered_y += current_bottom - current_top
    area += (right - left) * covered_y
  return area


def _describe_geometry(geometry: _SpecGeometry) -> str:
  if isinstance(geometry, _PointGeometry):
    columns, rows = geometry.block_shape
    kind = "wells" if any(label is not None for label in geometry.labels) else "points"
    return f"{kind}(count={len(geometry.centers_mm)}, block_shape={columns}x{rows})"
  if isinstance(geometry, _RandomGeometry):
    columns, rows = geometry.block_shape
    return (
      f"random(count={geometry.count}, block_shape={columns}x{rows}, "
      f"seed={geometry.seed}, non_overlapping={geometry.non_overlapping})"
    )
  return (
    f"full_coverage(X={geometry.bounds.left:g}..{geometry.bounds.right:g} mm, "
    f"Y={geometry.bounds.top:g}..{geometry.bounds.bottom:g} mm)"
  )


def _format_duration(duration: timedelta) -> str:
  seconds = max(0, round(duration.total_seconds()))
  hours, remainder = divmod(seconds, 3600)
  minutes, seconds = divmod(remainder, 60)
  parts = []
  if hours:
    parts.append(f"{hours}h")
  if minutes or hours:
    parts.append(f"{minutes}m")
  parts.append(f"{seconds}s")
  return " ".join(parts)


def _format_bytes(byte_count: int) -> str:
  units = ("B", "kB", "MB", "GB", "TB")
  value = float(byte_count)
  unit = units[0]
  for unit in units:
    if value < 1000.0 or unit == units[-1]:
      break
    value /= 1000.0
  return f"{value:.3g} {unit}"
