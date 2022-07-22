.. currentmodule:: pyhamilton.liquid_handling

pyhamilton.liquid_handling.backends package
===========================================

Backends are used to communicate with liquid handling devices on a low level. This can be useful
when you want to have very low level control over the liquid handling device or want to use a
feature that is not yet implemented in the front end.

Abstract
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pyhamilton.liquid_handling.backends.LiquidHandlerBackend

Hardware
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pyhamilton.liquid_handling.backends.hamilton.HamiltonLiquidHandler
    pyhamilton.liquid_handling.backends.hamilton.STAR


Simulator
---------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pyhamilton.liquid_handling.backends.simulation.SimulationBackend
