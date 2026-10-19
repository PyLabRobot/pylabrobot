import datetime

from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import duration_pb2 as _duration_pb2
from pylabrobot.rpc.v1 import device_pb2 as _device_pb2
from pylabrobot.rpc.v1 import jobs_pb2 as _jobs_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AutofocusMethod(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AUTOFOCUS_NONE: _ClassVar[AutofocusMethod]
    AUTOFOCUS_IMAGE: _ClassVar[AutofocusMethod]
AUTOFOCUS_NONE: AutofocusMethod
AUTOFOCUS_IMAGE: AutofocusMethod

class InitializationRequest(_message.Message):
    __slots__ = ("device_id", "request_id", "configuration_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    request_id: str
    configuration_id: str
    def __init__(self, device_id: _Optional[str] = ..., request_id: _Optional[str] = ..., configuration_id: _Optional[str] = ...) -> None: ...

class BrightfieldSettings(_message.Message):
    __slots__ = ("sample_x_mm", "sample_y_mm", "z_mm", "exposure_ms", "gain", "machine_auto_exposure")
    SAMPLE_X_MM_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_Y_MM_FIELD_NUMBER: _ClassVar[int]
    Z_MM_FIELD_NUMBER: _ClassVar[int]
    EXPOSURE_MS_FIELD_NUMBER: _ClassVar[int]
    GAIN_FIELD_NUMBER: _ClassVar[int]
    MACHINE_AUTO_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    sample_x_mm: float
    sample_y_mm: float
    z_mm: float
    exposure_ms: float
    gain: float
    machine_auto_exposure: bool
    def __init__(self, sample_x_mm: _Optional[float] = ..., sample_y_mm: _Optional[float] = ..., z_mm: _Optional[float] = ..., exposure_ms: _Optional[float] = ..., gain: _Optional[float] = ..., machine_auto_exposure: _Optional[bool] = ...) -> None: ...

class BrightfieldRequest(_message.Message):
    __slots__ = ("device_id", "request_id", "configuration_id", "settings")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    request_id: str
    configuration_id: str
    settings: BrightfieldSettings
    def __init__(self, device_id: _Optional[str] = ..., request_id: _Optional[str] = ..., configuration_id: _Optional[str] = ..., settings: _Optional[_Union[BrightfieldSettings, _Mapping]] = ...) -> None: ...

class CloseDoorRequest(_message.Message):
    __slots__ = ("device_id", "sample_x_mm", "sample_y_mm")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_X_MM_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_Y_MM_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    sample_x_mm: float
    sample_y_mm: float
    def __init__(self, device_id: _Optional[str] = ..., sample_x_mm: _Optional[float] = ..., sample_y_mm: _Optional[float] = ...) -> None: ...

class CeligoJobSpecification(_message.Message):
    __slots__ = ("capture", "width", "height", "scan_plan")
    CAPTURE_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    SCAN_PLAN_FIELD_NUMBER: _ClassVar[int]
    capture: BrightfieldSettings
    width: int
    height: int
    scan_plan: ScanPlan
    def __init__(self, capture: _Optional[_Union[BrightfieldSettings, _Mapping]] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., scan_plan: _Optional[_Union[ScanPlan, _Mapping]] = ...) -> None: ...

class CeligoConfiguration(_message.Message):
    __slots__ = ("configuration_id", "width", "height", "channels", "calibrated_brightfield_z_mm", "minimum_z_mm", "maximum_z_mm", "capture_contract", "maximum_exposure_ms", "maximum_gain", "default_scan_exposure_ms", "default_scan_gain", "autofocus_methods", "auto_exposure_supported")
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    CHANNELS_FIELD_NUMBER: _ClassVar[int]
    CALIBRATED_BRIGHTFIELD_Z_MM_FIELD_NUMBER: _ClassVar[int]
    MINIMUM_Z_MM_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_Z_MM_FIELD_NUMBER: _ClassVar[int]
    CAPTURE_CONTRACT_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_EXPOSURE_MS_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_GAIN_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_SCAN_EXPOSURE_MS_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_SCAN_GAIN_FIELD_NUMBER: _ClassVar[int]
    AUTOFOCUS_METHODS_FIELD_NUMBER: _ClassVar[int]
    AUTO_EXPOSURE_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    configuration_id: str
    width: int
    height: int
    channels: _containers.RepeatedScalarFieldContainer[str]
    calibrated_brightfield_z_mm: float
    minimum_z_mm: float
    maximum_z_mm: float
    capture_contract: str
    maximum_exposure_ms: float
    maximum_gain: float
    default_scan_exposure_ms: float
    default_scan_gain: float
    autofocus_methods: _containers.RepeatedScalarFieldContainer[AutofocusMethod]
    auto_exposure_supported: bool
    def __init__(self, configuration_id: _Optional[str] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., channels: _Optional[_Iterable[str]] = ..., calibrated_brightfield_z_mm: _Optional[float] = ..., minimum_z_mm: _Optional[float] = ..., maximum_z_mm: _Optional[float] = ..., capture_contract: _Optional[str] = ..., maximum_exposure_ms: _Optional[float] = ..., maximum_gain: _Optional[float] = ..., default_scan_exposure_ms: _Optional[float] = ..., default_scan_gain: _Optional[float] = ..., autofocus_methods: _Optional[_Iterable[_Union[AutofocusMethod, str]]] = ..., auto_exposure_supported: _Optional[bool] = ...) -> None: ...

class AcquisitionMetadata(_message.Message):
    __slots__ = ("channel", "exposure_ms", "gain", "sample_x_mm", "sample_y_mm", "z_mm", "focus_method", "stage_x_mm", "stage_y_mm", "galvo_x_volts", "galvo_y_volts", "configuration_id", "planned_frame_index", "block_index", "tile_row", "tile_column", "label", "focus_score", "machine_auto_exposure")
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    EXPOSURE_MS_FIELD_NUMBER: _ClassVar[int]
    GAIN_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_X_MM_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_Y_MM_FIELD_NUMBER: _ClassVar[int]
    Z_MM_FIELD_NUMBER: _ClassVar[int]
    FOCUS_METHOD_FIELD_NUMBER: _ClassVar[int]
    STAGE_X_MM_FIELD_NUMBER: _ClassVar[int]
    STAGE_Y_MM_FIELD_NUMBER: _ClassVar[int]
    GALVO_X_VOLTS_FIELD_NUMBER: _ClassVar[int]
    GALVO_Y_VOLTS_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    PLANNED_FRAME_INDEX_FIELD_NUMBER: _ClassVar[int]
    BLOCK_INDEX_FIELD_NUMBER: _ClassVar[int]
    TILE_ROW_FIELD_NUMBER: _ClassVar[int]
    TILE_COLUMN_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    FOCUS_SCORE_FIELD_NUMBER: _ClassVar[int]
    MACHINE_AUTO_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    channel: str
    exposure_ms: float
    gain: float
    sample_x_mm: float
    sample_y_mm: float
    z_mm: float
    focus_method: str
    stage_x_mm: float
    stage_y_mm: float
    galvo_x_volts: float
    galvo_y_volts: float
    configuration_id: str
    planned_frame_index: int
    block_index: int
    tile_row: int
    tile_column: int
    label: str
    focus_score: float
    machine_auto_exposure: bool
    def __init__(self, channel: _Optional[str] = ..., exposure_ms: _Optional[float] = ..., gain: _Optional[float] = ..., sample_x_mm: _Optional[float] = ..., sample_y_mm: _Optional[float] = ..., z_mm: _Optional[float] = ..., focus_method: _Optional[str] = ..., stage_x_mm: _Optional[float] = ..., stage_y_mm: _Optional[float] = ..., galvo_x_volts: _Optional[float] = ..., galvo_y_volts: _Optional[float] = ..., configuration_id: _Optional[str] = ..., planned_frame_index: _Optional[int] = ..., block_index: _Optional[int] = ..., tile_row: _Optional[int] = ..., tile_column: _Optional[int] = ..., label: _Optional[str] = ..., focus_score: _Optional[float] = ..., machine_auto_exposure: _Optional[bool] = ...) -> None: ...

class ScanRegion(_message.Message):
    __slots__ = ("left_mm", "top_mm", "right_mm", "bottom_mm")
    LEFT_MM_FIELD_NUMBER: _ClassVar[int]
    TOP_MM_FIELD_NUMBER: _ClassVar[int]
    RIGHT_MM_FIELD_NUMBER: _ClassVar[int]
    BOTTOM_MM_FIELD_NUMBER: _ClassVar[int]
    left_mm: float
    top_mm: float
    right_mm: float
    bottom_mm: float
    def __init__(self, left_mm: _Optional[float] = ..., top_mm: _Optional[float] = ..., right_mm: _Optional[float] = ..., bottom_mm: _Optional[float] = ...) -> None: ...

class SamplePoint(_message.Message):
    __slots__ = ("x_mm", "y_mm", "label")
    X_MM_FIELD_NUMBER: _ClassVar[int]
    Y_MM_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    x_mm: float
    y_mm: float
    label: str
    def __init__(self, x_mm: _Optional[float] = ..., y_mm: _Optional[float] = ..., label: _Optional[str] = ...) -> None: ...

class PointGeometry(_message.Message):
    __slots__ = ("centers", "block_columns", "block_rows")
    CENTERS_FIELD_NUMBER: _ClassVar[int]
    BLOCK_COLUMNS_FIELD_NUMBER: _ClassVar[int]
    BLOCK_ROWS_FIELD_NUMBER: _ClassVar[int]
    centers: _containers.RepeatedCompositeFieldContainer[SamplePoint]
    block_columns: int
    block_rows: int
    def __init__(self, centers: _Optional[_Iterable[_Union[SamplePoint, _Mapping]]] = ..., block_columns: _Optional[int] = ..., block_rows: _Optional[int] = ...) -> None: ...

class RandomGeometry(_message.Message):
    __slots__ = ("bounds", "count", "block_columns", "block_rows", "seed", "non_overlapping")
    BOUNDS_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    BLOCK_COLUMNS_FIELD_NUMBER: _ClassVar[int]
    BLOCK_ROWS_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    NON_OVERLAPPING_FIELD_NUMBER: _ClassVar[int]
    bounds: ScanRegion
    count: int
    block_columns: int
    block_rows: int
    seed: int
    non_overlapping: bool
    def __init__(self, bounds: _Optional[_Union[ScanRegion, _Mapping]] = ..., count: _Optional[int] = ..., block_columns: _Optional[int] = ..., block_rows: _Optional[int] = ..., seed: _Optional[int] = ..., non_overlapping: _Optional[bool] = ...) -> None: ...

class CaptureSettings(_message.Message):
    __slots__ = ("channel", "exposure_ms", "gain", "machine_auto_exposure")
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    EXPOSURE_MS_FIELD_NUMBER: _ClassVar[int]
    GAIN_FIELD_NUMBER: _ClassVar[int]
    MACHINE_AUTO_EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    channel: str
    exposure_ms: float
    gain: float
    machine_auto_exposure: bool
    def __init__(self, channel: _Optional[str] = ..., exposure_ms: _Optional[float] = ..., gain: _Optional[float] = ..., machine_auto_exposure: _Optional[bool] = ...) -> None: ...

class ScanSpecification(_message.Message):
    __slots__ = ("points", "random", "full_coverage", "captures", "autofocus")
    POINTS_FIELD_NUMBER: _ClassVar[int]
    RANDOM_FIELD_NUMBER: _ClassVar[int]
    FULL_COVERAGE_FIELD_NUMBER: _ClassVar[int]
    CAPTURES_FIELD_NUMBER: _ClassVar[int]
    AUTOFOCUS_FIELD_NUMBER: _ClassVar[int]
    points: PointGeometry
    random: RandomGeometry
    full_coverage: ScanRegion
    captures: _containers.RepeatedCompositeFieldContainer[CaptureSettings]
    autofocus: AutofocusMethod
    def __init__(self, points: _Optional[_Union[PointGeometry, _Mapping]] = ..., random: _Optional[_Union[RandomGeometry, _Mapping]] = ..., full_coverage: _Optional[_Union[ScanRegion, _Mapping]] = ..., captures: _Optional[_Iterable[_Union[CaptureSettings, _Mapping]]] = ..., autofocus: _Optional[_Union[AutofocusMethod, str]] = ...) -> None: ...

class ScanEstimateModel(_message.Message):
    __slots__ = ("seconds_per_frame", "seconds_per_stage_position", "seconds_per_autofocus", "bytes_per_pixel")
    SECONDS_PER_FRAME_FIELD_NUMBER: _ClassVar[int]
    SECONDS_PER_STAGE_POSITION_FIELD_NUMBER: _ClassVar[int]
    SECONDS_PER_AUTOFOCUS_FIELD_NUMBER: _ClassVar[int]
    BYTES_PER_PIXEL_FIELD_NUMBER: _ClassVar[int]
    seconds_per_frame: float
    seconds_per_stage_position: float
    seconds_per_autofocus: float
    bytes_per_pixel: int
    def __init__(self, seconds_per_frame: _Optional[float] = ..., seconds_per_stage_position: _Optional[float] = ..., seconds_per_autofocus: _Optional[float] = ..., bytes_per_pixel: _Optional[int] = ...) -> None: ...

class ScanBlock(_message.Message):
    __slots__ = ("index", "center_x_mm", "center_y_mm", "stage_x_mm", "stage_y_mm", "bounds", "block_columns", "block_rows", "label")
    INDEX_FIELD_NUMBER: _ClassVar[int]
    CENTER_X_MM_FIELD_NUMBER: _ClassVar[int]
    CENTER_Y_MM_FIELD_NUMBER: _ClassVar[int]
    STAGE_X_MM_FIELD_NUMBER: _ClassVar[int]
    STAGE_Y_MM_FIELD_NUMBER: _ClassVar[int]
    BOUNDS_FIELD_NUMBER: _ClassVar[int]
    BLOCK_COLUMNS_FIELD_NUMBER: _ClassVar[int]
    BLOCK_ROWS_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    index: int
    center_x_mm: float
    center_y_mm: float
    stage_x_mm: float
    stage_y_mm: float
    bounds: ScanRegion
    block_columns: int
    block_rows: int
    label: str
    def __init__(self, index: _Optional[int] = ..., center_x_mm: _Optional[float] = ..., center_y_mm: _Optional[float] = ..., stage_x_mm: _Optional[float] = ..., stage_y_mm: _Optional[float] = ..., bounds: _Optional[_Union[ScanRegion, _Mapping]] = ..., block_columns: _Optional[int] = ..., block_rows: _Optional[int] = ..., label: _Optional[str] = ...) -> None: ...

class ScanPosition(_message.Message):
    __slots__ = ("index", "block_index", "tile_row", "tile_column", "sample_x_mm", "sample_y_mm", "galvo_offset_x_mm", "galvo_offset_y_mm")
    INDEX_FIELD_NUMBER: _ClassVar[int]
    BLOCK_INDEX_FIELD_NUMBER: _ClassVar[int]
    TILE_ROW_FIELD_NUMBER: _ClassVar[int]
    TILE_COLUMN_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_X_MM_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_Y_MM_FIELD_NUMBER: _ClassVar[int]
    GALVO_OFFSET_X_MM_FIELD_NUMBER: _ClassVar[int]
    GALVO_OFFSET_Y_MM_FIELD_NUMBER: _ClassVar[int]
    index: int
    block_index: int
    tile_row: int
    tile_column: int
    sample_x_mm: float
    sample_y_mm: float
    galvo_offset_x_mm: float
    galvo_offset_y_mm: float
    def __init__(self, index: _Optional[int] = ..., block_index: _Optional[int] = ..., tile_row: _Optional[int] = ..., tile_column: _Optional[int] = ..., sample_x_mm: _Optional[float] = ..., sample_y_mm: _Optional[float] = ..., galvo_offset_x_mm: _Optional[float] = ..., galvo_offset_y_mm: _Optional[float] = ...) -> None: ...

class PlannedFrame(_message.Message):
    __slots__ = ("index", "block_index", "position", "capture")
    INDEX_FIELD_NUMBER: _ClassVar[int]
    BLOCK_INDEX_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    CAPTURE_FIELD_NUMBER: _ClassVar[int]
    index: int
    block_index: int
    position: ScanPosition
    capture: CaptureSettings
    def __init__(self, index: _Optional[int] = ..., block_index: _Optional[int] = ..., position: _Optional[_Union[ScanPosition, _Mapping]] = ..., capture: _Optional[_Union[CaptureSettings, _Mapping]] = ...) -> None: ...

class ScanPlan(_message.Message):
    __slots__ = ("specification", "configuration_fingerprint", "blocks", "frames", "estimate_model", "frame_count", "stage_position_count", "autofocus_count", "sampled_area_mm2", "estimated_duration", "estimated_storage_bytes")
    SPECIFICATION_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_FINGERPRINT_FIELD_NUMBER: _ClassVar[int]
    BLOCKS_FIELD_NUMBER: _ClassVar[int]
    FRAMES_FIELD_NUMBER: _ClassVar[int]
    ESTIMATE_MODEL_FIELD_NUMBER: _ClassVar[int]
    FRAME_COUNT_FIELD_NUMBER: _ClassVar[int]
    STAGE_POSITION_COUNT_FIELD_NUMBER: _ClassVar[int]
    AUTOFOCUS_COUNT_FIELD_NUMBER: _ClassVar[int]
    SAMPLED_AREA_MM2_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_DURATION_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_STORAGE_BYTES_FIELD_NUMBER: _ClassVar[int]
    specification: ScanSpecification
    configuration_fingerprint: str
    blocks: _containers.RepeatedCompositeFieldContainer[ScanBlock]
    frames: _containers.RepeatedCompositeFieldContainer[PlannedFrame]
    estimate_model: ScanEstimateModel
    frame_count: int
    stage_position_count: int
    autofocus_count: int
    sampled_area_mm2: float
    estimated_duration: _duration_pb2.Duration
    estimated_storage_bytes: int
    def __init__(self, specification: _Optional[_Union[ScanSpecification, _Mapping]] = ..., configuration_fingerprint: _Optional[str] = ..., blocks: _Optional[_Iterable[_Union[ScanBlock, _Mapping]]] = ..., frames: _Optional[_Iterable[_Union[PlannedFrame, _Mapping]]] = ..., estimate_model: _Optional[_Union[ScanEstimateModel, _Mapping]] = ..., frame_count: _Optional[int] = ..., stage_position_count: _Optional[int] = ..., autofocus_count: _Optional[int] = ..., sampled_area_mm2: _Optional[float] = ..., estimated_duration: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., estimated_storage_bytes: _Optional[int] = ...) -> None: ...

class BuildScanPlanRequest(_message.Message):
    __slots__ = ("device_id", "specification", "estimate_model")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    SPECIFICATION_FIELD_NUMBER: _ClassVar[int]
    ESTIMATE_MODEL_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    specification: ScanSpecification
    estimate_model: ScanEstimateModel
    def __init__(self, device_id: _Optional[str] = ..., specification: _Optional[_Union[ScanSpecification, _Mapping]] = ..., estimate_model: _Optional[_Union[ScanEstimateModel, _Mapping]] = ...) -> None: ...

class CreateScanRequest(_message.Message):
    __slots__ = ("device_id", "request_id", "configuration_id", "plan")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    request_id: str
    configuration_id: str
    plan: ScanPlan
    def __init__(self, device_id: _Optional[str] = ..., request_id: _Optional[str] = ..., configuration_id: _Optional[str] = ..., plan: _Optional[_Union[ScanPlan, _Mapping]] = ...) -> None: ...
