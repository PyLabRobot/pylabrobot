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

    pylabrobot.liquid_handling.backends.backend.LiquidHandlerBackend
    pylabrobot.liquid_handling.backends.serializing_backend.SerializingBackend
    pylabrobot.liquid_handling.backends.USBBackend.USBBackend

Hardware
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.backends.hamilton.STAR.HamiltonLiquidHandler
    pylabrobot.liquid_handling.backends.hamilton.STAR.STAR
    pylabrobot.liquid_handling.backends.opentrons_backend.OpentronsBackend

Net
---

Net backends can be used to communicate with servers that manage liquid handling devices.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.backends.http.HTTPBackend
    pylabrobot.liquid_handling.backends.websocket.WebSocketBackend

Simulator
---------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.backends.simulation.SimulatorBackend
