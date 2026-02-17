from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Coordinate(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ...) -> None: ...

class Rotation(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ...) -> None: ...

class Size(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ...) -> None: ...

class TipData(_message.Message):
    __slots__ = ("type", "name", "has_filter", "total_tip_length", "maximal_volume", "fitting_depth", "tip_size", "pickup_method")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    HAS_FILTER_FIELD_NUMBER: _ClassVar[int]
    TOTAL_TIP_LENGTH_FIELD_NUMBER: _ClassVar[int]
    MAXIMAL_VOLUME_FIELD_NUMBER: _ClassVar[int]
    FITTING_DEPTH_FIELD_NUMBER: _ClassVar[int]
    TIP_SIZE_FIELD_NUMBER: _ClassVar[int]
    PICKUP_METHOD_FIELD_NUMBER: _ClassVar[int]
    type: str
    name: str
    has_filter: bool
    total_tip_length: float
    maximal_volume: float
    fitting_depth: float
    tip_size: str
    pickup_method: str
    def __init__(self, type: _Optional[str] = ..., name: _Optional[str] = ..., has_filter: bool = ..., total_tip_length: _Optional[float] = ..., maximal_volume: _Optional[float] = ..., fitting_depth: _Optional[float] = ..., tip_size: _Optional[str] = ..., pickup_method: _Optional[str] = ...) -> None: ...

class ResourceData(_message.Message):
    __slots__ = ("name", "type", "size_x", "size_y", "size_z", "category", "model", "location", "rotation", "parent_name", "material_z_thickness", "max_volume", "well_bottom_type", "cross_section_type", "plate_type", "has_lid", "prototype_tip", "ordering", "nesting_z_height")
    class OrderingEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    SIZE_X_FIELD_NUMBER: _ClassVar[int]
    SIZE_Y_FIELD_NUMBER: _ClassVar[int]
    SIZE_Z_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    PARENT_NAME_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_Z_THICKNESS_FIELD_NUMBER: _ClassVar[int]
    MAX_VOLUME_FIELD_NUMBER: _ClassVar[int]
    WELL_BOTTOM_TYPE_FIELD_NUMBER: _ClassVar[int]
    CROSS_SECTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    PLATE_TYPE_FIELD_NUMBER: _ClassVar[int]
    HAS_LID_FIELD_NUMBER: _ClassVar[int]
    PROTOTYPE_TIP_FIELD_NUMBER: _ClassVar[int]
    ORDERING_FIELD_NUMBER: _ClassVar[int]
    NESTING_Z_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    name: str
    type: str
    size_x: float
    size_y: float
    size_z: float
    category: str
    model: str
    location: Coordinate
    rotation: Rotation
    parent_name: str
    material_z_thickness: float
    max_volume: float
    well_bottom_type: str
    cross_section_type: str
    plate_type: str
    has_lid: bool
    prototype_tip: TipData
    ordering: _containers.ScalarMap[str, str]
    nesting_z_height: float
    def __init__(self, name: _Optional[str] = ..., type: _Optional[str] = ..., size_x: _Optional[float] = ..., size_y: _Optional[float] = ..., size_z: _Optional[float] = ..., category: _Optional[str] = ..., model: _Optional[str] = ..., location: _Optional[_Union[Coordinate, _Mapping]] = ..., rotation: _Optional[_Union[Rotation, _Mapping]] = ..., parent_name: _Optional[str] = ..., material_z_thickness: _Optional[float] = ..., max_volume: _Optional[float] = ..., well_bottom_type: _Optional[str] = ..., cross_section_type: _Optional[str] = ..., plate_type: _Optional[str] = ..., has_lid: bool = ..., prototype_tip: _Optional[_Union[TipData, _Mapping]] = ..., ordering: _Optional[_Mapping[str, str]] = ..., nesting_z_height: _Optional[float] = ...) -> None: ...

class ResourceTree(_message.Message):
    __slots__ = ("data", "children")
    DATA_FIELD_NUMBER: _ClassVar[int]
    CHILDREN_FIELD_NUMBER: _ClassVar[int]
    data: ResourceData
    children: _containers.RepeatedCompositeFieldContainer[ResourceTree]
    def __init__(self, data: _Optional[_Union[ResourceData, _Mapping]] = ..., children: _Optional[_Iterable[_Union[ResourceTree, _Mapping]]] = ...) -> None: ...

class VolumeTrackerState(_message.Message):
    __slots__ = ("volume", "pending_volume", "max_volume", "is_disabled")
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    PENDING_VOLUME_FIELD_NUMBER: _ClassVar[int]
    MAX_VOLUME_FIELD_NUMBER: _ClassVar[int]
    IS_DISABLED_FIELD_NUMBER: _ClassVar[int]
    volume: float
    pending_volume: float
    max_volume: float
    is_disabled: bool
    def __init__(self, volume: _Optional[float] = ..., pending_volume: _Optional[float] = ..., max_volume: _Optional[float] = ..., is_disabled: bool = ...) -> None: ...

class TipTrackerState(_message.Message):
    __slots__ = ("has_tip", "tip", "is_disabled")
    HAS_TIP_FIELD_NUMBER: _ClassVar[int]
    TIP_FIELD_NUMBER: _ClassVar[int]
    IS_DISABLED_FIELD_NUMBER: _ClassVar[int]
    has_tip: bool
    tip: TipData
    is_disabled: bool
    def __init__(self, has_tip: bool = ..., tip: _Optional[_Union[TipData, _Mapping]] = ..., is_disabled: bool = ...) -> None: ...

class GetTreeRequest(_message.Message):
    __slots__ = ("root_name",)
    ROOT_NAME_FIELD_NUMBER: _ClassVar[int]
    root_name: str
    def __init__(self, root_name: _Optional[str] = ...) -> None: ...

class ResourceByNameRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class GetLocationWrtRequest(_message.Message):
    __slots__ = ("resource_name", "other_name", "anchor_x", "anchor_y", "anchor_z")
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    OTHER_NAME_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_X_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_Y_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_Z_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    other_name: str
    anchor_x: str
    anchor_y: str
    anchor_z: str
    def __init__(self, resource_name: _Optional[str] = ..., other_name: _Optional[str] = ..., anchor_x: _Optional[str] = ..., anchor_y: _Optional[str] = ..., anchor_z: _Optional[str] = ...) -> None: ...

class GetAbsoluteLocationRequest(_message.Message):
    __slots__ = ("resource_name", "anchor_x", "anchor_y", "anchor_z")
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_X_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_Y_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_Z_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    anchor_x: str
    anchor_y: str
    anchor_z: str
    def __init__(self, resource_name: _Optional[str] = ..., anchor_x: _Optional[str] = ..., anchor_y: _Optional[str] = ..., anchor_z: _Optional[str] = ...) -> None: ...

class GetAbsoluteRotationRequest(_message.Message):
    __slots__ = ("resource_name",)
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    def __init__(self, resource_name: _Optional[str] = ...) -> None: ...

class GetAbsoluteSizeRequest(_message.Message):
    __slots__ = ("resource_name",)
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    def __init__(self, resource_name: _Optional[str] = ...) -> None: ...

class GetHighestPointRequest(_message.Message):
    __slots__ = ("resource_name",)
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    def __init__(self, resource_name: _Optional[str] = ...) -> None: ...

class LocationWrtItem(_message.Message):
    __slots__ = ("resource_name", "other_name", "anchor_x", "anchor_y", "anchor_z")
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    OTHER_NAME_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_X_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_Y_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_Z_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    other_name: str
    anchor_x: str
    anchor_y: str
    anchor_z: str
    def __init__(self, resource_name: _Optional[str] = ..., other_name: _Optional[str] = ..., anchor_x: _Optional[str] = ..., anchor_y: _Optional[str] = ..., anchor_z: _Optional[str] = ...) -> None: ...

class BatchGetLocationWrtRequest(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[LocationWrtItem]
    def __init__(self, items: _Optional[_Iterable[_Union[LocationWrtItem, _Mapping]]] = ...) -> None: ...

class BatchCoordinateResponse(_message.Message):
    __slots__ = ("coordinates",)
    COORDINATES_FIELD_NUMBER: _ClassVar[int]
    coordinates: _containers.RepeatedCompositeFieldContainer[Coordinate]
    def __init__(self, coordinates: _Optional[_Iterable[_Union[Coordinate, _Mapping]]] = ...) -> None: ...

class ComputeVolumeHeightRequest(_message.Message):
    __slots__ = ("resource_name", "value")
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    value: float
    def __init__(self, resource_name: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...

class FloatResponse(_message.Message):
    __slots__ = ("value",)
    VALUE_FIELD_NUMBER: _ClassVar[int]
    value: float
    def __init__(self, value: _Optional[float] = ...) -> None: ...

class BoolResponse(_message.Message):
    __slots__ = ("value",)
    VALUE_FIELD_NUMBER: _ClassVar[int]
    value: bool
    def __init__(self, value: bool = ...) -> None: ...

class GetTipRequest(_message.Message):
    __slots__ = ("tip_spot_name",)
    TIP_SPOT_NAME_FIELD_NUMBER: _ClassVar[int]
    tip_spot_name: str
    def __init__(self, tip_spot_name: _Optional[str] = ...) -> None: ...

class TrackerOpRequest(_message.Message):
    __slots__ = ("resource_name", "volume")
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    volume: float
    def __init__(self, resource_name: _Optional[str] = ..., volume: _Optional[float] = ...) -> None: ...

class BatchTrackerOpRequest(_message.Message):
    __slots__ = ("ops",)
    OPS_FIELD_NUMBER: _ClassVar[int]
    ops: _containers.RepeatedCompositeFieldContainer[TrackerOpRequest]
    def __init__(self, ops: _Optional[_Iterable[_Union[TrackerOpRequest, _Mapping]]] = ...) -> None: ...

class TipTrackerOpRequest(_message.Message):
    __slots__ = ("tip_spot_name",)
    TIP_SPOT_NAME_FIELD_NUMBER: _ClassVar[int]
    tip_spot_name: str
    def __init__(self, tip_spot_name: _Optional[str] = ...) -> None: ...

class CommitRollbackRequest(_message.Message):
    __slots__ = ("resource_names",)
    RESOURCE_NAMES_FIELD_NUMBER: _ClassVar[int]
    resource_names: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, resource_names: _Optional[_Iterable[str]] = ...) -> None: ...

class AssignChildRequest(_message.Message):
    __slots__ = ("child_name", "parent_name", "location")
    CHILD_NAME_FIELD_NUMBER: _ClassVar[int]
    PARENT_NAME_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    child_name: str
    parent_name: str
    location: Coordinate
    def __init__(self, child_name: _Optional[str] = ..., parent_name: _Optional[str] = ..., location: _Optional[_Union[Coordinate, _Mapping]] = ...) -> None: ...

class UnassignChildRequest(_message.Message):
    __slots__ = ("resource_name",)
    RESOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    resource_name: str
    def __init__(self, resource_name: _Optional[str] = ...) -> None: ...

class HasLidRequest(_message.Message):
    __slots__ = ("plate_name",)
    PLATE_NAME_FIELD_NUMBER: _ClassVar[int]
    plate_name: str
    def __init__(self, plate_name: _Optional[str] = ...) -> None: ...
