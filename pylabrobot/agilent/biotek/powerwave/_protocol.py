"""Wire protocol for the Agilent BioTek PowerWave / PowerWaveX plate readers.

Pure functions and constants: no transport, no I/O, no state. Everything here is
directly testable against captured instrument traffic.

Page references are to the *PowerWaveX Microplate Scanning Spectrophotometer
Operator's Manual* (Bio-Tek p/n 7261014 Rev D2, July 2005), Appendix B
"Computer Control". Note that the older non-X PowerWave manual (p/n 7261000
Rev C1) does not document the carrier commands.

The link is RS-232: 9600 baud, 8 data bits, no parity, **2 stop bits**.
"""

from __future__ import annotations

import enum
import math
import re
import struct
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------------------
# Control bytes (page B-6)
# --------------------------------------------------------------------------------------

ACK = b"\x06"
NAK = b"\x15"
RS = b"\x1e"
ETX = b"\x03"
DLE = b"\x10"
CR = b"\x0d"
LF = b"\x0a"
SUB = b"\x1a"  # CTRL-Z, terminates a wavelength's plate data


# --------------------------------------------------------------------------------------
# Command bytes (Appendix B). The full documented command set is listed so that
# unimplemented commands are discoverable rather than lost in the manual.
# --------------------------------------------------------------------------------------


class Command(bytes, enum.Enum):
  """Single-byte PowerWave computer-control commands."""

  STORE_PLATE_CARRIER = b"\x41"  # 'A', page B-7
  PRESENT_PLATE_CARRIER = b"\x4a"  # 'J', page B-7
  CONFIGURE_WAVELENGTHS = b"\x4d"  # 'M', page B-8
  READ_PLATE = b"\x53"  # 'S', page B-9
  SET_ASSAY_DEFINITION = b"\x56"  # 'V', page B-10
  GET_WAVELENGTH_TABLE = b"\x57"  # 'W', page B-11
  HALT = b"\x58"  # 'X', page B-12
  SET_PLATE_GEOMETRY = b"\x7b"  # '{', page B-12
  SET_STRIP_RANGE = b"\x25"  # '%', page B-13
  GET_MINIMUM_KINETIC_INTERVAL = b"\x24"  # '$', page B-13
  SCAN_PLATE = b"\x26"  # '&', page B-14
  SELF_TEST = b"\x2a"  # '*', page B-16
  SET_TEMPERATURE = b"\x5b"  # '[', page B-17
  GET_TEMPERATURE = b"\x5d"  # ']', page B-18
  GET_PLATE_BARCODE = b"\x73"  # 's', page B-19
  GET_INSTRUMENT_CONFIGURATION = b"\x7d"  # '}', page B-19
  CLOSE_PLATE_ACCESS_DOOR = b"\x29"  # ')', page B-20


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


class PowerWaveError(RuntimeError):
  """Base class for PowerWave protocol and instrument errors."""


class PowerWaveNakError(PowerWaveError):
  """The instrument replied NAK: it rejected the command."""


class PowerWaveStatusError(PowerWaveError):
  """The instrument returned a non-OK status string (page B-21)."""

  def __init__(self, code: str, message: str) -> None:
    super().__init__(f"PowerWave status {code}: {message}")
    self.code = code
    self.message = message


class PowerWaveChecksumError(PowerWaveError):
  """A plate data block did not match its transmitted checksum (page B-9)."""


class PowerWaveParseError(PowerWaveError):
  """A response could not be parsed."""


class PowerWaveBusyError(PowerWaveError):
  """Another operation is using the serial port.

  The instrument answers one command at a time, and a read or scan holds the
  line for seconds to minutes while it streams data. A command issued meanwhile
  would interleave with that stream, so it is refused rather than allowed to
  corrupt both. Callers that poll - a display showing the incubator temperature,
  say - should treat this as "ask again later"; :meth:`.PowerWaveX.halt` is the
  way to take the line from a run that is in progress.
  """


class PowerWaveHaltedError(PowerWaveError):
  """A read or scan was interrupted by :meth:`.PowerWaveX.halt`.

  Raised in the task that was reading, so that a caller waiting on a long
  operation returns promptly once something else halts the instrument. The halt
  itself is not an error: it is reported to whoever asked for it by returning
  normally.
  """


class PowerWaveReadAbortedError(PowerWaveError):
  """The instrument abandoned a read or scan part way through.

  It signals this by sending ``DLE`` where the next data block's ``CR`` start
  code belongs - the same byte that acknowledges HALT - rather than by reporting
  the fault in the data stream. Any status string that follows carries the
  reason.

  Attributes:
    code: The status code reported after the abort, or ``None`` if the instrument
      sent none. An ELx code here is the one shown on the front panel, and stays
      latched until it is cleared there.
  """

  def __init__(self, message: str, code: Optional[str] = None) -> None:
    super().__init__(message)
    self.code = code


# --------------------------------------------------------------------------------------
# Status strings (page B-21)
# --------------------------------------------------------------------------------------

#: Status strings are RS + three ASCII characters + ETX. Only "000" means OK.
STATUS_MESSAGES: Dict[str, str] = {
  "000": "OK",
  "080": "Instrument failure - perform self-test",
  "090": "Error in assay, scan, or table definition",
  "0A0": "Error in strip range selection",
  "0B0": "Incubator setpoint error",
  "0C0": "Incubator temperature error",
  "0E0": "Barcode error",
}

STATUS_LENGTH = 5  # RS + 3 + ETX


def parse_status(response: bytes) -> str:
  """Validate a status string and return its code.

  The instrument has two status formats, both five bytes (page B-21):

  * ``RS`` + three characters + ``ETX``, whose middle digit is the fault code.
  * An ELx status: four hexadecimal characters + ``ETX``, with no ``RS``.

  Both are accepted here, since which one arrives depends on the instrument's
  status mode rather than on the command sent.

  Args:
    response: The raw five-byte status string.

  Returns:
    The status code: ``"000"`` in the first format, ``"0000"`` in the second.

  Raises:
    PowerWaveParseError: If the response is not a well-formed status string.
    PowerWaveFatalError: For an ELx ``A``-prefixed error, which needs a power cycle.
    PowerWaveStatusError: If the instrument reported any other error.
  """
  if is_elx_status(response):
    return parse_elx_status(response)

  if len(response) != STATUS_LENGTH or not response.startswith(RS) or not response.endswith(ETX):
    raise PowerWaveParseError(
      f"Malformed status string (expected RS + 3 bytes + ETX, or four hex digits + ETX, "
      f"page B-21): {response!r}"
    )

  code = response[1:-1].decode("ascii", errors="replace")
  if code == "000":
    return code

  raise PowerWaveStatusError(code, STATUS_MESSAGES.get(code, "Unknown status code"))


# --------------------------------------------------------------------------------------
# Instrument configuration (page B-19)
# --------------------------------------------------------------------------------------

#: Model identifiers, keyed by the 16-bit configuration value in big-endian byte order.
#: The instrument transmits it least significant byte first (page B-7).
DEVICE_CONFIGURATIONS: Dict[int, str] = {
  0x0030: "PowerWave 200",
  0x0031: "PowerWave 340",
  0x0032: "PowerWaveX",
  0x0038: "PowerWave 200 w/ Incubator",
  0x0039: "PowerWave 340 w/ Incubator",
  0x003A: "PowerWaveX w/ Incubator",
  0x003B: "PowerWaveX 340 w/ Incubator",
  0x0072: "PowerWaveX Select",
  0x007A: "PowerWaveX Select w/ Incubator",
  0x00B0: "PowerWave 200 w/ Robot Access Door",
  0x00B1: "PowerWave 340 w/ Robot Access Door",
  0x00B2: "PowerWaveX w/ Robot Access Door",
  0x00B8: "PowerWave 200 w/ Incubator and Robot Access Door",
  0x00B9: "PowerWave 340 w/ Incubator and Robot Access Door",
  0x00BA: "PowerWaveX w/ Incubator and Robot Access Door",
  0x00F2: "PowerWaveX Select w/ Robot Access Door",
  0x00FA: "PowerWaveX Select w/ Incubator and Robot Access Door",
}


def parse_uint16(payload: bytes) -> int:
  """Parse a two-byte little-endian unsigned integer (page B-7)."""
  if len(payload) != 2:
    raise PowerWaveParseError(f"Expected 2 bytes for a 16-bit integer, got {payload!r}")
  return int(struct.unpack("<H", payload)[0])


def parse_device_configuration(payload: bytes) -> str:
  """Parse the two-byte instrument configuration into a model name (page B-19).

  Raises:
    PowerWaveParseError: If the configuration value is not a known model.
  """
  value = parse_uint16(payload)
  try:
    return DEVICE_CONFIGURATIONS[value]
  except KeyError:
    raise PowerWaveParseError(
      f"Unrecognized instrument configuration 0x{value:04X} (page B-19)"
    ) from None


def configuration_has_incubator(model: str) -> bool:
  """Whether a model name from :data:`DEVICE_CONFIGURATIONS` includes the incubator."""
  return "Incubator" in model


# --------------------------------------------------------------------------------------
# Wavelength table (page B-11)
# --------------------------------------------------------------------------------------

MAX_WAVELENGTHS = 6  # at most six wavelengths per assay definition (page B-10)
WAVELENGTH_TABLE_LENGTH = 4 * MAX_WAVELENGTHS  # "550,620,665,520,450,595," - note trailing comma

MIN_WAVELENGTH = 200
MAX_WAVELENGTH = 999

#: Which order-sorting filter the monochromator uses for each wavelength band.
#:
#: Nothing here is selectable. The instrument picks the position itself from the
#: wavelength asked for: no computer-control command chooses it, and the wheel
#: surfaces over the serial port only as motor 1 in fault codes, such as
#: :data:`SELF_TEST_ERRORS` ``"0201"`` for a wheel that cannot find home. Note
#: that ``0F11``, the code a far-UV read latches, is *not* about this wheel - its
#: ``<filter>`` digit is the assay's wavelength slot, as in ``0E00``.
#:
#: Recorded because it is useful for reasoning about faults, which is all it is
#: good for. These edges come from third-party PowerWave documentation rather than
#: manual revision D2, and cannot be checked from here: nothing reports the
#: current position, and a wheel move is far too quick to time over a 9600 baud
#: line. Treat them as unverified.
#:
#: They do settle one question. 200, 205 and 215 nm all fall in the first band, so
#: the wheel sits in the same position for the far-UV wavelengths that abandon a
#: read and for the one that reads normally, which rules it out as the cause. See
#: :attr:`.PowerWaveX.abs_wavelength_range`.
ORDER_SORTING_FILTER_BANDS: Dict[Tuple[int, int], str] = {
  (200, 241): "open (no filter)",
  (242, 369): "filter 1",
  (370, 459): "filter 2",
  (460, 569): "filter 3",
  (570, 634): "filter 4",
  (635, 999): "filter 5",
}


def parse_wavelength_table(payload: bytes) -> List[int]:
  """Parse the 24-byte wavelength setup table (page B-11).

  This is a *calibration* table, not a list of installed filters. The PowerWaveX
  selects wavelengths with a monochromator and can read anywhere in 200-999 nm at
  1 nm increments (page 1-1). The table holds the up-to-six wavelengths whose
  gains were calibrated by the last self-test; reads at other wavelengths work,
  but "performance may be compromised slightly" (page B-8).

  Unused entries are reported as ``000`` and omitted from the result.
  """
  if len(payload) != WAVELENGTH_TABLE_LENGTH:
    raise PowerWaveParseError(
      f"Expected {WAVELENGTH_TABLE_LENGTH} bytes for the wavelength table, got {payload!r}"
    )

  fields = payload.decode("ascii", errors="replace").split(",")[:MAX_WAVELENGTHS]
  try:
    values = [int(field) for field in fields]
  except ValueError:
    raise PowerWaveParseError(f"Non-numeric wavelength table: {payload!r}") from None

  return [value for value in values if value != 0]


def encode_wavelength_table(wavelengths: Sequence[int]) -> bytes:
  """Encode a wavelength setup table for CONFIGURE WAVELENGTHS (page B-8).

  Six slots are always transmitted; unused slots are ``"000"``. The caller is
  responsible for sending ``ETX`` after the final comma, and for running a
  self-test afterwards to recalibrate gains. The table is stored in non-volatile
  memory, so it only needs writing when the calibrated set should change.
  """
  _validate_wavelengths(wavelengths, allow_empty=True)
  padded = list(wavelengths) + [0] * (MAX_WAVELENGTHS - len(wavelengths))
  encoded = "".join(f"{value:03d}," for value in padded).encode("ascii")
  assert len(encoded) == WAVELENGTH_TABLE_LENGTH
  return encoded


def _validate_wavelengths(wavelengths: Sequence[int], allow_empty: bool = False) -> None:
  if not allow_empty and len(wavelengths) == 0:
    raise ValueError("At least one wavelength must be specified")
  if len(wavelengths) > MAX_WAVELENGTHS:
    raise ValueError(f"At most {MAX_WAVELENGTHS} wavelengths may be specified")
  for value in wavelengths:
    if not isinstance(value, int) or isinstance(value, bool):
      raise TypeError(f"Wavelengths must be integers, got {value!r}")
    if not MIN_WAVELENGTH <= value <= MAX_WAVELENGTH:
      raise ValueError(
        f"Wavelength {value} out of range {MIN_WAVELENGTH}-{MAX_WAVELENGTH} nm. "
        "Note that 340 models cannot read below 340 nm (page 1-5)."
      )


# --------------------------------------------------------------------------------------
# Temperature (pages B-17, B-18)
# --------------------------------------------------------------------------------------

MIN_TEMPERATURE = 22.0
MAX_TEMPERATURE = 50.0


def parse_temperature(payload: bytes) -> float:
  """Parse the two-byte incubator temperature (page B-18).

  The instrument reports tenths of a degree, so ``371`` means 37.1 degrees C.

  Raises:
    PowerWaveError: If the instrument reports ``0x0000``, meaning it has no
      working incubator or the incubator is in error.
  """
  if payload == b"\x00\x00":
    raise PowerWaveError(
      "Instrument reported no temperature: it has no incubator, or the incubator is in error "
      "(page B-18)"
    )
  return parse_uint16(payload) / 10


def encode_temperature_setpoint(temperature: int) -> bytes:
  """Encode an incubator setpoint for SET TEMPERATURE (page B-17).

  Unlike the two-byte little-endian *read*, the setpoint is written as a single
  raw byte holding the integer degrees. Zero turns the heaters off.
  """
  if not isinstance(temperature, int) or isinstance(temperature, bool):
    raise TypeError(f"Temperature setpoint must be an integer, got {temperature!r}")
  if temperature != 0 and not MIN_TEMPERATURE <= temperature <= MAX_TEMPERATURE:
    raise ValueError(
      f"Temperature setpoint must be 0 (off) or between {MIN_TEMPERATURE:.0f} and "
      f"{MAX_TEMPERATURE:.0f} degrees C (page B-17), got {temperature}"
    )
  return bytes([temperature])


# --------------------------------------------------------------------------------------
# Assay definition table (page B-10)
# --------------------------------------------------------------------------------------

ASSAY_DEFINITION_LENGTH = 170

READ_TYPES: Dict[str, int] = {"endpoint": 0, "kinetic": 2, "scan": 3}

#: Bit flags for assay definition byte 31 (page B-10).
FLAG_SHAKE = 0x10
FLAG_SHAKE_EVERY_READ = 0x20
FLAG_SHAKE_CONTINUOUS = 0x40
FLAG_USE_TOTAL_KINETIC_TIME = 0x80

#: Byte 31 for a kinetic run governed by its read count: shake, shake before every
#: read, continuous shake.
#:
#: :data:`FLAG_USE_TOTAL_KINETIC_TIME` is deliberately absent. The 2021 bench runs
#: set it, but it makes the instrument run for ``total_kinetic_read_time`` and
#: ignore ``kinetic_read_count`` entirely: a PowerWaveX w/ Incubator (firmware
#: R06.20) asked for 3 cycles kept sending a 4th, leaving the caller reading into a
#: run that had hours left. With the bit cleared the same request sends exactly
#: three blocks and the closing ``^Z``. Verified directly.
KINETIC_FLAGS = FLAG_SHAKE | FLAG_SHAKE_EVERY_READ | FLAG_SHAKE_CONTINUOUS


def build_assay_definition(
  wavelengths: Sequence[int],
  read_type: str = "endpoint",
  *,
  assay_name: bytes = b"ABCDEF",
  flags: int = 0,
  scans_per_well: int = 31,
  kinetic_interval: int = 5,
  kinetic_read_count: int = 2,
  shake_time: int = 3,
  shake_speed: int = 1,
  total_kinetic_read_time: int = 1,
  delay_before_first_read: int = 0,
  delay: int = 2,
) -> bytes:
  """Build the 170-byte SET ASSAY DEFINITION table (page B-10).

  Byte offsets below are the manual's one-based numbering. All "don't care"
  fields are zero-filled as the manual recommends.

  Args:
    wavelengths: Up to six wavelengths in nm to read, selected by the
      monochromator. Unused slots become ``"000"``.
    read_type: One of ``"endpoint"``, ``"kinetic"`` or ``"scan"`` (byte 56).
      ``"scan"`` means *well* scanning; spectral scanning is the separate ``'&'``
      SCAN PLATE command.
    assay_name: Six ASCII characters, bytes 2-7.
    flags: Byte 31, built from the ``FLAG_*`` constants.
    scans_per_well: Byte 32, an odd integer from 1 to 31.
    kinetic_interval: Bytes 60-61, seconds between reads.
    kinetic_read_count: Bytes 62-63, number of reads, 2 to 9999.
    shake_time: Bytes 163-164, seconds.
    shake_speed: Byte 165, 0 slow, 1 medium, 2 fast, 3 variable.
    total_kinetic_read_time: Bytes 166-167. The manual says minutes, but a 2021
      bench test showed a setting of 9999 yielding a run of about 9999 *seconds*.
      Only consulted when :data:`FLAG_USE_TOTAL_KINETIC_TIME` is set.
    delay_before_first_read: Byte 168, 0 no, 1 yes.
    delay: Bytes 169-170, seconds.

  Returns:
    Exactly 170 bytes, ready to send after the ``'V'`` command byte.
  """
  if read_type not in READ_TYPES:
    raise ValueError(f"read_type must be one of {sorted(READ_TYPES)}, got {read_type!r}")
  _validate_wavelengths(wavelengths)

  if len(assay_name) != 6:
    raise ValueError(f"assay_name must be exactly 6 bytes, got {assay_name!r}")
  if not 0 <= flags <= 0xFF:
    raise ValueError(f"flags must fit in one byte, got {flags}")
  if not 1 <= scans_per_well <= 31 or scans_per_well % 2 == 0:
    raise ValueError(f"scans_per_well must be an odd integer from 1 to 31, got {scans_per_well}")
  if not 0 <= kinetic_interval <= 9999:
    raise ValueError(f"kinetic_interval must be 0-9999 seconds, got {kinetic_interval}")
  if not 2 <= kinetic_read_count <= 9999:
    raise ValueError(f"kinetic_read_count must be 2-9999, got {kinetic_read_count}")
  if not 0 <= shake_time <= 999:
    raise ValueError(f"shake_time must be 0-999 seconds, got {shake_time}")
  if not 0 <= shake_speed <= 3:
    raise ValueError(f"shake_speed must be 0-3, got {shake_speed}")
  if not 1 <= total_kinetic_read_time <= 9999:
    raise ValueError(f"total_kinetic_read_time must be 1-9999, got {total_kinetic_read_time}")
  if delay_before_first_read not in (0, 1):
    raise ValueError(f"delay_before_first_read must be 0 or 1, got {delay_before_first_read}")
  if not 0 <= delay <= 999:
    raise ValueError(f"delay must be 0-999 seconds, got {delay}")

  table = bytearray()
  table += bytes(1)  # 1: don't care
  table += assay_name  # 2-7: assay name
  table += bytes(23)  # 8-30: don't care
  table += bytes([flags])  # 31: shake and kinetic-timing flags
  table += bytes([scans_per_well])  # 32: scans per well
  table += bytes(17)  # 33-49: don't care
  table += b"000000"  # 50-55: fixed "000000"
  table += bytes([READ_TYPES[read_type]])  # 56: read type
  table += bytes(3)  # 57-59: don't care
  table += struct.pack("<H", kinetic_interval)  # 60-61
  table += struct.pack("<H", kinetic_read_count)  # 62-63
  table += bytes(3)  # 64-66: don't care
  table += encode_assay_wavelengths(wavelengths)  # 67-84
  table += bytes(78)  # 85-162: don't care
  table += struct.pack("<H", shake_time)  # 163-164
  table += bytes([shake_speed])  # 165
  table += struct.pack("<H", total_kinetic_read_time)  # 166-167
  table += bytes([delay_before_first_read])  # 168
  table += struct.pack("<H", delay)  # 169-170

  if len(table) != ASSAY_DEFINITION_LENGTH:
    raise AssertionError(
      f"Assay definition table must be {ASSAY_DEFINITION_LENGTH} bytes, built {len(table)}"
    )
  return bytes(table)


def encode_assay_wavelengths(wavelengths: Sequence[int]) -> bytes:
  """Encode assay definition bytes 67-84: six ASCII wavelengths, ``"000"`` if unused."""
  _validate_wavelengths(wavelengths)
  padded = list(wavelengths) + [0] * (MAX_WAVELENGTHS - len(wavelengths))
  encoded = "".join(f"{value:03d}" for value in padded).encode("ascii")
  assert len(encoded) == 3 * MAX_WAVELENGTHS
  return encoded


# --------------------------------------------------------------------------------------
# Plate data (page B-9)
# --------------------------------------------------------------------------------------

#: A well reading is a sign and four digits, or five asterisks when overrange.
_READING_RE = re.compile(rb"(?P<sign>[+-])(?P<magnitude>\d{4})|(?P<overrange>\*{5})")

#: Overrange wells ("*****") are reported as this value: a real but saturated
#: measurement, distinct from ``None`` which PyLabRobot uses for "not measured".
#:
#: The marker is not emitted at the 3.000 OD top of the instrument's specified
#: range (page 1-1). Reading a part-covered carrier on a PowerWaveX w/ Incubator
#: (firmware R06.20) returned finite values of 4.054, 4.043 and 4.026 OD
#: alongside genuinely overrange wells, so the switch to ``"*****"`` happens
#: somewhere above 4.05. Values between 3.000 and that point are outside the
#: specified range but still arrive as numbers, and should not be trusted as
#: measurements merely because they parsed.
OVERRANGE = math.inf


def plate_checksum(frame: bytes) -> int:
  """Compute the checksum of one wavelength's plate data block (page B-9).

  The manual specifies that the checksum "starts with the first byte AFTER the
  data start code, up through and including the ``<^Z>`` data terminator". The
  data start code is the ``CR`` that precedes the first row.

  This was verified against 330 consecutive plate reads captured from a
  PowerWaveX w/ Incubator, where each block's checksum byte is transmitted
  immediately after its ``^Z``.

  Args:
    frame: The plate data block, which may carry leading bytes before the ``CR``
      (such as the preceding block's checksum), through and including ``^Z``.
  """
  start = frame.find(CR)
  if start == -1:
    raise PowerWaveParseError(f"No CR data start code in plate frame: {frame[:32]!r}...")
  return sum(frame[start + 1 :]) % 256


def verify_plate_checksum(frame: bytes, checksum: int) -> None:
  """Check a plate data block against its transmitted checksum byte (page B-9).

  Raises:
    PowerWaveChecksumError: If the checksum does not match.
  """
  expected = plate_checksum(frame)
  if expected != checksum:
    raise PowerWaveChecksumError(
      f"Plate data checksum mismatch: computed {expected}, instrument sent {checksum}"
    )


def parse_plate(frame: bytes, num_rows: int = 8, num_columns: int = 12) -> List[List[float]]:
  """Parse one wavelength's plate data block into absorbances (page B-9).

  Readings arrive as a sign and four digits with no decimal point; ``"+1234"``
  means +1.234 OD. Overrange wells arrive as ``"*****"`` and are returned as
  :data:`OVERRANGE`.

  Args:
    frame: The plate data block through and including ``^Z``. Leading bytes
      before the first row are ignored, so a preceding checksum byte is harmless.
    num_rows: Rows expected, 8 for a standard 96-well plate.
    num_columns: Columns expected, 12 for a standard 96-well plate.

  Returns:
    A nested list indexed ``[row][column]`` in optical density units.

  Raises:
    PowerWaveParseError: If the block is not exactly ``num_rows`` comma-separated
      rows of ``num_columns`` well-formed readings, terminated by ``^Z``.
  """
  # Validate the row structure rather than scanning the block for anything that
  # looks like a reading. The checksum is the first guard, but it is a byte sum
  # and so blind to compensating edits: turning ",+0201,+0207" into
  # ",+0201.+0205" moves the separator up two and the digit down two, leaving the
  # checksum identical while silently changing a well from 0.207 to 0.205. Only
  # the framing catches that.
  start = frame.find(CR)
  if start == -1:
    raise PowerWaveParseError(f"No CR before the first plate row: {frame[:32]!r}...")
  body = frame[start + 1 :]

  if not body.endswith(SUB):
    raise PowerWaveParseError(
      f"Plate data block is not terminated by ^Z, so it is truncated: ...{frame[-24:]!r}"
    )
  body = body[:-1]

  rows = [row for row in body.split(CR + LF) if len(row.strip(CR + LF)) > 0]
  if len(rows) != num_rows:
    raise PowerWaveParseError(
      f"Expected {num_rows} rows for a {num_rows}x{num_columns} plate, found {len(rows)}. "
      f"The instrument's plate geometry or strip range may not match."
    )

  data: List[List[float]] = []
  for index, row in enumerate(rows):
    fields = row.strip(CR + LF).split(b",")
    # Every row opens with a comma, so the split leaves an empty leading field.
    if fields[0] != b"":
      raise PowerWaveParseError(f"Plate row {index + 1} does not begin with a comma: {row[:24]!r}")

    readings: List[float] = []
    for field in fields[1:]:
      reading = _READING_RE.fullmatch(field)
      if reading is None:
        raise PowerWaveParseError(
          f"Malformed reading in plate row {index + 1}: {field!r} in {row[:24]!r}"
        )
      if reading.group("overrange") is not None:
        readings.append(OVERRANGE)
      else:
        magnitude = int(reading.group("magnitude")) / 1000
        readings.append(magnitude if reading.group("sign") == b"+" else -magnitude)

    if len(readings) != num_columns:
      raise PowerWaveParseError(
        f"Expected {num_columns} readings in plate row {index + 1}, parsed "
        f"{len(readings)} from {row[:24]!r}"
      )
    data.append(readings)

  return data


def count_overrange(data: List[List[float]]) -> int:
  """Count wells that read overrange, for logging."""
  return sum(1 for row in data for value in row if math.isinf(value))


# --------------------------------------------------------------------------------------
# Strip range and plate geometry helpers (pages B-12, B-13)
# --------------------------------------------------------------------------------------


def wells_bounding_box(
  well_indices: Sequence[Tuple[int, int]],
) -> Optional[Tuple[int, int, int, int]]:
  """Return ``(min_row, min_column, max_row, max_column)`` for zero-based well indices.

  Returns ``None`` for an empty selection. Used to derive a SET STRIP RANGE
  request; the PowerWave can only read contiguous rectangles, so a sparse
  selection is over-read and filtered by the caller.
  """
  if len(well_indices) == 0:
    return None
  rows = [row for row, _ in well_indices]
  columns = [column for _, column in well_indices]
  return (min(rows), min(columns), max(rows), max(columns))


# --------------------------------------------------------------------------------------
# Plate geometry and strip range (pages B-12, B-13)
# --------------------------------------------------------------------------------------

#: SET PLATE GEOMETRY data byte, keyed by (rows, columns).
PLATE_GEOMETRIES: Dict[Tuple[int, int], int] = {
  (8, 12): 2,  # 96 wells
  (16, 24): 3,  # 384 wells
}

MAX_STRIP = 24


def encode_plate_geometry(num_rows: int, num_columns: int) -> bytes:
  """Encode the SET PLATE GEOMETRY data byte (page B-12).

  Raises:
    NotImplementedError: If the instrument has no geometry code for this layout.
      Only 96-well (8x12) and 384-well (16x24) are defined.
  """
  try:
    return bytes([PLATE_GEOMETRIES[(num_rows, num_columns)]])
  except KeyError:
    raise NotImplementedError(
      f"The PowerWave supports 8x12 and 16x24 plates only (page B-12), not {num_rows}x{num_columns}"
    ) from None


def encode_strip_range(first_strip: int, last_strip: int) -> bytes:
  """Encode the two SET STRIP RANGE data bytes (page B-13).

  Strips are plate columns, numbered from 1. The range is inclusive and must be
  contiguous: the instrument cannot skip strips, and cannot select rows at all.
  """
  if not 1 <= first_strip <= MAX_STRIP:
    raise ValueError(f"first_strip must be 1-{MAX_STRIP}, got {first_strip}")
  if not 1 <= last_strip <= MAX_STRIP:
    raise ValueError(f"last_strip must be 1-{MAX_STRIP}, got {last_strip}")
  if first_strip > last_strip:
    raise ValueError(f"first_strip {first_strip} is after last_strip {last_strip}")
  return bytes([first_strip, last_strip])


# --------------------------------------------------------------------------------------
# Self-test report (page B-16, error codes pages B-22 onward)
# --------------------------------------------------------------------------------------

#: Self-test error codes, in Rev D2 numbering. Rev C1 numbers several of these
#: differently (its 0202 is the monochromator and it has an 0203), so a code read
#: from a PowerWaveX must be looked up here and not in the older manual.
SELF_TEST_ERRORS: Dict[str, str] = {
  "0200": "Plate carrier did not find the home sensor",
  "0201": "Order sorting filter wheel did not find the home sensor",
  "0202": "Robot lid lift did not find the home sensor",
  "0204": "Y axis could not find the optosensor",
  "0300": "Carrier failed to find light beam",
  "0301": "Order sorting filter wheel did not find home",
  "0303": "Monochromator did not find home",
  "0400": "Track carrier failed position verify",
  "0401": "Order sorting filter wheel failed position verify",
  "0402": "Robot lid lift failed position verify",
  "0403": "Wavelength select (monochromator) failed",
  "0404": "Y axis failed position verify",
  "0500": "Saturation error",
  "0601": "LAMBDA #1 gain out of range",
  "0602": "LAMBDA #2 gain out of range",
  "0603": "LAMBDA #3 gain out of range",
  "0604": "LAMBDA #4 gain out of range",
  "0605": "LAMBDA #5 gain out of range",
  "0606": "LAMBDA #6 gain out of range",
  "0700": "Reader failed noise test",
  "0800": "Reader failed offset test",
  "0900": "Read time dark value out of range",
  "1000": "Configuration data is missing",
  "1100": "Failed configuration checksum test",
  "1200": "Calibration data missing",
  "1300": "Motor not correctly homed",
  "1400": "Assay incubation error",
  "1500": "Assay incubation error",
  "1600": "Computer control assay definition error",
  "1700": "Kinetic interval too short for selected options",
  "1800": "Too many kinetic intervals programmed",
  "1900": "Memory allocation failure",
}

_SELF_TEST_FAIL_RE = re.compile(r"\*\*\*\s*SYSTEM TEST FAIL\s*\*\*\*")
_SELF_TEST_ERROR_RE = re.compile(r"ERROR\s*\((\d{4})\)")


class PowerWaveSelfTestError(PowerWaveError):
  """The self-test report declared a failure (page B-16)."""

  def __init__(self, code: Optional[str], report: str) -> None:
    description = SELF_TEST_ERRORS.get(code or "", "Unknown self-test error")
    super().__init__(
      f"Self-test failed with error {code or '(no code)'}: {description}. "
      "The instrument cannot read plates until this is resolved."
    )
    self.code = code
    self.description = description
    self.report = report


def parse_self_test_report(report: str) -> Optional[str]:
  """Return the error code from a self-test report, or ``None`` if it passed.

  The self-test's *status string* reports whether the command ran, not whether
  the test passed: a failing instrument still answers ``"000"``. The verdict
  appears only in the ASCII report body, which this instrument prints in the
  older Rev C1 layout even though its error codes follow Rev D2.

  Returns:
    The four-digit error code, ``None`` if the report shows no failure, or the
    empty string if it declares a failure without naming a code.
  """
  if _SELF_TEST_FAIL_RE.search(report) is None:
    return None
  match = _SELF_TEST_ERROR_RE.search(report)
  return match.group(1) if match is not None else ""


# --------------------------------------------------------------------------------------
# ELx status codes (page B-21 onward)
# --------------------------------------------------------------------------------------
#
# The instrument has a second status format alongside the RS + S1S2S3 + ETX one:
# four ASCII hexadecimal characters followed by ETX, with no RS. The codes below
# are templates. The manual's convention is that a placeholder in angle brackets
# is "indicated by an ASCII digit replacing the last '0' character in the status
# code", and one in curly braces replaces the next-to-last '0'. So NO SENSOR ERR
# "0200" with motor 1 arrives as "0201", which is the order sorting filter wheel
# fault the self-test section spells out as ERROR 0201.

#: Fatal ELx errors. The manual states these "require recycling of instrument power".
ELX_FATAL_ERRORS: Dict[str, str] = {
  "A100": "Task control block not available",
  "A200": "Read already in progress",
  "A300": "<device> not available",
  "A400": "Failed code checksum test on powerup",
  "A500": "Data reduction steps alloc/free error <assay num>",
  "A600": "Data flash write timed out",
  "A700": "Data flash readback did not match write {test}<chip>",
  "A800": "Code flash write timed out",
  "A900": "Memory allocation heap corrupted",
  "AA00": "<device> A/D converter never saw ready transition",
}

#: Non-fatal ELx errors.
ELX_ERRORS: Dict[str, str] = {
  "0000": "No errors detected",
  "0100": "Read function aborted",
  "0200": "<motor> did not find opto-sensor transition",
  "0300": "<motor> did not find saturation transition",
  "0400": "<motor> failed positional verify",
  "0500": "A/D signal saturated <test type>",
  "0600": "<filter> gain out of range",
  "0700": "Reader {channel} failed noise test",
  "0800": "Reader {channel} failed offset test",
  "0900": "Read-time {channel}<filter> dark out of range",
  "0A00": "Read-time {channel}<filter> air/blank out of range",
  "0B00": "Invalid <assay number>",
  "0C00": "Printer timed out",
  "0D00": "Failed calibration checksum test",
  "0E00": "Wavelength not found in table <read filter>",
  "0F00": "{channel}<filter> signal out of range",
  "1000": "Necessary configuration data missing",
  "1100": "Failed configuration checksum test",
  "1200": "Necessary calibration data missing",
  "1300": "<motor> not homed successfully",
  "1500": "Incubator failure {error code}<zone(s)>",
  "1600": "Computer control assay definition error",
  "1700": "Kinetic interval too short for selected options",
  "1800": "Too many kinetic intervals",
  "1900": "Memory allocation failed",
  "1A00": "Store curve failure",
  "1B00": "Get curve failure",
  "1C00": "A/D calibration standby transition not detected",
  "1D00": "Results data error",
  "1E00": "Error in clock communications",
  "1F00": "Bandpass overlap in filterset",
  "2000": "No valid barcode detected",
  "2100": "Invalid parameter value selected",
  "2200": "PMT test signal too high <test type>",
  "2300": "Lamp control failure <test type>",
  "2400": "Test sensor position incorrect <motor>",
  "2500": "Motor went by flash location too soon",
  "2600": "Physical limit exceeded for area scan request",
  "2700": "<assay> method does not match first panel assay",
  "2800": "<motor> timer not available",
  "2900": "Voltage reference failed <test type>",
  "2A00": "<motor> did not find middle sensor",
}

#: Digit substitutions, page B-23. Keyed by the placeholder name the templates use.
ELX_MOTORS: Dict[str, str] = {
  "0": "carrier X axis",
  "1": "filter wheel",
  "2": "robotic door",
  "3": "monochromator",
  "4": "carrier Y axis",
}

ELX_TEST_TYPES: Dict[str, str] = {
  "1": "5V power failed",
  "2": "24V power failed",
}

ELX_AD_DEVICES: Dict[str, str] = {
  "1": "absorbance measurement",
  "2": "fluorescence measurement",
  "3": "incubation measurement",
  "4": "voltage reference",
}

ELX_INCUBATOR_FAULTS: Dict[str, str] = {
  "0": "range error",
  "1": "thermistor error",
  "2": "A/D error",
}

ELX_DATA_FLASH_FAULTS: Dict[str, str] = {
  "0": "readback error",
  "1": "copy error",
}

#: Which substitution table applies to the last digit of each templated code.
_ELX_LAST_DIGIT_TABLES: Dict[str, Dict[str, str]] = {
  "0200": ELX_MOTORS,
  "0300": ELX_MOTORS,
  "0400": ELX_MOTORS,
  "0500": ELX_TEST_TYPES,
  "1300": ELX_MOTORS,
  "2200": ELX_TEST_TYPES,
  "2300": ELX_TEST_TYPES,
  "2400": ELX_MOTORS,
  "2800": ELX_MOTORS,
  "2900": ELX_TEST_TYPES,
  "2A00": ELX_MOTORS,
  "A300": ELX_AD_DEVICES,
  "AA00": ELX_AD_DEVICES,
}

#: Which substitution table applies to the next-to-last digit.
_ELX_SECOND_DIGIT_TABLES: Dict[str, Dict[str, str]] = {
  "1500": ELX_INCUBATOR_FAULTS,
  "A700": ELX_DATA_FLASH_FAULTS,
}

ELX_STATUS_LENGTH = 5  # four hexadecimal characters + ETX


class PowerWaveFatalError(PowerWaveError):
  """A fatal ELx error. The manual requires an instrument power cycle to clear it."""

  def __init__(self, code: str, message: str) -> None:
    super().__init__(f"PowerWave fatal error {code}: {message}. Power-cycle the instrument.")
    self.code = code
    self.message = message


def describe_elx_status(code: str) -> Optional[str]:
  """Describe an ELx status code, expanding any templated digits (page B-21).

  The last digit fills the description's angle-bracket placeholder and the
  next-to-last fills the curly-brace one, so ``"0201"`` expands NO SENSOR ERR's
  ``<motor>`` to the filter wheel. Note that a digit of ``0`` is meaningful, not
  absent: motor ``0`` is the carrier X axis.

  Args:
    code: Four hexadecimal characters, for example ``"0201"``.

  Returns:
    A human-readable description, or ``None`` if the code is not documented.
  """
  code = code.upper()

  # Where D2's self-test appendix spells a code out, its wording is specific to
  # this instrument ("order sorting filter wheel" rather than "filter wheel").
  spelled_out = SELF_TEST_ERRORS.get(code)
  if spelled_out is not None:
    return spelled_out

  table = {**ELX_ERRORS, **ELX_FATAL_ERRORS}

  exact_base = table.get(code)
  if exact_base is not None and "<" not in exact_base and "{" not in exact_base:
    return exact_base

  base = code[:2] + "00"
  description = table.get(base)
  if description is None:
    return table.get(code)

  last, second = code[3], code[2]

  if base == "1500":
    # Zones are a bit field in the lowest digit, one bit per zone.
    zones = [str(z + 1) for z in range(4) if int(last, 16) & (1 << z)]
    fault = ELX_INCUBATOR_FAULTS.get(second, f"fault {second}")
    where = f"zone(s) {', '.join(zones)}" if zones else "no zone reported"
    return f"Incubator failure: {fault}, {where}"

  named_last = _substitution(_ELX_LAST_DIGIT_TABLES.get(base), last, "device")
  named_second = _substitution(_ELX_SECOND_DIGIT_TABLES.get(base), second, "detail")

  # Substitute with a leading space so that adjacent placeholders, as in
  # "{test}<chip>", do not run together; the whitespace is normalised after.
  expanded = re.sub(r"<[^>]*>", " " + named_last, description)
  expanded = re.sub(r"\{[^}]*\}", " " + named_second, expanded)
  expanded = " ".join(expanded.split())

  if len(expanded) == 0:
    return description
  # A description that led with a placeholder now starts lower-case.
  return expanded[0].upper() + expanded[1:]


def _substitution(table: Optional[Dict[str, str]], digit: str, noun: str) -> str:
  """Expand one templated digit.

  A digit of ``0`` is meaningful where the table defines it, as motor ``0`` is the
  carrier X axis. Where it is not defined it means the detail does not apply, so
  the placeholder is dropped rather than rendered as "device 0".
  """
  if table is not None and digit in table:
    return table[digit]
  if digit == "0":
    return ""
  return f"{noun} {digit}"


def is_elx_status(response: bytes) -> bool:
  """Whether a five-byte reply is an ELx status string rather than an RS one."""
  return (
    len(response) == ELX_STATUS_LENGTH
    and response.endswith(ETX)
    and not response.startswith(RS)
    and all(c in b"0123456789ABCDEFabcdef" for c in response[:4])
  )


def parse_elx_status(response: bytes) -> str:
  """Validate an ELx status string and return its four-character code.

  Raises:
    PowerWaveFatalError: For the ``A``-prefixed errors, which need a power cycle.
    PowerWaveStatusError: For any other non-zero code.
    PowerWaveParseError: If the response is not a well-formed ELx status string.
  """
  if not is_elx_status(response):
    raise PowerWaveParseError(f"Malformed ELx status string (page B-21): {response!r}")

  code = response[:4].decode("ascii").upper()
  if code == "0000":
    return code

  description = describe_elx_status(code) or "Unknown ELx status code"
  if code.startswith("A"):
    raise PowerWaveFatalError(code, description)
  raise PowerWaveStatusError(code, description)


# --------------------------------------------------------------------------------------
# Spectral scan (page B-14)
# --------------------------------------------------------------------------------------

#: Byte 14 of the scan request. A series lets several adjacent strips share one
#: calibration and one first pass, but they must be scanned left to right.
SCAN_SERIES_OPTIONS: Dict[str, int] = {"single": 0, "first": 1, "next": 2, "last": 3}

SCAN_REQUEST_LENGTH = 18
MAX_SCAN_STEP = 799
SCAN_WHOLE_STRIP = 0  # row "00" scans the entire column

#: Seconds to allow per scanned wavelength, on top of the driver's base timeout.
#: The instrument measures every wavelength before sending any data, so the whole
#: section arrives inside one deadline. Measured at roughly 1 s per wavelength on
#: a PowerWaveX w/ Incubator (firmware R06.20); this leaves generous headroom for
#: calibration passes and strip scans, which measure twice.
SCAN_SECONDS_PER_WAVELENGTH = 6.0


def scan_wavelengths(start: int, stop: int, step: int) -> List[int]:
  """The wavelengths a scan will visit, from start through stop in step increments."""
  return list(range(start, stop + 1, step))


def encode_scan_request(
  row: int,
  column: int,
  start_wavelength: int,
  stop_wavelength: int,
  step: int,
  series: str = "single",
  calibrate: bool = False,
  shake_time: int = 0,
  shake_speed: int = 0,
) -> bytes:
  """Build the 18-byte SCAN PLATE request (page B-14).

  The caller sends this after the ``'&'`` command byte and follows it with ``ETX``.

  Args:
    row: Plate row from 1, or :data:`SCAN_WHOLE_STRIP` (0) to scan the entire
      column. The manual warns against 0 on a 384-well plate.
    column: Plate column, from 1.
    start_wavelength: First wavelength in nm, 200 to 999.
    stop_wavelength: Last wavelength in nm, greater than the start.
    step: Increment in nm, 1 to 799 and no larger than the span.
    series: ``"single"``, ``"first"``, ``"next"`` or ``"last"``. A series shares
      one calibration and pipelines the two-pass reads across adjacent strips.
    calibrate: Calibrate every wavelength before scanning. Without it the
      instrument still calibrates any wavelength not yet scanned since power-up,
      and any with a previous error.
    shake_time: Seconds to shake first, 0 to 99.
    shake_speed: 0 slow, 1 medium, 2 fast, 3 cycle through the speeds.

  Returns:
    Exactly 18 ASCII bytes.
  """
  if series not in SCAN_SERIES_OPTIONS:
    raise ValueError(f"series must be one of {sorted(SCAN_SERIES_OPTIONS)}, got {series!r}")
  if not 0 <= row <= 16:
    raise ValueError(f"row must be 0 (whole strip) or 1-16, got {row}")
  if not 1 <= column <= 24:
    raise ValueError(f"column must be 1-24, got {column}")

  for name, value in (("start_wavelength", start_wavelength), ("stop_wavelength", stop_wavelength)):
    if not MIN_WAVELENGTH <= value <= MAX_WAVELENGTH:
      raise ValueError(f"{name} must be {MIN_WAVELENGTH}-{MAX_WAVELENGTH} nm, got {value}")

  if start_wavelength >= stop_wavelength:
    raise ValueError(
      f"start_wavelength {start_wavelength} must be below stop_wavelength {stop_wavelength}; "
      "at least two wavelengths must be scanned (page B-15)"
    )
  span = stop_wavelength - start_wavelength
  if not 1 <= step <= MAX_SCAN_STEP:
    raise ValueError(f"step must be 1-{MAX_SCAN_STEP} nm, got {step}")
  if step > span:
    raise ValueError(
      f"step {step} exceeds the span {span}; at least two wavelengths must be scanned"
    )

  if not 0 <= shake_time <= 99:
    raise ValueError(f"shake_time must be 0-99 seconds, got {shake_time}")
  if not 0 <= shake_speed <= 3:
    raise ValueError(f"shake_speed must be 0-3, got {shake_speed}")

  request = (
    f"{row:02d}"
    f"{column:02d}"
    f"{start_wavelength:03d}"
    f"{stop_wavelength:03d}"
    f"{step:03d}"
    f"{SCAN_SERIES_OPTIONS[series]:d}"
    f"{1 if calibrate else 0:d}"
    f"{shake_time:02d}"
    f"{shake_speed:d}"
  ).encode("ascii")

  if len(request) != SCAN_REQUEST_LENGTH:
    raise AssertionError(f"Scan request must be {SCAN_REQUEST_LENGTH} bytes, built {len(request)}")
  return request


def parse_scan_wavelength_list(section: bytes) -> List[int]:
  """Parse a scan's calibration or first-pass wavelength list (page B-14).

  Both sections are a run of three ASCII digits followed by ``CR``, closed by
  ``^Z``. Either may be empty: the manual notes the terminator is returned alone
  when no calibration was performed, and that the first-pass terminator is always
  sent even when no first pass was needed.
  """
  body = section[:-1] if section.endswith(SUB) else section
  wavelengths = []
  for field in body.split(CR):
    field = field.strip()
    if len(field) == 0:
      continue
    # Exactly three unsigned digits, per the manual. A bare int() would also take
    # "-1", "+24" and "4599612", none of which are wavelengths.
    if re.fullmatch(rb"\d{3}", field) is None:
      raise PowerWaveParseError(f"Wavelength in scan section is not three digits: {field!r}")
    wavelengths.append(int(field))
  return wavelengths


def scan_checksum(section: bytes) -> int:
  """Checksum of a scan's data section (page B-15).

  The sum runs from the first byte after the ``CR`` data start code through and
  including the ``^Z`` terminator, mod 256 - the same rule as READ PLATE. There
  is one checksum for the whole scan, not one per wavelength.

  Verified against a PowerWaveX w/ Incubator (firmware R06.20): a single-well
  400-440 nm scan in 10 nm steps closed with ``^Z`` and checksum 238, and an
  eight-row strip scan of 400-420 nm with checksum 70.

  Args:
    section: The data section through and including ``^Z``, with or without the
      leading ``CR`` start code.
  """
  body = section[1:] if section.startswith(CR) else section
  return sum(body) % 256


def verify_scan_checksum(section: bytes, checksum: int) -> None:
  """Check a scan data section against its transmitted checksum byte (page B-15).

  Raises:
    PowerWaveChecksumError: If the checksum does not match.
  """
  expected = scan_checksum(section)
  if expected != checksum:
    raise PowerWaveChecksumError(
      f"Scan data checksum mismatch: computed {expected}, instrument sent {checksum}"
    )


def parse_scan_data(section: bytes, num_wells: int) -> Tuple[List[int], List[List[float]]]:
  """Parse a scan's whole data section (page B-14).

  The manual describes the data as a series of blocks, which reads as though each
  wavelength were terminated by its own ``^Z`` and checksum. It is not. The
  instrument sends one ``CR LF``-terminated line per wavelength - the wavelength
  as three ASCII digits, then one comma-prefixed reading per well - and closes
  the entire series with a single ``^Z`` and a single checksum byte::

      <CR>400,+0000<CR><LF>410,+0000<CR><LF>...<^Z><checksum>

  Readings use the same encoding as a plate read: a sign and four digits with no
  decimal point, or ``*****`` when overrange. Verified against a PowerWaveX w/
  Incubator (firmware R06.20).

  Args:
    section: The data section through and including ``^Z``, with or without the
      leading ``CR`` start code.
    num_wells: Readings expected per wavelength: 1 for a single well, or the
      plate's row count for a whole strip.

  Returns:
    The wavelengths in nm, and the readings for each, indexed
    ``[wavelength][well]``.

  Raises:
    PowerWaveParseError: If a line is malformed, or any wavelength does not carry
      exactly ``num_wells`` readings.
  """
  body = section[1:] if section.startswith(CR) else section
  if not body.endswith(SUB):
    raise PowerWaveParseError(
      f"Scan data section is not terminated by ^Z, so it is truncated: ...{section[-24:]!r}"
    )
  body = body[:-1]

  wavelengths: List[int] = []
  data: List[List[float]] = []

  for line in body.split(CR + LF):
    line = line.strip(CR + LF)
    if len(line) == 0:
      continue

    # Validate the whole line rather than scanning it for anything that looks
    # like a reading. The checksum is the first guard, but it admits one
    # corruption in 256, and a lax parse turns those into plausible absorbances:
    # "537-7450" would otherwise read as 537 nm at -7.450 OD.
    fields = line.split(b",")
    if re.fullmatch(rb"\d{3}", fields[0]) is None:
      raise PowerWaveParseError(
        f"Scan line does not start with a three-digit wavelength: {line[:24]!r}"
      )
    wavelength = int(fields[0])

    readings: List[float] = []
    for field in fields[1:]:
      reading = _READING_RE.fullmatch(field)
      if reading is None:
        raise PowerWaveParseError(
          f"Malformed reading at {wavelength} nm: {field!r} in {line[:24]!r}"
        )
      if reading.group("overrange") is not None:
        readings.append(OVERRANGE)
      else:
        magnitude = int(reading.group("magnitude")) / 1000
        readings.append(magnitude if reading.group("sign") == b"+" else -magnitude)

    if len(readings) != num_wells:
      raise PowerWaveParseError(
        f"Expected {num_wells} readings at {wavelength} nm, parsed {len(readings)} "
        f"from {line[:24]!r}"
      )

    wavelengths.append(wavelength)
    data.append(readings)

  if len(wavelengths) == 0:
    raise PowerWaveParseError(f"Scan data section holds no wavelengths: {section[:32]!r}")

  return wavelengths, data
