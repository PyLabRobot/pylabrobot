.. currentmodule:: pylabrobot.resources

pylabrobot.resources package
============================

Resources represent on-deck liquid handling equipment, including tip racks, plates and carriers. Many resources defined in the VENUS and Opentrons labware libraries are also defined in this package. In addition, by (optionally subclassing and) instantiating the appropriate classes, you can define your own resources.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Carrier
    Container
    Coordinate
    Deck
    ItemizedResource
    utils.create_equally_spaced_2d
    Lid
    Liquid
    PetriDish
    Plate
    PlateCarrier
    PlateHolder
    Resource
    ResourceHolder
    ResourceStack
    Rotation
    tip.Tip
    TipCarrier
    TipRack
    Trough
    Tube
    TubeCarrier
    TubeRack
    Well


Azenta
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    azenta.plates


Biorad
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    biorad.plates


Boekel
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    boekel.tube_carriers


Corning Axygen
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    corning.axygen.plates


Corning Costar
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    corning.costar.plates


Corning Falcon
--------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    corning.falcon.tubes


Hamilton
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    hamilton
    hamilton.hamilton_decks.HamiltonDeck
    hamilton.STARDeck
    hamilton.STARLetDeck


ML Star resources
-----------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    hamilton
    hamilton.tip_creators
    hamilton.tip_racks
    hamilton.tip_carriers
    hamilton.plate_carriers


Opentrons
---------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    opentrons
    opentrons.deck
    opentrons.load
    opentrons.plates
    opentrons.tip_racks
    opentrons.tube_racks


Porvair
-------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    porvair.plates


Revvity
-------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    revvity.plates



Tecan
-----

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    tecan
    tecan.plates
    tecan.plate_carriers
    tecan.tecan_decks
    tecan.tecan_resource
    tecan.tip_carriers
    tecan.tip_creators
    tecan.tip_racks
    tecan.wash


Thermo Fisher
-------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    thermo_fisher.troughs


VWR
---

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    vwr.troughs


Tip trackers
------------

See :doc:`Using trackers </user_guide/machine-agnostic-features/using-trackers>` for a tutorial.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

  no_tip_tracking
  set_tip_tracking
  tip_tracker.TipTracker


Volume trackers
---------------

See :doc:`Using trackers </user_guide/machine-agnostic-features/using-trackers>` for a tutorial.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

  no_volume_tracking
  set_volume_tracking
  volume_tracker.VolumeTracker

Utils
-----

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

  utils
