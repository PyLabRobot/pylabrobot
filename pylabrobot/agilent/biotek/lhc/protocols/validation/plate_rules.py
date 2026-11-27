"""Which step types a plate allows.

The dispensers and the primes do not care what plate is loaded. The wash manifold steps need at
least 96 wells and cannot work a 1536-well plate, which has a wash of its own. The strip washer
steps need fewer than 1536 wells and a plate that is neither deep-well nor tubes. The peristaltic
wash steps work 96- and 384-well plates only.
"""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import Rejection

WRONG_PLATE = 24675
WASH_NEEDS_1536_WASH = 24627
STRIP_WRONG_PLATE = 24930
NEEDS_1536 = 24632

_DEEP_OR_TUBES = (
  PlateType.PLATE_96_MINI_TUBES,
  PlateType.PLATE_96_DEEP_WELL,
  PlateType.PLATE_384_DEEP_WELL,
)
_PLATE_INDEPENDENT = (
  StepType.PERI_DISPENSE,
  StepType.PERI_PRIME,
  StepType.PERI_PURGE,
  StepType.SYRINGE_DISPENSE,
  StepType.SYRINGE_PRIME,
  StepType.MANIFOLD_PRIME,
  StepType.MANIFOLD_AUTO_CLEAN,
  StepType.STRIP_PRIME,
  StepType.SHAKE_SOAK,
)
_STRIP_STEPS = (StepType.STRIP_WASH, StepType.STRIP_ASPIRATE, StepType.STRIP_DISPENSE)
_PERI_WASH_STEPS = (StepType.PERI_WASH_ASPIRATE, StepType.PERI_WASH_DISPENSE)


def check_plate(plate: PlateRecord, step_type: StepType) -> Rejection | None:
  """Check whether a plate allows a step type.

  Args:
    plate: The plate the protocol runs on.
    step_type: What the step does.

  Returns:
    A rejection, or None. A wash on a 1536-well plate is rejected with a code of its own, since
    that plate has a wash of its own.
  """
  wells = plate.wells
  if step_type in _PLATE_INDEPENDENT:
    return None
  if step_type is StepType.MANIFOLD_ASPIRATE:
    return None if wells >= 96 else Rejection(WRONG_PLATE)
  if step_type in (StepType.MANIFOLD_WASH, StepType.MANIFOLD_DISPENSE):
    if 96 <= wells < 1536:
      return None
    return Rejection(WASH_NEEDS_1536_WASH if wells == 1536 else WRONG_PLATE)
  if step_type in _STRIP_STEPS:
    if wells < 1536 and plate.plate_type not in _DEEP_OR_TUBES:
      return None
    return Rejection(STRIP_WRONG_PLATE)
  if step_type is StepType.WASH_1536:
    return None if wells == 1536 else Rejection(NEEDS_1536)
  if step_type in _PERI_WASH_STEPS:
    return None if wells in (96, 384) else Rejection(WRONG_PLATE)
  return Rejection(WRONG_PLATE)
