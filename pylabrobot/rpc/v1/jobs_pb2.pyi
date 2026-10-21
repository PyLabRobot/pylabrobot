import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

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

class JobRef(_message.Message):
    __slots__ = ("device_id", "job_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    job_id: str
    def __init__(self, device_id: _Optional[str] = ..., job_id: _Optional[str] = ...) -> None: ...

class RequestLookup(_message.Message):
    __slots__ = ("device_id", "request_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    request_id: str
    def __init__(self, device_id: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class JobStatus(_message.Message):
    __slots__ = ("job", "request_id", "kind", "state", "outcome", "phase", "cancellation_requested", "completed", "total", "progress_unit", "created_at", "updated_at", "error", "revision", "configuration_id", "state_durable")
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
    state_durable: bool
    def __init__(self, job: _Optional[_Union[JobRef, _Mapping]] = ..., request_id: _Optional[str] = ..., kind: _Optional[str] = ..., state: _Optional[_Union[JobState, str]] = ..., outcome: _Optional[_Union[Outcome, str]] = ..., phase: _Optional[str] = ..., cancellation_requested: _Optional[bool] = ..., completed: _Optional[int] = ..., total: _Optional[int] = ..., progress_unit: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., error: _Optional[str] = ..., revision: _Optional[int] = ..., configuration_id: _Optional[str] = ..., state_durable: _Optional[bool] = ...) -> None: ...
