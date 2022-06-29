.. currentmodule:: pyhamilton.liquid_handling

pyhamilton.liquid_handling.resources package
============================================

Resources represent on-deck liquid handling equipment, including plate and plate carriers and tips
and carriers. Many resources defined in VENUS are also defined in this package. In addition,
using the abstract base classes defined in :ref:`pyhamilton.liquid_handling.resources.abstract <pyhamilton.liquid_handling.resources:abstract>`,
you can define your own resources.

Abstract
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pyhamilton.liquid_handling.resources.abstract
    pyhamilton.liquid_handling.resources.abstract.Coordinate
    pyhamilton.liquid_handling.resources.abstract.Resource
    pyhamilton.liquid_handling.resources.abstract.Tips
    pyhamilton.liquid_handling.resources.abstract.TipType
    pyhamilton.liquid_handling.resources.abstract.Plate
    pyhamilton.liquid_handling.resources.abstract.Carrier
    pyhamilton.liquid_handling.resources.abstract.TipCarrier
    pyhamilton.liquid_handling.resources.abstract.PlateCarrier


ML Star resources
-----------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pyhamilton.liquid_handling.resources.ml_star
    pyhamilton.liquid_handling.resources.ml_star.tip_types
    pyhamilton.liquid_handling.resources.ml_star.tips
    pyhamilton.liquid_handling.resources.ml_star.tip_carriers
    pyhamilton.liquid_handling.resources.ml_star.plate_carriers


Corning Costar
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pyhamilton.liquid_handling.resources.corning_costar.plates
