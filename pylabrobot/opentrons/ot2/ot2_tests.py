import asyncio
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, Mock

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

FAILED_COMMAND = {
  "data": {
    "status": "failed",
    "error": {"errorType": "hardware", "detail": "simulated failure"},
  }
}


def _mock_http(
  left_pipette_name: Optional[str] = "p20_single_gen2",
  right_pipette_name: Optional[str] = None,
  api_version: str = "7.1.0",
) -> Tuple[AsyncMock, Dict[Tuple[str, str], Mock], Dict[str, Mock]]:
  """Configure HTTP response mocks for successful OT-2 operations."""
  io = AsyncMock(spec_set=HTTP)
  responses = {
    ("POST", "/runs"): Mock(return_value={"data": {"id": "run-id"}}),
    ("GET", "/runs"): Mock(return_value={"data": [{"id": "run-id", "status": "idle"}]}),
    ("GET", "/runs/run-id"): Mock(return_value={"data": {"id": "run-id", "status": "stopped"}}),
    ("GET", "/pipettes"): Mock(
      return_value={
        "left": {"name": left_pipette_name},
        "right": {"name": right_pipette_name},
      }
    ),
    ("GET", "/health"): Mock(
      return_value={
        "name": "test-ot2",
        "robot_model": "OT-2 Standard",
        "api_version": api_version,
      }
    ),
    ("POST", "/robot/home"): Mock(return_value={"data": {}}),
    ("GET", "/modules"): Mock(return_value={"data": [{"id": "temperature-module"}]}),
    ("POST", "/runs/run-id/actions"): Mock(return_value={"data": {}}),
    ("POST", "/runs/run-id/cancel"): Mock(return_value={"data": {}}),
    ("POST", "/runs/run-id/labware_definitions"): Mock(
      return_value={"data": {"definitionUri": "pylabrobot/test-tip-rack/1"}}
    ),
  }
  command_results = {
    kind: Mock(return_value={"data": {"status": "succeeded", "result": {}}})
    for kind in (
      "loadLabware",
      "pickUpTip",
      "dropTip",
      "moveToCoordinates",
      "aspirateInPlace",
      "dispenseInPlace",
      "moveToAddressableAreaForDropTip",
      "dropTipInPlace",
    )
  }
  for mount in ("left", "right"):
    command_results[f"loadPipette-{mount}"] = Mock(
      return_value={"data": {"status": "succeeded", "result": {"pipetteId": f"{mount}-pipette-id"}}}
    )
  command_results["savePosition"] = Mock(
    return_value={
      "data": {
        "status": "succeeded",
        "result": {
          "positionId": "position-id",
          "position": {"x": 30.25, "y": 40.5, "z": 20.0},
        },
      }
    }
  )

  async def request(
    method: str, path: str, data: Optional[Dict[str, Any]] = None
  ) -> Dict[str, Any]:
    """Dispatch canned responses, yielding so concurrent operations can interleave."""
    await asyncio.sleep(0)
    if method == "POST" and path == "/runs/run-id/commands":
      assert data is not None
      command = data["data"]
      command_id = command["commandType"]
      if command_id == "loadPipette":
        command_id += f"-{command['params']['mount']}"
      return {"data": {"id": command_id}}
    if method == "GET" and path.startswith("/runs/run-id/commands/"):
      return command_results[path.rsplit("/", 1)[-1]]()
    return responses[method, path]()

  io.request.side_effect = request
  return io, responses, command_results


def _submitted_commands(io: AsyncMock) -> List[Dict[str, Any]]:
  """Read submitted command payloads from the HTTP mock's await history."""
  return [
    request.args[2]["data"]
    for request in io.request.await_args_list
    if request.args[:2] == ("POST", "/runs/run-id/commands")
  ]


class OT2Tests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.io, self.responses, self.command_results = _mock_http()
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
    self.io.setup.assert_awaited_once()
    self.io.stop.assert_not_awaited()
    self.assertIsNotNone(self.robot.left_pipette)
    assert self.robot.left_pipette is not None
    self.assertEqual(self.robot.left_pipette.mount, "left")
    self.assertEqual(self.robot.left_pipette.name, "p20_single_gen2")
    self.assertEqual(self.robot.left_pipette.num_channels, 1)
    self.assertIsNone(self.robot.right_pipette)
    self.io.request.assert_any_await("POST", "/robot/home", {"target": "robot"})
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

    command_types = [command["commandType"] for command in _submitted_commands(self.io)]
    self.assertEqual(command_types.count("loadLabware"), 1)
    self.assertEqual(command_types.count("pickUpTip"), 1)
    self.assertEqual(command_types.count("aspirateInPlace"), 1)
    self.assertEqual(command_types.count("dispenseInPlace"), 1)
    self.assertEqual(command_types.count("moveToCoordinates"), 6)
    self.assertEqual(command_types.count("moveToAddressableAreaForDropTip"), 1)
    self.assertEqual(command_types.count("dropTipInPlace"), 1)

    definition_request = next(
      request.args[2]
      for request in self.io.request.await_args_list
      if request.args[:2] == ("POST", "/runs/run-id/labware_definitions")
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
      command for command in _submitted_commands(self.io) if command["commandType"] == "loadLabware"
    )
    self.assertIsInstance(load_labware["params"]["version"], int)

    pick_up_tip = next(
      command for command in _submitted_commands(self.io) if command["commandType"] == "pickUpTip"
    )
    self.assertEqual(pick_up_tip["params"]["wellName"], "A1")

    move_to_trash = next(
      command
      for command in _submitted_commands(self.io)
      if command["commandType"] == "moveToAddressableAreaForDropTip"
    )
    self.assertEqual(move_to_trash["params"]["offset"], {"x": 0, "y": 0, "z": 10})
    self.assertNotIn("wellLocation", move_to_trash["params"])

    aspirate = next(
      command
      for command in _submitted_commands(self.io)
      if command["commandType"] == "aspirateInPlace"
    )
    dispense = next(
      command
      for command in _submitted_commands(self.io)
      if command["commandType"] == "dispenseInPlace"
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
    command_types = [command["commandType"] for command in _submitted_commands(self.io)]
    self.assertEqual(command_types.count("loadLabware"), 1)
    self.assertEqual(command_types.count("dropTip"), 1)
    self.assertEqual(command_types[-3:], ["dropTip", "savePosition", "moveToCoordinates"])

  async def test_pickup_retracts_with_tip_state_committed(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    origin = self.tips.get_item("A1")

    await pipette.pick_up_tip(origin)

    pickup, save, retract = _submitted_commands(self.io)[-3:]
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
        self.command_results[failed_command].side_effect = lambda: FAILED_COMMAND
        tip = origin.get_tip()

        with self.assertRaisesRegex(OpentronsError, failed_command):
          await pipette.pick_up_tip(origin)

        self.assertIs(pipette.tip, tip)
        self.assertFalse(origin.has_tip())
        self.command_results[failed_command].side_effect = None
        await pipette.return_tip()
        self.assertIs(origin.get_tip(), tip)

  async def test_move_to_retracts_without_lowering_an_already_high_tip(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    for z in (10, 150):
      with self.subTest(z=z):
        self.command_results["savePosition"].return_value["data"]["result"]["position"] = {
          "x": 100,
          "y": 200,
          "z": z,
        }
        command_count = len(_submitted_commands(self.io))

        await asyncio.wait_for(pipette.move_to(Coordinate(100, 200, z)), timeout=1)

        commands = _submitted_commands(self.io)[command_count:]
        self.assertEqual(commands[0]["commandType"], "moveToCoordinates")
        self.assertEqual(
          commands[0]["params"]["coordinates"],
          self.command_results["savePosition"].return_value["data"]["result"]["position"],
        )
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

    drop, save, retract = _submitted_commands(self.io)[-3:]
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
        self.responses["GET", "/health"].return_value["api_version"] = version
        await self.robot.setup(skip_home=True)
        pipette = self.robot.left_pipette
        assert isinstance(pipette, OT2SingleChannelPipette)
        await pipette.pick_up_tip(self.tips.get_item(tip_index))

        await pipette.discard_tip()

        drop, save, retract = _submitted_commands(self.io)[-3:]
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
    self.command_results["savePosition"].return_value["data"]["result"]["position"]["z"] = (
      self.robot.traversal_height + 10
    )
    command_count = len(_submitted_commands(self.io))

    await pipette.return_tip()

    self.assertEqual(
      [command["commandType"] for command in _submitted_commands(self.io)[command_count:]],
      ["dropTip", "savePosition"],
    )
    self.assertFalse(pipette.has_tip)

  async def test_failed_drop_preserves_tip_and_does_not_retract(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    tip = pipette.tip
    self.command_results["dropTip"].side_effect = lambda: FAILED_COMMAND
    command_count = len(_submitted_commands(self.io))

    with self.assertRaisesRegex(OpentronsError, "dropTip"):
      await pipette.return_tip()

    self.assertIs(pipette.tip, tip)
    self.assertFalse(self.tips.get_item("A1").has_tip())
    self.assertEqual(
      [command["commandType"] for command in _submitted_commands(self.io)[command_count:]],
      ["dropTip"],
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
        self.command_results["savePosition"].side_effect = None
        self.command_results["moveToCoordinates"].side_effect = None
        origin = self.tips.get_item(tip_index)
        await pipette.pick_up_tip(origin)
        self.command_results[failed_command].side_effect = lambda: FAILED_COMMAND

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
    await pipette.pick_up_tip(tips.get_item("A1"))

    self.responses["POST", "/runs/run-id/labware_definitions"].assert_not_called()
    load_labware = next(
      command
      for command in reversed(_submitted_commands(self.io))
      if command["commandType"] == "loadLabware"
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
    self.command_results["aspirateInPlace"].side_effect = lambda: FAILED_COMMAND

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

        save, retract = _submitted_commands(self.io)[-2:]
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
    command_count = len(_submitted_commands(self.io))

    with self.assertRaises(TooLittleVolumeError):
      await pipette.aspirate(source, volume=10)

    self.assertEqual(len(_submitted_commands(self.io)), command_count)
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
    command_count = len(_submitted_commands(self.io))

    with self.assertRaises(TooLittleVolumeError):
      await pipette.dispense(destination, volume=10)

    self.assertEqual(len(_submitted_commands(self.io)), command_count)
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
    for operation, initial_tip, expected_tip, expected_well in (
      (pipette.aspirate, 0, 10, 20),
      (pipette.dispense, 10, 0, 40),
    ):
      with self.subTest(operation=operation.__name__):
        well.tracker.set_volume(30)
        pipette.tip.tracker.set_volume(initial_tip)
        self.command_results["moveToCoordinates"].side_effect = [
          self.command_results["moveToCoordinates"].return_value,
          FAILED_COMMAND,
        ]

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
    command_count = len(_submitted_commands(self.io))

    with self.assertRaisesRegex(ValueError, "assigned directly"):
      await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.deck.assign_child_at_slot(self.tips, slot=5)
    with self.assertRaisesRegex(ValueError, "loaded in slot 1"):
      await pipette.pick_up_tip(self.tips.get_item("A1"))

    self.assertEqual(len(_submitted_commands(self.io)), command_count)
    self.assertTrue(self.tips.get_item("A1").has_tip())
    self.assertFalse(pipette.has_tip)

  async def test_drop_into_moved_rack_preserves_tip_state(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    self.deck.unassign_child_resource(self.tips)
    self.deck.assign_child_at_slot(self.tips, slot=5)
    command_count = len(_submitted_commands(self.io))

    with self.assertRaisesRegex(ValueError, "loaded in slot 1"):
      await pipette.return_tip()

    self.assertEqual(len(_submitted_commands(self.io)), command_count)
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
    self.assertEqual(
      sum(command["commandType"] == "pickUpTip" for command in _submitted_commands(self.io)), 1
    )
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
      sum(
        command["commandType"] in {"dropTip", "dropTipInPlace"}
        for command in _submitted_commands(self.io)
      ),
      1,
    )

  async def test_mix_rejects_insufficient_liquid_or_tip_capacity_before_moving(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    source = self.plate.get_well("A1")
    command_count = len(_submitted_commands(self.io))
    for source_volume, tip_volume, error in (
      (0, 0, TooLittleLiquidError),
      (30, 15, TooLittleVolumeError),
    ):
      with self.subTest(source_volume=source_volume, tip_volume=tip_volume):
        source.tracker.set_volume(source_volume)
        pipette.tip.tracker.set_volume(tip_volume)
        with self.assertRaises(error):
          await pipette.mix(source, volume=10, repetitions=3)
        self.assertEqual(len(_submitted_commands(self.io)), command_count)
        self.assertEqual(source.tracker.get_used_volume(), source_volume)
        self.assertEqual(pipette.tip.tracker.get_used_volume(), tip_volume)

  async def test_mix_tracks_each_transfer_when_dispense_fails(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    self.command_results["dispenseInPlace"].side_effect = [
      self.command_results["dispenseInPlace"].return_value,
      FAILED_COMMAND,
    ]

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
    command_count = len(_submitted_commands(self.io))

    await pipette.mix(source, volume=10, repetitions=3)

    self.assertEqual(source.tracker.get_used_volume(), 15)
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 5)
    self.assertEqual(
      sum(command["commandType"] == "aspirateInPlace" for command in _submitted_commands(self.io)),
      3,
    )
    self.assertEqual(
      sum(command["commandType"] == "dispenseInPlace" for command in _submitted_commands(self.io)),
      3,
    )
    self.assertEqual(
      [command["commandType"] for command in _submitted_commands(self.io)[command_count:]],
      ["moveToCoordinates"]
      + ["aspirateInPlace", "dispenseInPlace"] * 3
      + ["savePosition", "moveToCoordinates"],
    )
    retract = _submitted_commands(self.io)[-1]
    self.assertEqual(retract["params"]["coordinates"], {"x": 30.25, "y": 40.5, "z": 120})
    self.assertTrue(retract["params"]["forceDirect"])

  async def test_unreachable_move_is_rejected_before_an_http_command(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    command_count = len(_submitted_commands(self.io))

    with self.assertRaisesRegex(ValueError, "reachable"):
      await pipette.move_to(Coordinate(500, 0, 10))

    self.assertEqual(len(_submitted_commands(self.io)), command_count)

  async def test_negative_z_move_is_rejected_before_an_http_command(self) -> None:
    pipette = self.robot.left_pipette
    assert isinstance(pipette, OT2SingleChannelPipette)
    command_count = len(_submitted_commands(self.io))

    with self.assertRaisesRegex(ValueError, "non-negative"):
      await pipette.move_to(Coordinate(10, 10, -1))

    self.assertEqual(len(_submitted_commands(self.io)), command_count)

  async def test_stop_cancels_run_and_clears_discovered_state(self) -> None:
    await self.robot.stop()
    self.io.stop.assert_awaited_once()
    self.assertIsNone(self.robot.left_pipette)
    self.assertIsNone(self.robot.software_version)
    self.io.request.assert_any_await(
      "POST", "/runs/run-id/actions", {"data": {"actionType": "stop"}}
    )

  async def test_stop_falls_back_for_older_robot_software(self) -> None:
    self.responses["POST", "/runs/run-id/actions"].side_effect = HTTPError(
      "POST", "/runs/run-id/actions", 404, "stop action is unsupported"
    )

    await self.robot.stop()

    self.io.request.assert_any_await("POST", "/runs/run-id/cancel", None)

  async def test_failed_stop_retains_state_and_transport_for_retry(self) -> None:
    pipette = self.robot.left_pipette
    run_id = self.robot._run
    self.responses["POST", "/runs/run-id/actions"].side_effect = RuntimeError(
      "stop request rejected"
    )

    with self.assertRaisesRegex(OpentronsError, "Could not cancel") as error:
      await self.robot.stop()

    self.assertIsInstance(error.exception.__cause__, RuntimeError)
    self.assertEqual(self.robot._run, run_id)
    self.assertIs(self.robot.left_pipette, pipette)
    self.io.setup.assert_awaited_once()
    self.io.stop.assert_not_awaited()
    self.responses["POST", "/runs/run-id/actions"].side_effect = None
    await self.robot.stop()
    self.assertIsNone(self.robot._run)
    self.assertIsNone(self.robot.left_pipette)
    self.io.stop.assert_awaited_once()

  async def test_failed_setup_cleanup_retains_run_for_stop_retry(self) -> None:
    await self.robot.stop()
    self.io.reset_mock()
    self.command_results["loadPipette-left"].return_value = FAILED_COMMAND
    self.responses["POST", "/runs/run-id/actions"].side_effect = RuntimeError(
      "stop request rejected"
    )

    with self.assertRaisesRegex(OpentronsError, "Could not cancel"):
      await self.robot.setup()

    assert self.robot._run is not None
    self.assertEqual(self.robot._run.id, "run-id")
    self.io.setup.assert_awaited_once()
    self.io.stop.assert_not_awaited()
    self.responses["POST", "/runs/run-id/actions"].side_effect = None
    await self.robot.stop()
    self.assertIsNone(self.robot._run)
    self.io.stop.assert_awaited_once()


class OT2MultiChannelTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    """Set up a multi and a single on the same robot using mocked HTTP."""
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.io, self.responses, self.command_results = _mock_http(
      left_pipette_name="p20_multi_gen2", right_pipette_name="p20_single_gen2"
    )
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
    """Close the mocked run and restore global tracking switches."""
    await self.robot.stop()
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_pickup_rejects_missing_or_mixed_models_before_commands(self) -> None:
    """A tip type is its model, and every nozzle must have a defined matching model."""
    last_tip = self.column[-1].get_tip()
    for model in (None, "different_tip_model"):
      with self.subTest(model=model):
        last_tip.model = model
        before = self.io.request.await_count
        with self.assertRaises(ValueError):
          await self.pipette.pick_up_tips(self.column)
        self.assertEqual(self.io.request.await_count, before)
        self.assertTrue(all(spot.has_tip() for spot in self.column))
        self.assertFalse(self.pipette.has_tip)

  async def test_setup_selects_the_concrete_class_on_either_mount(self) -> None:
    """Discovery selects a class from the pipette model, independently of its mount."""
    self.assertIsInstance(self.pipette, _OT2Pipette)
    for model in ("p10_multi", "p20_multi_gen2", "p50_multi", "p300_multi", "p300_multi_gen2"):
      with self.subTest(model=model):
        await self.robot.stop()
        self.responses["GET", "/pipettes"].return_value["left"]["name"] = "p20_single_gen2"
        self.responses["GET", "/pipettes"].return_value["right"]["name"] = model
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
    commands = _submitted_commands(self.io)
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
    pickups = [c["params"] for c in _submitted_commands(self.io) if c["commandType"] == "pickUpTip"]
    self.assertEqual([p["pipetteId"] for p in pickups], ["left-pipette-id", "right-pipette-id"])

  async def test_incomplete_or_misaligned_pickups_send_nothing(self) -> None:
    """Reject undeclared extra tips, repeated, mixed-column, and reversed targets before I/O."""
    before = self.io.request.await_count
    for spots in (
      [],
      self.column[:7],
      [self.column[0]] * 8,
      list(reversed(self.column)),
      self.column[:7] + [self.tips.get_item("H2")],
    ):
      with self.subTest(spots=spots), self.assertRaises(ValueError):
        await self.pipette.pick_up_tips(spots)
    self.assertEqual(self.io.request.await_count, before)
    self.assertTrue(all(spot.has_tip() for spot in self.column))

  async def test_missing_last_tip_and_failed_pickup_preserve_the_rack(self) -> None:
    """A missing eighth tip or failed command must not consume the first seven tips."""
    last_tip = self.column[-1].get_tip()
    self.column[-1].tracker.remove_tip(commit=True)
    before = self.io.request.await_count
    with self.assertRaises(NoTipError):
      await self.pipette.pick_up_tips(self.column)
    self.assertEqual(self.io.request.await_count, before)
    self.assertTrue(all(spot.has_tip() for spot in self.column[:-1]))
    self.column[-1].tracker.add_tip(last_tip)
    self.command_results["pickUpTip"].side_effect = lambda: FAILED_COMMAND
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
    before = self.io.request.await_count
    with self.assertRaises(HasTipError):
      await self.pipette.drop_tips(destinations)
    self.assertEqual(self.io.request.await_count, before)
    self.assertTrue(all(not spot.has_tip() for spot in destinations[:-1]))
    self.command_results["dropTip"].side_effect = lambda: FAILED_COMMAND
    with self.assertRaises(OpentronsError):
      await self.pipette.return_tips()
    self.assertTrue(all(not spot.has_tip() for spot in self.column))
    self.assertIs(self.pipette.tips, mounted)
    self.command_results["dropTip"].side_effect = None
    destinations[-1].tracker.remove_tip(commit=True)
    await self.pipette.drop_tips(destinations)
    for spot, tip in zip(destinations, mounted):
      self.assertIs(spot.get_tip(), tip)

  async def test_last_well_or_tip_capacity_failure_rolls_back_the_whole_transfer(self) -> None:
    """Every nozzle is validated before motion, with earlier staged updates undone."""
    await self.pipette.pick_up_tips(self.column)
    self.sources[-1].tracker.set_volume(0)
    before = self.io.request.await_count
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
    self.assertEqual(self.io.request.await_count, before)

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
        self.command_results[kind].side_effect = lambda: FAILED_COMMAND
        with self.assertRaises(OpentronsError):
          await operation(self.sources, volume=10)
        self.assertEqual([w.tracker.get_used_volume() for w in self.sources], [15] * 8)
        self.assertEqual(
          [tip.tracker.get_used_volume() for tip in self.pipette.tips], [tip_volume] * 8
        )

  async def test_liquid_targets_must_match_the_rigid_head(self) -> None:
    """A partial column or row cannot silently command an eight-nozzle stroke."""
    await self.pipette.pick_up_tips(self.column)
    before = self.io.request.await_count
    for targets in (self.sources[:7], self.plate["A1:A8"]):
      with self.subTest(targets=targets), self.assertRaises(ValueError):
        await self.pipette.aspirate(targets, volume=10)
    self.assertEqual(self.io.request.await_count, before)

  async def test_mix_commits_each_completed_stroke(self) -> None:
    """A later failed mix stroke retains the liquid moved by earlier completed strokes."""
    await self.pipette.pick_up_tips(self.column)
    await self.pipette.mix(self.sources, volume=5, repetitions=2)
    self.assertEqual([w.tracker.volume for w in self.sources], [15] * 8)
    self.command_results["dispenseInPlace"].side_effect = lambda: FAILED_COMMAND
    with self.assertRaises(OpentronsError):
      await self.pipette.mix(self.sources, volume=5, repetitions=1)
    self.assertEqual([w.tracker.volume for w in self.sources], [10] * 8)
    self.assertEqual([tip.tracker.volume for tip in self.pipette.tips], [5] * 8)

  async def test_retraction_failure_keeps_completed_tip_and_liquid_state(self) -> None:
    """A retraction failure must not undo the successful operation preceding it."""
    self.command_results["savePosition"].side_effect = lambda: FAILED_COMMAND
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
    before = self.io.request.await_count
    with self.assertRaisesRegex(ValueError, "contains liquid"):
      await self.pipette.discard_tips()
    self.assertEqual(self.io.request.await_count, before)
    self.command_results["dropTipInPlace"].side_effect = lambda: FAILED_COMMAND
    with self.assertRaises(OpentronsError):
      await self.pipette.discard_tips(allow_nonzero_volume=True)
    self.assertEqual(len(self.pipette.tips), 8)
    self.command_results["dropTipInPlace"].side_effect = None
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
    io, _, _ = _mock_http()
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
    self.assertTrue(all(request.args[0] == "GET" for request in io.request.await_args_list))
    io.stop.assert_awaited_once()

  async def test_queries_do_not_reconfigure_an_active_session(self) -> None:
    io, responses, _ = _mock_http()
    robot = OT2("ot2.local", io=io)
    await robot.setup(skip_home=True)
    try:
      state = vars(robot).copy()
      responses["GET", "/health"].return_value["api_version"] = "8.7.0"
      responses["GET", "/pipettes"].return_value["left"]["name"] = "p300_single_gen2"
      command_count = len(_submitted_commands(io))
      self.assertEqual((await robot.get_health()).software_version, "8.7.0")
      self.assertEqual((await robot.get_mounted_pipettes())[0].name, "p300_single_gen2")
      self.assertEqual(robot.software_version, "7.1.0")
      self.assertEqual(vars(robot), state)
      self.assertEqual(len(_submitted_commands(io)), command_count)
    finally:
      await robot.stop()

  async def test_reconnect_invalidates_old_pipette_and_labware_bindings(self) -> None:
    io, _, _ = _mock_http()
    robot = OT2("ot2.local", io=io)
    await robot.setup(skip_home=True)
    old_pipette, old_registry = robot.left_pipette, robot._labware
    assert old_pipette is not None
    await robot.stop()
    await robot.setup(skip_home=True)
    try:
      self.assertIsNot(robot._labware, old_registry)
      command_count = len(_submitted_commands(io))
      with self.assertRaisesRegex(RuntimeError, "earlier OT-2 run"):
        await old_pipette.move_to(Coordinate(100, 100, 120))
      self.assertEqual(len(_submitted_commands(io)), command_count)
      assert robot.left_pipette is not None
      await robot.left_pipette.move_to(Coordinate(100, 100, 120))
    finally:
      await robot.stop()

  async def test_two_robots_own_independent_sessions_and_connections(self) -> None:
    left_io, _, _ = _mock_http(api_version="6.3.0")
    right_io, _, _ = _mock_http(api_version="8.7.0")
    left = OT2("first.local", io=left_io)
    right = OT2("second.local", io=right_io)
    await asyncio.gather(left.setup(skip_home=True), right.setup(skip_home=True))
    try:
      self.assertIsNot(left._run, right._run)
      self.assertIsNot(left._labware, right._labware)
      self.assertEqual((left.software_version, right.software_version), ("6.3.0", "8.7.0"))
      await left.stop()
      right_io.setup.assert_awaited_once()
      right_io.stop.assert_not_awaited()
      assert right.left_pipette is not None
      await right.left_pipette.move_to(Coordinate(100, 100, 120))
      self.assertEqual([c["commandType"] for c in _submitted_commands(left_io)], ["loadPipette"])
    finally:
      await asyncio.gather(left.stop(), right.stop())

  async def test_unsupported_pipette_does_not_create_a_run_or_home(self) -> None:
    io, _, _ = _mock_http(left_pipette_name="unsupported")
    robot = OT2("ot2.local", io=io)
    with self.assertRaisesRegex(ValueError, "Unsupported OT-2 pipette"):
      await robot.setup()
    self.assertIsNone(robot._run)
    io.stop.assert_awaited_once()
    self.assertTrue(all(request.args[0] == "GET" for request in io.request.await_args_list))

  async def test_stop_waits_for_the_complete_pipette_operation(self) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    io, _, _ = _mock_http()
    respond = io.request.side_effect

    async def pause_movement(
      method: str, path: str, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
      """Hold movement requests until the test allows the operation to finish."""
      if data is not None and data.get("data", {}).get("commandType") == "moveToCoordinates":
        entered.set()
        await release.wait()
      return await respond(method, path, data)

    io.request.side_effect = pause_movement
    robot = OT2("ot2.local", io=io)
    await robot.setup(skip_home=True)
    assert robot.left_pipette is not None
    movement = asyncio.create_task(robot.left_pipette.move_to(Coordinate(100, 100, 100)))
    await asyncio.wait_for(entered.wait(), timeout=1)
    stop = asyncio.create_task(robot.stop())
    try:
      await asyncio.sleep(0)
      self.assertFalse(stop.done())
      io.setup.assert_awaited_once()
      io.stop.assert_not_awaited()
    finally:
      release.set()
      await asyncio.wait_for(asyncio.gather(movement, stop), timeout=1)
    io.stop.assert_awaited_once()
    self.assertEqual(
      [command["commandType"] for command in _submitted_commands(io)[-3:]],
      ["moveToCoordinates", "savePosition", "moveToCoordinates"],
    )


if __name__ == "__main__":
  unittest.main()
