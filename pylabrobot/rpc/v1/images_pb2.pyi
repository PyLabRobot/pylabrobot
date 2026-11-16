import datetime

from google.protobuf import any_pb2 as _any_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from pylabrobot.rpc.v1 import device_pb2 as _device_pb2
from pylabrobot.rpc.v1 import jobs_pb2 as _jobs_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ImageCapabilities(_message.Message):
    __slots__ = ("maximum_frame_bytes", "maximum_pixels", "retention_bytes", "pixel_formats", "encodings")
    MAXIMUM_FRAME_BYTES_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_PIXELS_FIELD_NUMBER: _ClassVar[int]
    RETENTION_BYTES_FIELD_NUMBER: _ClassVar[int]
    PIXEL_FORMATS_FIELD_NUMBER: _ClassVar[int]
    ENCODINGS_FIELD_NUMBER: _ClassVar[int]
    maximum_frame_bytes: int
    maximum_pixels: int
    retention_bytes: int
    pixel_formats: _containers.RepeatedScalarFieldContainer[str]
    encodings: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, maximum_frame_bytes: _Optional[int] = ..., maximum_pixels: _Optional[int] = ..., retention_bytes: _Optional[int] = ..., pixel_formats: _Optional[_Iterable[str]] = ..., encodings: _Optional[_Iterable[str]] = ...) -> None: ...

class ImageRetention(_message.Message):
    __slots__ = ("job", "images_released")
    JOB_FIELD_NUMBER: _ClassVar[int]
    IMAGES_RELEASED_FIELD_NUMBER: _ClassVar[int]
    job: _jobs_pb2.JobRef
    images_released: bool
    def __init__(self, job: _Optional[_Union[_jobs_pb2.JobRef, _Mapping]] = ..., images_released: _Optional[bool] = ...) -> None: ...

class ListFramesRequest(_message.Message):
    __slots__ = ("job", "after_sequence", "limit")
    JOB_FIELD_NUMBER: _ClassVar[int]
    AFTER_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    job: _jobs_pb2.JobRef
    after_sequence: int
    limit: int
    def __init__(self, job: _Optional[_Union[_jobs_pb2.JobRef, _Mapping]] = ..., after_sequence: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

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
    job: _jobs_pb2.JobRef
    frame_id: str
    def __init__(self, job: _Optional[_Union[_jobs_pb2.JobRef, _Mapping]] = ..., frame_id: _Optional[str] = ...) -> None: ...

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
    metadata: _any_pb2.Any
    def __init__(self, job: _Optional[_Union[_jobs_pb2.JobRef, _Mapping]] = ..., frame_id: _Optional[str] = ..., sequence: _Optional[int] = ..., representation: _Optional[str] = ..., captured_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., width: _Optional[int] = ..., height: _Optional[int] = ..., pixel_format: _Optional[str] = ..., encoding: _Optional[str] = ..., image: _Optional[bytes] = ..., sha256: _Optional[str] = ..., metadata: _Optional[_Union[_any_pb2.Any, _Mapping]] = ...) -> None: ...
