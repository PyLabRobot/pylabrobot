"""Reading one value off an instrument.

Two callers ask an instrument for single values -- the fitted-options read and the check that
measures a protocol against what the instrument accepts -- and both need the same two shapes: a
value that must be there, and one the instrument may decline to give. An older firmware answers a
query it does not implement by acknowledging it and sending nothing back, so "would not say" is an
ordinary answer rather than a failure, and telling the two apart in one place is what keeps the
callers from each having their own idea of it.
"""

from __future__ import annotations

import logging

from pylabrobot.agilent.biotek.lhc.comm.link import Link
from pylabrobot.agilent.biotek.lhc.error_handling import BiotekError
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber
from pylabrobot.agilent.biotek.lhc.serialization.commands.configuration import ByteQuery

logger = logging.getLogger(__name__)


async def byte(link: Link, number: CommandNumber) -> int:
  """Read a one-byte value.

  Args:
    link: The open link to the instrument.
    number: Which value to read.

  Returns:
    The byte.

  Raises:
    BiotekError: If the instrument refuses the query.
    ValueError: If it answers without a value.
  """
  command = ByteQuery(number)
  return command.parse(await link.request(command, operation=number.name))


async def flag(link: Link, number: CommandNumber) -> bool:
  """Read a value that is yes or no.

  Args:
    link: The open link to the instrument.
    number: Which value to read.

  Returns:
    Whether it is set.

  Raises:
    BiotekError: If the instrument refuses the query.
    ValueError: If it answers without a value.
  """
  return bool(await byte(link, number))


async def optional_byte(link: Link, number: CommandNumber) -> int | None:
  """Read a one-byte value the instrument may not have.

  Args:
    link: The open link to the instrument.
    number: Which value to read.

  Returns:
    The byte, or None when the instrument would not say -- either refusing the query, or answering
    it with no value, which is what firmware without that query does.
  """
  try:
    return await byte(link, number)
  except (BiotekError, ValueError) as error:
    logger.debug("%s does not answer %s: %s", link.name, number.name, error)
    return None


async def optional_flag(link: Link, number: CommandNumber) -> bool | None:
  """Read a yes-or-no value the instrument may not have.

  Args:
    link: The open link to the instrument.
    number: Which value to read.

  Returns:
    Whether it is set, or None when the instrument would not say.
  """
  answer = await optional_byte(link, number)
  return None if answer is None else bool(answer)


async def answers(link: Link, number: CommandNumber) -> bool:
  """Whether the instrument answers a query at all, rather than what it answers.

  One option is reported this way: firmware that has it answers, and firmware that does not refuses.

  Args:
    link: The open link to the instrument.
    number: Which query to send.

  Returns:
    Whether it was answered.
  """
  return await optional_byte(link, number) is not None
