.. currentmodule:: pylabrobot.liquid_handling

pylabrobot.liquid_handling.resources package
============================================

Resources represent on-deck liquid handling equipment, including plate and plate carriers and tips
and carriers. Many resources defined in VENUS are also defined in this package. In addition,
using the abstract base classes defined in :ref:`pylabrobot.liquid_handling.resources.abstract <pylabrobot.liquid_handling.resources:abstract>`,
you can define your own resources.

Abstract
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.abstract
    pylabrobot.liquid_handling.resources.abstract.Coordinate
    pylabrobot.liquid_handling.resources.abstract.Deck
    pylabrobot.liquid_handling.resources.abstract.Lid
    pylabrobot.liquid_handling.resources.abstract.Resource
    pylabrobot.liquid_handling.resources.abstract.Tips
    pylabrobot.liquid_handling.resources.abstract.TipType
    pylabrobot.liquid_handling.resources.abstract.Plate
    pylabrobot.liquid_handling.resources.abstract.Carrier
    pylabrobot.liquid_handling.resources.abstract.TipCarrier
    pylabrobot.liquid_handling.resources.abstract.PlateCarrier


Shared
------

Resources that are not abstract, but also not tied to a specific liquid handling equipment.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.Hotel
    pylabrobot.liquid_handling.resources.PlateReader

ML Star resources
-----------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.ml_star
    pylabrobot.liquid_handling.resources.ml_star.tip_types
    pylabrobot.liquid_handling.resources.ml_star.tips
    pylabrobot.liquid_handling.resources.ml_star.tip_carriers
    pylabrobot.liquid_handling.resources.ml_star.plate_carriers


Corning Costar
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.corning_costar.plates
