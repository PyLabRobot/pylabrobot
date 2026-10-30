"""Software-only lifecycle and command-cancellation checks for both robots."""

from typing import Any, Dict, Optional

import anyio
import pytest

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import OT2, Flex
from pylabrobot.opentrons.api import OpentronsAPI
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import LabwareIdentity
from pylabrobot.testing.concurrency import AnyioTestBase


class RobotHTTP(HTTP):
  """A scoped server with an observable stop-before-close boundary."""

  def __init__(self) -> None:
    super().__init__("offline robot", "http://offline:31950")
    self.events: list[str] = []
    self.fail_health = False
    self.stop_failure = False

  async def _close(self) -> None:
    self.events.append("close")
    await super()._close()

  async def request(
    self, method: str, path: str, data: Optional[Dict[str, Any]] = None
  ) -> Dict[str, Any]:
    self._require_active()
    self.events.append(f"{method} {path}")
    if path == "/health":
      if self.fail_health:
        raise RuntimeError("health unavailable")
      return {"name": "offline", "robot_model": "OT-3 Standard", "api_version": "9.1.2"}
    if path == "/pipettes":
      return {"left": {"name": None}, "right": {"name": None}}
    if path == "/deck_configuration":
      return {
        "data": {
          "cutoutFixtures": [{"cutoutId": "cutoutA3", "cutoutFixtureId": "trashBinAdapter"}]
        }
      }
    if path == "/instruments":
      return {
        "data": [
          {
            "mount": "right",
            "instrumentType": "pipette",
            "instrumentName": "p1000_single_flex",
            "instrumentModel": "p1000_single_v3.5",
            "data": {"channels": 1, "min_volume": 1, "max_volume": 1000},
          }
        ]
      }
    if path == "/runs" and method == "POST":
      return {"data": {"id": "run"}}
    if path == "/runs/run/actions":
      if self.stop_failure:
        raise RuntimeError("stop unavailable")
      return {}
    if path == "/runs/run":
      return {"data": {"id": "run", "status": "stopped"}}
    return {"data": {"id": "command", "status": "succeeded", "result": {"pipetteId": "pipette"}}}


class TestRobotLifespans(AnyioTestBase):
  """An owned transport closes after run shutdown on both AnyIO engines."""

  @pytest.mark.parametrize("robot_type", [OT2, Flex])
  async def test_owned_run_stops_before_http_close(
    self, robot_type: type, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    io = RobotHTTP()
    module = (
      "pylabrobot.opentrons.flex.flex" if robot_type is Flex else "pylabrobot.opentrons.ot2.ot2"
    )
    monkeypatch.setattr(f"{module}.HTTP", lambda **kwargs: io)
    robot = robot_type("offline")
    async with robot.lifespan(skip_home=True):
      assert robot.software_version == "9.1.2"
    assert io.events[-3:] == ["POST /runs/run/actions", "GET /runs/run", "close"]

  @pytest.mark.parametrize("robot_type", [OT2, Flex])
  async def test_failed_entry_closes_owned_http(self, robot_type: type) -> None:
    io = RobotHTTP()
    io.fail_health = True
    # Give the test its own default-owned transport.
    robot = robot_type("offline", io=io)
    robot._owns_io = True
    with pytest.raises(RuntimeError, match="health unavailable"):
      async with robot.lifespan(skip_home=True):
        pass
    assert io.events[-1] == "close"

  async def test_borrowed_http_remains_active(self) -> None:
    io = RobotHTTP()
    async with io:
      for robot_type in (OT2, Flex):
        async with robot_type("offline", io=io).lifespan(skip_home=True):
          pass
      assert (await OpentronsAPI(io).get_health()).name == "offline"
      assert "close" not in io.events

  @pytest.mark.parametrize("robot_type", [OT2, Flex])
  async def test_failed_run_stop_closes_owned_http_and_retries_on_reentry(
    self, robot_type: type, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    io = RobotHTTP()
    module = (
      "pylabrobot.opentrons.flex.flex" if robot_type is Flex else "pylabrobot.opentrons.ot2.ot2"
    )
    monkeypatch.setattr(f"{module}.HTTP", lambda **kwargs: io)
    robot = robot_type("offline")
    io.stop_failure = True
    with pytest.raises(Exception, match="stop unavailable|Could not cancel"):
      async with robot.lifespan(skip_home=True):
        pass
    assert io.events[-1] == "close"
    assert robot._run is not None
    io.stop_failure = False
    async with robot.lifespan(skip_home=True):
      assert robot._run is not None
    assert io.events.count("POST /runs/run/actions") == 3
    assert io.events[-1] == "close"


class TestRunTransactions(AnyioTestBase):
  """Cancellation after submission reconciles one finite command."""

  async def test_cancelled_command_waits_for_reply(self) -> None:
    io = RobotHTTP()
    submitted = anyio.Event()
    release = anyio.Event()

    class DelayedAPI(OpentronsAPI):
      async def submit_command(
        self, run_id: str, command_type: str, params: Dict[str, Any], intent="setup"
      ) -> str:
        submitted.set()
        await release.wait()
        return "command"

    async with io:
      run = OpentronsRun(DelayedAPI(io), "run", "9.1.2")
      async with anyio.create_task_group() as group:
        with anyio.CancelScope() as scope:

          async def cancel_after_dispatch() -> None:
            await submitted.wait()
            scope.cancel()
            release.set()

          group.start_soon(cancel_after_dispatch)
          await run.execute("home", {})
        assert scope.cancelled_caught
      assert "GET /runs/run/commands/command" in io.events

  async def test_cancelled_definition_upload_finishes_before_returning(self) -> None:
    io = RobotHTTP()
    dispatched = anyio.Event()
    release = anyio.Event()
    completed = False

    class DelayedAPI(OpentronsAPI):
      async def define_labware(self, run_id: str, definition: Dict[str, Any]) -> LabwareIdentity:
        nonlocal completed
        dispatched.set()
        await release.wait()
        completed = True
        return LabwareIdentity("pylabrobot", "rack", 1)

    async with io:
      run = OpentronsRun(DelayedAPI(io), "run", "9.1.2")
      async with anyio.create_task_group() as group:
        with anyio.CancelScope() as scope:

          async def cancel_upload() -> None:
            await dispatched.wait()
            scope.cancel()
            release.set()

          group.start_soon(cancel_upload)
          await run.define_labware({"name": "rack"})
        assert scope.cancelled_caught
      assert completed

  async def test_cancelled_ot2_home_waits_for_request_completion(self) -> None:
    io = RobotHTTP()
    dispatched = anyio.Event()
    release = anyio.Event()
    completed = False

    class DelayedAPI(OpentronsAPI):
      async def home(self) -> None:
        nonlocal completed
        dispatched.set()
        await release.wait()
        completed = True

    robot = OT2("offline", io=io)
    async with io, robot.lifespan(skip_home=True):
      robot._api = DelayedAPI(io)
      async with anyio.create_task_group() as group:
        with anyio.CancelScope() as scope:

          async def cancel_home() -> None:
            await dispatched.wait()
            scope.cancel()
            release.set()

          group.start_soon(cancel_home)
          await robot.home()
        assert scope.cancelled_caught
      assert completed
