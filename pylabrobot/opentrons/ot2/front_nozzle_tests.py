"""OT-2 eight-channel tips on the front nozzles, with mocked and run-level operations."""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional
from unittest.mock import ANY, AsyncMock, create_autospec, patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import OT2, OT2_8ChannelPipette
from pylabrobot.opentrons.api import OpentronsAPI
from pylabrobot.opentrons.labware import LabwareRegistry
from pylabrobot.opentrons.ot2 import pipette as ot2_pipette_module
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import CommandInfo, LabwareIdentity
from pylabrobot.resources import Coordinate, Resource, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul
from pylabrobot.testing.concurrency import AnyioTestBase

ALL = {"style": "ALL"}


class _ScriptedAPI(OpentronsAPI):
  """Minimal offline API recording submitted run commands through real ``OpentronsRun.execute``."""

  def __init__(self) -> None:
    super().__init__(AsyncMock(spec_set=HTTP))
    self.submitted: List[tuple[str, Dict[str, Any]]] = []
    self._commands_by_id: Dict[str, tuple[str, Dict[str, Any]]] = {}
    self._next_id = 0
    self.on_submit: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None

  async def define_labware(self, run_id: str, definition: Dict[str, Any]) -> LabwareIdentity:
    del run_id, definition
    return LabwareIdentity("pylabrobot", "rack", 1)

  async def submit_command(
    self,
    run_id: str,
    command_type: str,
    params: Dict[str, Any],
    intent: Literal["setup", "protocol"] = "setup",
  ) -> str:
    del run_id, intent
    self._next_id += 1
    command_id = f"cmd-{self._next_id}"
    self.submitted.append((command_type, params))
    self._commands_by_id[command_id] = (command_type, params)
    if self.on_submit is not None:
      await self.on_submit(command_type, params)
    return command_id

  async def get_command(self, run_id: str, command_id: str) -> CommandInfo:
    del run_id
    command_type, _params = self._commands_by_id[command_id]
    result: Dict[str, Any] = {}
    if command_type == "loadLabware":
      result = {"labwareId": "tips-id"}
    elif command_type == "savePosition":
      result = {"position": {"x": 30.25, "y": 40.5, "z": 20.0}}
    return CommandInfo(id=command_id, status="succeeded", result=result, error={})


class TestOT2FrontNozzles(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """An eight-channel pipette, a full rack and a plate, without hardware."""
    await super()._enter_lifespan(stack, **kwargs)
    set_tip_tracking(True)
    set_volume_tracking(True)
    stack.callback(set_tip_tracking, False)
    stack.callback(set_volume_tracking, False)
    self.protocol_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    self.protocol_run.active = True
    self.protocol_run.get_position.return_value = Coordinate(30.25, 40.5, 20)
    self.deck = OTDeck()
    self.robot = OT2("ot2.local", deck=self.deck, io=AsyncMock(spec_set=HTTP))
    self.robot._run = self.protocol_run
    self.robot._labware = LabwareRegistry(self.protocol_run)
    self.pipette = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-id")
    self.robot.left_pipette = self.pipette
    self.rack = opentrons_96_filtertiprack_20ul("tips")
    self.deck.assign_child_at_slot(self.rack, 4)
    self.plate = celltreat_96_wellplate_350uL_Fb("plate")
    self.deck.assign_child_at_slot(self.plate, 2)
    for well in self.plate["A1:H1"]:
      well.tracker.set_volume(15)

  def _layouts(self) -> List[Dict[str, Any]]:
    """The nozzle layouts sent, in order."""
    return [c.args[1] for c in self.protocol_run.mock_calls if c[0] == "configure_nozzle_layout"]

  async def test_front_tips_set_the_partial_column_and_move_by_the_front_nozzle(self) -> None:
    spots = self.rack["A1:C1"]
    tips = tuple(spot.get_tip() for spot in spots)

    await self.pipette.pick_up_tips(spots, use_channels=[5, 6, 7])

    assert self.pipette.tip_channels == (5, 6, 7)
    assert self.pipette.tips == tips
    names = [c[0] for c in self.protocol_run.mock_calls]
    assert names.index("configure_nozzle_layout") < names.index("pick_up_tip")
    assert self._layouts() == [
      {
        "style": "QUADRANT",
        "primaryNozzle": "H1",
        "frontRightNozzle": "H1",
        "backLeftNozzle": "F1",
      }
    ]
    self.protocol_run.pick_up_tip.assert_awaited_once_with(
      "left-id", ANY, "C1", Coordinate(z=tips[0].get_size_z())
    )

    self.protocol_run.reset_mock()
    wells = self.plate["F1:H1"]
    await self.pipette.aspirate(wells, volume=5)
    front_well = self.robot._deck_to_robot_frame(
      wells[-1].get_location_wrt(self.deck, "c", "c", "cavity_bottom")
    )
    self.protocol_run.move_to.assert_any_await(
      "left-id",
      front_well,
      speed=None,
      minimum_z_height=self.robot.traversal_height,
      force_direct=False,
    )
    assert [w.tracker.volume for w in self.plate["E1:H1"]] == [15, 10, 10, 10]

    self.protocol_run.reset_mock()
    await self.pipette.dispense(wells, volume=5)
    await self.pipette.return_tips()

    self.protocol_run.drop_tip.assert_awaited_once_with("left-id", ANY, "C1", Coordinate(z=10))
    assert self._layouts() == [ALL]
    names = [c[0] for c in self.protocol_run.mock_calls]
    assert names.index("drop_tip") < names.index("configure_nozzle_layout")
    assert self.pipette.tip_channels == ()
    assert all(spot.has_tip() for spot in spots)

  async def test_one_front_tip_is_the_single_layout_and_discards_back_to_all(self) -> None:
    await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
    assert self._layouts() == [{"style": "SINGLE", "primaryNozzle": "H1"}]

    # Over the trash the back nozzle stands beyond the reach the check allows; retracting
    # straight up from there is not checked again.
    self.protocol_run.get_position.return_value = Coordinate(330, 410, 80)
    await self.pipette.discard_tips()

    assert self._layouts()[-1] == ALL
    self.protocol_run.move_to.assert_awaited_with(
      "left-id",
      Coordinate(330, 410, self.robot.traversal_height),
      speed=None,
      minimum_z_height=None,
      force_direct=True,
    )
    assert not self.pipette.has_tip

  async def test_the_front_nozzle_reaches_where_the_back_one_cannot(self) -> None:
    target = Coordinate(100, -20, 60)
    await self.pipette.pick_up_tips(self.rack["H1:H1"])
    with pytest.raises(ValueError, match="reachable"):
      await self.pipette.move_to(target)
    await self.pipette.return_tips()

    await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
    await self.pipette.move_to(target)

  async def test_tips_behind_the_front_selection_are_refused(self) -> None:
    self.protocol_run.reset_mock()
    with pytest.raises(ValueError, match="undeclared tips at tips_A1, tips_B1, tips_C1"):
      await self.pipette.pick_up_tips(self.rack["D1:E1"], use_channels=[6, 7])
    assert self.protocol_run.mock_calls == []
    assert not self.pipette.has_tip

  async def test_channels_with_a_gap_or_in_the_middle_are_refused(self) -> None:
    for channels in ([0, 2], [3, 4], [6]):
      with pytest.raises(ValueError, match="use_channels"):
        await self.pipette.pick_up_tips(self.rack["A1:B1"], use_channels=channels)
    assert self.protocol_run.mock_calls == []

  async def test_a_failed_front_pickup_puts_the_layout_back(self) -> None:
    self.protocol_run.pick_up_tip.side_effect = RuntimeError("pickup failed")
    with pytest.raises(RuntimeError, match="pickup failed"):
      await self.pipette.pick_up_tips(self.rack["A1:B1"], use_channels=[6, 7])
    assert self._layouts()[-1] == ALL
    assert all(spot.has_tip() for spot in self.rack["A1:B1"])

  async def test_cancelled_front_pickup_before_dispatch_restores_all_layout(self) -> None:
    api = _ScriptedAPI()
    run = OpentronsRun(api, "run-1", "7.1.0")
    self.robot._run = run
    self.robot._labware = LabwareRegistry(run)
    pipette = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-id")
    self.robot.left_pipette = pipette

    with anyio.CancelScope() as scope:

      async def cancel_after_layout(command_type: str, params: Dict[str, Any]) -> None:
        if command_type == "configureNozzleLayout" and params["configurationParams"] != ALL:
          scope.cancel()

      api.on_submit = cancel_after_layout
      await pipette.pick_up_tips(self.rack["A1:B1"], use_channels=[6, 7])

    assert scope.cancelled_caught
    assert not pipette.has_tip
    assert all(spot.has_tip() for spot in self.rack["A1:B1"])
    layouts = [
      params["configurationParams"]
      for cmd, params in api.submitted
      if cmd == "configureNozzleLayout"
    ]
    assert layouts == [
      {
        "style": "QUADRANT",
        "primaryNozzle": "H1",
        "frontRightNozzle": "H1",
        "backLeftNozzle": "G1",
      },
      ALL,
    ]
    assert "pickUpTip" not in [cmd for cmd, _ in api.submitted]

  async def test_cancelled_retract_after_front_return_or_discard_restores_all_layout(self) -> None:
    for dispose in ("return", "discard"):
      api = _ScriptedAPI()
      run = OpentronsRun(api, "run-1", "7.1.0")
      self.robot._run = run
      self.robot._labware = LabwareRegistry(run)
      pipette = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-id")
      self.robot.left_pipette = pipette
      await pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
      api.submitted.clear()

      with anyio.CancelScope() as scope:

        async def cancel_after_drop(command_type: str, params: Dict[str, Any]) -> None:
          del params
          if command_type in {"dropTip", "dropTipInPlace"}:
            scope.cancel()

        api.on_submit = cancel_after_drop
        if dispose == "return":
          await pipette.return_tips()
        else:
          await pipette.discard_tips()

      assert scope.cancelled_caught
      assert not pipette.has_tip
      assert pipette.tip_channels == ()
      layouts = [
        params["configurationParams"]
        for cmd, params in api.submitted
        if cmd == "configureNozzleLayout"
      ]
      assert layouts == [ALL]
      if dispose == "return":
        assert self.rack["A1:A1"][0].has_tip()

  async def test_failed_layout_reset_after_front_pickup_self_heals_before_full_column_pickup(
    self,
  ) -> None:
    api = _ScriptedAPI()
    run = OpentronsRun(api, "run-1", "7.1.0")
    self.robot._run = run
    self.robot._labware = LabwareRegistry(run)
    pipette = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-id")
    self.robot.left_pipette = pipette

    with anyio.CancelScope() as scope:

      async def cancel_and_fail_reset(command_type: str, params: Dict[str, Any]) -> None:
        if command_type == "configureNozzleLayout":
          if params["configurationParams"] != ALL:
            scope.cancel()
          else:
            raise RuntimeError("transient reset failure")

      api.on_submit = cancel_and_fail_reset
      await pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])

    assert scope.cancelled_caught
    assert pipette._nozzle_layout_non_default
    api.on_submit = None
    api.submitted.clear()
    await pipette.pick_up_tips(self.rack["A2:H2"])
    command_types = [cmd for cmd, _ in api.submitted]
    assert command_types.index("configureNozzleLayout") < command_types.index("pickUpTip")
    assert (
      api.submitted[command_types.index("configureNozzleLayout")][1]["configurationParams"] == ALL
    )

  async def test_failed_layout_reset_after_front_pickup_self_heals_before_move_to(
    self,
  ) -> None:
    api = _ScriptedAPI()
    run = OpentronsRun(api, "run-1", "7.1.0")
    self.robot._run = run
    self.robot._labware = LabwareRegistry(run)
    pipette = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-id")
    self.robot.left_pipette = pipette

    with anyio.CancelScope() as scope:

      async def cancel_and_fail_reset(command_type: str, params: Dict[str, Any]) -> None:
        """Cancel after front layout setup, then fail the rollback reset."""
        if command_type == "configureNozzleLayout":
          if params["configurationParams"] != ALL:
            scope.cancel()
          else:
            raise RuntimeError("transient reset failure")

      api.on_submit = cancel_and_fail_reset
      await pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])

    assert scope.cancelled_caught
    assert pipette._nozzle_layout_non_default
    api.on_submit = None
    api.submitted.clear()
    await pipette.move_to(Coordinate(100, 100, 80))
    command_types = [cmd for cmd, _ in api.submitted]
    assert command_types.index("configureNozzleLayout") < command_types.index("moveToCoordinates")
    assert (
      api.submitted[command_types.index("configureNozzleLayout")][1]["configurationParams"] == ALL
    )

    api.submitted.clear()
    await pipette.move_to(Coordinate(110, 100, 80))
    assert "configureNozzleLayout" not in [cmd for cmd, _ in api.submitted]

  async def test_cancelled_front_pickup_at_run_return_checkpoint_keeps_tips_and_layout(
    self,
  ) -> None:
    api = _ScriptedAPI()
    run = OpentronsRun(api, "run-1", "7.1.0")
    self.robot._run = run
    self.robot._labware = LabwareRegistry(run)
    pipette = OT2_8ChannelPipette(self.robot, "left", "p20_multi_gen2", "left-id")
    self.robot.left_pipette = pipette
    spots = self.rack["A1:B1"]
    tips = tuple(spot.get_tip() for spot in spots)

    with anyio.CancelScope() as scope:

      async def cancel_during_pickup(command_type: str, params: Dict[str, Any]) -> None:
        """Cancel while pickUpTip is shielded inside OpentronsRun.execute."""
        del params
        if command_type == "pickUpTip":
          scope.cancel()

      api.on_submit = cancel_during_pickup
      await pipette.pick_up_tips(spots, use_channels=[6, 7])

    assert scope.cancelled_caught
    assert pipette.tips == tips
    assert pipette.tip_channels == (6, 7)
    assert all(not spot.has_tip() for spot in spots)
    layouts = [
      params["configurationParams"]
      for cmd, params in api.submitted
      if cmd == "configureNozzleLayout"
    ]
    assert layouts == [
      {
        "style": "QUADRANT",
        "primaryNozzle": "H1",
        "frontRightNozzle": "H1",
        "backLeftNozzle": "G1",
      }
    ]

  async def test_layout_reset_error_handling_and_shield_bounds(
    self,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    for dispose in ("return", "discard"):
      # 1. With unpatched command_timeout + COMMAND_POLL_HEADROOM, a 0.05 s ALL reset succeeds,
      # and on normal completion (retracted=True) a reset error propagates loudly.
      self.rack.fill()
      await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])

      async def slow_reset(pipette_id: str, configuration: Dict[str, Any]) -> None:
        del pipette_id
        if configuration == ALL:
          await anyio.sleep(0.05)

      self.protocol_run.configure_nozzle_layout.side_effect = slow_reset
      if dispose == "return":
        await self.pipette.return_tips()
      else:
        await self.pipette.discard_tips()
      assert not self.pipette._nozzle_layout_non_default

      self.protocol_run.configure_nozzle_layout.side_effect = None
      self.rack.fill()
      await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])

      async def fail_normal_reset(pipette_id: str, configuration: Dict[str, Any]) -> None:
        del pipette_id
        if configuration == ALL:
          raise RuntimeError("normal reset failed")

      self.protocol_run.configure_nozzle_layout.side_effect = fail_normal_reset
      with pytest.raises(RuntimeError, match="normal reset failed"):
        if dispose == "return":
          await self.pipette.return_tips()
        else:
          await self.pipette.discard_tips()
      assert self.pipette._nozzle_layout_non_default

      # If normal post-retract ALL reset stalls while outer caller cancels at retract return,
      # fail_after(..., shield=True) bounds the shielded scope with TimeoutError.
      self.protocol_run.configure_nozzle_layout.side_effect = None
      self.pipette._nozzle_layout_non_default = False
      self.rack.fill()
      await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])

      with anyio.CancelScope() as cancel_on_retract:

        async def cancel_at_retract(*args: Any, **kwargs: Any) -> None:
          del args, kwargs
          cancel_on_retract.cancel()

        async def stall_reset(pipette_id: str, configuration: Dict[str, Any]) -> None:
          del pipette_id
          if configuration == ALL:
            await anyio.sleep(0.5)

        self.protocol_run.move_to.side_effect = cancel_at_retract
        self.protocol_run.configure_nozzle_layout.side_effect = stall_reset
        try:
          with anyio.fail_after(0.25):
            with (
              patch.object(self.robot, "command_timeout", 0.01),
              patch.object(ot2_pipette_module, "COMMAND_POLL_HEADROOM", 0.01),
            ):
              with pytest.raises(TimeoutError):
                if dispose == "return":
                  await self.pipette.return_tips()
                else:
                  await self.pipette.discard_tips()
            await anyio.lowlevel.checkpoint()
        finally:
          self.protocol_run.move_to.side_effect = None
          self.protocol_run.configure_nozzle_layout.side_effect = None
      assert self.pipette._nozzle_layout_non_default

      # 2. When retract unwinds, secondary reset errors and stalls are bounded and logged
      # without masking the primary retract error.
      self.pipette._nozzle_layout_non_default = False
      self.rack.fill()
      await self.pipette.pick_up_tips(self.rack["A2:A2"], use_channels=[7])
      self.protocol_run.move_to.side_effect = RuntimeError("retract failed")
      self.protocol_run.configure_nozzle_layout.side_effect = fail_normal_reset
      caplog.clear()
      try:
        with caplog.at_level(logging.WARNING, logger="pylabrobot.opentrons.ot2.pipette"):
          with pytest.raises(RuntimeError, match="retract failed"):
            if dispose == "return":
              await self.pipette.return_tips()
            else:
              await self.pipette.discard_tips()
      finally:
        self.protocol_run.move_to.side_effect = None
        self.protocol_run.configure_nozzle_layout.side_effect = None
      assert self.pipette._nozzle_layout_non_default
      assert "Restoring ALL nozzle layout during unwind failed" in caplog.text

      self.pipette._nozzle_layout_non_default = False
      self.rack.fill()
      await self.pipette.pick_up_tips(self.rack["A2:A2"], use_channels=[7])
      self.protocol_run.move_to.side_effect = RuntimeError("retract failed")
      self.protocol_run.configure_nozzle_layout.side_effect = stall_reset
      caplog.clear()
      try:
        with caplog.at_level(logging.WARNING, logger="pylabrobot.opentrons.ot2.pipette"):
          with anyio.fail_after(0.25):
            with (
              patch.object(self.robot, "command_timeout", 0.01),
              patch.object(ot2_pipette_module, "COMMAND_POLL_HEADROOM", 0.01),
            ):
              with pytest.raises(RuntimeError, match="retract failed"):
                if dispose == "return":
                  await self.pipette.return_tips()
                else:
                  await self.pipette.discard_tips()
            await anyio.lowlevel.checkpoint()
      finally:
        self.protocol_run.move_to.side_effect = None
        self.protocol_run.configure_nozzle_layout.side_effect = None
      assert self.pipette._nozzle_layout_non_default
      assert "Restoring ALL nozzle layout during unwind timed out" in caplog.text

  async def test_nozzles_without_a_tip_stay_clear_of_the_deck(self) -> None:
    # A tall box in slot 5, behind the plate: the front tip's empty neighbours hang over it.
    tall = Resource("tall_box", 120, 80, 200)
    self.deck.assign_child_at_slot(tall, 5)
    await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
    self.protocol_run.reset_mock()

    with pytest.raises(ValueError, match="without a tip, would come down to .* over tall_box"):
      await self.pipette.aspirate(self.plate["A1:A1"], volume=5)

    self.protocol_run.move_to.assert_not_awaited()
    self.protocol_run.aspirate_in_place.assert_not_awaited()
    assert self.plate.get_well("A1").tracker.volume == 15

  async def test_the_clearance_can_be_set(self) -> None:
    await self.pipette.pick_up_tips(self.rack["A1:A1"], use_channels=[7])
    low = Resource("low_box", 120, 80, 1)
    self.deck.assign_child_at_slot(low, 5)
    await self.pipette.aspirate(self.plate["A1:A1"], volume=1)
    self.pipette.empty_nozzle_clearance = 1000
    with pytest.raises(ValueError, match="kept 1000 mm above"):
      await self.pipette.aspirate(self.plate["A1:A1"], volume=1)
