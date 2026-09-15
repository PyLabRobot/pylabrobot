"""The Flex run: command execution over Rick's OpentronsRun/OpentronsAPI layer.

``FlexRun`` inherits the run lifecycle (create is done by the device via
``OpentronsAPI.create_run``; ``stop`` and ``load_pipette`` come from
``OpentronsRun``) and adds one thing the OT-2-shaped base does not model: an
``execute`` that submits any of the Flex's ~30 raw command types and waits for
completion, returning the *full* command dict so the heads' existing
``result.get("result", {})`` reads are unchanged. It also restores the
per-command timeout the fixed base loop drops (a 1000 uL viscous aspirate or a
long gripper move outlasts a flat 30 s), keyed off the command's own plunger
travel.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict

from pylabrobot.opentrons.errors import OpentronsCommandTimeout
from pylabrobot.opentrons.flex.errors import OpentronsCommandError
from pylabrobot.opentrons.run import OpentronsRun

# Seconds of polling a command gets on top of however long its own motion takes.
COMMAND_POLL_HEADROOM = 30.0


def _plunger_seconds(params: Dict[str, Any]) -> float:
  """How long a command's own plunger travel takes, from its volume and rate.

  A 1000 uL aspirate at a viscous-liquid flow rate outlasts any fixed poll
  timeout, and giving up mid-command rolls the trackers back while the robot
  keeps pipetting.
  """
  volume, flow_rate = params.get("volume"), params.get("flowRate")
  if not isinstance(volume, (int, float)) or not isinstance(flow_rate, (int, float)):
    return 0.0
  return abs(volume) / flow_rate if flow_rate > 0 else 0.0


@dataclass
class PipetteInfo:
  mount: str
  pipette_name: str
  pipette_model: str
  pipette_id: str
  channels: int
  min_volume: float
  max_volume: float


class FlexRun(OpentronsRun):
  """One Flex run: raw command execution with per-command timeout."""

  async def execute(
    self,
    command_type: str,
    params: Dict[str, Any],
    wait: bool = True,
    timeout: float = 30.0,
  ) -> Dict[str, Any]:
    """Submit a command within the run and (by default) wait for completion.

    Returns the completed command dict — ``{"id", "status", "result", "error"}``
    — so callers keep reading ``result["result"][...]`` as before. Raises
    :class:`OpentronsCommandError` on a "failed" status and
    :class:`OpentronsCommandTimeout` if the command does not complete in time.
    """
    self._require_active()
    timeout = max(timeout, _plunger_seconds(params) + COMMAND_POLL_HEADROOM)
    command_id = await self._api.submit_command(self.id, command_type, params, intent="setup")
    if not wait:
      return {"id": command_id, "status": "queued", "result": {}, "error": {}}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      command = await self._api.get_command(self.id, command_id)
      if command.status == "succeeded":
        return {
          "id": command_id,
          "status": command.status,
          "result": command.result,
          "error": command.error,
        }
      if command.status == "failed":
        raise OpentronsCommandError(command_type, command.error)
      await asyncio.sleep(self.command_poll_interval)

    raise OpentronsCommandTimeout(self.id, command_id, command_type)
