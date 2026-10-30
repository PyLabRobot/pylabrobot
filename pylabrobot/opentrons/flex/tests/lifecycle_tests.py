"""Offline regression coverage for Flex lifecycle and operation ownership."""

from contextlib import AbstractAsyncContextManager
import logging
from typing import Any, Optional
from unittest.mock import AsyncMock, call, patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import Flex, OpentronsAPI
from pylabrobot.opentrons.errors import OpentronsError
from pylabrobot.opentrons.types import CommandInfo, DeckFixture, InstrumentInfo, RobotInfo, RunInfo
from pylabrobot.resources import Resource
from pylabrobot.resources.opentrons import FlexDeck
from pylabrobot.testing.concurrency import AnyioTestBase


class TestFlexLifecycle(AnyioTestBase):
  """Run ownership survives failures and excludes concurrent operations."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.io = AsyncMock(spec=HTTP)
    self.api = AsyncMock(spec=OpentronsAPI)
    self.api.get_health.return_value = RobotInfo("test-flex", "OT-3 Standard", "9.1.2")
    self.api.get_deck_configuration.return_value = (DeckFixture("cutoutA3", "trashBinAdapter"),)
    self.api.create_run.return_value = RunInfo("run")
    self.api.get_run.return_value = RunInfo("run", "stopped")
    self.api.get_instruments.return_value = (
      InstrumentInfo("right", "pipette", "p1000_single_flex", "p1000_single_v3.5", 1, 1, 1000),
      InstrumentInfo("extension", "gripper", "flexGripper", "gripperV1.3"),
    )
    self.api.submit_command.return_value = "command"
    self.api.get_command.return_value = CommandInfo(
      "succeeded", {"pipetteId": "pipette"}, {}, "command"
    )
    self.flex = Flex("offline", io=self.io)
    self.flex._api = self.api
    self._flex_cm: Optional[AbstractAsyncContextManager[Any]] = None
    await self._open_flex()
    stack.push_async_callback(self._cleanup_flex)

  async def _open_flex(self, *, skip_home: bool = False, query_only: bool = False) -> None:
    cm = self.flex.lifespan(skip_home=skip_home, query_only=query_only)
    await cm.__aenter__()
    self._flex_cm = cm

  async def _close_flex(self) -> None:
    cm = self._flex_cm
    self._flex_cm = None
    if cm is not None:
      await cm.__aexit__(None, None, None)

  async def _cleanup_flex(self) -> None:
    if self._flex_cm is not None:
      await self._close_flex()
    else:
      await self.flex.disconnect()

  async def test_setup_adds_trash_at_device_slot_and_stop_uses_it(self):
    """An initially empty deck acquires the device's bin, including its discard address."""
    await self._close_flex()
    deck = FlexDeck()
    self.flex.attach_deck(deck)
    with pytest.raises(ValueError):
      deck.get_trash_area()
    self.api.get_deck_configuration.return_value = (DeckFixture("cutoutB3", "trashBinAdapter"),)
    await self._open_flex()
    assert deck.get_resource_at_slot("A3") is None
    assert deck.get_slot(deck.get_trash_area()) == "B3"
    head = self.flex.right_pipette
    assert head is not None
    self.api.submit_command.reset_mock()
    with (
      patch.object(head, "has_tip_on_hardware", AsyncMock(side_effect=[True, False])),
      patch.object(head, "move_to_safe_z", AsyncMock()),
    ):
      await self._close_flex()
    move = self.api.submit_command.await_args_list[0]
    assert move.args[1] == "moveToAddressableAreaForDropTip"
    assert move.args[2]["addressableAreaName"] == "movableTrashB3"

  async def test_setup_removes_stale_trash_when_device_has_none(self):
    """A missing physical bin never falls back to a previously configured location."""
    await self._close_flex()
    self.api.get_deck_configuration.return_value = ()
    await self._open_flex()
    with pytest.raises(ValueError):
      self.flex.deck.get_trash_area()

  async def test_setup_preserves_labware_in_device_trash_slot(self):
    """A conflicting resource is rejected before a run or homing command starts."""
    await self._close_flex()
    occupied = Resource("plate", 127, 85, 15)
    self.flex.deck.assign_child_at_slot(occupied, "B3")
    original_trash = self.flex.deck.get_trash_area()
    self.api.get_deck_configuration.return_value = (DeckFixture("cutoutB3", "trashBinAdapter"),)
    self.api.submit_command.reset_mock()
    self.api.create_run.reset_mock()
    with pytest.raises(OpentronsError, match="B3 is occupied"):
      await self._open_flex()
    assert self.flex.deck.get_resource_at_slot("B3") is occupied
    assert self.flex.deck.get_trash_area() is original_trash
    self.api.create_run.assert_not_awaited()
    self.api.submit_command.assert_not_awaited()

  async def test_reconnect_refreshes_device_trash_slot(self):
    """Reconnecting moves the same trash resource to the updated device configuration."""
    trash = self.flex.deck.get_trash_area()
    await self._close_flex()
    self.api.get_deck_configuration.return_value = (DeckFixture("cutoutD1", "trashBinAdapter"),)
    await self._open_flex()
    assert self.flex.deck.get_trash_area() is trash
    assert self.flex.deck.get_slot(trash) == "D1"
    assert self.flex.deck.get_resource_at_slot("A3") is None

  async def test_repeated_setup_warns_and_preserves_session(self):
    """Repeated setup is rejected and leaves the existing run and instruments intact without IO."""
    run = self.flex._require_run()
    head, gripper = self.flex.right_pipette, self.flex.gripper
    self.api.reset_mock()
    self.io.reset_mock()
    for skip_home in (False, True):
      with pytest.raises(RuntimeError, match="already has an active lifespan"):
        async with self.flex.lifespan(skip_home=skip_home):
          pass
      assert self.flex._require_run() is run
      assert self.flex.right_pipette is head
      assert self.flex.gripper is gripper
      assert self.api.mock_calls == []
      assert self.io.mock_calls == []

  async def test_stop_discards_tips_reported_only_by_hardware(self):
    """Tips from an earlier session are discarded before homing and releasing the run."""
    head = self.flex.right_pipette
    assert head is not None
    assert all(tip is None for tip in head.get_mounted_tips())
    self.api.submit_command.reset_mock()
    with (
      patch.object(head, "has_tip_on_hardware", AsyncMock(side_effect=[True, False])),
      patch.object(head, "move_to_safe_z", AsyncMock()),
    ):
      await self._close_flex()
    assert [c.args[1] for c in self.api.submit_command.await_args_list] == [
      "moveToAddressableAreaForDropTip",
      "dropTipInPlace",
      "home",
    ]
    assert self.flex.run_id is None
    assert not self.flex._connected

  async def test_stop_skips_empty_or_unknown_heads(self):
    """Absent or unknown hardware tip state does not trigger an empty discard."""
    for presence in (False, None):
      head = self.flex.right_pipette
      assert head is not None
      with (
        patch.object(head, "has_tip_on_hardware", AsyncMock(return_value=presence)),
        patch.object(head, "discard_tips", AsyncMock()) as discard,
      ):
        await self._close_flex()
      discard.assert_not_awaited()
      await self._open_flex()

  async def test_stop_keeps_releasing_after_tip_discard_failure(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ):
    """A failed tip drop is logged without preventing run release."""
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    head = self.flex.right_pipette
    assert head is not None
    with (
      patch.object(head, "has_tip_on_hardware", AsyncMock(return_value=True)),
      patch.object(
        head, "discard_tips", AsyncMock(side_effect=RuntimeError("drop failed"))
      ) as discard,
      caplog.at_level(logging.WARNING, logger="pylabrobot.opentrons.flex.flex"),
    ):
      await self._close_flex()
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
    discard.assert_awaited_once_with(self.flex.deck.get_trash_area())
    assert self.flex.run_id is None
    assert not self.flex._connected

  async def test_stale_instruments_cannot_use_a_replacement_run(self):
    head, gripper = self.flex.right_pipette, self.flex.gripper
    assert head is not None and gripper is not None
    await self.flex.disconnect()
    await self._close_flex()
    await self._open_flex()
    self.api.submit_command.reset_mock()
    with pytest.raises(RuntimeError):
      await head.request_position()
    with pytest.raises(RuntimeError):
      await head.move_to_safe_z()
    with pytest.raises(RuntimeError):
      await gripper.open_jaw()
    self.api.submit_command.assert_not_awaited()

  async def test_failed_stop_retains_run_and_instruments_for_retry(self):
    run = self.flex._require_run()
    head = self.flex.right_pipette
    with patch.object(run, "stop", AsyncMock(side_effect=RuntimeError("stop failed"))):
      with pytest.raises(RuntimeError, match="stop failed"):
        await self.flex.disconnect()
    assert self.flex._require_run() is run
    assert self.flex.right_pipette is head
    assert self.flex._connected
    await self.flex.disconnect()
    assert self.flex.run_id is None

  async def test_deck_replacement_requires_releasing_run(self):
    with pytest.raises(RuntimeError, match="Stop the active run"):
      self.flex.attach_deck(FlexDeck())
    await self.flex.disconnect()
    deck = FlexDeck()
    self.flex.attach_deck(deck)
    assert self.flex.deck is deck

  async def test_operation_lock_serializes_and_releases_after_cancellation(self):
    entered = anyio.Event()
    release = anyio.Event()
    first_scope = anyio.CancelScope()

    async def command(command_type, *args, **kwargs):
      if command_type == "first":
        entered.set()
        await release.wait()
      return {}

    async def run_first() -> None:
      with first_scope:
        await self.flex.send_command("first")

    with patch.object(self.flex, "_execute_command", AsyncMock(side_effect=command)) as execute:
      async with anyio.create_task_group() as tg:
        tg.start_soon(run_first)
        await entered.wait()
        tg.start_soon(self.flex.send_command, "second")
        await anyio.sleep(0)
        execute.assert_awaited_once_with("first", {}, wait=True, timeout=None)
        first_scope.cancel()
      assert first_scope.cancelled_caught
      execute.assert_has_awaits(
        [
          call("first", {}, wait=True, timeout=None),
          call("second", {}, wait=True, timeout=None),
        ]
      )
      assert execute.await_count == 2

  async def test_initialization_failure_releases_created_run(self):
    await self.flex.disconnect()
    await self._close_flex()
    with patch.object(
      self.flex, "initialize", AsyncMock(side_effect=RuntimeError("bad instrument"))
    ):
      with pytest.raises(RuntimeError, match="bad instrument"):
        await self._open_flex()
    assert self.flex.run_id is None
    assert not self.flex._connected

  async def test_health_failure_closes_opened_transport(self):
    await self.flex.disconnect()
    await self._close_flex()
    with patch.object(
      self.flex._api, "get_health", AsyncMock(side_effect=RuntimeError("bad health"))
    ):
      with pytest.raises(RuntimeError, match="bad health"):
        async with self.flex.lifespan(query_only=True):
          pass
    self.io.stop.assert_not_awaited()
    assert not self.flex._connected
