"""Checking a whole protocol in one pass.

The pass answers two things at once. The report says whether the protocol can run and why not; the
reservations say what it requires of the peristaltic pumps, which is what opening a batch uses to
make the hardware match. Skip the pass and the reservations are empty, so the batch opens against
whatever cassette happens to be fitted -- which is why running a protocol validates it first.

Nothing here talks to the instrument. The device gathers the facts -- what is fitted, which plate,
which rules its firmware runs, and, when it has asked, which plates it accepts and which carrier is
on it -- and passes them in, so every rule is testable without hardware.
"""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.devices.build_rules import (
  BASECODE_STEP_TYPES,
  BuildRules,
  basecode_for,
)
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.basecode import Basecode
from pylabrobot.agilent.biotek.lhc.enums.motion.carrier_type import CarrierType
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_restriction import PlateRestriction
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import PeriDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_prime import PeriPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_random_access_dispense import (
  PeriRandomAccessDispense,
)
from pylabrobot.agilent.biotek.lhc.protocols.validation import configuration, plate_rules
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import (
  Rejection,
  StepReport,
  ValidationReport,
)
from pylabrobot.agilent.biotek.lhc.protocols.validation.reservations import (
  Reservations,
  check_cassette_head,
  check_pump_exclusivity,
  claim_pump,
  reserve_cassette,
  uses_random_access,
)
from pylabrobot.agilent.biotek.lhc.protocols.validation.step_checks import check_step

ONLY_96_WELL = 24582
ONLY_1536_WELL = 24583
WRONG_CARRIER = 24834
STEP_NOT_IN_FIRMWARE = 24707

_PERI_STEPS = (StepType.PERI_DISPENSE, StepType.PERI_PRIME, StepType.PERI_PURGE)


def validate(
  steps: list[Step],
  settings: InstrumentSettings,
  plate: PlateRecord,
  rules: BuildRules,
  plate_restriction: PlateRestriction | None = None,
  carrier_type: CarrierType | None = None,
) -> tuple[ValidationReport, Reservations]:
  """Check a protocol.

  Every step is checked, so the report lists all the failures rather than stopping at the first.
  What the protocol claims of the pumps accumulates across the steps, which means a conflict
  between two steps is reported against the second of them.

  Args:
    steps: The steps to check, in the order they will run.
    settings: What the instrument has fitted. These should be what the instrument reports rather
      than what a protocol file declares.
    plate: The plate the protocol runs on.
    rules: Which rules this model's firmware runs.
    plate_restriction: Which plates the instrument accepts, when the device has asked it.
    carrier_type: Which carrier is fitted, when the device has asked it.

  Returns:
    The report, and what the protocol requires of the pumps.
  """
  report = ValidationReport()
  reservations = Reservations()
  commitments = configuration.Commitments()
  absent = rules.absent_checks
  basecode = basecode_for(settings)

  rejection = _check_instrument(plate, plate_restriction, carrier_type, absent)
  if rejection is not None:
    report.rejection = rejection
    return report, reservations

  for number, step in enumerate(steps, start=1):
    report.steps.append(
      StepReport(
        number=number,
        step_type=step.step_type,
        rejection=_check_one(
          step, settings, plate, rules, basecode, reservations, commitments, carrier_type
        ),
      )
    )
  return report, reservations


def _check_instrument(
  plate: PlateRecord,
  plate_restriction: PlateRestriction | None,
  carrier_type: CarrierType | None,
  absent: frozenset[int],
) -> Rejection | None:
  """Check the plate against what the instrument itself will accept.

  This belongs to no single step: it is about the instrument and the plate, so it stops the whole
  protocol rather than one of its steps.

  Args:
    plate: The plate the protocol runs on.
    plate_restriction: Which plates the instrument accepts, when the device has asked it.
    carrier_type: Which carrier is fitted, when the device has asked it.
    absent: Rules this model does not have.

  Returns:
    A rejection, or None.
  """
  if plate_restriction is PlateRestriction.ALLOW_96_WELL_ONLY and plate.wells != 96:
    return Rejection(ONLY_96_WELL)
  if plate_restriction is PlateRestriction.ALLOW_1536_WELL_ONLY and plate.wells != 1536:
    return Rejection(ONLY_1536_WELL)
  if carrier_type is not None and WRONG_CARRIER not in absent:
    mini_tubes = plate.plate_type is PlateType.PLATE_96_MINI_TUBES
    if (carrier_type is CarrierType.MINI_TUBE) != mini_tubes:
      return Rejection(WRONG_CARRIER)
  return None


def _check_one(
  step: Step,
  settings: InstrumentSettings,
  plate: PlateRecord,
  rules: BuildRules,
  basecode: Basecode,
  reservations: Reservations,
  commitments: configuration.Commitments,
  carrier_type: CarrierType | None,
) -> Rejection | None:
  """Check one step, in the order the instrument checks it.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.
    rules: Which rules this model's firmware runs.
    basecode: Which firmware variant the instrument is running.
    reservations: What the protocol has claimed so far, updated in place.
    commitments: What the protocol has committed to so far, updated in place.
    carrier_type: Which carrier is fitted, when the device has asked it.

  Returns:
    A rejection, or None.
  """
  absent = rules.absent_checks
  rejection = plate_rules.check_plate(plate, step.step_type)
  if rejection is not None and rejection.code not in absent:
    return rejection
  if rules.basecode_step_types and step.step_type not in BASECODE_STEP_TYPES[basecode]:
    return Rejection(STEP_NOT_IN_FIRMWARE)
  if uses_random_access(step) and not settings.supports_random_access_tail:
    # This model cannot store the random-access fields, so it cannot be told to use them. The
    # alternative would be running the step as an ordinary one, which is a different operation.
    return Rejection(STEP_NOT_IN_FIRMWARE)
  if rules.peri_pump_exclusivity:
    claim_pump(step, reservations)
  rejection = check_step(step, settings, plate)
  if rejection is not None:
    return rejection
  if step.step_type in _PERI_STEPS and isinstance(step, (PeriPrime, PeriDispense)):
    rejection = reserve_cassette(step, settings, reservations, absent)
    if rejection is not None:
      return rejection
    if step.step_type is StepType.PERI_DISPENSE:
      reservations.dispense_reserved = True
  if isinstance(step, PeriRandomAccessDispense):
    rejection = check_cassette_head(step, plate, absent)
    if rejection is not None:
      return rejection
  rejection = configuration.check_configuration(
    step, settings, plate, commitments, absent, carrier_type
  )
  if rejection is not None or not rules.peri_pump_exclusivity:
    return rejection
  return check_pump_exclusivity(reservations)
