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
OPERATIONAL_STATE_UNSPECIFIED: OperationalState
OPERATIONAL_STATE_READY: OperationalState
OPERATIONAL_STATE_BUSY: OperationalState
OPERATIONAL_STATE_UNINITIALIZED: OperationalState
OPERATIONAL_STATE_INITIALIZING: OperationalState
OPERATIONAL_STATE_FAULTED: OperationalState

class DeviceRequest(_message.Message):
    __slots__ = ("device_id",)
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    def __init__(self, device_id: _Optional[str] = ...) -> None: ...

class DeviceInfo(_message.Message):
    __slots__ = ("device_id", "display_name", "device_type", "api_version", "configuration_id", "supported_operations", "services", "manufacturer", "model", "implementation_version", "instance_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DEVICE_TYPE_FIELD_NUMBER: _ClassVar[int]
    API_VERSION_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_OPERATIONS_FIELD_NUMBER: _ClassVar[int]
    SERVICES_FIELD_NUMBER: _ClassVar[int]
    MANUFACTURER_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTATION_VERSION_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    display_name: str
    device_type: str
    api_version: str
    configuration_id: str
    supported_operations: _containers.RepeatedScalarFieldContainer[str]
    services: _containers.RepeatedScalarFieldContainer[str]
    manufacturer: str
    model: str
    implementation_version: str
    instance_id: str
    def __init__(self, device_id: _Optional[str] = ..., display_name: _Optional[str] = ..., device_type: _Optional[str] = ..., api_version: _Optional[str] = ..., configuration_id: _Optional[str] = ..., supported_operations: _Optional[_Iterable[str]] = ..., services: _Optional[_Iterable[str]] = ..., manufacturer: _Optional[str] = ..., model: _Optional[str] = ..., implementation_version: _Optional[str] = ..., instance_id: _Optional[str] = ...) -> None: ...

class DeviceStatus(_message.Message):
    __slots__ = ("device_id", "state", "active_job_id", "observed_at", "revision", "fault_summary", "instance_id", "observation_valid")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_JOB_ID_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_AT_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    FAULT_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    OBSERVATION_VALID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    state: OperationalState
    active_job_id: str
    observed_at: _timestamp_pb2.Timestamp
    revision: int
    fault_summary: str
    instance_id: str
    observation_valid: bool
    def __init__(self, device_id: _Optional[str] = ..., state: _Optional[_Union[OperationalState, str]] = ..., active_job_id: _Optional[str] = ..., observed_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., revision: _Optional[int] = ..., fault_summary: _Optional[str] = ..., instance_id: _Optional[str] = ..., observation_valid: _Optional[bool] = ...) -> None: ...
