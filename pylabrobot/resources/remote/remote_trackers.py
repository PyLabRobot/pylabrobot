"""Remote tracker implementations that delegate to the DeckService server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pylabrobot.resources.tip import Tip

from . import deck_service_pb2 as pb2

if TYPE_CHECKING:
    from .deck_service_connect import DeckServiceClientSync


def _tip_from_proto(tip_data: pb2.TipData) -> Tip:
    """Construct a Tip or HamiltonTip from a TipData protobuf message."""
    if tip_data.type == "HamiltonTip" and tip_data.tip_size:
        from pylabrobot.resources.hamilton.tip_creators import HamiltonTip
        return HamiltonTip(
            has_filter=tip_data.has_filter,
            total_tip_length=tip_data.total_tip_length,
            maximal_volume=tip_data.maximal_volume,
            tip_size=tip_data.tip_size,
            pickup_method=tip_data.pickup_method,
            name=tip_data.name or None,
        )
    return Tip(
        has_filter=tip_data.has_filter,
        total_tip_length=tip_data.total_tip_length,
        maximal_volume=tip_data.maximal_volume,
        fitting_depth=tip_data.fitting_depth,
        name=tip_data.name or None,
    )


class RemoteVolumeTracker:
    """Drop-in replacement for VolumeTracker that delegates to the server."""

    def __init__(self, client: DeckServiceClientSync, resource_name: str):
        self._client = client
        self._resource_name = resource_name

    def _get_state(self) -> pb2.VolumeTrackerState:
        return self._client.get_volume_tracker_state(
            pb2.ResourceByNameRequest(name=self._resource_name))

    @property
    def is_disabled(self) -> bool:
        return self._get_state().is_disabled

    def get_used_volume(self) -> float:
        state = self._get_state()
        return state.pending_volume

    def get_free_volume(self) -> float:
        state = self._get_state()
        return state.max_volume - state.pending_volume

    def remove_liquid(self, volume: float) -> None:
        self._client.remove_liquid(
            pb2.TrackerOpRequest(resource_name=self._resource_name, volume=volume))

    def add_liquid(self, volume: float) -> None:
        self._client.add_liquid(
            pb2.TrackerOpRequest(resource_name=self._resource_name, volume=volume))

    def set_volume(self, volume: float) -> None:
        # Not directly supported as a single RPC; approximate via add/remove.
        # For correctness, the server should ideally handle this.
        pass

    def commit(self) -> None:
        self._client.commit_volume_trackers(
            pb2.CommitRollbackRequest(resource_names=[self._resource_name]))

    def rollback(self) -> None:
        self._client.rollback_volume_trackers(
            pb2.CommitRollbackRequest(resource_names=[self._resource_name]))

    def serialize(self) -> dict:
        state = self._get_state()
        return {
            "volume": state.volume,
            "pending_volume": state.pending_volume,
            "max_volume": state.max_volume,
            "is_disabled": state.is_disabled,
        }

    def disable(self) -> None:
        pass  # Not supported remotely; trackers are managed by the server.

    def enable(self) -> None:
        pass  # Not supported remotely; trackers are managed by the server.

    def register_callback(self, callback) -> None:
        pass  # Callbacks are server-side only.


class RemoteTipTracker:
    """Drop-in replacement for TipTracker that delegates to the server."""

    def __init__(self, client: DeckServiceClientSync, resource_name: str):
        self._client = client
        self._resource_name = resource_name

    def _get_state(self) -> pb2.TipTrackerState:
        return self._client.get_tip_tracker_state(
            pb2.ResourceByNameRequest(name=self._resource_name))

    @property
    def is_disabled(self) -> bool:
        return self._get_state().is_disabled

    @property
    def has_tip(self) -> bool:
        return self._get_state().has_tip

    def get_tip(self) -> Tip:
        state = self._get_state()
        if not state.has_tip:
            from pylabrobot.resources.tip_tracker import NoTipError
            raise NoTipError(f"No tip on {self._resource_name}")
        return _tip_from_proto(state.tip)

    def remove_tip(self, commit: bool = False) -> None:
        self._client.remove_tip(
            pb2.TipTrackerOpRequest(tip_spot_name=self._resource_name))
        if commit:
            self.commit()

    def add_tip(self, tip: Optional[Tip] = None, origin=None, commit: bool = True) -> None:
        self._client.add_tip(
            pb2.TipTrackerOpRequest(tip_spot_name=self._resource_name))
        if commit:
            self.commit()

    def commit(self) -> None:
        self._client.commit_tip_trackers(
            pb2.CommitRollbackRequest(resource_names=[self._resource_name]))

    def rollback(self) -> None:
        self._client.rollback_tip_trackers(
            pb2.CommitRollbackRequest(resource_names=[self._resource_name]))

    def serialize(self) -> dict:
        state = self._get_state()
        return {"has_tip": state.has_tip, "is_disabled": state.is_disabled}

    def disable(self) -> None:
        pass

    def enable(self) -> None:
        pass

    def register_callback(self, callback) -> None:
        pass

    def clear(self) -> None:
        pass
