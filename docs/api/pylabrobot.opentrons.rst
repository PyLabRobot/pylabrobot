.. currentmodule:: pylabrobot.opentrons

pylabrobot.opentrons package
============================

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

Flex
----

.. currentmodule:: pylabrobot.opentrons.flex

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Flex

Heads
-----

.. currentmodule:: pylabrobot.opentrons.flex.flex_head

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    FlexHead1
    FlexHead8
    FlexHead96

Gripper
-------

.. currentmodule:: pylabrobot.opentrons.flex.flex_gripper

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    FlexGripper
