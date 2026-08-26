"""PyLabRobot integration for the Agilent Bravo liquid handler.

Exposes the two pieces most callers need: :class:`Bravo`, the async device
facade, and :class:`AgilentBravoBackend`, the PyLabRobot
:class:`~pylabrobot.legacy.liquid_handling.backends.backend.LiquidHandlerBackend`
built on top of it, plus :class:`BravoDeck`, the PyLabRobot deck model for
the instrument's nine deck sites. Everything else in this package --
``transport``, ``protocol``, ``controllers``, ``darwin``, ``deck``,
``state_machine``, and the head/tip/config modules -- is available by
importing the relevant submodule directly.
"""

from __future__ import annotations

from .backend import AgilentBravoBackend
from .bravo import Bravo
from .deck.resource import BravoDeck

__all__ = ["AgilentBravoBackend", "Bravo", "BravoDeck"]
