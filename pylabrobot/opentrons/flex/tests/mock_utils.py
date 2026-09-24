"""Standard mocks for the API dependency of Flex driver unit tests."""

from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import Flex, OpentronsAPI
from pylabrobot.opentrons.flex.flex_wire import OFFLINE_API_VERSION
from pylabrobot.opentrons.types import (
  CommandInfo,
  InstrumentInfo,
  LabwareIdentity,
  RobotInfo,
  RunInfo,
)
from pylabrobot.resources.opentrons import FlexDeck

_MODELS = {
  "p50_single_flex": "p50_single_v3.5",
  "p50_multi_flex": "p50_multi_v3.5",
  "p1000_single_flex": "p1000_single_v3.5",
  "p1000_multi_flex": "p1000_multi_v3.5",
  "p1000_96": "p1000_96_v3.5",
  "p200_96": "p200_96_v3.3",
}


def make_api(
  *,
  pipette: Tuple[str, int, float, float] = ("p1000_single_flex", 1, 1.0, 1000.0),
  mount: str = "right",
  pipettes: Optional[List[Tuple[str, int, float, float, str]]] = None,
  gripper: bool = False,
  api_version: str = OFFLINE_API_VERSION,
  saved_position: Optional[Dict[str, float]] = None,
  liquid_probe_z: Optional[float] = None,
) -> AsyncMock:
  """Supply fixed API replies; individual tests set their own failures and readings."""
  api = AsyncMock(spec=OpentronsAPI)
  api.get_health.return_value = RobotInfo("test-flex", "OT-3 Standard", api_version)
  api.create_run.return_value = RunInfo("run")
  api.get_run.return_value = RunInfo("run", "stopped")
  instruments = [
    InstrumentInfo(side, "pipette", name, _MODELS.get(name, name), channels, minimum, maximum)
    for name, channels, minimum, maximum, side in (
      pipettes if pipettes is not None else [(*pipette, mount)]
    )
  ]
  if gripper:
    instruments.append(InstrumentInfo("extension", "gripper", "flexGripper", "gripperV1.3"))
  api.get_instruments.return_value = tuple(instruments)
  api.submit_command.return_value = "command"
  result: Dict[str, Any] = {
    "pipetteId": "pipette",
    "labwareId": "labware",
    "position": saved_position or {"x": 100.0, "y": 100.0, "z": 100.0},
    "status": "unknown",
  }
  if liquid_probe_z is not None:
    result["z_position"] = liquid_probe_z
  api.get_command.return_value = CommandInfo("succeeded", result, {})

  def definition_identity(run_id: str, definition: Dict[str, Any]) -> LabwareIdentity:
    """Use the uploaded definition's declared identity as the mock return value."""
    return LabwareIdentity(
      definition["namespace"], definition["parameters"]["loadName"], definition["version"]
    )

  api.define_labware.side_effect = definition_identity
  return api


def make_flex(host: str = "robot.test", *, api: AsyncMock, deck: Optional[FlexDeck] = None) -> Flex:
  """Construct the real driver with mocked IO and API dependencies."""
  flex = Flex(host, io=AsyncMock(spec=HTTP), deck=deck)
  flex._api = api
  return flex
