"""Which validation rules each instrument model runs.

The models do not check a protocol identically: newer firmware adds rules that older firmware does
not have, and one model has rules of its own. What is common to all of them lives in
``protocols.validation``; what differs is here, because it is a per-model fact, and validation
reads it rather than owning it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.basecode import Basecode
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType


@dataclass(frozen=True)
class BuildRules:
  """What a model checks over and under the common rule set.

  Attributes:
    basecode_step_types: Whether the firmware variant limits which step types may run.
    peri_pump_exclusivity: Whether an ordinary peristaltic step and a peristaltic wash step are
      forbidden from sharing a pump.
    absent_checks: Rules this model does not have, named by the code they would report. Each is
      skipped, and validation carries on to the next rule.
  """

  basecode_step_types: bool = False
  peri_pump_exclusivity: bool = False
  absent_checks: frozenset[int] = field(default_factory=frozenset)


COMMON = BuildRules()
"""The rules every model runs."""

MULTIFLO = BuildRules(
  absent_checks=frozenset(
    {24834, 24928, 24929, 24930, 24933, 24934, 24935, 24936, 24937, 24944, 24945}
  )
)
"""The oldest firmware, which predates three features and so lacks eleven rules.

They cover single-well dispensing, the strip washer, the small-plate syringe manifolds and the
mini-tube carrier. Most are unreachable on that model anyway, since it offers neither strip steps
nor random access; the single-well one is not, and an ordinary peristaltic dispense reaches it.
"""

MULTIFLO_FX = BuildRules(basecode_step_types=True, peri_pump_exclusivity=True)
"""The newest firmware, which adds two rules of its own."""

_BY_FAMILY: dict[InstrumentFamily, BuildRules] = {
  InstrumentFamily.MULTIFLO: MULTIFLO,
  InstrumentFamily.MULTIFLO_FX: MULTIFLO_FX,
}

_PERISTALTIC = frozenset(
  {StepType.PERI_DISPENSE, StepType.PERI_PRIME, StepType.PERI_PURGE, StepType.SHAKE_SOAK}
)
_STRIP = frozenset(
  {StepType.STRIP_WASH, StepType.STRIP_ASPIRATE, StepType.STRIP_DISPENSE, StepType.STRIP_PRIME}
)
_SYRINGE = frozenset({StepType.SYRINGE_DISPENSE, StepType.SYRINGE_PRIME})
_PERI_WASH = frozenset({StepType.PERI_WASH_ASPIRATE, StepType.PERI_WASH_DISPENSE})

BASECODE_STEP_TYPES: dict[Basecode, frozenset[StepType]] = {
  Basecode.BASIC: _PERISTALTIC | _STRIP | _SYRINGE,
  Basecode.RANDOM_ACCESS: _PERISTALTIC | _SYRINGE,
  Basecode.PERI_WASH: _PERISTALTIC | _PERI_WASH,
}
"""Which step types each firmware variant offers.

The peristaltic steps and the pause run on every variant and the plate-washer steps on none, since
a model with a firmware variant has a strip washer rather than a plate washer. A step type that is
not listed cannot run.
"""


def rules_for(family: InstrumentFamily) -> BuildRules:
  """The rules a model runs.

  Args:
    family: Which instrument model this is.

  Returns:
    Its rules, or the common set for a model whose firmware has not been examined.
  """
  return _BY_FAMILY.get(family, COMMON)


def basecode_for(settings: InstrumentSettings) -> Basecode:
  """Which firmware variant an instrument is running.

  This follows from what the instrument reports as fitted rather than being asked for directly.
  Note the order: an instrument reporting both single-well dispensing and peristaltic washing is
  taken to be running the random-access variant.

  Args:
    settings: What the instrument has fitted.

  Returns:
    The variant.
  """
  if settings.single_well_enabled:
    return Basecode.RANDOM_ACCESS
  if settings.peri_wash_enabled:
    return Basecode.PERI_WASH
  return Basecode.BASIC
