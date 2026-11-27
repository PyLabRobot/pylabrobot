"""What a protocol claims of the peristaltic pumps.

One instrument, two pumps, one cassette in each. Checking a protocol accumulates what its steps
require -- which cassette in which pump, which dispense head, which pumps are used at all -- and
that record is both a rule (two steps may not want different cassettes in one pump) and an output:
opening a batch is what makes the hardware match it.

A conflict therefore shows up on the *second* step of a pair, not the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_head import CassetteHead
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_type import CassetteType
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_pump import PeriPump
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import PeriDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_prime import PeriPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_random_access_dispense import (
  PeriRandomAccessDispense,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_wash_aspirate import PeriWashAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_wash_dispense import PeriWashDispense
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import Rejection

CONFLICT = 24617
SINGLE_WELL_UNAVAILABLE = 24933
NO_SECOND_PUMP = 24676
HEAD_PLATE_MISMATCH = 24945

_PERI_STEP_TYPES = (StepType.PERI_DISPENSE, StepType.PERI_PRIME, StepType.PERI_PURGE)
_PERI_WASH_STEP_TYPES = (StepType.PERI_WASH_ASPIRATE, StepType.PERI_WASH_DISPENSE)
_PERI_WASH_CASSETTES = ("PeriWash aspirate", "PeriWash dispense")
_SINGLE_WELL_HEADS = ("1 tube to 1 well", "8 tubes to 1 chute")
_MULTI_TUBE_HEADS = ("8 tubes to 8 wells", "8 tubes to 1 well", "8 tubes to 1 chute")


@dataclass
class Reservations:
  """What the protocol checked so far requires of the pumps.

  Attributes:
    cassette_primary: The cassette the primary pump must hold, or None while nothing requires one.
    cassette_secondary: The same for the secondary pump.
    cassette_head: The dispense head the fitted cassette must have, or None while nothing requires
      one.
    uses_primary: Whether any step drives the primary pump.
    uses_secondary: Whether any step drives the secondary pump.
    single_well: Whether any step dispenses into single wells.
    dispense_reserved: Whether a peristaltic dispense got far enough to claim a cassette, which is
      what makes the head worth checking against the plate.
    peri_pumps: Which pumps ordinary peristaltic steps claim, primary first.
    peri_wash_pumps: Which pumps peristaltic wash steps claim, primary first.
  """

  cassette_primary: CassetteType | None = None
  cassette_secondary: CassetteType | None = None
  cassette_head: CassetteHead | None = None
  uses_primary: bool = False
  uses_secondary: bool = False
  single_well: bool = False
  dispense_reserved: bool = False
  peri_pumps: list[bool] = field(default_factory=lambda: [False, False])
  peri_wash_pumps: list[bool] = field(default_factory=lambda: [False, False])
  any_cassette_primary: bool = False
  any_cassette_secondary: bool = False


def reserve_cassette(
  step: PeriPrime | PeriDispense,
  settings: InstrumentSettings,
  reservations: Reservations,
  absent: frozenset[int],
) -> Rejection | None:
  """Claim a pump and a cassette for a peristaltic step.

  This is what stops a protocol asking for two different cassettes in one pump, for a pump that is
  not fitted, or for single-well dispensing an instrument cannot do.

  Args:
    step: The peristaltic step, which names a cassette and a pump.
    settings: What the instrument has fitted.
    reservations: What the protocol has claimed so far, updated in place.
    absent: Rules this model does not have.

  Returns:
    A rejection, or None.
  """
  cassette = step.cassette_type
  pump = step.peri_pump
  head, random_access = _random_access_of(step)

  single_well = random_access or head in _SINGLE_WELL_HEADS
  if single_well:
    reservations.single_well = True
    if SINGLE_WELL_UNAVAILABLE not in absent:
      if not settings.single_well_enabled:
        return Rejection(SINGLE_WELL_UNAVAILABLE)
      if settings.peri_pump_2 and pump == "Primary":
        return Rejection(SINGLE_WELL_UNAVAILABLE)
  if pump == "Primary":
    reservations.uses_primary = True
  if pump == "Secondary":
    if not settings.peri_pump_2:
      return Rejection(NO_SECOND_PUMP)
    reservations.uses_secondary = True

  if cassette != "Any":
    if pump == "Primary":
      if reservations.cassette_primary is None:
        reservations.cassette_primary = cassette
      elif reservations.cassette_primary != cassette:
        return Rejection(CONFLICT)
    if pump == "Secondary":
      if reservations.cassette_secondary is None:
        reservations.cassette_secondary = cassette
      elif reservations.cassette_secondary != cassette:
        return Rejection(CONFLICT)
    if head is None:
      if pump == "Primary":
        reservations.any_cassette_primary = True
      else:
        reservations.any_cassette_secondary = True
    elif reservations.cassette_head is None:
      reservations.cassette_head = head
    elif reservations.cassette_head != head:
      return Rejection(CONFLICT)
  elif not random_access:
    if pump == "Primary":
      if reservations.single_well and not settings.peri_pump_2:
        return Rejection(CONFLICT)
      reservations.any_cassette_primary = True
    if pump == "Secondary":
      if reservations.single_well:
        return Rejection(CONFLICT)
      reservations.any_cassette_secondary = True
  else:
    if pump == "Primary" and reservations.any_cassette_primary:
      return Rejection(CONFLICT)
    if pump == "Secondary" and reservations.any_cassette_secondary:
      return Rejection(CONFLICT)

  if not single_well:
    if pump != "Primary":
      if reservations.single_well:
        return Rejection(CONFLICT)
    elif reservations.single_well and not settings.peri_pump_2:
      return Rejection(CONFLICT)
  elif pump == "Primary":
    if reservations.any_cassette_primary:
      return Rejection(CONFLICT)
  elif reservations.any_cassette_secondary:
    return Rejection(CONFLICT)
  return None


def _random_access_of(step: PeriPrime | PeriDispense) -> tuple[CassetteHead | None, bool]:
  """Whether a step dispenses at random access, and through which head.

  A dispense says so by being a random-access dispense; a prime or purge says so with a flag, since
  it primes the same cassette without dispensing into wells.

  Args:
    step: The peristaltic step.

  Returns:
    The head the step names, and whether it is a random-access step.
  """
  if isinstance(step, PeriRandomAccessDispense):
    return step.cassette_head, True
  if isinstance(step, PeriPrime):
    return step.random_access.cassette_head, step.random_access.enabled
  return None, False


def uses_random_access(step: Step) -> bool:
  """Whether a step carries random-access fields at all.

  A model that predates random access cannot store them, and so cannot run such a step.

  Args:
    step: The step to ask.

  Returns:
    Whether it does.
  """
  if isinstance(step, PeriRandomAccessDispense):
    return True
  if isinstance(step, PeriPrime):
    return step.random_access.enabled
  return False


def check_cassette_head(
  step: PeriRandomAccessDispense, plate: PlateRecord, absent: frozenset[int]
) -> Rejection | None:
  """Check the dispense head a step names against the plate it would dispense into.

  Args:
    step: The random-access dispense.
    plate: The plate the protocol runs on.
    absent: Rules this model does not have.

  Returns:
    A rejection, or None.
  """
  return check_head_fits(step.cassette_head, plate, absent)


def check_head_fits(
  head: CassetteHead | None, plate: PlateRecord, absent: frozenset[int]
) -> Rejection | None:
  """Check a dispense head against the plate it would dispense into.

  A head that feeds several tubes at once cannot serve more than 24 wells, and a single-tube head
  cannot serve more than 384. Opening a batch checks the reserved head this way, having no step to
  hand.

  Args:
    head: The head to check, or None for the single-tube head that is used when none is named.
    plate: The plate the protocol runs on.
    absent: Rules this model does not have.

  Returns:
    A rejection, or None.
  """
  if HEAD_PLATE_MISMATCH in absent:
    return None
  fitted = head if head is not None else "1 tube to 1 well"
  if fitted in _MULTI_TUBE_HEADS and plate.wells > 24:
    return Rejection(HEAD_PLATE_MISMATCH)
  if fitted == "1 tube to 1 well" and plate.wells > 384:
    return Rejection(HEAD_PLATE_MISMATCH)
  return None


def claim_pump(step: Step, reservations: Reservations) -> None:
  """Record which pump a step wants, and for which kind of fluid.

  A peristaltic dispense always claims an ordinary pump. A prime or purge claims one too, once it
  names a cassette -- and if that cassette is a peristaltic wash one it is priming a wash manifold,
  so it claims the wash side instead. A peristaltic wash step always claims the wash side.

  Args:
    step: The step to record.
    reservations: What the protocol has claimed so far, updated in place.
  """
  claims = reservations.peri_pumps
  if step.step_type in _PERI_WASH_STEP_TYPES:
    claims = reservations.peri_wash_pumps
  elif step.step_type in (StepType.PERI_PRIME, StepType.PERI_PURGE):
    cassette = step.cassette_type if isinstance(step, (PeriPrime, PeriDispense)) else None
    if cassette in _PERI_WASH_CASSETTES:
      claims = reservations.peri_wash_pumps
    elif cassette == "Any":
      return
  elif step.step_type is not StepType.PERI_DISPENSE:
    return
  claims[1 if _pump_of(step) == "Secondary" else 0] = True


def _pump_of(step: Step) -> PeriPump | None:
  """Which pump a step drives.

  Args:
    step: The step to ask.

  Returns:
    The pump, or None when the step names none or drives no pump at all.
  """
  if isinstance(step, (PeriPrime, PeriDispense, PeriWashAspirate, PeriWashDispense)):
    return step.peri_pump
  return None


def check_pump_exclusivity(reservations: Reservations) -> Rejection | None:
  """Check that no pump serves both an ordinary peristaltic step and a peristaltic wash step.

  The two use different cassettes, so one pump cannot do both jobs in one protocol. Claims are
  recorded before a step is checked, so a step rejected for its own reasons has still claimed its
  pump.

  Args:
    reservations: What the protocol claimed.

  Returns:
    A rejection, or None.
  """
  if any(
    peri and peri_wash
    for peri, peri_wash in zip(reservations.peri_pumps, reservations.peri_wash_pumps)
  ):
    return Rejection(
      CONFLICT,
      "A P-Dispense step cannot use the same Peri-pump as any PW-step. "
      "They use mutually exclusive cassettes.",
    )
  return None
