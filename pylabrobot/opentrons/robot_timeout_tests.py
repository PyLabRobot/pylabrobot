"""Tests for the robot's wait budgets: the transport timeout, the per-command
poll ceiling and the poll interval, and that a caller can set all three.

A deployment on a slow link, or one whose dispatcher aborts overrunning
commands itself, has to be able to move these without editing the module.
"""

import time
import unittest
from typing import Any, Dict, Optional

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.robot import DEFAULT_COMMAND_TIMEOUT, DEFAULT_STATUS_POLL_INTERVAL
from pylabrobot.opentrons.transport import ChatterboxTransport, HttpxTransport
from pylabrobot.resources.opentrons.flex_deck import FlexDeck


class _ScriptedStatusTransport(ChatterboxTransport):
  """Answers command status from a script instead of succeeding on the first read.

  Counts the reads it served, so a test can tell one poll rate from another, and
  reports "running" until ``succeed_after`` reads have gone by (forever if None).
  """

  def __init__(self, succeed_after: Optional[int] = None) -> None:
    super().__init__()
    self.status_reads = 0
    self._succeed_after = succeed_after

  async def get(self, path: str) -> Dict[str, Any]:
    if "/commands/" not in path:
      return await super().get(path)
    self.status_reads += 1
    finished = self._succeed_after is not None and self.status_reads > self._succeed_after
    return {
      "data": {
        "id": path.rsplit("/", 1)[-1],
        "commandType": "",
        "status": "succeeded" if finished else "running",
        "result": {},
      }
    }


def _flex(
  transport: Optional[ChatterboxTransport] = None,
  command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
  status_poll_interval: float = DEFAULT_STATUS_POLL_INTERVAL,
) -> OpentronsFlex:
  return OpentronsFlex(
    deck=FlexDeck(),
    host="localhost",
    transport=transport or ChatterboxTransport(),
    command_timeout=command_timeout,
    status_poll_interval=status_poll_interval,
  )


class RobotTimeoutTests(unittest.IsolatedAsyncioTestCase):
  def test_the_transport_the_robot_builds_carries_the_request_timeout(self):
    robot = OpentronsFlex(deck=FlexDeck(), host="localhost", request_timeout=7.5)

    transport = robot._transport
    assert isinstance(transport, HttpxTransport)
    self.assertEqual(transport.io.serialize()["timeout"], 7.5)
    self.assertEqual(robot.request_timeout, 7.5)

  def test_a_robot_handed_a_transport_reports_no_request_budget_of_its_own(self):
    """The injected transport owns the wire budget, so the robot must not name one."""
    robot = _flex(ChatterboxTransport())

    self.assertIsNone(robot.request_timeout)

  def test_a_request_timeout_next_to_an_injected_transport_is_refused(self):
    """It cannot reach the wire, and recording it would report a budget nothing uses."""
    with self.assertRaises(ValueError):
      OpentronsFlex(
        deck=FlexDeck(),
        host="localhost",
        transport=ChatterboxTransport(),
        request_timeout=7.5,
      )

  def test_defaults_match_what_the_robot_shipped_with(self):
    robot = _flex()

    self.assertEqual(robot.command_timeout, 120.0)
    self.assertEqual(robot.status_poll_interval, 0.2)
    self.assertEqual(OpentronsFlex(deck=FlexDeck(), host="localhost").request_timeout, 30.0)

  def test_a_budget_of_zero_or_less_is_refused(self):
    """A zero poll interval spins the robot-server; a zero command budget never polls."""
    with self.assertRaises(ValueError):
      _flex(command_timeout=0.0)
    with self.assertRaises(ValueError):
      _flex(command_timeout=-5.0)
    with self.assertRaises(ValueError):
      _flex(status_poll_interval=0.0)
    with self.assertRaises(ValueError):
      OpentronsFlex(deck=FlexDeck(), host="localhost", request_timeout=0.0)

  async def test_a_command_that_never_finishes_gives_up_at_the_command_timeout(self):
    robot = _flex(_ScriptedStatusTransport(), command_timeout=0.3, status_poll_interval=0.01)
    await robot.create_run()

    started = time.monotonic()
    with self.assertRaises(RuntimeError):
      await robot.send_command("home", {})

    self.assertLess(time.monotonic() - started, 5.0)

  async def test_a_caller_may_widen_one_command_past_the_robot_default(self):
    robot = _flex(_ScriptedStatusTransport(), command_timeout=0.1, status_poll_interval=0.01)
    await robot.create_run()

    started = time.monotonic()
    with self.assertRaises(RuntimeError):
      await robot.send_command("home", {}, timeout=0.6)

    self.assertGreater(time.monotonic() - started, 0.5)

  async def test_a_command_that_finished_during_the_last_sleep_is_not_a_timeout(self):
    """Status is read once more once the deadline passes, so no poll interval is blind.

    A wide interval on a slow link would otherwise abort a move the robot completed.
    """
    transport = _ScriptedStatusTransport(succeed_after=1)
    robot = _flex(transport, command_timeout=0.2, status_poll_interval=1.0)
    await robot.create_run()

    result = await robot.send_command("home", {})

    self.assertEqual(result["status"], "succeeded")
    self.assertEqual(transport.status_reads, 2)

  async def test_the_poll_interval_paces_the_status_reads(self):
    transport = _ScriptedStatusTransport()
    robot = _flex(transport, command_timeout=0.4, status_poll_interval=0.01)
    await robot.create_run()

    with self.assertRaises(RuntimeError):
      await robot.send_command("home", {})

    self.assertGreater(transport.status_reads, 10)
