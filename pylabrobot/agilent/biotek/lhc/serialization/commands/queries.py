"""Commands that read what the instrument is and what it is doing."""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.agilent.biotek.lhc.serialization.command import Command
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber

_SERIAL_NUMBER_LENGTH = 24
_VERSION_RECORD_LENGTH = 46
_PING_TIMEOUT = 5.0


class GetSerialNumber(Command):
  """Read the instrument's serial number."""

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.GET_SERIAL_NUMBER, answer_length=_SERIAL_NUMBER_LENGTH)

  def parse(self, answer: bytes) -> str:
    """Read the serial number out of a reply.

    Args:
      answer: The reply answer.

    Returns:
      The serial number, without trailing padding.
    """
    return answer.decode("latin-1").strip()


class Ping(Command):
  """Ask whether anything is listening.

  Only the status matters. The timeout is short so that an absent instrument fails quickly, and a
  reply proves something is on the line but not what it is -- reading the firmware version is what
  proves that.
  """

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(number=CommandNumber.PING, timeout=_PING_TIMEOUT)


@dataclass
class FirmwareVersion:
  """The firmware version record, whose halves are the instrument's two processors.

  Attributes:
    part_number: The firmware part number, which says which instrument this build is for.
    software_version: The firmware version.
    ui_checksum: Checksum of the user-interface processor's firmware.
    mc_checksum: Checksum of the motor controller's firmware.
    data_version: Version of the instrument's settings data.
    ui_version: Version of the user-interface processor's firmware.
    mc_version: Version of the motor controller's firmware.
  """

  part_number: str
  software_version: str
  ui_checksum: str
  mc_checksum: str
  data_version: str
  ui_version: str
  mc_version: str


class GetFirmwareVersion(Command):
  """Read the firmware version record."""

  def __init__(self) -> None:
    """Build the command."""
    super().__init__(
      number=CommandNumber.GET_BASECODE_VERSION, answer_length=_VERSION_RECORD_LENGTH
    )

  def parse(self, answer: bytes) -> FirmwareVersion:
    """Read the record out of a reply.

    Args:
      answer: The reply answer.

    Returns:
      The record, whose fields are fixed-width and run together.

    Raises:
      ValueError: If the reply is too short to hold the record.
    """
    if len(answer) < _VERSION_RECORD_LENGTH:
      raise ValueError(
        f"firmware version record is {len(answer)} bytes, expected {_VERSION_RECORD_LENGTH}"
      )
    text = answer.decode("latin-1")
    widths = (7, 8, 4, 4, 5, 3, 3)
    fields = []
    at = 0
    for width in widths:
      fields.append(text[at : at + width])
      at += width
    return FirmwareVersion(
      part_number=fields[0],
      software_version=fields[1],
      ui_checksum="0x" + fields[2],
      mc_checksum="0x" + fields[3],
      data_version=fields[4],
      ui_version=fields[5],
      mc_version=fields[6],
    )
