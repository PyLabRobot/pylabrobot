"""Proving what is on the line before anything is read off it.

An answered liveness check says something is listening; it does not say what. The firmware version
record read straight after it says two more things -- whether the basecode installed is one built
for the model being driven, and whether the settings data behind it is as new as this package reads
-- so a wrong instrument, or firmware too old for what a protocol will ask of it, is found while
opening rather than part-way through a plate.
"""

from __future__ import annotations

import logging

from pylabrobot.agilent.biotek.lhc.comm.link import Link
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.error_handling import (
  SETTINGS_DATA_TOO_OLD,
  WRONG_BASECODE_PART_NUMBER,
  ErrorKind,
  fail,
)
from pylabrobot.agilent.biotek.lhc.serialization.commands.queries import (
  FirmwareVersion,
  GetFirmwareVersion,
  Ping,
)

logger = logging.getLogger(__name__)

BASECODE_PART_NUMBERS: dict[InstrumentFamily, str] = {
  InstrumentFamily.EL406: "718",
  InstrumentFamily.MULTIFLO: "721",
  InstrumentFamily.MODEL_405_TS: "117",
  InstrumentFamily.MULTIFLO_FX: "126",
}
"""What a basecode part number begins with, per family.

The part number is seven characters and its first three say which family the firmware was built
for, so it is what distinguishes one instrument of this family from another on a link that frames
every model identically. A family absent here is one whose prefix is not known, and is not checked.
"""

SETTINGS_DATA_VERSION = 103.0
"""The oldest instrument settings data version this package reads.

Read as a number rather than compared as text: the field is a decimal that has gained digits over
time, so ``103`` sorts above ``99`` only numerically.
"""


async def check_communications(link: Link) -> FirmwareVersion:
  """Prove something is listening, and that it is the model being driven.

  Two frames: the liveness check, then the firmware version record.

  Args:
    link: The open link to the instrument.

  Returns:
    The version record the instrument answered with.

  Raises:
    BiotekError: If nothing answers, the record cannot be read, the basecode was built for another
      family, or the instrument's settings data is older than the oldest this package reads.
  """
  await link.request(Ping(), operation="ping")
  command = GetFirmwareVersion()
  try:
    version = command.parse(await link.request(command, operation="ping"))
  except ValueError as error:
    # Firmware keeping no record acknowledges the query and answers nothing, which leaves the
    # instrument unidentified: it is on the line, but there is nothing to say it is the right one.
    raise fail(
      ErrorKind.FIRMWARE,
      f"{link.name} did not say which basecode it runs: {error}",
      operation="ping",
      code=WRONG_BASECODE_PART_NUMBER,
    ) from error
  logger.info(
    "%s runs basecode %s, firmware %s, settings data %s",
    link.name,
    version.part_number.strip(),
    version.software_version.strip(),
    version.data_version.strip(),
  )
  expected = BASECODE_PART_NUMBERS.get(link.family)
  if expected is not None and not version.part_number.startswith(expected):
    raise fail(
      ErrorKind.FIRMWARE,
      f"{link.name} runs basecode {version.part_number.strip()}, where a "
      f"{link.family.name} runs one beginning {expected}; this is not the instrument this driver "
      f"drives",
      operation="ping",
      code=WRONG_BASECODE_PART_NUMBER,
    )
  data_version = _as_number(version.data_version)
  if data_version is None:
    logger.warning(
      "%s did not report a settings data version, so how old its basecode is stays unknown",
      link.name,
    )
  elif data_version < SETTINGS_DATA_VERSION:
    raise fail(
      ErrorKind.FIRMWARE,
      f"{link.name} reports settings data version {version.data_version.strip()}, older than the "
      f"{SETTINGS_DATA_VERSION:g} this package reads; its basecode needs updating",
      operation="ping",
      code=SETTINGS_DATA_TOO_OLD,
    )
  return version


def _as_number(field: str) -> float | None:
  """Read a version field that is meant to be a decimal number.

  Args:
    field: The field as the instrument wrote it, space-padded.

  Returns:
    The number, or None for a field that does not hold one.
  """
  try:
    return float(field)
  except ValueError:
    return None
