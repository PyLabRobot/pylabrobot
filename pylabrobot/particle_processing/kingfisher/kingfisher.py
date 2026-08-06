"""High-level KingFisher machine frontend wrapping the backend.

Same lifecycle as other machines (setup(), stop(), async with).
Exposes start_protocol(), get_status(), acknowledge(), error_acknowledge(),
next_event() for the next (name, evt, ack), and events() for a raw event stream.
Drive the run with next_event() in a loop; handle each event, await ack() when required; stop when Ready/Aborted/Error.
"""

import warnings
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from pylabrobot.machines.machine import Machine, need_setup_finished
from pylabrobot.particle_processing.kingfisher.bdz import (
  BdzProtocol,
  _CONTRACTS,
  read_bdz,
  write_bdz,
)
from pylabrobot.particle_processing.kingfisher.kingfisher_backend import (
  KingFisherBackend,
  TurntableLocation,
)


class KingFisher(Machine):
  """High-level KingFisher magnetic particle processor frontend.

  Works with any :class:`KingFisherBackend` subclass — pass
  :class:`KingFisherBackend` for the Presto or
  :class:`KingFisherDuoBackend` for the Duo.

  Use :meth:`next_event` for ``(name, evt, ack)`` or :meth:`events` for a
  raw event stream.
  """

  def __init__(self, backend: KingFisherBackend):
    super().__init__(backend=backend)
    self.backend: KingFisherBackend = backend
    self._last_run_state: Optional[Dict[str, Any]] = None

  async def setup(self, *, initialize_turntable: bool = False, **backend_kwargs) -> None:
    """Connect (backend.setup), then check run state; warn if instrument is not Idle (Busy or In error).

    When initialize_turntable is True, the backend rotates to power-on state on connect so
    turntable positions are known; the table may move. Passed to backend.setup().
    """
    await super().setup(initialize_turntable=initialize_turntable, **backend_kwargs)
    state = await self.get_run_state()
    if state.get("status") != "Idle":
      msg = state.get("message", "")
      if state.get("status") == "Busy":
        msg += " You are attaching to an existing protocol run. To continue driving it, use next_event(attach=True) then next_event() in a loop; or call stop_protocol() or abort() to stop."
      elif state.get("status") == "In error":
        msg += " Check error_code and error_text; you may need to call error_acknowledge()."
      warnings.warn(msg, stacklevel=2)

  @need_setup_finished
  async def get_status(self) -> dict:
    """Get instrument status: Idle, Busy, or In error. Returns dict with ok, status, error_code, error_text, error_code_description."""
    return await self.backend.get_status()

  @need_setup_finished
  async def get_protocol_time_left(self, protocol: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Get time left of protocol or single-step execution. Returns time_left and time_to_pause (XML Duration, e.g. PT2M42S).
    For single-step execution time_to_pause is None per spec."""
    return await self.backend.get_protocol_time_left(protocol)

  @need_setup_finished
  async def get_run_state(self) -> Dict[str, Any]:
    """Return instrument run state: get_status() keys plus time_left, time_to_pause, and message.
    Idle = no protocol in progress (ready for next command). Busy = protocol in progress (existing run); attach to continue with next_event() or stop with stop_protocol()/abort().
    Does not warn; setup() calls this and warns when status is not Idle."""
    status_dict = await self.backend.get_status()
    status = status_dict.get("status") or "In error"
    time_left: Optional[str] = None
    time_to_pause: Optional[str] = None
    if status == "Busy":
      try:
        t = await self.backend.get_protocol_time_left()
        time_left = t.get("time_left")
        time_to_pause = t.get("time_to_pause")
      except Exception:
        pass
    if status == "Idle":
      message = "No protocol in progress (ready for next command)."
    elif status == "Busy":
      message = "Protocol in progress (existing run). Attach to continue with next_event(), or stop_protocol() or abort() to stop."
      if time_left is not None:
        message += f" Time left: {time_left}."
    else:
      message = "Instrument in error. You may need to call error_acknowledge(); see error_code and error_text."
    result: Dict[str, Any] = {
      **status_dict,
      "time_left": time_left,
      "time_to_pause": time_to_pause,
      "message": message,
    }
    self._last_run_state = result
    return result

  @need_setup_finished
  async def get_protocol_duration(self, protocol: str) -> Dict[str, Any]:
    """Get protocol structure (tips and step names/durations) from the instrument.

    Per Interface Spec §5.7 GetProtocolDuration. Use this to list steps without
    downloading or parsing the BDZ. Returns dict with protocol, total_duration,
    and tips (each with name and steps list of {name, duration})."""
    return await self.backend.get_protocol_duration(protocol)

  @need_setup_finished
  async def list_protocols(self) -> Tuple[List[str], int]:
    """List protocols in instrument memory. Returns (protocol_names, memory_used_percent)."""
    return await self.backend.list_protocols()

  @need_setup_finished
  async def start_protocol(
    self,
    protocol: str,
    tip: Optional[str] = None,
    step: Optional[str] = None,
  ) -> None:
    """Start a protocol or single step. Protocol must already be in instrument memory (upload first)."""
    return await self.backend.start_protocol(protocol, tip=tip, step=step)

  @need_setup_finished
  async def stop_protocol(self) -> None:
    """Stop ongoing protocol/step execution."""
    return await self.backend.stop_protocol()

  def _print_step_started(self, evt: ET.Element) -> None:
    """Print progress for StepStarted (Evt/Step@name, @tip, @plate)."""
    step_el = evt.find("Step")
    if step_el is None:
      print("  StepStarted")
      return
    step_name = step_el.get("name", "?")
    tip_name = step_el.get("tip", "")
    plate_name = step_el.get("plate", "")
    parts = [f"Step: {step_name}"]
    if tip_name:
      parts.append(f"tip={tip_name}")
    if plate_name:
      parts.append(f"plate={plate_name}")
    print("  ", " | ".join(parts))

  def _print_protocol_time_left(self, evt: ET.Element) -> None:
    """Print progress for ProtocolTimeLeft (Evt/TimeLeft@value, TimeToPause@value)."""
    time_left_el = evt.find("TimeLeft")
    time_to_pause_el = evt.find("TimeToPause")
    time_left = time_left_el.get("value", "?") if time_left_el is not None else "?"
    time_to_pause = time_to_pause_el.get("value", "") if time_to_pause_el is not None else ""
    msg = f"  Time left: {time_left}"
    if time_to_pause:
      msg += f" (to next pause: {time_to_pause})"
    print(msg)

  @need_setup_finished
  async def next_event(
    self, *, attach: bool = False
  ) -> Tuple[str, Optional[ET.Element], Optional[Callable[..., Any]]]:
    """Wait for the next user-facing or terminal event; returns (name, evt, ack).

    Consumes StepStarted and ProtocolTimeLeft internally: prints progress (step name/tip/plate,
    time left) and keeps waiting until the instrument sends an event that either (a) requires
    user interaction (LoadPlate, RemovePlate, ChangePlate, Pause) or (b) ends the run
    (Ready, Aborted, Error). Returns that event so the caller can acknowledge if needed or
    treat as complete. Raw event stream without this behavior: use events().

    When attach=True (e.g. attaching to an in-progress run after setup when Busy), if status
    is already Idle or In error, returns (\"Ready\", None, None) without reading from the queue.
    """
    if attach:
      status_dict = await self.get_status()
      status = status_dict.get("status") or "In error"
      if status in ("Idle", "In error"):
        return ("Ready", None, None)
    while True:
      evt = await self.backend.get_event()
      name = evt.get("name")
      if name in ("LoadPlate", "RemovePlate", "ChangePlate", "Pause"):
        return (name, evt, self.acknowledge)
      if name == "Error":
        return (name, evt, self.error_acknowledge)
      if name in ("Ready", "Aborted"):
        return (name, evt, None)
      if name == "StepStarted":
        self._print_step_started(evt)
        continue
      if name == "ProtocolTimeLeft":
        self._print_protocol_time_left(evt)
        continue
      # Other informational (Temperature, ChangeMagnets, etc.): print and consume
      print(f"  Event: {name}")
      continue

  def events(self):
    """Async generator of raw events (ET.Element). Use for low-level control; for (name, evt, ack) use next_event()."""
    return self.backend.events()

  @need_setup_finished
  async def acknowledge(self) -> None:
    """Send Acknowledge (e.g. after LoadPlate, RemovePlate, ChangePlate, Pause)."""
    return await self.backend.acknowledge()

  @need_setup_finished
  async def error_acknowledge(self) -> None:
    """Send ErrorAcknowledge to clear instrument error state."""
    return await self.backend.error_acknowledge()

  @need_setup_finished
  async def abort(self) -> None:
    """Two-phase abort: stops execution and flushes communication buffers."""
    return await self.backend.abort()

  @need_setup_finished
  async def rotate(
    self,
    position: int = 1,
    location: Union[str, TurntableLocation] = TurntableLocation.LOADING,
  ) -> None:
    """Rotate the turntable so the given position (1 or 2) is at the given location.

    The turntable has two positions (slots) 1 and 2. Each can be at \"processing\" (under the
    magnetic head) or \"loading\" (the load/unload station). The backend waits for Ready/Error.
    For explicit state use get_turntable_state(); for bringing the plate at loading to
    processing use load_plate().
    """
    await self.backend.rotate(position=position, location=location)

  @need_setup_finished
  async def get_turntable_state(self) -> Dict[int, Optional[str]]:
    """Return current location of each position: {1: 'processing'|'loading'|None, 2: ...}.

    State is inferred only from rotate() commands that completed with Ready; unknown after
    setup/stop until the first successful rotate (or setup(initialize_turntable=True)).
    """
    return self.backend.get_turntable_state()

  @need_setup_finished
  async def load_plate(self) -> None:
    """Rotate the table so whatever is at the loading position moves to the processing position.

    Requires known turntable state; call rotate() first or setup(initialize_turntable=True)
    if state is unknown. For explicit control use rotate() and get_turntable_state().
    """
    await self.backend.load_plate()

  @need_setup_finished
  async def upload_protocol(self, name: str, protocol: BdzProtocol) -> None:
    """Serialize *protocol* and upload it to instrument memory.

    Validates that ``protocol.variant`` matches the backend's contract to
    prevent uploading a Duo protocol to a Presto (or vice versa).
    To upload raw .bdz bytes directly, use ``backend.upload_protocol(name, bytes)``.
    """
    expected = self.backend.contract
    actual   = _CONTRACTS[protocol.variant]
    if actual is not expected:
      raise ValueError(
        f"Protocol targets {protocol.variant.value!r} "
        f"(params_type={actual.params_type}) but connected backend "
        f"expects params_type={expected.params_type}"
      )
    await self.backend.upload_protocol(name, write_bdz(protocol))

  @need_setup_finished
  async def download_protocol(self, name: str) -> BdzProtocol:
    """Download a protocol from instrument memory and return a parsed :class:`BdzProtocol`."""
    return read_bdz(await self.backend.download_protocol(name))

  @property
  def instrument(self) -> Optional[str]:
    """Instrument name/type from Connect response."""
    return self.backend.instrument

  @property
  def version(self) -> Optional[str]:
    """Firmware version from Connect response."""
    return self.backend.version

  @property
  def serial(self) -> Optional[str]:
    """Instrument serial number from Connect response."""
    return self.backend.serial
