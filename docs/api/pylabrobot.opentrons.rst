.. currentmodule:: pylabrobot.opentrons

pylabrobot.opentrons package
=============================

``OT2`` owns the connection, active run, deck, and mounted pipettes. Use
``connect()`` for health and discovery queries without starting a run or moving
the robot; ``setup()`` also loads the pipettes into a run and optionally homes.
Queries return fresh values without changing the driver's session state.

Each mount is an ``OT2SingleChannelPipette`` or ``OT2_8ChannelPipette``, selected
from the discovered model. Both share motion, command execution, and transactional
tracking. Single-channel pipettes take one tip spot or container; eight-channel
pipettes take targets in nozzle order, with a shared volume per mounted tip.

Eight-channel pickup accepts one to eight consecutive tip spots in a single rack
column, mapped to nozzles starting at 0. For example,
``await pipette.pick_up_tips(rack["E1:H1"])`` mounts four tips on nozzles 0-3.
Occupied tip spots under the remaining nozzles always reject the pickup. Include
every tip the head would pick up in the selection, or clear the extra spots first.
All declared tips are committed together after the command succeeds.

``pipette.tips`` contains the mounted tips in consecutive nozzle order starting at 0.
Liquid operations and rack drops require one target per mounted tip in that order.
``return_tips()`` returns every mounted tip to its pickup spot.

Tip pickup and return/drop validate the nominal PLR target against the mount's
reach before sending commands. Eight-channel reach checks subtract the reference
nozzle's Y offset from ``OT2RobotGeometry.channel_y_offsets()`` to evaluate the
head center; command coordinates still address the reference nozzle.
Eight-channel pickup also checks other deck
labware against the full nozzle span, padded by 5 mm in XY and 10 mm below the
nozzle engagement height. Rotated resources and overhanging child resources
are included. This conservative destination check does not validate the travel
path or model the complete pipette body. The full eight-nozzle footprint is checked
even for partial pickups.

``OpentronsAPI`` contains the named HTTP endpoints and response parsing.
``OpentronsRun`` provides command primitives and waits for their completion.
Both use the device's ``pylabrobot.io.HTTP`` transport. Pipette operations own
resource tracking and retraction to traversal height.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    OT2
    OT2SingleChannelPipette
    OT2_8ChannelPipette
    OpentronsAPI
    OpentronsRun
    RobotInfo
    MountedPipette
    ModuleInfo
    OpentronsError
    OpentronsCommandError
    OpentronsCommandTimeout
    OpentronsProtocolError
