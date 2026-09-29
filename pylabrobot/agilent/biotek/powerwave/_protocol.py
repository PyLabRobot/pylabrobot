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
  """Validate a status string and return its three-character code.

  Args:
    response: The raw five-byte status string, ``RS`` + three characters + ``ETX``.

  Returns:
    The three-character status code, which is ``"000"`` on success.

  Raises:
    PowerWaveParseError: If the response is not a well-formed status string.
    PowerWaveStatusError: If the instrument reported an error.
  """
  if len(response) != STATUS_LENGTH or not response.startswith(RS) or not response.endswith(ETX):
    raise PowerWaveParseError(
      f"Malformed status string (expected RS + 3 bytes + ETX, page B-21): {response!r}"
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

#: Byte 31 as used by the kinetic runs that were verified on real hardware in 2021:
#: shake, shake before every read, continuous shake, and use the total kinetic time.
KINETIC_FLAGS = (
  FLAG_SHAKE | FLAG_SHAKE_EVERY_READ | FLAG_SHAKE_CONTINUOUS | FLAG_USE_TOTAL_KINETIC_TIME
)


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
    PowerWaveParseError: If the block does not hold exactly
      ``num_rows * num_columns`` readings.
  """
  expected = num_rows * num_columns
  readings: List[float] = []

  # latin-1 is byte-transparent, so a binary checksum prefix cannot raise here.
  for match in _READING_RE.finditer(frame):
    if match.group("overrange") is not None:
      readings.append(OVERRANGE)
    else:
      magnitude = int(match.group("magnitude")) / 1000
      readings.append(magnitude if match.group("sign") == b"+" else -magnitude)

  if len(readings) != expected:
    raise PowerWaveParseError(
      f"Expected {expected} readings for a {num_rows}x{num_columns} plate, parsed "
      f"{len(readings)}. The instrument's plate geometry or strip range may not match."
    )

  return [readings[row * num_columns : (row + 1) * num_columns] for row in range(num_rows)]


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
