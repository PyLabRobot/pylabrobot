import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from pylabrobot.rpc.v1 import jobs_pb2 as _jobs_pb2
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as _service_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

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
    job: _jobs_pb2.JobRef
    request_id: str
    kind: str
    state: _jobs_pb2.JobState
    outcome: _jobs_pb2.Outcome
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
    def __init__(self, job: _Optional[_Union[_jobs_pb2.JobRef, _Mapping]] = ..., request_id: _Optional[str] = ..., kind: _Optional[str] = ..., state: _Optional[_Union[_jobs_pb2.JobState, str]] = ..., outcome: _Optional[_Union[_jobs_pb2.Outcome, str]] = ..., phase: _Optional[str] = ..., cancellation_requested: _Optional[bool] = ..., completed: _Optional[int] = ..., total: _Optional[int] = ..., progress_unit: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., error: _Optional[str] = ..., revision: _Optional[int] = ..., configuration_id: _Optional[str] = ..., images_released: _Optional[bool] = ..., state_durable: _Optional[bool] = ...) -> None: ...

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
    job: _jobs_pb2.JobRef
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
    metadata: _service_pb2.AcquisitionMetadata
    def __init__(self, job: _Optional[_Union[_jobs_pb2.JobRef, _Mapping]] = ..., frame_id: _Optional[str] = ..., sequence: _Optional[int] = ..., representation: _Optional[str] = ..., captured_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., pixel_format: _Optional[str] = ..., encoding: _Optional[str] = ..., image: _Optional[bytes] = ..., sha256: _Optional[str] = ..., metadata: _Optional[_Union[_service_pb2.AcquisitionMetadata, _Mapping]] = ...) -> None: ...
