import unittest
from unittest.mock import AsyncMock, call, patch

from pylabrobot.io.http import HTTP, HTTPError
from pylabrobot.opentrons.api import OpentronsAPI
from pylabrobot.opentrons.errors import (
  OpentronsCommandError,
  OpentronsCommandTimeout,
  OpentronsError,
  OpentronsProtocolError,
)
from pylabrobot.opentrons.run import OpentronsRun, _version_at_least
from pylabrobot.resources import Coordinate


class OpentronsRunTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.io = AsyncMock(spec=HTTP)
    self.api = OpentronsAPI(self.io)
    self.protocol_run = OpentronsRun(self.api, "run", "8.7.0", command_poll_interval=0)

  async def test_named_command_waits_for_success_and_returns_only_its_result(self) -> None:
    self.io.request.side_effect = [
      {"data": {"id": "load"}},
      {"data": {"status": "queued"}},
      {"data": {"status": "running"}},
      {"data": {"status": "succeeded", "result": {"pipetteId": "loaded-pipette"}}},
    ]
    self.assertEqual(
      await self.protocol_run.load_pipette("p20_single_gen2", "left"), "loaded-pipette"
    )
    self.assertEqual(
      self.io.request.await_args_list,
      [
        call(
          "POST",
          "/runs/run/commands",
          {
            "data": {
              "commandType": "loadPipette",
              "params": {"pipetteName": "p20_single_gen2", "mount": "left"},
              "intent": "setup",
            }
          },
        ),
      ]
      + [call("GET", "/runs/run/commands/load")] * 3,
    )

  async def test_command_failure_preserves_server_identifiers(self) -> None:
    self.io.request.side_effect = [
      {"data": {"id": "aspirate"}},
      {"data": {"status": "failed", "error": {"errorType": "hardware", "detail": "Motor stalled"}}},
    ]
    with self.assertRaises(OpentronsCommandError) as raised:
      await self.protocol_run.aspirate_in_place("pipette", 10, 3)
    error = raised.exception
    self.assertEqual(
      (error.run_id, error.command_id, error.command_type), ("run", "aspirate", "aspirateInPlace")
    )
    self.assertEqual((error.error_type, error.detail), ("hardware", "Motor stalled"))

  async def test_timeout_keeps_command_id_and_never_resubmits(self) -> None:
    self.io.request.side_effect = [
      {"data": {"id": "slow-command"}},
      {"data": {"status": "running"}},
    ]
    with patch("pylabrobot.opentrons.run.time") as clock:
      clock.monotonic.side_effect = [0, 31]
      with self.assertRaises(OpentronsCommandTimeout) as raised:
        await self.protocol_run.aspirate_in_place("pipette", 10, 3)
    self.assertEqual(raised.exception.run_id, "run")
    self.assertEqual(raised.exception.command_id, "slow-command")
    self.assertEqual(self.io.request.await_count, 2)

  async def test_unexpected_status_and_missing_position_are_not_success(self) -> None:
    for response in (
      {"status": "cancelled"},
      {"status": "succeeded", "result": {}},
      {"status": "succeeded", "result": {"position": {"x": 1, "y": 2, "z": None}}},
    ):
      with self.subTest(response=response):
        self.io.request.side_effect = [{"data": {"id": "position"}}, {"data": response}]
        with self.assertRaises(OpentronsProtocolError):
          await self.protocol_run.get_position("pipette")

  async def test_poll_transport_timeout_preserves_the_submitted_command_id(self) -> None:
    failure = TimeoutError("HTTP response timed out")
    self.io.request.side_effect = [{"data": {"id": "pending"}}, failure]
    with self.assertRaises(OpentronsCommandTimeout) as raised:
      await self.protocol_run.aspirate_in_place("pipette", 10, 3)
    self.assertEqual(raised.exception.command_id, "pending")
    self.assertIs(raised.exception.__cause__, failure)
    self.assertEqual(self.io.request.await_count, 2)

  async def test_position_read_does_not_move_or_cache_the_position(self) -> None:
    self.io.request.side_effect = [
      {"data": {"id": "position"}},
      {
        "data": {
          "status": "succeeded",
          "result": {"positionId": "saved", "position": {"x": 1, "y": 2, "z": 120}},
        }
      },
    ]
    state = vars(self.protocol_run).copy()
    self.assertEqual(await self.protocol_run.get_position("pipette"), Coordinate(1, 2, 120))
    self.assertEqual(vars(self.protocol_run), state)
    self.assertEqual(self.io.request.await_count, 2)

  async def test_stopped_run_cannot_issue_commands_and_failed_stop_can_be_retried(self) -> None:
    self.io.request.side_effect = OSError("connection lost")
    with self.assertRaises(OpentronsError):
      await self.protocol_run.stop()
    self.assertTrue(self.protocol_run.active)
    self.io.request.side_effect = [
      {},
      {"data": {"id": "run", "status": "stop-requested"}},
      {"data": {"id": "run", "status": "stopped"}},
    ]
    self.io.request.reset_mock()
    await self.protocol_run.stop()
    self.assertFalse(self.protocol_run.active)
    self.assertEqual(
      self.io.request.await_args_list,
      [
        call("POST", "/runs/run/actions", {"data": {"actionType": "stop"}}),
        call("GET", "/runs/run"),
        call("GET", "/runs/run"),
      ],
    )
    self.io.request.reset_mock()
    await self.protocol_run.stop()
    with self.assertRaisesRegex(RuntimeError, "has stopped"):
      await self.protocol_run.move_to("pipette", Coordinate(1, 2, 3))
    self.io.request.assert_not_awaited()

  async def test_stop_timeout_retains_the_run_until_a_retry_confirms_shutdown(self) -> None:
    self.io.request.side_effect = [
      {},
      {"data": {"id": "run", "status": "stop-requested"}},
    ]
    with patch("pylabrobot.opentrons.run.time") as clock:
      clock.monotonic.side_effect = [0, 31]
      with self.assertRaisesRegex(OpentronsError, "Timed out waiting for run run to stop"):
        await self.protocol_run.stop()
    self.assertTrue(self.protocol_run.active)
    self.io.request.side_effect = [{}, {"data": {"id": "run", "status": "stopped"}}]
    await self.protocol_run.stop()
    self.assertFalse(self.protocol_run.active)

  async def test_stop_accepts_a_run_removed_by_a_legacy_endpoint(self) -> None:
    self.io.request.side_effect = [{}, HTTPError("GET", "/runs/run", 404, "not found")]
    await self.protocol_run.stop()
    self.assertFalse(self.protocol_run.active)

  async def test_stop_query_failure_keeps_the_run_active(self) -> None:
    self.io.request.side_effect = [{}, HTTPError("GET", "/runs/run", 500, "server error")]
    with self.assertRaises(HTTPError):
      await self.protocol_run.stop()
    self.assertTrue(self.protocol_run.active)


class OpentronsVersionTests(unittest.TestCase):
  def test_versions_are_compared_numerically(self) -> None:
    for version in ("7.10.0", "10.0.0", "7.1", "7.1.0-beta"):
      self.assertTrue(_version_at_least(version, "7.1.0"))
    self.assertFalse(_version_at_least("7.0.9", "7.1.0"))
    with self.assertRaises(ValueError):
      _version_at_least("unknown", "7.1.0")
