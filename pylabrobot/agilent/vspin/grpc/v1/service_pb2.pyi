from google.protobuf import empty_pb2 as _empty_pb2
from pylabrobot.rpc.v1 import device_pb2 as _device_pb2
from pylabrobot.rpc.v1 import jobs_pb2 as _jobs_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class VSpinConfiguration(_message.Message):
    __slots__ = ("configuration_id", "minimum_relative_centrifugal_force", "maximum_relative_centrifugal_force", "minimum_duration_seconds")
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    MINIMUM_RELATIVE_CENTRIFUGAL_FORCE_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_RELATIVE_CENTRIFUGAL_FORCE_FIELD_NUMBER: _ClassVar[int]
    MINIMUM_DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    configuration_id: str
    minimum_relative_centrifugal_force: float
    maximum_relative_centrifugal_force: float
    minimum_duration_seconds: float
    def __init__(self, configuration_id: _Optional[str] = ..., minimum_relative_centrifugal_force: _Optional[float] = ..., maximum_relative_centrifugal_force: _Optional[float] = ..., minimum_duration_seconds: _Optional[float] = ...) -> None: ...

class InitializationRequest(_message.Message):
    __slots__ = ("device_id", "request_id", "configuration_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    request_id: str
    configuration_id: str
    def __init__(self, device_id: _Optional[str] = ..., request_id: _Optional[str] = ..., configuration_id: _Optional[str] = ...) -> None: ...

class SpinSettings(_message.Message):
    __slots__ = ("relative_centrifugal_force", "duration_seconds", "acceleration_fraction", "deceleration_fraction")
    RELATIVE_CENTRIFUGAL_FORCE_FIELD_NUMBER: _ClassVar[int]
    DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    ACCELERATION_FRACTION_FIELD_NUMBER: _ClassVar[int]
    DECELERATION_FRACTION_FIELD_NUMBER: _ClassVar[int]
    relative_centrifugal_force: float
    duration_seconds: float
    acceleration_fraction: float
    deceleration_fraction: float
    def __init__(self, relative_centrifugal_force: _Optional[float] = ..., duration_seconds: _Optional[float] = ..., acceleration_fraction: _Optional[float] = ..., deceleration_fraction: _Optional[float] = ...) -> None: ...

class SpinRequest(_message.Message):
    __slots__ = ("device_id", "request_id", "configuration_id", "settings")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ID_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    request_id: str
    configuration_id: str
    settings: SpinSettings
    def __init__(self, device_id: _Optional[str] = ..., request_id: _Optional[str] = ..., configuration_id: _Optional[str] = ..., settings: _Optional[_Union[SpinSettings, _Mapping]] = ...) -> None: ...
