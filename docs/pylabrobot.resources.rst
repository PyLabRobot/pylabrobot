.. currentmodule:: pylabrobot

pylabrobot.resources package
============================

Resources represent on-deck liquid handling equipment, including plate and plate carriers and tips and carriers. Many resources defined in VENUS are also defined in this package. In addition, by instantiating classes defined in :ref:`pylabrobot.resources.abstract <pylabrobot.resources:abstract>` from scratch, you can define your own resources.

Abstract
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.abstract
    pylabrobot.resources.abstract.Coordinate
    pylabrobot.resources.abstract.Deck
    pylabrobot.resources.abstract.ItemizedResource
    pylabrobot.resources.abstract.create_equally_spaced
    pylabrobot.resources.abstract.Lid
    pylabrobot.resources.abstract.Resource
    pylabrobot.resources.ResourceStack
    pylabrobot.resources.abstract.TipRack
    pylabrobot.resources.abstract.Plate
    pylabrobot.resources.abstract.Carrier
    pylabrobot.liquid_handling.tip.Tip
    pylabrobot.resources.abstract.TipCarrier
    pylabrobot.resources.abstract.PlateCarrier


ML Star resources
-----------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.ml_star
    pylabrobot.resources.ml_star.tip_creators
    pylabrobot.resources.ml_star.tip_racks
    pylabrobot.resources.ml_star.tip_carriers
    pylabrobot.resources.ml_star.plate_carriers


Corning Costar
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.corning_costar.plates


Hamilton
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.hamilton
    pylabrobot.resources.hamilton.hamilton_decks.HamiltonDeck
    pylabrobot.resources.hamilton.STARDeck
    pylabrobot.resources.hamilton.STARLetDeck


Opentrons
---------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.opentrons
    pylabrobot.resources.opentrons.load
    pylabrobot.resources.opentrons.tip_racks
    pylabrobot.resources.opentrons.plates
