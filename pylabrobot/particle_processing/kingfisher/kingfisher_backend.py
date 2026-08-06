"""
KingFisherBackend — shared USB HID backend for all KingFisher instruments.

This is the base class that KingFisherPrestoBackend and KingFisherDuoBackend
both inherit from.  It contains all connection, command, and event logic that
is common across variants.  Variant-specific constants (contract, VID/PID) are
provided by subclasses.

See KingFisher Presto/Duo Interface Specification for Cmd/Res/Evt formats and error codes.
"""

import asyncio
import base64
import logging
import xml.etree.ElementTree as ET
import zlib
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from pylabrobot.machines.backend import MachineBackend

from pylabrobot.particle_processing.kingfisher.bdz import InstrumentContract
from pylabrobot.particle_processing.kingfisher.presto_connection import (
  PrestoConnection,
  PrestoConnectionError,
  format_error_message,
  get_error_code_description,
)

logger = logging.getLogger(__name__)


def _cmd_xml(name: str, **attrs: Optional[str]) -> str:
  """Build a <Cmd> XML string with optional attributes. Tag names/values per spec. None values are omitted."""
  parts = [f'<Cmd name="{name}"']
  for k, v in attrs.items():
    if v is not None:
      parts.append(f' {k}="{v}"')
  parts.append("/>\n")
  return "".join(parts)


def _text(el: Optional[ET.Element]) -> Optional[str]:
  return el.text.strip() if el is not None and el.text else None


class TurntableLocation:
  """Location of a turntable position: processing (under the magnetic head) or loading (load/unload station)."""

  PROCESSING = "processing"
  LOADING = "loading"


def _normalize_location(location: Union[str, TurntableLocation]) -> str:
  """Return 'processing' or 'loading'. Accept TurntableLocation constants or string."""
  s = str(location).strip().lower()
  if s in ("processing", "loading"):
    return s
  raise ValueError(f"location must be 'processing' or 'loading', got {location!r}")


class KingFisherBackend(MachineBackend):
  """Shared USB HID backend for KingFisher instruments (Presto, Duo, and future variants).

  Implements the full XML Cmd/Res/Evt protocol over 64-byte HID reports.
  Subclasses provide the instrument-specific ``contract`` (structural constants)
  and the USB ``VID``/``PID`` defaults.

  Do not instantiate directly — use :class:`KingFisherPrestoBackend` or
  :class:`KingFisherDuoBackend`.
  """

  def __init__(
    self,
    vid: int,
    pid: int,
    serial_number: Optional[str] = None,
    on_event: Optional[Callable[[ET.Element], None]] = None,
  ):
    super().__init__()
    self._vid = vid
    self._pid = pid
    self._serial_number = serial_number
    self._conn = PrestoConnection(
      vid=vid,
      pid=pid,
      serial_number=serial_number,
      on_event=on_event,
    )
    self._instrument: Optional[str] = None
    self._version: Optional[str] = None
    self._serial: Optional[str] = None
    self._position_at_processing: Optional[int] = None

  @property
  def contract(self) -> InstrumentContract:
    """Instrument contract carrying variant-specific structural constants.

    Subclasses must override this to return the appropriate contract
    (``PRESTO_CONTRACT`` or ``DUO_CONTRACT``).
    """
    raise NotImplementedError("Subclasses must define contract")

  async def setup(self, initialize_turntable: bool = False) -> None:
    """Open HID, send Connect, parse instrument/version/serial, start read loop.

    Turntable state is reset to unknown on every setup (covers reconnect/power cycle).
    When initialize_turntable is True, after Connect the backend rotates to power-on state
    (position 1 at processing, position 2 at loading) so positions are known; the table may move.
    """
    await self._conn.setup()
    self._position_at_processing = None
    res = await self._conn.send_command(_cmd_xml("Connect"))
    self._instrument = _text(res.find("Instrument"))
    self._version = _text(res.find("Version"))
    self._serial = _text(res.find("Serial"))
    logger.info(
      "KingFisher connected: %s, version %s, serial %s",
      self._instrument,
      self._version,
      self._serial,
    )
    if initialize_turntable:
      await self.rotate(position=1, location=TurntableLocation.PROCESSING)

  async def stop(self) -> None:
    """Send Disconnect (instrument may not reply), stop read loop, close HID."""
    try:
      await self._conn.send_command(_cmd_xml("Disconnect"))
    except (PrestoConnectionError, asyncio.TimeoutError, asyncio.CancelledError):
      pass
    await self._conn.stop()
    self._instrument = None
    self._version = None
    self._serial = None
    self._position_at_processing = None

  async def connect(self, set_time: Optional[str] = None) -> None:
    """Send Connect command (e.g. to set time). Connection is already established by setup(). setTime: YYYY-MM-DD hh:mm:ss."""
    cmd = _cmd_xml("Connect", setTime=set_time) if set_time else _cmd_xml("Connect")
    res = await self._conn.send_command(cmd)
    self._instrument = _text(res.find("Instrument"))
    self._version = _text(res.find("Version"))
    self._serial = _text(res.find("Serial"))

  async def disconnect(self) -> None:
    """Send Disconnect. Instrument may not reply if connection already closed."""
    await self._conn.send_command(_cmd_xml("Disconnect"))

  async def get_status(self) -> dict:
    """Get instrument status. Returns dict with ok, status, error_code, error_text, error_code_description.

    status is \"Idle\", \"Busy\", or \"In error\" per spec. error_code_description is the standard
    description for the error code (Interface Specification 4.8) when known.
    """
    res = await self._conn.send_command(_cmd_xml("GetStatus"), raise_on_error=False)
    status_el = res.find("Status")
    status = _text(status_el) if status_el is not None else None
    ok = (res.get("ok") or "false").lower() == "true"
    err_el = res.find("Error")
    error_code = int(err_el.get("code", 0)) if err_el is not None and err_el.get("code") else None
    error_text = _text(err_el) if err_el is not None else None
    error_code_description = get_error_code_description(error_code) if error_code is not None else None
    return {
      "ok": ok,
      "status": status or "In error",
      "error_code": error_code,
      "error_text": error_text,
      "error_code_description": error_code_description,
    }

  async def get_protocol_time_left(self, protocol: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Get time left of protocol or single-step execution. Returns time_left and time_to_pause (XML Duration).
    For single-step execution the spec omits TimeToPause; time_to_pause will be None."""
    cmd = _cmd_xml("GetProtocolTimeLeft", protocol=protocol)
    res = await self._conn.send_command(cmd, raise_on_error=False)
    time_left_el = res.find("TimeLeft")
    time_to_pause_el = res.find("TimeToPause")
    time_left = time_left_el.get("value") if time_left_el is not None else None
    time_to_pause = time_to_pause_el.get("value") if time_to_pause_el is not None else None
    return {"time_left": time_left, "time_to_pause": time_to_pause}

  async def get_protocol_duration(self, protocol: str) -> Dict[str, Any]:
    """Get protocol structure (tips and step names/durations) from the instrument.

    Per Interface Spec §5.7 GetProtocolDuration: returns Init, Tip(s) with TimeStamp
    entries (@step, @type, @duration), Finish, and Total duration. Use this to list
    steps without downloading or parsing the BDZ.
    """
    res = await self._conn.send_command(_cmd_xml("GetProtocolDuration", protocol=protocol))
    out: Dict[str, Any] = {"protocol": protocol, "total_duration": None, "tips": []}
    total_el = res.find("Total")
    if total_el is not None:
      out["total_duration"] = total_el.get("duration")
    for tip_el in res.findall("Tip"):
      tip_name = tip_el.get("name", "")
      steps: List[Dict[str, Optional[str]]] = []
      for ts in tip_el.findall("TimeStamp"):
        if ts.get("type") == "Start":
          step_name = ts.get("step", "")
          duration: Optional[str] = None
          for ts2 in tip_el.findall("TimeStamp"):
            if ts2.get("type") == "Stop" and ts2.get("step") == step_name:
              duration = ts2.get("duration")
              break
          steps.append({"name": step_name, "duration": duration})
      out["tips"].append({"name": tip_name, "steps": steps})
    return out

  async def list_protocols(self) -> Tuple[List[str], int]:
    """List protocols in instrument memory. Returns (protocol_names, memory_used_percent)."""
    res = await self._conn.send_command(_cmd_xml("ListProtocols"))
    protocols_el = res.find("Protocols")
    names: List[str] = []
    if protocols_el is not None:
      for p in protocols_el.findall("Protocol"):
        if p.text:
          names.append(p.text.strip())
    mem_el = res.find("MemoryUsed")
    memory_used = int(mem_el.get("value", 0)) if mem_el is not None else 0
    return (names, memory_used)

  async def upload_protocol(self, name: str, bdz_bytes: bytes) -> None:
    """Upload a protocol to instrument memory.

    Per Interface Spec §5.16: the .bdz file is base64-encoded and sent in a
    CDATA section with a CRC32 checksum.  Lines in the CDATA section must be
    a multiple of 4 base64 characters (as required by the spec).

    Overwrites any existing protocol with the same name.

    Generate the bdz_bytes argument with
    ``pylabrobot.particle_processing.kingfisher.bdz.write_bdz()``.
    """
    crc = zlib.crc32(bdz_bytes) & 0xFFFFFFFF
    b64 = base64.b64encode(bdz_bytes).decode("ascii")
    # 24-char lines = 6 base64 groups; n×4 chars as required by spec
    lines = "\n".join(b64[i : i + 24] for i in range(0, len(b64), 24))
    cmd = (
      f'<Cmd name="UploadProtocol" protocol="{name}" crc="{crc}">'
      f"<![CDATA[\n{lines}\n]]></Cmd>\n"
    )
    await self._conn.send_command(cmd)

  async def download_protocol(self, name: str) -> bytes:
    """Download a protocol from instrument memory and return the raw .bdz bytes.

    Per Interface Spec §5.5: the response contains base64-encoded .bdz data
    in a CDATA section.  Parse the returned bytes with
    ``pylabrobot.particle_processing.kingfisher.bdz.read_bdz()``.
    """
    res = await self._conn.send_command(_cmd_xml("DownloadProtocol", protocol=name))
    cdata = (res.text or "").strip()
    return base64.b64decode("".join(cdata.split()))

  async def remove_protocol(self, name: str) -> None:
    """Remove a protocol from instrument memory. Per Interface Spec §5.11."""
    await self._conn.send_command(_cmd_xml("RemoveProtocol", protocol=name))

  async def get_event(self) -> ET.Element:
    """Return the next raw event from the queue. Blocks until one is available.
    Internal: used by frontend next_event() and by backend rotate(). Prefer frontend next_event() for (name, evt, ack)."""
    return await self._conn.get_event()

  def events(self):
    """Async generator of events. Use: async for evt in backend.events()."""
    return self._conn.events()

  async def acknowledge(self) -> None:
    """Send Acknowledge (e.g. after LoadPlate, RemovePlate, ChangePlate, Pause)."""
    await self._conn.send_command(_cmd_xml("Acknowledge"))

  async def error_acknowledge(self) -> None:
    """Send ErrorAcknowledge to clear instrument error state."""
    await self._conn.send_command(_cmd_xml("ErrorAcknowledge"))

  async def abort(self) -> None:
    """Two-phase abort: Feature report then Abort character. Stops execution and flushes buffers."""
    await self._conn.abort()

  async def start_protocol(
    self,
    protocol: str,
    tip: Optional[str] = None,
    step: Optional[str] = None,
  ) -> None:
    """Start a protocol or single step. Protocol must already be in instrument memory."""
    attrs: dict = {"protocol": protocol}
    if tip is not None:
      attrs["tip"] = tip
    if step is not None:
      attrs["step"] = step
    await self._conn.send_command(_cmd_xml("StartProtocol", **attrs))

  async def stop_protocol(self) -> None:
    """Stop ongoing protocol/step execution."""
    await self._conn.send_command(_cmd_xml("Stop"))

  async def rotate(
    self,
    position: int = 1,
    location: Union[str, TurntableLocation] = TurntableLocation.LOADING,
  ) -> None:
    """Rotate the turntable so the given position (1 or 2) is at the given location.

    The turntable has two positions (slots) 1 and 2. Each can be at location \"processing\"
    (under the magnetic head) or \"loading\\\" (the load/unload station). This command moves
    one position to the requested location. State is inferred from Ready; on Error state
    is not updated and PrestoConnectionError is raised.
    """
    if position not in (1, 2):
      raise ValueError("position must be 1 or 2")
    normalized_location = _normalize_location(location)
    spec_position = 1 if normalized_location == "processing" else 2
    # Send Rotate without waiting for Res; instrument may signal completion only via Evt (Ready/Error).
    await self._conn.send_without_response(
      _cmd_xml("Rotate", nest=str(position), position=str(spec_position))
    )
    while True:
      evt = await self._conn.get_event()
      name = evt.get("name")
      if name == "Ready":
        self._position_at_processing = (
          position if normalized_location == "processing" else (3 - position)
        )
        return
      if name == "Error":
        await self.error_acknowledge()
        err_el = evt.find("Error")
        code = int(err_el.get("code", 0)) if err_el is not None and err_el.get("code") else None
        instrument_text = _text(err_el) if err_el is not None else None
        msg = format_error_message(code, instrument_text, kind="error")
        raise PrestoConnectionError(msg, code=code, res_name=name)
      # Ignore other events (e.g. StepStarted, Temperature)

  def get_turntable_state(self) -> Dict[int, Optional[str]]:
    """Return current location of each position: {1: 'processing'|'loading'|None, 2: ...}.

    State is inferred only from rotate() commands that completed with Ready; unknown after
    setup/stop until the first successful rotate (or setup(initialize_turntable=True)).
    """
    if self._position_at_processing is None:
      return {1: None, 2: None}
    return {
      1: "processing" if self._position_at_processing == 1 else "loading",
      2: "processing" if self._position_at_processing == 2 else "loading",
    }

  async def load_plate(self) -> None:
    """Rotate so whatever is at the loading position moves to the processing position.

    Requires known turntable state (call rotate() first or setup(initialize_turntable=True)).
    Raises ValueError if state is unknown.
    """
    if self._position_at_processing is None:
      raise ValueError("Turntable state unknown; call rotate() first to establish state.")
    position_at_loading = 3 - self._position_at_processing
    await self.rotate(position=position_at_loading, location=TurntableLocation.PROCESSING)

  @property
  def instrument(self) -> Optional[str]:
    return self._instrument

  @property
  def version(self) -> Optional[str]:
    return self._version

  @property
  def serial(self) -> Optional[str]:
    return self._serial

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "vid": self._vid,
      "pid": self._pid,
      "serial_number": self._serial_number,
    }
