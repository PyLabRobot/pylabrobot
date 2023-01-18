.. currentmodule:: pylabrobot

pylabrobot.resources package
============================

Resources represent on-deck liquid handling equipment, including tip racks, plates and carriers. Many resources defined in the VENUS and Opentrons labware libraries are also defined in this package. In addition, by (optionally subclassing and) instantiating the appropriate classes, you can define your own resources.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources
    pylabrobot.resources.Coordinate
    pylabrobot.resources.Deck
    pylabrobot.resources.ItemizedResource
    pylabrobot.resources.create_equally_spaced
    pylabrobot.resources.Lid
    pylabrobot.resources.Resource
    pylabrobot.resources.ResourceStack
    pylabrobot.resources.TipRack
    pylabrobot.resources.Plate
    pylabrobot.resources.Carrier
    pylabrobot.resources.tip.Tip
    pylabrobot.resources.TipCarrier
    pylabrobot.resources.PlateCarrier


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

VWR
---

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    pylabrobot.resources.vwr.troughs


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
  pylabrobot.resources.tip_tracker.SpotTipTracker


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
  pylabrobot.resources.volume_tracker.ContainerVolumeTracker
  pylabrobot.resources.volume_tracker.TipVolumeTracker
