import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OperationalState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OPERATIONAL_STATE_UNSPECIFIED: _ClassVar[OperationalState]
    OPERATIONAL_STATE_READY: _ClassVar[OperationalState]
    OPERATIONAL_STATE_BUSY: _ClassVar[OperationalState]
    OPERATIONAL_STATE_UNINITIALIZED: _ClassVar[OperationalState]
    OPERATIONAL_STATE_INITIALIZING: _ClassVar[OperationalState]
    OPERATIONAL_STATE_FAULTED: _ClassVar[OperationalState]

class JobState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    JOB_STATE_UNSPECIFIED: _ClassVar[JobState]
    JOB_STATE_READY: _ClassVar[JobState]
    JOB_STATE_RUNNING: _ClassVar[JobState]
    JOB_STATE_FINISHED: _ClassVar[JobState]

class Outcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OUTCOME_UNSPECIFIED: _ClassVar[Outcome]
    OUTCOME_PENDING: _ClassVar[Outcome]
    OUTCOME_SUCCEEDED: _ClassVar[Outcome]
    OUTCOME_CANCELLED: _ClassVar[Outcome]
    OUTCOME_FAILED: _ClassVar[Outcome]
    OUTCOME_INTERRUPTED: _ClassVar[Outcome]
OPERATIONAL_STATE_UNSPECIFIED: OperationalState
OPERATIONAL_STATE_READY: OperationalState
OPERATIONAL_STATE_BUSY: OperationalState
OPERATIONAL_STATE_UNINITIALIZED: OperationalState
OPERATIONAL_STATE_INITIALIZING: OperationalState
OPERATIONAL_STATE_FAULTED: OperationalState
JOB_STATE_UNSPECIFIED: JobState
JOB_STATE_READY: JobState
JOB_STATE_RUNNING: JobState
JOB_STATE_FINISHED: JobState
OUTCOME_UNSPECIFIED: Outcome
OUTCOME_PENDING: Outcome
OUTCOME_SUCCEEDED: Outcome
OUTCOME_CANCELLED: Outcome
OUTCOME_FAILED: Outcome
OUTCOME_INTERRUPTED: Outcome

class SimulationSpec(_message.Message):
    __slots__ = ("frame_count", "width", "height", "bit_depth", "interval_ms")
    FRAME_COUNT_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    BIT_DEPTH_FIELD_NUMBER: _ClassVar[int]
    INTERVAL_MS_FIELD_NUMBER: _ClassVar[int]
    frame_count: int
    width: int
    height: int
    bit_depth: int
    interval_ms: int
    def __init__(self, frame_count: _Optional[int] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., bit_depth: _Optional[int] = ..., interval_ms: _Optional[int] = ...) -> None: ...

class DeviceRequest(_message.Message):
    __slots__ = ("device_id",)
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    def __init__(self, device_id: _Optional[str] = ...) -> None: ...

class DeviceInfo(_message.Message):
    __slots__ = ("device_id", "display_name", "device_type", "api_version", "maximum_frame_bytes", "maximum_pixels", "retention_bytes", "configuration_id", "supported_operations")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DEVICE_TYPE_FIELD_NUMBER: _ClassVar[int]
    API_VERSION_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_FRAME_BYTES_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_PIXELS_FIELD_NUMBER: _ClassVar[int]
    RETENTION_BYTES_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_OPERATIONS_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    display_name: str
    device_type: str
    api_version: str
    maximum_frame_bytes: int
    maximum_pixels: int
    retention_bytes: int
    configuration_id: str
    supported_operations: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, device_id: _Optional[str] = ..., display_name: _Optional[str] = ..., device_type: _Optional[str] = ..., api_version: _Optional[str] = ..., maximum_frame_bytes: _Optional[int] = ..., maximum_pixels: _Optional[int] = ..., retention_bytes: _Optional[int] = ..., configuration_id: _Optional[str] = ..., supported_operations: _Optional[_Iterable[str]] = ...) -> None: ...

class DeviceStatus(_message.Message):
    __slots__ = ("device_id", "state", "active_job_id", "observed_at", "revision", "fault_summary")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_JOB_ID_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_AT_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    FAULT_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    state: OperationalState
    active_job_id: str
    observed_at: _timestamp_pb2.Timestamp
    revision: int
    fault_summary: str
    def __init__(self, device_id: _Optional[str] = ..., state: _Optional[_Union[OperationalState, str]] = ..., active_job_id: _Optional[str] = ..., observed_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., revision: _Optional[int] = ..., fault_summary: _Optional[str] = ...) -> None: ...

class CreateRequest(_message.Message):
    __slots__ = ("device_id", "request_id", "specification", "configuration_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SPECIFICATION_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    request_id: str
    specification: SimulationSpec
    configuration_id: str
    def __init__(self, device_id: _Optional[str] = ..., request_id: _Optional[str] = ..., specification: _Optional[_Union[SimulationSpec, _Mapping]] = ..., configuration_id: _Optional[str] = ...) -> None: ...

class RequestLookup(_message.Message):
    __slots__ = ("device_id", "request_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    request_id: str
    def __init__(self, device_id: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

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
    __slots__ = ("sample_x_mm", "sample_y_mm", "z_mm", "exposure_ms", "gain")
    SAMPLE_X_MM_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_Y_MM_FIELD_NUMBER: _ClassVar[int]
    Z_MM_FIELD_NUMBER: _ClassVar[int]
    EXPOSURE_MS_FIELD_NUMBER: _ClassVar[int]
    GAIN_FIELD_NUMBER: _ClassVar[int]
    sample_x_mm: float
    sample_y_mm: float
    z_mm: float
    exposure_ms: float
    gain: float
    def __init__(self, sample_x_mm: _Optional[float] = ..., sample_y_mm: _Optional[float] = ..., z_mm: _Optional[float] = ..., exposure_ms: _Optional[float] = ..., gain: _Optional[float] = ...) -> None: ...

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

class CeligoJobSpecification(_message.Message):
    __slots__ = ("capture", "width", "height")
    CAPTURE_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    capture: BrightfieldSettings
    width: int
    height: int
    def __init__(self, capture: _Optional[_Union[BrightfieldSettings, _Mapping]] = ..., width: _Optional[int] = ..., height: _Optional[int] = ...) -> None: ...

class CeligoConfiguration(_message.Message):
    __slots__ = ("configuration_id", "width", "height", "channels", "calibrated_brightfield_z_mm", "minimum_z_mm", "maximum_z_mm", "capture_contract", "maximum_exposure_ms", "maximum_gain")
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
    def __init__(self, configuration_id: _Optional[str] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., channels: _Optional[_Iterable[str]] = ..., calibrated_brightfield_z_mm: _Optional[float] = ..., minimum_z_mm: _Optional[float] = ..., maximum_z_mm: _Optional[float] = ..., capture_contract: _Optional[str] = ..., maximum_exposure_ms: _Optional[float] = ..., maximum_gain: _Optional[float] = ...) -> None: ...

class JobRef(_message.Message):
    __slots__ = ("device_id", "job_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    job_id: str
    def __init__(self, device_id: _Optional[str] = ..., job_id: _Optional[str] = ...) -> None: ...

class JobStatus(_message.Message):
    __slots__ = ("job", "request_id", "kind", "state", "outcome", "phase", "cancellation_requested", "completed", "total", "progress_unit", "created_at", "updated_at", "error", "revision", "configuration_id", "images_released", "state_durable")
    JOB_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    CANCELLATION_REQUESTED_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_UNIT_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    IMAGES_RELEASED_FIELD_NUMBER: _ClassVar[int]
    STATE_DURABLE_FIELD_NUMBER: _ClassVar[int]
    job: JobRef
    request_id: str
    kind: str
    state: JobState
    outcome: Outcome
    phase: str
    cancellation_requested: bool
    completed: int
    total: int
    progress_unit: str
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    error: str
    revision: int
    configuration_id: str
    images_released: bool
    state_durable: bool
    def __init__(self, job: _Optional[_Union[JobRef, _Mapping]] = ..., request_id: _Optional[str] = ..., kind: _Optional[str] = ..., state: _Optional[_Union[JobState, str]] = ..., outcome: _Optional[_Union[Outcome, str]] = ..., phase: _Optional[str] = ..., cancellation_requested: _Optional[bool] = ..., completed: _Optional[int] = ..., total: _Optional[int] = ..., progress_unit: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., error: _Optional[str] = ..., revision: _Optional[int] = ..., configuration_id: _Optional[str] = ..., images_released: _Optional[bool] = ..., state_durable: _Optional[bool] = ...) -> None: ...

class ListFramesRequest(_message.Message):
    __slots__ = ("job", "after_sequence", "limit")
    JOB_FIELD_NUMBER: _ClassVar[int]
    AFTER_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    job: JobRef
    after_sequence: int
    limit: int
    def __init__(self, job: _Optional[_Union[JobRef, _Mapping]] = ..., after_sequence: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class FrameInfo(_message.Message):
    __slots__ = ("frame_id", "sequence", "byte_length", "sha256", "available")
    FRAME_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    BYTE_LENGTH_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    frame_id: str
    sequence: int
    byte_length: int
    sha256: str
    available: bool
    def __init__(self, frame_id: _Optional[str] = ..., sequence: _Optional[int] = ..., byte_length: _Optional[int] = ..., sha256: _Optional[str] = ..., available: _Optional[bool] = ...) -> None: ...

class FramePage(_message.Message):
    __slots__ = ("frames",)
    FRAMES_FIELD_NUMBER: _ClassVar[int]
    frames: _containers.RepeatedCompositeFieldContainer[FrameInfo]
    def __init__(self, frames: _Optional[_Iterable[_Union[FrameInfo, _Mapping]]] = ...) -> None: ...

class GetFrameRequest(_message.Message):
    __slots__ = ("job", "frame_id")
    JOB_FIELD_NUMBER: _ClassVar[int]
    FRAME_ID_FIELD_NUMBER: _ClassVar[int]
    job: JobRef
    frame_id: str
    def __init__(self, job: _Optional[_Union[JobRef, _Mapping]] = ..., frame_id: _Optional[str] = ...) -> None: ...

class AcquisitionMetadata(_message.Message):
    __slots__ = ("channel", "exposure_ms", "gain", "sample_x_mm", "sample_y_mm", "z_mm", "focus_method", "stage_x_mm", "stage_y_mm", "galvo_x_volts", "galvo_y_volts", "configuration_id")
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
    def __init__(self, channel: _Optional[str] = ..., exposure_ms: _Optional[float] = ..., gain: _Optional[float] = ..., sample_x_mm: _Optional[float] = ..., sample_y_mm: _Optional[float] = ..., z_mm: _Optional[float] = ..., focus_method: _Optional[str] = ..., stage_x_mm: _Optional[float] = ..., stage_y_mm: _Optional[float] = ..., galvo_x_volts: _Optional[float] = ..., galvo_y_volts: _Optional[float] = ..., configuration_id: _Optional[str] = ...) -> None: ...

class ImageFrame(_message.Message):
    __slots__ = ("job", "frame_id", "sequence", "representation", "captured_at", "width", "height", "pixel_format", "encoding", "image", "sha256", "metadata")
    JOB_FIELD_NUMBER: _ClassVar[int]
    FRAME_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    REPRESENTATION_FIELD_NUMBER: _ClassVar[int]
    CAPTURED_AT_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    PIXEL_FORMAT_FIELD_NUMBER: _ClassVar[int]
    ENCODING_FIELD_NUMBER: _ClassVar[int]
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    job: JobRef
    frame_id: str
    sequence: int
    representation: str
    captured_at: _timestamp_pb2.Timestamp
    width: int
    height: int
    pixel_format: str
    encoding: str
    image: bytes
    sha256: str
    metadata: AcquisitionMetadata
    def __init__(self, job: _Optional[_Union[JobRef, _Mapping]] = ..., frame_id: _Optional[str] = ..., sequence: _Optional[int] = ..., representation: _Optional[str] = ..., captured_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., pixel_format: _Optional[str] = ..., encoding: _Optional[str] = ..., image: _Optional[bytes] = ..., sha256: _Optional[str] = ..., metadata: _Optional[_Union[AcquisitionMetadata, _Mapping]] = ...) -> None: ...
