from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import (
  ClassVar as _ClassVar,
  Mapping as _Mapping,
  Optional as _Optional,
  Union as _Union,
)

DESCRIPTOR: _descriptor.FileDescriptor

class Empty(_message.Message):
  __slots__ = ()
  def __init__(self) -> None: ...

class PlateSnapshot(_message.Message):
  __slots__ = ("name", "resource_json")
  NAME_FIELD_NUMBER: _ClassVar[int]
  RESOURCE_JSON_FIELD_NUMBER: _ClassVar[int]
  name: str
  resource_json: str
  def __init__(self, name: _Optional[str] = ..., resource_json: _Optional[str] = ...) -> None: ...

class ActiveTransfer(_message.Message):
  __slots__ = ("transfer_id", "plate_name")
  TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
  PLATE_NAME_FIELD_NUMBER: _ClassVar[int]
  transfer_id: str
  plate_name: str
  def __init__(
    self, transfer_id: _Optional[str] = ..., plate_name: _Optional[str] = ...
  ) -> None: ...

class ActiveOperation(_message.Message):
  __slots__ = ("name", "plate_name")
  NAME_FIELD_NUMBER: _ClassVar[int]
  PLATE_NAME_FIELD_NUMBER: _ClassVar[int]
  name: str
  plate_name: str
  def __init__(self, name: _Optional[str] = ..., plate_name: _Optional[str] = ...) -> None: ...

class StoreXState(_message.Message):
  __slots__ = ("storex_resource_json", "active_transfer", "active_operation")
  STOREX_RESOURCE_JSON_FIELD_NUMBER: _ClassVar[int]
  ACTIVE_TRANSFER_FIELD_NUMBER: _ClassVar[int]
  ACTIVE_OPERATION_FIELD_NUMBER: _ClassVar[int]
  storex_resource_json: str
  active_transfer: ActiveTransfer
  active_operation: ActiveOperation
  def __init__(
    self,
    storex_resource_json: _Optional[str] = ...,
    active_transfer: _Optional[_Union[ActiveTransfer, _Mapping]] = ...,
    active_operation: _Optional[_Union[ActiveOperation, _Mapping]] = ...,
  ) -> None: ...

class FetchPlateRequest(_message.Message):
  __slots__ = ("plate_name", "read_barcode")
  PLATE_NAME_FIELD_NUMBER: _ClassVar[int]
  READ_BARCODE_FIELD_NUMBER: _ClassVar[int]
  plate_name: str
  read_barcode: bool
  def __init__(self, plate_name: _Optional[str] = ..., read_barcode: bool = ...) -> None: ...

class ClaimTrayPlateRequest(_message.Message):
  __slots__ = ("transfer_id",)
  TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
  transfer_id: str
  def __init__(self, transfer_id: _Optional[str] = ...) -> None: ...

class ClaimTrayPlateResponse(_message.Message):
  __slots__ = ("transfer_id", "plate")
  TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
  PLATE_FIELD_NUMBER: _ClassVar[int]
  transfer_id: str
  plate: PlateSnapshot
  def __init__(
    self, transfer_id: _Optional[str] = ..., plate: _Optional[_Union[PlateSnapshot, _Mapping]] = ...
  ) -> None: ...

class TransferRequest(_message.Message):
  __slots__ = ("transfer_id",)
  TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
  transfer_id: str
  def __init__(self, transfer_id: _Optional[str] = ...) -> None: ...

class RegisterTrayPlateRequest(_message.Message):
  __slots__ = ("resource_json",)
  RESOURCE_JSON_FIELD_NUMBER: _ClassVar[int]
  resource_json: str
  def __init__(self, resource_json: _Optional[str] = ...) -> None: ...

class SiteAddress(_message.Message):
  __slots__ = ("cassette", "position")
  CASSETTE_FIELD_NUMBER: _ClassVar[int]
  POSITION_FIELD_NUMBER: _ClassVar[int]
  cassette: int
  position: int
  def __init__(self, cassette: _Optional[int] = ..., position: _Optional[int] = ...) -> None: ...

class SmallestFit(_message.Message):
  __slots__ = ()
  def __init__(self) -> None: ...

class RandomFit(_message.Message):
  __slots__ = ()
  def __init__(self) -> None: ...

class StoreTrayPlateRequest(_message.Message):
  __slots__ = ("plate_name", "site", "smallest_fit", "random_fit", "read_barcode")
  PLATE_NAME_FIELD_NUMBER: _ClassVar[int]
  SITE_FIELD_NUMBER: _ClassVar[int]
  SMALLEST_FIT_FIELD_NUMBER: _ClassVar[int]
  RANDOM_FIT_FIELD_NUMBER: _ClassVar[int]
  READ_BARCODE_FIELD_NUMBER: _ClassVar[int]
  plate_name: str
  site: SiteAddress
  smallest_fit: SmallestFit
  random_fit: RandomFit
  read_barcode: bool
  def __init__(
    self,
    plate_name: _Optional[str] = ...,
    site: _Optional[_Union[SiteAddress, _Mapping]] = ...,
    smallest_fit: _Optional[_Union[SmallestFit, _Mapping]] = ...,
    random_fit: _Optional[_Union[RandomFit, _Mapping]] = ...,
    read_barcode: bool = ...,
  ) -> None: ...

class StoredPlate(_message.Message):
  __slots__ = ("plate", "site")
  PLATE_FIELD_NUMBER: _ClassVar[int]
  SITE_FIELD_NUMBER: _ClassVar[int]
  plate: PlateSnapshot
  site: SiteAddress
  def __init__(
    self,
    plate: _Optional[_Union[PlateSnapshot, _Mapping]] = ...,
    site: _Optional[_Union[SiteAddress, _Mapping]] = ...,
  ) -> None: ...
