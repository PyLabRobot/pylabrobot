"""Brooks PreciseFlex robots.

Why one package for the family - every PreciseFlex arm runs the same Guidance/TCS controller and
speaks the same GPL command protocol, DataIDs, and error codes, so they share the bulk of this
driver. They differ only in kinematics (per geometry, e.g. the c10's R-P-R-R joint order, the c8A's
six axes) and gripper, which are handled by per-model device classes and per-geometry kinematics
modules within the package. Grouping by the shared controller keeps that common driver in one place
rather than duplicated per arm model.

Scope - the PreciseFlex robot line. Implemented:

- PreciseFlex 400 (PF400)
- PreciseFlex 3400 (PF3400)

To be added here:

- PreciseFlex 100 / 1400 (PF100 / PF1400)
- c-series: c3, c5, c8A, c10
- direct-drive: DD4, DD6
- linear rail

Everything here is PreciseFlex-specific, including the TCS controller protocol (``tcs_modules``),
``error_codes``, and the controller DataIDs (``data_ids``) - the PreciseFlex line is the only user of
the Guidance/TCS controller, so they live with it. A future, genuinely different Brooks device family
would get its own sibling package under ``brooks/``, and anything shared would be lifted up then.

Re-exports the public classes so ``from pylabrobot.brooks.precise_flex import PreciseFlex400`` keeps
working.
"""

from pylabrobot.brooks.precise_flex.arm_backend import PreciseFlexArmBackend
from pylabrobot.brooks.precise_flex.config import (
  Axis,
  PreciseFlexConfiguration,
)
from pylabrobot.brooks.precise_flex.driver import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.errors import OutOfRangeOfMotionError, PreciseFlexError
from pylabrobot.brooks.precise_flex.kinematics import (
  PreciseFlexCartesianPose,
  WorkEnvelope,
)
from pylabrobot.brooks.precise_flex.precise_flex import (
  PreciseFlex400,
  PreciseFlex3400,
)

__all__ = [
  "Axis",
  "PreciseFlex400",
  "PreciseFlex3400",
  "PreciseFlexArmBackend",
  "PreciseFlexCartesianPose",
  "PreciseFlexConfiguration",
  "PreciseFlexDriver",
  "PreciseFlexError",
  "OutOfRangeOfMotionError",
  "WorkEnvelope",
]
