.. currentmodule:: pylabrobot

pylabrobot.resources package
============================

Resources represent on-deck liquid handling equipment, including tip racks, plates and carriers. Many resources defined in the VENUS and Opentrons labware libraries are also defined in this package. In addition, by (optionally subclassing and) instantiating the appropriate classes, you can define your own resources.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources
    pylabrobot.resources.Carrier
    pylabrobot.resources.Container
    pylabrobot.resources.Coordinate
    pylabrobot.resources.Deck
    pylabrobot.resources.ItemizedResource
    pylabrobot.resources.create_equally_spaced
    pylabrobot.resources.Lid
    pylabrobot.resources.Liquid
    pylabrobot.resources.PetriDish
    pylabrobot.resources.Plate
    pylabrobot.resources.PlateCarrier
    pylabrobot.resources.Resource
    pylabrobot.resources.ResourceStack
    pylabrobot.resources.tip.Tip
    pylabrobot.resources.TipCarrier
    pylabrobot.resources.TipRack
    pylabrobot.resources.Trough
    pylabrobot.resources.Tube
    pylabrobot.resources.TubeCarrier
    pylabrobot.resources.TubeRack
    pylabrobot.resources.Well


Azenta
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.corning_axygen.plates


Boekel
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.boekel.tube_carriers


Corning Axygen
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.corning_axygen.plates


Corning Costar
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.corning_costar.plates


Falcon
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.falcon.tubes


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


Opentrons
---------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.opentrons
    pylabrobot.resources.opentrons.deck
    pylabrobot.resources.opentrons.load
    pylabrobot.resources.opentrons.tip_racks
    pylabrobot.resources.opentrons.plates


Porvair
-------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.porvair.plates


Revvity
-------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.revvity.plates



Tecan
-----

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.tecan
    pylabrobot.resources.tecan.plates
    pylabrobot.resources.tecan.plate_carriers
    pylabrobot.resources.tecan.tecan_decks
    pylabrobot.resources.tecan.tecan_resource
    pylabrobot.resources.tecan.tip_carriers
    pylabrobot.resources.tecan.tip_creators
    pylabrobot.resources.tecan.tip_racks
    pylabrobot.resources.tecan.wash


Thermo Fisher
-------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.thermo_fisher.troughs


VWR
---

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.vwr.troughs


Tip trackers
------------

See :doc:`Using trackers <using-trackers>` for a tutorial.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

  pylabrobot.resources.no_tip_tracking
  pylabrobot.resources.set_tip_tracking
  pylabrobot.resources.tip_tracker.TipTracker


Volume trackers
---------------

See :doc:`Using trackers <using-trackers>` for a tutorial.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

  pylabrobot.resources.no_volume_tracking
  pylabrobot.resources.set_volume_tracking
  pylabrobot.resources.volume_tracker.VolumeTracker
