.. currentmodule:: pylabrobot.opentrons

pylabrobot.opentrons package
=============================

``OT2`` owns the connection, active run, deck, and mounted pipettes. Use
``connect()`` for health and discovery queries without starting a run or moving
the robot; ``setup()`` also loads the pipettes into a run and optionally homes.
Queries return fresh values without changing the driver's session state.

``OpentronsAPI`` contains the named HTTP endpoints and response parsing.
``OpentronsRun`` provides command primitives and waits for their completion.
Both use the device's ``pylabrobot.io.HTTP`` transport. Pipette operations own
resource tracking and retraction to traversal height.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    OT2
    OT2Pipette
    OpentronsAPI
    OpentronsRun
    RobotInfo
    MountedPipette
    ModuleInfo
    OpentronsError
    OpentronsCommandError
    OpentronsCommandTimeout
    OpentronsProtocolError
