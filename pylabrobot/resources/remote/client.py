"""RemoteDeck — drop-in Deck replacement that loads from a ConnectRPC server."""

from __future__ import annotations

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.resource import Resource

from . import deck_service_pb2 as pb2
from .deck_service_connect import DeckServiceClientSync
from .proxies import _SpatialMixin, create_proxy


class RemoteDeck(_SpatialMixin, Deck):
    """Drop-in replacement for Deck that loads its resource tree from a ConnectRPC server.

    Usage::

        deck = RemoteDeck.connect("http://localhost:8080")
        lh = LiquidHandler(backend=STARBackend(), deck=deck)
        await lh.setup()
    """

    def __init__(self, client: DeckServiceClientSync):
        self._client = client
        self._building = True  # suppress RPC calls during initial tree build

        # Fetch the full tree from the server
        tree = client.get_tree(pb2.GetTreeRequest())
        deck_data = tree.data

        # Initialize the real Deck base class with the server's dimensions
        origin = Coordinate(0, 0, 0)
        if deck_data.HasField("location"):
            origin = Coordinate(deck_data.location.x, deck_data.location.y, deck_data.location.z)

        Deck.__init__(
            self,
            size_x=deck_data.size_x,
            size_y=deck_data.size_y,
            size_z=deck_data.size_z,
            name=deck_data.name or "deck",
            origin=origin,
        )

        # Recursively build proxy tree and assign as children
        self._build_tree(tree, parent=self)
        self._building = False

    def _build_tree(self, tree_node: pb2.ResourceTree, parent: Resource) -> None:
        """Recursively create proxy objects and wire up parent/child."""
        for child_tree in tree_node.children:
            child_data = child_tree.data
            proxy = create_proxy(self._client, child_data)
            loc = Coordinate(0, 0, 0)
            if child_data.HasField("location"):
                loc = Coordinate(
                    child_data.location.x, child_data.location.y, child_data.location.z)
            # Use Resource.assign_child_resource directly to avoid RPC during build
            Resource.assign_child_resource(parent, proxy, location=loc)
            self._build_tree(child_tree, parent=proxy)

    @classmethod
    def connect(cls, base_url: str = "http://localhost:8080") -> RemoteDeck:
        """Connect to a remote deck server.

        Args:
            base_url: The HTTP URL of the deck server (e.g. "http://localhost:8080")
        """
        client = DeckServiceClientSync(address=base_url)
        return cls(client)

    # --- Structure mutations go to server ---

    def assign_child_resource(self, resource, location=None, reassign=True):
        super().assign_child_resource(resource, location=location, reassign=reassign)
        if not self._building and location is not None:
            self._client.assign_child(pb2.AssignChildRequest(
                child_name=resource.name,
                parent_name=self.name,
                location=pb2.Coordinate(x=location.x, y=location.y, z=location.z),
            ))

    def unassign_child_resource(self, resource):
        super().unassign_child_resource(resource)
        if not self._building:
            self._client.unassign_child(
                pb2.UnassignChildRequest(resource_name=resource.name))
