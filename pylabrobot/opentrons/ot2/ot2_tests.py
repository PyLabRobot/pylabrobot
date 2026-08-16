import unittest
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons.ot2.ot2 import OpentronsOT2, OpentronsOT2Error, _version_at_least
from pylabrobot.resources import Coordinate, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul


class FakeHTTP(HTTP):
  """In-memory OT-2 HTTP API with successful commands by default."""

  def __init__(
    self,
    left_pipette_name: Optional[str] = "p20_single_gen2",
    right_pipette_name: Optional[str] = None,
    api_version: str = "7.1.0",
  ):
    self.left_pipette_name = left_pipette_name
    self.right_pipette_name = right_pipette_name
    self.api_version = api_version
    self.calls: List[Tuple[str, str, Optional[Dict[str, Any]]]] = []
    self.commands: List[Dict[str, Any]] = []
    self.command_results: Dict[str, Dict[str, Any]] = {}
    self.fail_command_type: Optional[str] = None
    self.stop_action_supported = True
    self.started = False

  async def setup(self) -> None:
    self.started = True

  async def stop(self) -> None:
    self.started = False

  async def request(
    self,
    method: str,
    path: str,
    data: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    self.calls.append((method, path, data))
    if method == "POST" and path == "/runs":
      return {"data": {"id": "run-id"}}
    if method == "GET" and path == "/pipettes":
      return {
        "left": {"name": self.left_pipette_name},
        "right": {"name": self.right_pipette_name},
      }
    if method == "GET" and path == "/health":
      return {"api_version": self.api_version}
    if method == "POST" and path == "/robot/home":
      return {"data": {}}
    if method == "GET" and path == "/modules":
      return {"data": [{"id": "temperature-module"}]}
    if method == "POST" and path == "/runs/run-id/actions":
      if data != {"data": {"actionType": "stop"}}:
        raise AssertionError(f"Unexpected stop action: {data}")
      if not self.stop_action_supported:
        raise RuntimeError("stop action is unsupported")
      return {"data": {}}
    if method == "POST" and path == "/runs/run-id/cancel":
      return {"data": {}}
    if method == "POST" and path == "/runs/run-id/labware_definitions":
      return {"data": {"definitionUri": "pylabrobot/fake-tip-rack/1"}}
    if method == "POST" and path == "/runs/run-id/commands":
      assert data is not None
      command = data["data"]
      self.commands.append(command)
      command_id = f"command-{len(self.commands)}"
      result: Dict[str, Any] = {}
      if command["commandType"] == "loadPipette":
        result = {"pipetteId": f"{command['params']['mount']}-pipette-id"}
      self.command_results[command_id] = {
        "commandType": command["commandType"],
        "result": result,
      }
      return {"data": {"id": command_id}}
    if method == "GET" and path.startswith("/runs/run-id/commands/"):
      command_id = path.rsplit("/", 1)[-1]
      command = self.command_results[command_id]
      if command["commandType"] == self.fail_command_type:
        return {
          "data": {
            "status": "failed",
            "error": {"errorType": "hardware", "detail": "simulated failure"},
          }
        }
      return {"data": {"status": "succeeded", "result": command["result"]}}
    raise AssertionError(f"Unexpected HTTP request: {method} {path} {data}")


class OpentronsOT2Tests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.io = FakeHTTP()
    self.deck = OTDeck()
    self.robot = OpentronsOT2(
      host="ot2.local",
      deck=self.deck,
      command_poll_interval=0,
      io=self.io,
    )
    await self.robot.setup()
    self.tips = opentrons_96_filtertiprack_20ul(name="tips")
    self.tips.model = None
    self.deck.assign_child_at_slot(self.tips, slot=1)
    self.plate = celltreat_96_wellplate_350uL_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=2)

  async def asyncTearDown(self) -> None:
    if self.robot._run_id is not None:
      await self.robot.stop()
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_setup_discovers_real_pipette_objects_and_homes(self) -> None:
    self.assertTrue(self.io.started)
    self.assertIsNotNone(self.robot.left_pipette)
    assert self.robot.left_pipette is not None
    self.assertEqual(self.robot.left_pipette.mount, "left")
    self.assertEqual(self.robot.left_pipette.name, "p20_single_gen2")
    self.assertEqual(self.robot.left_pipette.channels, 1)
    self.assertIsNone(self.robot.right_pipette)
    self.assertIn(("POST", "/robot/home", {"target": "robot"}), self.io.calls)
    self.assertEqual(await self.robot.list_connected_modules(), [{"id": "temperature-module"}])

  async def test_full_single_channel_protocol_updates_trackers_and_commands(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
    source = self.plate.get_well("A1")
    destination = self.plate.get_well("B1")
    source.tracker.set_volume(15)

    await pipette.pick_up_tip(self.tips.get_item("A1"))
    await pipette.aspirate(source, volume=10)
    await pipette.dispense(destination, volume=10)
    await pipette.discard_tip()

    self.assertFalse(self.tips.get_item("A1").has_tip())
    self.assertAlmostEqual(source.tracker.get_used_volume(), 5)
    self.assertAlmostEqual(destination.tracker.get_used_volume(), 10)
    self.assertFalse(pipette.has_tip)

    command_types = [command["commandType"] for command in self.io.commands]
    self.assertEqual(command_types.count("loadLabware"), 1)
    self.assertEqual(command_types.count("pickUpTip"), 1)
    self.assertEqual(command_types.count("aspirateInPlace"), 1)
    self.assertEqual(command_types.count("dispenseInPlace"), 1)
    self.assertEqual(command_types.count("moveToCoordinates"), 4)
    self.assertEqual(command_types.count("moveToAddressableAreaForDropTip"), 1)
    self.assertEqual(command_types.count("dropTipInPlace"), 1)

    definition_request = next(
      data
      for method, path, data in self.io.calls
      if method == "POST" and path.endswith("/labware_definitions")
    )
    assert definition_request is not None
    definition = definition_request["data"]
    self.assertEqual(definition["ordering"][0][0], "A1")
    self.assertIn("A1", definition["wells"])
    self.assertEqual(definition["groups"][0]["metadata"], {})
    self.assertEqual(definition["cornerOffsetFromSlot"], {"x": 0, "y": 0, "z": 0})
    self.assertEqual(
      definition["wells"]["A1"]["depth"],
      definition["parameters"]["tipLength"],
    )

    load_labware = next(
      command for command in self.io.commands if command["commandType"] == "loadLabware"
    )
    self.assertIsInstance(load_labware["params"]["version"], int)

    pick_up_tip = next(
      command for command in self.io.commands if command["commandType"] == "pickUpTip"
    )
    self.assertEqual(pick_up_tip["params"]["wellName"], "A1")

    move_to_trash = next(
      command
      for command in self.io.commands
      if command["commandType"] == "moveToAddressableAreaForDropTip"
    )
    self.assertEqual(move_to_trash["params"]["offset"], {"x": 0, "y": 0, "z": 10})
    self.assertNotIn("wellLocation", move_to_trash["params"])

    aspirate = next(
      command for command in self.io.commands if command["commandType"] == "aspirateInPlace"
    )
    dispense = next(
      command for command in self.io.commands if command["commandType"] == "dispenseInPlace"
    )
    self.assertEqual(aspirate["params"]["flowRate"], 3.78)
    self.assertEqual(dispense["params"]["flowRate"], 7.56)
    self.assertEqual(dispense["params"]["pushOut"], 0.0)

  async def test_return_tip_restores_its_origin(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
    origin = self.tips.get_item("A1")

    await pipette.pick_up_tip(origin)
    await pipette.return_tip()

    self.assertTrue(origin.has_tip())
    self.assertFalse(pipette.has_tip)
    command_types = [command["commandType"] for command in self.io.commands]
    self.assertEqual(command_types.count("loadLabware"), 1)
    self.assertEqual(command_types.count("dropTip"), 1)

  async def test_official_tip_rack_uses_builtin_definition_for_tip_length_calibration(self) -> None:
    tips = opentrons_96_filtertiprack_20ul(name="official_tips")
    self.deck.assign_child_at_slot(tips, slot=3)
    pipette = self.robot.left_pipette
    assert pipette is not None
    definition_request_count = len(
      [
        path
        for method, path, _ in self.io.calls
        if method == "POST" and path.endswith("definitions")
      ]
    )

    await pipette.pick_up_tip(tips.get_item("A1"))

    self.assertEqual(
      len(
        [
          path
          for method, path, _ in self.io.calls
          if method == "POST" and path.endswith("definitions")
        ]
      ),
      definition_request_count,
    )
    load_labware = next(
      command for command in reversed(self.io.commands) if command["commandType"] == "loadLabware"
    )
    self.assertEqual(load_labware["params"]["namespace"], "opentrons")
    self.assertEqual(
      load_labware["params"]["loadName"],
      "opentrons_96_filtertiprack_20ul",
    )
    self.assertEqual(load_labware["params"]["version"], 1)

  async def test_failed_aspiration_rolls_back_volume_trackers(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.io.fail_command_type = "aspirateInPlace"

    with self.assertRaisesRegex(OpentronsOT2Error, "simulated failure"):
      await pipette.aspirate(source, volume=10)

    self.assertAlmostEqual(source.tracker.get_used_volume(), 15)
    assert pipette.tip is not None
    self.assertAlmostEqual(pipette.tip.tracker.get_used_volume(), 0)

  async def test_unreachable_move_is_rejected_before_an_http_command(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
    command_count = len(self.io.commands)

    with self.assertRaisesRegex(ValueError, "reachable"):
      await pipette.move_to(Coordinate(500, 0, 10))

    self.assertEqual(len(self.io.commands), command_count)

  async def test_negative_z_move_is_rejected_before_an_http_command(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
    command_count = len(self.io.commands)

    with self.assertRaisesRegex(ValueError, "non-negative"):
      await pipette.move_to(Coordinate(10, 10, -1))

    self.assertEqual(len(self.io.commands), command_count)

  async def test_stop_cancels_run_and_clears_discovered_state(self) -> None:
    await self.robot.stop()
    self.assertFalse(self.io.started)
    self.assertIsNone(self.robot.left_pipette)
    self.assertIsNone(self.robot.api_version)
    self.assertIn(
      ("POST", "/runs/run-id/actions", {"data": {"actionType": "stop"}}),
      self.io.calls,
    )

  async def test_stop_falls_back_for_older_robot_software(self) -> None:
    self.io.stop_action_supported = False

    await self.robot.stop()

    self.assertIn(("POST", "/runs/run-id/cancel", None), self.io.calls)


class OpentronsOT2MultiChannelTests(unittest.IsolatedAsyncioTestCase):
  async def test_multi_channel_is_modeled_but_not_mistracked_as_one_tip(self) -> None:
    io = FakeHTTP(left_pipette_name="p20_multi_gen2")
    deck = OTDeck()
    robot = OpentronsOT2(host="ot2.local", deck=deck, command_poll_interval=0, io=io)
    await robot.setup(skip_home=True)
    tips = opentrons_96_filtertiprack_20ul(name="tips")
    deck.assign_child_at_slot(tips, slot=1)
    assert robot.left_pipette is not None
    self.assertEqual(robot.left_pipette.channels, 8)

    with self.assertRaisesRegex(NotImplementedError, "Multi-channel"):
      await robot.left_pipette.pick_up_tip(tips.get_item("A1"))

    await robot.stop()


class OpentronsVersionTests(unittest.TestCase):
  def test_version_comparison_is_numeric(self) -> None:
    self.assertTrue(_version_at_least("7.10.0", "7.1.0"))
    self.assertTrue(_version_at_least("10.0.0", "7.1.0"))
    self.assertFalse(_version_at_least("7.0.9", "7.1.0"))


if __name__ == "__main__":
  unittest.main()
