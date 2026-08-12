"""Tests for the Flex direct-motion surface: head jog + position read
(``_FlexHead.position``/``_FlexHead.move_to``) and gripper motion + jaw
control (``FlexGripper.move_to``/``grip``/``open_jaw``).

Drives ``OpentronsFlex.setup()`` with an injected ``ChatterboxTransport`` and
asserts the exact wire commands: ``savePosition`` reads, ``moveToCoordinates``
axis merging and ``minimumZHeight``/``speed`` handling, the ``robot/moveTo``
extension-mount params, jaw force validation before any wire command, and the
robot-software version gate on the robot/* command family.
"""

import asyncio
import unittest
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_gripper import FlexGripper, _require_robot_commands
from pylabrobot.opentrons.flex_head import _FlexHead
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.opentrons.transport import ChatterboxTransport
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.flex_deck import FlexDeck


def _flex_with_gripper(**transport_kwargs) -> Tuple[OpentronsFlex, ChatterboxTransport]:
  """An ``OpentronsFlex`` with a single-channel right-mount pipette and a
  gripper, returning the transport too so a test can inspect recorded
  commands. ``transport_kwargs`` are forwarded to ``ChatterboxTransport``.
  """
  transport = ChatterboxTransport(
    pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")],
    gripper=True,
    **transport_kwargs,
  )
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  return flex, transport


def _head(flex: OpentronsFlex) -> _FlexHead:
  head = flex.right
  assert head is not None
  return head


def _gripper(flex: OpentronsFlex) -> FlexGripper:
  gripper = flex.gripper
  assert gripper is not None
  return gripper


def _cmds(transport: ChatterboxTransport, command_type: str) -> List[Dict[str, Any]]:
  return [c for c in transport.commands if c["commandType"] == command_type]


class _VersionedTransport(ChatterboxTransport):
  """Chatterbox whose ``/health`` reports a caller-chosen robot software
  version, so tests can drive the robot/* version gate.
  """

  def __init__(self, api_version: str, **kwargs) -> None:
    super().__init__(**kwargs)
    self._api_version = api_version

  async def get(self, path: str) -> Dict[str, Any]:
    if path == "/health":
      return {
        "api_version": self._api_version,
        "robot_model": "OT-3 Standard",
        "name": "chatterbox",
      }
    return await super().get(path)


def _flex_with_version(api_version: str) -> Tuple[OpentronsFlex, ChatterboxTransport]:
  transport = _VersionedTransport(
    api_version,
    pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")],
    gripper=True,
  )
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  return flex, transport


class TestHeadPosition(unittest.TestCase):
  """position() reads the head's pose from a savePosition command result."""

  def test_position_reads_save_position_result(self):
    flex, transport = _flex_with_gripper(saved_position={"x": 10.0, "y": 20.0, "z": 30.5})
    asyncio.run(flex.setup())
    try:
      head = _head(flex)

      position = asyncio.run(head.position())

      self.assertEqual(position, Coordinate(10.0, 20.0, 30.5))
      save_cmds = _cmds(transport, "savePosition")
      self.assertEqual(len(save_cmds), 1)
      self.assertEqual(save_cmds[0]["params"], {"pipetteId": head.pipette_id})
    finally:
      asyncio.run(flex.stop())

  def test_position_default_saved_position(self):
    flex, _transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      position = asyncio.run(_head(flex).position())
      self.assertEqual(position, Coordinate(100.0, 100.0, 100.0))
    finally:
      asyncio.run(flex.stop())


class TestHeadMoveTo(unittest.TestCase):
  """move_to fills unspecified axes from the current position and sends ONE
  moveToCoordinates command. No tip is mounted in any of these tests: jogging
  is for teaching/recovery and must not require one.
  """

  def test_partial_axes_merge_saved_with_given(self):
    flex, transport = _flex_with_gripper(saved_position={"x": 10.0, "y": 20.0, "z": 30.0})
    asyncio.run(flex.setup())
    try:
      head = _head(flex)

      asyncio.run(head.move_to(x=50.0))

      self.assertEqual(len(_cmds(transport, "savePosition")), 1)
      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(
        move_cmds[0]["params"],
        {
          "pipetteId": head.pipette_id,
          "coordinates": {"x": 50.0, "y": 20.0, "z": 30.0},
          "minimumZHeight": 120.0,
        },
      )
    finally:
      asyncio.run(flex.stop())

  def test_all_axes_given_skips_position_read(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))

      self.assertEqual(len(_cmds(transport, "savePosition")), 0)
      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(move_cmds[0]["params"]["coordinates"], {"x": 1.0, "y": 2.0, "z": 3.0})
    finally:
      asyncio.run(flex.stop())

  def test_minimum_z_height_override(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0, minimum_z_height=35.0))

      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertEqual(move_cmds[0]["params"]["minimumZHeight"], 35.0)
    finally:
      asyncio.run(flex.stop())

  def test_speed_passthrough(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0, speed=40.0))

      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertEqual(move_cmds[0]["params"]["speed"], 40.0)
    finally:
      asyncio.run(flex.stop())

  def test_speed_omitted_by_default(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))

      move_cmds = _cmds(transport, "moveToCoordinates")
      self.assertNotIn("speed", move_cmds[0]["params"])
    finally:
      asyncio.run(flex.stop())

  def test_no_axes_raises_before_any_wire_command(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      with self.assertRaises(ValueError):
        asyncio.run(_head(flex).move_to())

      self.assertEqual(len(_cmds(transport, "savePosition")), 0)
      self.assertEqual(len(_cmds(transport, "moveToCoordinates")), 0)
    finally:
      asyncio.run(flex.stop())


class TestGripperMoveTo(unittest.TestCase):
  """Gripper move_to sends robot/moveTo with the extension mount."""

  def test_exact_wire_params(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).move_to(100.0, 50.0, 75.5))

      move_cmds = _cmds(transport, "robot/moveTo")
      self.assertEqual(len(move_cmds), 1)
      self.assertEqual(
        move_cmds[0]["params"],
        {"mount": "extension", "destination": {"x": 100.0, "y": 50.0, "z": 75.5}},
      )
    finally:
      asyncio.run(flex.stop())

  def test_speed_passthrough(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).move_to(1.0, 2.0, 3.0, speed=25.0))

      move_cmds = _cmds(transport, "robot/moveTo")
      self.assertEqual(move_cmds[0]["params"]["speed"], 25.0)
    finally:
      asyncio.run(flex.stop())


class TestGripperJaw(unittest.TestCase):
  """grip() validates force before the wire; open_jaw() homes the jaw open."""

  def test_grip_without_force_sends_empty_params(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).grip())

      close_cmds = _cmds(transport, "robot/closeGripperJaw")
      self.assertEqual(len(close_cmds), 1)
      self.assertEqual(close_cmds[0]["params"], {})
    finally:
      asyncio.run(flex.stop())

  def test_grip_force_boundaries_accepted(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)

      asyncio.run(gripper.grip(force=2.0))
      asyncio.run(gripper.grip(force=30.0))

      close_cmds = _cmds(transport, "robot/closeGripperJaw")
      self.assertEqual(len(close_cmds), 2)
      self.assertEqual(close_cmds[0]["params"], {"force": 2.0})
      self.assertEqual(close_cmds[1]["params"], {"force": 30.0})
    finally:
      asyncio.run(flex.stop())

  def test_grip_force_out_of_range_raises_before_any_wire_command(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)

      for force in (1.9, 30.1, 0.0, -5.0):
        with self.assertRaises(OpentronsError):
          asyncio.run(gripper.grip(force=force))

      self.assertEqual(len(_cmds(transport, "robot/closeGripperJaw")), 0)
    finally:
      asyncio.run(flex.stop())

  def test_open_jaw_sends_open_gripper_jaw(self):
    flex, transport = _flex_with_gripper()
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())

      open_cmds = _cmds(transport, "robot/openGripperJaw")
      self.assertEqual(len(open_cmds), 1)
      self.assertEqual(open_cmds[0]["params"], {})
    finally:
      asyncio.run(flex.stop())


class TestRobotCommandsVersionGate(unittest.TestCase):
  """robot/* commands require robot software 8.2.0+; dev builds and offline
  stand-ins (non-release version strings) are exempt. Head motion
  (savePosition/moveToCoordinates) is NOT gated -- it predates the robot/*
  family.
  """

  def _assert_no_robot_commands(self, transport: ChatterboxTransport) -> None:
    robot_cmds = [c for c in transport.commands if c["commandType"].startswith("robot/")]
    self.assertEqual(len(robot_cmds), 0, "no robot/* wire command may be sent")

  def test_old_release_raises_and_sends_no_robot_commands(self):
    flex, transport = _flex_with_version("8.1.0")
    asyncio.run(flex.setup())
    try:
      gripper = _gripper(flex)

      with self.assertRaises(OpentronsError) as ctx:
        asyncio.run(gripper.move_to(1.0, 2.0, 3.0))
      self.assertIn("8.2.0", str(ctx.exception))
      with self.assertRaises(OpentronsError):
        asyncio.run(gripper.grip())
      with self.assertRaises(OpentronsError):
        asyncio.run(gripper.open_jaw())

      self._assert_no_robot_commands(transport)
    finally:
      asyncio.run(flex.stop())

  def test_minimum_release_passes(self):
    flex, transport = _flex_with_version("8.2.0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_dev_build_passes(self):
    flex, transport = _flex_with_version("0.0.0.dev0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_default_chatterbox_passes(self):
    flex, transport = _flex_with_gripper()  # /health reports "dry-run"
    asyncio.run(flex.setup())
    try:
      asyncio.run(_gripper(flex).open_jaw())
      self.assertEqual(len(_cmds(transport, "robot/openGripperJaw")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_head_motion_is_not_gated(self):
    flex, transport = _flex_with_version("8.1.0")
    asyncio.run(flex.setup())
    try:
      asyncio.run(_head(flex).move_to(x=1.0, y=2.0, z=3.0))
      self.assertEqual(len(_cmds(transport, "moveToCoordinates")), 1)
    finally:
      asyncio.run(flex.stop())

  def test_unknown_version_raises(self):
    with self.assertRaises(OpentronsError):
      _require_robot_commands("robot/moveTo", None)


if __name__ == "__main__":
  unittest.main()
