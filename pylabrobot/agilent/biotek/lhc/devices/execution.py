"""Running steps, and checking a protocol before running it.

A step command is answered as soon as the instrument has accepted the step, so the only way to
learn that it has finished is to ask: send it, then poll the run state until it stops being busy.
That, and the check that has to happen before any of it, is the same on every model, so it lives
here as functions over a :class:`~.runtime.Runtime` rather than in a class the models inherit.

Checking is not only a check. The pass accumulates what the protocol requires of the peristaltic
pumps, and opening a batch is what makes the hardware match it, so a run that skips the check opens
against whatever happens to be fitted. That is why running a protocol checks it first, and why the
opt-out is a named argument rather than the default.
"""

from __future__ import annotations

import asyncio
import logging

from pylabrobot.agilent.biotek.lhc.devices.batch import batch
from pylabrobot.agilent.biotek.lhc.devices.queries import optional_byte
from pylabrobot.agilent.biotek.lhc.devices.runtime import Runtime
from pylabrobot.agilent.biotek.lhc.enums.motion.carrier_type import CarrierType
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_restriction import PlateRestriction
from pylabrobot.agilent.biotek.lhc.enums.status.run_state import RunState
from pylabrobot.agilent.biotek.lhc.error_handling import ErrorKind, LinkError, fail
from pylabrobot.agilent.biotek.lhc.protocols.protocol import Protocol
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.validation.protocol_pass import validate
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import ValidationReport
from pylabrobot.agilent.biotek.lhc.protocols.validation.reservations import Reservations
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import (
  CommandNumber,
  command_for_step,
)
from pylabrobot.agilent.biotek.lhc.serialization.commands.run_control import (
  AbortStep,
  GetProtocolStatus,
  PauseStep,
  ResumeStep,
  RunStatus,
  RunStep,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.1
"""How long to wait between two status polls, in seconds."""

READY_TIMEOUT = 15.0
"""How long to wait for the instrument to go idle before sending a step, in seconds."""

ABORT_TIMEOUT = 30.0
"""How long to wait for a stopped instrument to come back, in seconds.

Stopping is a motion: the instrument leaves the wire while it homes, which takes it well past the
patience a single exchange has. What is waited for here is the whole of it.
"""

STEP_TIMEOUT = 3600.0
"""How long to wait for a step to finish, in seconds. A wash with a long soak is minutes of it."""

_RUNNING = (RunState.BUSY, RunState.PAUSED)


def steps_of(protocol: Protocol | list[Step]) -> list[Step]:
  """The steps to run, from either a protocol or a list of them.

  A protocol read from a file carries its entries rather than its steps, so the steps are built
  from them the first time they are asked for.

  Args:
    protocol: The protocol, or the steps on their own.

  Returns:
    The steps, in the order they will run.

  Raises:
    ValueError: If the protocol carries a step this package cannot read.
  """
  if isinstance(protocol, list):
    return protocol
  return protocol.steps if protocol.steps else protocol.build_steps()


async def status(runtime: Runtime) -> RunStatus:
  """Ask the instrument what it is doing.

  Args:
    runtime: The device's state.

  Returns:
    The run state, and the phase and countdown of a running step.

  Raises:
    BiotekError: If the instrument reports a fault, which is how a step that failed is learned of.
  """
  command = GetProtocolStatus()
  return command.parse(await runtime.link.request(command, operation="status"))


async def wait_until_idle(
  runtime: Runtime, timeout: float = READY_TIMEOUT, interval: float = POLL_INTERVAL
) -> None:
  """Wait for the instrument to stop being busy.

  Args:
    runtime: The device's state.
    timeout: How long to wait, in seconds.
    interval: How long to wait between polls, in seconds.

  Raises:
    BiotekError: If the instrument is still busy when the time is up, or reports a fault.
  """
  deadline = asyncio.get_running_loop().time() + timeout
  while True:
    if (await status(runtime)).state not in _RUNNING:
      return
    if asyncio.get_running_loop().time() >= deadline:
      raise fail(
        ErrorKind.LINK,
        f"{runtime.link.name} is still busy after {timeout:g}s",
        operation="wait until idle",
      )
    await asyncio.sleep(interval)


async def run_step(
  runtime: Runtime,
  step: Step,
  timeout: float = STEP_TIMEOUT,
  interval: float = POLL_INTERVAL,
) -> None:
  """Run one step inside the batch that is already open, and wait for it to finish.

  Args:
    runtime: The device's state.
    step: The step to run, encoded against what the instrument has fitted.
    timeout: How long to wait for it to finish, in seconds.
    interval: How long to wait between polls, in seconds.

  Raises:
    BiotekError: If the instrument refuses the step, reports a fault while running it, or is still
      running it when the time is up.
    RejectedError: If no plate has been set.
    KeyError: If no command runs that kind of step.
  """
  plate_type = runtime.plate_type
  runtime.aborting = False
  await wait_until_idle(runtime)
  command = RunStep(command_for_step(step), plate_type, step.to_bytes(runtime.settings))
  await runtime.link.request(command, operation=step.step_type.name)
  logger.info("running %s on %s", step.step_type.name, runtime.link.name)
  await asyncio.sleep(runtime.settle)
  await _wait_for_step(runtime, step, timeout, interval)


async def _wait_for_step(runtime: Runtime, step: Step, timeout: float, interval: float) -> None:
  """Poll until a running step finishes.

  Args:
    runtime: The device's state.
    step: The step being waited for, named in any exception raised.
    timeout: How long to wait, in seconds.
    interval: How long to wait between polls, in seconds.

  Raises:
    BiotekError: If the instrument reports a fault, or the step is still running when the time is
      up.
  """
  deadline = asyncio.get_running_loop().time() + timeout
  paused = False
  while True:
    reported = await status(runtime)
    if reported.state not in _RUNNING:
      if runtime.aborting or reported.state is RunState.STOPPED:
        runtime.aborting = False
        raise fail(
          ErrorKind.ABORTED,
          f"{step.step_type.name} was stopped on {runtime.link.name}",
          operation=step.step_type.name,
        )
      logger.info("%s finished on %s", step.step_type.name, runtime.link.name)
      return
    if reported.state is RunState.PAUSED and not paused:
      paused = True
      logger.warning("%s is paused on %s", step.step_type.name, runtime.link.name)
    if asyncio.get_running_loop().time() >= deadline:
      raise fail(
        ErrorKind.LINK,
        f"{step.step_type.name} has not finished on {runtime.link.name} after {timeout:g}s",
        operation=step.step_type.name,
      )
    await asyncio.sleep(interval)


async def run_steps(
  runtime: Runtime,
  steps: list[Step],
  check: bool = True,
  home_on_close: bool = False,
  timeout: float = STEP_TIMEOUT,
) -> None:
  """Check a protocol, then run every step of it in one batch.

  Args:
    runtime: The device's state.
    steps: The steps to run, in order.
    check: Whether to check the protocol first. Leaving this out is what makes the batch open
      against whatever cassette happens to be fitted, since it is the check that works out what the
      protocol requires.
    home_on_close: Whether to home the transport before closing the batch.
    timeout: How long to wait for each step to finish, in seconds.

  Raises:
    BiotekError: If the protocol cannot run, if the hardware cannot be made to match it, or if a
      step fails.
    RejectedError: If no plate has been set.
  """
  if check:
    report = await can_run(runtime, steps)
    if not report:
      raise fail(
        ErrorKind.REJECTED,
        str(report),
        operation="run protocol",
        code=_first_code(report),
      )
  else:
    runtime.reservations = Reservations()
    logger.warning(
      "running %d steps on %s unchecked: the batch will open against the fitted cassettes rather "
      "than the ones the protocol asks for",
      len(steps),
      runtime.link.name,
    )
  async with batch(runtime, home_on_close=home_on_close):
    for step in steps:
      await run_step(runtime, step, timeout=timeout)


async def can_run(runtime: Runtime, steps: list[Step]) -> ValidationReport:
  """Check whether a protocol can run on the instrument as it is.

  The facts the rules are measured against are gathered here -- what is fitted, which plate is on
  the carrier, which rules the firmware runs, and, where the instrument answers for them, which
  plates it accepts and which carrier is on it. What the protocol requires of the pumps is kept for
  the next batch to open against.

  Args:
    runtime: The device's state.
    steps: The steps to check, in the order they would run.

  Returns:
    The report: truthy when every step can run, and printing as the list of those that cannot.

  Raises:
    RejectedError: If no plate has been set.
  """
  plate = runtime.plate_record
  await _read_instrument_facts(runtime)
  report, reservations = validate(
    steps=steps,
    settings=runtime.settings,
    plate=plate,
    rules=runtime.rules,
    plate_restriction=runtime.plate_restriction,
    carrier_type=runtime.carrier_type,
  )
  runtime.reservations = reservations
  logger.debug("%s: %s", runtime.link.name, report)
  return report


async def abort(
  runtime: Runtime, timeout: float = ABORT_TIMEOUT, interval: float = POLL_INTERVAL
) -> None:
  """Stop the running step and wait for the instrument to come back.

  The instrument acknowledges the request, stops where it is, homes itself, and answers nothing
  until it is home, so a poll that finds out whether it stopped can go unanswered for as long as
  that motion lasts. One that does is not a failure here -- it is the instrument being busy -- so
  it is retried until the instrument answers or the time is up.

  Args:
    runtime: The device's state.
    timeout: How long to wait for it to come back, in seconds.
    interval: How long to wait between polls, in seconds.

  Raises:
    BiotekError: If the instrument does not come back, or reports a fault when it does.
  """
  runtime.aborting = True
  try:
    await runtime.link.request(AbortStep(), operation="abort")
  except BaseException:
    # A request that never went out has stopped nothing, and must not have the step it was aimed
    # at reported as stopped.
    runtime.aborting = False
    raise
  deadline = asyncio.get_running_loop().time() + timeout
  while True:
    try:
      if (await status(runtime)).state not in _RUNNING:
        return
    except LinkError:
      logger.debug("%s is not answering while it stops", runtime.link.name)
    if asyncio.get_running_loop().time() >= deadline:
      raise fail(
        ErrorKind.LINK,
        f"{runtime.link.name} was asked to stop and has not come back after {timeout:g}s",
        operation="abort",
      )
    await asyncio.sleep(interval)


async def pause(runtime: Runtime) -> None:
  """Pause the running step.

  Args:
    runtime: The device's state.

  Raises:
    BiotekError: If the instrument will not pause.
  """
  await runtime.link.request(PauseStep(), operation="pause")


async def resume(runtime: Runtime) -> None:
  """Resume a paused step.

  Args:
    runtime: The device's state.

  Raises:
    BiotekError: If the instrument will not resume.
  """
  await runtime.link.request(ResumeStep(), operation="resume")


async def _read_instrument_facts(runtime: Runtime) -> None:
  """Ask the instrument which plates it accepts and which carrier is fitted, once.

  Both are set on the instrument rather than by a protocol, and both are rules of their own. A
  model whose firmware does not answer for one leaves it unknown, and the rule that reads it is
  skipped rather than guessed at.

  Args:
    runtime: The device's state, updated in place.
  """
  if runtime.plate_restriction is None:
    answer = await optional_byte(runtime.link, CommandNumber.GET_PLATE_RESTRICTION)
    if answer is not None:
      runtime.plate_restriction = PlateRestriction(answer)
  if runtime.carrier_type is None:
    answer = await optional_byte(runtime.link, CommandNumber.GET_CARRIER_TYPE)
    if answer is not None:
      runtime.carrier_type = CarrierType(answer)


def _first_code(report: ValidationReport) -> int:
  """The code of the first thing that stops a protocol running.

  Args:
    report: The report to read.

  Returns:
    The code, or zero when the report says nothing is wrong.
  """
  if report.rejection is not None:
    return report.rejection.code
  for step in report.steps:
    if step.rejection is not None:
      return step.rejection.code
  return 0
