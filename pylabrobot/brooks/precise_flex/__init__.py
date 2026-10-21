"""Brooks PreciseFlex robots.

Why one package for the family - every PreciseFlex arm runs the same Guidance/TCS controller and
speaks the same GPL command protocol, DataIDs, and error codes, so they share the bulk of this
driver. They differ only in kinematics (per geometry, e.g. the c10's R-P-R-R joint order, the c8A's
six axes), handled in ``kinematics``, and gripper. Grouping by the shared controller keeps that
common driver in one place rather than duplicated per arm model.

Scope - the PreciseFlex robot line. Implemented:

- PreciseFlex 400 (PF400)
- PreciseFlex 3400 (PF3400)

To be added here:

- PreciseFlex 100 / 1400 (PF100 / PF1400)
- c-series: c3, c5, c8A, c10
- direct-drive: DD4, DD6
- linear rail

Everything here is PreciseFlex-specific, including the TCS controller protocol (``tcs_modules``),
``errors``, and the controller DataIDs (``data_ids``) - the PreciseFlex line is the only user of the
Guidance/TCS controller, so they live with it. A future, genuinely different Brooks device family
would get its own sibling package under ``brooks/``, and anything shared would be lifted up then.

Re-exports the public classes, so ``from pylabrobot.brooks.precise_flex import PreciseFlexDriver``
works.
"""

from pylabrobot.brooks.precise_flex.driver.configuration import (
  Axis,
  PreciseFlexConfiguration,
)
from pylabrobot.brooks.precise_flex.driver.errors import (
  OutOfRangeOfMotionError,
  PreciseFlexCollisionError,
  PreciseFlexError,
  PreciseFlexNotReadyError,
  PreciseFlexPowerError,
  PreciseFlexReachError,
  PreciseFlexServoError,
)
from pylabrobot.brooks.precise_flex.driver.features.arm import MotionProfile, PreciseFlexArm
from pylabrobot.brooks.precise_flex.driver.features.gripper import PreciseFlexGripper
from pylabrobot.brooks.precise_flex.driver.features.rail import PreciseFlexRail
from pylabrobot.brooks.precise_flex.driver.master import PreciseFlex, PreciseFlexDriver
from pylabrobot.brooks.precise_flex.kinematics import (
  ElbowOrientation,
  PreciseFlexCartesianPose,
  WorkEnvelope,
  Wrist,
)

__all__ = [
  "Axis",
  "ElbowOrientation",
  "MotionProfile",
  "OutOfRangeOfMotionError",
  "PreciseFlex",
  "PreciseFlexDriver",
  "PreciseFlexArm",
  "PreciseFlexCartesianPose",
  "PreciseFlexCollisionError",
  "PreciseFlexConfiguration",
  "PreciseFlexError",
  "PreciseFlexGripper",
  "PreciseFlexNotReadyError",
  "PreciseFlexPowerError",
  "PreciseFlexReachError",
  "PreciseFlexRail",
  "PreciseFlexServoError",
  "WorkEnvelope",
  "Wrist",
]
