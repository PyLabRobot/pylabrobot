"""Validation, transfer, and verification of standard STAR component firmware."""

import asyncio
import datetime
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Tuple, Union, cast

if TYPE_CHECKING:
  from pylabrobot.hamilton.star.driver.features.pipettes import PipetteCycleCounts
  from pylabrobot.hamilton.star.driver.master import STARDriver

logger = logging.getLogger(__name__)

# Major versions distinguish incompatible controller generations.
_FIRMWARE_FAMILIES = {
  "PX": ("GRUPXS", (4,)),
  "X0": ("GRUX0S", (1, 5)),
  "R0": ("GRUR0S", (3, 4)),
  "I0": ("GRUALS", (3,)),
  "H0": ("GRUH0S", (2, 5)),
  "D0": ("GRUD0S", (1,)),
}
_VERSION_PATTERN = rb"\d+\.\d+S(?: [a-z])? \d{4}-\d{2}-\d{2}(?: \([XH]0 XE167\))?"
_X_DRIVE_ADJUSTMENT_PARAMETERS = (
  "kx",
  "kp",
  "kd",
  "ki",
  "il",
  "le",
  "qp",
  "qd",
  "qi",
  "ql",
  "qe",
  "px",
  "ks",
  "mp",
  "md",
  "mi",
  "ml",
  "me",
  "np",
  "nd",
  "ni",
  "nl",
  "ne",
  "ps",
  "kc",
  "up",
  "ud",
  "ui",
  "ul",
  "ue",
  "tp",
  "td",
  "ti",
  "tl",
  "te",
  "pc",
  "ax",
  "as",
)


def _firmware_version_parts(version: str) -> Tuple[int, datetime.date]:
  """Require a complete standard-board version and extract its generation and date."""
  if re.fullmatch(_VERSION_PATTERN, version.encode("ascii")) is None:
    raise ValueError(f"Unsupported firmware version: {version!r}")
  date = re.search(r"\d{4}-\d{2}-\d{2}", version)
  assert date is not None
  return int(version.split(".")[0]), datetime.date.fromisoformat(date[0])


def _firmware_reply_body(response: str, module: str, command: str) -> str:
  """Remove the matching command header and optional success field from a status reply."""
  match = re.fullmatch(re.escape(module + command) + r"id\d{4}(.*)", response)
  if match is None:
    raise ValueError(f"Malformed {module} {command} reply: {response!r}")
  body = match[1].removeprefix("er00")
  if not body:
    raise ValueError(f"Empty {module} {command} reply: {response!r}")
  if re.search(r"[A-Z][0-9A-Z][A-Z]{2}id", body):
    raise ValueError(f"Concatenated {module} {command} reply: {response!r}")
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
class _FirmwareImage:
  """A fully validated fixed-width ACH image held in memory before flash is erased."""

  module: str
  records: Tuple[str, ...]
  version: str
  build_date: datetime.date
  sha256: str

  @classmethod
  def from_file(cls, path: Path, expected_version: str, module: str) -> "_FirmwareImage":
    """Validate the target family, embedded version, and every record before device I/O."""
    family = "PX" if re.fullmatch(r"P[1-9A-G]", module) else module
    if family not in _FIRMWARE_FAMILIES:
      raise ValueError(f"Unsupported firmware target: {module}")
    prefix, generations = _FIRMWARE_FAMILIES[family]
    if not path.name.upper().startswith(prefix) or path.suffix.upper() != ".ACH":
      raise ValueError(f"Expected a {prefix} .ACH file for {module}")
    generation, build_date = _firmware_version_parts(expected_version)
    if generation not in generations:
      raise ValueError(f"Unsupported firmware generation for {module}: {expected_version}")
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
    versions = set(re.findall(_VERSION_PATTERN, payload))
    if versions != {expected_version.encode("ascii")}:
      raise ValueError("ACH embedded version does not match expected_version")
    return cls(
      module=module,
      records=records,
      version=expected_version,
      build_date=build_date,
      sha256=hashlib.sha256(raw).hexdigest(),
    )

  def check_compatible(self, previous_version: str, allow_downgrade: bool) -> None:
    """Reject controller-generation changes and require explicit consent for older images."""
    previous_generation, previous_date = _firmware_version_parts(previous_version)
    generation, _ = _firmware_version_parts(self.version)
    if previous_generation != generation:
      raise ValueError("Firmware cannot cross controller generations, even with allow_downgrade")
    if self.build_date < previous_date and not allow_downgrade:
      raise ValueError("An older firmware image requires allow_downgrade=True")


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


@dataclass(frozen=True)
class FirmwareUpdateResult:
  """A verified component transfer; power cycling and initialization are still required.

  Counter keys are gripping_cycles for iSWAP, barcode_scans for autoload, and tip_pickups,
  tip_discards, aspirations, dispenses for heads. X-drive counters are not read and return {}.
  """

  module: str
  previous_version: str
  version: str
  sha256: str
  records_programmed: int
  cycle_counts_before: Dict[str, int]
  cycle_counts_after: Dict[str, int]


class _FirmwareDownloader:
  """Named commands and the shared AP/DE/DP transfer for standard component boards."""

  def __init__(self, driver: "STARDriver", module: str):
    """Bind one individual board; broadcast and master targets are not supported."""
    if module not in ("X0", "R0", "I0", "H0", "D0") and not re.fullmatch(r"P[1-9A-G]", module):
      raise ValueError(f"Unsupported firmware target: {module}")
    self._driver = driver
    self.module = module

  async def request_status(self) -> int:
    """Read the status bitmask; only 0x0800 denotes an error-free downloader."""
    reply = await self._driver.send_command(self.module, "RQ", fmt="rq****")
    return cast(int, reply["rq"])

  async def request_eeprom_status(self) -> int:
    """Read the EEPROM validity bitmask; zero denotes valid stored data."""
    reply = await self._driver.send_command(self.module, "QV", fmt="qv****")
    return cast(int, reply["qv"])

  async def request_firmware_version(self) -> str:
    """Read the complete application version, rejecting malformed responses."""
    reply = await self._driver.send_command(self.module, "RF")
    body = _firmware_reply_body(reply, self.module, "RF")
    if not body.startswith("rf"):
      raise ValueError(f"Malformed firmware version: {body!r}")
    version = body[2:]
    _firmware_version_parts(version)
    return version

  async def _switch_firmware_mode(self) -> None:
    """Send AP without an ID or acknowledgement to toggle application/downloader mode."""
    await self._driver.send_raw_command(f"{self.module}AP", wait=False)

  async def _erase_firmware(self) -> None:
    """Erase application flash in download mode and require explicit acknowledgement."""
    reply = await self._driver.send_command(self.module, "DE", read_timeout=90)
    _require_firmware_acknowledgement(reply, self.module, "DE")

  async def _program_firmware_record(self, record: str) -> None:
    """Program one padded ACH record, requiring acknowledgement without retry."""
    _validate_ach_record(record)
    reply = await self._driver.send_command(self.module, "DP", dp=record, read_timeout=30)
    _require_firmware_acknowledgement(reply, self.module, "DP")

  async def program(self, image: _FirmwareImage) -> None:
    """Transfer a preflighted image under the driver's exclusive firmware-update session.

    A failed write leaves the board in the downloader. DE/DP are never sent until RQ confirms
    download mode: these opcodes can be movement commands in a head's application.
    """
    if self._driver._firmware_update_task is not asyncio.current_task():
      raise RuntimeError("Firmware programming requires an exclusive firmware update session")
    if image.module != self.module:
      raise ValueError("Firmware image was validated for another module")
    await self._switch_firmware_mode()
    await asyncio.sleep(15)
    if await self.request_status() != 0x0800:
      raise RuntimeError(f"{self.module} did not enter download mode with clear error bits")
    await self._erase_firmware()
    for index, record in enumerate(image.records, 1):
      await self._program_firmware_record(record)
      if index % 100 == 0 or index == len(image.records):
        logger.info(
          "%s: %s/%s firmware records acknowledged", self.module, index, len(image.records)
        )
    await self._switch_firmware_mode()
    await asyncio.sleep(18)
    if await self.request_status() != 0:
      raise RuntimeError(f"{self.module} status is not clear after the firmware restart")
    version = await self.request_firmware_version()
    if version != image.version:
      raise RuntimeError(f"Expected firmware {image.version!r}, received {version!r}")
    if await self.request_eeprom_status() != 0:
      raise RuntimeError(f"{self.module} EEPROM verification failed after the firmware update")

  async def _request_adjustment_values(self) -> str:
    """Read RK calibration fields verbatim for comparison."""
    reply = await self._driver.send_command(self.module, "RK")
    return _firmware_reply_body(reply, self.module, "RK")

  async def _request_x_drive_adjustment_parameter(self, parameter: str) -> str:
    """Read one legacy X-drive calibration field, including its complete array when applicable."""
    if parameter not in _X_DRIVE_ADJUSTMENT_PARAMETERS:
      raise ValueError(f"Not an X-drive calibration parameter: {parameter}")
    reply = await self._driver.send_command("X0", "RA", ra=parameter)
    body = _firmware_reply_body(reply, "X0", "RA")
    if not body.startswith(parameter) or len(body) == len(parameter):
      raise ValueError(f"Missing X-drive calibration parameter {parameter}: {reply!r}")
    return body

  async def _request_x_drive_adjustment_values(self) -> str:
    """Read every legacy RK field separately, avoiding its USB packet-boundary reply."""
    return "".join(
      [
        await self._request_x_drive_adjustment_parameter(parameter)
        for parameter in _X_DRIVE_ADJUSTMENT_PARAMETERS
      ]
    )

  async def _request_adjustment_status(self) -> str:
    """Read RJ's adjustment date and completion status."""
    reply = await self._driver.send_command(self.module, "RJ")
    return _firmware_reply_body(reply, self.module, "RJ")

  async def _request_technical_status(self) -> str:
    """Read QT's assembly identity fields verbatim."""
    reply = await self._driver.send_command(self.module, "QT")
    return _firmware_reply_body(reply, self.module, "QT")

  async def _request_autoload_configuration(self) -> str:
    """Read the autoload's complete configuration array."""
    reply = await self._driver.send_command("I0", "RA", ra="au")
    return _firmware_reply_body(reply, "I0", "RA")

  async def _request_autoload_initialization_track(self) -> str:
    """Read the autoload's stored scanner initialization track."""
    reply = await self._driver.send_command("I0", "QX")
    return _firmware_reply_body(reply, "I0", "QX")

  async def _request_head_configuration(self) -> str:
    """Read the head's complete hardware configuration array."""
    reply = await self._driver.send_command(self.module, "QU")
    return _firmware_reply_body(reply, self.module, "QU")

  async def _request_head_type(self) -> int:
    """Read the head variant for the controller-generation compatibility check."""
    reply = await self._driver.send_command(self.module, "QG", fmt="qg#")
    return cast(int, reply["qg"])

  async def _request_component_state(self, version: str) -> Dict[str, str]:
    """Capture identity, calibration, and the settings specific to this component."""
    generation, _ = _firmware_version_parts(version)
    state = {
      "adjustments": (
        await self._request_x_drive_adjustment_values()
        if self.module == "X0" and generation == 1
        else await self._request_adjustment_values()
      ),
      "adjustment_status": await self._request_adjustment_status(),
      "technical_status": await self._request_technical_status(),
    }
    if self.module == "I0":
      state["configuration"] = await self._request_autoload_configuration()
      state["initialization_track"] = await self._request_autoload_initialization_track()
    elif self.module in ("H0", "D0"):
      state["configuration"] = await self._request_head_configuration()
      head_type = await self._request_head_type()
      allowed_types = (0, 1, 2) if self.module == "D0" else (2, 3) if generation == 5 else (0, 1)
      if head_type not in allowed_types:
        raise ValueError(f"Unsupported {self.module} head type {head_type} for {version}")
      state["head_type"] = str(head_type)
    return state

  async def _request_component_cycle_counts(self) -> Dict[str, int]:
    """Read documented counters, without sending RV to legacy X-drive firmware."""
    if self.module == "X0":
      return {}
    if self.module in ("R0", "I0"):
      reply = await self._driver.send_command(self.module, "RV", fmt="na##########")
      name = "gripping_cycles" if self.module == "R0" else "barcode_scans"
      return {name: reply["na"]}
    if self.module in ("H0", "D0"):
      reply = await self._driver.send_command(
        self.module, "RV", fmt="na##########nb##########nc##########nd##########"
      )
      return dict(
        zip(
          ("tip_pickups", "tip_discards", "aspirations", "dispenses"),
          (reply["na"], reply["nb"], reply["nc"], reply["nd"]),
        )
      )
    raise ValueError("Pipette counters must be read through Pipettes.request_cycle_counts")

  async def update_component(
    self,
    firmware_file: Union[str, Path],
    *,
    expected_version: str,
    allow_downgrade: bool,
    invalidate_version: Callable[[], None],
  ) -> FirmwareUpdateResult:
    """Validate, transfer, and verify a non-pipette component under the device-wide lock."""
    if self.module.startswith("P"):
      raise ValueError("Use Pipettes.update_firmware for pipetting channels")
    image = _FirmwareImage.from_file(Path(firmware_file), expected_version, self.module)
    async with self._driver._firmware_update_session():
      previous_version = await self.request_firmware_version()
      image.check_compatible(previous_version, allow_downgrade)
      if await self.request_status() != 0 or await self.request_eeprom_status() != 0:
        raise RuntimeError("Component status and EEPROM must be clear before a firmware update")
      master_before = await self._driver.request_firmware_version()
      state_before = await self._request_component_state(previous_version)
      counts_before = await self._request_component_cycle_counts()
      logger.info(
        "Updating %s from %s to %s (sha256 %s)",
        self.module,
        previous_version,
        image.version,
        image.sha256,
      )
      invalidate_version()
      await self.program(image)
      if await self._request_component_state(image.version) != state_before:
        raise RuntimeError(
          "Component calibration, identity, or configuration changed during update"
        )
      if await self._driver.request_firmware_version() != master_before:
        raise RuntimeError("Master firmware changed during the component update")
      counts_after = await self._request_component_cycle_counts()
      if counts_after != counts_before:
        logger.warning(
          "%s cycle counts changed across the firmware restart: %s -> %s",
          self.module,
          counts_before,
          counts_after,
        )
      return FirmwareUpdateResult(
        self.module,
        previous_version,
        image.version,
        image.sha256,
        len(image.records),
        counts_before,
        counts_after,
      )
