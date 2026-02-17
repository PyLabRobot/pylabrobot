"""Remote deck via ConnectRPC — serve a Deck over HTTP and access it as a drop-in replacement."""

from .client import RemoteDeck
from .server import DeckServiceImpl, create_app

__all__ = ["RemoteDeck", "DeckServiceImpl", "create_app"]
