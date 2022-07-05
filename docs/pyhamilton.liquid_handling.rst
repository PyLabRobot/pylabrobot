.. currentmodule:: pyhamilton.liquid_handling

pyhamilton.liquid_handling package
==================================

This package contains all APIs relevant to liquid handling.
See :ref:`Basic liquid handling <Basic:Basic liquid handling>` for a simple example.

Machine control is split into two parts: backends and front ends. Backends are used to control the
machine, and front ends are used to interact with the backend. Front ends are designed to be
largely backend agnostic, and can be used with any backend, meaning programs using this API can
be run on practically all supported hardware.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pyhamilton.liquid_handling.LiquidHandler
    pyhamilton.liquid_handling.liquid_handler.AspirationInfo
    pyhamilton.liquid_handling.liquid_handler.DispenseInfo


Backends
--------

.. toctree::
  :maxdepth: 3

  pyhamilton.liquid_handling.backends


Resources
---------

The subpackage :code:`resources` contains all resources used by the liquid handling package. You
can use these to define deck layouts. Many of VENUS' resources are already implemented. If the
resource is not implemented, you can implement it yourself by subclassing the appropriate class
in :ref:`pyhamilton.liquid_handling.resources:abstract`.

.. toctree::
  :maxdepth: 2

  pyhamilton.liquid_handling.resources


Liquid classes
--------------

.. toctree::
  :maxdepth: 2

  pyhamilton.liquid_handling.liquid_classes
