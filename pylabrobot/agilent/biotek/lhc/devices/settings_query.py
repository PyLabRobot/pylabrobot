"""Asking an instrument what it has fitted.

A protocol file says what the instrument was fitted with when the protocol was written; this asks
the instrument what it is fitted with now. The two can disagree, and it is this answer that steps
are encoded against and validation is run against.

Which options an instrument answers for depends on its family, so there is one sequence per family.
A query for hardware the family cannot carry is not sent at all, and the field keeps the value that
means "not fitted".
"""

from __future__ import annotations

import logging

from pylabrobot.agilent.biotek.lhc.comm.link import Link
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.devices.queries import (
  answers,
  byte,
  flag,
  optional_byte,
  optional_flag,
)
from pylabrobot.agilent.biotek.lhc.enums.instrument.basecode import Basecode
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.strip_washer_manifold import StripWasherManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_size import SyringeBoxSize
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_type import SyringeBoxType
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_manifold import SyringeManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.valve_box import ValveBox
from pylabrobot.agilent.biotek.lhc.enums.instrument.washer_manifold import WasherManifold
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_pump import PERI_PUMP_TO_BYTE, PeriPump
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber
from pylabrobot.agilent.biotek.lhc.serialization.commands.configuration import (
  GetSyringeBoxInfo,
  SelectorQuery,
  SyringeBox,
)

logger = logging.getLogger(__name__)


async def read_settings(link: Link, family: InstrumentFamily) -> InstrumentSettings:
  """Ask an instrument what it has fitted.

  Args:
    link: The open link to the instrument.
    family: Which model this is. It is declared rather than discovered: the instrument does not
      report which model it is.

  Returns:
    What it answered, with every option the family cannot carry left at "not fitted".

  Raises:
    BiotekError: If an option the family does carry cannot be read, since a step encoded against a
      half-read record would be encoded wrongly.
  """
  if family is InstrumentFamily.MODEL_405_TS:
    return await _read_washer(link, family)
  settings = await _read_dispenser(link, family)
  if family is not InstrumentFamily.MULTIFLO_FX:
    return settings
  return await _read_strip_washer_and_firmware(link, settings)


async def _read_washer(link: Link, family: InstrumentFamily) -> InstrumentSettings:
  """Ask a wash-only instrument what it has fitted.

  Args:
    link: The open link to the instrument.
    family: Which model this is.

  Returns:
    What it answered.

  Raises:
    BiotekError: If an option cannot be read.
  """
  valve_box = ValveBox(await byte(link, CommandNumber.GET_EXT_VALVE_MODULE_INSTALLED))
  return InstrumentSettings(
    family=family,
    washer_manifold=WasherManifold(await byte(link, CommandNumber.GET_WASHER_MANIFOLD_INSTALLED)),
    syringe_box=SyringeBoxType.NOT_INSTALLED,
    syringe_manifold=SyringeManifold.NOT_INSTALLED,
    buffer_switching=valve_box is not ValveBox.NOT_INSTALLED,
    valve_box=valve_box,
    vacuum_filtration=await flag(link, CommandNumber.GET_VACUUM_FILTRATION_INSTALLED),
    peri_pump=False,
    peri_pump_2=False,
    ultrasonic=await flag(link, CommandNumber.GET_ULTRASONIC_CLEANER_INSTALLED),
    cell_washing=await flag(link, CommandNumber.GET_CELL_WASHING_INSTALLED),
    y_axis_installed=await flag(link, CommandNumber.GET_Y_AXIS_INSTALLED),
    half_ul_enabled=False,
    strip_washer_manifold=StripWasherManifold.NOT_INSTALLED,
  )


async def _read_dispenser(link: Link, family: InstrumentFamily) -> InstrumentSettings:
  """Ask an instrument with syringes and peristaltic pumps what it has fitted.

  The wash manifold, the valve box and the modules that go with it are only asked about on the
  model that can carry them; on the others the query is not sent and the field says "not fitted".

  Args:
    link: The open link to the instrument.
    family: Which model this is.

  Returns:
    What it answered.

  Raises:
    BiotekError: If an option cannot be read.
  """
  washes = family is InstrumentFamily.EL406
  syringe_manifold = SyringeManifold(await byte(link, CommandNumber.GET_SYRINGE_MANIFOLD_INSTALLED))
  box = await _syringe_box(link)
  primary = await _peri_installed(link, "Primary")
  secondary = False if washes else await _peri_installed(link, "Secondary")
  valve_box = (
    ValveBox(await byte(link, CommandNumber.GET_EXT_VALVE_MODULE_INSTALLED))
    if washes
    else ValveBox.NOT_INSTALLED
  )
  return InstrumentSettings(
    family=family,
    washer_manifold=(
      WasherManifold(await byte(link, CommandNumber.GET_WASHER_MANIFOLD_INSTALLED))
      if washes
      else WasherManifold.NOT_INSTALLED
    ),
    syringe_box=SyringeBoxType(box.box_type),
    syringe_manifold=syringe_manifold,
    buffer_switching=valve_box is not ValveBox.NOT_INSTALLED,
    valve_box=valve_box,
    vacuum_filtration=(
      await flag(link, CommandNumber.GET_VACUUM_FILTRATION_INSTALLED) if washes else False
    ),
    peri_pump=primary,
    peri_pump_2=secondary,
    ultrasonic=(
      await flag(link, CommandNumber.GET_ULTRASONIC_CLEANER_INSTALLED) if washes else False
    ),
    cell_washing=(await flag(link, CommandNumber.GET_CELL_WASHING_INSTALLED) if washes else False),
    y_axis_installed=True,
    half_ul_enabled=bool(await optional_flag(link, CommandNumber.GET_IS_PERI_HALF_UL_SUPPORTED)),
    strip_washer_manifold=StripWasherManifold.NOT_INSTALLED,
    syringe_box_size=SyringeBoxSize(box.box_size),
  )


async def _read_strip_washer_and_firmware(
  link: Link, settings: InstrumentSettings
) -> InstrumentSettings:
  """Add what only the newest model reports: its strip washer and its firmware variant.

  The strip washer manifold is only asked about once both the box and its hardware answer yes, so
  an instrument that has neither keeps a manifold of "not fitted" and offers no strip wash steps.
  The firmware variant is what makes the peristaltic wash steps available, and a firmware that does
  not report one leaves them unavailable.

  Args:
    link: The open link to the instrument.
    settings: What has been read so far.

  Returns:
    The settings, with the strip washer and firmware fields filled in.
  """
  manifold = settings.strip_washer_manifold
  single_well = settings.single_well_enabled
  if await optional_flag(link, CommandNumber.IS_STRIP_WASHER_BOX_CONNECTED) and (
    await optional_flag(link, CommandNumber.GET_STRIP_WASHER_HW_INSTALLED)
  ):
    fitted = await optional_byte(link, CommandNumber.GET_STRIP_WASHER_MANIFOLD_TYPE)
    if fitted is not None:
      manifold = StripWasherManifold(fitted)
    single_well = bool(await optional_flag(link, CommandNumber.GET_SINGLE_WELL_DISPENSER_INSTALLED))
  basecode = await optional_byte(link, CommandNumber.GET_WHICH_BASECODE_IS_INSTALLED)
  return InstrumentSettings(
    family=settings.family,
    washer_manifold=settings.washer_manifold,
    syringe_box=settings.syringe_box,
    syringe_manifold=settings.syringe_manifold,
    buffer_switching=settings.buffer_switching,
    valve_box=settings.valve_box,
    vacuum_filtration=settings.vacuum_filtration,
    peri_pump=settings.peri_pump,
    peri_pump_2=settings.peri_pump_2,
    ultrasonic=settings.ultrasonic,
    cell_washing=settings.cell_washing,
    y_axis_installed=settings.y_axis_installed,
    half_ul_enabled=settings.half_ul_enabled,
    strip_washer_manifold=manifold,
    single_well_enabled=single_well,
    peri_wash_enabled=basecode == Basecode.PERI_WASH,
    advanced_dispense_offsets=await answers(link, CommandNumber.GET_FLUID_TRACKING_ENABLED),
    syringe_box_size=settings.syringe_box_size,
  )


async def _syringe_box(link: Link) -> SyringeBox:
  """Read which syringe box is fitted and how many bottles it holds.

  Args:
    link: The open link to the instrument.

  Returns:
    The box type and size.

  Raises:
    BiotekError: If the answer cannot be read.
  """
  command = GetSyringeBoxInfo()
  return command.parse(await link.request(command, operation="syringe box"))


async def _peri_installed(link: Link, pump: PeriPump) -> bool:
  """Read whether a peristaltic pump is fitted.

  Args:
    link: The open link to the instrument.
    pump: Which pump to ask about.

  Returns:
    Whether it is fitted.

  Raises:
    BiotekError: If the answer cannot be read.
  """
  command = SelectorQuery(CommandNumber.GET_SELECTED_PERI_INSTALLED, PERI_PUMP_TO_BYTE[pump])
  return command.parse_flag(await link.request(command, operation=f"peri pump {pump.lower()}"))
