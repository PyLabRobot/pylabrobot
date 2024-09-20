.. currentmodule:: pylabrobot.liquid_handling

pylabrobot.liquid_handling.backends package
===========================================

Backends are used to communicate with liquid handling devices on a low level. Using them directly can be useful when you want to have very low level control over the liquid handling device or want to use a feature that is not yet implemented in the front end.

Abstract
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    backends.backend.LiquidHandlerBackend
    backends.serializing_backend.SerializingBackend

Hardware
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    backends.hamilton.base.HamiltonLiquidHandler
    backends.hamilton.STAR.STAR
    backends.hamilton.vantage.Vantage
    backends.opentrons_backend.OpentronsBackend
    backends.tecan.EVO.EVO

Net
---

Net backends can be used to communicate with servers that manage liquid handling devices.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    backends.http.HTTPBackend
    backends.websocket.WebSocketBackend


Testing
-------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    backends.chatterbox_backend.ChatterBoxBackend
