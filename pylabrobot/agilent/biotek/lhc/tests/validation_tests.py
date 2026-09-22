"""Whether a protocol can run, and why not.

Every rule here is a pure function of the step, the plate, what is fitted and what the firmware
knows, so none of it needs an instrument. What the pass accumulates across steps is tested too: the
cassette a pump is pinned to is the one output besides the report, and opening a batch is what acts
on it.
"""

from __future__ import annotations

import pytest

from pylabrobot.agilent.biotek.lhc.devices.build_rules import COMMON, MULTIFLO_FX, rules_for
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_type import SyringeBoxType
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_manifold import SyringeManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.valve_box import ValveBox
from pylabrobot.agilent.biotek.lhc.enums.instrument.washer_manifold import WasherManifold
from pylabrobot.agilent.biotek.lhc.enums.motion.carrier_type import CarrierType
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_restriction import PlateRestriction
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.plate_geometry.plates import find
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_auto_clean import (
  ManifoldAutoClean,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_dispense import ManifoldDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_prime import ManifoldPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_wash import ManifoldWash
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import PeriDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_prime import PeriPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_prime import StripPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_prime import SyringePrime
from pylabrobot.agilent.biotek.lhc.protocols.validation import plate_rules
from pylabrobot.agilent.biotek.lhc.protocols.validation.configuration import available_step_types
from pylabrobot.agilent.biotek.lhc.protocols.validation.protocol_pass import validate
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import ValidationReport
from pylabrobot.agilent.biotek.lhc.protocols.validation.reservations import Reservations

EL406 = InstrumentFamily.EL406


def offered(plate_type: PlateType) -> PlateRecord:
  """The record this model works a format by.

  Args:
    plate_type: The format to look up.

  Returns:
    The record.

  Raises:
    AssertionError: If the model does not offer that format, which would make the test meaningless.
  """
  record = find(plate_type, EL406)
  assert record is not None, plate_type.name
  return record


PLATE_96 = offered(PlateType.PLATE_96_WELL)
PLATE_384 = offered(PlateType.PLATE_384_WELL)
PLATE_1536 = offered(PlateType.PLATE_1536_WELL)


def check(
  steps: list[Step],
  settings: InstrumentSettings | None = None,
  plate: PlateRecord = PLATE_96,
  plate_restriction: PlateRestriction | None = None,
  carrier_type: CarrierType | None = None,
) -> tuple[ValidationReport, Reservations]:
  """Run the pass over some steps.

  Args:
    steps: The steps to check.
    settings: What the instrument has fitted, defaulting to a fully equipped one.
    plate: The plate on the carrier.
    plate_restriction: Which plates the instrument accepts, when it has been asked.
    carrier_type: Which carrier is fitted, when it has been asked.

  Returns:
    The report, and what the protocol requires of the pumps.
  """
  return validate(
    steps=steps,
    settings=settings if settings is not None else InstrumentSettings(family=EL406),
    plate=plate,
    rules=rules_for(EL406),
    plate_restriction=plate_restriction,
    carrier_type=carrier_type,
  )


class TestTheReport:
  """What the pass answers with."""

  def test_a_protocol_that_can_run_is_truthy(self):
    """A protocol that can run is truthy."""
    report, _ = check([ManifoldPrime(volume=40_000)])
    assert report
    assert report.failures == []

  def test_every_step_is_checked_rather_than_stopping_at_the_first(self):
    """A report that stopped early would hide the second reason a protocol will not run."""
    settings = InstrumentSettings(family=EL406, ultrasonic=False, vacuum_filtration=False)
    report, _ = check([ManifoldAutoClean(), ManifoldAutoClean()], settings)
    assert not report
    assert len(report.steps) == 2
    assert len(report.failures) == 2

  def test_a_report_prints_only_what_cannot_run(self):
    """A report prints only what cannot run."""
    settings = InstrumentSettings(family=EL406, ultrasonic=False)
    report, _ = check([ManifoldPrime(volume=40_000), ManifoldAutoClean()], settings)
    printed = str(report)
    assert "1 of 2 steps" in printed
    assert "MANIFOLD_AUTO_CLEAN" in printed

  def test_a_step_carries_its_number_and_its_reason(self):
    """A step carries its number and its reason."""
    settings = InstrumentSettings(family=EL406, ultrasonic=False)
    report, _ = check([ManifoldPrime(volume=40_000), ManifoldAutoClean()], settings)
    failure = report.failures[0]
    assert failure.number == 2
    assert failure.step_type is StepType.MANIFOLD_AUTO_CLEAN
    assert failure.rejection is not None
    assert failure.rejection.code != 0


class TestThePlateRule:
  """Which step types a plate allows, which is the first thing asked about a step."""

  @pytest.mark.parametrize(
    "step_type, wells, allowed",
    [
      (StepType.MANIFOLD_WASH, 96, True),
      (StepType.MANIFOLD_WASH, 384, True),
      (StepType.MANIFOLD_WASH, 1536, False),
      (StepType.WASH_1536, 1536, True),
      (StepType.WASH_1536, 96, False),
      (StepType.MANIFOLD_PRIME, 1536, True),
      (StepType.PERI_DISPENSE, 1536, True),
      (StepType.PERI_WASH_ASPIRATE, 96, True),
      (StepType.PERI_WASH_ASPIRATE, 1536, False),
    ],
  )
  def test_a_plate_allows_a_step_type_or_does_not(
    self, step_type: StepType, wells: int, allowed: bool
  ):
    """A plate allows a step type or does not."""
    plate = {96: PLATE_96, 384: PLATE_384, 1536: PLATE_1536}[wells]
    assert (plate_rules.check_plate(plate, step_type) is None) is allowed

  def test_a_wash_on_a_1536_plate_has_a_reason_of_its_own(self):
    """That plate has a wash of its own, so the rejection says so rather than reporting a plate the
    step cannot use."""
    plain = plate_rules.check_plate(PLATE_1536, StepType.MANIFOLD_WASH)
    unusable = plate_rules.check_plate(PLATE_96, StepType.WASH_1536)
    assert plain is not None and unusable is not None
    assert plain.code != unusable.code

  def test_the_plate_is_the_first_verdict(self):
    """A step whose plate rules it out is rejected for that, even when its own fields are also
    wrong -- the order matters, because the reason is what a user acts on."""
    report, _ = check([ManifoldWash(cycles=0)], plate=PLATE_1536)
    assert not report
    assert report.failures[0].rejection == plate_rules.check_plate(
      PLATE_1536, StepType.MANIFOLD_WASH
    )


class TestWhatMustBeFitted:
  """A step that needs hardware the instrument does not have."""

  @pytest.mark.parametrize(
    "step, settings",
    [
      (ManifoldAutoClean(), InstrumentSettings(family=EL406, ultrasonic=False)),
      (PeriPrime(), InstrumentSettings(family=EL406, peri_pump=False)),
      (
        SyringePrime(),
        InstrumentSettings(
          family=EL406,
          syringe_box=SyringeBoxType.NOT_INSTALLED,
          syringe_manifold=SyringeManifold.NOT_INSTALLED,
        ),
      ),
      (StripPrime(), InstrumentSettings(family=EL406)),
    ],
  )
  def test_a_step_whose_hardware_is_absent_cannot_run(self, step: Step, settings):
    """A step whose hardware is absent cannot run."""
    report, _ = check([step], settings)
    assert not report

  def test_a_step_whose_hardware_is_fitted_can_run(self):
    """A step whose hardware is fitted can run."""
    report, _ = check([PeriPrime()], InstrumentSettings(family=EL406, peri_pump=True))
    assert report

  def test_the_palette_is_what_is_fitted(self):
    """The palette is what is fitted."""
    everything = available_step_types(InstrumentSettings(family=EL406))
    without = available_step_types(
      InstrumentSettings(family=EL406, peri_pump=False, ultrasonic=False)
    )
    assert StepType.PERI_DISPENSE in everything
    assert StepType.PERI_DISPENSE not in without
    assert StepType.MANIFOLD_AUTO_CLEAN not in without
    assert StepType.SHAKE_SOAK in without


class TestWhatTheProtocolCommitsTo:
  """The rules that are about the protocol as a whole rather than one step."""

  def test_one_buffer_throughout_unless_a_valve_box_can_switch_it(self):
    """One buffer throughout unless a valve box can switch it."""
    without = InstrumentSettings(
      family=EL406, valve_box=ValveBox.NOT_INSTALLED, buffer_switching=False
    )
    report, _ = check(
      [ManifoldDispense(volume=100, buffer="A"), ManifoldDispense(volume=100, buffer="B")],
      without,
    )
    assert not report
    assert report.failures[0].number == 2

  def test_the_same_buffer_twice_is_no_conflict(self):
    """The same buffer twice is no conflict."""
    without = InstrumentSettings(
      family=EL406, valve_box=ValveBox.NOT_INSTALLED, buffer_switching=False
    )
    report, _ = check(
      [ManifoldDispense(volume=100, buffer="A"), ManifoldDispense(volume=100, buffer="A")],
      without,
    )
    assert report

  def test_a_conflict_is_reported_against_the_second_step(self):
    """The claim accumulates, so the first step is fine and the second is where it shows."""
    report, _ = check(
      [
        PeriPrime(cassette_type="1uL", peri_pump="Primary"),
        PeriPrime(cassette_type="5uL", peri_pump="Primary"),
      ]
    )
    assert not report
    assert [failure.number for failure in report.failures] == [2]


class TestWhatThePassReserves:
  """The pass's second output: what the protocol requires of the pumps."""

  def test_a_pinned_cassette_is_reserved_for_its_pump(self):
    """A pinned cassette is reserved for its pump."""
    _, reservations = check([PeriPrime(cassette_type="5uL", peri_pump="Primary")])
    assert reservations.cassette_primary == "5uL"
    assert reservations.uses_primary

  def test_accepting_any_cassette_pins_nothing(self):
    """Accepting any cassette pins nothing."""
    _, reservations = check([PeriPrime(cassette_type="Any", peri_pump="Primary")])
    assert reservations.cassette_primary is None

  def test_a_dispense_records_that_it_got_far_enough_to_claim_one(self):
    """A dispense records that it got far enough to claim one."""
    _, reservations = check([PeriDispense(volume=10, cassette_type="5uL")])
    assert reservations.dispense_reserved

  def test_a_protocol_that_drives_no_pump_reserves_nothing(self):
    """Which is why a wash-only protocol opens its batch with one command and nothing before it."""
    _, reservations = check([ManifoldPrime(volume=40_000)])
    assert reservations.cassette_primary is None
    assert reservations.cassette_secondary is None
    assert not reservations.uses_primary
    assert not reservations.uses_secondary

  def test_every_pass_starts_over(self):
    """A second protocol must not inherit the first one's claims."""
    _, first = check([PeriPrime(cassette_type="5uL")])
    _, second = check([ManifoldPrime(volume=40_000)])
    assert first.cassette_primary == "5uL"
    assert second.cassette_primary is None


class TestWhatTheInstrumentItselfRefuses:
  """Rules about the instrument and the plate, which stop the whole protocol."""

  def test_a_plate_the_instrument_does_not_accept_stops_everything(self):
    """A plate the instrument does not accept stops everything."""
    report, _ = check(
      [ManifoldPrime(volume=40_000)],
      plate=PLATE_384,
      plate_restriction=PlateRestriction.ALLOW_96_WELL_ONLY,
    )
    assert not report
    assert report.rejection is not None
    assert report.steps == []
    assert "cannot run" in str(report)

  def test_an_accepted_plate_runs(self):
    """An accepted plate runs."""
    report, _ = check(
      [ManifoldPrime(volume=40_000)],
      plate=PLATE_96,
      plate_restriction=PlateRestriction.ALLOW_96_WELL_ONLY,
    )
    assert report

  def test_a_restriction_the_instrument_was_not_asked_about_is_not_guessed(self):
    """A restriction the instrument was not asked about is not guessed."""
    report, _ = check([ManifoldPrime(volume=40_000)], plate=PLATE_384)
    assert report


class TestWhatTheFirmwareKnows:
  """The per-model rule sets, which are data rather than code."""

  def test_the_models_do_not_check_identically(self):
    """The models do not check identically."""
    assert rules_for(InstrumentFamily.MULTIFLO_FX) is MULTIFLO_FX
    assert rules_for(InstrumentFamily.EL406) is COMMON
    assert MULTIFLO_FX.basecode_step_types
    assert not COMMON.basecode_step_types

  def test_the_oldest_firmware_lacks_rules_the_others_have(self):
    """The oldest firmware lacks rules the others have."""
    older = rules_for(InstrumentFamily.MULTIFLO)
    assert older.absent_checks
    assert not COMMON.absent_checks

  def test_a_rule_a_model_does_not_have_is_skipped(self):
    """A washer manifold the plate rules out is a rejection on a model that checks for it, and the
    step carries on to the next rule on one that does not."""
    settings = InstrumentSettings(family=EL406, washer_manifold=WasherManifold.TUBE_128)
    report, _ = check([ManifoldDispense(volume=100)], settings, plate=PLATE_96)
    assert not report
