"""Running a step, and the batch it runs inside.

The instrument here is a fake that answers real frames, so what is exercised is the whole path above
the transport: the batch bracket, the send-and-poll strategy, the status word turning into an
exception, and the check that decides whether a protocol may run at all.
"""

from __future__ import annotations

import asyncio
import unittest

from pylabrobot.agilent.biotek.lhc.devices import execution
from pylabrobot.agilent.biotek.lhc.devices.batch import batch
from pylabrobot.agilent.biotek.lhc.devices.build_rules import rules_for
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.devices.runtime import Runtime
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_type import SyringeBoxType
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_manifold import SyringeManifold
from pylabrobot.agilent.biotek.lhc.enums.status.run_state import RunState
from pylabrobot.agilent.biotek.lhc.error_handling import (
  AbortedError,
  BiotekError,
  RejectedError,
)
from pylabrobot.agilent.biotek.lhc.plate_geometry.resolution import resolve
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_prime import ManifoldPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_prime import SyringePrime
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber
from pylabrobot.agilent.biotek.lhc.serialization.frame import HEADER_LENGTH, Header
from pylabrobot.agilent.biotek.lhc.tests.helpers import (
  ACCEPTS_EVERY_PLATE,
  FakeInstrument,
  fake_link,
  make_plate,
)

FAMILY = InstrumentFamily.EL406

_STARTUP_POLLS = 100
"""How many turns of the loop to give a step to reach the wire."""


class Homing(FakeInstrument):
  """A fake that leaves one status poll unanswered, the way an instrument moving its motors does.

  Args:
    **kwargs: Passed to the plain fake.

  Attributes:
    ignored: How many more polls to leave unanswered.
  """

  def __init__(self, **kwargs) -> None:
    super().__init__(**kwargs)
    self.ignored = 1

  async def write(self, data: bytes) -> None:
    """Take a frame, and answer it unless it is the poll being ignored.

    Args:
      data: The bytes written.
    """
    header = Header.from_bytes(data[:HEADER_LENGTH])
    if header.number == int(CommandNumber.GET_PROTOCOL_STATUS) and self.ignored:
      self.ignored -= 1
      self.sent.append(header.number)
      return
    await super().write(data)


class ExecutionTestCase(unittest.IsolatedAsyncioTestCase):
  """A device's state wired to a fake instrument, opened and ready to run steps."""

  async def opened(
    self,
    busy_polls: int = 0,
    status: int = 0,
    with_plate: bool = True,
    busy_after_step: bool = False,
    answers: dict | None = None,
    io: FakeInstrument | None = None,
  ) -> tuple[Runtime, FakeInstrument]:
    """Build the state and open the link.

    Args:
      busy_polls: How many polls report a running step before one reports it finished.
      status: The status word the instrument answers with. Anything but zero is a failure.
      with_plate: Whether a plate is on the carrier.
      busy_after_step: Whether a step, once sent, never finishes.
      answers: What the instrument answers, defaulting to one that accepts every plate.
      io: A transport to use instead of a plain fake.

    Returns:
      The state and the fake instrument behind it.
    """
    link, io = fake_link(
      answers=ACCEPTS_EVERY_PLATE if answers is None else answers,
      busy_polls=busy_polls,
      status=status,
      family=FAMILY,
      busy_after_step=busy_after_step,
      io=io,
    )
    state = Runtime(
      link=link,
      rules=rules_for(FAMILY),
      reported_settings=InstrumentSettings(family=FAMILY),
      settle=0,
    )
    if with_plate:
      state.plate = resolve(make_plate(96), FAMILY)
    await link.setup()
    return state, io

  def count(self, io: FakeInstrument, command: CommandNumber) -> int:
    """How many times a command was sent.

    Args:
      io: The fake instrument.
      command: The command to count.

    Returns:
      The count.
    """
    return io.sent.count(int(command))


class TestRunningAStep(ExecutionTestCase):
  """Sending one step and waiting for it."""

  async def test_a_step_is_sent_with_the_plate_in_front_of_it(self):
    """A step is sent with the plate in front of it."""
    state, io = await self.opened()
    async with batch(state):
      await execution.run_step(state, ManifoldPrime(volume=40_000))
    payload = io.payload_of(CommandNumber.MANIFOLD_PRIME)
    self.assertEqual(payload[0], int(state.plate_type))
    self.assertEqual(payload[1:], ManifoldPrime(volume=40_000).to_bytes(state.settings))

  async def test_a_step_is_polled_until_it_stops_being_busy(self):
    """A step is polled until it stops being busy."""
    state, io = await self.opened(busy_polls=2)
    async with batch(state):
      await execution.run_step(state, ManifoldPrime(), interval=0)
    # One poll finds the instrument idle before sending, then two report it busy and one finished.
    self.assertGreaterEqual(self.count(io, CommandNumber.GET_PROTOCOL_STATUS), 4)

  async def test_a_step_that_never_finishes_gives_up(self):
    """The instrument takes the step and then reports it running forever."""
    state, _ = await self.opened(busy_after_step=True)
    async with batch(state):
      with self.assertRaisesRegex(BiotekError, "has not finished"):
        await execution.run_step(state, ManifoldPrime(), timeout=0, interval=0)

  async def test_an_instrument_that_never_goes_idle_is_not_sent_a_step(self):
    """A step is only sent to an idle instrument, so one that stays busy gives up first -- and the
    step never reaches the wire."""
    state, io = await self.opened(busy_polls=1000)
    async with batch(state):
      with self.assertRaisesRegex(BiotekError, "still busy"):
        await execution.wait_until_idle(state, timeout=0, interval=0)
    self.assertNotIn(int(CommandNumber.MANIFOLD_PRIME), io.sent)

  async def test_a_status_the_instrument_reports_becomes_an_exception(self):
    """A non-zero status word is the instrument refusing or failing, and it is raised as the kind of
    failure it is rather than returned."""
    state, _ = await self.opened(status=0x6029)
    with self.assertRaises(RejectedError):
      await execution.status(state)

  async def test_nothing_runs_without_a_plate(self):
    """Nothing runs without a plate."""
    state, _ = await self.opened(with_plate=False)
    with self.assertRaisesRegex(RejectedError, "no plate"):
      await execution.run_step(state, ManifoldPrime())

  async def test_the_status_reports_what_the_instrument_is_doing(self):
    """The status reports what the instrument is doing."""
    state, _ = await self.opened()
    self.assertIs((await execution.status(state)).state, RunState.READY)


class TestTheBatchBracket(ExecutionTestCase):
  """Opening and closing the batch a step has to run inside."""

  async def test_a_batch_opens_and_closes_around_the_block(self):
    """A batch opens and closes around the block."""
    state, io = await self.opened()
    async with batch(state):
      self.assertTrue(state.in_batch)
    self.assertFalse(state.in_batch)
    self.assertLess(
      io.sent.index(int(CommandNumber.INIT_PROTOCOL)),
      io.sent.index(int(CommandNumber.EXIT_PROTOCOL)),
    )

  async def test_a_batch_inside_a_batch_does_nothing(self):
    """A batch inside a batch does nothing."""
    state, io = await self.opened()
    async with batch(state):
      async with batch(state):
        self.assertTrue(state.in_batch)
      # The inner block must not have closed the outer one.
      self.assertTrue(state.in_batch)
      self.assertEqual(self.count(io, CommandNumber.EXIT_PROTOCOL), 0)
    self.assertEqual(self.count(io, CommandNumber.INIT_PROTOCOL), 1)
    self.assertEqual(self.count(io, CommandNumber.EXIT_PROTOCOL), 1)

  async def test_a_batch_closes_even_when_the_block_fails(self):
    """A batch closes even when the block fails."""
    state, io = await self.opened()
    with self.assertRaises(RuntimeError):
      async with batch(state):
        raise RuntimeError("the step went wrong")
    self.assertEqual(self.count(io, CommandNumber.EXIT_PROTOCOL), 1)
    self.assertFalse(state.in_batch)
    self.assertFalse(state.port.locked())

  async def test_the_instrument_is_released_when_the_batch_cannot_open(self):
    """The instrument is released when the batch cannot open."""
    state, _ = await self.opened(status=0x6029)
    with self.assertRaises(BiotekError):
      async with batch(state):
        pass
    self.assertFalse(state.port.locked())
    self.assertFalse(state.in_batch)

  async def test_homing_before_the_close_is_asked_for_not_assumed(self):
    """Homing before the close is asked for not assumed."""
    state, io = await self.opened()
    async with batch(state):
      pass
    self.assertEqual(self.count(io, CommandNumber.HOME_VERIFY_MOTORS), 0)

    state, io = await self.opened()
    async with batch(state, home_on_close=True):
      pass
    self.assertLess(
      io.sent.index(int(CommandNumber.HOME_VERIFY_MOTORS)),
      io.sent.index(int(CommandNumber.EXIT_PROTOCOL)),
    )


class TestCheckingAProtocol(ExecutionTestCase):
  """The pass that decides whether a protocol may run, and what skipping it costs."""

  async def test_a_protocol_is_checked_before_the_batch_opens(self):
    """A protocol is checked before the batch opens."""
    state, io = await self.opened()
    await execution.run_steps(state, [ManifoldPrime(volume=40_000)])
    # What the instrument accepts is read as part of the check, so before the open.
    self.assertLess(
      io.sent.index(int(CommandNumber.GET_PLATE_RESTRICTION)),
      io.sent.index(int(CommandNumber.INIT_PROTOCOL)),
    )

  async def test_a_protocol_that_cannot_run_is_refused_before_anything_moves(self):
    """A syringe prime on an instrument with no syringe box cannot run, and the batch is never
    opened for it."""
    state, io = await self.opened()
    state.reported_settings = InstrumentSettings(
      family=FAMILY,
      syringe_box=SyringeBoxType.NOT_INSTALLED,
      syringe_manifold=SyringeManifold.NOT_INSTALLED,
    )
    with self.assertRaises(RejectedError):
      await execution.run_steps(state, [SyringePrime()])
    self.assertEqual(self.count(io, CommandNumber.INIT_PROTOCOL), 0)

  async def test_skipping_the_check_gives_up_what_the_check_reserved(self):
    """The check is also what works out which cassette each pump needs, so a run that skips it
    opens its batch against whatever is fitted."""
    state, _ = await self.opened()
    await execution.run_steps(state, [ManifoldPrime(volume=40_000)], check=False)
    self.assertIsNone(state.reservations.cassette_primary)
    self.assertFalse(state.reservations.uses_primary)

  async def test_the_check_reports_rather_than_raises(self):
    """The check reports rather than raises."""
    state, _ = await self.opened()
    report = await execution.can_run(state, [ManifoldPrime(volume=40_000)])
    self.assertTrue(report)
    self.assertIn("can run", str(report))

  async def test_what_the_instrument_accepts_is_read_once(self):
    """What the instrument accepts is read once."""
    state, io = await self.opened()
    await execution.can_run(state, [ManifoldPrime()])
    await execution.can_run(state, [ManifoldPrime()])
    self.assertEqual(self.count(io, CommandNumber.GET_PLATE_RESTRICTION), 1)

  async def test_a_new_plate_makes_the_next_check_ask_again(self):
    """A new plate makes the next check ask again."""
    state, io = await self.opened()
    await execution.can_run(state, [ManifoldPrime()])
    state.forget_instrument_facts()
    await execution.can_run(state, [ManifoldPrime()])
    self.assertEqual(self.count(io, CommandNumber.GET_PLATE_RESTRICTION), 2)


class TestRunControl(ExecutionTestCase):
  """Stopping a running step, and letting it go on."""

  async def test_abort_sends_its_own_command_and_then_asks_whether_it_worked(self):
    """Abort sends its own command and then asks whether it worked."""
    state, io = await self.opened()
    await execution.abort(state)
    self.assertEqual(
      io.sent, [int(CommandNumber.ABORT_STEP), int(CommandNumber.GET_PROTOCOL_STATUS)]
    )

  async def test_abort_waits_for_an_instrument_that_is_still_stopping(self):
    """Abort waits for an instrument that is still stopping."""
    state, io = await self.opened(busy_polls=3)
    await execution.abort(state, interval=0)
    self.assertGreaterEqual(io.sent.count(int(CommandNumber.GET_PROTOCOL_STATUS)), 4)

  async def test_abort_retries_a_poll_that_goes_unanswered(self):
    """Abort retries a poll that goes unanswered, which is what homing looks like."""
    io = Homing(answers=ACCEPTS_EVERY_PLATE)
    state, _ = await self.opened(io=io)
    await execution.abort(state, interval=0)
    self.assertEqual(io.ignored, 0)
    self.assertEqual(io.sent[-1], int(CommandNumber.GET_PROTOCOL_STATUS))

  async def test_abort_gives_up_on_an_instrument_that_never_comes_back(self):
    """Abort gives up on an instrument that never comes back."""
    state, _ = await self.opened(busy_polls=1000)
    with self.assertRaisesRegex(BiotekError, "has not come back"):
      await execution.abort(state, timeout=0, interval=0)

  async def test_the_step_that_was_stopped_reports_that_it_was(self):
    """The step that was stopped reports that it was, rather than reporting it finished."""
    state, io = await self.opened(busy_after_step=True)
    async with batch(state):
      running = asyncio.create_task(execution.run_step(state, ManifoldPrime(), interval=0))
      for _ in range(_STARTUP_POLLS):
        if int(CommandNumber.MANIFOLD_PRIME) in io.sent:
          break
        await asyncio.sleep(0)
      await execution.abort(state, interval=0)
      with self.assertRaises(AbortedError):
        await running

  async def test_an_instrument_stopped_from_its_keypad_reports_that_too(self):
    """An instrument stopped from its keypad reports that too, with nothing having asked it to."""
    state, _ = await self.opened(io=FakeInstrument(answers=ACCEPTS_EVERY_PLATE, stops=True))
    async with batch(state):
      with self.assertRaises(AbortedError):
        await execution.run_step(state, ManifoldPrime(), interval=0)

  async def test_a_new_step_is_not_a_stopped_one(self):
    """A new step is not a stopped one, whatever was asked of the last."""
    state, _ = await self.opened()
    state.aborting = True
    async with batch(state):
      await execution.run_step(state, ManifoldPrime(), interval=0)
    self.assertFalse(state.aborting)

  async def test_pause_sends_its_own_command(self):
    """Pause sends its own command."""
    state, io = await self.opened()
    await execution.pause(state)
    self.assertEqual(io.sent, [int(CommandNumber.PAUSE_STEP)])

  async def test_resume_sends_its_own_command(self):
    """Resume sends its own command."""
    state, io = await self.opened()
    await execution.resume(state)
    self.assertEqual(io.sent, [int(CommandNumber.RESUME_STEP)])
