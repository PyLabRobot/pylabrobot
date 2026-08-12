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
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, cast, runtime_checkable

try:
  import httpx  # type: ignore[import-not-found]

  _HAS_HTTPX = True
except ImportError:
  _HAS_HTTPX = False

logger = logging.getLogger(__name__)


@runtime_checkable
class OpentronsTransport(Protocol):
  """Wire-level seam: the subset of HTTP that ``OpentronsRobot`` needs.

  Implementations return parsed JSON bodies directly (no response object) —
  raising for non-2xx status is the transport's job, not the robot's.
  """

  async def get(self, path: str) -> Dict[str, Any]: ...

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...

  async def delete(self, path: str) -> Dict[str, Any]: ...

  async def close(self) -> None: ...


class HttpxTransport:
  """Real transport: wraps an ``httpx.AsyncClient`` against the robot-server."""

  def __init__(
    self,
    base_url: str,
    timeout: float = 30.0,
    headers: Optional[Dict[str, str]] = None,
  ) -> None:
    if not _HAS_HTTPX:
      raise RuntimeError("httpx is required. Install with: pip install httpx")
    self._client = httpx.AsyncClient(
      base_url=base_url,
      timeout=timeout,
      headers=headers or {"opentrons-version": "3"},
    )

  async def get(self, path: str) -> Dict[str, Any]:
    response = await self._client.get(path)
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = await self._client.post(path, json=json or {})
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())

  async def delete(self, path: str) -> Dict[str, Any]:
    response = await self._client.delete(path)
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())

  async def close(self) -> None:
    await self._client.aclose()


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
      so ``_FlexHead._verify_tips_seated()`` sees ``tipDetected: False`` and
      raises. Default False: a pickup always seats a tip (existing behavior).
    simulate_stuck_tip: if True, ``dropTip``/``dropTipInPlace`` do NOT clear
      the issuing pipette's simulated tip-presence sensor -- models a tip
      stuck to the nozzle after a drop, so ``_FlexHead._confirm_tips_cleared()``
      sees ``tipDetected: True`` and logs a warning. Default False: a drop
      always clears the sensor (existing behavior).
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
    """
    if pipettes is not None:
      self._pipettes: List[Tuple[str, int, float, float, str]] = list(pipettes)
    else:
      name, channels, min_v, max_v = pipette
      self._pipettes = [(name, channels, min_v, max_v, mount)]
    self._gripper = gripper
    self._log = log or logger.info
    self._cmds: Dict[str, Dict[str, Any]] = {}  # cmd_id -> full command data
    self._n = 0
    self._pipette_load_count = 0
    self.load_pipette_commands: List[Dict[str, Any]] = []  # recorded loadPipette params
    self.commands: List[Dict[str, Any]] = []  # every command, in send order: {commandType, params}
    self.labware_definitions: List[Dict[str, Any]] = []  # recorded custom definition uploads
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

  async def get(self, path: str) -> Dict[str, Any]:
    if path == "/health":
      return {"api_version": "dry-run", "robot_model": "OT-3 Standard", "name": "chatterbox"}
    if path == "/instruments":
      instruments: List[Dict[str, Any]] = [
        {
          "instrumentType": "pipette",
          "mount": mount,
          "instrumentName": name,
          "instrumentModel": name,
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
      self._n += 1
      cmd_id = f"cmd-{self._n}"
      self.commands.append({"commandType": ctype, "params": dict(params)})
      result: Dict[str, Any] = {}
      if ctype == "loadPipette":
        self._pipette_load_count += 1
        pipette_id = f"chatterbox-pip-{self._pipette_load_count}"
        result = {"pipetteId": pipette_id}
        self.load_pipette_commands.append(dict(params))
        mount = params.get("mount")
        if mount is not None:
          self._pipette_id_to_mount[pipette_id] = mount
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
      self._cmds[cmd_id] = cmd_data
      self._log("Chatterbox: %s %s", ctype, params)
      return {"data": cmd_data}
    return {"data": {}}  # e.g. /actions

  async def delete(self, path: str) -> Dict[str, Any]:
    return {"data": {}}

  async def close(self) -> None:
    return None
