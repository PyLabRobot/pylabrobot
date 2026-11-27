"""What a device holds while it is running.

Every model needs the same things to hand: the link to the instrument, what the instrument has
fitted, which rules its firmware runs, which plate is on the carrier, and what the last validation
pass reserved. They are gathered here so the shared functions in :mod:`.execution` and :mod:`.batch`
can take one argument instead of eight, and so a device file stays a list of its own public methods.

This is state, not behaviour: a device owns one of these and passes it to those functions. It is
deliberately not a base class -- nothing inherits from it, and a model that needs a fact the others
do not simply keeps that fact itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pylabrobot.agilent.biotek.lhc.comm.link import Link
from pylabrobot.agilent.biotek.lhc.devices.build_rules import COMMON, BuildRules
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.motion.carrier_type import CarrierType
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_restriction import PlateRestriction
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.error_handling import ErrorKind, fail
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import PlateRecord
from pylabrobot.agilent.biotek.lhc.protocols.validation.reservations import Reservations


@dataclass
class Runtime:
  """Everything shared behaviour needs from the device that owns it.

  Attributes:
    link: The connection to the instrument, which carries which model this is.
    rules: Which validation rules this model's firmware runs.
    reported_settings: What the instrument reported fitted, or None until ``setup()`` has asked
      it. Read it through :attr:`settings`, which refuses to hand over a record the instrument has
      not given.
    reconciles_cassette_head: Whether opening a batch reconciles the peristaltic dispense head as
      well as the cassettes. Only one model carries a head that can be set.
    plate: The plate on the carrier, or None while none has been set.
    reservations: What the last validation pass claimed of the pumps, which is what opening a batch
      makes the hardware match. Empty until a pass has run.
    plate_restriction: Which plates the instrument accepts, once it has been asked.
    carrier_type: Which carrier is fitted, once the instrument has been asked.
    settle: How long to wait after the instrument accepts a step before asking whether it has
      finished, in seconds. The first poll of a step that has only just started can still report the
      instrument idle, so this is what stops a step being called finished before it began.
    in_batch: Whether a batch is open, which is what makes the batch context re-entrant.
    aborting: Whether a stop has been asked for and not yet reported. The instrument reports
      itself ready once it has stopped and homed, which is indistinguishable from a step that
      finished, so this is what lets the caller waiting on the step be told it was stopped.
    port: Held for as long as a batch is open, so two callers cannot interleave runs on one
      instrument.
  """

  link: Link
  rules: BuildRules = COMMON
  reported_settings: InstrumentSettings | None = None
  reconciles_cassette_head: bool = False
  plate: PlateRecord | None = None
  reservations: Reservations = field(default_factory=Reservations)
  plate_restriction: PlateRestriction | None = None
  carrier_type: CarrierType | None = None
  settle: float = 0.5
  in_batch: bool = False
  aborting: bool = False
  port: asyncio.Lock = field(default_factory=asyncio.Lock)

  @property
  def settings(self) -> InstrumentSettings:
    """What the instrument reported fitted.

    Raises:
      RejectedError: If the instrument has not been read. Nothing can be encoded or checked without
        that: a step is encoded against what is fitted, and a check measures it against the same
        record, so answering from a default would describe some other machine. In particular it
        would describe a fully equipped instrument of the first model, and so answer "yes, that can
        run" for hardware this instrument does not have.
    """
    if self.reported_settings is None:
      raise fail(
        ErrorKind.REJECTED,
        "the instrument has not been read; call setup() before encoding or checking anything",
        operation="settings",
      )
    return self.reported_settings

  @property
  def plate_record(self) -> PlateRecord:
    """The plate on the carrier.

    Raises:
      RejectedError: If no plate has been set. Nothing can be encoded or checked without one:
        every step's offsets are measured from the plate's own heights.
    """
    if self.plate is None:
      raise fail(
        ErrorKind.REJECTED,
        "no plate is on the carrier; set one before running anything",
        operation="plate",
      )
    return self.plate

  @property
  def plate_type(self) -> PlateType:
    """Which format the instrument is told is on the carrier.

    Raises:
      RejectedError: If no plate has been set.
    """
    return self.plate_record.plate_type

  @property
  def family(self) -> InstrumentFamily:
    """Which model this is, which the instrument does not report and so is declared."""
    return self.link.family

  def forget_instrument_facts(self) -> None:
    """Forget what was read off the instrument about the plate it will accept.

    Called when the plate changes, so the next check asks again rather than measuring a new plate
    against an answer given for the old one.
    """
    self.plate_restriction = None
    self.carrier_type = None
