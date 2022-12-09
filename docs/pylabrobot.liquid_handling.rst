.. currentmodule:: pylabrobot.liquid_handling

pylabrobot.liquid_handling package
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

    pylabrobot.liquid_handling.liquid_handler.LiquidHandler


Backends
--------

.. toctree::
  :maxdepth: 3

  pylabrobot.liquid_handling.backends


Resources
---------

The subpackage :code:`resources` contains all resources used by the liquid handling package. You can use these to define deck layouts. Many of VENUS' and Opentrons' resources are already implemented. If the resource is not implemented, you can implement it yourself by implementing or subclassing the appropriate class in :ref:`pylabrobot.liquid_handling.resources:abstract`.

.. toctree::
  :maxdepth: 2

  pylabrobot.liquid_handling.resources


Tip trackers
------------

See :doc:`Using tip trackers <using-tip-trackers>` for a tutorial.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

  pylabrobot.liquid_handling.no_tip_tracking
  pylabrobot.liquid_handling.set_tip_tracking
  pylabrobot.liquid_handling.tip_tracker.TipTracker
  pylabrobot.liquid_handling.tip_tracker.ChannelTipTracker
  pylabrobot.liquid_handling.tip_tracker.SpotTipTracker


Operations
----------

Operations are the main data holders used to transmit information from the liquid handler to a backend. They are the basis of "standard form".


.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

  pylabrobot.liquid_handling.standard


Strictness
----------

.. toctree::
   :maxdepth: 1
   :caption: Strictness

   pylabrobot.liquid_handling.strictness
