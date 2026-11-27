"""Commands that read and write what the instrument has fitted.

Most of them share one of four wire shapes, so the shapes are classes in their own right and each
fitted option is one of them with a different command number.
"""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.agilent.biotek.lhc.serialization.command import Command
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber


class ByteQuery(Command):
  """A command that sends nothing and reads back one byte."""

  def __init__(self, number: CommandNumber) -> None:
    """Build the command.

    Args:
      number: Which command to send.
    """
    super().__init__(number=number)

  def parse(self, answer: bytes) -> int:
    """Read the byte out of a reply.

    Args:
      answer: The reply answer.

    Returns:
      The byte.

    Raises:
      ValueError: If the reply carried no answer. An instrument that acknowledges a query it does
        not implement answers with its status and nothing else, so this is how "it would not say"
        reaches a caller that can carry on without knowing.
    """
    if not answer:
      raise ValueError(f"command {self.number} was answered with no value")
    return answer[0]


class FlagQuery(ByteQuery):
  """A command that sends nothing and reads back one byte meaning yes or no."""

  def parse_flag(self, answer: bytes) -> bool:
    """Read the byte out of a reply as a flag.

    Args:
      answer: The reply answer.

    Returns:
      Whether the byte is set.

    Raises:
      ValueError: If the reply carried no answer.
    """
    return bool(self.parse(answer))


class SelectorQuery(Command):
  """A command that sends one byte selecting what to ask about and reads back one byte."""

  def __init__(self, number: CommandNumber, selector: int) -> None:
    """Build the command.

    Args:
      number: Which command to send.
      selector: What to ask about -- a pump, a sensor, or whatever the command selects.
    """
    super().__init__(number=number, payload=bytes([selector]))

  def parse(self, answer: bytes) -> int:
    """Read the byte out of a reply.

    Args:
      answer: The reply answer.

    Returns:
      The byte.

    Raises:
      ValueError: If the reply carried no answer.
    """
    if not answer:
      raise ValueError(f"command {self.number} was answered with no value")
    return answer[0]

  def parse_flag(self, answer: bytes) -> bool:
    """Read the byte out of a reply as a flag.

    Args:
      answer: The reply answer.

    Returns:
      Whether the byte is set.

    Raises:
      ValueError: If the reply carried no answer.
    """
    return bool(self.parse(answer))


class SelectorWrite(Command):
  """A command that sends a selector byte and a value byte, and reads back only the status."""

  def __init__(self, number: CommandNumber, selector: int, value: int) -> None:
    """Build the command.

    Args:
      number: Which command to send.
      selector: What to write to.
      value: The byte to write.
    """
    super().__init__(number=number, payload=bytes([selector, value]))


@dataclass
class SyringeBox:
  """What syringe box is fitted.

  Attributes:
    box_type: Which kind of box it is.
    box_size: How many bottles it holds.
  """

  box_type: int
  box_size: int


class GetSyringeBoxInfo(Command):
  """Read which syringe box is fitted."""

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.GET_SYRINGE_BOX_INFO)

  def parse(self, answer: bytes) -> SyringeBox:
    """Read the two bytes out of a reply.

    Args:
      answer: The reply answer.

    Returns:
      The box type and size.

    Raises:
      ValueError: If the reply is too short.
    """
    if len(answer) < 2:
      raise ValueError(f"syringe box reply is {len(answer)} bytes, expected 2")
    return SyringeBox(box_type=answer[0], box_size=answer[1])
