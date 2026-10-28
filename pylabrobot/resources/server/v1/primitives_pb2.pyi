from pylabrobot.resources.server.v1 import types_pb2 as _types_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SetLocation(_message.Message):
    __slots__ = ("resource", "location")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    resource: _types_pb2.ResourceRef
    location: _types_pb2.PartialVec3
    def __init__(self, resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., location: _Optional[_Union[_types_pb2.PartialVec3, _Mapping]] = ...) -> None: ...

class AddLiquid(_message.Message):
    __slots__ = ("resource", "volume")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    resource: _types_pb2.ResourceRef
    volume: float
    def __init__(self, resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., volume: _Optional[float] = ...) -> None: ...

class RemoveLiquid(_message.Message):
    __slots__ = ("resource", "volume")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    resource: _types_pb2.ResourceRef
    volume: float
    def __init__(self, resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., volume: _Optional[float] = ...) -> None: ...

class SetJawWidth(_message.Message):
    __slots__ = ("gripper", "jaw_width")
    GRIPPER_FIELD_NUMBER: _ClassVar[int]
    JAW_WIDTH_FIELD_NUMBER: _ClassVar[int]
    gripper: _types_pb2.ResourceRef
    jaw_width: float
    def __init__(self, gripper: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., jaw_width: _Optional[float] = ...) -> None: ...

class Change(_message.Message):
    __slots__ = ("assign_child", "unassign_child", "set_location", "rotation", "set_volume", "add_liquid", "remove_liquid", "set_tracking", "set_tip_presence", "set_metadata", "set_barcode", "set_jaw_width")
    ASSIGN_CHILD_FIELD_NUMBER: _ClassVar[int]
    UNASSIGN_CHILD_FIELD_NUMBER: _ClassVar[int]
    SET_LOCATION_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    SET_VOLUME_FIELD_NUMBER: _ClassVar[int]
    ADD_LIQUID_FIELD_NUMBER: _ClassVar[int]
    REMOVE_LIQUID_FIELD_NUMBER: _ClassVar[int]
    SET_TRACKING_FIELD_NUMBER: _ClassVar[int]
    SET_TIP_PRESENCE_FIELD_NUMBER: _ClassVar[int]
    SET_METADATA_FIELD_NUMBER: _ClassVar[int]
    SET_BARCODE_FIELD_NUMBER: _ClassVar[int]
    SET_JAW_WIDTH_FIELD_NUMBER: _ClassVar[int]
    assign_child: _types_pb2.AssignChild
    unassign_child: _types_pb2.UnassignChild
    set_location: SetLocation
    rotation: _types_pb2.RotationChange
    set_volume: _types_pb2.SetVolume
    add_liquid: AddLiquid
    remove_liquid: RemoveLiquid
    set_tracking: _types_pb2.SetTracking
    set_tip_presence: _types_pb2.SetTipPresence
    set_metadata: _types_pb2.SetMetadata
    set_barcode: _types_pb2.SetBarcode
    set_jaw_width: SetJawWidth
    def __init__(self, assign_child: _Optional[_Union[_types_pb2.AssignChild, _Mapping]] = ..., unassign_child: _Optional[_Union[_types_pb2.UnassignChild, _Mapping]] = ..., set_location: _Optional[_Union[SetLocation, _Mapping]] = ..., rotation: _Optional[_Union[_types_pb2.RotationChange, _Mapping]] = ..., set_volume: _Optional[_Union[_types_pb2.SetVolume, _Mapping]] = ..., add_liquid: _Optional[_Union[AddLiquid, _Mapping]] = ..., remove_liquid: _Optional[_Union[RemoveLiquid, _Mapping]] = ..., set_tracking: _Optional[_Union[_types_pb2.SetTracking, _Mapping]] = ..., set_tip_presence: _Optional[_Union[_types_pb2.SetTipPresence, _Mapping]] = ..., set_metadata: _Optional[_Union[_types_pb2.SetMetadata, _Mapping]] = ..., set_barcode: _Optional[_Union[_types_pb2.SetBarcode, _Mapping]] = ..., set_jaw_width: _Optional[_Union[SetJawWidth, _Mapping]] = ...) -> None: ...

class ChangeGroup(_message.Message):
    __slots__ = ("changes",)
    CHANGES_FIELD_NUMBER: _ClassVar[int]
    changes: _containers.RepeatedCompositeFieldContainer[Change]
    def __init__(self, changes: _Optional[_Iterable[_Union[Change, _Mapping]]] = ...) -> None: ...

class ApplyChangesRequest(_message.Message):
    __slots__ = ("lock_tokens", "groups", "staged", "on_tracking_problem")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    GROUPS_FIELD_NUMBER: _ClassVar[int]
    STAGED_FIELD_NUMBER: _ClassVar[int]
    ON_TRACKING_PROBLEM_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    groups: _containers.RepeatedCompositeFieldContainer[ChangeGroup]
    staged: bool
    on_tracking_problem: _types_pb2.OnTrackingProblem
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., groups: _Optional[_Iterable[_Union[ChangeGroup, _Mapping]]] = ..., staged: _Optional[bool] = ..., on_tracking_problem: _Optional[_Union[_types_pb2.OnTrackingProblem, str]] = ...) -> None: ...

class ApplyChangesResponse(_message.Message):
    __slots__ = ("operation", "nodes", "issues", "revision")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    NODES_FIELD_NUMBER: _ClassVar[int]
    ISSUES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    operation: _types_pb2.Operation
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    issues: _containers.RepeatedCompositeFieldContainer[_types_pb2.Issue]
    revision: int
    def __init__(self, operation: _Optional[_Union[_types_pb2.Operation, _Mapping]] = ..., nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., issues: _Optional[_Iterable[_Union[_types_pb2.Issue, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...
