"""Reading and writing the fitted-options document a protocol file carries.

A protocol file stores what the instrument was fitted with as a nested document, and hands it over
as text. This module is what turns that text into :class:`InstrumentSettings` and back, so a file
round-trips unchanged: the element order, the two-space indent and the carriage returns below are
the layout a file stores, not a choice.

Two fields are not in the document at all. Which plate carrier and cassette are fitted is not part
of it, and neither is whether the dispensers accept the wider offset range -- that one is only ever
learned by asking the instrument, so loading a file leaves it off.
"""

from __future__ import annotations

from typing import Mapping
from xml.etree import ElementTree

from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.strip_washer_manifold import StripWasherManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_type import SyringeBoxType
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_manifold import SyringeManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.valve_box import ValveBox
from pylabrobot.agilent.biotek.lhc.enums.instrument.washer_manifold import WasherManifold

FAMILY_TEXT: dict[str, InstrumentFamily] = {
  "e406Basic": InstrumentFamily.EL406,
  "eMMDBasic": InstrumentFamily.MULTIFLO,
  "e405TSBasic": InstrumentFamily.MODEL_405_TS,
  "eMultiFloFX": InstrumentFamily.MULTIFLO_FX,
}
"""The text a file stores each instrument family as."""

WASHER_MANIFOLD_TEXT: dict[str, WasherManifold] = {
  "e96TubeDual": WasherManifold.TUBE_96_DUAL,
  "e192Tube": WasherManifold.TUBE_192,
  "e128Tube": WasherManifold.TUBE_128,
  "e96TubeSingle": WasherManifold.TUBE_96_SINGLE,
  "e96DeepPin": WasherManifold.DEEP_PIN_96,
  "eNotInstalled": WasherManifold.NOT_INSTALLED,
}
"""The text a file stores each wash manifold as."""

SYRINGE_BOX_TEXT: dict[str, SyringeBoxType] = {
  "eNotInstalled": SyringeBoxType.NOT_INSTALLED,
  "eAutoclavable": SyringeBoxType.AUTOCLAVABLE,
  "eNonAutoclavable": SyringeBoxType.NON_AUTOCLAVABLE,
}
"""The text a file stores each syringe box as."""

SYRINGE_MANIFOLD_TEXT: dict[str, SyringeManifold] = {
  "eNotInstalled": SyringeManifold.NOT_INSTALLED,
  "e16Tube": SyringeManifold.TUBE_16,
  "e32TubeLB": SyringeManifold.TUBE_32_LARGE_BORE,
  "e32TubeSB": SyringeManifold.TUBE_32_SMALL_BORE,
  "e16Tube7": SyringeManifold.TUBE_16_7,
  "e8Tube": SyringeManifold.TUBE_8,
  "e6WellPlate": SyringeManifold.PLATE_6_WELL,
  "e12WellPlate": SyringeManifold.PLATE_12_WELL,
  "e24WellPlate": SyringeManifold.PLATE_24_WELL,
  "e48WellPlate": SyringeManifold.PLATE_48_WELL,
}
"""The text a file stores each syringe manifold as."""

VALVE_BOX_TEXT: dict[str, ValveBox] = {
  "eNotInstalled": ValveBox.NOT_INSTALLED,
  "eWasher": ValveBox.WASHER,
  "eSyringe": ValveBox.SYRINGE,
  "eInternal_1": ValveBox.INTERNAL_1,
  "eInternal_2": ValveBox.INTERNAL_2,
  "eInternal_3": ValveBox.INTERNAL_3,
  "eInternal_4": ValveBox.INTERNAL_4,
}
"""The text a file stores each valve box as."""

STRIP_WASHER_MANIFOLD_TEXT: dict[str, StripWasherManifold] = {
  "e6WellPlate": StripWasherManifold.PLATE_6_WELL,
  "e12WellPlate": StripWasherManifold.PLATE_12_WELL,
  "e24WellPlate": StripWasherManifold.PLATE_24_WELL,
  "e48WellPlate": StripWasherManifold.PLATE_48_WELL,
  "e96WellPlate": StripWasherManifold.PLATE_96_WELL,
  "eNotInstalled": StripWasherManifold.NOT_INSTALLED,
}
"""The text a file stores each strip wash manifold as."""

ROOT = "CInstrumentSettings"
_HEADER = (
  '<?xml version="1.0"?>\r\n'
  f"<{ROOT}"
  ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
  ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
)
_TRUE = "true"
_FALSE = "false"

_FAMILY = "Instrument"
_WASHER_MANIFOLD = "WasherManifold"
_SYRINGE_BOX = "SyringeBox"
_SYRINGE_MANIFOLD = "SyringeManifold"
_BUFFER_SWITCHING = "BufferSwitchingModule"
_BUFFER_SWITCHING_SHORT = "BufferSwitching"
_VALVE_BOX = "ValveBoxType"
_VACUUM_FILTRATION = "VacuumFiltrationWasher"
_PERI_PUMP = "PeriPumpModule"
_PERI_PUMP_2 = "PeriPumpModule2"
_ULTRASONIC = "UltrasonicModule"
_CELL_WASHING = "CellWashingModule"
_Y_AXIS = "m_bYAxisInstalled"
_HALF_UL = "m_bHalfULEnabled"
_STRIP_WASHER_MANIFOLD = "StripWasherManifold"
_SINGLE_WELL = "m_bSingleWellEnabled"
_PERI_WASH = "m_bPWEnabled"


def from_xml(document: str) -> InstrumentSettings:
  """Read a fitted-options document.

  An element that is absent, empty or carries text this package does not know leaves its field at
  the default, which is how a document written by an older release stays readable. Which instrument
  wrote it is the one exception: a model this package does not know is an error rather than a
  default, because every field below is read as that model would mean it.

  Args:
    document: The document as the protocol file stores it.

  Returns:
    The settings it describes.

  Raises:
    ValueError: If the text is not a well-formed document, or names an instrument this package does
      not work with.
  """
  try:
    root = ElementTree.fromstring(document.strip())
  except ElementTree.ParseError as error:
    raise ValueError(f"fitted options will not read: {error}") from error
  default = InstrumentSettings()

  def text(tag: str) -> str:
    """The text of one element.

    Args:
      tag: Which element to read.

    Returns:
      Its text, stripped, or an empty string when the element is absent or empty.
    """
    element = root.find(tag)
    return "" if element is None or element.text is None else element.text.strip()

  def flag(tag: str, fallback: bool) -> bool:
    """One element read as a flag.

    Args:
      tag: Which element to read.
      fallback: What an absent element means.

    Returns:
      The flag.
    """
    found = text(tag)
    return fallback if not found else found == _TRUE

  named = text(_FAMILY)
  if named and named not in FAMILY_TEXT:
    raise ValueError(
      f"fitted options are for {named}, which this package does not work with; it works with "
      f"{', '.join(FAMILY_TEXT)}"
    )
  valve_box = VALVE_BOX_TEXT.get(text(_VALVE_BOX), default.valve_box)
  return InstrumentSettings(
    family=FAMILY_TEXT.get(named, default.family),
    washer_manifold=WASHER_MANIFOLD_TEXT.get(text(_WASHER_MANIFOLD), default.washer_manifold),
    syringe_box=SYRINGE_BOX_TEXT.get(text(_SYRINGE_BOX), default.syringe_box),
    syringe_manifold=SYRINGE_MANIFOLD_TEXT.get(text(_SYRINGE_MANIFOLD), default.syringe_manifold),
    buffer_switching=flag(
      _BUFFER_SWITCHING, flag(_BUFFER_SWITCHING_SHORT, default.buffer_switching)
    ),
    valve_box=valve_box,
    vacuum_filtration=flag(_VACUUM_FILTRATION, default.vacuum_filtration),
    peri_pump=flag(_PERI_PUMP, default.peri_pump),
    peri_pump_2=flag(_PERI_PUMP_2, default.peri_pump_2),
    ultrasonic=flag(_ULTRASONIC, default.ultrasonic),
    cell_washing=flag(_CELL_WASHING, default.cell_washing),
    y_axis_installed=flag(_Y_AXIS, default.y_axis_installed),
    half_ul_enabled=flag(_HALF_UL, default.half_ul_enabled),
    strip_washer_manifold=STRIP_WASHER_MANIFOLD_TEXT.get(
      text(_STRIP_WASHER_MANIFOLD), default.strip_washer_manifold
    ),
    single_well_enabled=flag(_SINGLE_WELL, default.single_well_enabled),
    peri_wash_enabled=flag(_PERI_WASH, default.peri_wash_enabled),
  )


def to_xml(settings: InstrumentSettings) -> str:
  """Write a fitted-options document.

  Args:
    settings: The settings to write.

  Returns:
    The document, in the layout a protocol file stores: two-space indent, carriage returns
    throughout and no trailing newline. Every element is written, so a document that was stored
    without the newer ones gains them, each carrying what was read for it.

  Raises:
    ValueError: If a field holds a value no document has text for.
  """
  lines = [_HEADER]
  for tag, value in (
    (_FAMILY, _text_for(FAMILY_TEXT, settings.family, _FAMILY)),
    (_WASHER_MANIFOLD, _text_for(WASHER_MANIFOLD_TEXT, settings.washer_manifold, _WASHER_MANIFOLD)),
    (_SYRINGE_BOX, _text_for(SYRINGE_BOX_TEXT, settings.syringe_box, _SYRINGE_BOX)),
    (
      _SYRINGE_MANIFOLD,
      _text_for(SYRINGE_MANIFOLD_TEXT, settings.syringe_manifold, _SYRINGE_MANIFOLD),
    ),
    (_BUFFER_SWITCHING, _TRUE if settings.buffer_switching else _FALSE),
    (_VALVE_BOX, _text_for(VALVE_BOX_TEXT, settings.valve_box, _VALVE_BOX)),
    (_VACUUM_FILTRATION, _TRUE if settings.vacuum_filtration else _FALSE),
    (_PERI_PUMP, _TRUE if settings.peri_pump else _FALSE),
    (_PERI_PUMP_2, _TRUE if settings.peri_pump_2 else _FALSE),
    (_ULTRASONIC, _TRUE if settings.ultrasonic else _FALSE),
    (_CELL_WASHING, _TRUE if settings.cell_washing else _FALSE),
    (_Y_AXIS, _TRUE if settings.y_axis_installed else _FALSE),
    (_HALF_UL, _TRUE if settings.half_ul_enabled else _FALSE),
    (
      _STRIP_WASHER_MANIFOLD,
      _text_for(STRIP_WASHER_MANIFOLD_TEXT, settings.strip_washer_manifold, _STRIP_WASHER_MANIFOLD),
    ),
    (_SINGLE_WELL, _TRUE if settings.single_well_enabled else _FALSE),
    (_PERI_WASH, _TRUE if settings.peri_wash_enabled else _FALSE),
  ):
    lines.append(f"  <{tag}>{value}</{tag}>")
  lines.append(f"</{ROOT}>")
  return "\r\n".join(lines)


def _text_for(table: Mapping[str, int], value: int, tag: str) -> str:
  """The text a document stores a value as.

  Args:
    table: The texts for that element, mapping each to the value it means.
    value: The value to write.
    tag: Which element this is, for the message of any exception raised.

  Returns:
    The text.

  Raises:
    ValueError: If the table has no text for that value.
  """
  for text, known in table.items():
    if known == value:
      return text
  raise ValueError(f"no {tag} text for {value!r}")
