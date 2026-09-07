import asyncio
import unittest
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons.ot2.ot2 import OpentronsOT2, OpentronsOT2Error, _version_at_least
from pylabrobot.resources import Coordinate, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.errors import TooLittleLiquidError, TooLittleVolumeError
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
    if self.stop_requests_fail and (
      path.endswith(("/actions", "/cancel")) or method == "DELETE"
    ):
      raise RuntimeError("stop request rejected")
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
    self.assertEqual(self.robot.left_pipette.num_channels, 1)
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

  async def test_rejected_aspiration_preserves_both_volume_trackers(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
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
    assert pipette is not None
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
    assert pipette is not None
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

        with self.assertRaisesRegex(OpentronsOT2Error, "moveToCoordinates"):
          await operation(well, volume=10)

        self.assertEqual(well.tracker.get_used_volume(), expected_well)
        self.assertEqual(well.tracker.volume, expected_well)
        self.assertEqual(pipette.tip.tracker.get_used_volume(), expected_tip)
        self.assertEqual(pipette.tip.tracker.volume, expected_tip)

  async def test_moved_or_unassigned_loaded_rack_is_rejected_before_a_command(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
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
    assert pipette is not None
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
    assert pipette is not None
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
      sum(command["commandType"] == "pickUpTip" for command in self.io.commands), 1
    )
    self.assertTrue(pipette.has_tip)
    self.assertFalse(self.tips.get_item("A1").has_tip())
    self.assertTrue(self.tips.get_item("A2").has_tip())

  async def test_concurrent_transfer_checks_state_after_preceding_operation(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
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
    assert pipette is not None
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
        for command in self.io.commands
      ),
      1,
    )

  async def test_mix_rejects_insufficient_liquid_or_tip_capacity_before_moving(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
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
    assert pipette is not None
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    self.io.fail_command_type = "dispenseInPlace"
    self.io.fail_command_occurrence = 2

    with self.assertRaisesRegex(OpentronsOT2Error, "dispenseInPlace"):
      await pipette.mix(source, volume=10, repetitions=3)

    self.assertEqual(source.tracker.get_used_volume(), 5)
    self.assertEqual(source.tracker.volume, 5)
    assert pipette.tip is not None
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 10)
    self.assertEqual(pipette.tip.tracker.volume, 10)

  async def test_mix_preserves_volume_after_completed_cycles(self) -> None:
    pipette = self.robot.left_pipette
    assert pipette is not None
    await pipette.pick_up_tip(self.tips.get_item("A1"))
    assert pipette.tip is not None
    source = self.plate.get_well("A1")
    source.tracker.set_volume(15)
    pipette.tip.tracker.set_volume(5)

    await pipette.mix(source, volume=10, repetitions=3)

    self.assertEqual(source.tracker.get_used_volume(), 15)
    self.assertEqual(pipette.tip.tracker.get_used_volume(), 5)
    self.assertEqual(
      sum(command["commandType"] == "aspirateInPlace" for command in self.io.commands), 3
    )
    self.assertEqual(
      sum(command["commandType"] == "dispenseInPlace" for command in self.io.commands), 3
    )

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

  async def test_failed_stop_retains_state_and_transport_for_retry(self) -> None:
    pipette = self.robot.left_pipette
    run_id = self.robot._run_id
    self.io.stop_requests_fail = True

    with self.assertRaisesRegex(OpentronsOT2Error, "Could not cancel") as error:
      await self.robot.stop()

    self.assertIsInstance(error.exception.__cause__, RuntimeError)
    self.assertEqual(self.robot._run_id, run_id)
    self.assertIs(self.robot.left_pipette, pipette)
    self.assertTrue(self.io.started)
    self.io.stop_requests_fail = False
    await self.robot.stop()
    self.assertIsNone(self.robot._run_id)
    self.assertIsNone(self.robot.left_pipette)
    self.assertFalse(self.io.started)

  async def test_failed_setup_cleanup_retains_run_for_stop_retry(self) -> None:
    await self.robot.stop()
    self.io.fail_command_type = "loadPipette"
    self.io.stop_requests_fail = True

    with self.assertRaisesRegex(OpentronsOT2Error, "Could not cancel"):
      await self.robot.setup()

    self.assertEqual(self.robot._run_id, "run-id")
    self.assertTrue(self.io.started)
    self.io.stop_requests_fail = False
    await self.robot.stop()
    self.assertIsNone(self.robot._run_id)
    self.assertFalse(self.io.started)


class OpentronsOT2MultiChannelTests(unittest.IsolatedAsyncioTestCase):
  async def test_multi_channel_is_modeled_but_not_mistracked_as_one_tip(self) -> None:
    io = FakeHTTP(left_pipette_name="p20_multi_gen2")
    deck = OTDeck()
    robot = OpentronsOT2(host="ot2.local", deck=deck, command_poll_interval=0, io=io)
    await robot.setup(skip_home=True)
    tips = opentrons_96_filtertiprack_20ul(name="tips")
    deck.assign_child_at_slot(tips, slot=1)
    assert robot.left_pipette is not None
    self.assertEqual(robot.left_pipette.num_channels, 8)

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
