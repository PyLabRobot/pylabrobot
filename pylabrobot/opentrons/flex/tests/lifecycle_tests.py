"""Offline regression coverage for Flex lifecycle and operation ownership."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.opentrons import ChatterboxHTTP, Flex
from pylabrobot.resources.opentrons import FlexDeck


class FlexLifecycleTests(unittest.IsolatedAsyncioTestCase):
  """Run ownership survives failures and excludes concurrent operations."""

  async def asyncSetUp(self):
    self.io = ChatterboxHTTP(gripper=True)
    self.flex = Flex("offline", io=self.io)
    await self.flex.setup()
    self.addAsyncCleanup(self.flex.disconnect)

  async def test_stale_instruments_cannot_use_a_replacement_run(self):
    head, gripper = self.flex.right_pipette, self.flex.gripper
    assert head is not None and gripper is not None
    await self.flex.disconnect()
    await self.flex.setup()
    count = len(self.io.commands)
    with self.assertRaises(RuntimeError):
      await head.position()
    with self.assertRaises(RuntimeError):
      await gripper.open_jaw()
    self.assertEqual(len(self.io.commands), count)

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
    calls = []

    async def command(command_type, *args, **kwargs):
      calls.append(command_type)
      if command_type == "first":
        entered.set()
        await release.wait()
      return {}

    with patch.object(self.flex, "_execute_command", command):
      first = asyncio.create_task(self.flex.send_command("first"))
      await entered.wait()
      second = asyncio.create_task(self.flex.send_command("second"))
      await asyncio.sleep(0)
      self.assertEqual(calls, ["first"])
      first.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await first
      await second
      self.assertEqual(calls, ["first", "second"])

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
