"""Offline regression coverage for Flex lifecycle and operation ownership."""

import asyncio
import unittest
from unittest.mock import AsyncMock, call, patch

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import Flex, OpentronsAPI
from pylabrobot.opentrons.types import CommandInfo, InstrumentInfo, RobotInfo, RunInfo
from pylabrobot.resources.opentrons import FlexDeck


class FlexLifecycleTests(unittest.IsolatedAsyncioTestCase):
  """Run ownership survives failures and excludes concurrent operations."""

  async def asyncSetUp(self):
    self.io = AsyncMock(spec=HTTP)
    self.api = AsyncMock(spec=OpentronsAPI)
    self.api.get_health.return_value = RobotInfo("test-flex", "OT-3 Standard", "9.1.2")
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
    await self.flex.setup()
    self.addAsyncCleanup(self.flex.disconnect)

  async def test_repeated_setup_warns_and_preserves_session(self):
    """Repeated setup leaves the existing run and instruments intact without IO."""
    run = self.flex._require_run()
    head, gripper = self.flex.right_pipette, self.flex.gripper
    self.api.reset_mock()
    self.io.reset_mock()
    for skip_home in (False, True):
      with self.subTest(skip_home=skip_home), self.assertWarnsRegex(UserWarning, "already set up"):
        await self.flex.setup(skip_home=skip_home)
      self.assertIs(self.flex._require_run(), run)
      self.assertIs(self.flex.right_pipette, head)
      self.assertIs(self.flex.gripper, gripper)
      self.assertEqual(self.api.mock_calls, [])
      self.assertEqual(self.io.mock_calls, [])

  async def test_stop_discards_tips_reported_only_by_hardware(self):
    """Tips from an earlier session are discarded before homing and releasing the run."""
    head = self.flex.right_pipette
    assert head is not None
    self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))
    self.api.submit_command.reset_mock()
    with (
      patch.object(head, "has_tip_on_hardware", AsyncMock(side_effect=[True, False])),
      patch.object(head, "move_to_safe_z", AsyncMock()),
    ):
      await self.flex.stop()
    self.assertEqual(
      [c.args[1] for c in self.api.submit_command.await_args_list],
      ["moveToAddressableAreaForDropTip", "dropTipInPlace", "home"],
    )
    self.assertIsNone(self.flex.run_id)
    self.assertFalse(self.flex._connected)

  async def test_stop_skips_empty_or_unknown_heads(self):
    """Absent or unknown hardware tip state does not trigger an empty discard."""
    for presence in (False, None):
      with self.subTest(presence=presence):
        head = self.flex.right_pipette
        assert head is not None
        with (
          patch.object(head, "has_tip_on_hardware", AsyncMock(return_value=presence)),
          patch.object(head, "discard_tips", AsyncMock()) as discard,
        ):
          await self.flex.stop()
        discard.assert_not_awaited()
        await self.flex.setup()

  async def test_stop_keeps_releasing_after_tip_discard_failure(self):
    """A failed tip drop is logged without preventing run release."""
    head = self.flex.right_pipette
    assert head is not None
    with (
      patch.object(head, "has_tip_on_hardware", AsyncMock(return_value=True)),
      patch.object(
        head, "discard_tips", AsyncMock(side_effect=RuntimeError("drop failed"))
      ) as discard,
      self.assertLogs("pylabrobot.opentrons.flex.flex", level="WARNING"),
    ):
      await self.flex.stop()
    discard.assert_awaited_once_with(self.flex.deck.get_trash_area())
    self.assertIsNone(self.flex.run_id)
    self.assertFalse(self.flex._connected)

  async def test_stale_instruments_cannot_use_a_replacement_run(self):
    head, gripper = self.flex.right_pipette, self.flex.gripper
    assert head is not None and gripper is not None
    await self.flex.disconnect()
    await self.flex.setup()
    self.api.submit_command.reset_mock()
    with self.assertRaises(RuntimeError):
      await head.request_position()
    with self.assertRaises(RuntimeError):
      await head.move_to_safe_z()
    with self.assertRaises(RuntimeError):
      await gripper.open_jaw()
    self.api.submit_command.assert_not_awaited()

  async def test_failed_stop_retains_run_and_instruments_for_retry(self):
    run = self.flex._require_run()
    head = self.flex.right_pipette
    with patch.object(run, "stop", AsyncMock(side_effect=RuntimeError("stop failed"))):
      with self.assertRaisesRegex(RuntimeError, "stop failed"):
        await self.flex.disconnect()
    self.assertIs(self.flex._require_run(), run)
    self.assertIs(self.flex.right_pipette, head)
    self.assertTrue(self.flex._connected)
    await self.flex.disconnect()
    self.assertIsNone(self.flex.run_id)

  async def test_deck_replacement_requires_releasing_run(self):
    with self.assertRaisesRegex(RuntimeError, "Stop the active run"):
      self.flex.attach_deck(FlexDeck())
    await self.flex.disconnect()
    deck = FlexDeck()
    self.flex.attach_deck(deck)
    self.assertIs(self.flex.deck, deck)

  async def test_operation_lock_serializes_and_releases_after_cancellation(self):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def command(command_type, *args, **kwargs):
      if command_type == "first":
        entered.set()
        await release.wait()
      return {}

    with patch.object(self.flex, "_execute_command", AsyncMock(side_effect=command)) as execute:
      first = asyncio.create_task(self.flex.send_command("first"))
      await entered.wait()
      second = asyncio.create_task(self.flex.send_command("second"))
      try:
        await asyncio.sleep(0)
        execute.assert_awaited_once_with("first", {}, wait=True, timeout=None)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
          await first
        await second
        execute.assert_has_awaits(
          [
            call("first", {}, wait=True, timeout=None),
            call("second", {}, wait=True, timeout=None),
          ]
        )
        self.assertEqual(execute.await_count, 2)
      finally:
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)

  async def test_initialization_failure_releases_created_run(self):
    await self.flex.disconnect()
    with patch.object(
      self.flex, "initialize", AsyncMock(side_effect=RuntimeError("bad instrument"))
    ):
      with self.assertRaisesRegex(RuntimeError, "bad instrument"):
        await self.flex.setup()
    self.assertIsNone(self.flex.run_id)
    self.assertFalse(self.flex._connected)

  async def test_health_failure_closes_opened_transport(self):
    await self.flex.disconnect()
    with patch.object(self.io, "stop", AsyncMock()) as stop:
      with patch.object(
        self.flex._api, "get_health", AsyncMock(side_effect=RuntimeError("bad health"))
      ):
        with self.assertRaisesRegex(RuntimeError, "bad health"):
          await self.flex.connect()
      stop.assert_awaited_once()
    self.assertFalse(self.flex._connected)
