.. currentmodule:: pylabrobot.liquid_handling

pylabrobot.liquid_handling.resources package
============================================

Resources represent on-deck liquid handling equipment, including plate and plate carriers and tips and carriers. Many resources defined in VENUS are also defined in this package. In addition, by instantiating classes defined in :ref:`pylabrobot.liquid_handling.resources.abstract <pylabrobot.liquid_handling.resources:abstract>` from scratch, you can define your own resources.

Abstract
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.abstract
    pylabrobot.liquid_handling.resources.abstract.Coordinate
    pylabrobot.liquid_handling.resources.abstract.Deck
    pylabrobot.liquid_handling.resources.abstract.ItemizedResource
    pylabrobot.liquid_handling.resources.abstract.create_equally_spaced
    pylabrobot.liquid_handling.resources.abstract.Lid
    pylabrobot.liquid_handling.resources.abstract.Resource
    pylabrobot.liquid_handling.resources.ResourceStack
    pylabrobot.liquid_handling.resources.abstract.TipRack
    pylabrobot.liquid_handling.resources.abstract.Plate
    pylabrobot.liquid_handling.resources.abstract.Carrier
    pylabrobot.liquid_handling.tip.Tip
    pylabrobot.liquid_handling.resources.abstract.TipCarrier
    pylabrobot.liquid_handling.resources.abstract.PlateCarrier


Shared
------

Resources that are not abstract, but also not tied to a specific liquid handling equipment.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.PlateReader

ML Star resources
-----------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.ml_star
    pylabrobot.liquid_handling.resources.ml_star.tip_creators
    pylabrobot.liquid_handling.resources.ml_star.tip_racks
    pylabrobot.liquid_handling.resources.ml_star.tip_carriers
    pylabrobot.liquid_handling.resources.ml_star.plate_carriers


Corning Costar
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.corning_costar.plates


Hamilton
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.hamilton
    pylabrobot.liquid_handling.resources.hamilton.hamilton_decks.HamiltonDeck
    pylabrobot.liquid_handling.resources.hamilton.STARDeck
    pylabrobot.liquid_handling.resources.hamilton.STARLetDeck


Opentrons
---------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.liquid_handling.resources.opentrons
    pylabrobot.liquid_handling.resources.opentrons.load
    pylabrobot.liquid_handling.resources.opentrons.tip_racks
    pylabrobot.liquid_handling.resources.opentrons.plates
