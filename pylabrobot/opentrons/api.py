"""Named robot-server endpoints over a caller-owned PLR HTTP transport."""

from typing import Any, Dict, List, Literal, Optional, Tuple

from pylabrobot.io.http import HTTP, HTTPError
from pylabrobot.opentrons.errors import OpentronsError, OpentronsProtocolError
from pylabrobot.opentrons.types import (
  CommandInfo,
  LabwareIdentity,
  ModuleInfo,
  Mount,
  MountedPipette,
  RobotInfo,
  RunInfo,
  _object,
  _optional_string,
  _string,
)

HTTP_API_VERSION = "3"


def _data_list(response: Dict[str, Any]) -> List[Dict[str, Any]]:
  data = response.get("data")
  if not isinstance(data, list):
    raise OpentronsProtocolError("Expected a list in the response's 'data' field")
  return [_object(item) for item in data]


class OpentronsAPI:
  """HTTP endpoint encoding and decoding, without device or selected-run state.

  The caller opens and closes ``io``. Queries return independent readings;
  commands return server receipts without attaching them to a device.
  """

  def __init__(self, io: HTTP) -> None:
    self._io = io

  async def get_health(self) -> RobotInfo:
    return RobotInfo.from_response(await self._io.request("GET", "/health"))

  async def get_mounted_pipettes(self) -> Tuple[MountedPipette, ...]:
    """Read the OT-2 mount endpoint without loading instruments into a run."""
    response = await self._io.request("GET", "/pipettes")
    pipettes = []
    mounts: Tuple[Mount, ...] = ("left", "right")
    for mount in mounts:
      data = _object(response.get(mount))
      name = _optional_string(data, "name")
      if name is not None:
        pipettes.append(
          MountedPipette(mount, name, _optional_string(data, "model"), _optional_string(data, "id"))
        )
    return tuple(pipettes)

  async def get_connected_modules(self) -> Tuple[ModuleInfo, ...]:
    response = await self._io.request("GET", "/modules")
    return tuple(ModuleInfo.from_response(data) for data in _data_list(response))

  async def get_runs(self) -> Tuple[RunInfo, ...]:
    response = await self._io.request("GET", "/runs")
    return tuple(RunInfo.from_response(data) for data in _data_list(response))

  async def get_run(self, run_id: str) -> RunInfo:
    response = await self._io.request("GET", f"/runs/{run_id}")
    return RunInfo.from_response(_object(response.get("data")))

  async def create_run(self) -> RunInfo:
    response = await self._io.request("POST", "/runs")
    return RunInfo.from_response(_object(response.get("data")))

  async def stop_run(self, run_id: str) -> None:
    """Stop a run, falling back only when a firmware endpoint is unsupported."""
    requests = (
      ("POST", f"/runs/{run_id}/actions", {"data": {"actionType": "stop"}}),
      ("POST", f"/runs/{run_id}/cancel", None),
      ("POST", f"/runs/{run_id}/actions/cancel", None),
      ("DELETE", f"/runs/{run_id}", None),
    )
    last_error: Optional[Exception] = None
    for method, path, data in requests:
      try:
        await self._io.request(method, path, data)
        return
      except HTTPError as error:
        last_error = error
        if error.status not in {404, 405}:
          break
      except Exception as error:
        last_error = error
        break
    raise OpentronsError(
      f"Could not cancel Opentrons run {run_id}; state is retained for retry"
    ) from (last_error)

  async def home(self) -> None:
    """Home the OT-2 gantry and pipette axes through the robot endpoint."""
    await self._io.request("POST", "/robot/home", {"target": "robot"})

  async def submit_command(
    self,
    run_id: str,
    command_type: str,
    params: Dict[str, Any],
    intent: Literal["setup", "protocol"] = "setup",
  ) -> str:
    response = await self._io.request(
      "POST",
      f"/runs/{run_id}/commands",
      {"data": {"commandType": command_type, "params": params, "intent": intent}},
    )
    return _string(_object(response.get("data")), "id")

  async def get_command(self, run_id: str, command_id: str) -> CommandInfo:
    response = await self._io.request("GET", f"/runs/{run_id}/commands/{command_id}")
    return CommandInfo.from_response(_object(response.get("data")))

  async def define_labware(self, run_id: str, definition: Dict[str, Any]) -> LabwareIdentity:
    response = await self._io.request(
      "POST", f"/runs/{run_id}/labware_definitions", {"data": definition}
    )
    return LabwareIdentity.from_uri(_string(_object(response.get("data")), "definitionUri"))
