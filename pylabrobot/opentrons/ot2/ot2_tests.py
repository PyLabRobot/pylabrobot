from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import OT2, OpentronsError, OT2_8ChannelPipette, OT2SingleChannelPipette
from pylabrobot.opentrons.api import OpentronsAPI
from pylabrobot.opentrons.labware import LabwareRegistry
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import (
  DoorStatus,
  LabwareIdentity,
  ModuleInfo,
  MountedPipette,
  RobotInfo,
  RunInfo,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.opentrons import opentrons_96_filtertiprack_20ul
from pylabrobot.testing.concurrency import AnyioTestBase


class TestOT2Lifecycle(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Keep transport and run execution outside lifecycle tests."""
    await super()._enter_lifespan(stack, **kwargs)
    self.io = create_autospec(HTTP, instance=True, spec_set=True)
    self.api = create_autospec(OpentronsAPI, instance=True, spec_set=True)
    self.api.get_health.return_value = RobotInfo("test-ot2", "OT-2 Standard", "7.1.0")
    self.api.get_mounted_pipettes.return_value = (MountedPipette("left", "p20_single_gen2"),)
    self.api.create_run.return_value = RunInfo("run-id")
    self.api.get_runs.return_value = ()
    self.robot = OT2("ot2.local", io=self.io)
    self.robot._api = self.api
    self.run_class = stack.enter_context(
      patch("pylabrobot.opentrons.ot2.ot2.OpentronsRun", autospec=True)
    )
    self.protocol_run = self.run_class.return_value
    self.protocol_run.active = True
    self.protocol_run.software_version = "7.1.0"
    self.protocol_run.load_pipette.return_value = "left-pipette-id"

  async def test_setup_binds_the_discovered_pipette_and_homes(self) -> None:
    async with self.robot:
      self.io.setup.assert_not_awaited()
      self.run_class.assert_called_once_with(
        self.api, "run-id", "7.1.0", command_timeout=30, command_poll_interval=0.05
      )
      self.protocol_run.load_pipette.assert_awaited_once_with("p20_single_gen2", "left")
      pipette = self.robot.left_pipette
      assert isinstance(pipette, OT2SingleChannelPipette)
      assert (pipette.mount, pipette.name, pipette.pipette_id) == (
        "left",
        "p20_single_gen2",
        "left-pipette-id",
      )
      assert self.robot.right_pipette is None
      assert self.robot.pipettes == [pipette]
      self.api.home.assert_awaited_once_with()

  async def test_skip_home_still_loads_pipettes(self) -> None:
    async with self.robot.lifespan(skip_home=True):
      self.protocol_run.load_pipette.assert_awaited_once_with("p20_single_gen2", "left")
      self.api.home.assert_not_awaited()

  async def test_discovery_selects_multi_channel_models_on_either_mount(self) -> None:
    for mount in ("left", "right"):
      for model in ("p10_multi", "p20_multi_gen2", "p50_multi", "p300_multi", "p300_multi_gen2"):
        self.api.get_mounted_pipettes.return_value = (MountedPipette(mount, model),)
        self.protocol_run.load_pipette.return_value = "multi-id"
        self.protocol_run.load_pipette.reset_mock()

        async with self.robot.lifespan(skip_home=True):
          self.protocol_run.load_pipette.assert_awaited_once_with(model, mount)
          assert len(self.robot.pipettes) == 1
          pipette = self.robot.pipettes[0]
          assert isinstance(pipette, OT2_8ChannelPipette)
          assert (pipette.mount, pipette.name, pipette.num_channels) == (mount, model, 8)

  async def test_connect_allows_queries_without_creating_a_run(self) -> None:
    modules = (ModuleInfo("temperature-module"),)
    runs = (RunInfo("other-run", "idle"),)
    self.api.get_connected_modules.return_value = modules
    self.api.get_runs.return_value = runs

    async with self.robot.lifespan(query_only=True):
      await self.robot.connect()
      assert await self.robot.get_health() == self.api.get_health.return_value
      assert await self.robot.get_mounted_pipettes() == self.api.get_mounted_pipettes.return_value
      assert await self.robot.list_connected_modules() is modules
      assert await self.robot.get_runs() is runs
    self.api.get_connected_modules.assert_awaited_once_with()
    self.api.get_runs.assert_awaited_once_with()
    self.api.create_run.assert_not_awaited()
    self.api.home.assert_not_awaited()
    assert self.robot._run is None
    assert self.robot.software_version is None
    assert self.robot.pipettes == []
    self.io.stop.assert_not_awaited()
    self.protocol_run.stop.assert_not_awaited()

  async def test_rail_lights_and_door_need_a_connection_but_no_run(self) -> None:
    self.api.get_rail_lights.return_value = True
    self.api.get_door_status.return_value = DoorStatus(
      closed=True, required_closed_for_protocol=False
    )
    with pytest.raises(RuntimeError, match="not connected"):
      await self.robot.set_rail_lights(True)

    async with self.robot.lifespan(query_only=True):
      await self.robot.connect()
      await self.robot.set_rail_lights(False)
      assert await self.robot.get_rail_lights()
      assert (await self.robot.get_door_status()).closed
      with pytest.raises(TypeError):
        await self.robot.set_rail_lights(1)  # type: ignore[arg-type]

    self.api.set_rail_lights.assert_awaited_once_with(False)
    self.api.create_run.assert_not_awaited()

  async def test_queries_do_not_rebind_an_active_session(self) -> None:
    async with self.robot.lifespan(skip_home=True):
      pipette, registry = self.robot.left_pipette, self.robot._labware
      self.api.get_health.return_value = RobotInfo("test-ot2", "OT-2 Standard", "8.7.0")
      self.api.get_mounted_pipettes.return_value = (MountedPipette("left", "p300_single_gen2"),)

      assert (await self.robot.get_health()).software_version == "8.7.0"
      assert (await self.robot.get_mounted_pipettes())[0].name == "p300_single_gen2"

      assert self.robot.software_version == "7.1.0"
      assert self.robot.left_pipette is pipette
      assert self.robot._labware is registry
      assert self.robot._run is self.protocol_run
      self.api.create_run.assert_awaited_once()
      self.protocol_run.load_pipette.assert_awaited_once()

  async def test_setup_refuses_an_unfinished_current_run(self) -> None:
    self.api.get_runs.return_value = (
      RunInfo("old", "stopped", current=False),
      RunInfo("left-open", "idle", current=True, created_at="2026-11-04T12:00:00Z"),
    )

    with pytest.raises(RuntimeError, match=r"unfinished run left-open \(idle"):
      async with self.robot:
        pass

    self.api.create_run.assert_not_awaited()
    self.io.stop.assert_not_awaited()
    assert not self.robot._connected
    assert self.robot._run is None

  async def test_setup_goes_ahead_beside_finished_and_earlier_runs(self) -> None:
    self.api.get_runs.return_value = (
      RunInfo("earlier", "idle", current=False),
      RunInfo("finished", "succeeded", current=True),
    )

    async with self.robot.lifespan(skip_home=True):
      self.api.create_run.assert_awaited_once_with()

  async def test_stop_run_stops_an_unfinished_run_and_waits_for_it(self) -> None:
    self.api.get_run.side_effect = [
      RunInfo("left-open", "paused"),
      RunInfo("left-open", "stop-requested"),
      RunInfo("left-open", "stopped"),
    ]
    self.robot.command_poll_interval = 0
    async with self.robot.lifespan(query_only=True):
      await self.robot.connect()
      run = await self.robot.stop_run("left-open")

    assert run == RunInfo("left-open", "stopped")
    self.api.stop_run.assert_awaited_once_with("left-open")
    assert self.api.get_run.await_count == 3

  async def test_stop_run_leaves_a_finished_run_as_it_is(self) -> None:
    self.api.get_run.return_value = RunInfo("done", "succeeded")
    async with self.robot.lifespan(query_only=True):
      await self.robot.connect()
      assert await self.robot.stop_run("done") == RunInfo("done", "succeeded")

    self.api.stop_run.assert_not_awaited()

  async def test_stop_run_reports_a_run_that_does_not_finish(self) -> None:
    self.api.get_run.return_value = RunInfo("stuck", "stop-requested")
    self.robot.command_poll_interval = 0
    async with self.robot.lifespan(query_only=True):
      await self.robot.connect()
      with pytest.raises(TimeoutError, match="did not finish"):
        await self.robot.stop_run("stuck", timeout=0.01)

  async def test_stop_run_refuses_the_robots_own_run(self) -> None:
    async with self.robot.lifespan(skip_home=True):
      self.protocol_run.id = "run-id"
      with pytest.raises(RuntimeError, match="own run"):
        await self.robot.stop_run("run-id")

    self.api.stop_run.assert_not_awaited()

  async def test_home_needs_a_connection_but_no_run(self) -> None:
    with pytest.raises(RuntimeError, match="not connected"):
      await self.robot.home()

    async with self.robot.lifespan(query_only=True):
      await self.robot.connect()
      await self.robot.home()

    self.api.home.assert_awaited_once_with()
    self.api.create_run.assert_not_awaited()

  async def test_unsupported_pipette_closes_connection_before_creating_a_run(self) -> None:
    self.api.get_mounted_pipettes.return_value = (MountedPipette("left", "unsupported"),)

    with pytest.raises(ValueError, match="Unsupported OT-2 pipette"):
      async with self.robot:
        pass

    self.api.create_run.assert_not_awaited()
    self.api.home.assert_not_awaited()
    self.io.stop.assert_not_awaited()
    assert not self.robot._connected
    assert self.robot._run is None

  async def test_stop_clears_bindings_and_closes_transport_after_run_stops(self) -> None:
    async def stop_run() -> None:
      """Observe that bindings remain available until run shutdown completes."""
      assert self.robot._run is self.protocol_run
      assert self.robot.left_pipette is not None
      self.io.stop.assert_not_awaited()

    self.protocol_run.stop.side_effect = stop_run

    async with self.robot:
      pass

    self.protocol_run.stop.assert_awaited_once_with()
    self.io.stop.assert_not_awaited()
    assert self.robot._run is None
    assert self.robot._labware is None
    assert self.robot.software_version is None
    assert self.robot.pipettes == []

  async def test_failed_stop_retains_bindings_and_transport_for_retry(self) -> None:
    failure = OpentronsError("run shutdown failed")
    self.protocol_run.stop.side_effect = [failure, None]

    with pytest.raises(OpentronsError) as raised:
      async with self.robot:
        pipette, registry = self.robot.left_pipette, self.robot._labware

    assert raised.value is failure
    assert self.robot._run is self.protocol_run
    assert self.robot.left_pipette is pipette
    assert self.robot._labware is registry
    self.io.stop.assert_not_awaited()

    await self.robot._stop_run()

    self.io.stop.assert_not_awaited()
    assert self.robot._run is None
    assert self.robot.pipettes == []

  async def test_failed_setup_cleanup_retains_run_for_stop_retry(self) -> None:
    self.protocol_run.load_pipette.side_effect = OpentronsError("pipette load failed")
    failure = OpentronsError("run shutdown failed")
    self.protocol_run.stop.side_effect = [failure, None]

    with pytest.raises(OpentronsError) as raised:
      async with self.robot:
        pass

    assert raised.value is failure
    assert self.robot._run is self.protocol_run
    self.io.stop.assert_not_awaited()

    await self.robot._stop_run()

    assert self.robot._run is None
    self.io.stop.assert_not_awaited()

  async def test_reconnect_invalidates_old_pipette_and_labware_bindings(self) -> None:
    async with self.robot.lifespan(skip_home=True):
      old_pipette, old_registry = self.robot.left_pipette, self.robot._labware
      assert old_pipette is not None

    new_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    new_run.active = True
    new_run.load_pipette.return_value = "new-pipette-id"
    new_run.get_position.return_value = Coordinate(100, 100, 120)
    self.run_class.return_value = new_run
    async with self.robot.lifespan(skip_home=True):
      with pytest.raises(RuntimeError, match="earlier OT-2 run"):
        await old_pipette.move_to(Coordinate(100, 100, 120))

      self.protocol_run.move_to.assert_not_awaited()
      new_run.move_to.assert_not_awaited()
      assert self.robot._labware is not old_registry
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
    other_api.get_runs.return_value = ()
    other_api.create_run.return_value = RunInfo("other-run")
    other_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    other_run.active = True
    other_run.software_version = "8.7.0"
    self.run_class.side_effect = [other_run, self.protocol_run]

    async with other.lifespan(skip_home=True):
      async with self.robot.lifespan(skip_home=True):
        pass

      assert other._run is other_run
      assert other.software_version == "8.7.0"
      other_run.stop.assert_not_awaited()
      other_io.stop.assert_not_awaited()
      await other.home()
      other_api.home.assert_awaited_once_with()


class TestOT2Labware(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Exercise deck-to-registry binding without run execution."""
    await super()._enter_lifespan(stack, **kwargs)
    self.robot = OT2("ot2.local", io=create_autospec(HTTP, instance=True, spec_set=True))
    self.protocol_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    self.protocol_run.active = True
    self.robot._run = self.protocol_run
    self.registry = create_autospec(LabwareRegistry, instance=True, spec_set=True)
    self.registry.is_loaded.return_value = False
    self.robot._labware = self.registry
    self.rack = opentrons_96_filtertiprack_20ul("tips")
    self.robot.deck.assign_child_at_slot(self.rack, 1)

  async def test_official_rack_uploads_generated_definition(self) -> None:
    identity = LabwareIdentity("pylabrobot", "uploaded", 1)
    self.registry.define.return_value = identity

    await self.robot._load_tip_rack(self.rack, self.rack.get_item("A1").get_tip())

    self.registry.define.assert_awaited_once()
    self.registry.load.assert_awaited_once_with(self.rack, "1", identity)

  async def test_custom_rack_passes_its_geometry_to_the_registry(self) -> None:
    self.rack.model = None
    tip = self.rack.get_item("A1").get_tip()
    identity = LabwareIdentity("pylabrobot", "uploaded", 1)
    self.registry.define.return_value = identity

    await self.robot._load_tip_rack(self.rack, tip)

    self.registry.define.assert_awaited_once()
    rack, definition = self.registry.define.await_args.args
    assert rack is self.rack
    assert len(definition["parameters"]["loadName"]) == 32
    assert all(ch in "0123456789abcdef" for ch in definition["parameters"]["loadName"])
    assert definition["parameters"]["tipLength"] == tip.get_size_z()
    assert definition["metadata"]["displayName"] == self.rack.name
    self.registry.load.assert_awaited_once_with(self.rack, "1", identity)
