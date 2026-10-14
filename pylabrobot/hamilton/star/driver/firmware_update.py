"""Validation and results for STAR pipetting-channel firmware downloads."""

import datetime
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.features.pipettes import PipetteCycleCounts


def _firmware_reply_body(response: str, module: str, command: str) -> str:
  """Remove the matching command header and optional success field from a status reply."""
  match = re.fullmatch(re.escape(module + command) + r"id\d{4}(.*)", response)
  if match is None:
    raise ValueError(f"Malformed {module} {command} reply: {response!r}")
  body = match[1].removeprefix("er00")
  if not body:
    raise ValueError(f"Empty {module} {command} reply: {response!r}")
  return body


def _require_firmware_acknowledgement(response: str, module: str, command: str) -> None:
  """Require an explicit success reply before continuing an erase or program operation."""
  if re.fullmatch(re.escape(module + command) + r"id\d{4}er00", response) is None:
    raise RuntimeError(f"Missing successful {module} {command} acknowledgement: {response!r}")


def _validate_ach_record(record: str) -> bytes:
  """Decode a fixed-width ACH record and verify its length, type, and checksum.

  This format pads every record to 32 data bytes, including the address and EOF records.
  It is not the variable-width Intel HEX format.
  """
  if re.fullmatch(r":[0-9A-Fa-f]{74}", record) is None:
    raise ValueError("An ACH record must contain ':' followed by 74 hexadecimal characters")
  data = bytes.fromhex(record[1:])
  if data[0] != 32 or sum(data) % 256 != 0:
    raise ValueError("Invalid ACH record length or checksum")
  if data[3] not in (0, 1, 4):
    raise ValueError(f"Unsupported ACH record type: {data[3]}")
  return data


@dataclass(frozen=True)
class _PipetteFirmwareImage:
  """A fully validated GRUPXS image held in memory before any flash is erased."""

  records: Tuple[str, ...]
  version: str
  build_date: datetime.date
  sha256: str

  @classmethod
  def from_file(cls, path: Path, expected_version: str) -> "_PipetteFirmwareImage":
    """Read a pipetting-channel image and check its embedded version and every record."""
    if not path.name.upper().startswith("GRUPXS") or path.suffix.upper() != ".ACH":
      raise ValueError("Expected a GRUPXS pipetting-channel .ACH file")
    raw = path.read_bytes()
    records = tuple(raw.decode("ascii").splitlines())
    if not records:
      raise ValueError("Empty ACH image")
    payload = bytearray()
    have_address = False
    for index, record in enumerate(records):
      data = _validate_ach_record(record)
      kind = data[3]
      if kind == 4:
        if data[1:3] != b"\x00\x00":
          raise ValueError("Invalid ACH address record")
        have_address = True
      elif kind == 0:
        if not have_address:
          raise ValueError("ACH data precedes its address record")
        payload.extend(data[4:-1])
      elif index != len(records) - 1:
        raise ValueError("ACH EOF record must be last")
    if bytes.fromhex(records[-1][1:])[3] != 1 or not payload:
      raise ValueError("ACH image requires data and a final EOF record")
    versions = set(re.findall(rb"\d+\.\d+S [a-z] \d{4}-\d{2}-\d{2}", payload))
    if versions != {expected_version.encode("ascii")}:
      raise ValueError("ACH embedded version does not match expected_version")
    return cls(
      records=records,
      version=expected_version,
      build_date=datetime.date.fromisoformat(expected_version[-10:]),
      sha256=hashlib.sha256(raw).hexdigest(),
    )


@dataclass(frozen=True)
class PipetteFirmwareUpdateResult:
  """A verified transfer; power cycling and normal initialization are still required.

  The counters are reported without rewriting them when a firmware restart changes them.
  """

  previous_version: str
  version: str
  sha256: str
  records_programmed: int
  cycle_counts_before: "PipetteCycleCounts"
  cycle_counts_after: "PipetteCycleCounts"


@dataclass(frozen=True)
class _PipetteFirmwareState:
  """Persistent identity, configuration, and calibration to compare across an update."""

  adjustments: str
  extended_adjustments: str
  adjustment_status: str
  technical_status: str
  monitoring_configuration: str
  plunger_capacity: int
