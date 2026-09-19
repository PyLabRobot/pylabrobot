"""What the tests around here share: labware to work, and an instrument that is not there.

The fake instrument answers a framed request the way a real one does -- acknowledge, header,
payload, checksum -- so everything above the transport is exercised for real: framing, the status
word, the error hierarchy, the send-and-poll strategy and the batch bracket. What it answers is a
table the test sets, so a test can say what is fitted, how long a step takes to finish, or that a
command fails.
"""

from __future__ import annotations

from functools import lru_cache

from pylabrobot.agilent.biotek.lhc.comm.link import ACK, Link
from pylabrobot.agilent.biotek.lhc.comm.transport import Transport
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.status.run_state import RunState
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import (
  STEP_TYPE_TO_COMMAND,
  CommandNumber,
)
from pylabrobot.agilent.biotek.lhc.serialization.frame import HEADER_LENGTH, Header, checksum
from pylabrobot.resources import Plate, Well
from pylabrobot.resources.utils import create_ordered_items_2d

STEP_COMMANDS = frozenset(int(command) for command in STEP_TYPE_TO_COMMAND.values())
"""Every command that runs a step, so the fake can tell one from a query."""

_GRIDS: dict[int, tuple[int, int]] = {
  6: (3, 2),
  24: (6, 4),
  96: (12, 8),
  384: (24, 16),
  1536: (48, 32),
}
_PITCHES: dict[int, float] = {6: 39.0, 24: 19.3, 96: 9.0, 384: 4.5, 1536: 2.25}


@lru_cache(maxsize=None)
def _built(wells: int, well_depth: float, name: str) -> Plate:
  """Build the labware, once per shape.

  Args:
    wells: How many wells it has.
    well_depth: How deep a well is, in mm.
    name: The resource's name.

  Returns:
    The plate.
  """
  columns, rows = _GRIDS[wells]
  pitch = _PITCHES[wells]
  return Plate(
    name=name,
    size_x=127.0,
    size_y=85.0,
    size_z=max(14.0, well_depth + 3.0),
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=columns,
      num_items_y=rows,
      dx=10.0,
      dy=7.0,
      dz=1.0,
      item_dx=pitch,
      item_dy=pitch,
      size_x=pitch * 0.75,
      size_y=pitch * 0.75,
      size_z=well_depth,
    ),
  )


def make_plate(wells: int = 96, well_depth: float = 10.9, name: str = "plate") -> Plate:
  """Labware of a given well count for a test.

  Args:
    wells: How many wells it has, which decides its columns and rows.
    well_depth: How deep a well is, in mm. Past the deep-well threshold this is what makes the
      plate resolve to a deep-well format.
    name: The resource's name.

  Returns:
    The plate.

  Raises:
    KeyError: If no grid is defined for that well count.
  """
  return _built(wells, well_depth, name)


def fitted_el406() -> InstrumentSettings:
  """A fully equipped instrument of the family's original model.

  Returns:
    The record, which is what the step encoders and the rule set are measured against.
  """
  return InstrumentSettings(family=InstrumentFamily.EL406)


class FakeInstrument(Transport):
  """A transport that answers framed requests without an instrument behind it.

  Args:
    answers: What to answer each command with, as the payload after the status word. A command not
      named here is answered with the status word alone.
    busy_polls: How many status polls report a running step before one reports it finished.
    status: The status word to answer with. Anything but zero is an error the link raises for.
    busy_after_step: Whether to report a running step forever once one has been sent, which is what
      a step that never finishes looks like. The polls before it still report the instrument idle,
      so a caller gets as far as sending the step.
    stops: Whether to report itself stopped rather than ready once it is no longer running, which
      is what a step stopped from the instrument's own keypad looks like.

  Attributes:
    sent: Every command number written, in order, so a test can assert what was sent and when.
    payloads: The payload of every command written, in the same order.
  """

  def __init__(
    self,
    answers: dict[CommandNumber, bytes] | None = None,
    busy_polls: int = 0,
    status: int = 0,
    busy_after_step: bool = False,
    stops: bool = False,
  ) -> None:
    super().__init__(port="fake", timeout=1.0)
    self.answers = dict(answers) if answers else {}
    self.busy_polls = busy_polls
    self.status = status
    self.busy_after_step = busy_after_step
    self.stops = stops
    self._step_sent = False
    self.sent: list[int] = []
    self.payloads: list[bytes] = []
    self.is_open = False
    self._pending: Header | None = None
    self._out = bytearray()
    self._polls = 0

  async def setup(self) -> None:
    """Open the fake link."""
    self.is_open = True

  async def stop(self) -> None:
    """Close the fake link."""
    self.is_open = False

  async def purge(self) -> None:
    """Discard whatever is waiting to be read."""
    self._out.clear()

  async def write(self, data: bytes) -> None:
    """Take a header, then a payload if the header declared one, and queue the answer.

    Args:
      data: The bytes written.
    """
    if self._pending is None:
      header = Header.from_bytes(data[:HEADER_LENGTH])
      if header.payload_length:
        self._pending = header
        return
      self._answer(header, b"")
      return
    header, self._pending = self._pending, None
    self._answer(header, bytes(data))

  async def read(self, num_bytes: int = 1) -> bytes:
    """Read from what has been queued.

    Args:
      num_bytes: The most bytes to return.

    Returns:
      What was waiting, up to that many bytes.
    """
    taken = bytes(self._out[:num_bytes])
    del self._out[:num_bytes]
    return taken

  def _answer(self, header: Header, payload: bytes) -> None:
    """Queue the reply to one command.

    Args:
      header: The request's header.
      payload: The request's payload.
    """
    self.sent.append(header.number)
    self.payloads.append(payload)
    if header.number == int(CommandNumber.ABORT_STEP):
      # Stopping ends the step, so an instrument that was reporting one no longer does.
      self._step_sent = False
    self._out += bytes([ACK]) + self._reply(header.number)

  def _reply(self, number: int) -> bytes:
    """Frame the reply to one command.

    Args:
      number: Which command is being answered.

    Returns:
      The reply header followed by its payload.
    """
    answer = self._answer_for(number)
    payload = self.status.to_bytes(2, "little") + answer
    header = Header(number=number, payload_length=len(payload))
    header.check = checksum(header.to_bytes(), payload)
    return header.to_bytes() + payload

  def _answer_for(self, number: int) -> bytes:
    """What to answer a command with, after its status word.

    Args:
      number: Which command is being answered.

    Returns:
      The answer, which is empty for a command that reports only a status.
    """
    if number in STEP_COMMANDS:
      self._step_sent = True
    if number == CommandNumber.GET_PROTOCOL_STATUS:
      self._polls += 1
      running = self._polls <= self.busy_polls or (self.busy_after_step and self._step_sent)
      if running:
        state = RunState.BUSY
      else:
        state = RunState.STOPPED if self.stops else RunState.READY
      return int(state).to_bytes(2, "little") + (0).to_bytes(4, "little") + bytes([0])
    for command, answer in self.answers.items():
      if int(command) == number:
        return answer
    return b""

  def payload_of(self, command: CommandNumber) -> bytes:
    """The payload of the first request for a command.

    Args:
      command: Which command to look for.

    Returns:
      Its payload.

    Raises:
      AssertionError: If that command was never sent.
    """
    for number, payload in zip(self.sent, self.payloads):
      if number == int(command):
        return payload
    raise AssertionError(f"{command.name} was never sent; sent {self.sent}")


ACCEPTS_EVERY_PLATE: dict[CommandNumber, bytes] = {
  CommandNumber.GET_PLATE_RESTRICTION: bytes([0]),
  CommandNumber.GET_CARRIER_TYPE: bytes([0]),
}
"""An instrument that accepts every plate and has the ordinary carrier fitted.

Both are read by every check, so a fake that does not answer them is an instrument whose firmware
does not implement those queries -- which is worth testing, but not what most tests are about.
"""


def fake_link(
  answers: dict[CommandNumber, bytes] | None = None,
  busy_polls: int = 0,
  status: int = 0,
  family: InstrumentFamily = InstrumentFamily.EL406,
  busy_after_step: bool = False,
  io: FakeInstrument | None = None,
) -> tuple[Link, FakeInstrument]:
  """A link onto a fake instrument, and the instrument itself.

  Args:
    answers: What to answer each command with.
    busy_polls: How many polls report a running step before one reports it finished.
    status: The status word to answer with.
    family: Which family the link decodes error codes for.
    busy_after_step: Whether a step, once sent, never finishes.
    io: A transport to use instead of a plain fake, for a test that needs one that misbehaves.

  Returns:
    The link, unopened, and the transport behind it.
  """
  if io is None:
    io = FakeInstrument(
      answers=answers, busy_polls=busy_polls, status=status, busy_after_step=busy_after_step
    )
  return Link(port="fake", family=family, name="fake instrument", timeout=1.0, io=io), io
