"""gRPC server that hosts a Resource tree and exposes it over the network.

Usage:
    python -m grpc_demo.server            # default root: 100x100x10 "deck"
    python -m grpc_demo.server --port 50052
"""

from __future__ import annotations

import argparse
import logging
import threading
from concurrent import futures

import grpc

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation

from grpc_demo import resource_tree_pb2 as pb
from grpc_demo import resource_tree_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conversion helpers: Resource <-> protobuf
# ---------------------------------------------------------------------------

def _coord_to_pb(c: Coordinate | None) -> pb.CoordinateMsg | None:
    if c is None:
        return None
    return pb.CoordinateMsg(x=c.x, y=c.y, z=c.z)


def _pb_to_coord(msg: pb.CoordinateMsg) -> Coordinate:
    return Coordinate(x=msg.x, y=msg.y, z=msg.z)


def _rotation_to_pb(r: Rotation) -> pb.RotationMsg:
    return pb.RotationMsg(x=r.x, y=r.y, z=r.z)


def _resource_to_pb(r: Resource) -> pb.ResourceMsg:
    msg = pb.ResourceMsg(
        name=r.name,
        size_x=r.get_size_x(),
        size_y=r.get_size_y(),
        size_z=r.get_size_z(),
        rotation=_rotation_to_pb(r.rotation),
        parent_name=r.parent.name if r.parent else "",
    )
    if r.location is not None:
        msg.location.CopyFrom(_coord_to_pb(r.location))
    if r.category is not None:
        msg.category = r.category
    if r.model is not None:
        msg.model = r.model
    for child in r.children:
        msg.children.append(_resource_to_pb(child))
    return msg


def _pb_to_resource(msg: pb.ResourceMsg) -> Resource:
    """Create a stand-alone Resource from a protobuf message (no parent wiring)."""
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
# Service implementation
# ---------------------------------------------------------------------------

class ResourceTreeServicer(pb_grpc.ResourceTreeServiceServicer):
    def __init__(self, root: Resource):
        self._root = root
        self._lock = threading.Lock()
        # Subscribers receive TreeEvent messages
        self._subscribers: list[list[pb.TreeEvent]] = []
        self._sub_lock = threading.Lock()

    def _broadcast(self, event: pb.TreeEvent):
        with self._sub_lock:
            for q in self._subscribers:
                q.append(event)

    # -- Queries --

    def GetTree(self, request, context):
        with self._lock:
            return pb.TreeResponse(root=_resource_to_pb(self._root))

    def GetResource(self, request, context):
        with self._lock:
            try:
                r = self._root.get_resource(request.name)
            except Exception as e:
                context.abort(grpc.StatusCode.NOT_FOUND, str(e))
            return pb.ResourceResponse(resource=_resource_to_pb(r))

    def GetChildren(self, request, context):
        with self._lock:
            try:
                r = self._root.get_resource(request.name)
            except Exception as e:
                context.abort(grpc.StatusCode.NOT_FOUND, str(e))
            return pb.ChildrenResponse(
                children=[_resource_to_pb(c) for c in r.children]
            )

    # -- Mutations --

    def AssignChild(self, request, context):
        with self._lock:
            try:
                parent = self._root.get_resource(request.parent_name)
            except Exception as e:
                context.abort(grpc.StatusCode.NOT_FOUND, str(e))

            child = _pb_to_resource(request.child)
            loc = _pb_to_coord(request.location) if request.HasField("location") else None
            try:
                parent.assign_child_resource(child, location=loc, reassign=request.reassign)
            except ValueError as e:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

            logger.info("Assigned '%s' to '%s' at %s", child.name, parent.name, loc)

            self._broadcast(pb.TreeEvent(
                type=pb.TreeEvent.CHILD_ASSIGNED,
                parent_name=parent.name,
                resource_name=child.name,
                snapshot=_resource_to_pb(child),
            ))
            return pb.ResourceResponse(resource=_resource_to_pb(child))

    def UnassignChild(self, request, context):
        with self._lock:
            try:
                parent = self._root.get_resource(request.parent_name)
                child = self._root.get_resource(request.child_name)
            except Exception as e:
                context.abort(grpc.StatusCode.NOT_FOUND, str(e))

            try:
                parent.unassign_child_resource(child)
            except ValueError as e:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

            logger.info("Unassigned '%s' from '%s'", child.name, parent.name)

            self._broadcast(pb.TreeEvent(
                type=pb.TreeEvent.CHILD_UNASSIGNED,
                parent_name=request.parent_name,
                resource_name=request.child_name,
            ))
            return pb.Empty()

    def Rotate(self, request, context):
        with self._lock:
            try:
                r = self._root.get_resource(request.name)
            except Exception as e:
                context.abort(grpc.StatusCode.NOT_FOUND, str(e))

            r.rotate(x=request.x, y=request.y, z=request.z)
            logger.info("Rotated '%s' by (%s, %s, %s)", r.name, request.x, request.y, request.z)

            self._broadcast(pb.TreeEvent(
                type=pb.TreeEvent.RESOURCE_ROTATED,
                parent_name=r.parent.name if r.parent else "",
                resource_name=r.name,
                snapshot=_resource_to_pb(r),
            ))
            return pb.ResourceResponse(resource=_resource_to_pb(r))

    def Move(self, request, context):
        with self._lock:
            try:
                old_parent = self._root.get_resource(request.parent_name)
                child = self._root.get_resource(request.child_name)
                new_parent = self._root.get_resource(request.new_parent_name)
            except Exception as e:
                context.abort(grpc.StatusCode.NOT_FOUND, str(e))

            loc = _pb_to_coord(request.new_location) if request.HasField("new_location") else None
            try:
                old_parent.unassign_child_resource(child)
                new_parent.assign_child_resource(child, location=loc)
            except ValueError as e:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

            logger.info("Moved '%s' from '%s' to '%s'", child.name, old_parent.name, new_parent.name)

            self._broadcast(pb.TreeEvent(
                type=pb.TreeEvent.RESOURCE_MOVED,
                parent_name=new_parent.name,
                resource_name=child.name,
                snapshot=_resource_to_pb(child),
            ))
            return pb.ResourceResponse(resource=_resource_to_pb(child))

    # -- Streaming --

    def Subscribe(self, request, context):
        q: list[pb.TreeEvent] = []
        with self._sub_lock:
            self._subscribers.append(q)
        logger.info("New subscriber connected")
        try:
            while context.is_active():
                while q:
                    yield q.pop(0)
                # Avoid busy-spin
                context.is_active()  # ~cheap check
                import time
                time.sleep(0.05)
        finally:
            with self._sub_lock:
                self._subscribers.remove(q)
            logger.info("Subscriber disconnected")


# ---------------------------------------------------------------------------
# Server entry-point
# ---------------------------------------------------------------------------

def serve(port: int = 50051, root: Resource | None = None):
    if root is None:
        root = Resource("deck", size_x=100, size_y=100, size_z=10)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_ResourceTreeServiceServicer_to_server(
        ResourceTreeServicer(root), server
    )
    addr = f"[::]:{port}"
    server.add_insecure_port(addr)
    server.start()
    logger.info("ResourceTree gRPC server listening on %s", addr)
    print(f"Server started on port {port} with root resource '{root.name}'")
    server.wait_for_termination()


def main():
    parser = argparse.ArgumentParser(description="Resource-tree gRPC server")
    parser.add_argument("--port", type=int, default=50051)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    serve(port=args.port)


if __name__ == "__main__":
    main()
