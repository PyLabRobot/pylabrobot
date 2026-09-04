"""Swappable wire-level transport for :class:`~pylabrobot.opentrons.robot.OpentronsRobot`.

``OpentronsRobot`` talks to the robot-server's Protocol-Engine HTTP API
(``/health``, ``/runs``, ``/instruments``, ``/runs/{id}/commands``). Everything
it needs from the wire is three verbs (``get``/``post``/``delete``) that return
parsed JSON, plus a ``close()`` to tear the connection down. That surface is
captured here as the :class:`OpentronsTransport` Protocol so the robot can be
driven by a real ``httpx.AsyncClient`` (:class:`HttpxTransport`) or by an
offline recording stand-in (:class:`ChatterboxTransport`) without knowing the
difference.

``ChatterboxTransport`` is the transport-level analog of Hamilton's
``STARChatterboxDriver`` (which logs firmware commands instead of sending them
over USB): it logs each command and returns a canned "succeeded" response, so
the robot lifecycle (health check, create-run, instrument discovery) — and any
PLR-native checks layered on top of it — can run with no network.
"""

import logging
from pathlib import Path
from typing import (
  Any,
  Callable,
  Dict,
  List,
  Optional,
  Protocol,
  Tuple,
  Union,
  cast,
  runtime_checkable,
)

from pylabrobot.io.capture import CaptureReader
from pylabrobot.io.http import HTTP, HTTPValidator

logger = logging.getLogger(__name__)

# The robot-server rejects a request that does not name the API version it
# should be read as.
DEFAULT_HEADERS = {"opentrons-version": "3"}

# ChatterboxTransport's default /health version: deliberately not a version
# string, so a caller gating on robot software can tell offline from any robot.
OFFLINE_API_VERSION = "dry-run"

# /instruments serves a versioned model, and flow-rate defaults differ between
# versions of the same pipette, so the fake cannot just echo the name back.
_MODELS_BY_NAME = {
  "p50_single_flex": "p50_single_v3.5",
  "p50_multi_flex": "p50_multi_v3.5",
  "p1000_single_flex": "p1000_single_v3.5",
  "p1000_multi_flex": "p1000_multi_v3.5",
  "p1000_96": "p1000_96_v3.5",
  "p200_96": "p200_96_v3.3",
}


# Enum-valued command params, with what the robot-server accepts. A fake that
# takes any string lets a driver ship a value the real robot answers 422 to.
_COMMAND_ENUMS = {
  ("setTipState", "tipWellState"): frozenset({"clean", "used", "empty"}),
  ("verifyTipPresence", "expectedState"): frozenset({"present", "absent"}),
  ("moveLabware", "strategy"): frozenset(
    {"usingGripper", "manualMoveWithPause", "manualMoveWithoutPause"}
  ),
}


def _reject_unknown_enum_values(command_type: str, params: Dict[str, Any]) -> None:
  """Refuse a param value the real robot-server would reject."""
  for (ctype, key), allowed in _COMMAND_ENUMS.items():
    if ctype != command_type or key not in params:
      continue
    if params[key] not in allowed:
      raise ValueError(
        f"{command_type} param {key}={params[key]!r} is not one of {sorted(allowed)}; "
        "the robot-server answers 422 to this."
      )


@runtime_checkable
class OpentronsTransport(Protocol):
  """Wire-level seam: the subset of HTTP that ``OpentronsRobot`` needs.

  Implementations return parsed JSON bodies directly (no response object) —
  raising for non-2xx status is the transport's job, not the robot's.
  """

  async def setup(self) -> None: ...

  async def get(self, path: str) -> Dict[str, Any]: ...

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...

  async def delete(self, path: str) -> Dict[str, Any]: ...

  async def close(self) -> None: ...


class HttpxTransport:
  """Real transport, over the :class:`~pylabrobot.io.http.HTTP` io.

  Going through the io rather than a bare ``httpx.AsyncClient`` is what puts
  every robot-server exchange in PyLabRobot's capture log, so a run wrapped in
  ``start_capture()`` can later be replayed by :class:`ReplayTransport`.
  """

  def __init__(
    self,
    base_url: str,
    timeout: float = 30.0,
    headers: Optional[Dict[str, str]] = None,
  ) -> None:
    self.io = HTTP(base_url=base_url, timeout=timeout, headers=headers or DEFAULT_HEADERS)

  async def setup(self) -> None:
    await self.io.setup()

  async def get(self, path: str) -> Dict[str, Any]:
    return cast(Dict[str, Any], await self.io.get(path))

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return cast(Dict[str, Any], await self.io.post(path, json=json or {}))

  async def delete(self, path: str) -> Dict[str, Any]:
    return cast(Dict[str, Any], await self.io.delete(path))

  async def close(self) -> None:
    await self.io.stop()


class ReplayTransport:
  """Offline transport that replays a capture file recorded from a real robot.

  Where :class:`ChatterboxTransport` returns responses we wrote by hand, this
  returns the ones an actual robot gave, in the order it gave them, and raises
  the same refusal on an exchange that failed. Nothing reaches the network.

  Build the capture file by wrapping a live run in ``start_capture()`` /
  ``stop_capture()``. Construct the robot BEFORE arming the capture: every
  pylabrobot io refuses construction while one is active, and the robot builds
  its transport in ``__init__`` for exactly that reason.

      flex = OpentronsFlex(deck=deck, host=host)   # transport built here
      pylabrobot.start_capture(path)
      await flex.setup()
      ...
      pylabrobot.stop_capture()

  Call :meth:`assert_fully_replayed` at the end of a test: it fails when the
  protocol stopped short of the recording, which is what catches a dropped
  command.
  """

  def __init__(
    self,
    capture_file: Union[str, Path],
    base_url: str,
    headers: Optional[Dict[str, str]] = None,
  ) -> None:
    self._cr = CaptureReader(path=str(capture_file))
    self.io = HTTPValidator(self._cr, base_url=base_url, headers=headers or DEFAULT_HEADERS)

  async def setup(self) -> None:
    await self.io.setup()

  async def get(self, path: str) -> Dict[str, Any]:
    return cast(Dict[str, Any], await self.io.get(path))

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return cast(Dict[str, Any], await self.io.post(path, json=json or {}))

  async def delete(self, path: str) -> Dict[str, Any]:
    return cast(Dict[str, Any], await self.io.delete(path))

  async def close(self) -> None:
    await self.io.stop()

  def assert_fully_replayed(self) -> None:
    """Raise unless every recorded exchange was consumed."""
    self._cr.done()


class ChatterboxTransport:
  """Offline transport: logs commands, returns canned 'succeeded' responses.

  Instead of reaching a robot server it returns the fixed ``/health``,
  ``/instruments``, ``/runs``, ``/runs/{id}/commands`` and
  ``/runs/{id}/labware_definitions`` shapes the ``OpentronsRobot`` lifecycle
  (``setup()``: health check, create-run, discover pipette) and labware
  loading read, so a caller can drive the robot with no network.

  Scope: this exercises PLR-native checks only. It does NOT reproduce the
  Opentrons Protocol Engine's *analysis* stage (deck-conflict, capacity,
  partial-tip extents) — that is protocol-file based (``opentrons_simulate``
  against a virtual Protocol Engine) and needs the ``opentrons`` package,
  which an HTTP transport cannot reach.

  It does track the engine's plunger-priming rule (see ``_track_plunger``),
  so a draw the real robot would refuse fails here too. That rule cost a
  round of tests that passed on wire traffic hardware rejects.
  """

  def __init__(
    self,
    pipette: Tuple[str, int, float, float] = ("p1000_single_flex", 1, 1.0, 1000.0),
    mount: str = "right",
    pipettes: Optional[List[Tuple[str, int, float, float, str]]] = None,
    log: Optional[Callable[..., None]] = None,
    simulate_failed_pickup: bool = False,
    simulate_stuck_tip: bool = False,
    liquid_probe_z: Optional[float] = None,
    simulate_liquid_probe_not_found: bool = False,
    gripper: bool = False,
    saved_position: Optional[Dict[str, float]] = None,
    api_version: str = OFFLINE_API_VERSION,
  ) -> None:
    """Args:
    pipette: the simulated mounted pipette as ``(name, channels, min_vol, max_vol)``.
      Configurable so callers can simulate a 1/8/96-channel head. Ignored if
      ``pipettes`` is given.
    mount: which mount ``/instruments`` reports ``pipette`` on (``"left"`` or
      ``"right"``), so tests can drive left- vs right-mount discovery. Ignored
      if ``pipettes`` is given.
    pipettes: the simulated mounted pipettes as a list of
      ``(name, channels, min_vol, max_vol, mount)`` — one entry per mount, so
      tests can simulate multiple pipettes (e.g. left + right) at once. Pass
      ``[]`` to simulate no pipette mounted. Takes precedence over
      ``pipette``/``mount`` when given (even when empty).
    log: where to send the per-command chatter (defaults to this module's logger).
    simulate_failed_pickup: if True, a ``pickUpTip`` command does NOT flip the
      issuing pipette's simulated tip-presence sensor to detected -- models a
      hardware pickup that moved through the motion but never seated a tip,
      so ``_FlexHead._verify_tips_seated()`` reads "absent" and raises.
      Default False: a pickup always seats a tip (existing behavior).
    simulate_stuck_tip: if True, ``dropTip``/``dropTipInPlace`` do NOT clear
      the issuing pipette's simulated tip-presence sensor -- models a tip
      stuck to the nozzle after a drop, so ``_FlexHead._confirm_tips_cleared()``
      reads "present" and logs a warning. Default False: a drop always clears
      the sensor (existing behavior).
    liquid_probe_z: the liquid height (mm) a ``liquidProbe``/``tryLiquidProbe``
      command reports as ``z_position`` in its result. Default None: the key
      is omitted from the result entirely (not set to null), matching the
      real robot-server's shape when no liquid is found.
    simulate_liquid_probe_not_found: if True, a ``liquidProbe`` command FAILS
      with the engine's defined "liquidNotFound" error -- the real-hardware
      behavior when no liquid is detected -- instead of succeeding with the
      ``z_position`` key absent. ``tryLiquidProbe`` is unaffected: it
      genuinely succeeds with the key absent. Default False.
    gripper: if True, ``/instruments`` also reports a gripper on the extension
      mount, so tests can drive gripper discovery. Default False: no gripper
      mounted (existing behavior).
    saved_position: the position a ``savePosition`` command reports in its
      result, as an ``{"x", "y", "z"}`` dict. Default None: report
      ``{"x": 100.0, "y": 100.0, "z": 100.0}``.
    api_version: the robot software version ``/health`` reports. Defaults to
      the ``OFFLINE_API_VERSION`` sentinel, which no real robot returns; pass
      a real version string (e.g. ``"8.1.0"``) to drive a caller's own
      version gating without subclassing.
    """
    if pipettes is not None:
      self._pipettes: List[Tuple[str, int, float, float, str]] = list(pipettes)
    else:
      name, channels, min_v, max_v = pipette
      self._pipettes = [(name, channels, min_v, max_v, mount)]
    self._gripper = gripper
    self.api_version = api_version
    self._log = log or logger.info
    self._cmds: Dict[str, Dict[str, Any]] = {}  # cmd_id -> full command data
    self._n = 0
    self._pipette_load_count = 0
    self._labware_load_count = 0
    self.load_pipette_commands: List[Dict[str, Any]] = []  # recorded loadPipette params
    self.commands: List[Dict[str, Any]] = []  # every command, in send order: {commandType, params}
    # Reading GET /instruments re-caches the pipettes, which clears the run's
    # record of the attached tip, so it must not happen mid-run.
    self.instrument_reads = 0
    self.labware_definitions: List[Dict[str, Any]] = []  # recorded custom definition uploads
    self.labware_ids: Dict[str, str] = {}  # displayName -> the id this transport assigned it
    self.simulate_failed_pickup = simulate_failed_pickup
    self.simulate_stuck_tip = simulate_stuck_tip
    self.liquid_probe_z = liquid_probe_z
    self.simulate_liquid_probe_not_found = simulate_liquid_probe_not_found
    self.saved_position = saved_position
    # Per-mount simulated hardware tip-presence sensor state (Flex reports
    # ONE bool per pipette, not per nozzle -- see /instruments below).
    self._tip_detected: Dict[str, bool] = {mount: False for *_rest, mount in self._pipettes}
    # pipetteId (as returned by loadPipette) -> mount, so a later
    # pickUpTip/dropTip command's pipetteId can be resolved back to a mount.
    self._pipette_id_to_mount: Dict[str, str] = {}
    # Simulated plunger state per pipetteId, so a draw the real robot would
    # refuse is refused here too. See _plunger_is_ready.
    self._plunger_primed: Dict[str, bool] = {}
    self._tip_volume: Dict[str, Optional[float]] = {}

  async def setup(self) -> None:
    """No connection to open."""

  async def get(self, path: str) -> Dict[str, Any]:
    if path == "/health":
      return {
        "api_version": self.api_version,
        "robot_model": "OT-3 Standard",
        "name": "chatterbox",
      }
    if path == "/instruments":
      self.instrument_reads += 1
      instruments: List[Dict[str, Any]] = [
        {
          "instrumentType": "pipette",
          "mount": mount,
          "instrumentName": name,
          "instrumentModel": _MODELS_BY_NAME.get(name, name),
          "data": {"channels": channels, "min_volume": min_v, "max_volume": max_v},
          "state": {"tipDetected": self._tip_detected.get(mount, False)},
        }
        for name, channels, min_v, max_v, mount in self._pipettes
      ]
      if self._gripper:
        instruments.append(
          {
            "instrumentType": "gripper",
            "mount": "extension",
            "instrumentName": "flexGripper",
            "instrumentModel": "gripperV1.3",
            "data": {"jawState": "unhomed"},
          }
        )
      return {"data": instruments}
    if "/commands/" in path:  # a poll for one command's status
      cmd_id = path.rsplit("/", 1)[-1]
      cmd_data = self._cmds.get(
        cmd_id, {"id": cmd_id, "commandType": "", "status": "succeeded", "result": {}}
      )
      return {"data": cmd_data}
    return {"data": {}}

  def _plunger_is_ready(self, pipette_id: str) -> bool:
    """Whether the robot would let this pipette draw where it stands.

    Mirrors ``HardwarePipettingHandler.get_is_ready_to_aspirate``: the
    plunger must be at its dispense bottom AND the robot must still know how
    much the tip holds. Dropping a tip makes the contents unknown, which is
    why a drop leaves the pipette not ready even though nothing pushed the
    plunger down.
    """
    return self._tip_volume.get(pipette_id) is not None and self._plunger_primed.get(
      pipette_id, False
    )

  def _track_plunger(self, ctype: str, params: Dict[str, Any]) -> None:
    """Apply one command's effect on the simulated plunger and tip contents.

    Straight from the Protocol Engine's own state updates. The two that
    surprise people: a tip pickup PRIMES the plunger (the engine primes it as
    part of the pickup), and a dispense only un-primes when it empties the
    tip, because that is the one the robot follows with a push-out.
    """
    pid = params.get("pipetteId")
    if not isinstance(pid, str):
      return
    volume = float(params.get("volume", 0.0) or 0.0)
    held = self._tip_volume.get(pid)
    if ctype == "configureForVolume":
      self._plunger_primed[pid] = False
    elif ctype in ("pickUpTip", "prepareToAspirate", "liquidProbe", "tryLiquidProbe"):
      self._plunger_primed[pid] = True
      self._tip_volume[pid] = 0.0
    elif ctype in ("aspirate", "aspirateInPlace", "airGapInPlace"):
      # A well-addressed aspirate primes itself at the well top when it has to.
      self._plunger_primed[pid] = True
      self._tip_volume[pid] = (held or 0.0) + volume
    elif ctype in ("dispense", "dispenseInPlace"):
      left = (held or 0.0) - volume
      self._tip_volume[pid] = left
      push_out = params.get("pushOut")
      self._plunger_primed[pid] = push_out == 0 if push_out is not None else abs(left) > 1e-9
    elif ctype in ("blowOut", "blowOutInPlace", "unsafe/blowOutInPlace"):
      self._plunger_primed[pid] = False
      self._tip_volume[pid] = 0.0
    elif ctype in ("dropTip", "dropTipInPlace", "unsafe/dropTipInPlace"):
      self._tip_volume[pid] = None

  def _plunger_refusal(self, ctype: str, params: Dict[str, Any]) -> Optional[str]:
    """The robot's own message when a command needs a primed plunger and finds none.

    Only commands that name no well refuse: a well-addressed ``aspirate``
    primes itself at the well top instead. A liquid probe refuses despite
    naming a well, deliberately, because getting there means the tip has held
    liquid and a probe wants a dry one.
    """
    pid = params.get("pipetteId")
    if not isinstance(pid, str):
      return None
    if ctype in ("aspirateInPlace", "airGapInPlace") and not self._plunger_is_ready(pid):
      return (
        "Pipette cannot aspirate in place because a previous dispense or blowout pushed "
        "the plunger beyond the bottom position. The subsequent aspirate must be from a "
        "specific well so the plunger can be reset in a known safe position."
      )
    if ctype in ("liquidProbe", "tryLiquidProbe") and self._tip_volume.get(pid) is None:
      return (
        "The pipette cannot probe liquid because a previous dispense or blowout pushed "
        "the plunger beyond the bottom position. The plunger must be reset while the tip "
        "is somewhere away from liquid."
      )
    return None

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if path == "/runs":
      return {"data": {"id": "chatterbox-run"}}
    if path.endswith("/labware_definitions"):  # custom labware definition upload
      definition = (json or {}).get("data", {})
      self.labware_definitions.append(dict(definition))
      # The real robot-server answers with the stored definition's URI, which
      # the caller parses to reference the definition in loadLabware.
      uri = "/".join(
        str(part)
        for part in (
          definition.get("namespace"),
          definition.get("parameters", {}).get("loadName"),
          definition.get("version"),
        )
      )
      self._log("Chatterbox: defineLabware %s", uri)
      return {"data": {"definitionUri": uri}}
    if path.endswith("/commands"):
      data = (json or {}).get("data", {})
      ctype = data.get("commandType", "?")
      params = data.get("params", {})
      _reject_unknown_enum_values(ctype, params)
      self._n += 1
      cmd_id = f"cmd-{self._n}"
      self.commands.append({"commandType": ctype, "params": dict(params)})
      result: Dict[str, Any] = {}
      refusal = self._plunger_refusal(ctype, params)
      if refusal is not None:
        # Raised undefined by the engine, so the wire carries the exception's
        # class name rather than a defined error code.
        cmd_data = {
          "id": cmd_id,
          "commandType": ctype,
          "status": "failed",
          "error": {
            "errorType": "PipetteNotReadyToAspirateError",
            "errorCode": "4000",
            "detail": refusal,
          },
        }
        self._cmds[cmd_id] = cmd_data
        self._log("Chatterbox: %s %s REFUSED (plunger not primed)", ctype, params)
        return {"data": cmd_data}
      if ctype == "loadPipette":
        self._pipette_load_count += 1
        pipette_id = f"chatterbox-pip-{self._pipette_load_count}"
        result = {"pipetteId": pipette_id}
        self.load_pipette_commands.append(dict(params))
        mount = params.get("mount")
        if mount is not None:
          self._pipette_id_to_mount[pipette_id] = mount
      elif ctype == "loadLabware":
        self._labware_load_count += 1
        labware_id = f"chatterbox-labware-{self._labware_load_count}"
        result = {"labwareId": labware_id}
        self.labware_ids[str(params.get("displayName"))] = labware_id
      else:
        result = {}
        if ctype == "pickUpTip":
          mount = self._pipette_id_to_mount.get(params.get("pipetteId"))
          if mount is not None:
            self._tip_detected[mount] = not self.simulate_failed_pickup
        elif ctype in ("dropTip", "dropTipInPlace"):
          mount = self._pipette_id_to_mount.get(params.get("pipetteId"))
          if mount is not None:
            self._tip_detected[mount] = self.simulate_stuck_tip
        elif ctype in ("getTipPresence", "verifyTipPresence"):
          mount = self._pipette_id_to_mount.get(params.get("pipetteId"))
          detected = self._tip_detected.get(mount, False) if mount is not None else False
          result = {"status": "present" if detected else "absent"}
        elif ctype in ("liquidProbe", "tryLiquidProbe"):
          if self.liquid_probe_z is not None:
            result = {"z_position": self.liquid_probe_z}
        elif ctype == "savePosition":
          pos = self.saved_position or {"x": 100.0, "y": 100.0, "z": 100.0}
          result = {"position": dict(pos)}
      cmd_data = {"id": cmd_id, "commandType": ctype, "status": "succeeded", "result": result}
      if ctype == "liquidProbe" and self.simulate_liquid_probe_not_found:
        # The real robot-server fails the command with a defined
        # ErrorOccurrence when the probe finds no liquid.
        cmd_data = {
          "id": cmd_id,
          "commandType": ctype,
          "status": "failed",
          "error": {
            "errorType": "liquidNotFound",
            "errorCode": "2017",
            "detail": "No liquid detected during the liquid probe process.",
            "isDefined": True,
          },
        }
      if ctype == "verifyTipPresence" and result.get("status") != params.get("expectedState"):
        # The point of verifyTipPresence over getTipPresence: the robot, not
        # the caller, refuses the mismatch.
        cmd_data = {
          "id": cmd_id,
          "commandType": ctype,
          "status": "failed",
          "error": {
            "errorType": "tipPresenceMismatch",
            "detail": f"Expected tip to be {params.get('expectedState')}.",
            "isDefined": True,
          },
        }
      if cmd_data["status"] == "succeeded":
        self._track_plunger(ctype, params)
      self._cmds[cmd_id] = cmd_data
      self._log("Chatterbox: %s %s", ctype, params)
      return {"data": cmd_data}
    return {"data": {}}  # e.g. /actions

  async def delete(self, path: str) -> Dict[str, Any]:
    return {"data": {}}

  async def close(self) -> None:
    return None
