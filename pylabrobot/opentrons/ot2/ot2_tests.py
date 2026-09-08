import asyncio
import unittest
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.io.http import HTTP, HTTPError
from pylabrobot.opentrons import OT2, OpentronsError, OT2_8ChannelPipette, OT2SingleChannelPipette
from pylabrobot.opentrons.ot2.pipette import _OT2Pipette
from pylabrobot.opentrons.types import ModuleInfo
from pylabrobot.resources import Coordinate, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.errors import (
  HasTipError,
  NoTipError,
  TooLittleLiquidError,
  TooLittleVolumeError,
)
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
    self.fail_command_occurrence = 1
    self.stop_action_supported = True
    self.stop_requests_fail = False
    self.started = False
    self.saved_position = {"x": 30.25, "y": 40.5, "z": 20.0}

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
    await asyncio.sleep(0)
    self.calls.append((method, path, data))
    if self.stop_requests_fail and (path.endswith(("/actions", "/cancel")) or method == "DELETE"):
      raise RuntimeError("stop request rejected")
    if method == "POST" and path == "/runs":
      return {"data": {"id": "run-id"}}
    if method == "GET" and path == "/runs":
      return {"data": [{"id": "run-id", "status": "idle"}]}
    if method == "GET" and path == "/runs/run-id":
      return {"data": {"id": "run-id", "status": "stopped"}}
    if method == "GET" and path == "/pipettes":
      return {
        "left": {"name": self.left_pipette_name},
        "right": {"name": self.right_pipette_name},
      }
    if method == "GET" and path == "/health":
      return {"name": "test-ot2", "robot_model": "OT-2 Standard", "api_version": self.api_version}
    if method == "POST" and path == "/robot/home":
      return {"data": {}}
    if method == "GET" and path == "/modules":
      return {"data": [{"id": "temperature-module"}]}
    if method == "POST" and path == "/runs/run-id/actions":
      if data != {"data": {"actionType": "stop"}}:
        raise AssertionError(f"Unexpected stop action: {data}")
      if not self.stop_action_supported:
        raise HTTPError(method, path, 404, "stop action is unsupported")
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
      elif command["commandType"] == "savePosition":
        result = {"positionId": "position-id", "position": self.saved_position.copy()}
      self.command_results[command_id] = {
        "commandType": command["commandType"],
        "result": result,
        "occurrence": sum(
          previous["commandType"] == command["commandType"] for previous in self.commands
        ),
      }
      return {"data": {"id": command_id}}
    if method == "GET" and path.startswith("/runs/run-id/commands/"):
      command_id = path.rsplit("/", 1)[-1]
      command = self.command_results[command_id]
      if (
        command["commandType"] == self.fail_command_type
        and command["occurrence"] >= self.fail_command_occurrence
      ):
        return {
          "data": {
            "status": "failed",
            "error": {"errorType": "hardware", "detail": "simulated failure"},
          }
        }
      return {"data": {"status": "succeeded", "result": command["result"]}}
    raise AssertionError(f"Unexpected HTTP request: {method} {path} {data}")


class OT2Tests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.io = FakeHTTP()
    self.deck = OTDeck()
    self.robot = OT2(
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
    if self.robot._run is not None:
      await self.robot.stop()
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_setup_discovers_real_pipette_objects_and_homes(self) -> None:
    self.assertTrue(self.io.started)
    self.assertIsNotNone(self.robot.left_pipette)
    assert self.robot.left_pipette is not None
    self.assertEqual(self.robot.left_pipette.mount, "left")
    self.assertEqual(self.robot.left_pipette.name, "p20_single_gen2")
    self.assertEqual(self.robot.left_pipette.num_channels, 1)
    self.assertIsNone(self.robot.right_pipette)
    self.assertIn(("POST", "/robot/home", {"target": "robot"}), self.io.calls)
    self.assertEqual(await self.robot.list_connected_modules(), (ModuleInfo("temperature-module"),))

  async def test_full_single_channel_protocol_updates_trackers_and_commands(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
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
    self.assertEqual(command_types.count("moveToCoordinates"), 6)
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
    assert isinstance(pipette, OT2SingleChannelPipette)
    origin = self.tips.get_item("A1")

    await pipette.pick_up_tip(origin)
    await pipette.return_tip()

    self.assertTrue(origin.has_tip())
    self.assertFalse(pipette.has_tip)
    command_types = [command["commandType"] for command in self.io.commands]
    self.assertEqual(command_types.count("loadLabware"), 1)
    self.assertEqual(command_types.count("dropTip"), 1)
    self.assertEqual(command_types[-3:], ["dropTip", "savePosition", "moveToCoordinates"])

  async def test_pickup_retracts_with_tip_state_committed(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    origin = self.tips.get_item("A1")

    await pipette.pick_up_tip(origin)

    pickup, save, retract = self.io.commands[-3:]
    self.assertEqual(pickup["commandType"], "pickUpTip")
    self.assertEqual(save["commandType"], "savePosition")
    self.assertEqual(save["params"], {"pipetteId": pipette.pipette_id})
    self.assertEqual(retract["commandType"], "moveToCoordinates")
    self.assertEqual(retract["params"]["coordinates"], {"x": 30.25, "y": 40.5, "z": 120})
    self.assertTrue(retract["params"]["forceDirect"])
    self.assertTrue(pipette.has_tip)
    self.assertFalse(origin.has_tip())

  async def test_failed_retraction_preserves_completed_pickup(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    origin = self.tips.get_item("A1")
    for failed_command in ("savePosition", "moveToCoordinates"):
      with self.subTest(failed_command=failed_command):
        self.io.fail_command_type = failed_command
        tip = origin.get_tip()

        with self.assertRaisesRegex(OpentronsError, failed_command):
          await pipette.pick_up_tip(origin)

        self.assertIs(pipette.tip, tip)
        self.assertFalse(origin.has_tip())
        self.io.fail_command_type = None
        await pipette.return_tip()
        self.assertIs(origin.get_tip(), tip)

  async def test_move_to_retracts_without_lowering_an_already_high_tip(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    for z in (10, 150):
      with self.subTest(z=z):
        self.io.saved_position = {"x": 100, "y": 200, "z": z}
        command_count = len(self.io.commands)

        await asyncio.wait_for(pipette.move_to(Coordinate(100, 200, z)), timeout=1)

        commands = self.io.commands[command_count:]
        self.assertEqual(commands[0]["commandType"], "moveToCoordinates")
        self.assertEqual(commands[0]["params"]["coordinates"], self.io.saved_position)
        self.assertEqual(commands[1]["commandType"], "savePosition")
        if z < self.robot.traversal_height:
          self.assertEqual(len(commands), 3)
          self.assertEqual(commands[2]["commandType"], "moveToCoordinates")
          self.assertEqual(commands[2]["params"]["coordinates"], {"x": 100, "y": 200, "z": 120})
          self.assertTrue(commands[2]["params"]["forceDirect"])
        else:
          self.assertEqual(len(commands), 2)

  async def test_drop_tip_retracts_vertically_from_reported_position(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.tips.set_tip_state({"A12": False})
    self.robot.traversal_height = 140

    await pipette.drop_tip(self.tips.get_item("A12"))

    drop, save, retract = self.io.commands[-3:]
    self.assertEqual(drop["commandType"], "dropTip")
    self.assertEqual(drop["params"]["wellName"], "A12")
    self.assertEqual(save["commandType"], "savePosition")
    self.assertEqual(save["params"], {"pipetteId": pipette.pipette_id})
    self.assertEqual(retract["commandType"], "moveToCoordinates")
    self.assertEqual(retract["params"]["pipetteId"], pipette.pipette_id)
    self.assertEqual(retract["params"]["coordinates"], {"x": 30.25, "y": 40.5, "z": 140})
    self.assertTrue(retract["params"]["forceDirect"])
    self.assertTrue(self.tips.get_item("A12").has_tip())
    self.assertFalse(pipette.has_tip)

  async def test_discard_tip_retracts_for_both_trash_apis(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    for tip_index, version in enumerate(("6.3.0", "7.1.0")):
      with self.subTest(version=version):
        await self.robot.stop()
        self.io.api_version = version
        await self.robot.setup(skip_home=True)
        pipette = self.robot.left_pipette
        assert isinstance(pipette, OT2SingleChannelPipette)
        await pipette.pick_up_tip(self.tips.get_item(tip_index))

        await pipette.discard_tip()

        drop, save, retract = self.io.commands[-3:]
        expected_drop = "dropTip" if version == "6.3.0" else "dropTipInPlace"
        self.assertEqual(drop["commandType"], expected_drop)
        self.assertEqual(save["commandType"], "savePosition")
        self.assertEqual(retract["commandType"], "moveToCoordinates")
        self.assertEqual(retract["params"]["coordinates"], {"x": 30.25, "y": 40.5, "z": 120})
        self.assertTrue(retract["params"]["forceDirect"])
        self.assertFalse(pipette.has_tip)

  async def test_drop_does_not_lower_a_nozzle_above_traversal_height(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.io.saved_position["z"] = self.robot.traversal_height + 10
    command_count = len(self.io.commands)

    await pipette.return_tip()

    self.assertEqual(
      [command["commandType"] for command in self.io.commands[command_count:]],
      ["dropTip", "savePosition"],
    )
    self.assertFalse(pipette.has_tip)

  async def test_failed_drop_preserves_tip_and_does_not_retract(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    tip = pipette.tip
    self.io.fail_command_type = "dropTip"
    command_count = len(self.io.commands)

    with self.assertRaisesRegex(OpentronsError, "dropTip"):
      await pipette.return_tip()

    self.assertIs(pipette.tip, tip)
    self.assertFalse(self.tips.get_item("A1").has_tip())
    self.assertEqual(
      [command["commandType"] for command in self.io.commands[command_count:]], ["dropTip"]
    )

  async def test_failed_retraction_preserves_completed_tip_drop(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    for tip_index, (operation, failed_command, returned) in enumerate(
      (
        (pipette.return_tip, "savePosition", True),
        (pipette.return_tip, "moveToCoordinates", True),
        (pipette.discard_tip, "savePosition", False),
        (pipette.discard_tip, "moveToCoordinates", False),
      )
    ):
      with self.subTest(operation=operation.__name__, failed_command=failed_command):
        self.io.fail_command_type = None
        origin = self.tips.get_item(tip_index)
        await pipette.pick_up_tip(origin)
        self.io.fail_command_type = failed_command

        with self.assertRaisesRegex(OpentronsError, failed_command):
          await operation()

        self.assertFalse(pipette.has_tip)
        self.assertIsNone(pipette.tip)
        self.assertEqual(origin.has_tip(), returned)
        with self.assertRaisesRegex(RuntimeError, "origin is unknown"):
          await pipette.return_tip()

  async def test_official_tip_rack_uses_builtin_definition_for_tip_length_calibration(self) -> None:
    tips = opentrons_96_filtertiprack_20ul(name="official_tips")
    self.deck.assign_child_at_slot(tips, slot=3)
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
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
    assert isinstance(pipette, OT2SingleChannelPipette)
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.io.fail_command_type = "aspirateInPlace"

    with self.assertRaisesRegex(OpentronsError, "simulated failure"):
      await pipette.aspirate(source, volume=10)

    self.assertAlmostEqual(source.tracker.get_used_volume(), 15)
    assert pipette.tip is not None
    self.assertAlmostEqual(pipette.tip.tracker.get_used_volume(), 0)

  async def test_liquid_operations_retract_from_reported_position(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    well = self.plate.get_well("A1")
    for operation, initial_tip in ((pipette.aspirate, 0), (pipette.dispense, 10)):
      with self.subTest(operation=operation.__name__):
        well.tracker.set_volume(30)
        pipette.tip.tracker.set_volume(initial_tip)

        await operation(well, volume=10)

        save, retract = self.io.commands[-2:]
        self.assertEqual(save["commandType"], "savePosition")
        self.assertEqual(save["params"], {"pipetteId": pipette.pipette_id})
        self.assertEqual(retract["commandType"], "moveToCoordinates")
        self.assertEqual(retract["params"]["coordinates"], {"x": 30.25, "y": 40.5, "z": 120})
        self.assertTrue(retract["params"]["forceDirect"])

  async def test_rejected_aspiration_preserves_both_volume_trackers(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    pipette.tip.tracker.set_volume(15)
    source = self.plate.get_well("A1")
    source.tracker.set_volume(30)
    command_count = len(self.io.commands)

    with self.assertRaises(TooLittleVolumeError):
      await pipette.aspirate(source, volume=10)

    self.assertEqual(len(self.io.commands), command_count)
    self.assertEqual(source.tracker.get_used_volume(), 30)
    self.assertEqual(source.tracker.volume, 30)
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 15)
    self.assertEqual(pipette.tip.tracker.volume, 15)

  async def test_rejected_dispense_preserves_both_volume_trackers(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    pipette.tip.tracker.set_volume(10)
    destination = self.plate.get_well("A1")
    destination.tracker.set_volume(destination.tracker.max_volume - 5)
    initial_volume = destination.tracker.get_used_volume()
    command_count = len(self.io.commands)

    with self.assertRaises(TooLittleVolumeError):
      await pipette.dispense(destination, volume=10)

    self.assertEqual(len(self.io.commands), command_count)
    self.assertEqual(destination.tracker.get_used_volume(), initial_volume)
    self.assertEqual(destination.tracker.volume, initial_volume)
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 10)
    self.assertEqual(pipette.tip.tracker.volume, 10)

  async def test_failed_retraction_preserves_completed_transfer(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    well = self.plate.get_well("A1")
    self.io.fail_command_type = "moveToCoordinates"
    for operation, initial_tip, expected_tip, expected_well in (
      (pipette.aspirate, 0, 10, 20),
      (pipette.dispense, 10, 0, 40),
    ):
      with self.subTest(operation=operation.__name__):
        well.tracker.set_volume(30)
        pipette.tip.tracker.set_volume(initial_tip)
        move_count = sum(
          command["commandType"] == "moveToCoordinates" for command in self.io.commands
        )
        self.io.fail_command_occurrence = move_count + 2

        with self.assertRaisesRegex(OpentronsError, "moveToCoordinates"):
          await operation(well, volume=10)

        self.assertEqual(well.tracker.get_used_volume(), expected_well)
        self.assertEqual(well.tracker.volume, expected_well)
        self.assertEqual(pipette.tip.tracker.get_used_volume(), expected_tip)
        self.assertEqual(pipette.tip.tracker.volume, expected_tip)

  async def test_moved_or_unassigned_loaded_rack_is_rejected_before_a_command(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    await pipette.return_tip()
    self.deck.unassign_child_resource(self.tips)
    command_count = len(self.io.commands)

    with self.assertRaisesRegex(ValueError, "assigned directly"):
      await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.deck.assign_child_at_slot(self.tips, slot=5)
    with self.assertRaisesRegex(ValueError, "loaded in slot 1"):
      await pipette.pick_up_tip(self.tips.get_item("A1"))

    self.assertEqual(len(self.io.commands), command_count)
    self.assertTrue(self.tips.get_item("A1").has_tip())
    self.assertFalse(pipette.has_tip)

  async def test_drop_into_moved_rack_preserves_tip_state(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.deck.unassign_child_resource(self.tips)
    self.deck.assign_child_at_slot(self.tips, slot=5)
    command_count = len(self.io.commands)

    with self.assertRaisesRegex(ValueError, "loaded in slot 1"):
      await pipette.return_tip()

    self.assertEqual(len(self.io.commands), command_count)
    self.assertFalse(self.tips.get_item("A1").has_tip())
    self.assertTrue(pipette.has_tip)

  async def test_concurrent_pickups_only_pick_up_one_tip(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    results = await asyncio.wait_for(
      asyncio.gather(
        pipette.pick_up_tip(self.tips.get_item("A1")),
        pipette.pick_up_tip(self.tips.get_item("A2")),
        return_exceptions=True,
      ),
      timeout=1,
    )

    self.assertIsNone(results[0])
    self.assertIsInstance(results[1], RuntimeError)
    self.assertIn("already has a tip", str(results[1]))
    self.assertEqual(sum(command["commandType"] == "pickUpTip" for command in self.io.commands), 1)
    self.assertTrue(pipette.has_tip)
    self.assertFalse(self.tips.get_item("A1").has_tip())
    self.assertTrue(self.tips.get_item("A2").has_tip())

  async def test_concurrent_transfer_checks_state_after_preceding_operation(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    source = self.plate.get_well("A1")
    destination = self.plate.get_well("B1")
    source.tracker.set_volume(15)

    await asyncio.wait_for(
      asyncio.gather(
        pipette.aspirate(source, volume=10),
        pipette.dispense(destination, volume=10),
      ),
      timeout=1,
    )

    self.assertEqual(source.tracker.get_used_volume(), 5)
    self.assertEqual(destination.tracker.get_used_volume(), 10)
    assert pipette.tip is not None
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 0)

  async def test_concurrent_return_and_discard_only_drop_once(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    results = await asyncio.wait_for(
      asyncio.gather(pipette.return_tip(), pipette.discard_tip(), return_exceptions=True),
      timeout=1,
    )

    self.assertIsNone(results[0])
    self.assertIsInstance(results[1], RuntimeError)
    self.assertIn("does not have a tip", str(results[1]))
    self.assertFalse(pipette.has_tip)
    self.assertTrue(self.tips.get_item("A1").has_tip())
    self.assertEqual(
      sum(command["commandType"] in {"dropTip", "dropTipInPlace"} for command in self.io.commands),
      1,
    )

  async def test_mix_rejects_insufficient_liquid_or_tip_capacity_before_moving(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    source = self.plate.get_well("A1")
    command_count = len(self.io.commands)
    for source_volume, tip_volume, error in (
      (0, 0, TooLittleLiquidError),
      (30, 15, TooLittleVolumeError),
    ):
      with self.subTest(source_volume=source_volume, tip_volume=tip_volume):
        source.tracker.set_volume(source_volume)
        pipette.tip.tracker.set_volume(tip_volume)
        with self.assertRaises(error):
          await pipette.mix(source, volume=10, repetitions=3)
        self.assertEqual(len(self.io.commands), command_count)
        self.assertEqual(source.tracker.get_used_volume(), source_volume)
        self.assertEqual(pipette.tip.tracker.get_used_volume(), tip_volume)

  async def test_mix_tracks_each_transfer_when_dispense_fails(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    self.io.fail_command_type = "dispenseInPlace"
    self.io.fail_command_occurrence = 2

    with self.assertRaisesRegex(OpentronsError, "dispenseInPlace"):
      await pipette.mix(source, volume=10, repetitions=3)

    self.assertEqual(source.tracker.get_used_volume(), 5)
    self.assertEqual(source.tracker.volume, 5)
    assert pipette.tip is not None
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 10)
    self.assertEqual(pipette.tip.tracker.volume, 10)

  async def test_mix_preserves_volume_after_completed_cycles(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    pipette.tip.tracker.set_volume(5)
    command_count = len(self.io.commands)

    await pipette.mix(source, volume=10, repetitions=3)

    self.assertEqual(source.tracker.get_used_volume(), 15)
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 5)
    self.assertEqual(
      sum(command["commandType"] == "aspirateInPlace" for command in self.io.commands), 3
    )
    self.assertEqual(
      sum(command["commandType"] == "dispenseInPlace" for command in self.io.commands), 3
    )
    self.assertEqual(
      [command["commandType"] for command in self.io.commands[command_count:]],
      ["moveToCoordinates"]
      + ["aspirateInPlace", "dispenseInPlace"] * 3
      + ["savePosition", "moveToCoordinates"],
    )
    retract = self.io.commands[-1]
    self.assertEqual(retract["params"]["coordinates"], {"x": 30.25, "y": 40.5, "z": 120})
    self.assertTrue(retract["params"]["forceDirect"])

  async def test_unreachable_move_is_rejected_before_an_http_command(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    command_count = len(self.io.commands)

    with self.assertRaisesRegex(ValueError, "reachable"):
      await pipette.move_to(Coordinate(500, 0, 10))

    self.assertEqual(len(self.io.commands), command_count)

  async def test_negative_z_move_is_rejected_before_an_http_command(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    command_count = len(self.io.commands)

    with self.assertRaisesRegex(ValueError, "non-negative"):
      await pipette.move_to(Coordinate(10, 10, -1))

    self.assertEqual(len(self.io.commands), command_count)

  async def test_stop_cancels_run_and_clears_discovered_state(self) -> None:
    await self.robot.stop()
    self.assertFalse(self.io.started)
    self.assertIsNone(self.robot.left_pipette)
    self.assertIsNone(self.robot.software_version)
    self.assertIn(
      ("POST", "/runs/run-id/actions", {"data": {"actionType": "stop"}}),
      self.io.calls,
    )

  async def test_stop_falls_back_for_older_robot_software(self) -> None:
    self.io.stop_action_supported = False

    await self.robot.stop()

    self.assertIn(("POST", "/runs/run-id/cancel", None), self.io.calls)

  async def test_failed_stop_retains_state_and_transport_for_retry(self) -> None:
    pipette = self.robot.left_pipette
    run_id = self.robot._run
    self.io.stop_requests_fail = True

    with self.assertRaisesRegex(OpentronsError, "Could not cancel") as error:
      await self.robot.stop()

    self.assertIsInstance(error.exception.__cause__, RuntimeError)
    self.assertEqual(self.robot._run, run_id)
    self.assertIs(self.robot.left_pipette, pipette)
    self.assertTrue(self.io.started)
    self.io.stop_requests_fail = False
    await self.robot.stop()
    self.assertIsNone(self.robot._run)
    self.assertIsNone(self.robot.left_pipette)
    self.assertFalse(self.io.started)

  async def test_failed_setup_cleanup_retains_run_for_stop_retry(self) -> None:
    await self.robot.stop()
    self.io.fail_command_type = "loadPipette"
    self.io.stop_requests_fail = True

    with self.assertRaisesRegex(OpentronsError, "Could not cancel"):
      await self.robot.setup()

    assert self.robot._run is not None
    self.assertEqual(self.robot._run.id, "run-id")
    self.assertTrue(self.io.started)
    self.io.stop_requests_fail = False
    await self.robot.stop()
    self.assertIsNone(self.robot._run)
    self.assertFalse(self.io.started)


class OT2MultiChannelTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    """Set up a multi and a single on the same robot using only fake HTTP."""
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.io = FakeHTTP(left_pipette_name="p20_multi_gen2", right_pipette_name="p20_single_gen2")
    self.deck = OTDeck()
    self.robot = OT2(host="ot2.local", deck=self.deck, command_poll_interval=0, io=self.io)
    await self.robot.setup(skip_home=True)
    assert isinstance(self.robot.left_pipette, OT2_8ChannelPipette)
    self.pipette = self.robot.left_pipette
    self.tips = opentrons_96_filtertiprack_20ul(name="tips")
    self.tips.model = None
    self.deck.assign_child_at_slot(self.tips, slot=1)
    self.plate = celltreat_96_wellplate_350uL_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=2)
    self.column = self.tips["A1:H1"]
    self.sources = self.plate["A1:H1"]
    self.destinations = self.plate["A2:H2"]
    for well in self.sources:
      well.tracker.set_volume(15)

  async def asyncTearDown(self) -> None:
    """Close the fake run and restore global tracking switches."""
    await self.robot.stop()
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_setup_selects_the_concrete_class_on_either_mount(self) -> None:
    """Discovery selects a class from the pipette model, independently of its mount."""
    self.assertIsInstance(self.pipette, _OT2Pipette)
    for model in ("p10_multi", "p20_multi_gen2", "p50_multi", "p300_multi", "p300_multi_gen2"):
      with self.subTest(model=model):
        await self.robot.stop()
        self.io.left_pipette_name = "p20_single_gen2"
        self.io.right_pipette_name = model
        await self.robot.setup(skip_home=True)
        self.assertIsInstance(self.robot.left_pipette, OT2SingleChannelPipette)
        self.assertIsInstance(self.robot.right_pipette, OT2_8ChannelPipette)
        self.assertEqual([p.num_channels for p in self.robot.pipettes], [1, 8])

  def test_constructors_reject_models_with_the_wrong_channel_count(self) -> None:
    """A concrete class cannot bind a model whose nozzle count contradicts its API."""
    with self.assertRaisesRegex(ValueError, "8-channel"):
      OT2SingleChannelPipette(self.robot, "left", "p20_multi_gen2", "left-pipette-id")
    with self.assertRaisesRegex(ValueError, "1-channel"):
      OT2_8ChannelPipette(self.robot, "right", "p20_single_gen2", "right-pipette-id")

  async def test_full_column_uses_one_command_and_tracks_each_nozzle(self) -> None:
    """One physical stroke transfers the requested volume separately in eight wells."""
    original_tips = [spot.get_tip() for spot in self.column]
    await self.pipette.pick_up_tips(self.column)
    self.assertEqual(self.pipette.num_channels, 8)
    self.assertEqual(len(self.pipette.tips), 8)
    for actual, original in zip(self.pipette.tips, original_tips):
      self.assertIs(actual, original)
    await self.pipette.aspirate(self.sources, volume=10)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [10] * 8)
    await self.pipette.dispense(self.destinations, volume=10, flow_rate=6)
    await self.pipette.return_tips()

    for spot, original in zip(self.column, original_tips):
      self.assertIs(spot.get_tip(), original)
      self.assertEqual(original.tracker.volume, 0)
    self.assertFalse(self.pipette.has_tip)
    self.assertEqual(self.pipette.tips, ())
    self.assertEqual([well.tracker.volume for well in self.sources], [5] * 8)
    self.assertEqual([well.tracker.volume for well in self.destinations], [10] * 8)
    commands = self.io.commands
    for kind in ("pickUpTip", "aspirateInPlace", "dispenseInPlace", "dropTip"):
      selected = [command for command in commands if command["commandType"] == kind]
      self.assertEqual(len(selected), 1)
      params = selected[0]["params"]
      self.assertEqual(params["pipetteId"], "left-pipette-id")
      if kind in ("pickUpTip", "dropTip"):
        self.assertEqual(params["wellName"], "A1")
      else:
        self.assertEqual(params["volume"], 10)
        self.assertEqual(params["flowRate"], 7.6 if kind == "aspirateInPlace" else 6)
    # Two liquid moves plus four vertical retractions; the liquid moves anchor at row A.
    moves = [c["params"] for c in commands if c["commandType"] == "moveToCoordinates"]
    self.assertEqual(len(moves), 6)
    anchor = self.sources[0].get_location_wrt(self.deck, "c", "c", "cavity_bottom")
    anchor -= self.deck.slot_locations[0]
    self.assertEqual(moves[1]["coordinates"], {"x": anchor.x, "y": anchor.y, "z": anchor.z})

  async def test_single_mount_and_multi_mount_keep_independent_tips(self) -> None:
    """A multi on one mount does not change the other mount's single-channel API."""
    single = self.robot.right_pipette
    assert isinstance(single, OT2SingleChannelPipette)
    self.assertEqual(single.num_channels, 1)
    await asyncio.gather(
      self.pipette.pick_up_tips(self.column), single.pick_up_tip(self.tips.get_item("A3"))
    )
    await self.pipette.discard_tips()
    self.assertTrue(single.has_tip)
    self.assertIsNotNone(single.tip)
    await single.return_tip()
    self.assertTrue(self.tips.get_item("A3").has_tip())
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    pickups = [c["params"] for c in self.io.commands if c["commandType"] == "pickUpTip"]
    self.assertEqual([p["pipetteId"] for p in pickups], ["left-pipette-id", "right-pipette-id"])

  async def test_incomplete_or_misaligned_pickups_send_nothing(self) -> None:
    """Reject undeclared extra tips, repeated, mixed-column, and reversed targets before I/O."""
    before = len(self.io.calls)
    for spots in (
      [],
      self.column[:7],
      [self.column[0]] * 8,
      list(reversed(self.column)),
      self.column[:7] + [self.tips.get_item("H2")],
    ):
      with self.subTest(spots=spots), self.assertRaises(ValueError):
        await self.pipette.pick_up_tips(spots)
    self.assertEqual(len(self.io.calls), before)
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_missing_last_tip_and_failed_pickup_preserve_the_rack(self) -> None:
    """A missing eighth tip or failed command must not consume the first seven tips."""
    last_tip = self.column[-1].get_tip()
    self.column[-1].tracker.remove_tip(commit=True)
    before = len(self.io.calls)
    with self.assertRaises(NoTipError):
      await self.pipette.pick_up_tips(self.column)
    self.assertEqual(len(self.io.calls), before)
    self.assertTrue(all(spot.has_tip() for spot in self.column[:-1]))
    self.column[-1].tracker.add_tip(last_tip)
    self.io.fail_command_type = "pickUpTip"
    with self.assertRaises(OpentronsError):
      await self.pipette.pick_up_tips(self.column)
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    self.assertFalse(self.pipette.has_tip)

  async def test_drop_preflight_and_command_failure_roll_back_every_spot(self) -> None:
    """A late occupied destination or command failure preserves all mounted tips."""
    await self.pipette.pick_up_tips(self.column)
    mounted = self.pipette.tips
    destinations = self.tips["A2:H2"]
    for spot in destinations[:-1]:
      spot.tracker.remove_tip(commit=True)
    before = len(self.io.calls)
    with self.assertRaises(HasTipError):
      await self.pipette.drop_tips(destinations)
    self.assertEqual(len(self.io.calls), before)
    self.assertTrue(all(not spot.has_tip() for spot in destinations[:-1]))
    self.io.fail_command_type = "dropTip"
    with self.assertRaises(OpentronsError):
      await self.pipette.return_tips()
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    self.assertIs(self.pipette.tips, mounted)
    self.io.fail_command_type = None
    destinations[-1].tracker.remove_tip(commit=True)
    await self.pipette.drop_tips(destinations)
    for spot, tip in zip(destinations, mounted):
      self.assertIs(spot.get_tip(), tip)

  async def test_last_well_or_tip_capacity_failure_rolls_back_the_whole_transfer(self) -> None:
    """Every nozzle is validated before motion, with earlier staged updates undone."""
    await self.pipette.pick_up_tips(self.column)
    self.sources[-1].tracker.set_volume(0)
    before = len(self.io.calls)
    with self.assertRaises(TooLittleLiquidError):
      await self.pipette.aspirate(self.sources, volume=10)
    self.assertEqual([w.tracker.get_used_volume() for w in self.sources], [15] * 7 + [0])
    self.assertEqual([tip.tracker.get_used_volume() for tip in self.pipette.tips], [0] * 8)
    self.sources[-1].tracker.set_volume(15)
    self.pipette.tips[-1].tracker.set_volume(20)
    with self.assertRaises(TooLittleVolumeError):
      await self.pipette.aspirate(self.sources, volume=10)
    self.assertEqual([w.tracker.get_used_volume() for w in self.sources], [15] * 8)
    self.assertEqual([tip.tracker.get_used_volume() for tip in self.pipette.tips], [0] * 7 + [20])
    self.assertEqual(len(self.io.calls), before)

  async def test_liquid_command_failures_preserve_all_volumes(self) -> None:
    """A rejected aspiration or dispense rolls back the entire column."""
    await self.pipette.pick_up_tips(self.column)
    for operation, kind, tip_volume in (
      (self.pipette.aspirate, "aspirateInPlace", 0),
      (self.pipette.dispense, "dispenseInPlace", 10),
    ):
      with self.subTest(kind=kind):
        for tip in self.pipette.tips:
          tip.tracker.set_volume(tip_volume)
        self.io.fail_command_type = kind
        with self.assertRaises(OpentronsError):
          await operation(self.sources, volume=10)
        self.assertEqual([w.tracker.get_used_volume() for w in self.sources], [15] * 8)
        self.assertEqual(
          [tip.tracker.get_used_volume() for tip in self.pipette.tips], [tip_volume] * 8
        )

  async def test_liquid_targets_must_match_the_rigid_head(self) -> None:
    """A partial column or row cannot silently command an eight-nozzle stroke."""
    await self.pipette.pick_up_tips(self.column)
    before = len(self.io.calls)
    for targets in (self.sources[:7], self.plate["A1:A8"]):
      with self.subTest(targets=targets), self.assertRaises(ValueError):
        await self.pipette.aspirate(targets, volume=10)
    self.assertEqual(len(self.io.calls), before)

  async def test_mix_commits_each_completed_stroke(self) -> None:
    """A later failed mix stroke retains the liquid moved by earlier completed strokes."""
    await self.pipette.pick_up_tips(self.column)
    await self.pipette.mix(self.sources, volume=5, repetitions=2)
    self.assertEqual([w.tracker.volume for w in self.sources], [15] * 8)
    self.io.fail_command_type = "dispenseInPlace"
    with self.assertRaises(OpentronsError):
      await self.pipette.mix(self.sources, volume=5, repetitions=1)
    self.assertEqual([w.tracker.volume for w in self.sources], [10] * 8)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [5] * 8)

  async def test_retraction_failure_keeps_completed_tip_and_liquid_state(self) -> None:
    """A retraction failure must not undo the successful operation preceding it."""
    self.io.fail_command_type = "savePosition"
    with self.assertRaises(OpentronsError):
      await self.pipette.pick_up_tips(self.column)
    self.assertEqual(len(self.pipette.tips), 8)
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    with self.assertRaises(OpentronsError):
      await self.pipette.aspirate(self.sources, volume=10)
    self.assertEqual([w.tracker.volume for w in self.sources], [5] * 8)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [10] * 8)
    with self.assertRaises(OpentronsError):
      await self.pipette.return_tips(allow_nonzero_volume=True)
    self.assertFalse(self.pipette.has_tip)
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_discard_checks_every_tip_and_retains_tips_on_failure(self) -> None:
    """Liquid in the last tip blocks disposal; a failed drop keeps tip ownership."""
    await self.pipette.pick_up_tips(self.column)
    self.pipette.tips[-1].tracker.set_volume(1)
    before = len(self.io.calls)
    with self.assertRaisesRegex(ValueError, "contains liquid"):
      await self.pipette.discard_tips()
    self.assertEqual(len(self.io.calls), before)
    self.io.fail_command_type = "dropTipInPlace"
    with self.assertRaises(OpentronsError):
      await self.pipette.discard_tips(allow_nonzero_volume=True)
    self.assertEqual(len(self.pipette.tips), 8)
    self.io.fail_command_type = None
    await self.pipette.discard_tips(allow_nonzero_volume=True)
    self.assertFalse(self.pipette.has_tip)

  async def test_disabled_tracking_still_owns_and_operates_all_tips(self) -> None:
    """Global tracking switches do not change the physical command grouping."""
    set_tip_tracking(False)
    set_volume_tracking(False)
    await self.pipette.pick_up_tips(self.column)
    await self.pipette.aspirate(self.sources, volume=10)
    await self.pipette.dispense(self.destinations, volume=10)
    self.assertEqual(len(self.pipette.tips), 8)
    self.assertTrue(all(spot.has_tip() for spot in self.column))
    self.assertEqual([w.tracker.volume for w in self.sources], [15] * 8)
    self.assertEqual([w.tracker.volume for w in self.destinations], [0] * 8)
    await self.pipette.discard_tips()


class OT2ArchitectureTests(unittest.IsolatedAsyncioTestCase):
  async def test_connection_allows_queries_without_creating_a_run(self) -> None:
    io = FakeHTTP()
    robot = OT2("ot2.local", io=io)
    await robot.connect()
    try:
      self.assertEqual((await robot.get_health()).software_version, "7.1.0")
      self.assertEqual((await robot.get_mounted_pipettes())[0].name, "p20_single_gen2")
      self.assertEqual((await robot.get_runs())[0].id, "run-id")
      self.assertEqual(await robot.list_connected_modules(), (ModuleInfo("temperature-module"),))
      self.assertIsNone(robot._run)
      self.assertIsNone(robot.software_version)
      self.assertEqual(robot.pipettes, [])
    finally:
      await robot.stop()
    self.assertTrue(all(method == "GET" for method, _, _ in io.calls))
    self.assertFalse(io.started)

  async def test_queries_do_not_reconfigure_an_active_session(self) -> None:
    io = FakeHTTP()
    robot = OT2("ot2.local", io=io)
    await robot.setup(skip_home=True)
    try:
      state = vars(robot).copy()
      io.api_version = "8.7.0"
      io.left_pipette_name = "p300_single_gen2"
      command_count = len(io.commands)
      self.assertEqual((await robot.get_health()).software_version, "8.7.0")
      self.assertEqual((await robot.get_mounted_pipettes())[0].name, "p300_single_gen2")
      self.assertEqual(robot.software_version, "7.1.0")
      self.assertEqual(vars(robot), state)
      self.assertEqual(len(io.commands), command_count)
    finally:
      await robot.stop()

  async def test_reconnect_invalidates_old_pipette_and_labware_bindings(self) -> None:
    io = FakeHTTP()
    robot = OT2("ot2.local", io=io)
    await robot.setup(skip_home=True)
    old_pipette, old_registry = robot.left_pipette, robot._labware
    assert old_pipette is not None
    await robot.stop()
    await robot.setup(skip_home=True)
    try:
      self.assertIsNot(robot._labware, old_registry)
      command_count = len(io.commands)
      with self.assertRaisesRegex(RuntimeError, "earlier OT-2 run"):
        await old_pipette.move_to(Coordinate(100, 100, 120))
      self.assertEqual(len(io.commands), command_count)
      assert robot.left_pipette is not None
      await robot.left_pipette.move_to(Coordinate(100, 100, 120))
    finally:
      await robot.stop()

  async def test_two_robots_own_independent_sessions_and_connections(self) -> None:
    left_io = FakeHTTP(api_version="6.3.0")
    right_io = FakeHTTP(api_version="8.7.0")
    left = OT2("first.local", io=left_io)
    right = OT2("second.local", io=right_io)
    await asyncio.gather(left.setup(skip_home=True), right.setup(skip_home=True))
    try:
      self.assertIsNot(left._run, right._run)
      self.assertIsNot(left._labware, right._labware)
      self.assertEqual((left.software_version, right.software_version), ("6.3.0", "8.7.0"))
      await left.stop()
      self.assertTrue(right_io.started)
      assert right.left_pipette is not None
      await right.left_pipette.move_to(Coordinate(100, 100, 120))
      self.assertEqual([c["commandType"] for c in left_io.commands], ["loadPipette"])
    finally:
      await asyncio.gather(left.stop(), right.stop())

  async def test_unsupported_pipette_does_not_create_a_run_or_home(self) -> None:
    io = FakeHTTP(left_pipette_name="unsupported")
    robot = OT2("ot2.local", io=io)
    with self.assertRaisesRegex(ValueError, "Unsupported OT-2 pipette"):
      await robot.setup()
    self.assertIsNone(robot._run)
    self.assertFalse(io.started)
    self.assertTrue(all(method == "GET" for method, _, _ in io.calls))

  async def test_stop_waits_for_the_complete_pipette_operation(self) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class PausingHTTP(FakeHTTP):
      async def request(
        self, method: str, path: str, data: Optional[Dict[str, Any]] = None
      ) -> Dict[str, Any]:
        if data is not None and data.get("data", {}).get("commandType") == "moveToCoordinates":
          entered.set()
          await release.wait()
        return await super().request(method, path, data)

    io = PausingHTTP()
    robot = OT2("ot2.local", io=io)
    await robot.setup(skip_home=True)
    assert robot.left_pipette is not None
    movement = asyncio.create_task(robot.left_pipette.move_to(Coordinate(100, 100, 100)))
    await asyncio.wait_for(entered.wait(), timeout=1)
    stop = asyncio.create_task(robot.stop())
    try:
      await asyncio.sleep(0)
      self.assertFalse(stop.done())
      self.assertTrue(io.started)
    finally:
      release.set()
      await asyncio.wait_for(asyncio.gather(movement, stop), timeout=1)
    self.assertFalse(io.started)
    self.assertEqual(
      [command["commandType"] for command in io.commands[-3:]],
      ["moveToCoordinates", "savePosition", "moveToCoordinates"],
    )


if __name__ == "__main__":
  unittest.main()
