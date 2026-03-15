"""gRPC client that provides a Resource-like API for a remote resource tree.

Usage:
    from grpc_demo.client import RemoteResourceTree

    tree = RemoteResourceTree("localhost:50051")

    # mirrors the local Resource API
    tree.assign_child_resource("deck", name="plate", size_x=80, size_y=80, size_z=5,
                                location=(10, 10, 0))
    tree.assign_child_resource("plate", name="well_A1", size_x=5, size_y=5, size_z=10,
                                location=(0, 0, 0))
    tree.unassign_child_resource("deck", "plate")
    print(tree.get_tree())
"""

from __future__ import annotations

import threading
from typing import Callable

import grpc

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation

from grpc_demo import resource_tree_pb2 as pb
from grpc_demo import resource_tree_pb2_grpc as pb_grpc


# ---------------------------------------------------------------------------
# protobuf -> local helpers  (mirrors server.py converters)
# ---------------------------------------------------------------------------

def _pb_to_coord(msg: pb.CoordinateMsg) -> Coordinate:
    return Coordinate(x=msg.x, y=msg.y, z=msg.z)


def _pb_to_resource(msg: pb.ResourceMsg) -> Resource:
    """Reconstruct a local Resource tree from a protobuf message."""
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


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class RemoteResourceTree:
    """Thin client that mirrors the Resource tree API over gRPC.

    Every mutation round-trips to the server; the server is the single source
    of truth.  Queries like ``get_tree`` / ``get_resource`` fetch a snapshot.
    """

    def __init__(self, target: str = "localhost:50051"):
        self._channel = grpc.insecure_channel(target)
        self._stub = pb_grpc.ResourceTreeServiceStub(self._channel)

    def close(self):
        self._channel.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- Queries (return local Resource snapshots) --

    def get_tree(self) -> Resource:
        """Fetch the full tree and return a local Resource copy."""
        resp = self._stub.GetTree(pb.GetTreeRequest())
        return _pb_to_resource(resp.root)

    def get_resource(self, name: str) -> Resource:
        """Fetch a single resource by name."""
        resp = self._stub.GetResource(pb.GetResourceRequest(name=name))
        return _pb_to_resource(resp.resource)

    def get_children(self, name: str) -> list[Resource]:
        """Fetch direct children of a named resource."""
        resp = self._stub.GetChildren(pb.GetChildrenRequest(name=name))
        return [_pb_to_resource(c) for c in resp.children]

    # -- Mutations --

    def assign_child_resource(
        self,
        parent_name: str,
        *,
        name: str,
        size_x: float,
        size_y: float,
        size_z: float,
        location: tuple[float, float, float] | Coordinate | None = None,
        rotation: tuple[float, float, float] | Rotation | None = None,
        category: str | None = None,
        model: str | None = None,
        reassign: bool = True,
    ) -> Resource:
        """Create a new resource and assign it as a child of *parent_name*.

        This is the network equivalent of::

            parent.assign_child_resource(
                Resource(name, size_x, size_y, size_z, ...),
                location=Coordinate(x, y, z),
            )

        Returns a local snapshot of the newly assigned child.
        """
        child_msg = pb.ResourceMsg(
            name=name,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
        )
        if rotation is not None:
            if isinstance(rotation, tuple):
                rotation = Rotation(x=rotation[0], y=rotation[1], z=rotation[2])
            child_msg.rotation.CopyFrom(pb.RotationMsg(x=rotation.x, y=rotation.y, z=rotation.z))
        if category is not None:
            child_msg.category = category
        if model is not None:
            child_msg.model = model

        req = pb.AssignChildRequest(
            parent_name=parent_name,
            child=child_msg,
            reassign=reassign,
        )
        if location is not None:
            if isinstance(location, tuple):
                location = Coordinate(x=location[0], y=location[1], z=location[2])
            req.location.CopyFrom(pb.CoordinateMsg(x=location.x, y=location.y, z=location.z))

        resp = self._stub.AssignChild(req)
        return _pb_to_resource(resp.resource)

    def assign_child(
        self,
        parent_name: str,
        resource: Resource,
        location: Coordinate | None = None,
        reassign: bool = True,
    ) -> Resource:
        """Assign an existing local Resource object as child of *parent_name*.

        Serializes the full subtree rooted at *resource* to the server.
        """
        child_msg = self._resource_to_pb(resource)
        req = pb.AssignChildRequest(
            parent_name=parent_name,
            child=child_msg,
            reassign=reassign,
        )
        if location is not None:
            req.location.CopyFrom(pb.CoordinateMsg(x=location.x, y=location.y, z=location.z))

        resp = self._stub.AssignChild(req)
        return _pb_to_resource(resp.resource)

    def unassign_child_resource(self, parent_name: str, child_name: str) -> None:
        """Unassign a child from its parent (equivalent to parent.unassign_child_resource(child))."""
        self._stub.UnassignChild(pb.UnassignChildRequest(
            parent_name=parent_name,
            child_name=child_name,
        ))

    def rotate(self, name: str, x: float = 0, y: float = 0, z: float = 0) -> Resource:
        """Rotate a resource (equivalent to resource.rotate(x, y, z))."""
        resp = self._stub.Rotate(pb.RotateRequest(name=name, x=x, y=y, z=z))
        return _pb_to_resource(resp.resource)

    def move(
        self,
        child_name: str,
        *,
        from_parent: str,
        to_parent: str,
        location: tuple[float, float, float] | Coordinate | None = None,
    ) -> Resource:
        """Move a resource from one parent to another."""
        req = pb.MoveRequest(
            parent_name=from_parent,
            child_name=child_name,
            new_parent_name=to_parent,
        )
        if location is not None:
            if isinstance(location, tuple):
                location = Coordinate(x=location[0], y=location[1], z=location[2])
            req.new_location.CopyFrom(pb.CoordinateMsg(x=location.x, y=location.y, z=location.z))
        resp = self._stub.Move(req)
        return _pb_to_resource(resp.resource)

    # -- Subscriptions --

    def subscribe(self, callback: Callable[[pb.TreeEvent], None]) -> threading.Thread:
        """Subscribe to tree events in a background thread.

        *callback* is called with each ``TreeEvent`` as it arrives.
        Returns the background thread (daemon) so you can join() it if needed.
        """
        def _listen():
            for event in self._stub.Subscribe(pb.SubscribeRequest()):
                callback(event)

        t = threading.Thread(target=_listen, daemon=True)
        t.start()
        return t

    # -- internal --

    @staticmethod
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
            msg.children.append(RemoteResourceTree._resource_to_pb(child))
        return msg
