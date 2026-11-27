"""Device vocabulary for the BioTek washer/dispenser family.

Grouped by what it describes: :mod:`instrument` for the instrument and its fitted options,
:mod:`motion` for motors and the carrier, :mod:`steps` for what a protocol step is written in,
:mod:`plates` for labware formats, and :mod:`status` for what a run reports.

The words a caller types are string ``Literal`` aliases with a dict to their wire value; integer
enums are reserved for wire codes a caller never writes.
"""

from pylabrobot.agilent.biotek.lhc.enums.instrument import *
from pylabrobot.agilent.biotek.lhc.enums.motion import *
from pylabrobot.agilent.biotek.lhc.enums.plates import *
from pylabrobot.agilent.biotek.lhc.enums.status import *
from pylabrobot.agilent.biotek.lhc.enums.steps import *
