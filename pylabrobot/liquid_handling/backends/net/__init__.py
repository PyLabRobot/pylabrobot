""" Backends that don't talk to a physical device, but rather over a network """

from .websocket import WebSocketBackend
from .http import HTTPBackend
