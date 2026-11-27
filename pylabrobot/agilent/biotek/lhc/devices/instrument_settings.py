"""What an instrument has fitted.

A step encodes itself against this record, and validation measures a step against it, so it is the
one description of the hardware that the rest of the package reads. Reading it off the instrument
and loading it from a protocol file both happen in this package; the record itself is plain data.
"""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.strip_washer_manifold import StripWasherManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_size import SyringeBoxSize
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_type import SyringeBoxType
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_manifold import SyringeManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.valve_box import ValveBox
from pylabrobot.agilent.biotek.lhc.enums.instrument.washer_manifold import WasherManifold


@dataclass(frozen=True)
class InstrumentSettings:
  """The options an instrument is fitted with.

  Read-only, because what it describes is: of the whole command vocabulary only the peristaltic
  cassettes can be written, and those are reconciled when a batch opens rather than held here.
  Everything else is hardware somebody fitted, or configuration set at the instrument itself, so a
  record that could be edited would only ever mislead. A different instrument is a different record.

  Attributes:
    family: Which instrument model this is.
    washer_manifold: The wash manifold fitted, or ``NOT_INSTALLED``.
    syringe_box: The syringe box fitted, or ``NOT_INSTALLED``.
    syringe_manifold: The syringe manifold fitted, or ``NOT_INSTALLED``.
    buffer_switching: Whether the buffer switching module is fitted.
    valve_box: The valve box fitted, or ``NOT_INSTALLED``.
    vacuum_filtration: Whether the vacuum filtration option is fitted.
    peri_pump: Whether the primary peristaltic pump is fitted.
    peri_pump_2: Whether the secondary peristaltic pump is fitted.
    ultrasonic: Whether the ultrasonic cleaning module is fitted.
    cell_washing: Whether the cell washing module is fitted, which unlocks the single-speed
      travel rates and flow rates below 3.
    y_axis_installed: Whether the manifold can be offset along Y.
    half_ul_enabled: Whether syringe volumes may be given in half microlitres.
    strip_washer_manifold: The strip washer manifold fitted, or ``NOT_INSTALLED``.
    single_well_enabled: Whether single-well operations are enabled.
    peri_wash_enabled: Whether the peristaltic wash step types are available.
    advanced_dispense_offsets: Whether the dispensers accept the wider X offset range. Set only by
      reading the instrument, never by loading a protocol file.
    syringe_box_size: How many syringe bottles the fitted box holds, or ``UNKNOWN`` when the
      instrument has not been asked.
  """

  family: InstrumentFamily = InstrumentFamily.EL406
  washer_manifold: WasherManifold = WasherManifold.TUBE_96_DUAL
  syringe_box: SyringeBoxType = SyringeBoxType.AUTOCLAVABLE
  syringe_manifold: SyringeManifold = SyringeManifold.TUBE_16
  buffer_switching: bool = True
  valve_box: ValveBox = ValveBox.WASHER
  vacuum_filtration: bool = False
  peri_pump: bool = True
  peri_pump_2: bool = False
  ultrasonic: bool = True
  cell_washing: bool = True
  y_axis_installed: bool = True
  half_ul_enabled: bool = True
  strip_washer_manifold: StripWasherManifold = StripWasherManifold.NOT_INSTALLED
  single_well_enabled: bool = False
  peri_wash_enabled: bool = False
  advanced_dispense_offsets: bool = False
  syringe_box_size: SyringeBoxSize = SyringeBoxSize.UNKNOWN

  @property
  def supports_random_access_tail(self) -> bool:
    """Whether peristaltic step definitions on this model may carry random-access fields.

    The MultiFlo predates random-access dispensing and reads a definition of the exact older
    length, so the extra fields make a definition it cannot parse at all.
    """
    return self.family is not InstrumentFamily.MULTIFLO
