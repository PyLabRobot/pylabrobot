"""DeckService server implementation — wraps a real Deck object."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.well import Well

from . import deck_service_pb2 as pb2
from .deck_service_connect import DeckService, DeckServiceASGIApplication

if TYPE_CHECKING:
    from pylabrobot.resources.deck import Deck
    from connectrpc.request import RequestContext


# ============================================================
# Conversion helpers
# ============================================================

def _tip_to_proto(tip: Tip) -> pb2.TipData:
    """Convert a Tip (or HamiltonTip) to a TipData protobuf message."""
    from pylabrobot.resources.hamilton.tip_creators import HamiltonTip
    td = pb2.TipData(
        type=tip.__class__.__name__,
        name=tip.name or "",
        has_filter=tip.has_filter,
        total_tip_length=tip.total_tip_length,
        maximal_volume=tip.maximal_volume,
        fitting_depth=tip.fitting_depth,
    )
    if isinstance(tip, HamiltonTip):
        td.tip_size = tip.tip_size.name
        td.pickup_method = tip.pickup_method.name
    return td


def _resource_to_data(resource: Resource) -> pb2.ResourceData:
    """Convert a single resource to a ResourceData protobuf message."""
    data = pb2.ResourceData(
        name=resource.name,
        type=resource.__class__.__name__,
        size_x=resource._size_x,
        size_y=resource._size_y,
        size_z=resource._size_z,
        category=resource.category or "",
        model=resource.model or "",
    )

    # Location relative to parent
    if resource.location is not None:
        data.location.CopyFrom(pb2.Coordinate(
            x=resource.location.x, y=resource.location.y, z=resource.location.z))

    # Rotation
    if resource.rotation is not None:
        data.rotation.CopyFrom(pb2.Rotation(
            x=resource.rotation.x, y=resource.rotation.y, z=resource.rotation.z))

    # Parent name
    if resource.parent is not None:
        data.parent_name = resource.parent.name

    # Container fields
    if isinstance(resource, Container):
        if resource._material_z_thickness is not None:
            data.material_z_thickness = resource._material_z_thickness
        if resource.max_volume is not None:
            data.max_volume = resource.max_volume

    # Well fields
    if isinstance(resource, Well):
        data.well_bottom_type = resource.bottom_type.value if hasattr(resource.bottom_type, 'value') else str(resource.bottom_type)
        data.cross_section_type = resource.cross_section_type.value if hasattr(resource.cross_section_type, 'value') else str(resource.cross_section_type)

    # Plate fields
    if isinstance(resource, Plate):
        data.plate_type = resource.plate_type
        data.has_lid = resource.has_lid()
        # Include ordering
        for key, val in resource._ordering.items():
            data.ordering[key] = val

    # TipRack fields
    if isinstance(resource, TipRack):
        for key, val in resource._ordering.items():
            data.ordering[key] = val

    # TipSpot fields
    if isinstance(resource, TipSpot):
        prototype = resource.make_tip()
        data.prototype_tip.CopyFrom(_tip_to_proto(prototype))

    # Lid fields
    if isinstance(resource, Lid):
        data.nesting_z_height = resource.nesting_z_height

    # Trash fields
    if isinstance(resource, Trash):
        if resource._material_z_thickness is not None:
            data.material_z_thickness = resource._material_z_thickness
        if resource.max_volume is not None and resource.max_volume != float("inf"):
            data.max_volume = resource.max_volume

    return data


def _resource_to_tree(resource: Resource) -> pb2.ResourceTree:
    """Recursively convert a resource and its children to a ResourceTree."""
    tree = pb2.ResourceTree(data=_resource_to_data(resource))
    for child in resource.children:
        tree.children.append(_resource_to_tree(child))
    return tree


# ============================================================
# Service implementation
# ============================================================

class DeckServiceImpl(DeckService):
    """ConnectRPC service that wraps a real Deck object."""

    def __init__(self, deck: Deck):
        self._deck = deck

    # --- Tree ---

    async def get_tree(self, request: pb2.GetTreeRequest, ctx: RequestContext) -> pb2.ResourceTree:
        if request.root_name:
            root = self._deck.get_resource(request.root_name)
        else:
            root = self._deck
        return _resource_to_tree(root)

    async def get_resource(self, request: pb2.ResourceByNameRequest, ctx: RequestContext) -> pb2.ResourceData:
        resource = self._deck.get_resource(request.name)
        return _resource_to_data(resource)

    async def has_resource(self, request: pb2.ResourceByNameRequest, ctx: RequestContext) -> pb2.BoolResponse:
        return pb2.BoolResponse(value=self._deck.has_resource(request.name))

    async def get_trash_area(self, request: pb2.Empty, ctx: RequestContext) -> pb2.ResourceData:
        trash = self._deck.get_trash_area()
        return _resource_to_data(trash)

    async def get_trash_area96(self, request: pb2.Empty, ctx: RequestContext) -> pb2.ResourceData:
        trash = self._deck.get_trash_area96()
        return _resource_to_data(trash)

    # --- Spatial ---

    async def get_location_wrt(self, request: pb2.GetLocationWrtRequest, ctx: RequestContext) -> pb2.Coordinate:
        resource = self._deck.get_resource(request.resource_name)
        other = self._deck.get_resource(request.other_name)
        coord = resource.get_location_wrt(
            other, x=request.anchor_x, y=request.anchor_y, z=request.anchor_z)
        return pb2.Coordinate(x=coord.x, y=coord.y, z=coord.z)

    async def get_absolute_location(self, request: pb2.GetAbsoluteLocationRequest, ctx: RequestContext) -> pb2.Coordinate:
        resource = self._deck.get_resource(request.resource_name)
        coord = resource.get_absolute_location(
            x=request.anchor_x, y=request.anchor_y, z=request.anchor_z)
        return pb2.Coordinate(x=coord.x, y=coord.y, z=coord.z)

    async def get_absolute_rotation(self, request: pb2.GetAbsoluteRotationRequest, ctx: RequestContext) -> pb2.Rotation:
        resource = self._deck.get_resource(request.resource_name)
        rot = resource.get_absolute_rotation()
        return pb2.Rotation(x=rot.x, y=rot.y, z=rot.z)

    async def get_absolute_size(self, request: pb2.GetAbsoluteSizeRequest, ctx: RequestContext) -> pb2.Size:
        resource = self._deck.get_resource(request.resource_name)
        return pb2.Size(
            x=resource.get_absolute_size_x(),
            y=resource.get_absolute_size_y(),
            z=resource.get_absolute_size_z(),
        )

    async def get_highest_point(self, request: pb2.GetHighestPointRequest, ctx: RequestContext) -> pb2.FloatResponse:
        resource = self._deck.get_resource(request.resource_name)
        return pb2.FloatResponse(value=resource.get_highest_known_point())

    async def batch_get_location_wrt(self, request: pb2.BatchGetLocationWrtRequest, ctx: RequestContext) -> pb2.BatchCoordinateResponse:
        coords = []
        for item in request.items:
            resource = self._deck.get_resource(item.resource_name)
            other = self._deck.get_resource(item.other_name)
            coord = resource.get_location_wrt(
                other, x=item.anchor_x, y=item.anchor_y, z=item.anchor_z)
            coords.append(pb2.Coordinate(x=coord.x, y=coord.y, z=coord.z))
        return pb2.BatchCoordinateResponse(coordinates=coords)

    # --- Computed methods ---

    async def compute_volume_from_height(self, request: pb2.ComputeVolumeHeightRequest, ctx: RequestContext) -> pb2.FloatResponse:
        resource = self._deck.get_resource(request.resource_name)
        return pb2.FloatResponse(value=resource.compute_volume_from_height(request.value))

    async def compute_height_from_volume(self, request: pb2.ComputeVolumeHeightRequest, ctx: RequestContext) -> pb2.FloatResponse:
        resource = self._deck.get_resource(request.resource_name)
        return pb2.FloatResponse(value=resource.compute_height_from_volume(request.value))

    async def supports_compute_height_volume(self, request: pb2.ResourceByNameRequest, ctx: RequestContext) -> pb2.BoolResponse:
        resource = self._deck.get_resource(request.name)
        return pb2.BoolResponse(value=resource.supports_compute_height_volume_functions())

    async def has_lid(self, request: pb2.HasLidRequest, ctx: RequestContext) -> pb2.BoolResponse:
        plate = self._deck.get_resource(request.plate_name)
        return pb2.BoolResponse(value=plate.has_lid())

    # --- Tip access ---

    async def get_tip(self, request: pb2.GetTipRequest, ctx: RequestContext) -> pb2.TipData:
        tip_spot = self._deck.get_resource(request.tip_spot_name)
        tip = tip_spot.get_tip()
        return _tip_to_proto(tip)

    # --- Volume tracker ---

    async def get_volume_tracker_state(self, request: pb2.ResourceByNameRequest, ctx: RequestContext) -> pb2.VolumeTrackerState:
        resource = self._deck.get_resource(request.name)
        tracker = resource.tracker
        return pb2.VolumeTrackerState(
            volume=tracker.volume,
            pending_volume=tracker.pending_volume,
            max_volume=tracker.max_volume,
            is_disabled=tracker.is_disabled,
        )

    async def remove_liquid(self, request: pb2.TrackerOpRequest, ctx: RequestContext) -> pb2.Empty:
        resource = self._deck.get_resource(request.resource_name)
        resource.tracker.remove_liquid(request.volume)
        return pb2.Empty()

    async def add_liquid(self, request: pb2.TrackerOpRequest, ctx: RequestContext) -> pb2.Empty:
        resource = self._deck.get_resource(request.resource_name)
        resource.tracker.add_liquid(request.volume)
        return pb2.Empty()

    async def batch_remove_liquid(self, request: pb2.BatchTrackerOpRequest, ctx: RequestContext) -> pb2.Empty:
        for op in request.ops:
            resource = self._deck.get_resource(op.resource_name)
            resource.tracker.remove_liquid(op.volume)
        return pb2.Empty()

    async def batch_add_liquid(self, request: pb2.BatchTrackerOpRequest, ctx: RequestContext) -> pb2.Empty:
        for op in request.ops:
            resource = self._deck.get_resource(op.resource_name)
            resource.tracker.add_liquid(op.volume)
        return pb2.Empty()

    # --- Tip tracker ---

    async def get_tip_tracker_state(self, request: pb2.ResourceByNameRequest, ctx: RequestContext) -> pb2.TipTrackerState:
        resource = self._deck.get_resource(request.name)
        tracker = resource.tracker
        state = pb2.TipTrackerState(
            has_tip=tracker.has_tip,
            is_disabled=tracker.is_disabled,
        )
        if tracker.has_tip:
            state.tip.CopyFrom(_tip_to_proto(tracker.get_tip()))
        return state

    async def remove_tip(self, request: pb2.TipTrackerOpRequest, ctx: RequestContext) -> pb2.Empty:
        resource = self._deck.get_resource(request.tip_spot_name)
        resource.tracker.remove_tip()
        return pb2.Empty()

    async def add_tip(self, request: pb2.TipTrackerOpRequest, ctx: RequestContext) -> pb2.Empty:
        resource = self._deck.get_resource(request.tip_spot_name)
        tip_spot = resource
        tip_spot.tracker.add_tip(tip_spot.make_tip(), origin=tip_spot)
        return pb2.Empty()

    # --- Commit / Rollback ---

    async def commit_volume_trackers(self, request: pb2.CommitRollbackRequest, ctx: RequestContext) -> pb2.Empty:
        for name in request.resource_names:
            resource = self._deck.get_resource(name)
            resource.tracker.commit()
        return pb2.Empty()

    async def rollback_volume_trackers(self, request: pb2.CommitRollbackRequest, ctx: RequestContext) -> pb2.Empty:
        for name in request.resource_names:
            resource = self._deck.get_resource(name)
            resource.tracker.rollback()
        return pb2.Empty()

    async def commit_tip_trackers(self, request: pb2.CommitRollbackRequest, ctx: RequestContext) -> pb2.Empty:
        for name in request.resource_names:
            resource = self._deck.get_resource(name)
            resource.tracker.commit()
        return pb2.Empty()

    async def rollback_tip_trackers(self, request: pb2.CommitRollbackRequest, ctx: RequestContext) -> pb2.Empty:
        for name in request.resource_names:
            resource = self._deck.get_resource(name)
            resource.tracker.rollback()
        return pb2.Empty()

    # --- Structure mutation ---

    async def assign_child(self, request: pb2.AssignChildRequest, ctx: RequestContext) -> pb2.Empty:
        child = self._deck.get_resource(request.child_name)
        parent = self._deck.get_resource(request.parent_name)
        loc = Coordinate(request.location.x, request.location.y, request.location.z)
        parent.assign_child_resource(child, location=loc)
        return pb2.Empty()

    async def unassign_child(self, request: pb2.UnassignChildRequest, ctx: RequestContext) -> pb2.Empty:
        resource = self._deck.get_resource(request.resource_name)
        if resource.parent is not None:
            resource.parent.unassign_child_resource(resource)
        return pb2.Empty()


# ============================================================
# ASGI app factory
# ============================================================

def create_app(deck: Deck) -> DeckServiceASGIApplication:
    """Create an ASGI application for the given deck.

    Usage::

        import uvicorn
        from pylabrobot.resources import Deck
        from pylabrobot.resources.remote.server import create_app

        deck = Deck.load_from_json_file("hamilton-layout.json")
        app = create_app(deck)
        uvicorn.run(app, host="0.0.0.0", port=8080)
    """
    return DeckServiceASGIApplication(DeckServiceImpl(deck))
