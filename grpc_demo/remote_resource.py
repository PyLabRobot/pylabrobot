"""A Resource subclass that proxies all operations to a remote gRPC worker.

Usage on the master:

    root = Resource("lab", size_x=1000, size_y=1000, size_z=100)
    hamilton = RemoteResource.from_target("192.168.1.10:50051")
    root.assign_child_resource(hamilton, location=Coordinate(0, 0, 0))

    # Now hamilton.children, hamilton.get_size_x(), etc. are all live RPCs.
    #
    # get_resource returns live proxies too:
    #   carrier = hamilton.get_resource("plate_carrier")
    #   carrier.assign_child_resource(plate, location=...)   # → RPC
"""

from __future__ import annotations

from typing import List, Optional

import grpc

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation

from grpc_demo import resource_tree_pb2 as pb
from grpc_demo import resource_tree_pb2_grpc as pb_grpc


def _pb_to_coord(msg: pb.CoordinateMsg) -> Coordinate:
    return Coordinate(x=msg.x, y=msg.y, z=msg.z)


def _pb_to_resource(msg: pb.ResourceMsg) -> Resource:
    r = Resource(
        name=msg.name,
        size_x=msg.size_x,
        size_y=msg.size_y,
        size_z=msg.size_z,
        rotation=Rotation(x=msg.rotation.x, y=msg.rotation.y, z=msg.rotation.z),
        category=msg.category if msg.category else None,
        model=msg.model if msg.model else None,
    )
    for child_msg in msg.children:
        child = _pb_to_resource(child_msg)
        loc = _pb_to_coord(child_msg.location) if child_msg.HasField("location") else None
        r.assign_child_resource(child, location=loc)
    return r


def _resource_to_pb(r: Resource) -> pb.ResourceMsg:
    msg = pb.ResourceMsg(
        name=r.name,
        size_x=r.get_size_x(),
        size_y=r.get_size_y(),
        size_z=r.get_size_z(),
        rotation=pb.RotationMsg(x=r.rotation.x, y=r.rotation.y, z=r.rotation.z),
        parent_name=r.parent.name if r.parent else "",
    )
    if r.location is not None:
        msg.location.CopyFrom(pb.CoordinateMsg(x=r.location.x, y=r.location.y, z=r.location.z))
    if r.category is not None:
        msg.category = r.category
    if r.model is not None:
        msg.model = r.model
    for child in r.children:
        msg.children.append(_resource_to_pb(child))
    return msg


def _init_from_msg(msg: pb.ResourceMsg) -> dict:
    """Extract Resource.__init__ kwargs from a protobuf message."""
    return dict(
        name=msg.name,
        size_x=msg.size_x,
        size_y=msg.size_y,
        size_z=msg.size_z,
        rotation=Rotation(x=msg.rotation.x, y=msg.rotation.y, z=msg.rotation.z),
        category=msg.category if msg.category else None,
        model=msg.model if msg.model else None,
    )


class RemoteResource(Resource):
    """A Resource whose state lives on a remote gRPC worker.

    Create with factory methods:
        RemoteResource.from_target("localhost:50051")
        RemoteResource.from_target("localhost:50051", name="my_hamilton")
    """

    def __init__(self, stub, remote_name: str, is_root: bool = False,
                 channel=None, target: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._stub = stub
        self._remote_name = remote_name
        self._is_root = is_root
        self._channel = channel
        self._target = target
        self._snapshot_children: List[RemoteResource] = []

    @classmethod
    def from_target(cls, target: str, name: str | None = None) -> RemoteResource:
        """Mount a remote worker's root resource.

        Args:
            target: gRPC address, e.g. "localhost:50051".
            name: Override the resource name. If None, uses the worker's root name.
        """
        channel = grpc.insecure_channel(target)
        stub = pb_grpc.ResourceTreeServiceStub(channel)

        resp = stub.GetTree(pb.GetTreeRequest())
        root_msg = resp.root

        kwargs = _init_from_msg(root_msg)
        if name is not None:
            kwargs["name"] = name

        return cls(
            stub=stub,
            remote_name=root_msg.name,
            is_root=True,
            channel=channel,
            target=target,
            **kwargs,
        )

    @classmethod
    def _from_msg(cls, stub, msg: pb.ResourceMsg) -> RemoteResource:
        """Create a sub-resource proxy from a protobuf message (internal)."""
        r = cls(stub=stub, remote_name=msg.name, **_init_from_msg(msg))
        for child_msg in msg.children:
            child = cls._from_msg(stub, child_msg)
            child.location = _pb_to_coord(child_msg.location) if child_msg.HasField("location") else None
            child.parent = r
            r._snapshot_children.append(child)
        return r

    def close(self):
        if self._channel is not None:
            self._channel.close()

    # -- Fetching remote root (only for root mounts) --

    def _get_remote_root(self) -> pb.ResourceMsg:
        resp = self._stub.GetTree(pb.GetTreeRequest())
        return resp.root

    # -- Size --

    def get_size_x(self) -> float:
        if self._is_root:
            return self._get_remote_root().size_x
        return self._size_x

    def get_size_y(self) -> float:
        if self._is_root:
            return self._get_remote_root().size_y
        return self._size_y

    def get_size_z(self) -> float:
        if self._is_root:
            return self._get_remote_root().size_z
        return self._local_size_z

    # -- Rotation --

    @property
    def rotation(self) -> Rotation:
        if self._is_root:
            msg = self._get_remote_root().rotation
            return Rotation(x=msg.x, y=msg.y, z=msg.z)
        return super().rotation

    @rotation.setter
    def rotation(self, value: Rotation):
        pass

    # -- Children --

    @property  # type: ignore[override]
    def children(self) -> List[Resource]:
        if self._is_root:
            root_msg = self._get_remote_root()
            result = []
            for child_msg in root_msg.children:
                child = RemoteResource._from_msg(self._stub, child_msg)
                child.location = _pb_to_coord(child_msg.location) if child_msg.HasField("location") else None
                child.parent = self
                result.append(child)
            return result
        return list(self._snapshot_children)

    @children.setter
    def children(self, value):
        pass

    # -- Queries --

    def get_resource(self, name: str) -> Resource:
        if self.name == name:
            return self
        try:
            resp = self._stub.GetResource(pb.GetResourceRequest(name=name))
            return RemoteResource._from_msg(self._stub, resp.resource)
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                from pylabrobot.resources.errors import ResourceNotFoundError
                raise ResourceNotFoundError(f"Resource with name '{name}' does not exist.")
            raise

    def get_all_children(self) -> List[Resource]:
        root_msg = self._get_remote_root()
        root_local = _pb_to_resource(root_msg)
        return root_local.get_all_children()

    # -- Mutations --

    def assign_child_resource(
        self,
        resource: Resource,
        location: Optional[Coordinate] = None,
        reassign: bool = True,
    ):
        child_msg = _resource_to_pb(resource)
        req = pb.AssignChildRequest(
            parent_name=self._remote_name,
            child=child_msg,
            reassign=reassign,
        )
        if location is not None:
            req.location.CopyFrom(pb.CoordinateMsg(x=location.x, y=location.y, z=location.z))
        self._stub.AssignChild(req)

    def unassign_child_resource(self, resource: Resource):
        self._stub.UnassignChild(pb.UnassignChildRequest(
            parent_name=self._remote_name,
            child_name=resource.name,
        ))

    def rotate(self, x: float = 0, y: float = 0, z: float = 0):
        self._stub.Rotate(pb.RotateRequest(name=self._remote_name, x=x, y=y, z=z))

    # -- Serialize --

    def serialize(self) -> dict:
        root_msg = self._get_remote_root()
        local_copy = _pb_to_resource(root_msg)
        data = local_copy.serialize()
        data["name"] = self.name
        if self._target is not None:
            data["remote_target"] = self._target
        return data

    def __repr__(self):
        if self._target:
            return f"RemoteResource({self.name!r}, target={self._target!r})"
        return f"RemoteResource({self.name!r})"
