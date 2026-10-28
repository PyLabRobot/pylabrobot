import datetime

from google.protobuf import duration_pb2 as _duration_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from pylabrobot.resources.server.v1 import types_pb2 as _types_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TransferDirection(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TRANSFER_DIRECTION_UNSPECIFIED: _ClassVar[TransferDirection]
    TRANSFER_DIRECTION_ASPIRATE: _ClassVar[TransferDirection]
    TRANSFER_DIRECTION_DISPENSE: _ClassVar[TransferDirection]

class ChangeAspect(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHANGE_ASPECT_UNSPECIFIED: _ClassVar[ChangeAspect]
    CHANGE_ASPECT_PLACEMENT: _ClassVar[ChangeAspect]
    CHANGE_ASPECT_STATE: _ClassVar[ChangeAspect]
    CHANGE_ASPECT_ATTRIBUTES: _ClassVar[ChangeAspect]
    CHANGE_ASPECT_CHILDREN: _ClassVar[ChangeAspect]
TRANSFER_DIRECTION_UNSPECIFIED: TransferDirection
TRANSFER_DIRECTION_ASPIRATE: TransferDirection
TRANSFER_DIRECTION_DISPENSE: TransferDirection
CHANGE_ASPECT_UNSPECIFIED: ChangeAspect
CHANGE_ASPECT_PLACEMENT: ChangeAspect
CHANGE_ASPECT_STATE: ChangeAspect
CHANGE_ASPECT_ATTRIBUTES: ChangeAspect
CHANGE_ASPECT_CHILDREN: ChangeAspect

class GetServerInfoRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ServerInfo(_message.Message):
    __slots__ = ("api_version", "implementation", "implementation_version", "instance_id", "revision")
    API_VERSION_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTATION_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTATION_VERSION_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    api_version: str
    implementation: str
    implementation_version: str
    instance_id: str
    revision: int
    def __init__(self, api_version: _Optional[str] = ..., implementation: _Optional[str] = ..., implementation_version: _Optional[str] = ..., instance_id: _Optional[str] = ..., revision: _Optional[int] = ...) -> None: ...

class Lock(_message.Message):
    __slots__ = ("token", "resource_id", "holder", "acquired_at")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    HOLDER_FIELD_NUMBER: _ClassVar[int]
    ACQUIRED_AT_FIELD_NUMBER: _ClassVar[int]
    token: bytes
    resource_id: bytes
    holder: str
    acquired_at: _timestamp_pb2.Timestamp
    def __init__(self, token: _Optional[bytes] = ..., resource_id: _Optional[bytes] = ..., holder: _Optional[str] = ..., acquired_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AcquireLockRequest(_message.Message):
    __slots__ = ("resource", "holder", "wait")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    HOLDER_FIELD_NUMBER: _ClassVar[int]
    WAIT_FIELD_NUMBER: _ClassVar[int]
    resource: _types_pb2.ResourceRef
    holder: str
    wait: _duration_pb2.Duration
    def __init__(self, resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., holder: _Optional[str] = ..., wait: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ...) -> None: ...

class ReleaseLockRequest(_message.Message):
    __slots__ = ("resource", "token", "force")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    FORCE_FIELD_NUMBER: _ClassVar[int]
    resource: _types_pb2.ResourceRef
    token: bytes
    force: bool
    def __init__(self, resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., token: _Optional[bytes] = ..., force: _Optional[bool] = ...) -> None: ...

class ReleaseLockResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetLockRequest(_message.Message):
    __slots__ = ("resource",)
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    resource: _types_pb2.ResourceRef
    def __init__(self, resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ...) -> None: ...

class GetLockResponse(_message.Message):
    __slots__ = ("lock",)
    LOCK_FIELD_NUMBER: _ClassVar[int]
    lock: Lock
    def __init__(self, lock: _Optional[_Union[Lock, _Mapping]] = ...) -> None: ...

class PutModelRequest(_message.Message):
    __slots__ = ("model",)
    MODEL_FIELD_NUMBER: _ClassVar[int]
    model: _types_pb2.ResourceModel
    def __init__(self, model: _Optional[_Union[_types_pb2.ResourceModel, _Mapping]] = ...) -> None: ...

class GetModelRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class ListModelsRequest(_message.Message):
    __slots__ = ("kind", "page_size", "page_token")
    KIND_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    kind: _types_pb2.ResourceKind
    page_size: int
    page_token: str
    def __init__(self, kind: _Optional[_Union[_types_pb2.ResourceKind, str]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class ListModelsResponse(_message.Message):
    __slots__ = ("models", "next_page_token")
    MODELS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    models: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceModel]
    next_page_token: str
    def __init__(self, models: _Optional[_Iterable[_Union[_types_pb2.ResourceModel, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class CreateResource(_message.Message):
    __slots__ = ("model", "definition", "name", "assign")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    DEFINITION_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ASSIGN_FIELD_NUMBER: _ClassVar[int]
    model: str
    definition: _types_pb2.ResourceDefinition
    name: str
    assign: _types_pb2.AssignChild
    def __init__(self, model: _Optional[str] = ..., definition: _Optional[_Union[_types_pb2.ResourceDefinition, _Mapping]] = ..., name: _Optional[str] = ..., assign: _Optional[_Union[_types_pb2.AssignChild, _Mapping]] = ...) -> None: ...

class CreateResourcesRequest(_message.Message):
    __slots__ = ("lock_tokens", "resources")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    resources: _containers.RepeatedCompositeFieldContainer[CreateResource]
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., resources: _Optional[_Iterable[_Union[CreateResource, _Mapping]]] = ...) -> None: ...

class CreateResourcesResponse(_message.Message):
    __slots__ = ("nodes", "revision")
    NODES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    revision: int
    def __init__(self, nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class DeleteResourcesRequest(_message.Message):
    __slots__ = ("lock_tokens", "resources")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    resources: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceRef]
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., resources: _Optional[_Iterable[_Union[_types_pb2.ResourceRef, _Mapping]]] = ...) -> None: ...

class DeleteResourcesResponse(_message.Message):
    __slots__ = ("revision",)
    REVISION_FIELD_NUMBER: _ClassVar[int]
    revision: int
    def __init__(self, revision: _Optional[int] = ...) -> None: ...

class AssignResourcesRequest(_message.Message):
    __slots__ = ("lock_tokens", "assignments")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    ASSIGNMENTS_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    assignments: _containers.RepeatedCompositeFieldContainer[_types_pb2.AssignChild]
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., assignments: _Optional[_Iterable[_Union[_types_pb2.AssignChild, _Mapping]]] = ...) -> None: ...

class AssignResourcesResponse(_message.Message):
    __slots__ = ("nodes", "revision")
    NODES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    revision: int
    def __init__(self, nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class UnassignResourcesRequest(_message.Message):
    __slots__ = ("lock_tokens", "resources")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    resources: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceRef]
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., resources: _Optional[_Iterable[_Union[_types_pb2.ResourceRef, _Mapping]]] = ...) -> None: ...

class UnassignResourcesResponse(_message.Message):
    __slots__ = ("nodes", "revision")
    NODES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    revision: int
    def __init__(self, nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class ResourceUpdate(_message.Message):
    __slots__ = ("rotation", "metadata", "barcode", "tracking", "volume", "tip_presence", "preferred_pickup_location", "display_model", "clear_trash")
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    BARCODE_FIELD_NUMBER: _ClassVar[int]
    TRACKING_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    TIP_PRESENCE_FIELD_NUMBER: _ClassVar[int]
    PREFERRED_PICKUP_LOCATION_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_MODEL_FIELD_NUMBER: _ClassVar[int]
    CLEAR_TRASH_FIELD_NUMBER: _ClassVar[int]
    rotation: _types_pb2.RotationChange
    metadata: _types_pb2.SetMetadata
    barcode: _types_pb2.SetBarcode
    tracking: _types_pb2.SetTracking
    volume: _types_pb2.SetVolume
    tip_presence: _types_pb2.SetTipPresence
    preferred_pickup_location: _types_pb2.SetPreferredPickupLocation
    display_model: _types_pb2.SetDisplayModel
    clear_trash: _types_pb2.ClearTrash
    def __init__(self, rotation: _Optional[_Union[_types_pb2.RotationChange, _Mapping]] = ..., metadata: _Optional[_Union[_types_pb2.SetMetadata, _Mapping]] = ..., barcode: _Optional[_Union[_types_pb2.SetBarcode, _Mapping]] = ..., tracking: _Optional[_Union[_types_pb2.SetTracking, _Mapping]] = ..., volume: _Optional[_Union[_types_pb2.SetVolume, _Mapping]] = ..., tip_presence: _Optional[_Union[_types_pb2.SetTipPresence, _Mapping]] = ..., preferred_pickup_location: _Optional[_Union[_types_pb2.SetPreferredPickupLocation, _Mapping]] = ..., display_model: _Optional[_Union[_types_pb2.SetDisplayModel, _Mapping]] = ..., clear_trash: _Optional[_Union[_types_pb2.ClearTrash, _Mapping]] = ...) -> None: ...

class UpdateResourcesRequest(_message.Message):
    __slots__ = ("lock_tokens", "updates", "on_tracking_problem")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    UPDATES_FIELD_NUMBER: _ClassVar[int]
    ON_TRACKING_PROBLEM_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    updates: _containers.RepeatedCompositeFieldContainer[ResourceUpdate]
    on_tracking_problem: _types_pb2.OnTrackingProblem
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., updates: _Optional[_Iterable[_Union[ResourceUpdate, _Mapping]]] = ..., on_tracking_problem: _Optional[_Union[_types_pb2.OnTrackingProblem, str]] = ...) -> None: ...

class UpdateResourcesResponse(_message.Message):
    __slots__ = ("nodes", "revision", "issues")
    NODES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    ISSUES_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    revision: int
    issues: _containers.RepeatedCompositeFieldContainer[_types_pb2.Issue]
    def __init__(self, nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., revision: _Optional[int] = ..., issues: _Optional[_Iterable[_Union[_types_pb2.Issue, _Mapping]]] = ...) -> None: ...

class GetResourcesRequest(_message.Message):
    __slots__ = ("resources", "depth", "view", "ancestors")
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    DEPTH_FIELD_NUMBER: _ClassVar[int]
    VIEW_FIELD_NUMBER: _ClassVar[int]
    ANCESTORS_FIELD_NUMBER: _ClassVar[int]
    resources: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceRef]
    depth: int
    view: _types_pb2.View
    ancestors: bool
    def __init__(self, resources: _Optional[_Iterable[_Union[_types_pb2.ResourceRef, _Mapping]]] = ..., depth: _Optional[int] = ..., view: _Optional[_Union[_types_pb2.View, str]] = ..., ancestors: _Optional[bool] = ...) -> None: ...

class GetResourcesResponse(_message.Message):
    __slots__ = ("nodes", "revision")
    NODES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    revision: int
    def __init__(self, nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class ResourceFilter(_message.Message):
    __slots__ = ("kinds", "models", "categories", "name_prefix", "names")
    KINDS_FIELD_NUMBER: _ClassVar[int]
    MODELS_FIELD_NUMBER: _ClassVar[int]
    CATEGORIES_FIELD_NUMBER: _ClassVar[int]
    NAME_PREFIX_FIELD_NUMBER: _ClassVar[int]
    NAMES_FIELD_NUMBER: _ClassVar[int]
    kinds: _containers.RepeatedScalarFieldContainer[_types_pb2.ResourceKind]
    models: _containers.RepeatedScalarFieldContainer[str]
    categories: _containers.RepeatedScalarFieldContainer[str]
    name_prefix: str
    names: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, kinds: _Optional[_Iterable[_Union[_types_pb2.ResourceKind, str]]] = ..., models: _Optional[_Iterable[str]] = ..., categories: _Optional[_Iterable[str]] = ..., name_prefix: _Optional[str] = ..., names: _Optional[_Iterable[str]] = ...) -> None: ...

class FindResourcesRequest(_message.Message):
    __slots__ = ("scope", "filter", "view", "page_size", "page_token")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    VIEW_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    scope: _types_pb2.ResourceRef
    filter: ResourceFilter
    view: _types_pb2.View
    page_size: int
    page_token: str
    def __init__(self, scope: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., filter: _Optional[_Union[ResourceFilter, _Mapping]] = ..., view: _Optional[_Union[_types_pb2.View, str]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class FindResourcesResponse(_message.Message):
    __slots__ = ("nodes", "next_page_token", "revision")
    NODES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    next_page_token: str
    revision: int
    def __init__(self, nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., next_page_token: _Optional[str] = ..., revision: _Optional[int] = ...) -> None: ...

class ListRootsRequest(_message.Message):
    __slots__ = ("filter", "page_size", "page_token")
    FILTER_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    filter: ResourceFilter
    page_size: int
    page_token: str
    def __init__(self, filter: _Optional[_Union[ResourceFilter, _Mapping]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class ListRootsResponse(_message.Message):
    __slots__ = ("roots", "next_page_token", "revision")
    ROOTS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    roots: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    next_page_token: str
    revision: int
    def __init__(self, roots: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., next_page_token: _Optional[str] = ..., revision: _Optional[int] = ...) -> None: ...

class GeometryQuery(_message.Message):
    __slots__ = ("resource", "frame", "anchors")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    FRAME_FIELD_NUMBER: _ClassVar[int]
    ANCHORS_FIELD_NUMBER: _ClassVar[int]
    resource: _types_pb2.ResourceRef
    frame: _types_pb2.ResourceRef
    anchors: _containers.RepeatedCompositeFieldContainer[_types_pb2.Anchor]
    def __init__(self, resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., frame: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., anchors: _Optional[_Iterable[_Union[_types_pb2.Anchor, _Mapping]]] = ...) -> None: ...

class Geometry(_message.Message):
    __slots__ = ("resource_id", "locations", "rotation_in_frame", "absolute_rotation", "absolute_size", "size", "highest_point", "issue")
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    LOCATIONS_FIELD_NUMBER: _ClassVar[int]
    ROTATION_IN_FRAME_FIELD_NUMBER: _ClassVar[int]
    ABSOLUTE_ROTATION_FIELD_NUMBER: _ClassVar[int]
    ABSOLUTE_SIZE_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    HIGHEST_POINT_FIELD_NUMBER: _ClassVar[int]
    ISSUE_FIELD_NUMBER: _ClassVar[int]
    resource_id: bytes
    locations: _containers.RepeatedCompositeFieldContainer[_types_pb2.Vec3]
    rotation_in_frame: _types_pb2.Rotation
    absolute_rotation: _types_pb2.Rotation
    absolute_size: _types_pb2.Vec3
    size: _types_pb2.Vec3
    highest_point: float
    issue: _types_pb2.Issue
    def __init__(self, resource_id: _Optional[bytes] = ..., locations: _Optional[_Iterable[_Union[_types_pb2.Vec3, _Mapping]]] = ..., rotation_in_frame: _Optional[_Union[_types_pb2.Rotation, _Mapping]] = ..., absolute_rotation: _Optional[_Union[_types_pb2.Rotation, _Mapping]] = ..., absolute_size: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., size: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., highest_point: _Optional[float] = ..., issue: _Optional[_Union[_types_pb2.Issue, _Mapping]] = ...) -> None: ...

class ResolveGeometryRequest(_message.Message):
    __slots__ = ("queries", "view")
    QUERIES_FIELD_NUMBER: _ClassVar[int]
    VIEW_FIELD_NUMBER: _ClassVar[int]
    queries: _containers.RepeatedCompositeFieldContainer[GeometryQuery]
    view: _types_pb2.View
    def __init__(self, queries: _Optional[_Iterable[_Union[GeometryQuery, _Mapping]]] = ..., view: _Optional[_Union[_types_pb2.View, str]] = ...) -> None: ...

class ResolveGeometryResponse(_message.Message):
    __slots__ = ("results", "revision")
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[Geometry]
    revision: int
    def __init__(self, results: _Optional[_Iterable[_Union[Geometry, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class HeightVolumeQuery(_message.Message):
    __slots__ = ("container", "height", "volume")
    CONTAINER_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    container: _types_pb2.ResourceRef
    height: float
    volume: float
    def __init__(self, container: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., height: _Optional[float] = ..., volume: _Optional[float] = ...) -> None: ...

class HeightVolumeResult(_message.Message):
    __slots__ = ("height", "volume", "issue")
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    ISSUE_FIELD_NUMBER: _ClassVar[int]
    height: float
    volume: float
    issue: _types_pb2.Issue
    def __init__(self, height: _Optional[float] = ..., volume: _Optional[float] = ..., issue: _Optional[_Union[_types_pb2.Issue, _Mapping]] = ...) -> None: ...

class ConvertHeightVolumeRequest(_message.Message):
    __slots__ = ("queries",)
    QUERIES_FIELD_NUMBER: _ClassVar[int]
    queries: _containers.RepeatedCompositeFieldContainer[HeightVolumeQuery]
    def __init__(self, queries: _Optional[_Iterable[_Union[HeightVolumeQuery, _Mapping]]] = ...) -> None: ...

class ConvertHeightVolumeResponse(_message.Message):
    __slots__ = ("results",)
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[HeightVolumeResult]
    def __init__(self, results: _Optional[_Iterable[_Union[HeightVolumeResult, _Mapping]]] = ...) -> None: ...

class FindResourcesAtRequest(_message.Message):
    __slots__ = ("frame", "location", "exclude", "view")
    FRAME_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    EXCLUDE_FIELD_NUMBER: _ClassVar[int]
    VIEW_FIELD_NUMBER: _ClassVar[int]
    frame: _types_pb2.ResourceRef
    location: _types_pb2.Vec3
    exclude: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceRef]
    view: _types_pb2.View
    def __init__(self, frame: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., location: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., exclude: _Optional[_Iterable[_Union[_types_pb2.ResourceRef, _Mapping]]] = ..., view: _Optional[_Union[_types_pb2.View, str]] = ...) -> None: ...

class FindResourcesAtResponse(_message.Message):
    __slots__ = ("nodes", "revision")
    NODES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    revision: int
    def __init__(self, nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class DevicePosition(_message.Message):
    __slots__ = ("resource", "frame", "location", "reference_point", "rotation", "jaw_width", "drive_angles")
    class DriveAnglesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    FRAME_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_POINT_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    JAW_WIDTH_FIELD_NUMBER: _ClassVar[int]
    DRIVE_ANGLES_FIELD_NUMBER: _ClassVar[int]
    resource: _types_pb2.ResourceRef
    frame: _types_pb2.ResourceRef
    location: _types_pb2.PartialVec3
    reference_point: _types_pb2.Vec3
    rotation: _types_pb2.RotationChange
    jaw_width: float
    drive_angles: _containers.ScalarMap[str, float]
    def __init__(self, resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., frame: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., location: _Optional[_Union[_types_pb2.PartialVec3, _Mapping]] = ..., reference_point: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., rotation: _Optional[_Union[_types_pb2.RotationChange, _Mapping]] = ..., jaw_width: _Optional[float] = ..., drive_angles: _Optional[_Mapping[str, float]] = ...) -> None: ...

class RecordDevicePositionsRequest(_message.Message):
    __slots__ = ("lock_tokens", "positions")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    POSITIONS_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    positions: _containers.RepeatedCompositeFieldContainer[DevicePosition]
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., positions: _Optional[_Iterable[_Union[DevicePosition, _Mapping]]] = ...) -> None: ...

class RecordDevicePositionsResponse(_message.Message):
    __slots__ = ("nodes", "revision")
    NODES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    revision: int
    def __init__(self, nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class Measurement(_message.Message):
    __slots__ = ("volume", "liquid_height", "tool_present", "barcode", "resource")
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    LIQUID_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    TOOL_PRESENT_FIELD_NUMBER: _ClassVar[int]
    BARCODE_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    volume: float
    liquid_height: float
    tool_present: bool
    barcode: _types_pb2.Barcode
    resource: _types_pb2.ResourceRef
    def __init__(self, volume: _Optional[float] = ..., liquid_height: _Optional[float] = ..., tool_present: _Optional[bool] = ..., barcode: _Optional[_Union[_types_pb2.Barcode, _Mapping]] = ..., resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ...) -> None: ...

class MeasurementResult(_message.Message):
    __slots__ = ("previous_volume", "volume", "issue")
    PREVIOUS_VOLUME_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    ISSUE_FIELD_NUMBER: _ClassVar[int]
    previous_volume: float
    volume: float
    issue: _types_pb2.Issue
    def __init__(self, previous_volume: _Optional[float] = ..., volume: _Optional[float] = ..., issue: _Optional[_Union[_types_pb2.Issue, _Mapping]] = ...) -> None: ...

class RecordMeasurementsRequest(_message.Message):
    __slots__ = ("lock_tokens", "measurements")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    MEASUREMENTS_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    measurements: _containers.RepeatedCompositeFieldContainer[Measurement]
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., measurements: _Optional[_Iterable[_Union[Measurement, _Mapping]]] = ...) -> None: ...

class RecordMeasurementsResponse(_message.Message):
    __slots__ = ("results", "revision")
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[MeasurementResult]
    revision: int
    def __init__(self, results: _Optional[_Iterable[_Union[MeasurementResult, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class Placement(_message.Message):
    __slots__ = ("resource_id", "kind", "bottom", "cavity_bottom", "top", "absolute_size", "rotation_in_frame", "container", "volume")
    RESOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    BOTTOM_FIELD_NUMBER: _ClassVar[int]
    CAVITY_BOTTOM_FIELD_NUMBER: _ClassVar[int]
    TOP_FIELD_NUMBER: _ClassVar[int]
    ABSOLUTE_SIZE_FIELD_NUMBER: _ClassVar[int]
    ROTATION_IN_FRAME_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    resource_id: bytes
    kind: _types_pb2.ResourceKind
    bottom: _types_pb2.Vec3
    cavity_bottom: _types_pb2.Vec3
    top: _types_pb2.Vec3
    absolute_size: _types_pb2.Vec3
    rotation_in_frame: _types_pb2.Rotation
    container: _types_pb2.ContainerSpec
    volume: _types_pb2.VolumeState
    def __init__(self, resource_id: _Optional[bytes] = ..., kind: _Optional[_Union[_types_pb2.ResourceKind, str]] = ..., bottom: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., cavity_bottom: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., top: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., absolute_size: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., rotation_in_frame: _Optional[_Union[_types_pb2.Rotation, _Mapping]] = ..., container: _Optional[_Union[_types_pb2.ContainerSpec, _Mapping]] = ..., volume: _Optional[_Union[_types_pb2.VolumeState, _Mapping]] = ...) -> None: ...

class ToolPickupItem(_message.Message):
    __slots__ = ("source", "shaft")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    SHAFT_FIELD_NUMBER: _ClassVar[int]
    source: _types_pb2.ResourceRef
    shaft: _types_pb2.ResourceRef
    def __init__(self, source: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., shaft: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ...) -> None: ...

class BeginToolPickupRequest(_message.Message):
    __slots__ = ("lock_tokens", "frame", "items", "context_shafts", "on_tracking_problem")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    FRAME_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_SHAFTS_FIELD_NUMBER: _ClassVar[int]
    ON_TRACKING_PROBLEM_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    frame: _types_pb2.ResourceRef
    items: _containers.RepeatedCompositeFieldContainer[ToolPickupItem]
    context_shafts: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceRef]
    on_tracking_problem: _types_pb2.OnTrackingProblem
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., frame: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., items: _Optional[_Iterable[_Union[ToolPickupItem, _Mapping]]] = ..., context_shafts: _Optional[_Iterable[_Union[_types_pb2.ResourceRef, _Mapping]]] = ..., on_tracking_problem: _Optional[_Union[_types_pb2.OnTrackingProblem, str]] = ...) -> None: ...

class ToolPickupPlan(_message.Message):
    __slots__ = ("item", "tool", "source")
    ITEM_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    item: int
    tool: _types_pb2.ResourceNode
    source: Placement
    def __init__(self, item: _Optional[int] = ..., tool: _Optional[_Union[_types_pb2.ResourceNode, _Mapping]] = ..., source: _Optional[_Union[Placement, _Mapping]] = ...) -> None: ...

class ShaftContext(_message.Message):
    __slots__ = ("shaft_id", "tool")
    SHAFT_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    shaft_id: bytes
    tool: _types_pb2.ResourceNode
    def __init__(self, shaft_id: _Optional[bytes] = ..., tool: _Optional[_Union[_types_pb2.ResourceNode, _Mapping]] = ...) -> None: ...

class BeginToolPickupResponse(_message.Message):
    __slots__ = ("operation", "items", "context_shafts", "issues", "revision")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_SHAFTS_FIELD_NUMBER: _ClassVar[int]
    ISSUES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    operation: _types_pb2.Operation
    items: _containers.RepeatedCompositeFieldContainer[ToolPickupPlan]
    context_shafts: _containers.RepeatedCompositeFieldContainer[ShaftContext]
    issues: _containers.RepeatedCompositeFieldContainer[_types_pb2.Issue]
    revision: int
    def __init__(self, operation: _Optional[_Union[_types_pb2.Operation, _Mapping]] = ..., items: _Optional[_Iterable[_Union[ToolPickupPlan, _Mapping]]] = ..., context_shafts: _Optional[_Iterable[_Union[ShaftContext, _Mapping]]] = ..., issues: _Optional[_Iterable[_Union[_types_pb2.Issue, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class ToolDropItem(_message.Message):
    __slots__ = ("shaft", "target", "origin")
    SHAFT_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    shaft: _types_pb2.ResourceRef
    target: _types_pb2.ResourceRef
    origin: bool
    def __init__(self, shaft: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., target: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., origin: _Optional[bool] = ...) -> None: ...

class BeginToolDropRequest(_message.Message):
    __slots__ = ("lock_tokens", "frame", "items", "context_shafts", "on_tracking_problem")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    FRAME_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_SHAFTS_FIELD_NUMBER: _ClassVar[int]
    ON_TRACKING_PROBLEM_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    frame: _types_pb2.ResourceRef
    items: _containers.RepeatedCompositeFieldContainer[ToolDropItem]
    context_shafts: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceRef]
    on_tracking_problem: _types_pb2.OnTrackingProblem
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., frame: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., items: _Optional[_Iterable[_Union[ToolDropItem, _Mapping]]] = ..., context_shafts: _Optional[_Iterable[_Union[_types_pb2.ResourceRef, _Mapping]]] = ..., on_tracking_problem: _Optional[_Union[_types_pb2.OnTrackingProblem, str]] = ...) -> None: ...

class ToolDropPlan(_message.Message):
    __slots__ = ("item", "tool", "destination")
    ITEM_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_FIELD_NUMBER: _ClassVar[int]
    item: int
    tool: _types_pb2.ResourceNode
    destination: Placement
    def __init__(self, item: _Optional[int] = ..., tool: _Optional[_Union[_types_pb2.ResourceNode, _Mapping]] = ..., destination: _Optional[_Union[Placement, _Mapping]] = ...) -> None: ...

class BeginToolDropResponse(_message.Message):
    __slots__ = ("operation", "items", "context_shafts", "issues", "revision")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_SHAFTS_FIELD_NUMBER: _ClassVar[int]
    ISSUES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    operation: _types_pb2.Operation
    items: _containers.RepeatedCompositeFieldContainer[ToolDropPlan]
    context_shafts: _containers.RepeatedCompositeFieldContainer[ShaftContext]
    issues: _containers.RepeatedCompositeFieldContainer[_types_pb2.Issue]
    revision: int
    def __init__(self, operation: _Optional[_Union[_types_pb2.Operation, _Mapping]] = ..., items: _Optional[_Iterable[_Union[ToolDropPlan, _Mapping]]] = ..., context_shafts: _Optional[_Iterable[_Union[ShaftContext, _Mapping]]] = ..., issues: _Optional[_Iterable[_Union[_types_pb2.Issue, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class TransferItem(_message.Message):
    __slots__ = ("container", "shaft", "volume")
    CONTAINER_FIELD_NUMBER: _ClassVar[int]
    SHAFT_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    container: _types_pb2.ResourceRef
    shaft: _types_pb2.ResourceRef
    volume: float
    def __init__(self, container: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., shaft: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., volume: _Optional[float] = ...) -> None: ...

class BeginLiquidTransferRequest(_message.Message):
    __slots__ = ("lock_tokens", "frame", "direction", "items", "on_tracking_problem")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    FRAME_FIELD_NUMBER: _ClassVar[int]
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    ON_TRACKING_PROBLEM_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    frame: _types_pb2.ResourceRef
    direction: TransferDirection
    items: _containers.RepeatedCompositeFieldContainer[TransferItem]
    on_tracking_problem: _types_pb2.OnTrackingProblem
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., frame: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., direction: _Optional[_Union[TransferDirection, str]] = ..., items: _Optional[_Iterable[_Union[TransferItem, _Mapping]]] = ..., on_tracking_problem: _Optional[_Union[_types_pb2.OnTrackingProblem, str]] = ...) -> None: ...

class TransferPlan(_message.Message):
    __slots__ = ("item", "container", "tip")
    ITEM_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_FIELD_NUMBER: _ClassVar[int]
    TIP_FIELD_NUMBER: _ClassVar[int]
    item: int
    container: Placement
    tip: _types_pb2.ResourceNode
    def __init__(self, item: _Optional[int] = ..., container: _Optional[_Union[Placement, _Mapping]] = ..., tip: _Optional[_Union[_types_pb2.ResourceNode, _Mapping]] = ...) -> None: ...

class BeginLiquidTransferResponse(_message.Message):
    __slots__ = ("operation", "items", "issues", "revision")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    ISSUES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    operation: _types_pb2.Operation
    items: _containers.RepeatedCompositeFieldContainer[TransferPlan]
    issues: _containers.RepeatedCompositeFieldContainer[_types_pb2.Issue]
    revision: int
    def __init__(self, operation: _Optional[_Union[_types_pb2.Operation, _Mapping]] = ..., items: _Optional[_Iterable[_Union[TransferPlan, _Mapping]]] = ..., issues: _Optional[_Iterable[_Union[_types_pb2.Issue, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class Grip(_message.Message):
    __slots__ = ("distance_from_top", "distance_from_bottom")
    DISTANCE_FROM_TOP_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_FROM_BOTTOM_FIELD_NUMBER: _ClassVar[int]
    distance_from_top: float
    distance_from_bottom: float
    def __init__(self, distance_from_top: _Optional[float] = ..., distance_from_bottom: _Optional[float] = ...) -> None: ...

class BeginResourcePickupRequest(_message.Message):
    __slots__ = ("lock_tokens", "frame", "resource", "carrier", "grip")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    FRAME_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    CARRIER_FIELD_NUMBER: _ClassVar[int]
    GRIP_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    frame: _types_pb2.ResourceRef
    resource: _types_pb2.ResourceRef
    carrier: _types_pb2.ResourceRef
    grip: Grip
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., frame: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., carrier: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., grip: _Optional[_Union[Grip, _Mapping]] = ...) -> None: ...

class BeginResourcePickupResponse(_message.Message):
    __slots__ = ("operation", "resource", "pick_location", "grip_location", "grip_height", "absolute_size", "rotation_in_frame", "issues", "revision")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    PICK_LOCATION_FIELD_NUMBER: _ClassVar[int]
    GRIP_LOCATION_FIELD_NUMBER: _ClassVar[int]
    GRIP_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    ABSOLUTE_SIZE_FIELD_NUMBER: _ClassVar[int]
    ROTATION_IN_FRAME_FIELD_NUMBER: _ClassVar[int]
    ISSUES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    operation: _types_pb2.Operation
    resource: _types_pb2.ResourceNode
    pick_location: _types_pb2.Vec3
    grip_location: _types_pb2.Vec3
    grip_height: float
    absolute_size: _types_pb2.Vec3
    rotation_in_frame: _types_pb2.Rotation
    issues: _containers.RepeatedCompositeFieldContainer[_types_pb2.Issue]
    revision: int
    def __init__(self, operation: _Optional[_Union[_types_pb2.Operation, _Mapping]] = ..., resource: _Optional[_Union[_types_pb2.ResourceNode, _Mapping]] = ..., pick_location: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., grip_location: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., grip_height: _Optional[float] = ..., absolute_size: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., rotation_in_frame: _Optional[_Union[_types_pb2.Rotation, _Mapping]] = ..., issues: _Optional[_Iterable[_Union[_types_pb2.Issue, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class BeginResourcePlaceRequest(_message.Message):
    __slots__ = ("lock_tokens", "frame", "resource", "destination_resource", "destination_location", "rotation_applied_by_move", "grip")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    FRAME_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_RESOURCE_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_LOCATION_FIELD_NUMBER: _ClassVar[int]
    ROTATION_APPLIED_BY_MOVE_FIELD_NUMBER: _ClassVar[int]
    GRIP_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    frame: _types_pb2.ResourceRef
    resource: _types_pb2.ResourceRef
    destination_resource: _types_pb2.ResourceRef
    destination_location: _types_pb2.Vec3
    rotation_applied_by_move: float
    grip: Grip
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., frame: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., destination_resource: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., destination_location: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., rotation_applied_by_move: _Optional[float] = ..., grip: _Optional[_Union[Grip, _Mapping]] = ...) -> None: ...

class BeginResourcePlaceResponse(_message.Message):
    __slots__ = ("operation", "destination", "place_location", "grip_location", "grip_height", "rotation_in_destination", "issues", "revision")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_FIELD_NUMBER: _ClassVar[int]
    PLACE_LOCATION_FIELD_NUMBER: _ClassVar[int]
    GRIP_LOCATION_FIELD_NUMBER: _ClassVar[int]
    GRIP_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    ROTATION_IN_DESTINATION_FIELD_NUMBER: _ClassVar[int]
    ISSUES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    operation: _types_pb2.Operation
    destination: _types_pb2.ResourceNode
    place_location: _types_pb2.Vec3
    grip_location: _types_pb2.Vec3
    grip_height: float
    rotation_in_destination: _types_pb2.Rotation
    issues: _containers.RepeatedCompositeFieldContainer[_types_pb2.Issue]
    revision: int
    def __init__(self, operation: _Optional[_Union[_types_pb2.Operation, _Mapping]] = ..., destination: _Optional[_Union[_types_pb2.ResourceNode, _Mapping]] = ..., place_location: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., grip_location: _Optional[_Union[_types_pb2.Vec3, _Mapping]] = ..., grip_height: _Optional[float] = ..., rotation_in_destination: _Optional[_Union[_types_pb2.Rotation, _Mapping]] = ..., issues: _Optional[_Iterable[_Union[_types_pb2.Issue, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class ItemOutcome(_message.Message):
    __slots__ = ("item", "succeeded", "volume")
    ITEM_FIELD_NUMBER: _ClassVar[int]
    SUCCEEDED_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    item: int
    succeeded: bool
    volume: float
    def __init__(self, item: _Optional[int] = ..., succeeded: _Optional[bool] = ..., volume: _Optional[float] = ...) -> None: ...

class ResolveOperationRequest(_message.Message):
    __slots__ = ("lock_tokens", "operation_id", "outcomes")
    LOCK_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOMES_FIELD_NUMBER: _ClassVar[int]
    lock_tokens: _containers.RepeatedScalarFieldContainer[bytes]
    operation_id: bytes
    outcomes: _containers.RepeatedCompositeFieldContainer[ItemOutcome]
    def __init__(self, lock_tokens: _Optional[_Iterable[bytes]] = ..., operation_id: _Optional[bytes] = ..., outcomes: _Optional[_Iterable[_Union[ItemOutcome, _Mapping]]] = ...) -> None: ...

class ResolveOperationResponse(_message.Message):
    __slots__ = ("operation", "nodes", "revision")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    NODES_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    operation: _types_pb2.Operation
    nodes: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceNode]
    revision: int
    def __init__(self, operation: _Optional[_Union[_types_pb2.Operation, _Mapping]] = ..., nodes: _Optional[_Iterable[_Union[_types_pb2.ResourceNode, _Mapping]]] = ..., revision: _Optional[int] = ...) -> None: ...

class GetOperationRequest(_message.Message):
    __slots__ = ("operation_id",)
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    operation_id: bytes
    def __init__(self, operation_id: _Optional[bytes] = ...) -> None: ...

class ListOperationsRequest(_message.Message):
    __slots__ = ("root", "unresolved_only", "page_size", "page_token")
    ROOT_FIELD_NUMBER: _ClassVar[int]
    UNRESOLVED_ONLY_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    root: _types_pb2.ResourceRef
    unresolved_only: bool
    page_size: int
    page_token: str
    def __init__(self, root: _Optional[_Union[_types_pb2.ResourceRef, _Mapping]] = ..., unresolved_only: _Optional[bool] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class ListOperationsResponse(_message.Message):
    __slots__ = ("operations", "next_page_token")
    OPERATIONS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    operations: _containers.RepeatedCompositeFieldContainer[_types_pb2.Operation]
    next_page_token: str
    def __init__(self, operations: _Optional[_Iterable[_Union[_types_pb2.Operation, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class WatchRequest(_message.Message):
    __slots__ = ("roots", "after_revision")
    ROOTS_FIELD_NUMBER: _ClassVar[int]
    AFTER_REVISION_FIELD_NUMBER: _ClassVar[int]
    roots: _containers.RepeatedCompositeFieldContainer[_types_pb2.ResourceRef]
    after_revision: int
    def __init__(self, roots: _Optional[_Iterable[_Union[_types_pb2.ResourceRef, _Mapping]]] = ..., after_revision: _Optional[int] = ...) -> None: ...

class ResourceEvent(_message.Message):
    __slots__ = ("revision", "at", "created", "deleted", "updated", "operation", "changed")
    REVISION_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    CREATED_FIELD_NUMBER: _ClassVar[int]
    DELETED_FIELD_NUMBER: _ClassVar[int]
    UPDATED_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    CHANGED_FIELD_NUMBER: _ClassVar[int]
    revision: int
    at: _timestamp_pb2.Timestamp
    created: _types_pb2.ResourceNode
    deleted: bytes
    updated: _types_pb2.ResourceNode
    operation: _types_pb2.Operation
    changed: _containers.RepeatedScalarFieldContainer[ChangeAspect]
    def __init__(self, revision: _Optional[int] = ..., at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., created: _Optional[_Union[_types_pb2.ResourceNode, _Mapping]] = ..., deleted: _Optional[bytes] = ..., updated: _Optional[_Union[_types_pb2.ResourceNode, _Mapping]] = ..., operation: _Optional[_Union[_types_pb2.Operation, _Mapping]] = ..., changed: _Optional[_Iterable[_Union[ChangeAspect, str]]] = ...) -> None: ...
