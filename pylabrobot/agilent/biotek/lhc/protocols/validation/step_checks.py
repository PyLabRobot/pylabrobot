"""What each step type checks about itself.

Every rule here is a pure function of the step, what the instrument has fitted and the plate. The
first check that fails is the answer. A step that a wash owns is checked with the flags that wash
pushes into it, which is why the wash rules build adjusted copies of their steps rather than
checking them as stored.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_manifold import SyringeManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.valve_box import ValveBox
from pylabrobot.agilent.biotek.lhc.enums.instrument.washer_manifold import WasherManifold
from pylabrobot.agilent.biotek.lhc.enums.steps.travel_rate import (
  STRIP_TRAVEL_RATES,
  WASHER_TRAVEL_RATES,
)
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_aspirate import ManifoldAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_auto_clean import (
  ManifoldAutoClean,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_dispense import ManifoldDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_prime import ManifoldPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_wash import ManifoldWash
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import PeriDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_prime import PeriPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_purge import PeriPurge
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_random_access_dispense import (
  PeriRandomAccessDispense,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_wash_aspirate import PeriWashAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_wash_dispense import PeriWashDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.shake_soak import ShakeSoak
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_aspirate import StripAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_dispense import StripDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_prime import StripPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_wash import StripWash
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_dispense import SyringeDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_prime import SyringePrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.wash_1536 import Wash1536
from pylabrobot.agilent.biotek.lhc.protocols.validation import checks
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import Rejection

_VACUUM_FILTRATION_ON_1536 = 24694
_CELL_WASHING_NEEDS_VACUUM_DELAY = 24726
_NOTHING_TO_DO = 24712
_MOVE_HOME_REQUIRED = 24711
_NO_SECTOR_SELECTED = 24725
_STRIP_OFFSET_X_RANGE = (-400, 400)
_STRIP_OFFSET_Y_RANGE = (-99, 99)
_STRIP_OFFSET_Z_RANGE = (1, 1500)
_WIDE_OFFSET_X_RANGE = (-400, 400)
_WIDE_OFFSET_Y_RANGE = (-99, 99)
_WIDE_VOLUME_MAXIMUM = 30000
_VOLUME_MAXIMUM = 3000


def _manifold_y_range(settings: InstrumentSettings) -> tuple[int, int]:
  """How far the wash manifold reaches along the plate.

  Args:
    settings: What the instrument has fitted.

  Returns:
    The range.
  """
  if settings.family is InstrumentFamily.MODEL_405_TS:
    return checks.OFFSET_Y_405TS_RANGE
  return checks.OFFSET_Y_OTHER_RANGE


def _manifold_z_range(settings: InstrumentSettings) -> tuple[int, int]:
  """How deep the wash manifold reaches.

  Args:
    settings: What the instrument has fitted.

  Returns:
    The range.
  """
  if settings.family is InstrumentFamily.MODEL_405_TS:
    return checks.OFFSET_Z_405TS_RANGE
  return checks.OFFSET_Z_OTHER_RANGE


def check_manifold_prime(
  step: ManifoldPrime, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a wash manifold prime.

  The buffer is only checked when no valve box can switch it, which is the same condition that
  makes a protocol commit to one buffer throughout.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Washer Prime "
  rejection = checks.volume(step.volume // 1000, 5, 999, prefix)
  switches_buffer = settings.valve_box in (ValveBox.WASHER, ValveBox.INTERNAL_4)
  if rejection is None and not switches_buffer:
    rejection = checks.buffer(step.buffer, prefix)
  rejection = rejection or checks.flow_rate(step.flow_rate, 3, 11, prefix)
  if rejection is not None:
    return rejection
  if step.prime_low_flow_path:
    rejection = checks.volume(step.low_flow_path_volume // 1000, 5, 999, "Low Flow Path ")
    if rejection is not None:
      return rejection
  if not step.submerge.enabled:
    return None
  return checks.long_duration(step.submerge.duration, False, "Submerge ")


def check_manifold_auto_clean(
  step: ManifoldAutoClean, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check an automatic clean.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "AutoClean "
  return checks.buffer(step.buffer, prefix) or checks.long_duration(step.duration, True, prefix)


def check_manifold_dispense(
  step: ManifoldDispense, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a wash manifold dispense.

  Three limits move with what is fitted: the smallest volume is larger through a 96-tube manifold;
  the two slowest flow rates need the cell washing module and a 96-tube dual-action manifold; and
  using one of those rates additionally requires the vacuum delay.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Washer Dispense "
  manifold = settings.washer_manifold
  tube_96 = manifold in (WasherManifold.TUBE_96_DUAL, WasherManifold.TUBE_96_SINGLE)
  minimum = 50 if tube_96 else 25
  switches_buffer = settings.valve_box in (ValveBox.WASHER, ValveBox.INTERNAL_4)
  cell_washing_rates = manifold is WasherManifold.TUBE_96_DUAL and settings.cell_washing

  if switches_buffer and step.check_buffer:
    rejection = checks.buffer(step.buffer, prefix)
    if rejection is not None:
      return rejection
  if not step.check_volume:
    return _check_manifold_pre_dispense(step, minimum)
  rejection = checks.volume(step.volume, minimum, _VOLUME_MAXIMUM, prefix)
  if rejection is not None:
    return rejection
  rejection = checks.flow_rate(step.flow_rate, 1 if cell_washing_rates else 3, 11, prefix)
  if rejection is not None:
    if manifold is not WasherManifold.TUBE_96_DUAL:
      rejection = Rejection(
        rejection.code,
        rejection.reason + "\r\nCell Wash Flow Rates are only available when \r\n"
        "the 96-tube dual-action manifold is installed.",
      )
    return rejection
  rejection = (
    checks.offset_x(step.positioning.x_steps, *checks.OFFSET_X_MANIFOLD_RANGE, prefix)
    or checks.offset_y(step.positioning.y_steps, *_manifold_y_range(settings), prefix)
    or checks.offset_z(step.positioning.z_steps, *_manifold_z_range(settings), prefix)
  )
  if rejection is not None:
    return rejection
  if step.flow_rate in (1, 2) and not step.vacuum.enabled:
    return Rejection(
      _CELL_WASHING_NEEDS_VACUUM_DELAY,
      prefix + "requires 'Delay start of Vacuum' option when using Cell Washing flow rates",
    )
  if step.vacuum.enabled:
    rejection = checks.volume(
      step.vacuum.volume, 0, _VOLUME_MAXIMUM, prefix + "'Delay start of Vacuum' "
    )
    if rejection is not None:
      return rejection
  return _check_manifold_pre_dispense(step, minimum)


def _check_manifold_pre_dispense(step: ManifoldDispense, minimum: int) -> Rejection | None:
  """Check the pre-dispense of a wash manifold dispense.

  Args:
    step: The step to check.
    minimum: The smallest volume this manifold dispenses.

  Returns:
    A rejection, or None.
  """
  if not step.pre_dispense.enabled:
    return None
  prefix = "Washer Pre-dispense "
  return checks.volume(
    step.pre_dispense.volume, minimum, _VOLUME_MAXIMUM, prefix
  ) or checks.flow_rate(step.pre_dispense.flow_rate, 3, 11, prefix)


def check_manifold_aspirate(
  step: ManifoldAspirate, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a wash manifold aspirate.

  Filtering under vacuum is a short path: it is not offered on a 1536-well plate, the delay is a
  time in seconds, and nothing else is checked because the tips do not move.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Washer Aspirate "
  if step.vacuum_filtration:
    if plate.wells == 1536:
      return Rejection(_VACUUM_FILTRATION_ON_1536)
    return checks.aspirate_delay(step.delay, True, prefix)
  rejection = (
    checks.aspirate_delay(step.delay, False, prefix)
    or checks.travel_rate(step.travel_rate, WASHER_TRAVEL_RATES, prefix)
    or checks.offset_x(step.positioning.x_steps, *checks.OFFSET_X_MANIFOLD_RANGE, prefix)
    or checks.offset_y(step.positioning.y_steps, *_manifold_y_range(settings), prefix)
    or checks.offset_z(step.positioning.z_steps, *_manifold_z_range(settings), prefix)
  )
  if rejection is not None:
    return rejection
  if plate.wells == 1536:
    return checks.column_selection(step.columns.values, prefix)
  if not step.secondary.enabled:
    return None
  prefix = "Secondary Aspirate "
  return (
    checks.offset_x(step.secondary.positioning.x_steps, *checks.OFFSET_X_MANIFOLD_RANGE, prefix)
    or checks.offset_y(step.secondary.positioning.y_steps, *_manifold_y_range(settings), prefix)
    or checks.offset_z(step.secondary.positioning.z_steps, *_manifold_z_range(settings), prefix)
  )


def check_syringe_prime(
  step: SyringePrime, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a syringe prime.

  The syringe is checked against a sixteen-tube manifold whatever is fitted, so a prime is never
  rejected for the manifold it will actually run on.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Syringe Prime "
  rejection = (
    checks.syringe(step.syringe, SyringeManifold.TUBE_16, prefix)
    or checks.flow_rate(step.flow_rate, 1, 5, prefix)
    or checks.prime_cycles(step.cycles)
    or checks.pump_delay(step.pump_delay, prefix)
    or checks.syringe_prime_volume(step.volume, step.flow_rate, prefix)
  )
  if rejection is not None or not step.submerge.enabled:
    return rejection
  return checks.long_duration(step.submerge.duration, False, "Submerge ")


def check_syringe_dispense(
  step: SyringeDispense, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a syringe dispense.

  The wider dispense offsets move three limits at once: the largest volume and both offset ranges.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Syringe Dispense "
  wide = settings.advanced_dispense_offsets
  maximum = _WIDE_VOLUME_MAXIMUM if wide else _VOLUME_MAXIMUM
  x_range = _WIDE_OFFSET_X_RANGE if wide else checks.OFFSET_X_RANGE
  y_range = _WIDE_OFFSET_Y_RANGE if wide else checks.OFFSET_Y_RANGE
  rejection = (
    checks.syringe(step.syringe, settings.syringe_manifold, prefix)
    or checks.flow_rate(step.flow_rate, 1, 5, prefix)
    or checks.syringe_volume(
      step.volume, step.flow_rate, settings.syringe_manifold, plate.wells, maximum, prefix
    )
    or checks.offset_x(step.positioning.x_steps, *x_range, prefix)
    or checks.offset_y(step.positioning.y_steps, *y_range, prefix)
    or checks.column_selection(step.columns.values, prefix)
    or checks.pump_delay(step.pump_delay, prefix)
    or checks.offset_z(step.positioning.z_steps, *checks.OFFSET_Z_RANGE, prefix)
  )
  if rejection is not None or not step.pre_dispense.enabled:
    return rejection
  prefix = "Syringe Pre-dispense "
  return checks.syringe_volume(
    step.pre_dispense.volume,
    step.flow_rate,
    settings.syringe_manifold,
    plate.wells,
    maximum,
    prefix,
  ) or checks.count(step.pre_dispense.count, prefix)


def check_peri_prime(
  step: PeriPrime, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a peristaltic prime or purge.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Peri-pump Purge " if isinstance(step, PeriPurge) else "Peri-pump Prime "
  rejection = (
    checks.cassette_type(step.cassette_type, settings.peri_wash_enabled)
    or checks.volume(step.volume, 1, _VOLUME_MAXIMUM, prefix)
    or checks.peri_flow_rate(step.flow_rate, prefix)
  )
  if rejection is not None or step.fixed_volume:
    return rejection
  return checks.duration(step.duration, prefix)


def check_peri_dispense(
  step: PeriDispense, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a peristaltic dispense.

  Two limits move with what is fitted: the largest volume, and how far the step may reach across
  the plate. Half-microlitre volumes are only offered when the step dispenses through one tube.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Peri-pump Dispense "
  random_access = isinstance(step, PeriRandomAccessDispense)
  chute_head = isinstance(step, PeriRandomAccessDispense) and (
    step.cassette_head == "8 tubes to 1 chute"
  )
  tubes = 8 if chute_head else 1
  half_allowed = tubes == 1 and settings.half_ul_enabled
  maximum = _WIDE_VOLUME_MAXIMUM if settings.advanced_dispense_offsets else _VOLUME_MAXIMUM
  x_range = (
    _WIDE_OFFSET_X_RANGE
    if settings.family is InstrumentFamily.MULTIFLO_FX
    else checks.OFFSET_X_RANGE
  )
  y_range = _WIDE_OFFSET_Y_RANGE if random_access else checks.OFFSET_Y_RANGE
  rejection = (
    checks.cassette_type(step.cassette_type)
    or checks.volume_or_half_microlitre(step.volume, 1, maximum, half_allowed, prefix)
    or checks.peri_flow_rate(step.flow_rate, prefix)
    or checks.offset_x(step.positioning.x_steps, *x_range, prefix)
    or checks.offset_y(step.positioning.y_steps, *y_range, prefix)
    or checks.offset_z(step.positioning.z_steps, *checks.OFFSET_Z_RANGE, prefix)
    or checks.column_selection(step.columns.values, prefix)
    or checks.row_selection(step.rows.values, plate.rows // 8, prefix)
  )
  if rejection is not None or not step.pre_dispense.enabled:
    return rejection
  prefix = "Peri-pump Pre-dispense "
  return checks.volume_or_half_microlitre(
    step.pre_dispense.volume, 1, maximum, settings.half_ul_enabled, prefix
  ) or checks.count(step.pre_dispense.count, prefix)


def check_shake_soak(
  step: ShakeSoak, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a shake or soak.

  A step that neither shakes, soaks nor moves the carrier home has nothing to do. Shaking and
  soaking for more than a minute together requires moving the carrier home afterwards.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  if not step.move_carrier_home and not step.shake.enabled and not step.soak.enabled:
    return Rejection(_NOTHING_TO_DO)
  total = 0
  if step.shake.enabled:
    rejection = checks.short_duration(step.shake.duration, "Shake ")
    if rejection is not None:
      return rejection
    total += step.shake.duration
  if step.soak.enabled:
    rejection = checks.short_duration(step.soak.duration, "Soak ")
    if rejection is not None:
      return rejection
    total += step.soak.duration
  if total > 60 and not step.move_carrier_home:
    return Rejection(
      _MOVE_HOME_REQUIRED,
      "'Move carrier home' is required if the total Shake/Soak durations exceed 1 minute",
    )
  return None


def check_manifold_wash(
  step: ManifoldWash, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a wash and each of the steps it will actually run.

  With a sector format selected the wash has to name a sector the plate has. The bottom wash is
  checked without a buffer of its own, and its volume only when the stage that runs it is on.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  rejection = checks.wash_cycles(step.cycles) or checks.wash_format(step.wash_format)
  if rejection is not None:
    return rejection
  if step.sectors.value == 0:
    return Rejection(_NO_SECTOR_SELECTED)
  bottom_wash = dataclasses.replace(
    step.bottom_wash, check_buffer=False, check_volume=step.stages.bottom_wash
  )
  rejection = (
    check_manifold_dispense(bottom_wash, settings, plate)
    or check_manifold_aspirate(step.aspirate, settings, plate)
    or check_manifold_dispense(step.dispense, settings, plate)
  )
  if rejection is not None:
    return rejection
  if step.stages.shake_soak_after_dispense:
    rejection = check_shake_soak(step.shake_soak, settings, plate)
    if rejection is not None:
      return rejection
  if step.stages.final_aspirate:
    return check_manifold_aspirate(step.final_aspirate, settings, plate)
  return None


def check_wash_1536(
  step: Wash1536, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a 1536-well wash and the steps it will actually run.

  The volume it pre-dispenses before washing is checked as a syringe volume against a 32-tube
  large-bore manifold and 1536 wells, which is the only geometry this wash runs on, at the flow
  rate of the syringe dispense it owns.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  if step.stages.pre_dispense_before:
    prefix = "Pre-dispense before washing "
    rejection = checks.syringe_volume(
      step.pre_dispense_before_volume,
      step.dispense.flow_rate,
      SyringeManifold.TUBE_32_LARGE_BORE,
      1536,
      _VOLUME_MAXIMUM,
      prefix,
    ) or checks.count(step.pre_dispense_before_count, prefix)
    if rejection is not None:
      return rejection
  rejection = (
    checks.wash_cycles(step.cycles)
    or checks.wash_format(step.wash_format)
    or check_manifold_aspirate(step.aspirate, settings, plate)
    or check_syringe_dispense(step.dispense, settings, plate)
  )
  if rejection is not None:
    return rejection
  if step.stages.shake_soak_after_dispense:
    rejection = check_shake_soak(step.shake_soak, settings, plate)
    if rejection is not None:
      return rejection
  if step.stages.final_aspirate:
    return check_manifold_aspirate(step.final_aspirate, settings, plate)
  return None


def check_strip_prime(
  step: StripPrime, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a strip washer prime.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Washer Prime "
  rejection = (
    checks.flow_rate(step.flow_rate, 1, 11, prefix)
    or checks.strip_prime_volume(
      step.volume, step.flow_rate, settings.strip_washer_manifold, prefix
    )
    or checks.prime_cycles(step.cycles)
  )
  if rejection is not None or not step.submerge.enabled:
    return rejection
  return checks.long_duration(step.submerge.duration, False, "Submerge ")


def check_strip_aspirate(
  step: StripAspirate, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a strip washer aspirate.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Washer Aspirate "
  rejection = (
    checks.aspirate_delay(step.delay, False, prefix)
    or checks.travel_rate(step.travel_rate, STRIP_TRAVEL_RATES, prefix)
    or checks.offset_x(step.positioning.x_steps, *_STRIP_OFFSET_X_RANGE, prefix)
    or checks.offset_y(step.positioning.y_steps, *_STRIP_OFFSET_Y_RANGE, prefix)
    or checks.offset_z(step.positioning.z_steps, *_STRIP_OFFSET_Z_RANGE, prefix)
  )
  if rejection is not None:
    return rejection
  if plate.wells == 1536:
    return checks.column_selection(step.columns.values, prefix)
  if not step.secondary.enabled:
    return None
  prefix = "Secondary Aspirate "
  return (
    checks.offset_x(step.secondary.positioning.x_steps, *_STRIP_OFFSET_X_RANGE, prefix)
    or checks.offset_y(step.secondary.positioning.y_steps, *_STRIP_OFFSET_Y_RANGE, prefix)
    or checks.offset_z(step.secondary.positioning.z_steps, *_STRIP_OFFSET_Z_RANGE, prefix)
  )


def check_strip_dispense(
  step: StripDispense, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a strip washer dispense.

  A step a wash owns skips the rate and volume checks unless it is the bottom wash or the
  between-cycles dispense, and has no selections of its own to check.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "Bottom Wash " if step.is_bottom_wash else "Wash Dispense "
  manifold = settings.strip_washer_manifold
  checked = (not step.in_wash) or step.is_cycle_dispense or step.is_bottom_wash
  if checked:
    rejection = (
      checks.flow_rate(step.flow_rate, 1, 11, prefix)
      or checks.strip_dispense_volume(step.volume, step.flow_rate, manifold, prefix)
      or checks.offset_x(step.positioning.x_steps, *_STRIP_OFFSET_X_RANGE, prefix)
      or checks.offset_y(step.positioning.y_steps, *_STRIP_OFFSET_Y_RANGE, prefix)
      or checks.offset_z(step.positioning.z_steps, *_STRIP_OFFSET_Z_RANGE, prefix)
    )
    if rejection is not None:
      return rejection
  if step.vacuum.enabled:
    rejection = checks.volume(
      step.vacuum.volume, 0, _WIDE_VOLUME_MAXIMUM, prefix + " 'Delay start of Vacuum'"
    )
    if rejection is not None:
      return rejection
  if not step.in_wash:
    rejection = checks.column_selection(step.columns.values, prefix) or checks.row_selection(
      step.rows.values, plate.rows // 8, prefix
    )
    if rejection is not None:
      return rejection
  if not (step.pre_dispense.enabled or step.force_pre_dispense):
    return None
  prefix = "Wash Pre-dispense "
  return (
    checks.flow_rate(step.pre_dispense.flow_rate, 1, 11, prefix)
    or checks.strip_dispense_volume(
      step.pre_dispense.volume, step.pre_dispense.flow_rate, manifold, prefix
    )
    or checks.count(step.pre_dispense.count, prefix)
  )


def check_strip_wash(
  step: StripWash, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a strip washer wash and the steps it will actually run.

  The two dispenses take their pre-dispense from different stage flags before anything is checked,
  which is what makes their pre-dispense rules fire.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  bottom_wash = dataclasses.replace(
    step.bottom_wash,
    is_cycle_dispense=False,
    is_bottom_wash=step.stages.bottom_wash,
    force_pre_dispense=False,
    pre_dispense=dataclasses.replace(
      step.bottom_wash.pre_dispense, enabled=step.stages.pre_dispense_before
    ),
  )
  dispense = dataclasses.replace(
    step.dispense,
    is_cycle_dispense=True,
    is_bottom_wash=False,
    force_pre_dispense=step.stages.pre_dispense_between,
    pre_dispense=dataclasses.replace(step.dispense.pre_dispense, enabled=False),
  )
  rejection = (
    checks.wash_cycles(step.cycles)
    or checks.wash_format(step.wash_format)
    or check_strip_dispense(bottom_wash, settings, plate)
    or check_strip_aspirate(step.aspirate, settings, plate)
    or check_strip_dispense(dispense, settings, plate)
  )
  if rejection is not None:
    return rejection
  if step.stages.shake_soak_after_dispense:
    rejection = check_shake_soak(step.shake_soak, settings, plate)
    if rejection is not None:
      return rejection
  if step.stages.final_aspirate:
    return check_strip_aspirate(step.final_aspirate, settings, plate)
  return None


def check_peri_wash_aspirate(
  step: PeriWashAspirate, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a peristaltic wash aspirate.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "PW-Aspirate "
  return (
    checks.volume(step.volume, 1, _WIDE_VOLUME_MAXIMUM, prefix)
    or checks.offset_y(step.positioning.y_steps, *_STRIP_OFFSET_Y_RANGE, prefix)
    or checks.offset_x(step.positioning.x_steps, *_STRIP_OFFSET_X_RANGE, prefix)
    or checks.row_selection(step.rows.values, plate.rows // 8, prefix)
    or checks.column_selection(step.columns.values, prefix)
    or checks.flow_rate(step.flow_rate, 0, 4, prefix)
    or checks.offset_z(step.positioning.z_steps, *_STRIP_OFFSET_Z_RANGE, prefix)
  )


def check_peri_wash_dispense(
  step: PeriWashDispense, settings: InstrumentSettings, plate: PlateRecord
) -> Rejection | None:
  """Check a peristaltic wash dispense.

  Both pre-dispense values are checked whether or not pre-dispensing is switched on, which no
  other dispense does.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.
  """
  prefix = "PW-Dispense "
  return (
    checks.volume(step.volume, 1, _WIDE_VOLUME_MAXIMUM, prefix)
    or checks.offset_y(step.positioning.y_steps, *_STRIP_OFFSET_Y_RANGE, prefix)
    or checks.offset_x(step.positioning.x_steps, *_STRIP_OFFSET_X_RANGE, prefix)
    or checks.volume(step.pre_dispense.volume, 25, _WIDE_VOLUME_MAXIMUM, "Pre-dispense ")
    or checks.count(step.pre_dispense.count, "Pre-dispense ")
    or checks.row_selection(step.rows.values, plate.rows // 8, prefix)
    or checks.column_selection(step.columns.values, prefix)
    or checks.flow_rate(step.flow_rate, 0, 7, prefix)
    or checks.offset_z(step.positioning.z_steps, *_STRIP_OFFSET_Z_RANGE, prefix)
  )


StepCheck = Callable[[Any, InstrumentSettings, PlateRecord], "Rejection | None"]
"""The rules for one kind of step. Each takes the step class it is registered for."""

CHECKS: dict[type[Step], StepCheck] = {
  ManifoldPrime: check_manifold_prime,
  ManifoldAutoClean: check_manifold_auto_clean,
  ManifoldDispense: check_manifold_dispense,
  ManifoldAspirate: check_manifold_aspirate,
  ManifoldWash: check_manifold_wash,
  SyringePrime: check_syringe_prime,
  SyringeDispense: check_syringe_dispense,
  PeriPrime: check_peri_prime,
  PeriPurge: check_peri_prime,
  PeriDispense: check_peri_dispense,
  PeriRandomAccessDispense: check_peri_dispense,
  ShakeSoak: check_shake_soak,
  Wash1536: check_wash_1536,
  StripPrime: check_strip_prime,
  StripAspirate: check_strip_aspirate,
  StripDispense: check_strip_dispense,
  StripWash: check_strip_wash,
  PeriWashAspirate: check_peri_wash_aspirate,
  PeriWashDispense: check_peri_wash_dispense,
}
"""Which rules apply to each step class."""


def check_step(step: Step, settings: InstrumentSettings, plate: PlateRecord) -> Rejection | None:
  """Check a step's own fields.

  Args:
    step: The step to check.
    settings: What the instrument has fitted.
    plate: The plate the protocol runs on.

  Returns:
    A rejection, or None.

  Raises:
    KeyError: If no rules are known for this kind of step.
  """
  return CHECKS[type(step)](step, settings, plate)
