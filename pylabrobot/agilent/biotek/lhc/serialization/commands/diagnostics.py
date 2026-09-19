"""Commands that exercise the instrument rather than read from it.

Each one answers only when it has finished, so each carries a timeout long enough for the motion
it performs.
"""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.serialization.command import Command
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber

_RESET_TIMEOUT = 30.0
_SELF_CHECK_TIMEOUT = 90.0
_HOME_TIMEOUT = 32.0


class ResetInstrument(Command):
  """Reset the instrument."""

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.RESET_INSTRUMENT, timeout=_RESET_TIMEOUT)


class RunSelfCheck(Command):
  """Run the instrument's self-check.

  The result is the status rather than an answer: a passing check reports success, and a failing
  one reports the fault as its status code.
  """

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.RUN_SELF_CHECK, timeout=_SELF_CHECK_TIMEOUT)


class HomeVerifyMotors(Command):
  """Home a motor and verify that it reached its home position."""

  def __init__(self, home_type: int, motor: int) -> None:
    """Build the command.

    Args:
      home_type: Which homing routine to run.
      motor: Which motor to home.
    """
    super().__init__(
      number=CommandNumber.HOME_VERIFY_MOTORS,
      payload=bytes([home_type, motor]),
      timeout=_HOME_TIMEOUT,
    )
