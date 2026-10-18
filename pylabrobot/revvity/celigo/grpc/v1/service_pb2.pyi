from pylabrobot.rpc.v1 import device_pb2 as _device_pb2
from pylabrobot.rpc.v1 import jobs_pb2 as _jobs_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

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
