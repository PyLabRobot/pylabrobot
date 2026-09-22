"""Opening and closing a batch.

The instrument has no notion of a step outside a batch: opening one is what homes the motors and
tells the firmware which plate it is working with, and every step then carries that plate's byte.
The bracket is re-entrant, so one imperative call brackets itself while a caller who opens a batch
by hand pays the opening cost once for a whole sequence of them.

Opening is not only the one command. What a protocol requires of the peristaltic pumps was worked
out by the validation pass; this is where the hardware is made to match it -- the cassette in each
pinned pump, the dispense head on the models that have a settable one, and that each pump a step
drives can run at all. A protocol that reserves nothing, which is every wash-only protocol, opens
with the one command and nothing before it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from pylabrobot.agilent.biotek.lhc.comm.link import Link
from pylabrobot.agilent.biotek.lhc.devices.runtime import Runtime
from pylabrobot.agilent.biotek.lhc.enums.motion.motor import Motor
from pylabrobot.agilent.biotek.lhc.enums.motion.motor_home_type import MotorHomeType
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_head import (
  CASSETTE_HEAD_TO_BYTE,
  CassetteHead,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_mode import CASSETTE_MODE_TO_BYTE
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_type import (
  CASSETTE_TYPE_TO_BYTE,
  CassetteType,
)
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_pump import PERI_PUMP_TO_BYTE, PeriPump
from pylabrobot.agilent.biotek.lhc.error_handling import raise_for_status
from pylabrobot.agilent.biotek.lhc.protocols.validation.reservations import (
  CONFLICT,
  check_head_fits,
)
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber
from pylabrobot.agilent.biotek.lhc.serialization.commands.configuration import (
  ByteQuery,
  SelectorQuery,
  SelectorWrite,
)
from pylabrobot.agilent.biotek.lhc.serialization.commands.diagnostics import HomeVerifyMotors
from pylabrobot.agilent.biotek.lhc.serialization.commands.run_control import (
  ExitProtocol,
  InitProtocol,
)

logger = logging.getLogger(__name__)

PUMP_COVER_OPEN = 24728
"""The pump's cover is open, so it cannot turn."""

NO_PUMP_ASSEMBLY = 24729
"""No pump assembly is in place."""

_PUMP_READY = 2
_PUMP_COVER_OPEN = 1
_PLATE_WIDE_HEAD: CassetteHead = "8 tubes to 8 wells"
_SINGLE_TUBE_HEAD: CassetteHead = "1 tube to 1 well"


@asynccontextmanager
async def batch(runtime: Runtime, home_on_close: bool = False) -> AsyncIterator[None]:
  """Hold a batch open for the duration of the block.

  Re-entrant: inside a batch that is already open this does nothing, so a call that brackets itself
  is free to be made inside a larger batch. The instrument is held for as long as the batch is open
  and released whether the block succeeded, failed, or the open itself failed.

  Args:
    runtime: The device's state.
    home_on_close: Whether to home the transport before closing. The instrument does not do this of
      its own accord; ask for it when the next thing to touch the plate is a person.

  Yields:
    Nothing. The batch is open for the body of the block.

  Raises:
    BiotekError: If the batch cannot be opened, including because the hardware cannot be made to
      match what the protocol requires.
  """
  if runtime.in_batch:
    yield
    return
  await runtime.port.acquire()
  try:
    await open_batch(runtime)
  except BaseException:
    runtime.port.release()
    raise
  runtime.in_batch = True
  try:
    yield
  finally:
    runtime.in_batch = False
    try:
      await close_batch(runtime, home_on_close=home_on_close)
    finally:
      runtime.port.release()


async def open_batch(runtime: Runtime) -> None:
  """Make the pumps match what the protocol requires, then open the batch.

  Args:
    runtime: The device's state, whose reservations say what to match.

  Raises:
    BiotekError: If the hardware cannot be made to match, if a pump a step drives cannot run, or if
      the instrument will not open the batch.
    RejectedError: If no plate has been set.
  """
  plate_type = runtime.plate_type
  await _reconcile_pumps(runtime)
  await runtime.link.request(InitProtocol(plate_type), operation="open batch")
  logger.info("batch open on %s for a %s", runtime.link.name, plate_type.name)


async def close_batch(runtime: Runtime, home_on_close: bool = False) -> None:
  """Close the batch.

  Args:
    runtime: The device's state.
    home_on_close: Whether to home the transport first.

  Raises:
    BiotekError: If the instrument will not close the batch.
  """
  if home_on_close:
    await runtime.link.request(
      HomeVerifyMotors(int(MotorHomeType.HOME_XYZ_MOTORS), int(Motor.CARRIER_X)),
      operation="home",
    )
  await runtime.link.request(ExitProtocol(), operation="close batch")
  logger.info("batch closed on %s", runtime.link.name)


async def _reconcile_pumps(runtime: Runtime) -> None:
  """Make the peristaltic hardware match what the protocol reserved.

  In order: the dispense head, on the model that has a settable one; then each pinned pump's
  cassette; then that every pump a step drives can run. A protocol that pinned nothing and drives
  no pump does none of it.

  Args:
    runtime: The device's state.

  Raises:
    BiotekError: If a required cassette or head cannot be fitted, or a pump cannot run.
  """
  reservations = runtime.reservations
  head = reservations.cassette_head
  move_primary = False
  move_secondary = False
  if runtime.reconciles_cassette_head:
    head, move_primary, move_secondary = await _resolve_head(runtime)
  await _reconcile_cassette(runtime, "Primary", reservations.cassette_primary, head, move_primary)
  await _reconcile_cassette(
    runtime, "Secondary", reservations.cassette_secondary, head, move_secondary
  )
  if reservations.uses_primary:
    await _check_pump_can_run(runtime.link, "Primary")
  if reservations.uses_secondary:
    await _check_pump_can_run(runtime.link, "Secondary")


async def _resolve_head(runtime: Runtime) -> tuple[CassetteHead | None, bool, bool]:
  """Work out which dispense head the protocol needs and whether it has to be written.

  The head is fitted to one pump -- the second when two are fitted -- so at most one of the two
  answers is ever true. Writing it is only possible where that pump's cassette is pinned to a
  particular one, since the head is written as part of setting the cassette; where the protocol
  accepts any cassette there is nothing to write and the fitted head has to be right already.

  Args:
    runtime: The device's state.

  Returns:
    The head to write, and whether it must be written for the primary and for the secondary pump.

  Raises:
    BiotekError: If the fitted head cannot serve the protocol, or does not suit the plate.
  """
  settings = runtime.settings
  reservations = runtime.reservations
  required = reservations.cassette_head
  fitted = await _fitted_head(runtime) if settings.single_well_enabled else None
  secondary = settings.peri_pump_2

  if reservations.single_well:
    if required is None:
      # Nothing reserved a head, so the fitted one has to serve single wells already: a head that
      # feeds eight wells at once, or no head at all, cannot.
      if fitted is None or fitted == _PLATE_WIDE_HEAD:
        raise_for_status(CONFLICT, runtime.family, "cassette head")
      if reservations.dispense_reserved:
        _require_head_fits(runtime, fitted)
      return None, False, False
    pinned = reservations.cassette_secondary if secondary else reservations.cassette_primary
    move = fitted != required and pinned is not None
    if reservations.dispense_reserved:
      _require_head_fits(runtime, required)
    return required, move and not secondary, move and secondary

  if not settings.single_well_enabled:
    return required, False, False
  if not (
    (reservations.any_cassette_primary and not secondary) or reservations.any_cassette_secondary
  ):
    return required, False, False

  # No step needs single wells, but one accepts any cassette on an instrument that can dispense
  # into them, so the head has to be the plate-wide one.
  if required is None:
    required = _PLATE_WIDE_HEAD
  elif required != _PLATE_WIDE_HEAD:
    raise_for_status(CONFLICT, runtime.family, "cassette head")
  move = fitted != required
  pinned = reservations.cassette_secondary if secondary else reservations.cassette_primary
  if move and pinned is None:
    raise_for_status(CONFLICT, runtime.family, "cassette head")
  if reservations.dispense_reserved:
    _require_head_fits(runtime, required)
  return required, move and not secondary, move and secondary


def _require_head_fits(runtime: Runtime, head: CassetteHead | None) -> None:
  """Check a dispense head against the plate on the carrier.

  Args:
    runtime: The device's state.
    head: The head that would be used.

  Raises:
    BiotekError: If the head cannot serve that plate.
    RejectedError: If no plate has been set.
  """
  rejection = check_head_fits(head, runtime.plate_record, runtime.rules.absent_checks)
  if rejection is not None:
    raise_for_status(rejection.code, runtime.family, "cassette head")


async def _fitted_head(runtime: Runtime) -> CassetteHead | None:
  """Read which dispense head is on the instrument.

  Args:
    runtime: The device's state.

  Returns:
    The head, or None when the instrument reports one this package does not know, which is how it
    reports having none.
  """
  selector = PERI_PUMP_TO_BYTE["Secondary" if runtime.settings.peri_pump_2 else "Primary"]
  command = SelectorQuery(CommandNumber.GET_EXT_PERI_CASSETTE_HEAD, selector)
  answer = command.parse(await runtime.link.request(command, operation="fitted cassette head"))
  for head, value in CASSETTE_HEAD_TO_BYTE.items():
    if value == answer:
      return head
  return None


async def _reconcile_cassette(
  runtime: Runtime,
  pump: PeriPump,
  required: CassetteType | None,
  head: CassetteHead | None,
  move_head: bool,
) -> None:
  """Make one pump hold the cassette the protocol requires.

  Nothing happens unless the protocol pinned a cassette for that pump. Otherwise the fitted one is
  read and, if it is wrong -- or right while the head still has to move -- the instrument's own
  setting decides what may be done about it: only an instrument set to fit the cassette itself is
  written to, and any other setting reports the mismatch, because there is nobody here to ask to
  change a cassette by hand. A write is read back, so a cassette that did not change is reported
  rather than run with.

  Args:
    runtime: The device's state.
    pump: Which pump to reconcile.
    required: The cassette the protocol pinned, or None when it pinned none.
    head: The dispense head to write along with the cassette.
    move_head: Whether the head has to be written.

  Raises:
    BiotekError: If the cassette cannot be made to match.
  """
  if required is None:
    return
  link = runtime.link
  selector = PERI_PUMP_TO_BYTE[pump]
  fitted = SelectorQuery(CommandNumber.GET_SELECTED_PERI_CASSETTE_TYPE, selector)
  answer = fitted.parse(await link.request(fitted, operation=f"{pump.lower()} pump cassette"))
  if answer == CASSETTE_TYPE_TO_BYTE[required] and not move_head:
    return
  mode = ByteQuery(CommandNumber.GET_CASSETTE_MODE)
  setting = mode.parse(await link.request(mode, operation="cassette mode"))
  if setting != CASSETTE_MODE_TO_BYTE["Auto set"]:
    raise_for_status(CONFLICT, runtime.family, f"{pump.lower()} pump cassette")
  await link.request(
    SelectorWrite(
      CommandNumber.SET_SELECTED_PERI_CASSETTE_TYPE, selector, CASSETTE_TYPE_TO_BYTE[required]
    ),
    operation=f"{pump.lower()} pump cassette",
  )
  answer = fitted.parse(await link.request(fitted, operation=f"{pump.lower()} pump cassette"))
  if answer != CASSETTE_TYPE_TO_BYTE[required]:
    raise_for_status(CONFLICT, runtime.family, f"{pump.lower()} pump cassette")
  logger.info("set the %s pump to a %s cassette", pump.lower(), required)
  if not move_head:
    return
  await link.request(
    SelectorWrite(
      CommandNumber.SET_EXT_PERI_CASSETTE_HEAD,
      selector,
      CASSETTE_HEAD_TO_BYTE[head if head is not None else _SINGLE_TUBE_HEAD],
    ),
    operation="cassette head",
  )
  logger.info("set the dispense head to %s", head)


async def _check_pump_can_run(link: Link, pump: PeriPump) -> None:
  """Check that a pump a step drives is in a state to turn.

  Args:
    link: The link to the instrument.
    pump: Which pump to check.

  Raises:
    BiotekError: If its cover is open or no pump assembly is in place.
  """
  command = SelectorQuery(CommandNumber.GET_SELECTED_PERI_STATE, PERI_PUMP_TO_BYTE[pump])
  state = command.parse(await link.request(command, operation=f"{pump.lower()} pump state"))
  if state == _PUMP_READY:
    return
  code = PUMP_COVER_OPEN if state == _PUMP_COVER_OPEN else NO_PUMP_ASSEMBLY
  raise_for_status(code, link.family, f"{pump.lower()} pump")
