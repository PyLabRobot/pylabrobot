"""Tests for the OpentronsRobot transport seam (Protocol + chatterbox)."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

import pytest

import pylabrobot
from pylabrobot.io.errors import ValidationError
from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.robot import OpentronsRobot
from pylabrobot.opentrons.transport import (
  ChatterboxTransport,
  HttpxTransport,
  OpentronsTransport,
  ReplayTransport,
)
from pylabrobot.resources.opentrons.flex_deck import FlexDeck

if TYPE_CHECKING:
  import httpx
else:
  httpx = pytest.importorskip("httpx")

from pylabrobot.testing.http_server import serving  # noqa: E402


class _StubRobot(OpentronsRobot):
  """Minimal concrete subclass so we can exercise the shared lifecycle.

  Stands in for a future single-pipette OT-2 subclass: discovery is the
  subclass's job (the base ``setup()`` no longer calls
  ``_discover_pipette()`` itself), so ``_model_setup()`` calls it here.
  """

  async def _model_setup(self) -> None:
    self.pipette = await self._discover_pipette()


class _NoOpStubRobot(OpentronsRobot):
  """A subclass whose ``_model_setup()`` does nothing — no pipette discovery."""

  async def _model_setup(self) -> None:
    pass


class TestChatterboxTransportProtocol(unittest.TestCase):
  """ChatterboxTransport satisfies the OpentronsTransport Protocol."""

  def test_is_instance_of_protocol(self):
    transport: OpentronsTransport = ChatterboxTransport()
    self.assertIsInstance(transport, OpentronsTransport)

  def test_post_commands_returns_succeeded_shaped_dict(self):
    transport = ChatterboxTransport()
    payload: Dict[str, Any] = {"data": {"commandType": "home", "params": {}, "intent": "setup"}}
    result = asyncio.run(transport.post("/runs/some-run/commands", json=payload))
    data = result["data"]
    self.assertEqual(data["status"], "succeeded")
    self.assertEqual(data["commandType"], "home")
    self.assertIn("result", data)

  def test_close_is_a_noop(self):
    transport = ChatterboxTransport()
    asyncio.run(transport.close())  # must not raise


class TestOpentronsRobotWithInjectedChatterbox(unittest.TestCase):
  """An injected ChatterboxTransport lets setup() complete with no network."""

  def test_setup_completes_offline(self):
    transport = ChatterboxTransport(pipette=("p1000_single_flex", 1, 1.0, 1000.0))
    robot = _StubRobot(host="localhost", transport=transport)
    asyncio.run(robot.setup())
    try:
      self.assertIs(robot._transport, transport)
      self.assertEqual(robot.api_version, "dry-run")
      self.assertIsNotNone(robot.run_id)
      self.assertIsNotNone(robot.pipette)
      assert robot.pipette is not None
      self.assertEqual(robot.pipette.channels, 1)
      self.assertEqual(robot.pipette.pipette_id, "chatterbox-pip-1")
    finally:
      asyncio.run(robot.stop())

  def test_setup_discovers_configured_channel_count(self):
    transport = ChatterboxTransport(pipette=("p50_multi_flex", 8, 1.0, 50.0))
    robot = _StubRobot(host="localhost", transport=transport)
    asyncio.run(robot.setup())
    try:
      assert robot.pipette is not None
      self.assertEqual(robot.pipette.channels, 8)
    finally:
      asyncio.run(robot.stop())

  def test_home_routes_through_injected_transport(self):
    transport = ChatterboxTransport()
    robot = _StubRobot(host="localhost", transport=transport)
    asyncio.run(robot.setup())
    try:
      result = asyncio.run(robot.home())
      self.assertEqual(result["status"], "succeeded")
    finally:
      asyncio.run(robot.stop())


class TestBaseSetupDoesNotDiscoverPipette(unittest.TestCase):
  """Regression for the double-``loadPipette`` bug: base ``setup()`` must not
  call ``_discover_pipette()`` itself — that is entirely ``_model_setup()``'s
  job, so a subclass whose ``_model_setup()`` skips discovery loads zero
  pipettes, not one.
  """

  def test_no_op_model_setup_loads_no_pipette(self):
    transport = ChatterboxTransport(pipette=("p1000_single_flex", 1, 1.0, 1000.0))
    robot = _NoOpStubRobot(host="localhost", transport=transport)
    asyncio.run(robot.setup())
    try:
      self.assertIsNone(robot.pipette)
      self.assertEqual(len(transport.load_pipette_commands), 0)
    finally:
      asyncio.run(robot.stop())


class TestChatterboxTransportMultiplePipettes(unittest.TestCase):
  """ChatterboxTransport can simulate more than one mounted pipette."""

  def test_pipettes_kwarg_reports_both_mounts(self):
    transport = ChatterboxTransport(
      pipettes=[
        ("p50_multi_flex", 8, 1.0, 50.0, "left"),
        ("p1000_single_flex", 1, 1.0, 1000.0, "right"),
      ]
    )
    result = asyncio.run(transport.get("/instruments"))
    mounts = {entry["mount"]: entry for entry in result["data"]}
    self.assertEqual(set(mounts), {"left", "right"})
    self.assertEqual(mounts["left"]["data"]["channels"], 8)
    self.assertEqual(mounts["right"]["data"]["channels"], 1)

  def test_empty_pipettes_list_reports_no_instruments(self):
    transport = ChatterboxTransport(pipettes=[])
    result = asyncio.run(transport.get("/instruments"))
    self.assertEqual(result["data"], [])

  def test_single_pipette_kwarg_still_works(self):
    transport = ChatterboxTransport(pipette=("p1000_single_flex", 1, 1.0, 1000.0), mount="left")
    result = asyncio.run(transport.get("/instruments"))
    self.assertEqual(len(result["data"]), 1)
    self.assertEqual(result["data"][0]["mount"], "left")

  def test_load_pipette_commands_are_recorded_with_distinct_ids(self):
    transport = ChatterboxTransport(
      pipettes=[
        ("p50_multi_flex", 8, 1.0, 50.0, "left"),
        ("p1000_single_flex", 1, 1.0, 1000.0, "right"),
      ]
    )

    async def _load_both() -> List[str]:
      ids: List[str] = []
      for name, mount in (("p50_multi_flex", "left"), ("p1000_single_flex", "right")):
        result = await transport.post(
          "/runs/some-run/commands",
          json={
            "data": {
              "commandType": "loadPipette",
              "params": {"pipetteName": name, "mount": mount},
              "intent": "setup",
            }
          },
        )
        ids.append(result["data"]["result"]["pipetteId"])
      return ids

    ids = asyncio.run(_load_both())
    self.assertEqual(len(ids), 2)
    self.assertEqual(len(set(ids)), 2)  # distinct pipetteIds
    self.assertEqual(len(transport.load_pipette_commands), 2)


class TestTransportIsBuiltBeforeCaptureCanBeArmed(unittest.TestCase):
  """A robot builds its transport in __init__, not on connect.

  Every pylabrobot io refuses construction while a capture is active, so
  building it on connect made the documented recording recipe (construct,
  start_capture, setup) die with "Cannot create a new HTTP object while
  capture or validation is active" unless the caller passed a transport in.
  """

  def test_a_robot_built_with_no_transport_still_has_one(self):
    flex = OpentronsFlex(deck=FlexDeck(), host="robot.test")
    self.assertIsInstance(flex._transport, HttpxTransport)

  def test_arming_a_capture_after_construction_does_not_refuse_the_io(self):
    flex = OpentronsFlex(deck=FlexDeck(), host="robot.test")
    with tempfile.TemporaryDirectory() as tmp:
      pylabrobot.start_capture(Path(tmp) / "c.json")
      try:
        # The io already exists, so nothing here needs to construct one.
        self.assertIsInstance(flex._transport, HttpxTransport)
      finally:
        pylabrobot.stop_capture()


class RecordAndReplayTests(unittest.IsolatedAsyncioTestCase):
  """A recorded Flex lifecycle replays with nothing on the network.

  The recording is taken over ``HttpxTransport``, which is the path a real
  robot uses, so what the replay proves is that the driver reaches the same
  state from the capture file alone.
  """

  def setUp(self):
    self._dir = tempfile.TemporaryDirectory()
    self.capture_file = Path(self._dir.name) / "flex_setup.json"

  def tearDown(self):
    self._dir.cleanup()

  async def _record_a_setup(self) -> None:
    """Drive setup() over HTTP against a stand-in server, capturing as we go."""
    robot_server = ChatterboxTransport(
      pipettes=[("p1000_multi_flex", 8, 5.0, 1000.0, "left")],
      gripper=True,
    )

    async def answer(request: httpx.Request) -> httpx.Response:
      path = request.url.path
      if request.method == "GET":
        body = await robot_server.get(path)
      elif request.method == "DELETE":
        body = await robot_server.delete(path)
      else:
        sent = json.loads(request.content) if request.content else {}
        body = await robot_server.post(path, json=sent)
      return httpx.Response(200, json=body)

    # The transport is built before capture starts: every pylabrobot io
    # refuses construction while a capture is active.
    transport = HttpxTransport(base_url="http://robot.test:31950")
    flex = OpentronsFlex(deck=FlexDeck(), host="robot.test", transport=transport)
    with serving(answer):
      pylabrobot.start_capture(self.capture_file)
      try:
        await flex.setup()
      finally:
        pylabrobot.stop_capture()

  async def test_replayed_setup_discovers_the_same_head(self):
    await self._record_a_setup()

    replay = ReplayTransport(self.capture_file, base_url="http://robot.test:31950")
    flex = OpentronsFlex(deck=FlexDeck(), host="robot.test", transport=replay)
    await flex.setup()

    left = flex.left
    self.assertIsNotNone(left)
    self.assertIsNone(flex.right)
    assert left is not None
    self.assertEqual(left.channels, 8)
    replay.assert_fully_replayed()

  async def test_a_dropped_command_fails_the_replay(self):
    """Skipping a step must fail, or a replay could pass while doing less."""
    await self._record_a_setup()

    replay = ReplayTransport(self.capture_file, base_url="http://robot.test:31950")
    flex = OpentronsFlex(deck=FlexDeck(), host="robot.test", transport=replay)
    await flex.connect()

    with self.assertRaisesRegex(ValidationError, "not fully read"):
      replay.assert_fully_replayed()


if __name__ == "__main__":
  unittest.main()
