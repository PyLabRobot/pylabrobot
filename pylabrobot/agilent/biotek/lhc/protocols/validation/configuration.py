"""Whether the instrument is configured for what a step asks of it.

These rules are about the instrument rather than the step: what a step type needs to be fitted,
which plates the fitted manifolds can work, and the two commitments a protocol makes to itself --
one buffer inlet throughout unless a valve box can switch it, and one bottle per syringe.
"""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.strip_washer_manifold import StripWasherManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_type import SyringeBoxType
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_manifold import SyringeManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.valve_box import ValveBox
from pylabrobot.agilent.biotek.lhc.enums.instrument.washer_manifold import WasherManifold
from pylabrobot.agilent.biotek.lhc.enums.motion.carrier_type import CarrierType
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.enums.steps.buffer import Buffer
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe_bottle import SyringeBottle
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_aspirate import ManifoldAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_auto_clean import (
  ManifoldAutoClean,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_dispense import ManifoldDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_prime import ManifoldPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_wash import ManifoldWash
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_dispense import SyringeDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_prime import SyringePrime
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import Rejection

COMMITMENT_CONFLICT = 24614
SYRINGE_PLATE_16_TUBE = 24628
SYRINGE_PLATE_32_TUBE = 24629
SYRINGE_PLATE_8_TUBE = 24584
SYRINGE_PLATE_6_WELL = 24935
SYRINGE_PLATE_12_WELL = 24936
SYRINGE_PLATE_24_WELL = 24937
SYRINGE_PLATE_48_WELL = 24944
NO_SYRINGE = 24615
WASHER_PLATE_384_128_TUBE = 24631
WASHER_PLATE_384_96_SINGLE = 24585
WASHER_PLATE_1536 = 24630
WASHER_PLATE = 24625
NO_VACUUM_FILTRATION = 24616
NO_PERI_PUMP = 24729
NO_ULTRASONIC = 24678
NO_STRIP_WASHER = 24928
STRIP_WASHER_PLATE = 24929
NO_PERI_WASH = 24706

_NEEDS_SYRINGE_PLATE = (StepType.SYRINGE_DISPENSE, StepType.WASH_1536)
_NEEDS_WASHER_PLATE = (
  StepType.MANIFOLD_WASH,
  StepType.MANIFOLD_DISPENSE,
  StepType.MANIFOLD_ASPIRATE,
  StepType.WASH_1536,
)
_NEEDS_BUFFER = (
  StepType.MANIFOLD_WASH,
  StepType.MANIFOLD_DISPENSE,
  StepType.MANIFOLD_AUTO_CLEAN,
  StepType.MANIFOLD_PRIME,
)
_NEEDS_BOTTLE = (StepType.SYRINGE_PRIME, StepType.SYRINGE_DISPENSE)
_NEEDS_VACUUM = (StepType.MANIFOLD_WASH, StepType.MANIFOLD_ASPIRATE)
_NEEDS_PERI_PUMP = (StepType.PERI_DISPENSE, StepType.PERI_PRIME, StepType.PERI_PURGE)
_NEEDS_SYRINGE = (StepType.SYRINGE_PRIME, StepType.SYRINGE_DISPENSE)
_NEEDS_STRIP_PLATE = (StepType.STRIP_WASH, StepType.STRIP_ASPIRATE, StepType.STRIP_DISPENSE)
_NEEDS_PERI_WASH = (StepType.PERI_WASH_ASPIRATE, StepType.PERI_WASH_DISPENSE)

_STRIP_WASHER_PLATES: dict[StripWasherManifold, tuple[int, ...]] = {
  StripWasherManifold.PLATE_6_WELL: (6,),
  StripWasherManifold.PLATE_12_WELL: (12,),
  StripWasherManifold.PLATE_24_WELL: (24,),
  StripWasherManifold.PLATE_48_WELL: (48,),
  StripWasherManifold.PLATE_96_WELL: (96, 384),
}

_A_BOTTLES = ("A1", "A2")
_B_BOTTLES = ("B1", "B2")
_INCOMPATIBLE: tuple[tuple[SyringeBottle, tuple[SyringeBottle, ...]], ...] = (
  ("A1", ("A2B1", "A2B2")),
  ("A2", ("A1B1", "A1B2")),
  ("B1", ("A1B2", "A2B2")),
  ("B2", ("A1B1", "A2B1")),
)


@dataclass
class Commitments:
  """What a protocol has committed itself to as it is checked.

  Attributes:
    buffer: The buffer inlet the protocol draws from, once a step has named one.
    bottle_a: The bottle syringe A draws from, once a step has named one.
    bottle_b: The same for syringe B.
    bottle_both: The pairing used when both syringes run together.
  """

  buffer: Buffer | None = None
  bottle_a: SyringeBottle | None = None
  bottle_b: SyringeBottle | None = None
  bottle_both: SyringeBottle | None = None


def available_step_types(settings: InstrumentSettings) -> list[StepType]:
  """Which step types the fitted hardware can carry out at all.

  This is the hardware half of what a device offers: a step type whose hardware is not fitted can
  never run, whatever plate is on the carrier or which firmware is installed. Everything that
  depends on the plate stays with the checks above, and the firmware restriction belongs to the
  model, so a device narrows this by its own palette and its own firmware variant.

  Args:
    settings: What the instrument has fitted.

  Returns:
    The step types, in the order they are numbered.
  """
  washes = settings.washer_manifold is not WasherManifold.NOT_INSTALLED
  syringes = (
    settings.syringe_box is not SyringeBoxType.NOT_INSTALLED
    and settings.syringe_manifold is not SyringeManifold.NOT_INSTALLED
  )
  strips = settings.strip_washer_manifold is not StripWasherManifold.NOT_INSTALLED
  fitted: dict[StepType, bool] = {
    StepType.PERI_DISPENSE: settings.peri_pump,
    StepType.PERI_PRIME: settings.peri_pump,
    StepType.PERI_PURGE: settings.peri_pump,
    StepType.SYRINGE_DISPENSE: syringes,
    StepType.SYRINGE_PRIME: syringes,
    StepType.MANIFOLD_WASH: washes,
    StepType.MANIFOLD_ASPIRATE: washes,
    StepType.MANIFOLD_DISPENSE: washes,
    StepType.MANIFOLD_PRIME: washes,
    StepType.MANIFOLD_AUTO_CLEAN: washes and settings.ultrasonic,
    StepType.SHAKE_SOAK: True,
    StepType.WASH_1536: washes and syringes,
    StepType.STRIP_WASH: strips,
    StepType.STRIP_ASPIRATE: strips,
    StepType.STRIP_DISPENSE: strips,
    StepType.STRIP_PRIME: strips,
    StepType.PERI_WASH_ASPIRATE: settings.peri_wash_enabled,
    StepType.PERI_WASH_DISPENSE: settings.peri_wash_enabled,
  }
  return [step_type for step_type, available in fitted.items() if available]


def check_configuration(
  step: Step,
  settings: InstrumentSettings,
  plate: PlateRecord,
  commitments: Commitments,
  absent: frozenset[int],
  carrier_type: CarrierType | None = None,
) -> Rejection | None:
  """Check what a step needs from the instrument's configuration.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.
    commitments: What the protocol has committed to so far, updated in place.
    absent: Rules this model does not have.
    carrier_type: The fitted carrier, when the device has read it. Vacuum filtration is only
      confirmed when it has.

  Returns:
    A rejection, or None.
  """
  step_type = step.step_type
  wells = plate.wells

  if step_type in _NEEDS_SYRINGE_PLATE:
    rejection = _check_syringe_plate(settings.syringe_manifold, plate, absent)
    if rejection is not None:
      return rejection

  # A model without a wash manifold is not asked to have one.
  if settings.family is not InstrumentFamily.MULTIFLO_FX and step_type in _NEEDS_WASHER_PLATE:
    rejection = _check_washer_plate(settings.washer_manifold, wells)
    if rejection is not None:
      return rejection

  if step_type in _NEEDS_BUFFER and settings.valve_box in (
    ValveBox.SYRINGE,
    ValveBox.NOT_INSTALLED,
  ):
    buffer = _buffer_of(step)
    if buffer is not None:
      if commitments.buffer is None:
        commitments.buffer = buffer
      elif commitments.buffer != buffer:
        return Rejection(COMMITMENT_CONFLICT)

  if step_type in _NEEDS_BOTTLE and settings.valve_box is not ValveBox.SYRINGE:
    rejection = _check_bottle(step, commitments)
    if rejection is not None:
      return rejection

  if step_type in _NEEDS_VACUUM and _wants_vacuum(step):
    if not settings.vacuum_filtration:
      return Rejection(NO_VACUUM_FILTRATION)
    if carrier_type is not None and carrier_type is not CarrierType.VACUUM_FILTRATION:
      return Rejection(NO_VACUUM_FILTRATION)

  if step_type in _NEEDS_PERI_PUMP and not settings.peri_pump:
    return Rejection(NO_PERI_PUMP)
  if step_type is StepType.MANIFOLD_AUTO_CLEAN and not settings.ultrasonic:
    return Rejection(NO_ULTRASONIC)
  if step_type in _NEEDS_SYRINGE and (
    settings.syringe_box is SyringeBoxType.NOT_INSTALLED
    or settings.syringe_manifold is SyringeManifold.NOT_INSTALLED
  ):
    return Rejection(NO_SYRINGE)
  if (
    step_type is StepType.STRIP_PRIME
    and NO_STRIP_WASHER not in absent
    and settings.strip_washer_manifold is StripWasherManifold.NOT_INSTALLED
  ):
    return Rejection(NO_STRIP_WASHER)
  if step_type in _NEEDS_STRIP_PLATE and STRIP_WASHER_PLATE not in absent:
    manifold = settings.strip_washer_manifold
    required = _STRIP_WASHER_PLATES.get(manifold)
    if required is None:
      if manifold is StripWasherManifold.NOT_INSTALLED:
        return Rejection(NO_STRIP_WASHER)
      return Rejection(STRIP_WASHER_PLATE)
    if wells not in required:
      return Rejection(STRIP_WASHER_PLATE)
  if step_type in _NEEDS_PERI_WASH and not settings.peri_wash_enabled:
    return Rejection(NO_PERI_WASH)
  return None


def _check_syringe_plate(
  manifold: SyringeManifold, plate: PlateRecord, absent: frozenset[int]
) -> Rejection | None:
  """Check the plate against the fitted syringe manifold.

  Args:
    manifold: The fitted syringe manifold.
    plate: The plate the protocol runs on.
    absent: Rules this model does not have.

  Returns:
    A rejection, or None.
  """
  wells = plate.wells
  if manifold in (SyringeManifold.TUBE_16, SyringeManifold.TUBE_16_7):
    if wells not in (96, 384) or plate.plate_type is PlateType.PLATE_96_HALF_WELL:
      return Rejection(SYRINGE_PLATE_16_TUBE)
  elif manifold in (SyringeManifold.TUBE_32_LARGE_BORE, SyringeManifold.TUBE_32_SMALL_BORE):
    if wells != 1536:
      return Rejection(SYRINGE_PLATE_32_TUBE)
  elif manifold is SyringeManifold.TUBE_8:
    if wells not in (96, 384):
      return Rejection(SYRINGE_PLATE_8_TUBE)
  elif manifold is SyringeManifold.PLATE_6_WELL and wells != 6:
    if SYRINGE_PLATE_6_WELL not in absent:
      return Rejection(SYRINGE_PLATE_6_WELL)
  elif manifold is SyringeManifold.PLATE_12_WELL and wells != 12:
    if SYRINGE_PLATE_12_WELL not in absent:
      return Rejection(SYRINGE_PLATE_12_WELL)
  elif manifold is SyringeManifold.PLATE_24_WELL and wells != 24:
    if SYRINGE_PLATE_24_WELL not in absent:
      return Rejection(SYRINGE_PLATE_24_WELL)
  elif manifold is SyringeManifold.PLATE_48_WELL and wells != 48:
    if SYRINGE_PLATE_48_WELL not in absent:
      return Rejection(SYRINGE_PLATE_48_WELL)
  elif manifold is SyringeManifold.NOT_INSTALLED:
    return Rejection(NO_SYRINGE)
  return None


def _check_washer_plate(manifold: WasherManifold, wells: int) -> Rejection | None:
  """Check the plate against the fitted wash manifold.

  Args:
    manifold: The fitted wash manifold.
    wells: How many wells the plate has.

  Returns:
    A rejection, or None.
  """
  if wells == 384:
    if manifold is WasherManifold.TUBE_128:
      return Rejection(WASHER_PLATE_384_128_TUBE)
    if manifold is WasherManifold.TUBE_96_SINGLE:
      return Rejection(WASHER_PLATE_384_96_SINGLE)
  elif wells == 1536:
    if manifold is not WasherManifold.TUBE_128:
      return Rejection(WASHER_PLATE_1536)
  elif manifold not in (WasherManifold.TUBE_96_DUAL, WasherManifold.TUBE_96_SINGLE):
    return Rejection(WASHER_PLATE)
  return None


def _check_bottle(step: Step, commitments: Commitments) -> Rejection | None:
  """Check which bottle a syringe step draws from against what the protocol has committed to.

  A step that runs both syringes names a pairing, which has to agree with the bottle each side has
  already been committed to.

  Args:
    step: The syringe step.
    commitments: What the protocol has committed to so far, updated in place.

  Returns:
    A rejection, or None.
  """
  if not isinstance(step, (SyringePrime, SyringeDispense)):
    return None
  bottle = step.syringe_bottle
  if bottle in _A_BOTTLES:
    if commitments.bottle_a is None:
      commitments.bottle_a = bottle
    elif commitments.bottle_a != bottle:
      return Rejection(COMMITMENT_CONFLICT)
  elif bottle in _B_BOTTLES:
    if commitments.bottle_b is None:
      commitments.bottle_b = bottle
    elif commitments.bottle_b != bottle:
      return Rejection(COMMITMENT_CONFLICT)
  else:
    if commitments.bottle_both is None:
      commitments.bottle_both = bottle
    elif commitments.bottle_both != bottle:
      return Rejection(COMMITMENT_CONFLICT)
  for chosen, incompatible in _INCOMPATIBLE:
    side = commitments.bottle_a if chosen in _A_BOTTLES else commitments.bottle_b
    if side == chosen and commitments.bottle_both in incompatible:
      return Rejection(COMMITMENT_CONFLICT)
  return None


def _buffer_of(step: Step) -> Buffer | None:
  """Which buffer inlet a step draws from.

  Args:
    step: The step to ask.

  Returns:
    The inlet, or None when the step names none. A wash draws through the dispense it owns.
  """
  if isinstance(step, (ManifoldPrime, ManifoldDispense, ManifoldAutoClean)):
    return step.buffer
  if isinstance(step, ManifoldWash):
    return step.dispense.buffer
  return None


def _wants_vacuum(step: Step) -> bool:
  """Whether a step filters under vacuum.

  A wash filters when the aspirate it always runs does, or when the final aspirate does and the
  stage that runs it is on.

  Args:
    step: The step to ask.

  Returns:
    Whether it needs the vacuum.
  """
  if isinstance(step, ManifoldAspirate):
    return step.vacuum_filtration
  if isinstance(step, ManifoldWash):
    return step.aspirate.vacuum_filtration or (
      step.stages.final_aspirate and step.final_aspirate.vacuum_filtration
    )
  return False
