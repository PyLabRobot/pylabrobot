"""Commands that open a batch, run steps inside it and report on them."""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.enums.status.activity import Activity
from pylabrobot.agilent.biotek.lhc.enums.status.run_state import RunState
from pylabrobot.agilent.biotek.lhc.serialization.command import Command
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber

_INIT_TIMEOUT = 32.0
_EXIT_TIMEOUT = 60.0
_STATUS_LENGTH = 7


class InitProtocol(Command):
  """Open a batch, telling the instrument which plate it is working with.

  The instrument homes its motors before answering, so this takes a while. A successful call is
  what puts the instrument in a state where steps may be sent.
  """

  def __init__(self, plate_type: PlateType) -> None:
    """Build the command.

    Args:
      plate_type: The plate the batch runs on.
    """
    super().__init__(
      number=CommandNumber.INIT_PROTOCOL,
      payload=bytes([int(plate_type)]),
      timeout=_INIT_TIMEOUT,
    )


class ExitProtocol(Command):
  """Close a batch."""

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.EXIT_PROTOCOL, timeout=_EXIT_TIMEOUT)


class AbortStep(Command):
  """Stop the running step.

  Acknowledged and then not answered: the instrument stops what it is doing and homes itself, and
  says nothing at all until it is home. Whether it stopped is learned by asking afterwards.
  """

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.ABORT_STEP, expects_reply=False)


class PauseStep(Command):
  """Pause the running step."""

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.PAUSE_STEP)


class ResumeStep(Command):
  """Resume a paused step."""

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.RESUME_STEP)


class RunStep(Command):
  """Run one step.

  The frame is the same for every step type; only the command number and the payload differ. The
  reply comes as soon as the instrument has accepted the step, so a status poll is what says when
  it has finished.
  """

  def __init__(self, number: CommandNumber, plate_type: PlateType, payload: bytes) -> None:
    """Build the command.

    Args:
      number: The command that runs this step type.
      plate_type: The plate the batch runs on.
      payload: The step's own encoded parameters.
    """
    super().__init__(number=number, payload=bytes([int(plate_type)]) + payload)


@dataclass
class RunStatus:
  """What a status poll reports.

  The countdown and the phase go together, and both are empty most of the time: a dispense, an
  aspirate, a prime, or a wash between its stages counts nothing down and reports no phase. A
  countdown is not how far through the step the instrument is -- it is the time left in the one
  phase it is in, so a step that ends in a soak reports 0 until it reaches the soak.

  Attributes:
    state: What the instrument is doing.
    remaining: Seconds left in the timed phase the instrument is in, and 0 whenever it is in none
      -- which is most of every ordinary step.
    activity: Which timed phase the step is in, and ``NONE`` whenever it is in none.
  """

  state: RunState = RunState.READY
  remaining: int = 0
  activity: Activity = Activity.NONE


class GetProtocolStatus(Command):
  """Ask whether the running step has finished."""

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.GET_PROTOCOL_STATUS)

  def parse(self, answer: bytes) -> RunStatus:
    """Read the status out of a reply.

    Args:
      answer: The reply answer: two bytes of state, four of remaining seconds, one of activity.

    Returns:
      The status.

    Raises:
      ValueError: If the reply is too short, or reports a state or activity that does not exist.
    """
    if len(answer) < _STATUS_LENGTH:
      raise ValueError(f"status reply is {len(answer)} bytes, expected {_STATUS_LENGTH}")
    return RunStatus(
      state=RunState(int.from_bytes(answer[0:2], "little", signed=True)),
      remaining=int.from_bytes(answer[2:6], "little"),
      activity=Activity(answer[6]),
    )
