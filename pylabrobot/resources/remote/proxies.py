"""Proxy classes that pass isinstance checks and delegate spatial/state ops to the server."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Dict, Optional

from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.well import Well

from . import deck_service_pb2 as pb2
from .remote_trackers import RemoteTipTracker, RemoteVolumeTracker, _tip_from_proto

if TYPE_CHECKING:
    from .deck_service_connect import DeckServiceClientSync


# ============================================================
# Spatial method mixin — avoids repeating the same overrides
# ============================================================

class _SpatialMixin:
    """Mixin that overrides spatial methods to delegate to the remote server.

    Requires ``self._client`` and ``self.name`` to be set.
    """

    _client: DeckServiceClientSync
    name: str

    def get_location_wrt(self, other, x="l", y="f", z="b"):
        resp = self._client.get_location_wrt(pb2.GetLocationWrtRequest(
            resource_name=self.name, other_name=other.name,
            anchor_x=str(x), anchor_y=str(y), anchor_z=str(z),
        ))
        return Coordinate(resp.x, resp.y, resp.z)

    def get_absolute_location(self, x="l", y="f", z="b"):
        resp = self._client.get_absolute_location(pb2.GetAbsoluteLocationRequest(
            resource_name=self.name, anchor_x=str(x), anchor_y=str(y), anchor_z=str(z),
        ))
        return Coordinate(resp.x, resp.y, resp.z)

    def get_absolute_rotation(self):
        resp = self._client.get_absolute_rotation(
            pb2.GetAbsoluteRotationRequest(resource_name=self.name))
        return Rotation(resp.x, resp.y, resp.z)

    def get_absolute_size_x(self):
        resp = self._client.get_absolute_size(
            pb2.GetAbsoluteSizeRequest(resource_name=self.name))
        return resp.x

    def get_absolute_size_y(self):
        resp = self._client.get_absolute_size(
            pb2.GetAbsoluteSizeRequest(resource_name=self.name))
        return resp.y

    def get_absolute_size_z(self):
        resp = self._client.get_absolute_size(
            pb2.GetAbsoluteSizeRequest(resource_name=self.name))
        return resp.z

    def get_highest_known_point(self):
        resp = self._client.get_highest_point(
            pb2.GetHighestPointRequest(resource_name=self.name))
        return resp.value


# ============================================================
# Proxy classes
# ============================================================

class ResourceProxy(_SpatialMixin, Resource):
    """Base proxy. Holds immutable data locally, delegates spatial + state to server."""

    def __init__(self, client: DeckServiceClientSync, data: pb2.ResourceData):
        Resource.__init__(
            self,
            name=data.name,
            size_x=data.size_x,
            size_y=data.size_y,
            size_z=data.size_z,
            category=data.category or None,
            model=data.model or None,
        )
        self._client = client


class ContainerProxy(_SpatialMixin, Container):
    """Proxy for Container — adds remote volume tracker."""

    def __init__(self, client: DeckServiceClientSync, data: pb2.ResourceData):
        Container.__init__(
            self,
            name=data.name,
            size_x=data.size_x,
            size_y=data.size_y,
            size_z=data.size_z,
            material_z_thickness=data.material_z_thickness if data.HasField("material_z_thickness") else None,
            max_volume=data.max_volume if data.HasField("max_volume") else None,
            category=data.category or None,
            model=data.model or None,
        )
        self._client = client
        self.tracker = RemoteVolumeTracker(client, self.name)

    def compute_volume_from_height(self, height: float) -> float:
        resp = self._client.compute_volume_from_height(
            pb2.ComputeVolumeHeightRequest(resource_name=self.name, value=height))
        return resp.value

    def compute_height_from_volume(self, volume: float) -> float:
        resp = self._client.compute_height_from_volume(
            pb2.ComputeVolumeHeightRequest(resource_name=self.name, value=volume))
        return resp.value

    def supports_compute_height_volume_functions(self) -> bool:
        resp = self._client.supports_compute_height_volume(
            pb2.ResourceByNameRequest(name=self.name))
        return resp.value


class WellProxy(_SpatialMixin, Well):
    """Proxy for Well — passes isinstance(x, Well) and isinstance(x, Container)."""

    def __init__(self, client: DeckServiceClientSync, data: pb2.ResourceData):
        Well.__init__(
            self,
            name=data.name,
            size_x=data.size_x,
            size_y=data.size_y,
            size_z=data.size_z,
            material_z_thickness=data.material_z_thickness if data.HasField("material_z_thickness") else None,
            max_volume=data.max_volume if data.HasField("max_volume") else None,
            bottom_type=data.well_bottom_type or "unknown",
            cross_section_type=data.cross_section_type or "circle",
            category=data.category or "well",
            model=data.model or None,
        )
        self._client = client
        self.tracker = RemoteVolumeTracker(client, self.name)

    def compute_volume_from_height(self, height: float) -> float:
        resp = self._client.compute_volume_from_height(
            pb2.ComputeVolumeHeightRequest(resource_name=self.name, value=height))
        return resp.value

    def compute_height_from_volume(self, volume: float) -> float:
        resp = self._client.compute_height_from_volume(
            pb2.ComputeVolumeHeightRequest(resource_name=self.name, value=volume))
        return resp.value

    def supports_compute_height_volume_functions(self) -> bool:
        resp = self._client.supports_compute_height_volume(
            pb2.ResourceByNameRequest(name=self.name))
        return resp.value


class TipSpotProxy(_SpatialMixin, TipSpot):
    """Proxy for TipSpot — tip creation and tracking go to server."""

    def __init__(self, client: DeckServiceClientSync, data: pb2.ResourceData):
        prototype = data.prototype_tip

        def make_tip(name: str) -> Tip:
            return _tip_from_proto(pb2.TipData(
                type=prototype.type,
                name=name,
                has_filter=prototype.has_filter,
                total_tip_length=prototype.total_tip_length,
                maximal_volume=prototype.maximal_volume,
                fitting_depth=prototype.fitting_depth,
                tip_size=prototype.tip_size,
                pickup_method=prototype.pickup_method,
            ))

        # TipSpot.__init__ swaps size_x and size_y internally, so we pass them
        # in the original (pre-swap) order. Since the server already stores the
        # post-swap values, we reverse the swap here.
        TipSpot.__init__(
            self,
            name=data.name,
            size_x=data.size_y,  # reverse the swap
            size_y=data.size_x,  # reverse the swap
            size_z=data.size_z,
            make_tip=make_tip,
            category=data.category or "tip_spot",
        )
        self._client = client
        self.tracker = RemoteTipTracker(client, self.name)

    def get_tip(self) -> Tip:
        """Always get the tip from the server (authoritative state)."""
        resp = self._client.get_tip(pb2.GetTipRequest(tip_spot_name=self.name))
        return _tip_from_proto(resp)


class PlateProxy(_SpatialMixin, Plate):
    """Proxy for Plate — has_lid() goes to server (lid can be moved)."""

    def __init__(self, client: DeckServiceClientSync, data: pb2.ResourceData,
                 ordering: Dict[str, str]):
        Plate.__init__(
            self,
            name=data.name,
            size_x=data.size_x,
            size_y=data.size_y,
            size_z=data.size_z,
            ordering=OrderedDict(ordering),
            plate_type=data.plate_type or "skirted",
            category=data.category or "plate",
            model=data.model or None,
        )
        self._client = client

    def has_lid(self) -> bool:
        resp = self._client.has_lid(pb2.HasLidRequest(plate_name=self.name))
        return resp.value


class TipRackProxy(_SpatialMixin, TipRack):
    """Proxy for TipRack."""

    def __init__(self, client: DeckServiceClientSync, data: pb2.ResourceData,
                 ordering: Dict[str, str]):
        TipRack.__init__(
            self,
            name=data.name,
            size_x=data.size_x,
            size_y=data.size_y,
            size_z=data.size_z,
            ordering=OrderedDict(ordering),
            category=data.category or "tip_rack",
            model=data.model or None,
        )
        self._client = client


class TrashProxy(_SpatialMixin, Trash):
    """Proxy for Trash."""

    def __init__(self, client: DeckServiceClientSync, data: pb2.ResourceData):
        Trash.__init__(
            self,
            name=data.name,
            size_x=data.size_x,
            size_y=data.size_y,
            size_z=data.size_z,
            material_z_thickness=data.material_z_thickness if data.HasField("material_z_thickness") else 0,
            max_volume=data.max_volume if data.HasField("max_volume") else float("inf"),
            category=data.category or "trash",
            model=data.model or None,
        )
        self._client = client
        self.tracker = RemoteVolumeTracker(client, self.name)


class LidProxy(_SpatialMixin, Lid):
    """Proxy for Lid."""

    def __init__(self, client: DeckServiceClientSync, data: pb2.ResourceData):
        Lid.__init__(
            self,
            name=data.name,
            size_x=data.size_x,
            size_y=data.size_y,
            size_z=data.size_z,
            nesting_z_height=data.nesting_z_height if data.HasField("nesting_z_height") else 0,
            category=data.category or "lid",
            model=data.model or None,
        )
        self._client = client


# ============================================================
# Proxy factory
# ============================================================

_PROXY_MAP = {
    "Well": WellProxy,
    "Plate": PlateProxy,
    "TipSpot": TipSpotProxy,
    "TipRack": TipRackProxy,
    "Trash": TrashProxy,
    "Container": ContainerProxy,
    "Lid": LidProxy,
}


def create_proxy(client: DeckServiceClientSync, data: pb2.ResourceData) -> Resource:
    """Create the appropriate proxy object from a ResourceData message."""
    cls = _PROXY_MAP.get(data.type, ResourceProxy)
    if cls in (PlateProxy, TipRackProxy):
        return cls(client, data, ordering=dict(data.ordering))
    return cls(client, data)
