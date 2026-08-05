"""Tests for the OpentronsRobot transport seam (Protocol + chatterbox)."""

import asyncio
import unittest
from typing import Any, Dict, List

from pylabrobot.opentrons.robot import OpentronsRobot
from pylabrobot.opentrons.transport import ChatterboxTransport, OpentronsTransport


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


if __name__ == "__main__":
  unittest.main()
