"""KingFisher Presto magnetic particle processor over USB HID."""

import asyncio
import base64
import logging
import re
import xml.etree.ElementTree as ET
import zlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import TracebackType
from typing import Literal, Optional, cast
from xml.sax.saxutils import quoteattr

from pylabrobot.io.hid import HID
from pylabrobot.thermo_fisher.kingfisher.bdz import BdzProtocol, bdz_variant, write_bdz
from pylabrobot.thermo_fisher.kingfisher.presto.connection import (
  PrestoConnection,
  PrestoError,
  command_xml,
  response_error,
)

logger = logging.getLogger(__name__)
Location = Literal["processing", "loading"]


def _duration(value: Optional[str]) -> Optional[float]:
  """Convert the instrument's XML duration to seconds."""
  if value is None:
    return None
  match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value)
  if match is None or not any(match.groups()):
    raise PrestoError(f"Invalid instrument duration: {value!r}")
  hours, minutes, seconds = (float(part or 0) for part in match.groups())
  return hours * 3600 + minutes * 60 + seconds


@dataclass(frozen=True)
class PrestoStatus:
  """Current instrument status and any reported fault."""

  status: Literal["Idle", "Busy", "In error"]
  error_code: Optional[int] = None
  error_text: Optional[str] = None


@dataclass(frozen=True)
class ProtocolTimeLeft:
  """Remaining run time and time until the next pause, in seconds."""

  time_left: Optional[float]
  time_to_pause: Optional[float]


@dataclass(frozen=True)
class ProtocolStep:
  """Named step under a tip, with its reported duration in seconds."""

  tip: str
  name: str
  duration: Optional[float]


@dataclass(frozen=True)
class ProtocolDuration:
  """Protocol timing reported by the instrument, in seconds."""

  name: str
  total_duration: Optional[float]
  steps: tuple[ProtocolStep, ...]


@dataclass(frozen=True)
class PrestoEvent:
  """Instrument event with its full XML details preserved."""

  name: str
  details: ET.Element

  @property
  def requires_acknowledgement(self) -> bool:
    """Whether the operator must complete an action before acknowledging."""
    return self.name in ("LoadPlate", "RemovePlate", "ChangePlate", "Pause")


class KingFisherPresto:
  """Control a Presto directly through its XML command/event protocol.

  The driver has not been verified on hardware. ``setup()`` connects without
  moving the turntable. Nests are numbered 1 and 2; their position is unknown
  until a rotation completes. Protocol timing remains instrument-controlled.

  Args:
    serial_number: Select a particular instrument when several are connected.
    timeout: Command and rotation deadline in seconds.
  """

  _position_at_processing: Optional[int]

  def _on_event(self, event: ET.Element) -> None:
    """Invalidate inferred position when firmware execution may move the table."""
    name = event.get("name")
    logger.info("Presto event: %s", name)
    if name == "Ready":
      self._ready_generation = self._position_generation
    if name in ("Error", "Aborted", "StepStarted", "LoadPlate", "RemovePlate", "ChangePlate"):
      self._position_at_processing = None
      self._position_generation += 1

  def __init__(self, serial_number: Optional[str] = None, timeout: float = 30.0) -> None:
    """Configure a USB HID connection without opening hardware."""
    self.io = HID("KingFisher Presto", vid=0x0AB6, pid=0x02C9, serial_number=serial_number)
    self._connection = PrestoConnection(self.io, self._on_event, timeout)
    self._setup_finished = False
    self._lifecycle = asyncio.Lock()
    self._motion = asyncio.Lock()
    self._position_generation = 0
    self._ready_generation = 0
    self._position_at_processing = None
    self.instrument: Optional[str] = None
    self.version: Optional[str] = None
    self.serial: Optional[str] = None

  def _clear_state(self) -> None:
    """Forget state that belongs to the previous connection."""
    self._setup_finished = False
    self._position_generation += 1
    self._position_at_processing = None
    self.instrument = self.version = self.serial = None

  async def _command(self, name: str, **attributes: Optional[str]) -> ET.Element:
    """Send a command only after successful setup."""
    if not self._setup_finished:
      raise PrestoError("Call setup() before using the Presto")
    return await self._connection.command(command_xml(name, **attributes))

  async def get_status(self) -> PrestoStatus:
    """Read Idle, Busy, or In error, preserving any error code and text."""
    if not self._setup_finished:
      raise PrestoError("Call setup() before using the Presto")
    result = await self._connection.command(command_xml("GetStatus"), raise_on_error=False)
    status = (result.findtext("Status") or "").strip()
    if status not in ("Idle", "Busy", "In error"):
      raise PrestoError(f"Unexpected Presto status: {status!r}")
    if status != "Idle":
      self._position_at_processing = None
      self._position_generation += 1
    error = response_error(result) if result.find("Error") is not None else None
    return PrestoStatus(
      cast(Literal["Idle", "Busy", "In error"], status),
      error.code if error else None,
      str(error) if error else None,
    )

  async def setup(self) -> None:
    """Connect and read identity/status without moving or acknowledging faults."""
    async with self._lifecycle:
      if self._setup_finished:
        self._connection.check_open()
        return
      logger.warning(
        "KingFisher Presto is unverified on hardware. Please report successful testing "
        "with the firmware version and update this warning after verification."
      )
      self._clear_state()
      self._connection.io = self.io
      try:
        await self._connection.setup()
        result = await self._connection.command(command_xml("Connect"))
        self.instrument = result.findtext("Instrument")
        self.version = result.findtext("Version")
        self.serial = result.findtext("Serial")
        self._setup_finished = True
        status = await self.get_status()
        logger.info("Connected to %s (%s): %s", self.instrument, self.serial, status.status)
        if status.status != "Idle":
          logger.warning("Presto connected with status %s: %s", status.status, status.error_text)
      except BaseException:
        self._clear_state()
        await self._connection.stop()
        raise

  async def stop(self) -> None:
    """Close the connection; use stop_protocol() to stop instrument execution."""
    async with self._lifecycle:
      try:
        if self._setup_finished:
          try:
            await self._connection.disconnect()
          except (PrestoError, OSError, asyncio.TimeoutError):
            logger.debug("Presto disconnect unavailable; closing transport", exc_info=True)
      finally:
        self._clear_state()
        await self._connection.stop()
        logger.info("Presto connection closed")

  async def __aenter__(self) -> "KingFisherPresto":
    """Connect for an async context."""
    await self.setup()
    return self

  async def __aexit__(
    self,
    exc_type: Optional[type[BaseException]],
    exc_value: Optional[BaseException],
    traceback: Optional[TracebackType],
  ) -> None:
    """Close the connection on context exit."""
    await self.stop()

  async def get_protocol_time_left(self, protocol: Optional[str] = None) -> ProtocolTimeLeft:
    """Read remaining protocol/step time in seconds."""
    result = await self._command("GetProtocolTimeLeft", protocol=protocol)
    left, pause = result.find("TimeLeft"), result.find("TimeToPause")
    return ProtocolTimeLeft(
      _duration(left.get("value") if left is not None else None),
      _duration(pause.get("value") if pause is not None else None),
    )

  async def get_protocol_duration(self, protocol: str) -> ProtocolDuration:
    """Read named tips/steps and their durations without downloading the file."""
    result = await self._command("GetProtocolDuration", protocol=protocol)
    total = result.find("Total")
    steps = []
    for tip in result.findall("Tip"):
      timestamps = tip.findall("TimeStamp")
      for start in timestamps:
        if start.get("type") != "Start":
          continue
        name = start.get("step", "")
        stop = next(
          (item for item in timestamps if item.get("type") == "Stop" and item.get("step") == name),
          None,
        )
        steps.append(
          ProtocolStep(
            tip.get("name", ""), name, _duration(stop.get("duration") if stop is not None else None)
          )
        )
    return ProtocolDuration(
      protocol, _duration(total.get("duration") if total is not None else None), tuple(steps)
    )

  async def list_protocols(self) -> tuple[list[str], int]:
    """Return stored protocol names and memory utilization as a percentage."""
    result = await self._command("ListProtocols")
    names = [(item.text or "").strip() for item in result.findall("Protocols/Protocol")]
    memory = result.find("MemoryUsed")
    return names, int(memory.get("value", "0")) if memory is not None else 0

  async def upload_protocol(self, name: str, protocol: bytes | BdzProtocol) -> None:
    """Upload a Presto BDZ, overwriting the named stored protocol.

    Bytes are transferred unchanged, preserving BindIt features outside the
    structured codec's subset. A structured protocol is serialized first.
    """
    if not name or len(name.encode("utf-8")) > 100:
      raise ValueError("Protocol name must contain 1 to 100 UTF-8 bytes")
    data = write_bdz(protocol) if isinstance(protocol, BdzProtocol) else protocol
    if bdz_variant(data) != "presto":
      raise ValueError("Only Presto protocols can be uploaded to a Presto")
    if not self._setup_finished:
      raise PrestoError("Call setup() before using the Presto")
    encoded = base64.b64encode(data).decode("ascii")
    lines = "\n".join(encoded[index : index + 24] for index in range(0, len(encoded), 24))
    checksum = zlib.crc32(data) & 0xFFFFFFFF
    payload = (
      f'<Cmd name="UploadProtocol" protocol={quoteattr(name)} crc="{checksum}">'
      f"<![CDATA[\n{lines}\n]]></Cmd>\n"
    )
    await self._connection.command(payload.encode("utf-8"))
    logger.info("Uploaded Presto protocol %s", name)

  async def download_protocol(self, name: str) -> bytes:
    """Download unchanged BDZ bytes; read_bdz() can decode supported Mix protocols."""
    result = await self._command("DownloadProtocol", protocol=name)
    return base64.b64decode("".join((result.text or "").split()), validate=True)

  async def remove_protocol(self, name: str) -> None:
    """Remove a stored protocol by name."""
    await self._command("RemoveProtocol", protocol=name)

  async def start_protocol(
    self, protocol: str, *, tip: Optional[str] = None, step: Optional[str] = None
  ) -> None:
    """Start a stored protocol or one named tip/step; consume events for completion."""
    if (tip is None) != (step is None):
      raise ValueError("Provide both tip and step for single-step execution")
    async with self._motion:
      self._position_at_processing = None
      await self._command("StartProtocol", protocol=protocol, tip=tip, step=step)
    logger.info("Started Presto protocol %s (tip=%s, step=%s)", protocol, tip, step)

  async def stop_protocol(self) -> None:
    """Request execution to stop; consume the subsequent terminal event."""
    self._position_at_processing = None
    await self._command("Stop")

  async def acknowledge(self) -> None:
    """Continue after completing a LoadPlate/RemovePlate/ChangePlate/Pause action."""
    await self._command("Acknowledge")

  async def error_acknowledge(self) -> None:
    """Explicitly acknowledge an instrument fault after inspecting it."""
    self._position_at_processing = None
    await self._command("ErrorAcknowledge")

  async def next_event(self, *, attach: bool = False) -> PrestoEvent:
    """Return every event, including progress; attach checks an existing run first."""
    if attach:
      status = await self.get_status()
      if status.status != "Busy":
        name = "Ready" if status.status == "Idle" else "Error"
        element = ET.Element("Evt", name=name)
        if name == "Error":
          error = ET.SubElement(element, "Error")
          if status.error_code is not None:
            error.set("code", str(status.error_code))
          error.text = status.error_text
        return PrestoEvent(name, element)
    element = await self._connection.get_event()
    return PrestoEvent(element.get("name", ""), element)

  async def events(self) -> AsyncIterator[PrestoEvent]:
    """Yield typed events, leaving physical actions and acknowledgement to the caller."""
    while True:
      yield await self.next_event()

  def get_turntable_state(self) -> dict[int, Optional[Location]]:
    """Return inferred nest locations; None means the physical position is unknown."""
    if self._position_at_processing is None:
      return {1: None, 2: None}
    return {
      nest: "processing" if nest == self._position_at_processing else "loading" for nest in (1, 2)
    }

  async def _rotate(self, position: int, location: Location) -> None:
    """Move the specified nest to a location, waiting for Ready/Error/Aborted.

    Repeating a completed move to the same location does not swap the nests.
    All events remain available through next_event(), including completion.
    """
    if not isinstance(position, int) or isinstance(position, bool) or position not in (1, 2):
      raise ValueError("position must be 1 or 2")
    if location not in ("processing", "loading"):
      raise ValueError("location must be 'processing' or 'loading'")
    if not self._setup_finished:
      raise PrestoError("Call setup() before using the Presto")
    self._connection.check_open()
    if self.get_turntable_state()[position] == location:
      return
    self._position_at_processing = None
    result = await self._connection.command(
      command_xml("Rotate", nest=str(position), position="1" if location == "processing" else "2"),
      terminal=True,
    )
    if result.get("name") == "Error":
      raise response_error(result)
    if result.get("name") == "Aborted":
      raise PrestoError("Turntable rotation aborted")
    if self._position_generation == self._ready_generation:
      self._position_at_processing = position if location == "processing" else 3 - position

  async def rotate(self, position: int, location: Location) -> None:
    """Move a specific nest to loading or processing and wait for completion.

    Repeated moves to the same confirmed location do not swap nests. Position
    becomes unknown on execution, faults, interruption, or a new session.
    """
    async with self._motion:
      await self._rotate(position, location)

  async def load_plate(self, position: int) -> None:
    """Move a specific nest to processing; the caller places the plate beforehand."""
    await self.rotate(position, "processing")

  async def abort(self) -> None:
    """Abort execution; call stop() then setup() to start a fresh connection."""
    self._position_at_processing = None
    self._position_generation += 1
    await self._connection.abort()
