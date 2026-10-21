"""Translate the existing Celigo scan types to and from their protobuf contract."""

from dataclasses import asdict, replace

from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.scan import (
  AutofocusMethod,
  Capture,
  FullCoverageGeometry,
  PlannedFrame,
  PointGeometry,
  RandomGeometry,
  ScanBlock,
  ScanEstimateModel,
  ScanPlan,
  ScanPosition,
  ScanRegion,
  ScanSpec,
)


def encode_region(region: ScanRegion) -> pb.ScanRegion:
  """Encode sample-relative bounds with explicit wire units."""
  return pb.ScanRegion(
    left_mm=region.left, top_mm=region.top, right_mm=region.right, bottom_mm=region.bottom
  )


def decode_region(value: pb.ScanRegion) -> ScanRegion:
  """Validate and decode physical bounds."""
  return ScanRegion(value.left_mm, value.top_mm, value.right_mm, value.bottom_mm)


def encode_capture(capture: Capture) -> pb.CaptureSettings:
  """Preserve omitted camera settings for resolution by the planning service."""
  return pb.CaptureSettings(
    channel=capture.channel,
    exposure_ms=capture.exposure_ms,
    gain=capture.gain,
    machine_auto_exposure=capture.machine_auto_exposure,
  )


def decode_capture(value: pb.CaptureSettings) -> Capture:
  """Decode capture settings without interpreting omitted values as zero."""
  return Capture(
    channel=value.channel,
    exposure_ms=value.exposure_ms if value.HasField("exposure_ms") else None,
    gain=value.gain if value.HasField("gain") else None,
    machine_auto_exposure=value.machine_auto_exposure,
  )


def encode_scan_specification(spec: ScanSpec) -> pb.ScanSpecification:
  """Encode geometry, capture order, and focus policy without retaining a plate."""
  value = pb.ScanSpecification(
    captures=[encode_capture(capture) for capture in spec.captures],
    autofocus=pb.AUTOFOCUS_IMAGE if spec.autofocus == "image" else pb.AUTOFOCUS_NONE,
    initial_brightfield_z_mm=spec.initial_brightfield_z_mm,
  )
  geometry = spec.geometry
  if isinstance(geometry, PointGeometry):
    value.points.CopyFrom(
      pb.PointGeometry(
        centers=[
          pb.SamplePoint(x_mm=x, y_mm=y, label=label)
          for (x, y), label in zip(geometry.centers_mm, geometry.labels)
        ],
        block_columns=geometry.block_shape[0],
        block_rows=geometry.block_shape[1],
      )
    )
  elif isinstance(geometry, RandomGeometry):
    value.random.CopyFrom(
      pb.RandomGeometry(
        bounds=encode_region(geometry.bounds),
        count=geometry.count,
        block_columns=geometry.block_shape[0],
        block_rows=geometry.block_shape[1],
        seed=geometry.seed,
        non_overlapping=geometry.non_overlapping,
      )
    )
  elif isinstance(geometry, FullCoverageGeometry):
    value.full_coverage.CopyFrom(encode_region(geometry.bounds))
  else:
    raise ValueError("unsupported scan geometry")
  return value


def decode_scan_specification(value: pb.ScanSpecification) -> ScanSpec:
  """Construct validated PLR scan geometry from the transport representation."""
  if value.autofocus not in (pb.AUTOFOCUS_NONE, pb.AUTOFOCUS_IMAGE):
    raise ValueError("unsupported autofocus method")
  autofocus: AutofocusMethod | None = "image" if value.autofocus == pb.AUTOFOCUS_IMAGE else None
  captures = tuple(decode_capture(capture) for capture in value.captures)
  initial_brightfield_z_mm = (
    value.initial_brightfield_z_mm if value.HasField("initial_brightfield_z_mm") else None
  )
  geometry = value.WhichOneof("geometry")
  if geometry == "points":
    spec = ScanSpec.points(
      [(point.x_mm, point.y_mm) for point in value.points.centers],
      block_shape=(value.points.block_columns, value.points.block_rows),
      captures=captures,
      autofocus=autofocus,
      initial_brightfield_z_mm=initial_brightfield_z_mm,
    )
    labels = tuple(
      point.label if point.HasField("label") else None for point in value.points.centers
    )
    if any(label is not None and not label.strip() for label in labels):
      raise ValueError("point labels must be non-empty when provided")
    assert isinstance(spec.geometry, PointGeometry)
    return replace(spec, geometry=replace(spec.geometry, labels=labels))
  if geometry == "random":
    return ScanSpec.random(
      decode_region(value.random.bounds),
      count=value.random.count,
      block_shape=(value.random.block_columns, value.random.block_rows),
      seed=value.random.seed,
      non_overlapping=(
        value.random.non_overlapping if value.random.HasField("non_overlapping") else True
      ),
      captures=captures,
      autofocus=autofocus,
      initial_brightfield_z_mm=initial_brightfield_z_mm,
    )
  if geometry == "full_coverage":
    return ScanSpec.full_coverage(
      decode_region(value.full_coverage),
      captures=captures,
      autofocus=autofocus,
      initial_brightfield_z_mm=initial_brightfield_z_mm,
    )
  raise ValueError("scan geometry is required")


def encode_estimate_model(model: ScanEstimateModel) -> pb.ScanEstimateModel:
  """Encode the user's throughput and pixel-storage assumptions."""
  return pb.ScanEstimateModel(**asdict(model))


def decode_estimate_model(value: pb.ScanEstimateModel) -> ScanEstimateModel:
  """Apply PLR defaults only to omitted estimate fields."""
  defaults = ScanEstimateModel()
  return ScanEstimateModel(
    seconds_per_frame=(
      value.seconds_per_frame if value.HasField("seconds_per_frame") else defaults.seconds_per_frame
    ),
    seconds_per_stage_position=(
      value.seconds_per_stage_position
      if value.HasField("seconds_per_stage_position")
      else defaults.seconds_per_stage_position
    ),
    seconds_per_autofocus=(
      value.seconds_per_autofocus
      if value.HasField("seconds_per_autofocus")
      else defaults.seconds_per_autofocus
    ),
    bytes_per_pixel=(
      value.bytes_per_pixel if value.HasField("bytes_per_pixel") else defaults.bytes_per_pixel
    ),
  )


def encode_scan_plan(plan: ScanPlan) -> pb.ScanPlan:
  """Encode the full inspected plan, including ordered blocks and frame operations."""
  value = pb.ScanPlan(
    specification=encode_scan_specification(plan.spec),
    configuration_fingerprint=plan.configuration_fingerprint,
    blocks=[
      pb.ScanBlock(
        index=block.index,
        center_x_mm=block.center_x_mm,
        center_y_mm=block.center_y_mm,
        stage_x_mm=block.stage_x_mm,
        stage_y_mm=block.stage_y_mm,
        bounds=encode_region(block.bounds),
        block_columns=block.block_shape[0],
        block_rows=block.block_shape[1],
        label=block.label,
      )
      for block in plan.blocks
    ],
    frames=[
      pb.PlannedFrame(
        index=frame.index,
        block_index=frame.block.index,
        position=pb.ScanPosition(**asdict(frame.position)),
        capture=encode_capture(frame.capture),
      )
      for frame in plan.frames
    ],
    estimate_model=encode_estimate_model(plan.estimate_model),
    frame_count=plan.frame_count,
    stage_position_count=plan.stage_position_count,
    autofocus_count=plan.autofocus_count,
    sampled_area_mm2=plan.sampled_area_mm2,
    estimated_storage_bytes=plan.estimated_storage_bytes,
  )
  value.estimated_duration.FromTimedelta(plan.estimated_duration)
  return value


def decode_scan_plan(value: pb.ScanPlan) -> ScanPlan:
  """Reconstruct shared PLR plan objects and reject broken block/frame references."""
  blocks = tuple(
    ScanBlock(
      index=block.index,
      center_x_mm=block.center_x_mm,
      center_y_mm=block.center_y_mm,
      stage_x_mm=block.stage_x_mm,
      stage_y_mm=block.stage_y_mm,
      bounds=decode_region(block.bounds),
      block_shape=(block.block_columns, block.block_rows),
      label=block.label if block.HasField("label") else None,
    )
    for block in value.blocks
  )
  if any(block.index != index for index, block in enumerate(blocks)):
    raise ValueError("scan block indices must be consecutive")
  frames = []
  for index, frame in enumerate(value.frames):
    if frame.index != index or frame.block_index >= len(blocks):
      raise ValueError("scan frame indices or block references are invalid")
    position = frame.position
    if position.block_index != frame.block_index:
      raise ValueError("scan position belongs to a different block")
    frames.append(
      PlannedFrame(
        index=frame.index,
        block=blocks[frame.block_index],
        position=ScanPosition(
          index=position.index,
          block_index=position.block_index,
          tile_row=position.tile_row,
          tile_column=position.tile_column,
          sample_x_mm=position.sample_x_mm,
          sample_y_mm=position.sample_y_mm,
          galvo_offset_x_mm=position.galvo_offset_x_mm,
          galvo_offset_y_mm=position.galvo_offset_y_mm,
        ),
        capture=decode_capture(frame.capture),
      )
    )
  return ScanPlan(
    spec=decode_scan_specification(value.specification),
    configuration_fingerprint=value.configuration_fingerprint,
    blocks=blocks,
    frames=tuple(frames),
    estimate_model=decode_estimate_model(value.estimate_model),
    frame_count=value.frame_count,
    stage_position_count=value.stage_position_count,
    autofocus_count=value.autofocus_count,
    sampled_area_mm2=value.sampled_area_mm2,
    estimated_duration=value.estimated_duration.ToTimedelta(),
    estimated_storage_bytes=value.estimated_storage_bytes,
  )
