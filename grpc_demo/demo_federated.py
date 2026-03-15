#!/usr/bin/env python
"""
Federated demo: two worker machines, one master that mounts them.

This runs everything in one process for demonstration, but each worker
could be on a separate machine — the master only needs the address.

    python -m grpc_demo.demo_federated
"""

from __future__ import annotations

import time
from concurrent import futures

import grpc

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from grpc_demo import resource_tree_pb2_grpc as pb_grpc
from grpc_demo.remote_resource import RemoteResource
from grpc_demo.server import ResourceTreeServicer


def print_tree(resource, indent=0):
    prefix = "  " * indent
    loc = resource.location
    loc_str = f" @ ({loc.x}, {loc.y}, {loc.z})" if loc else ""
    tag = " [REMOTE]" if isinstance(resource, RemoteResource) else ""
    print(f"{prefix}{resource.name}  "
          f"[{resource.get_size_x()}x{resource.get_size_y()}x{resource.get_size_z()}]"
          f"{loc_str}{tag}")
    for child in resource.children:
        print_tree(child, indent + 1)


def start_worker(root: Resource, port: int) -> grpc.Server:
    """Start a worker gRPC server in a background thread."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb_grpc.add_ResourceTreeServiceServicer_to_server(
        ResourceTreeServicer(root), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"Worker '{root.name}' started on port {port}")
    return server


def main():
    # ------------------------------------------------------------------
    # 1. Start two workers (in a real setup these are separate machines)
    # ------------------------------------------------------------------
    local_hamilton = Resource("hamilton_deck", size_x=100, size_y=100, size_z=10)
    local_odtc = Resource("odtc", size_x=40, size_y=40, size_z=60)

    hamilton_server = start_worker(local_hamilton, port=50061)
    odtc_server = start_worker(local_odtc, port=50062)

    time.sleep(0.5)  # let servers bind

    # ------------------------------------------------------------------
    # 2. Master builds its tree, mounting remote workers
    # ------------------------------------------------------------------
    root = Resource("lab", size_x=2000, size_y=1000, size_z=200)

    trash = Resource("trash", size_x=50, size_y=50, size_z=80)
    root.assign_child_resource(trash, location=Coordinate(900, 0, 0))

    remote_hamilton = RemoteResource.from_target("localhost:50061")
    root.assign_child_resource(remote_hamilton, location=Coordinate(0, 0, 0))

    remote_odtc = RemoteResource.from_target("localhost:50062")
    root.assign_child_resource(remote_odtc, location=Coordinate(200, 0, 0))

    print("\n=== Initial tree (workers are empty) ===")
    print_tree(root)

    # ------------------------------------------------------------------
    # 3. Worker mutates its own tree locally (simulates separate machine)
    # ------------------------------------------------------------------
    print("\n=== Worker assigns resources locally ===")
    local_hamilton.assign_child_resource(
        Resource("plate_carrier", size_x=80, size_y=80, size_z=20, category="plate_carrier"),
        location=Coordinate(10, 10, 0),
    )
    local_hamilton.assign_child_resource(
        Resource("tip_rack_1", size_x=40, size_y=40, size_z=15, category="tips"),
        location=Coordinate(0, 60, 0),
    )

    # Master sees it immediately — no sync needed
    print("\n=== Master's view (after worker's local changes) ===")
    print_tree(root)

    # ------------------------------------------------------------------
    # 4. Master can also mutate remote subtrees
    # ------------------------------------------------------------------
    print("\n=== Master assigns plate_1 into plate_carrier ===")
    carrier = remote_hamilton.get_resource("plate_carrier")
    carrier.assign_child_resource(
        Resource("plate_1", size_x=60, size_y=60, size_z=5, category="plate"),
        location=Coordinate(10, 10, 0),
    )

    remote_odtc.assign_child_resource(
        Resource("plate_slot", size_x=30, size_y=30, size_z=5),
        location=Coordinate(5, 5, 0),
    )

    print("\n=== Full tree ===")
    print_tree(root)

    # ------------------------------------------------------------------
    # 5. Query through the master
    # ------------------------------------------------------------------
    print("\n=== Master queries ===")

    plate_carrier = remote_hamilton.get_resource("plate_carrier")
    print(f"Found plate_carrier: {plate_carrier.name}, "
          f"{len(plate_carrier.children)} children")

    all_hamilton_children = remote_hamilton.get_all_children()
    print(f"All hamilton descendants: {[c.name for c in all_hamilton_children]}")

    # ------------------------------------------------------------------
    # 6. Rotate the remote deck
    # ------------------------------------------------------------------
    print("\n=== Rotating hamilton deck 90° ===")
    remote_hamilton.rotate(z=90)
    rot = remote_hamilton.rotation
    print(f"Hamilton rotation: ({rot.x}, {rot.y}, {rot.z})")

    # ------------------------------------------------------------------
    # 7. Unassign from remote subtree
    # ------------------------------------------------------------------
    print("\n=== Removing tip_rack_1 from hamilton ===")
    tip_rack = remote_hamilton.get_resource("tip_rack_1")
    remote_hamilton.unassign_child_resource(tip_rack)

    print("\n=== Final tree ===")
    print_tree(root)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    remote_hamilton.close()
    remote_odtc.close()
    hamilton_server.stop(grace=0)
    odtc_server.stop(grace=0)
    print("\nDone.")


if __name__ == "__main__":
    main()
