.. currentmodule:: pylabrobot.agilent

pylabrobot.agilent package
==========================

BenchCel 4R
-----------

.. currentmodule:: pylabrobot.agilent.benchcel

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    BenchCel4R
    BenchCelLabwareSettings
    PlateNotchSettings


BioTek washers and dispensers
-----------------------------

One class per model. Each exposes the capability objects its fitted hardware supports.

.. currentmodule:: pylabrobot.agilent.biotek.lhc

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    EL406
    MultiFlo
    MultiFloFX
    Washer405TS
    Protocol
    read
    write

.. currentmodule:: pylabrobot.agilent.biotek.lhc.devices.components

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    PlateWasher
    SyringeDispenser
    PeristalticDispenser

.. currentmodule:: pylabrobot.agilent.biotek.lhc.devices

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    InstrumentSettings
    SettingsComparison

Steps
~~~~~

One class per operation the instruments perform, holding everything that operation can be given.
A capability method takes these where it takes a step, and a protocol file stores them.

.. currentmodule:: pylabrobot.agilent.biotek.lhc.protocols.steps.steps

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    ManifoldWash
    ManifoldDispense
    ManifoldAspirate
    ManifoldPrime
    ManifoldAutoClean
    Wash1536
    StripWash
    StripDispense
    StripAspirate
    StripPrime
    SyringeDispense
    SyringePrime
    PeriDispense
    PeriRandomAccessDispense
    PeriPrime
    PeriPurge
    PeriWashDispense
    PeriWashAspirate
    ShakeSoak

.. currentmodule:: pylabrobot.agilent.biotek.lhc.protocols.steps

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Step
    step_from_definition

Step parts
~~~~~~~~~~

The groups of parameters the steps above are built from: where in the well a step works, which
stages of a wash run, and the optional behaviours a step can switch on.

.. currentmodule:: pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Positioning

.. currentmodule:: pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    WashStages
    Sectors
    PreDispense
    SecondaryAspirate
    VacuumDelay
    Shake
    Soak
    Submerge
    RandomAccess
    WellVolumeMap

.. currentmodule:: pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    WellMask

.. currentmodule:: pylabrobot.agilent.biotek.lhc.protocols.validation

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    ValidationReport
    StepReport
    Rejection

.. currentmodule:: pylabrobot.agilent.biotek.lhc.error_handling

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    BiotekError
    LinkError
    RejectedError


BioTek Cytation
---------------

.. currentmodule:: pylabrobot.agilent.biotek.cytation

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Cytation1
    Cytation5
    CytationImagingConfig


BioTek Synergy H1
------------------

.. currentmodule:: pylabrobot.agilent.biotek.synergy

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    SynergyH1


.. _vspin-api:

VSpin
-----

For operation states, plate-transfer coordination, and recovery behavior, see the
:doc:`VSpin and Access2 state-machine guide </user_guide/agilent/vspin/state-machine>`.

.. currentmodule:: pylabrobot.agilent.vspin

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    VSpin
    Access2
    Access2Driver
    ServoStatus
    Access2Status


PlateLoc
--------

.. currentmodule:: pylabrobot.agilent.plateloc

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    PlateLoc
    PlateLocSerialProfile
    PlateLocStatus
    PlateLocError
