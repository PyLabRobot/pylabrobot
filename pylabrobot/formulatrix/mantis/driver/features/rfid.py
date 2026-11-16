"""The RFID reader: which chip sits in each input slot.

A PPI write (board 0, command 41) at address 120 selects the slot's antenna; a TRF7970A reader on
the FTDI's interface A then reads the tag in that antenna's field, 24 blocks of 4 bytes. A slot
holds a chip when a whole tag is read.
"""

import asyncio
import logging
import re
import struct
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..errors import MantisConfigurationError, MantisNotConnectedError, MantisTimeoutError
from ..framing import MOTION_BOARD

if TYPE_CHECKING:
  from pylabrobot.io.ftdi import FTDI

  from ..master import MantisDriver

logger = logging.getLogger(__name__)

# The reader's link: interface A of the device's FTDI bridge, 115200 baud, 8N1.
RFID_FTDI_INTERFACE = 1
RFID_BAUD_RATE = 115_200
# The reader's commands, ASCII hex as sent: ping, external antenna, ISO15693 at full power, and a
# read of the tag's 24 blocks.
RFID_PING_DEVICE_COMMAND = b"0108000304FF0000"
RFID_SET_ANTENNA_MODE_COMMAND = b"01080003042B0000"
RFID_SET_UP_ISO15693_PROTOCOL_COMMAND = b"010C00030410002101000000"
RFID_READ_TAG_COMMAND = b"010C00030418002300170000"
RFID_TAG_BLOCK_COUNT = 24
RFID_REPLY_TIMEOUT = 1.0  # s
# The PPI address of the antenna multiplexer on board 0; the valve bank sits elsewhere.
RFID_MULTIPLEXER_BASE_INDEX = 120
# The chip type each tag code names; 0x7FFF marks a tag whose chip type is invalid.
RFID_CHIP_TYPE_CODES: Dict[int, str] = {
  0x00: "high_volume",
  0x01: "low_volume",
  0x02: "high_volume_pfe",
  0x03: "low_volume_pfe",
  0x04: "high_volume_3pfe",
  0x05: "low_volume_3pfe",
  0x06: "hv_continuous_flow",
  0x07: "high_volume_hi_fi",
  0x08: "low_volume_hi_fi",
  0x09: "high_viscosity_continuous_flow",
  0x0A: "xl_volume_pfe",
}
RFID_CHIP_TYPE_INVALID = 0x7FFF
# Where the fields sit in a tag: 1 flag byte, then the blocks.
RFID_TAG_SERIAL_OFFSET = 5  # blocks 1-4
RFID_TAG_SERIAL_LENGTH = 16
RFID_TAG_CHIP_TYPE_OFFSET = 21  # block 5, a little-endian u32
RFID_DATE_OF_MANUFACTURE_OFFSET = 25  # blocks 6-7, yyyyMMdd
RFID_DATE_OF_MANUFACTURE_LENGTH = 8


def _transform_serial(*, raw: str) -> str:
  """Reverse each 4-character block, drop NULs and trim: ``52SL3210`` reads ``LS250123``."""
  text = "".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))
  return text.replace("\x00", "").strip()


def compute_tag_serial(*, tag: bytes) -> Optional[str]:
  """The chip's serial, from a whole tag; None when ``tag`` is not one.

  Args:
    tag: the tag, hex-decoded.
  """
  if len(tag) != 1 + 4 * RFID_TAG_BLOCK_COUNT:
    return None
  end = RFID_TAG_SERIAL_OFFSET + RFID_TAG_SERIAL_LENGTH
  return _transform_serial(raw=tag[RFID_TAG_SERIAL_OFFSET:end].decode("ascii", "replace"))


def compute_chip_type_code(*, tag: bytes) -> Optional[int]:
  """The tag's chip type code (``RFID_CHIP_TYPE_CODES``); None when ``tag`` is too short.

  Args:
    tag: the tag, hex-decoded.
  """
  end = RFID_TAG_CHIP_TYPE_OFFSET + 4
  if len(tag) < end:
    return None
  return int.from_bytes(tag[RFID_TAG_CHIP_TYPE_OFFSET:end], "little")


def compute_tag_valid(*, tag: bytes) -> bool:
  """Whether a tag is valid.

  Valid means a known chip type code, a serial that is not an integer, and a yyyyMMdd date.

  Args:
    tag: the tag, hex-decoded.
  """
  serial = compute_tag_serial(tag=tag)
  if serial is None:
    return False
  code = compute_chip_type_code(tag=tag)
  if code not in RFID_CHIP_TYPE_CODES and code != RFID_CHIP_TYPE_INVALID:
    return False
  if re.fullmatch(r"\s*[+-]?[0-9]+\s*", serial) and -(2**63) <= int(serial) < 2**63:
    return False
  end = RFID_DATE_OF_MANUFACTURE_OFFSET + RFID_DATE_OF_MANUFACTURE_LENGTH
  date = _transform_serial(raw=tag[RFID_DATE_OF_MANUFACTURE_OFFSET:end].decode("ascii", "replace"))
  if not re.fullmatch(r"[0-9]{8}", date):
    return False
  try:
    datetime.strptime(date, "%Y%m%d")
  except ValueError:
    return False
  return True


async def _send_command_to_ftdi(*, io: "FTDI", command: bytes) -> None:
  """Clear what the reader sent before, then write one command."""
  await io.usb_purge_rx_buffer()
  await io.write(command)


async def _read_reply(*, io: "FTDI", stop_key: bytes) -> bytes:
  """Read the reader's reply until ``stop_key`` arrives.

  Raises:
    MantisTimeoutError: if ``stop_key`` has not arrived within ``RFID_REPLY_TIMEOUT``.
  """
  loop = asyncio.get_running_loop()
  deadline = loop.time() + RFID_REPLY_TIMEOUT
  buffer = b""
  while stop_key not in buffer:
    if loop.time() > deadline:
      raise MantisTimeoutError(f"the RFID reader did not answer in time; read {buffer!r}")
    chunk = await io.read(num_bytes=512)
    if chunk:
      buffer += chunk
    else:
      await asyncio.sleep(0.01)  # PyLabRobot design choice: poll every 10 ms
  return buffer


async def _initialize_rfid_module(*, io: "FTDI") -> None:
  """Ping the reader, then set its external antenna and ISO15693.

  Raises:
    MantisNotConnectedError: if the ping's reply does not name the TRF7970A.
    MantisTimeoutError: if the ping is not answered.
  """
  await _send_command_to_ftdi(io=io, command=RFID_PING_DEVICE_COMMAND)
  if b"TRF7970A EVM" not in await _read_reply(io=io, stop_key=b"\r\n"):
    raise MantisNotConnectedError("no TRF7970A RFID reader on interface A")
  for command in (RFID_SET_ANTENNA_MODE_COMMAND, RFID_SET_UP_ISO15693_PROTOCOL_COMMAND):
    await _send_command_to_ftdi(io=io, command=command)
    await asyncio.sleep(0.1)  # its replies are cleared, not read
    await io.usb_purge_rx_buffer()


class Rfid:
  """The RFID reader. Reading a slot selects its antenna, then reads the tag in its field."""

  def __init__(
    self,
    *,
    driver: "MantisDriver",
    slot_to_antenna: Optional[Dict[int, int]],
    multiplexer_delay_raw: float,
  ):
    """
    Args:
      driver: the driver it sends through.
      slot_to_antenna: the unit's antenna for each input slot, as declared; None when none is
        declared, and then a read refuses.
      multiplexer_delay_raw: the delay after selecting an antenna, as the unit's file writes it;
        its unit is unconfirmed, so a read refuses unless it is 0.
    """
    self._driver = driver
    self.slot_to_antenna = slot_to_antenna
    self.multiplexer_delay_raw = multiplexer_delay_raw
    self._lock = asyncio.Lock()  # an antenna and the read through it go together

  async def _unchecked_fw_write_ppi(self, *, start_address: int, values: List[int]) -> None:
    """A PPI write (board 0, command 41), sent once.

    Args:
      start_address: the first PPI address written.
      values: the values written from there.
    """
    payload = struct.pack("<hh", start_address, len(values)) + b"".join(
      struct.pack("<H", value) for value in values
    )
    await self._driver._send_command(board=MOTION_BOARD, command=41, payload=payload, attempts=1)

  async def _switch_antenna(self, *, antenna: int) -> None:
    """Select one antenna on the multiplexer."""
    await self._unchecked_fw_write_ppi(start_address=RFID_MULTIPLEXER_BASE_INDEX, values=[antenna])

  async def _read_tag(self) -> Optional[bytes]:
    """The tag in the selected antenna's field, hex-decoded; None if no whole tag is read in time.

    Raises:
      MantisNotConnectedError: if the reader's link is not open.
    """
    io = self._driver.rfid_io
    if io is None:
      raise MantisNotConnectedError(f"{self._driver.name}: the RFID reader's link is not open")
    await _send_command_to_ftdi(io=io, command=RFID_READ_TAG_COMMAND)
    try:
      reply = await _read_reply(io=io, stop_key=b"]")
    except MantisTimeoutError:
      return None
    match = re.search(rb"\[([0-9A-Fa-f]*)\]", reply)
    if match is None or len(match.group(1)) != 2 + 8 * RFID_TAG_BLOCK_COUNT:
      return None
    return bytes.fromhex(match.group(1).decode("ascii"))

  async def request_tag(self, *, slot: int) -> Optional[Dict[str, Any]]:
    """Read the tag of the chip in an input slot.

    Args:
      slot: one of ``slot_to_antenna``.

    Returns:
      ``serial``, ``chip_type`` (None for a code not in ``RFID_CHIP_TYPE_CODES``),
      ``chip_type_code``, ``date_of_manufacture`` (yyyyMMdd), ``valid`` and ``tag_hex``; None
      when the slot holds no chip.

    Raises:
      MantisConfigurationError: if there are no antennas, or ``multiplexer_delay_raw`` is not 0;
        nothing is sent.
      ValueError: if ``slot`` is not one of them; nothing is sent.
    """
    if self.multiplexer_delay_raw != 0:
      raise MantisConfigurationError(
        f"{self._driver.name}: RFID multiplexer delay {self.multiplexer_delay_raw} has an "
        "unconfirmed unit; only 0 is supported"
      )
    if self.slot_to_antenna is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no RFID antennas for this unit or its generation; give its "
        "vendor folder (oem_config_directory)"
      )
    self._driver._check_index(name="slot", value=slot, allowed=tuple(sorted(self.slot_to_antenna)))
    async with self._lock:
      await self._switch_antenna(antenna=self.slot_to_antenna[slot])
      tag = await self._read_tag()
    if tag is None:
      return None
    code = compute_chip_type_code(tag=tag)
    end = RFID_DATE_OF_MANUFACTURE_OFFSET + RFID_DATE_OF_MANUFACTURE_LENGTH
    result = {
      "serial": compute_tag_serial(tag=tag),
      "chip_type": RFID_CHIP_TYPE_CODES.get(code) if code is not None else None,
      "chip_type_code": code,
      "date_of_manufacture": _transform_serial(
        raw=tag[RFID_DATE_OF_MANUFACTURE_OFFSET:end].decode("ascii", "replace")
      ),
      "valid": compute_tag_valid(tag=tag),
      "tag_hex": tag.hex(),
    }
    if not result["valid"]:
      logger.warning("[%s] slot %d: tag %s is not valid", self._driver.name, slot, result["serial"])
    return result

  async def request_tags(self) -> Dict[int, Optional[Dict[str, Any]]]:
    """Read every declared slot in turn; ``request_tag`` for each, by slot.

    Raises:
      MantisConfigurationError: if there are no antennas; nothing is sent.
    """
    if self.slot_to_antenna is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no RFID antennas for this unit or its generation; give its "
        "vendor folder (oem_config_directory)"
      )
    return {slot: await self.request_tag(slot=slot) for slot in sorted(self.slot_to_antenna)}
