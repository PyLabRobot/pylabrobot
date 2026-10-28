import datetime

from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AnchorX(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ANCHOR_X_UNSPECIFIED: _ClassVar[AnchorX]
    ANCHOR_X_LEFT: _ClassVar[AnchorX]
    ANCHOR_X_CENTER: _ClassVar[AnchorX]
    ANCHOR_X_RIGHT: _ClassVar[AnchorX]

class AnchorY(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ANCHOR_Y_UNSPECIFIED: _ClassVar[AnchorY]
    ANCHOR_Y_FRONT: _ClassVar[AnchorY]
    ANCHOR_Y_CENTER: _ClassVar[AnchorY]
    ANCHOR_Y_BACK: _ClassVar[AnchorY]

class AnchorZ(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ANCHOR_Z_UNSPECIFIED: _ClassVar[AnchorZ]
    ANCHOR_Z_BOTTOM: _ClassVar[AnchorZ]
    ANCHOR_Z_CENTER: _ClassVar[AnchorZ]
    ANCHOR_Z_TOP: _ClassVar[AnchorZ]
    ANCHOR_Z_CAVITY_BOTTOM: _ClassVar[AnchorZ]

class ResourceKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RESOURCE_KIND_UNSPECIFIED: _ClassVar[ResourceKind]
    RESOURCE_KIND_RESOURCE: _ClassVar[ResourceKind]
    RESOURCE_KIND_DECK: _ClassVar[ResourceKind]
    RESOURCE_KIND_CARRIER: _ClassVar[ResourceKind]
    RESOURCE_KIND_RESOURCE_HOLDER: _ClassVar[ResourceKind]
    RESOURCE_KIND_PLATE_HOLDER: _ClassVar[ResourceKind]
    RESOURCE_KIND_CONTAINER: _ClassVar[ResourceKind]
    RESOURCE_KIND_WELL: _ClassVar[ResourceKind]
    RESOURCE_KIND_TROUGH: _ClassVar[ResourceKind]
    RESOURCE_KIND_TUBE: _ClassVar[ResourceKind]
    RESOURCE_KIND_PETRI_DISH: _ClassVar[ResourceKind]
    RESOURCE_KIND_TRASH: _ClassVar[ResourceKind]
    RESOURCE_KIND_PLATE: _ClassVar[ResourceKind]
    RESOURCE_KIND_TIP_RACK: _ClassVar[ResourceKind]
    RESOURCE_KIND_TIP_SPOT: _ClassVar[ResourceKind]
    RESOURCE_KIND_TIP: _ClassVar[ResourceKind]
    RESOURCE_KIND_LID: _ClassVar[ResourceKind]
    RESOURCE_KIND_PLATE_ADAPTER: _ClassVar[ResourceKind]
    RESOURCE_KIND_RESOURCE_STACK: _ClassVar[ResourceKind]
    RESOURCE_KIND_CONTAINER_RACK: _ClassVar[ResourceKind]
    RESOURCE_KIND_TUBE_RACK: _ClassVar[ResourceKind]
    RESOURCE_KIND_PETRI_DISH_HOLDER: _ClassVar[ResourceKind]
    RESOURCE_KIND_EMBEDDED_TIP_RACK_HOLDER: _ClassVar[ResourceKind]
    RESOURCE_KIND_HEAD_TOOL: _ClassVar[ResourceKind]
    RESOURCE_KIND_PIPETTE_CHANNEL: _ClassVar[ResourceKind]
    RESOURCE_KIND_TIP_MOUNTING_SHAFT: _ClassVar[ResourceKind]
    RESOURCE_KIND_N_CHANNEL_PIPETTE: _ClassVar[ResourceKind]
    RESOURCE_KIND_LINK_BODY: _ClassVar[ResourceKind]
    RESOURCE_KIND_MECHANICAL_GRIPPER: _ClassVar[ResourceKind]

class WellBottomType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WELL_BOTTOM_TYPE_UNSPECIFIED: _ClassVar[WellBottomType]
    WELL_BOTTOM_TYPE_FLAT: _ClassVar[WellBottomType]
    WELL_BOTTOM_TYPE_U: _ClassVar[WellBottomType]
    WELL_BOTTOM_TYPE_V: _ClassVar[WellBottomType]

class CrossSectionType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CROSS_SECTION_TYPE_UNSPECIFIED: _ClassVar[CrossSectionType]
    CROSS_SECTION_TYPE_CIRCLE: _ClassVar[CrossSectionType]
    CROSS_SECTION_TYPE_RECTANGLE: _ClassVar[CrossSectionType]

class TipPickupMethod(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TIP_PICKUP_METHOD_UNSPECIFIED: _ClassVar[TipPickupMethod]
    TIP_PICKUP_METHOD_OUT_OF_RACK: _ClassVar[TipPickupMethod]
    TIP_PICKUP_METHOD_OUT_OF_WASH_LIQUID: _ClassVar[TipPickupMethod]

class TipRackStyle(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TIP_RACK_STYLE_UNSPECIFIED: _ClassVar[TipRackStyle]
    TIP_RACK_STYLE_EMBEDDED: _ClassVar[TipRackStyle]
    TIP_RACK_STYLE_STANDING: _ClassVar[TipRackStyle]
    TIP_RACK_STYLE_NESTED: _ClassVar[TipRackStyle]

class StackDirection(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STACK_DIRECTION_UNSPECIFIED: _ClassVar[StackDirection]
    STACK_DIRECTION_X: _ClassVar[StackDirection]
    STACK_DIRECTION_Y: _ClassVar[StackDirection]
    STACK_DIRECTION_Z: _ClassVar[StackDirection]

class View(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    VIEW_UNSPECIFIED: _ClassVar[View]
    VIEW_PENDING: _ClassVar[View]
    VIEW_COMMITTED: _ClassVar[View]

class Problem(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROBLEM_UNSPECIFIED: _ClassVar[Problem]
    PROBLEM_NOT_FOUND: _ClassVar[Problem]
    PROBLEM_NAME_CONFLICT: _ClassVar[Problem]
    PROBLEM_NO_LOCATION: _ClassVar[Problem]
    PROBLEM_NOT_IN_FRAME: _ClassVar[Problem]
    PROBLEM_WRONG_KIND: _ClassVar[Problem]
    PROBLEM_CANNOT_DROP_HERE: _ClassVar[Problem]
    PROBLEM_NO_HEIGHT_VOLUME_DATA: _ClassVar[Problem]
    PROBLEM_OUTSIDE_HEIGHT_VOLUME_DATA: _ClassVar[Problem]
    PROBLEM_OUT_OF_RANGE: _ClassVar[Problem]
    PROBLEM_NO_TIP: _ClassVar[Problem]
    PROBLEM_HAS_TIP: _ClassVar[Problem]
    PROBLEM_TOO_LITTLE_LIQUID: _ClassVar[Problem]
    PROBLEM_TOO_LITTLE_VOLUME: _ClassVar[Problem]
    PROBLEM_TIP_NOT_EMPTY: _ClassVar[Problem]
    PROBLEM_LID_ON: _ClassVar[Problem]

class OnTrackingProblem(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ON_TRACKING_PROBLEM_UNSPECIFIED: _ClassVar[OnTrackingProblem]
    ON_TRACKING_PROBLEM_RECORD: _ClassVar[OnTrackingProblem]
    ON_TRACKING_PROBLEM_REJECT: _ClassVar[OnTrackingProblem]

class ItemState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ITEM_STATE_UNSPECIFIED: _ClassVar[ItemState]
    ITEM_STATE_PENDING: _ClassVar[ItemState]
    ITEM_STATE_COMMITTED: _ClassVar[ItemState]
    ITEM_STATE_ROLLED_BACK: _ClassVar[ItemState]
ANCHOR_X_UNSPECIFIED: AnchorX
ANCHOR_X_LEFT: AnchorX
ANCHOR_X_CENTER: AnchorX
ANCHOR_X_RIGHT: AnchorX
ANCHOR_Y_UNSPECIFIED: AnchorY
ANCHOR_Y_FRONT: AnchorY
ANCHOR_Y_CENTER: AnchorY
ANCHOR_Y_BACK: AnchorY
ANCHOR_Z_UNSPECIFIED: AnchorZ
ANCHOR_Z_BOTTOM: AnchorZ
ANCHOR_Z_CENTER: AnchorZ
ANCHOR_Z_TOP: AnchorZ
ANCHOR_Z_CAVITY_BOTTOM: AnchorZ
RESOURCE_KIND_UNSPECIFIED: ResourceKind
RESOURCE_KIND_RESOURCE: ResourceKind
RESOURCE_KIND_DECK: ResourceKind
RESOURCE_KIND_CARRIER: ResourceKind
RESOURCE_KIND_RESOURCE_HOLDER: ResourceKind
RESOURCE_KIND_PLATE_HOLDER: ResourceKind
RESOURCE_KIND_CONTAINER: ResourceKind
RESOURCE_KIND_WELL: ResourceKind
RESOURCE_KIND_TROUGH: ResourceKind
RESOURCE_KIND_TUBE: ResourceKind
RESOURCE_KIND_PETRI_DISH: ResourceKind
RESOURCE_KIND_TRASH: ResourceKind
RESOURCE_KIND_PLATE: ResourceKind
RESOURCE_KIND_TIP_RACK: ResourceKind
RESOURCE_KIND_TIP_SPOT: ResourceKind
RESOURCE_KIND_TIP: ResourceKind
RESOURCE_KIND_LID: ResourceKind
RESOURCE_KIND_PLATE_ADAPTER: ResourceKind
RESOURCE_KIND_RESOURCE_STACK: ResourceKind
RESOURCE_KIND_CONTAINER_RACK: ResourceKind
RESOURCE_KIND_TUBE_RACK: ResourceKind
RESOURCE_KIND_PETRI_DISH_HOLDER: ResourceKind
RESOURCE_KIND_EMBEDDED_TIP_RACK_HOLDER: ResourceKind
RESOURCE_KIND_HEAD_TOOL: ResourceKind
RESOURCE_KIND_PIPETTE_CHANNEL: ResourceKind
RESOURCE_KIND_TIP_MOUNTING_SHAFT: ResourceKind
RESOURCE_KIND_N_CHANNEL_PIPETTE: ResourceKind
RESOURCE_KIND_LINK_BODY: ResourceKind
RESOURCE_KIND_MECHANICAL_GRIPPER: ResourceKind
WELL_BOTTOM_TYPE_UNSPECIFIED: WellBottomType
WELL_BOTTOM_TYPE_FLAT: WellBottomType
WELL_BOTTOM_TYPE_U: WellBottomType
WELL_BOTTOM_TYPE_V: WellBottomType
CROSS_SECTION_TYPE_UNSPECIFIED: CrossSectionType
CROSS_SECTION_TYPE_CIRCLE: CrossSectionType
CROSS_SECTION_TYPE_RECTANGLE: CrossSectionType
TIP_PICKUP_METHOD_UNSPECIFIED: TipPickupMethod
TIP_PICKUP_METHOD_OUT_OF_RACK: TipPickupMethod
TIP_PICKUP_METHOD_OUT_OF_WASH_LIQUID: TipPickupMethod
TIP_RACK_STYLE_UNSPECIFIED: TipRackStyle
TIP_RACK_STYLE_EMBEDDED: TipRackStyle
TIP_RACK_STYLE_STANDING: TipRackStyle
TIP_RACK_STYLE_NESTED: TipRackStyle
STACK_DIRECTION_UNSPECIFIED: StackDirection
STACK_DIRECTION_X: StackDirection
STACK_DIRECTION_Y: StackDirection
STACK_DIRECTION_Z: StackDirection
VIEW_UNSPECIFIED: View
VIEW_PENDING: View
VIEW_COMMITTED: View
PROBLEM_UNSPECIFIED: Problem
PROBLEM_NOT_FOUND: Problem
PROBLEM_NAME_CONFLICT: Problem
PROBLEM_NO_LOCATION: Problem
PROBLEM_NOT_IN_FRAME: Problem
PROBLEM_WRONG_KIND: Problem
PROBLEM_CANNOT_DROP_HERE: Problem
PROBLEM_NO_HEIGHT_VOLUME_DATA: Problem
PROBLEM_OUTSIDE_HEIGHT_VOLUME_DATA: Problem
PROBLEM_OUT_OF_RANGE: Problem
PROBLEM_NO_TIP: Problem
PROBLEM_HAS_TIP: Problem
PROBLEM_TOO_LITTLE_LIQUID: Problem
PROBLEM_TOO_LITTLE_VOLUME: Problem
PROBLEM_TIP_NOT_EMPTY: Problem
PROBLEM_LID_ON: Problem
ON_TRACKING_PROBLEM_UNSPECIFIED: OnTrackingProblem
ON_TRACKING_PROBLEM_RECORD: OnTrackingProblem
ON_TRACKING_PROBLEM_REJECT: OnTrackingProblem
ITEM_STATE_UNSPECIFIED: ItemState
ITEM_STATE_PENDING: ItemState
ITEM_STATE_COMMITTED: ItemState
ITEM_STATE_ROLLED_BACK: ItemState

class ResourceRef(_message.Message):
    __slots__ = ("id", "name", "item")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ITEM_FIELD_NUMBER: _ClassVar[int]
    id: bytes
    name: NameRef
    item: ItemRef
    def __init__(self, id: _Optional[bytes] = ..., name: _Optional[_Union[NameRef, _Mapping]] = ..., item: _Optional[_Union[ItemRef, _Mapping]] = ...) -> None: ...

class NameRef(_message.Message):
    __slots__ = ("root", "name")
    ROOT_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    root: str
    name: str
    def __init__(self, root: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class ItemRef(_message.Message):
    __slots__ = ("parent", "identifier")
    PARENT_FIELD_NUMBER: _ClassVar[int]
    IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    parent: ResourceRef
    identifier: str
    def __init__(self, parent: _Optional[_Union[ResourceRef, _Mapping]] = ..., identifier: _Optional[str] = ...) -> None: ...

class Vec3(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ...) -> None: ...

class PartialVec3(_message.Message):
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

class PartialRotation(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ...) -> None: ...

class Box(_message.Message):
    __slots__ = ("min", "max")
    MIN_FIELD_NUMBER: _ClassVar[int]
    MAX_FIELD_NUMBER: _ClassVar[int]
    min: Vec3
    max: Vec3
    def __init__(self, min: _Optional[_Union[Vec3, _Mapping]] = ..., max: _Optional[_Union[Vec3, _Mapping]] = ...) -> None: ...

class Anchor(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: AnchorX
    y: AnchorY
    z: AnchorZ
    def __init__(self, x: _Optional[_Union[AnchorX, str]] = ..., y: _Optional[_Union[AnchorY, str]] = ..., z: _Optional[_Union[AnchorZ, str]] = ...) -> None: ...

class Barcode(_message.Message):
    __slots__ = ("data", "symbology", "position_on_resource")
    DATA_FIELD_NUMBER: _ClassVar[int]
    SYMBOLOGY_FIELD_NUMBER: _ClassVar[int]
    POSITION_ON_RESOURCE_FIELD_NUMBER: _ClassVar[int]
    data: str
    symbology: str
    position_on_resource: str
    def __init__(self, data: _Optional[str] = ..., symbology: _Optional[str] = ..., position_on_resource: _Optional[str] = ...) -> None: ...

class HeightVolumePoint(_message.Message):
    __slots__ = ("height", "volume")
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    height: float
    volume: float
    def __init__(self, height: _Optional[float] = ..., volume: _Optional[float] = ...) -> None: ...

class ContainerSpec(_message.Message):
    __slots__ = ("max_volume", "material_z_thickness", "height_volume_data", "no_go_zones", "bottom_type", "cross_section_type", "through_base_to_container_base")
    MAX_VOLUME_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_Z_THICKNESS_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_VOLUME_DATA_FIELD_NUMBER: _ClassVar[int]
    NO_GO_ZONES_FIELD_NUMBER: _ClassVar[int]
    BOTTOM_TYPE_FIELD_NUMBER: _ClassVar[int]
    CROSS_SECTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    THROUGH_BASE_TO_CONTAINER_BASE_FIELD_NUMBER: _ClassVar[int]
    max_volume: float
    material_z_thickness: float
    height_volume_data: _containers.RepeatedCompositeFieldContainer[HeightVolumePoint]
    no_go_zones: _containers.RepeatedCompositeFieldContainer[Box]
    bottom_type: WellBottomType
    cross_section_type: CrossSectionType
    through_base_to_container_base: float
    def __init__(self, max_volume: _Optional[float] = ..., material_z_thickness: _Optional[float] = ..., height_volume_data: _Optional[_Iterable[_Union[HeightVolumePoint, _Mapping]]] = ..., no_go_zones: _Optional[_Iterable[_Union[Box, _Mapping]]] = ..., bottom_type: _Optional[_Union[WellBottomType, str]] = ..., cross_section_type: _Optional[_Union[CrossSectionType, str]] = ..., through_base_to_container_base: _Optional[float] = ...) -> None: ...

class ItemizedSpec(_message.Message):
    __slots__ = ("num_items_x", "num_items_y", "grid")
    NUM_ITEMS_X_FIELD_NUMBER: _ClassVar[int]
    NUM_ITEMS_Y_FIELD_NUMBER: _ClassVar[int]
    GRID_FIELD_NUMBER: _ClassVar[int]
    num_items_x: int
    num_items_y: int
    grid: ItemGrid
    def __init__(self, num_items_x: _Optional[int] = ..., num_items_y: _Optional[int] = ..., grid: _Optional[_Union[ItemGrid, _Mapping]] = ...) -> None: ...

class ItemGrid(_message.Message):
    __slots__ = ("item", "offset", "item_dx", "item_dy")
    ITEM_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    ITEM_DX_FIELD_NUMBER: _ClassVar[int]
    ITEM_DY_FIELD_NUMBER: _ClassVar[int]
    item: ResourceDefinition
    offset: Vec3
    item_dx: float
    item_dy: float
    def __init__(self, item: _Optional[_Union[ResourceDefinition, _Mapping]] = ..., offset: _Optional[_Union[Vec3, _Mapping]] = ..., item_dx: _Optional[float] = ..., item_dy: _Optional[float] = ...) -> None: ...

class PlateSpec(_message.Message):
    __slots__ = ("plate_type", "stacking_z_height")
    PLATE_TYPE_FIELD_NUMBER: _ClassVar[int]
    STACKING_Z_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    plate_type: str
    stacking_z_height: float
    def __init__(self, plate_type: _Optional[str] = ..., stacking_z_height: _Optional[float] = ...) -> None: ...

class TipRackSpec(_message.Message):
    __slots__ = ("style", "frame_height", "sinking_depth", "stacking_z_height")
    STYLE_FIELD_NUMBER: _ClassVar[int]
    FRAME_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    SINKING_DEPTH_FIELD_NUMBER: _ClassVar[int]
    STACKING_Z_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    style: TipRackStyle
    frame_height: float
    sinking_depth: float
    stacking_z_height: float
    def __init__(self, style: _Optional[_Union[TipRackStyle, str]] = ..., frame_height: _Optional[float] = ..., sinking_depth: _Optional[float] = ..., stacking_z_height: _Optional[float] = ...) -> None: ...

class HamiltonTipSpec(_message.Message):
    __slots__ = ("tip_size", "pickup_method")
    TIP_SIZE_FIELD_NUMBER: _ClassVar[int]
    PICKUP_METHOD_FIELD_NUMBER: _ClassVar[int]
    tip_size: str
    pickup_method: TipPickupMethod
    def __init__(self, tip_size: _Optional[str] = ..., pickup_method: _Optional[_Union[TipPickupMethod, str]] = ...) -> None: ...

class HeadToolSpec(_message.Message):
    __slots__ = ("fitting_depth", "is_conductive", "pick_up_location", "grip_line_height")
    FITTING_DEPTH_FIELD_NUMBER: _ClassVar[int]
    IS_CONDUCTIVE_FIELD_NUMBER: _ClassVar[int]
    PICK_UP_LOCATION_FIELD_NUMBER: _ClassVar[int]
    GRIP_LINE_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    fitting_depth: float
    is_conductive: bool
    pick_up_location: Vec3
    grip_line_height: float
    def __init__(self, fitting_depth: _Optional[float] = ..., is_conductive: _Optional[bool] = ..., pick_up_location: _Optional[_Union[Vec3, _Mapping]] = ..., grip_line_height: _Optional[float] = ...) -> None: ...

class TipSpec(_message.Message):
    __slots__ = ("diameter", "has_filter", "maximal_volume", "nominal_volume", "collar_height", "hamilton")
    DIAMETER_FIELD_NUMBER: _ClassVar[int]
    HAS_FILTER_FIELD_NUMBER: _ClassVar[int]
    MAXIMAL_VOLUME_FIELD_NUMBER: _ClassVar[int]
    NOMINAL_VOLUME_FIELD_NUMBER: _ClassVar[int]
    COLLAR_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    HAMILTON_FIELD_NUMBER: _ClassVar[int]
    diameter: float
    has_filter: bool
    maximal_volume: float
    nominal_volume: float
    collar_height: float
    hamilton: HamiltonTipSpec
    def __init__(self, diameter: _Optional[float] = ..., has_filter: _Optional[bool] = ..., maximal_volume: _Optional[float] = ..., nominal_volume: _Optional[float] = ..., collar_height: _Optional[float] = ..., hamilton: _Optional[_Union[HamiltonTipSpec, _Mapping]] = ...) -> None: ...

class TipSpotSpec(_message.Message):
    __slots__ = ("prototype_tip",)
    PROTOTYPE_TIP_FIELD_NUMBER: _ClassVar[int]
    prototype_tip: ResourceAttributes
    def __init__(self, prototype_tip: _Optional[_Union[ResourceAttributes, _Mapping]] = ...) -> None: ...

class LidSpec(_message.Message):
    __slots__ = ("nesting_z_height",)
    NESTING_Z_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    nesting_z_height: float
    def __init__(self, nesting_z_height: _Optional[float] = ...) -> None: ...

class HolderSpec(_message.Message):
    __slots__ = ("child_location", "pedestal_size_z", "accepts", "capacity", "channel_y_centers")
    CHILD_LOCATION_FIELD_NUMBER: _ClassVar[int]
    PEDESTAL_SIZE_Z_FIELD_NUMBER: _ClassVar[int]
    ACCEPTS_FIELD_NUMBER: _ClassVar[int]
    CAPACITY_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_Y_CENTERS_FIELD_NUMBER: _ClassVar[int]
    child_location: Vec3
    pedestal_size_z: float
    accepts: _containers.RepeatedScalarFieldContainer[ResourceKind]
    capacity: int
    channel_y_centers: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, child_location: _Optional[_Union[Vec3, _Mapping]] = ..., pedestal_size_z: _Optional[float] = ..., accepts: _Optional[_Iterable[_Union[ResourceKind, str]]] = ..., capacity: _Optional[int] = ..., channel_y_centers: _Optional[_Iterable[float]] = ...) -> None: ...

class ResourceStackSpec(_message.Message):
    __slots__ = ("direction",)
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    direction: StackDirection
    def __init__(self, direction: _Optional[_Union[StackDirection, str]] = ...) -> None: ...

class PlateAdapterSpec(_message.Message):
    __slots__ = ("dx", "dy", "dz", "adapter_hole_size_x", "adapter_hole_size_y", "adapter_hole_size_z", "adapter_hole_dx", "adapter_hole_dy", "plate_z_offset")
    DX_FIELD_NUMBER: _ClassVar[int]
    DY_FIELD_NUMBER: _ClassVar[int]
    DZ_FIELD_NUMBER: _ClassVar[int]
    ADAPTER_HOLE_SIZE_X_FIELD_NUMBER: _ClassVar[int]
    ADAPTER_HOLE_SIZE_Y_FIELD_NUMBER: _ClassVar[int]
    ADAPTER_HOLE_SIZE_Z_FIELD_NUMBER: _ClassVar[int]
    ADAPTER_HOLE_DX_FIELD_NUMBER: _ClassVar[int]
    ADAPTER_HOLE_DY_FIELD_NUMBER: _ClassVar[int]
    PLATE_Z_OFFSET_FIELD_NUMBER: _ClassVar[int]
    dx: float
    dy: float
    dz: float
    adapter_hole_size_x: float
    adapter_hole_size_y: float
    adapter_hole_size_z: float
    adapter_hole_dx: float
    adapter_hole_dy: float
    plate_z_offset: float
    def __init__(self, dx: _Optional[float] = ..., dy: _Optional[float] = ..., dz: _Optional[float] = ..., adapter_hole_size_x: _Optional[float] = ..., adapter_hole_size_y: _Optional[float] = ..., adapter_hole_size_z: _Optional[float] = ..., adapter_hole_dx: _Optional[float] = ..., adapter_hole_dy: _Optional[float] = ..., plate_z_offset: _Optional[float] = ...) -> None: ...

class PipetteChannelSpec(_message.Message):
    __slots__ = ("reference_point",)
    REFERENCE_POINT_FIELD_NUMBER: _ClassVar[int]
    reference_point: Vec3
    def __init__(self, reference_point: _Optional[_Union[Vec3, _Mapping]] = ...) -> None: ...

class TipMountingShaftSpec(_message.Message):
    __slots__ = ("tip_pickup_mode", "cross_section_type")
    TIP_PICKUP_MODE_FIELD_NUMBER: _ClassVar[int]
    CROSS_SECTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    tip_pickup_mode: str
    cross_section_type: CrossSectionType
    def __init__(self, tip_pickup_mode: _Optional[str] = ..., cross_section_type: _Optional[_Union[CrossSectionType, str]] = ...) -> None: ...

class NChannelPipetteSpec(_message.Message):
    __slots__ = ("reference_point", "independent_channel_actuation")
    REFERENCE_POINT_FIELD_NUMBER: _ClassVar[int]
    INDEPENDENT_CHANNEL_ACTUATION_FIELD_NUMBER: _ClassVar[int]
    reference_point: Vec3
    independent_channel_actuation: bool
    def __init__(self, reference_point: _Optional[_Union[Vec3, _Mapping]] = ..., independent_channel_actuation: _Optional[bool] = ...) -> None: ...

class LinkBodySpec(_message.Message):
    __slots__ = ("proximal_joint", "distal_joint")
    PROXIMAL_JOINT_FIELD_NUMBER: _ClassVar[int]
    DISTAL_JOINT_FIELD_NUMBER: _ClassVar[int]
    proximal_joint: Vec3
    distal_joint: Vec3
    def __init__(self, proximal_joint: _Optional[_Union[Vec3, _Mapping]] = ..., distal_joint: _Optional[_Union[Vec3, _Mapping]] = ...) -> None: ...

class MechanicalGripperSpec(_message.Message):
    __slots__ = ("proximal_joint", "tool_center_point", "jaw_width_min", "jaw_width_max", "body", "fingers", "pads", "jaws")
    PROXIMAL_JOINT_FIELD_NUMBER: _ClassVar[int]
    TOOL_CENTER_POINT_FIELD_NUMBER: _ClassVar[int]
    JAW_WIDTH_MIN_FIELD_NUMBER: _ClassVar[int]
    JAW_WIDTH_MAX_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    FINGERS_FIELD_NUMBER: _ClassVar[int]
    PADS_FIELD_NUMBER: _ClassVar[int]
    JAWS_FIELD_NUMBER: _ClassVar[int]
    proximal_joint: Vec3
    tool_center_point: Vec3
    jaw_width_min: float
    jaw_width_max: float
    body: str
    fingers: _containers.RepeatedScalarFieldContainer[str]
    pads: _containers.RepeatedScalarFieldContainer[str]
    jaws: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, proximal_joint: _Optional[_Union[Vec3, _Mapping]] = ..., tool_center_point: _Optional[_Union[Vec3, _Mapping]] = ..., jaw_width_min: _Optional[float] = ..., jaw_width_max: _Optional[float] = ..., body: _Optional[str] = ..., fingers: _Optional[_Iterable[str]] = ..., pads: _Optional[_Iterable[str]] = ..., jaws: _Optional[_Iterable[str]] = ...) -> None: ...

class HamiltonDeckSpec(_message.Message):
    __slots__ = ("num_tracks", "track_origin", "track_pitch")
    NUM_TRACKS_FIELD_NUMBER: _ClassVar[int]
    TRACK_ORIGIN_FIELD_NUMBER: _ClassVar[int]
    TRACK_PITCH_FIELD_NUMBER: _ClassVar[int]
    num_tracks: int
    track_origin: Vec3
    track_pitch: float
    def __init__(self, num_tracks: _Optional[int] = ..., track_origin: _Optional[_Union[Vec3, _Mapping]] = ..., track_pitch: _Optional[float] = ...) -> None: ...

class DeckSpec(_message.Message):
    __slots__ = ("hamilton",)
    HAMILTON_FIELD_NUMBER: _ClassVar[int]
    hamilton: HamiltonDeckSpec
    def __init__(self, hamilton: _Optional[_Union[HamiltonDeckSpec, _Mapping]] = ...) -> None: ...

class ResourceAttributes(_message.Message):
    __slots__ = ("name", "kind", "model", "category", "size", "location", "rotation", "identifier", "preferred_pickup_location", "barcode", "metadata", "display_model", "container", "itemized", "plate", "tip_rack", "head_tool", "tip", "tip_spot", "lid", "holder", "stack", "plate_adapter", "pipette_channel", "tip_mounting_shaft", "n_channel_pipette", "link_body", "mechanical_gripper", "deck")
    NAME_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_PICKUP_LOCATION_FIELD_NUMBER: _ClassVar[int]
    BARCODE_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_MODEL_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_FIELD_NUMBER: _ClassVar[int]
    ITEMIZED_FIELD_NUMBER: _ClassVar[int]
    PLATE_FIELD_NUMBER: _ClassVar[int]
    TIP_RACK_FIELD_NUMBER: _ClassVar[int]
    HEAD_TOOL_FIELD_NUMBER: _ClassVar[int]
    TIP_FIELD_NUMBER: _ClassVar[int]
    TIP_SPOT_FIELD_NUMBER: _ClassVar[int]
    LID_FIELD_NUMBER: _ClassVar[int]
    HOLDER_FIELD_NUMBER: _ClassVar[int]
    STACK_FIELD_NUMBER: _ClassVar[int]
    PLATE_ADAPTER_FIELD_NUMBER: _ClassVar[int]
    PIPETTE_CHANNEL_FIELD_NUMBER: _ClassVar[int]
    TIP_MOUNTING_SHAFT_FIELD_NUMBER: _ClassVar[int]
    N_CHANNEL_PIPETTE_FIELD_NUMBER: _ClassVar[int]
    LINK_BODY_FIELD_NUMBER: _ClassVar[int]
    MECHANICAL_GRIPPER_FIELD_NUMBER: _ClassVar[int]
    DECK_FIELD_NUMBER: _ClassVar[int]
    name: str
    kind: ResourceKind
    model: str
    category: str
    size: Vec3
    location: Vec3
    rotation: Rotation
    identifier: str
    preferred_pickup_location: Vec3
    barcode: Barcode
    metadata: _struct_pb2.Struct
    display_model: str
    container: ContainerSpec
    itemized: ItemizedSpec
    plate: PlateSpec
    tip_rack: TipRackSpec
    head_tool: HeadToolSpec
    tip: TipSpec
    tip_spot: TipSpotSpec
    lid: LidSpec
    holder: HolderSpec
    stack: ResourceStackSpec
    plate_adapter: PlateAdapterSpec
    pipette_channel: PipetteChannelSpec
    tip_mounting_shaft: TipMountingShaftSpec
    n_channel_pipette: NChannelPipetteSpec
    link_body: LinkBodySpec
    mechanical_gripper: MechanicalGripperSpec
    deck: DeckSpec
    def __init__(self, name: _Optional[str] = ..., kind: _Optional[_Union[ResourceKind, str]] = ..., model: _Optional[str] = ..., category: _Optional[str] = ..., size: _Optional[_Union[Vec3, _Mapping]] = ..., location: _Optional[_Union[Vec3, _Mapping]] = ..., rotation: _Optional[_Union[Rotation, _Mapping]] = ..., identifier: _Optional[str] = ..., preferred_pickup_location: _Optional[_Union[Vec3, _Mapping]] = ..., barcode: _Optional[_Union[Barcode, _Mapping]] = ..., metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., display_model: _Optional[str] = ..., container: _Optional[_Union[ContainerSpec, _Mapping]] = ..., itemized: _Optional[_Union[ItemizedSpec, _Mapping]] = ..., plate: _Optional[_Union[PlateSpec, _Mapping]] = ..., tip_rack: _Optional[_Union[TipRackSpec, _Mapping]] = ..., head_tool: _Optional[_Union[HeadToolSpec, _Mapping]] = ..., tip: _Optional[_Union[TipSpec, _Mapping]] = ..., tip_spot: _Optional[_Union[TipSpotSpec, _Mapping]] = ..., lid: _Optional[_Union[LidSpec, _Mapping]] = ..., holder: _Optional[_Union[HolderSpec, _Mapping]] = ..., stack: _Optional[_Union[ResourceStackSpec, _Mapping]] = ..., plate_adapter: _Optional[_Union[PlateAdapterSpec, _Mapping]] = ..., pipette_channel: _Optional[_Union[PipetteChannelSpec, _Mapping]] = ..., tip_mounting_shaft: _Optional[_Union[TipMountingShaftSpec, _Mapping]] = ..., n_channel_pipette: _Optional[_Union[NChannelPipetteSpec, _Mapping]] = ..., link_body: _Optional[_Union[LinkBodySpec, _Mapping]] = ..., mechanical_gripper: _Optional[_Union[MechanicalGripperSpec, _Mapping]] = ..., deck: _Optional[_Union[DeckSpec, _Mapping]] = ...) -> None: ...

class ResourceDefinition(_message.Message):
    __slots__ = ("attributes", "children", "initial_state")
    ATTRIBUTES_FIELD_NUMBER: _ClassVar[int]
    CHILDREN_FIELD_NUMBER: _ClassVar[int]
    INITIAL_STATE_FIELD_NUMBER: _ClassVar[int]
    attributes: ResourceAttributes
    children: _containers.RepeatedCompositeFieldContainer[ResourceDefinition]
    initial_state: ResourceState
    def __init__(self, attributes: _Optional[_Union[ResourceAttributes, _Mapping]] = ..., children: _Optional[_Iterable[_Union[ResourceDefinition, _Mapping]]] = ..., initial_state: _Optional[_Union[ResourceState, _Mapping]] = ...) -> None: ...

class ResourceModel(_message.Message):
    __slots__ = ("name", "definition", "description")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DEFINITION_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    name: str
    definition: ResourceDefinition
    description: str
    def __init__(self, name: _Optional[str] = ..., definition: _Optional[_Union[ResourceDefinition, _Mapping]] = ..., description: _Optional[str] = ...) -> None: ...

class VolumeState(_message.Message):
    __slots__ = ("volume", "pending_volume", "tracking_enabled")
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    PENDING_VOLUME_FIELD_NUMBER: _ClassVar[int]
    TRACKING_ENABLED_FIELD_NUMBER: _ClassVar[int]
    volume: float
    pending_volume: float
    tracking_enabled: bool
    def __init__(self, volume: _Optional[float] = ..., pending_volume: _Optional[float] = ..., tracking_enabled: _Optional[bool] = ...) -> None: ...

class ResourceState(_message.Message):
    __slots__ = ("volume", "tip_tracking_enabled", "jaw_width", "tips_made", "drive_angles")
    class DriveAnglesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    TIP_TRACKING_ENABLED_FIELD_NUMBER: _ClassVar[int]
    JAW_WIDTH_FIELD_NUMBER: _ClassVar[int]
    TIPS_MADE_FIELD_NUMBER: _ClassVar[int]
    DRIVE_ANGLES_FIELD_NUMBER: _ClassVar[int]
    volume: VolumeState
    tip_tracking_enabled: bool
    jaw_width: float
    tips_made: int
    drive_angles: _containers.ScalarMap[str, float]
    def __init__(self, volume: _Optional[_Union[VolumeState, _Mapping]] = ..., tip_tracking_enabled: _Optional[bool] = ..., jaw_width: _Optional[float] = ..., tips_made: _Optional[int] = ..., drive_angles: _Optional[_Mapping[str, float]] = ...) -> None: ...

class ResourceNode(_message.Message):
    __slots__ = ("id", "parent_id", "root_id", "index", "attributes", "state", "child_ids", "pending_operation_ids", "revision")
    ID_FIELD_NUMBER: _ClassVar[int]
    PARENT_ID_FIELD_NUMBER: _ClassVar[int]
    ROOT_ID_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    ATTRIBUTES_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    CHILD_IDS_FIELD_NUMBER: _ClassVar[int]
    PENDING_OPERATION_IDS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    id: bytes
    parent_id: bytes
    root_id: bytes
    index: int
    attributes: ResourceAttributes
    state: ResourceState
    child_ids: _containers.RepeatedScalarFieldContainer[bytes]
    pending_operation_ids: _containers.RepeatedScalarFieldContainer[bytes]
    revision: int
    def __init__(self, id: _Optional[bytes] = ..., parent_id: _Optional[bytes] = ..., root_id: _Optional[bytes] = ..., index: _Optional[int] = ..., attributes: _Optional[_Union[ResourceAttributes, _Mapping]] = ..., state: _Optional[_Union[ResourceState, _Mapping]] = ..., child_ids: _Optional[_Iterable[bytes]] = ..., pending_operation_ids: _Optional[_Iterable[bytes]] = ..., revision: _Optional[int] = ...) -> None: ...

class Issue(_message.Message):
    __slots__ = ("problem", "message", "resource_id", "item")
    PROBLEM_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_FIELD_NUMBER: _ClassVar[int]
    problem: Problem
    message: str
    resource_id: bytes
    item: int
    def __init__(self, problem: _Optional[_Union[Problem, str]] = ..., message: _Optional[str] = ..., resource_id: _Optional[bytes] = ..., item: _Optional[int] = ...) -> None: ...

class OperationItem(_message.Message):
    __slots__ = ("item", "state", "resolved_at")
    ITEM_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_AT_FIELD_NUMBER: _ClassVar[int]
    item: int
    state: ItemState
    resolved_at: _timestamp_pb2.Timestamp
    def __init__(self, item: _Optional[int] = ..., state: _Optional[_Union[ItemState, str]] = ..., resolved_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class Operation(_message.Message):
    __slots__ = ("id", "root_id", "kind", "items", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    ROOT_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: bytes
    root_id: bytes
    kind: str
    items: _containers.RepeatedCompositeFieldContainer[OperationItem]
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[bytes] = ..., root_id: _Optional[bytes] = ..., kind: _Optional[str] = ..., items: _Optional[_Iterable[_Union[OperationItem, _Mapping]]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AssignChild(_message.Message):
    __slots__ = ("child", "parent", "location", "track", "no_location", "index")
    CHILD_FIELD_NUMBER: _ClassVar[int]
    PARENT_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    TRACK_FIELD_NUMBER: _ClassVar[int]
    NO_LOCATION_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    child: ResourceRef
    parent: ResourceRef
    location: Vec3
    track: int
    no_location: bool
    index: int
    def __init__(self, child: _Optional[_Union[ResourceRef, _Mapping]] = ..., parent: _Optional[_Union[ResourceRef, _Mapping]] = ..., location: _Optional[_Union[Vec3, _Mapping]] = ..., track: _Optional[int] = ..., no_location: _Optional[bool] = ..., index: _Optional[int] = ...) -> None: ...

class UnassignChild(_message.Message):
    __slots__ = ("resource",)
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    resource: ResourceRef
    def __init__(self, resource: _Optional[_Union[ResourceRef, _Mapping]] = ...) -> None: ...

class RotationChange(_message.Message):
    __slots__ = ("resource", "by", "to", "pivot")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    BY_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    PIVOT_FIELD_NUMBER: _ClassVar[int]
    resource: ResourceRef
    by: Rotation
    to: PartialRotation
    pivot: Vec3
    def __init__(self, resource: _Optional[_Union[ResourceRef, _Mapping]] = ..., by: _Optional[_Union[Rotation, _Mapping]] = ..., to: _Optional[_Union[PartialRotation, _Mapping]] = ..., pivot: _Optional[_Union[Vec3, _Mapping]] = ...) -> None: ...

class SetMetadata(_message.Message):
    __slots__ = ("resource", "metadata", "replace")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    REPLACE_FIELD_NUMBER: _ClassVar[int]
    resource: ResourceRef
    metadata: _struct_pb2.Struct
    replace: bool
    def __init__(self, resource: _Optional[_Union[ResourceRef, _Mapping]] = ..., metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., replace: _Optional[bool] = ...) -> None: ...

class SetBarcode(_message.Message):
    __slots__ = ("resource", "barcode")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    BARCODE_FIELD_NUMBER: _ClassVar[int]
    resource: ResourceRef
    barcode: Barcode
    def __init__(self, resource: _Optional[_Union[ResourceRef, _Mapping]] = ..., barcode: _Optional[_Union[Barcode, _Mapping]] = ...) -> None: ...

class SetPreferredPickupLocation(_message.Message):
    __slots__ = ("resource", "location")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    resource: ResourceRef
    location: Vec3
    def __init__(self, resource: _Optional[_Union[ResourceRef, _Mapping]] = ..., location: _Optional[_Union[Vec3, _Mapping]] = ...) -> None: ...

class SetTracking(_message.Message):
    __slots__ = ("scope", "volume_tracking", "tip_tracking")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    VOLUME_TRACKING_FIELD_NUMBER: _ClassVar[int]
    TIP_TRACKING_FIELD_NUMBER: _ClassVar[int]
    scope: ResourceRef
    volume_tracking: bool
    tip_tracking: bool
    def __init__(self, scope: _Optional[_Union[ResourceRef, _Mapping]] = ..., volume_tracking: _Optional[bool] = ..., tip_tracking: _Optional[bool] = ...) -> None: ...

class SetVolume(_message.Message):
    __slots__ = ("resource", "volume")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    resource: ResourceRef
    volume: float
    def __init__(self, resource: _Optional[_Union[ResourceRef, _Mapping]] = ..., volume: _Optional[float] = ...) -> None: ...

class SetTipPresence(_message.Message):
    __slots__ = ("spots", "present")
    SPOTS_FIELD_NUMBER: _ClassVar[int]
    PRESENT_FIELD_NUMBER: _ClassVar[int]
    spots: _containers.RepeatedCompositeFieldContainer[ResourceRef]
    present: bool
    def __init__(self, spots: _Optional[_Iterable[_Union[ResourceRef, _Mapping]]] = ..., present: _Optional[bool] = ...) -> None: ...

class SetDisplayModel(_message.Message):
    __slots__ = ("resource", "display_model")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_MODEL_FIELD_NUMBER: _ClassVar[int]
    resource: ResourceRef
    display_model: str
    def __init__(self, resource: _Optional[_Union[ResourceRef, _Mapping]] = ..., display_model: _Optional[str] = ...) -> None: ...

class ClearTrash(_message.Message):
    __slots__ = ("trash",)
    TRASH_FIELD_NUMBER: _ClassVar[int]
    trash: ResourceRef
    def __init__(self, trash: _Optional[_Union[ResourceRef, _Mapping]] = ...) -> None: ...
