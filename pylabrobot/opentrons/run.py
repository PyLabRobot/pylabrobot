"""Run-scoped Opentrons command primitives and completion handling."""

import asyncio
import math
import re
import time
from typing import Any, Dict, Optional, Tuple

from pylabrobot.io.http import HTTPError
from pylabrobot.opentrons.api import OpentronsAPI
from pylabrobot.opentrons.errors import (
  OpentronsCommandError,
  OpentronsCommandTimeout,
  OpentronsError,
  OpentronsProtocolError,
)
from pylabrobot.opentrons.types import LabwareIdentity, Mount, _object, _string
from pylabrobot.resources.coordinate import Coordinate


def _version_tuple(version: str) -> Tuple[int, ...]:
  parts = []
  for part in version.split("."):
    match = re.match(r"\d+", part)
    if match is None:
      break
    parts.append(int(match.group()))
  if not parts:
    raise ValueError(f"Unrecognized robot software version {version!r}")
  return tuple(parts)


def _version_at_least(version: str, required: str) -> bool:
  actual, minimum = _version_tuple(version), _version_tuple(required)
  width = max(len(actual), len(minimum))
  return actual + (0,) * (width - len(actual)) >= minimum + (0,) * (width - len(minimum))


def _coordinates(location: Coordinate) -> Dict[str, float]:
  return {"x": location.x, "y": location.y, "z": location.z}


def _well_params(
  pipette_id: str, labware_id: str, well_name: str, offset: Coordinate
) -> Dict[str, Any]:
  return {
    "pipetteId": pipette_id,
    "labwareId": labware_id,
    "wellName": well_name,
    "wellLocation": {"origin": "bottom", "offset": _coordinates(offset)},
  }


class OpentronsRun:
  """One server run, bound to one client and a fixed software version.

  Primitives await confirmed completion and do not modify PLR resource trackers
  or add pipette retractions. The owning device sequences those operations.
  """

  def __init__(
    self,
    api: OpentronsAPI,
    run_id: str,
    software_version: str,
    command_timeout: float = 30,
    command_poll_interval: float = 0.05,
  ) -> None:
    if not math.isfinite(command_timeout) or command_timeout <= 0:
      raise ValueError("command_timeout must be finite and greater than zero")
    if not math.isfinite(command_poll_interval) or command_poll_interval < 0:
      raise ValueError("command_poll_interval must be finite and non-negative")
    self._api = api
    self._id = run_id
    self._software_version = software_version
    self.command_timeout = command_timeout
    self.command_poll_interval = command_poll_interval
    self._active = True

  @property
  def id(self) -> str:
    return self._id

  @property
  def software_version(self) -> str:
    return self._software_version

  @property
  def active(self) -> bool:
    return self._active

  def _require_active(self) -> None:
    if not self._active:
      raise RuntimeError(f"Opentrons run {self.id} has stopped")

  async def stop(self) -> None:
    """Wait for this run to stop; retain active state until shutdown is confirmed."""
    if self._active:
      await self._api.stop_run(self.id)
      deadline = time.monotonic() + self.command_timeout
      while True:
        try:
          status = (await self._api.get_run(self.id)).status
        except HTTPError as error:
          if error.status == 404:
            break  # The legacy stop route can delete the run.
          raise
        if status == "stopped":
          break
        if status is None:
          raise OpentronsProtocolError(f"Missing status while waiting for run {self.id} to stop")
        if time.monotonic() >= deadline:
          raise OpentronsError(
            f"Timed out waiting for run {self.id} to stop (status {status!r}); "
            "state is retained for retry"
          )
        await asyncio.sleep(self.command_poll_interval)
      self._active = False

  async def _execute(self, command_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    self._require_active()
    command_id = await self._api.submit_command(self.id, command_type, params)
    deadline = time.monotonic() + self.command_timeout
    while True:
      try:
        command = await self._api.get_command(self.id, command_id)
      except TimeoutError as error:
        raise OpentronsCommandTimeout(self.id, command_id, command_type) from error
      if command.status == "succeeded":
        return command.result
      if command.status == "failed":
        raise OpentronsCommandError(
          self.id,
          command_id,
          command_type,
          command.error.get("errorType", "unknown"),
          command.error.get("detail", "no detail returned"),
        )
      if command.status not in {"queued", "running"}:
        raise OpentronsProtocolError(
          f"{command_type} ({command_id} in run {self.id}) returned "
          f"unexpected command status {command.status!r}"
        )
      if time.monotonic() >= deadline:
        raise OpentronsCommandTimeout(self.id, command_id, command_type)
      await asyncio.sleep(self.command_poll_interval)

  async def load_pipette(self, name: str, mount: Mount) -> str:
    result = await self._execute("loadPipette", {"pipetteName": name, "mount": mount})
    return _string(result, "pipetteId")

  async def define_labware(self, definition: Dict[str, Any]) -> LabwareIdentity:
    self._require_active()
    return await self._api.define_labware(self.id, definition)

  async def load_labware(
    self, identity: LabwareIdentity, slot: str, labware_id: str, display_name: str
  ) -> None:
    await self._execute(
      "loadLabware",
      {
        "location": {"slotName": slot},
        "loadName": identity.load_name,
        "namespace": identity.namespace,
        "version": identity.version,
        "labwareId": labware_id,
        "displayName": display_name,
      },
    )

  async def pick_up_tip(
    self, pipette_id: str, labware_id: str, well_name: str, offset: Coordinate
  ) -> None:
    await self._execute("pickUpTip", _well_params(pipette_id, labware_id, well_name, offset))

  async def drop_tip(
    self, pipette_id: str, labware_id: str, well_name: str, offset: Coordinate
  ) -> None:
    await self._execute("dropTip", _well_params(pipette_id, labware_id, well_name, offset))

  async def move_to(
    self,
    pipette_id: str,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    force_direct: bool = False,
  ) -> None:
    params: Dict[str, Any] = {
      "pipetteId": pipette_id,
      "coordinates": _coordinates(location),
      "forceDirect": force_direct,
    }
    if minimum_z_height is not None:
      params["minimumZHeight"] = minimum_z_height
    if speed is not None:
      params["speed"] = speed
    await self._execute("moveToCoordinates", params)

  async def get_position(self, pipette_id: str) -> Coordinate:
    """Read the nozzle or mounted tip's critical point through savePosition."""
    result = await self._execute("savePosition", {"pipetteId": pipette_id})
    position = _object(result.get("position"))
    axes = [position.get(axis) for axis in ("x", "y", "z")]
    if not all(isinstance(axis, (float, int)) and math.isfinite(axis) for axis in axes):
      raise OpentronsProtocolError(f"Invalid position in savePosition result: {position!r}")
    return Coordinate(x=position["x"], y=position["y"], z=position["z"])

  async def aspirate_in_place(self, pipette_id: str, volume: float, flow_rate: float) -> None:
    await self._execute(
      "aspirateInPlace", {"pipetteId": pipette_id, "volume": volume, "flowRate": flow_rate}
    )

  async def dispense_in_place(self, pipette_id: str, volume: float, flow_rate: float) -> None:
    await self._execute(
      "dispenseInPlace",
      {"pipetteId": pipette_id, "volume": volume, "flowRate": flow_rate, "pushOut": 0.0},
    )

  async def discard_tip_in_fixed_trash(self, pipette_id: str, offset: Coordinate) -> None:
    """Drop in OT-2 fixed trash using the command form supported by this run's server."""
    if _version_at_least(self.software_version, "7.1.0"):
      await self._execute(
        "moveToAddressableAreaForDropTip",
        {
          "pipetteId": pipette_id,
          "addressableAreaName": "fixedTrash",
          "offset": _coordinates(offset),
          "alternateDropLocation": False,
        },
      )
      await self._execute("dropTipInPlace", {"pipetteId": pipette_id})
    else:
      await self.drop_tip(pipette_id, "fixedTrash", "A1", offset)
