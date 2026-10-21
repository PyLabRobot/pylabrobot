import unittest
from unittest.mock import AsyncMock, create_autospec, patch

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import OT2, OpentronsError, OT2_8ChannelPipette, OT2SingleChannelPipette
from pylabrobot.opentrons.api import OpentronsAPI
from pylabrobot.opentrons.labware import LabwareRegistry
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import (
  LabwareIdentity,
  ModuleInfo,
  MountedPipette,
  RobotInfo,
  RunInfo,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.opentrons import opentrons_96_filtertiprack_20ul


class OT2LifecycleTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    """Keep transport and run execution outside lifecycle tests."""
    self.io = create_autospec(HTTP, instance=True, spec_set=True)
    self.api = create_autospec(OpentronsAPI, instance=True, spec_set=True)
    self.api.get_health.return_value = RobotInfo("test-ot2", "OT-2 Standard", "7.1.0")
    self.api.get_mounted_pipettes.return_value = (MountedPipette("left", "p20_single_gen2"),)
    self.api.create_run.return_value = RunInfo("run-id")
    self.robot = OT2("ot2.local", io=self.io)
    self.robot._api = self.api
    run_patch = patch("pylabrobot.opentrons.ot2.ot2.OpentronsRun", autospec=True)
    self.run_class = run_patch.start()
    self.addCleanup(run_patch.stop)
    self.protocol_run = self.run_class.return_value
    self.protocol_run.active = True
    self.protocol_run.software_version = "7.1.0"
    self.protocol_run.load_pipette.return_value = "left-pipette-id"

  async def test_setup_binds_the_discovered_pipette_and_homes(self) -> None:
    await self.robot.setup()

    self.io.setup.assert_awaited_once_with()
    self.run_class.assert_called_once_with(
      self.api, "run-id", "7.1.0", command_timeout=30, command_poll_interval=0.05
    )
    self.protocol_run.load_pipette.assert_awaited_once_with("p20_single_gen2", "left")
    pipette = self.robot.left_pipette
    self.assertIsInstance(pipette, OT2SingleChannelPipette)
    assert pipette is not None
    self.assertEqual(
      (pipette.mount, pipette.name, pipette.pipette_id),
      ("left", "p20_single_gen2", "left-pipette-id"),
    )
    self.assertIsNone(self.robot.right_pipette)
    self.assertEqual(self.robot.pipettes, [pipette])
    self.api.home.assert_awaited_once_with()

  async def test_skip_home_still_loads_pipettes(self) -> None:
    await self.robot.setup(skip_home=True)

    self.protocol_run.load_pipette.assert_awaited_once_with("p20_single_gen2", "left")
    self.api.home.assert_not_awaited()

  async def test_discovery_selects_multi_channel_models_on_either_mount(self) -> None:
    for mount in ("left", "right"):
      for model in ("p10_multi", "p20_multi_gen2", "p50_multi", "p300_multi", "p300_multi_gen2"):
        with self.subTest(mount=mount, model=model):
          self.api.get_mounted_pipettes.return_value = (MountedPipette(mount, model),)
          self.protocol_run.load_pipette.return_value = "multi-id"
          self.protocol_run.load_pipette.reset_mock()

          await self.robot.setup(skip_home=True)

          self.protocol_run.load_pipette.assert_awaited_once_with(model, mount)
          self.assertEqual(len(self.robot.pipettes), 1)
          pipette = self.robot.pipettes[0]
          self.assertIsInstance(pipette, OT2_8ChannelPipette)
          self.assertEqual((pipette.mount, pipette.name, pipette.num_channels), (mount, model, 8))
          await self.robot.stop()

  async def test_connect_allows_queries_without_creating_a_run(self) -> None:
    modules = (ModuleInfo("temperature-module"),)
    runs = (RunInfo("other-run", "idle"),)
    self.api.get_connected_modules.return_value = modules
    self.api.get_runs.return_value = runs

    await self.robot.connect()

    self.assertEqual(await self.robot.get_health(), self.api.get_health.return_value)
    self.assertEqual(
      await self.robot.get_mounted_pipettes(), self.api.get_mounted_pipettes.return_value
    )
    self.assertIs(await self.robot.list_connected_modules(), modules)
    self.assertIs(await self.robot.get_runs(), runs)
    self.api.get_connected_modules.assert_awaited_once_with()
    self.api.get_runs.assert_awaited_once_with()
    self.api.create_run.assert_not_awaited()
    self.api.home.assert_not_awaited()
    self.assertIsNone(self.robot._run)
    self.assertIsNone(self.robot.software_version)
    self.assertEqual(self.robot.pipettes, [])
    await self.robot.stop()
    self.io.stop.assert_awaited_once_with()
    self.protocol_run.stop.assert_not_awaited()

  async def test_queries_do_not_rebind_an_active_session(self) -> None:
    await self.robot.setup(skip_home=True)
    pipette, registry = self.robot.left_pipette, self.robot._labware
    self.api.get_health.return_value = RobotInfo("test-ot2", "OT-2 Standard", "8.7.0")
    self.api.get_mounted_pipettes.return_value = (MountedPipette("left", "p300_single_gen2"),)

    self.assertEqual((await self.robot.get_health()).software_version, "8.7.0")
    self.assertEqual((await self.robot.get_mounted_pipettes())[0].name, "p300_single_gen2")

    self.assertEqual(self.robot.software_version, "7.1.0")
    self.assertIs(self.robot.left_pipette, pipette)
    self.assertIs(self.robot._labware, registry)
    self.assertIs(self.robot._run, self.protocol_run)
    self.api.create_run.assert_awaited_once()
    self.protocol_run.load_pipette.assert_awaited_once()

  async def test_unsupported_pipette_closes_connection_before_creating_a_run(self) -> None:
    self.api.get_mounted_pipettes.return_value = (MountedPipette("left", "unsupported"),)

    with self.assertRaisesRegex(ValueError, "Unsupported OT-2 pipette"):
      await self.robot.setup()

    self.api.create_run.assert_not_awaited()
    self.api.home.assert_not_awaited()
    self.io.stop.assert_awaited_once_with()
    self.assertIsNone(self.robot._run)

  async def test_stop_clears_bindings_and_closes_transport_after_run_stops(self) -> None:
    await self.robot.setup()

    async def stop_run() -> None:
      """Observe that bindings remain available until run shutdown completes."""
      self.assertIs(self.robot._run, self.protocol_run)
      self.assertIsNotNone(self.robot.left_pipette)
      self.io.stop.assert_not_awaited()

    self.protocol_run.stop.side_effect = stop_run

    await self.robot.stop()

    self.protocol_run.stop.assert_awaited_once_with()
    self.io.stop.assert_awaited_once_with()
    self.assertIsNone(self.robot._run)
    self.assertIsNone(self.robot._labware)
    self.assertIsNone(self.robot.software_version)
    self.assertEqual(self.robot.pipettes, [])

  async def test_failed_stop_retains_bindings_and_transport_for_retry(self) -> None:
    await self.robot.setup()
    pipette, registry = self.robot.left_pipette, self.robot._labware
    failure = OpentronsError("run shutdown failed")
    self.protocol_run.stop.side_effect = [failure, None]

    with self.assertRaises(OpentronsError) as raised:
      await self.robot.stop()

    self.assertIs(raised.exception, failure)
    self.assertIs(self.robot._run, self.protocol_run)
    self.assertIs(self.robot.left_pipette, pipette)
    self.assertIs(self.robot._labware, registry)
    self.io.stop.assert_not_awaited()

    await self.robot.stop()

    self.io.stop.assert_awaited_once_with()
    self.assertIsNone(self.robot._run)
    self.assertEqual(self.robot.pipettes, [])

  async def test_failed_setup_cleanup_retains_run_for_stop_retry(self) -> None:
    self.protocol_run.load_pipette.side_effect = OpentronsError("pipette load failed")
    failure = OpentronsError("run shutdown failed")
    self.protocol_run.stop.side_effect = [failure, None]

    with self.assertRaises(OpentronsError) as raised:
      await self.robot.setup()

    self.assertIs(raised.exception, failure)
    self.assertIs(self.robot._run, self.protocol_run)
    self.io.stop.assert_not_awaited()

    await self.robot.stop()

    self.assertIsNone(self.robot._run)
    self.io.stop.assert_awaited_once_with()

  async def test_reconnect_invalidates_old_pipette_and_labware_bindings(self) -> None:
    await self.robot.setup(skip_home=True)
    old_pipette, old_registry = self.robot.left_pipette, self.robot._labware
    assert old_pipette is not None
    await self.robot.stop()
    new_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    new_run.active = True
    new_run.load_pipette.return_value = "new-pipette-id"
    new_run.get_position.return_value = Coordinate(100, 100, 120)
    self.run_class.return_value = new_run
    await self.robot.setup(skip_home=True)

    with self.assertRaisesRegex(RuntimeError, "earlier OT-2 run"):
      await old_pipette.move_to(Coordinate(100, 100, 120))

    self.protocol_run.move_to.assert_not_awaited()
    new_run.move_to.assert_not_awaited()
    self.assertIsNot(self.robot._labware, old_registry)
    assert self.robot.left_pipette is not None
    await self.robot.left_pipette.move_to(Coordinate(100, 100, 120))
    new_run.move_to.assert_awaited_once()

  async def test_two_robots_own_independent_runs_and_connections(self) -> None:
    other_io = create_autospec(HTTP, instance=True, spec_set=True)
    other = OT2("other.local", io=other_io)
    other_api = create_autospec(OpentronsAPI, instance=True, spec_set=True)
    other._api = other_api
    other_api.get_health.return_value = RobotInfo("other", "OT-2 Standard", "8.7.0")
    other_api.get_mounted_pipettes.return_value = ()
    other_api.create_run.return_value = RunInfo("other-run")
    other_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    other_run.active = True
    other_run.software_version = "8.7.0"
    self.run_class.side_effect = [self.protocol_run, other_run]
    await self.robot.setup(skip_home=True)
    await other.setup(skip_home=True)

    await self.robot.stop()

    self.assertIs(other._run, other_run)
    self.assertEqual(other.software_version, "8.7.0")
    other_run.stop.assert_not_awaited()
    other_io.stop.assert_not_awaited()
    await other.home()
    other_api.home.assert_awaited_once_with()


class OT2LabwareTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    """Exercise deck-to-registry binding without run execution."""
    self.robot = OT2("ot2.local", io=AsyncMock(spec_set=HTTP))
    self.protocol_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    self.protocol_run.active = True
    self.robot._run = self.protocol_run
    self.registry = create_autospec(LabwareRegistry, instance=True, spec_set=True)
    self.registry.is_loaded.return_value = False
    self.robot._labware = self.registry
    self.rack = opentrons_96_filtertiprack_20ul("tips")
    self.robot.deck.assign_child_at_slot(self.rack, 1)

  async def test_official_rack_uses_builtin_definition_for_calibration(self) -> None:
    await self.robot._load_tip_rack(self.rack, self.rack.get_item("A1").get_tip())

    self.registry.load.assert_awaited_once_with(
      self.rack, "1", LabwareIdentity("opentrons", "opentrons_96_filtertiprack_20ul", 1)
    )
    self.registry.define.assert_not_awaited()

  async def test_custom_rack_passes_its_geometry_to_the_registry(self) -> None:
    self.rack.model = None
    tip = self.rack.get_item("A1").get_tip()
    identity = LabwareIdentity("pylabrobot", "uploaded", 1)
    self.registry.define.return_value = identity

    await self.robot._load_tip_rack(self.rack, tip)

    self.registry.define.assert_awaited_once()
    rack, definition = self.registry.define.await_args.args
    self.assertIs(rack, self.rack)
    self.assertRegex(definition["parameters"]["loadName"], r"^[0-9a-f]{32}$")
    self.assertEqual(definition["parameters"]["tipLength"], tip.get_size_z())
    self.assertEqual(definition["metadata"]["displayName"], self.rack.name)
    self.registry.load.assert_awaited_once_with(self.rack, "1", identity)


if __name__ == "__main__":
  unittest.main()
