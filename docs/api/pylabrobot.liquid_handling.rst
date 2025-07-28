.. currentmodule:: pylabrobot.liquid_handling

pylabrobot.liquid_handling package
==================================

This package contains all APIs relevant to liquid handling.
.. See :doc:`Basic liquid handling </user_guide/00_liquid-handling/hamilton-star/basic>` for a simple example.

Machine control is split into two parts: backends and front ends. Backends are used to control the
machine, and front ends are used to interact with the backend. Front ends are designed to be
largely backend agnostic, and can be used with any backend, meaning programs using this API can
be run on practically all supported hardware.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    liquid_handler.LiquidHandler


Backends
--------

.. toctree::
  :maxdepth: 3

  pylabrobot.liquid_handling.backends


Operations
----------

Operations are the main data holders used to transmit information from the liquid handler to a backend. They are the basis of "standard form".


.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

  standard


Strictness
----------

.. toctree::
   :maxdepth: 1
   :caption: Strictness

   pylabrobot.liquid_handling.strictness
