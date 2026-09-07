"""Tests for the offline ``ChatterboxHTTP`` io and the record/replay roundtrip.

``ChatterboxHTTP`` answers the robot-server exchanges from canned state, so an
``OpentronsFlex`` runs its whole lifecycle with no network. These pin the reply
shapes a driver reads (the ``{"data": ...}`` envelope, succeeded/failed command
bodies, the instrument list) and prove a Flex setup recorded through the real
``HTTP`` replays byte-for-byte through ``ReplayTransport``.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

import pylabrobot
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.http import HTTP
from pylabrobot.opentrons.flex.chatterbox import DEFAULT_HEADERS, ChatterboxHTTP, ReplayTransport
from pylabrobot.opentrons.flex.flex import OpentronsFlex
from pylabrobot.opentrons.flex.flex_head import FlexHead8
from pylabrobot.resources.opentrons.flex_deck import FlexDeck


class TestChatterboxHTTPRequest(unittest.TestCase):
  """The single ``request`` seam answers every endpoint the lifecycle drives."""

  def test_health_reports_the_configured_version(self):
    io = ChatterboxHTTP(api_version="8.1.0")
    result = asyncio.run(io.request("GET", "/health"))
    self.assertEqual(result["api_version"], "8.1.0")
    self.assertEqual(result["robot_model"], "OT-3 Standard")

  def test_post_commands_returns_a_succeeded_shaped_dict(self):
    io = ChatterboxHTTP()
    payload: Dict[str, Any] = {"data": {"commandType": "home", "params": {}, "intent": "setup"}}
    result = asyncio.run(io.request("POST", "/runs/some-run/commands", payload))
    data = result["data"]
    self.assertEqual(data["status"], "succeeded")
    self.assertEqual(data["commandType"], "home")
    self.assertIn("result", data)

  def test_a_submitted_command_is_pollable_by_its_id(self):
    io = ChatterboxHTTP()
    payload = {"data": {"commandType": "home", "params": {}, "intent": "setup"}}
    posted = asyncio.run(io.request("POST", "/runs/r/commands", payload))
    cmd_id = posted["data"]["id"]
    polled = asyncio.run(io.request("GET", f"/runs/r/commands/{cmd_id}"))
    self.assertEqual(polled["data"]["status"], "succeeded")

  def test_delete_returns_an_enveloped_body(self):
    io = ChatterboxHTTP()
    self.assertEqual(asyncio.run(io.request("DELETE", "/runs/r")), {"data": {}})

  def test_stop_and_setup_are_noops(self):
    io = ChatterboxHTTP()
    asyncio.run(io.setup())  # must not raise
    asyncio.run(io.stop())  # must not raise

  def test_unknown_enum_value_is_rejected_like_the_robot_server(self):
    io = ChatterboxHTTP()
    payload = {"data": {"commandType": "verifyTipPresence", "params": {"expectedState": "bogus"}}}
    with self.assertRaises(ValueError):
      asyncio.run(io.request("POST", "/runs/r/commands", payload))


class TestChatterboxHTTPInstruments(unittest.TestCase):
  """``/instruments`` serves the mounted pipettes (and optional gripper)."""

  def test_pipettes_kwarg_reports_both_mounts(self):
    io = ChatterboxHTTP(
      pipettes=[
        ("p50_multi_flex", 8, 1.0, 50.0, "left"),
        ("p1000_single_flex", 1, 1.0, 1000.0, "right"),
      ]
    )
    result = asyncio.run(io.request("GET", "/instruments"))
    mounts = {entry["mount"]: entry for entry in result["data"]}
    self.assertEqual(set(mounts), {"left", "right"})
    self.assertEqual(mounts["left"]["data"]["channels"], 8)
    self.assertEqual(mounts["right"]["data"]["channels"], 1)

  def test_empty_pipettes_list_reports_no_instruments(self):
    io = ChatterboxHTTP(pipettes=[])
    result = asyncio.run(io.request("GET", "/instruments"))
    self.assertEqual(result["data"], [])

  def test_single_pipette_kwarg_reports_on_the_given_mount(self):
    io = ChatterboxHTTP(pipette=("p1000_single_flex", 1, 1.0, 1000.0), mount="left")
    result = asyncio.run(io.request("GET", "/instruments"))
    self.assertEqual(len(result["data"]), 1)
    self.assertEqual(result["data"][0]["mount"], "left")

  def test_gripper_is_reported_on_the_extension_mount_when_asked(self):
    io = ChatterboxHTTP(pipettes=[], gripper=True)
    result = asyncio.run(io.request("GET", "/instruments"))
    grippers = [e for e in result["data"] if e["instrumentType"] == "gripper"]
    self.assertEqual(len(grippers), 1)
    self.assertEqual(grippers[0]["mount"], "extension")

  def test_reading_instruments_increments_the_guard_counter(self):
    io = ChatterboxHTTP()
    asyncio.run(io.request("GET", "/instruments"))
    asyncio.run(io.request("GET", "/instruments"))
    self.assertEqual(io.instrument_reads, 2)

  def test_load_pipette_commands_get_distinct_ids(self):
    io = ChatterboxHTTP(
      pipettes=[
        ("p50_multi_flex", 8, 1.0, 50.0, "left"),
        ("p1000_single_flex", 1, 1.0, 1000.0, "right"),
      ]
    )

    async def _load_both():
      ids = []
      for name, mount in (("p50_multi_flex", "left"), ("p1000_single_flex", "right")):
        result = await io.request(
          "POST",
          "/runs/r/commands",
          {
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
    self.assertEqual(len(set(ids)), 2)
    self.assertEqual(len(io.load_pipette_commands), 2)


class TestChatterboxPlungerModeling(unittest.TestCase):
  """The plunger-priming rule models a draw the real robot would refuse."""

  def test_aspirate_in_place_on_an_unprimed_plunger_fails_without_raising(self):
    io = ChatterboxHTTP()

    async def _drive():
      load = await io.request(
        "POST",
        "/runs/r/commands",
        {"data": {"commandType": "loadPipette", "params": {"mount": "right"}}},
      )
      pid = load["data"]["result"]["pipetteId"]
      # An in-place aspirate with nothing to prime it: the engine refuses, and
      # the refusal is a failed command body, NOT a transport exception.
      return await io.request(
        "POST",
        "/runs/r/commands",
        {"data": {"commandType": "aspirateInPlace", "params": {"pipetteId": pid, "volume": 5.0}}},
      )

    result = asyncio.run(_drive())
    self.assertEqual(result["data"]["status"], "failed")
    self.assertEqual(result["data"]["error"]["errorType"], "PipetteNotReadyToAspirateError")


class TestOpentronsFlexOffline(unittest.IsolatedAsyncioTestCase):
  """An injected ``ChatterboxHTTP`` lets the whole lifecycle run with no network."""

  async def test_setup_completes_and_discovers_the_configured_head(self):
    io = ChatterboxHTTP(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")])
    flex = OpentronsFlex(deck=FlexDeck(), host="localhost", io=io)
    await flex.setup()
    try:
      self.assertEqual(flex.api_version, "dry-run")
      self.assertIsInstance(flex.left, FlexHead8)
      self.assertIsNone(flex.right)
    finally:
      await flex.stop()


class _RecordingThroughChatterbox(HTTP):
  """A real ``HTTP`` whose wire is answered by a ``ChatterboxHTTP``.

  The base ``HTTP.request`` records every exchange to the capture log, so
  driving a setup through this records a replayable capture with no robot: only
  ``_make_request`` is overridden, to hand the request to the chatterbox and
  serialize its reply as the response body the recorder expects.
  """

  def __init__(self, chatterbox: ChatterboxHTTP, base_url: str) -> None:
    super().__init__("Opentrons Flex", base_url=base_url, headers=DEFAULT_HEADERS)
    self._chatterbox = chatterbox

  def _make_request(self, method: str, path: str, data: Optional[Dict[str, Any]]):
    body = asyncio.run(self._chatterbox.request(method, path, data))
    return 200, json.dumps(body)


class RecordAndReplayTests(unittest.IsolatedAsyncioTestCase):
  """A recorded Flex setup replays with nothing on the network.

  The recording is taken over the real ``HTTP`` path, so what the replay proves
  is that the driver reaches the same state from the capture file alone.
  """

  def setUp(self):
    self._dir = tempfile.TemporaryDirectory()
    self.capture_file = Path(self._dir.name) / "flex_setup.json"
    self.base_url = "http://robot.test:31950"

  def tearDown(self):
    self._dir.cleanup()

  async def _record_a_setup(self) -> None:
    chatterbox = ChatterboxHTTP(
      pipettes=[("p1000_multi_flex", 8, 5.0, 1000.0, "left")],
      gripper=True,
    )
    # The io is built before capture starts: every pylabrobot io refuses
    # construction while a capture is active.
    recording = _RecordingThroughChatterbox(chatterbox, base_url=self.base_url)
    flex = OpentronsFlex(deck=FlexDeck(), host="robot.test", io=recording)
    pylabrobot.start_capture(self.capture_file)
    try:
      await flex.setup()
    finally:
      pylabrobot.stop_capture()

  async def test_replayed_setup_discovers_the_same_head(self):
    await self._record_a_setup()

    replay = ReplayTransport(self.capture_file, base_url=self.base_url)
    flex = OpentronsFlex(deck=FlexDeck(), host="robot.test", io=replay)
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

    replay = ReplayTransport(self.capture_file, base_url=self.base_url)
    flex = OpentronsFlex(deck=FlexDeck(), host="robot.test", io=replay)
    await flex.connect()

    with self.assertRaisesRegex(ValidationError, "not fully read"):
      replay.assert_fully_replayed()


if __name__ == "__main__":
  unittest.main()
