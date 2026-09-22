import math
import unittest
from typing import Any, List, Optional, Tuple, cast
from unittest.mock import AsyncMock, patch

from pylabrobot.hamilton.protocol.text.framing import assemble_command
from pylabrobot.hamilton.star.device import RECORDING_STAR
from pylabrobot.hamilton.star.driver.features.iswap import iSWAP, iSWAPAxis
from pylabrobot.hamilton.star.driver.simulator import STARSimulationDriver
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.hamilton import STARDeck
from pylabrobot.resources.resource import Resource
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3


async def gripper() -> Tuple[iSWAP, List[str]]:
  """The iSWAP of a simulated device, and the list its commands are recorded in.

  Returns:
    The feature, and every command it sends from here on.
  """
  driver = STARSimulationDriver(deck=STARDeck(), declared_configuration_json=RECORDING_STAR)
  await driver.setup()
  iswap = driver.iswap
  assert iswap is not None

  sent: List[str] = []
  answer = driver.send_command

  async def recorded(
    module: str,
    command: str,
    fmt: Optional[Any] = None,
    subsystem: Optional[str] = None,
    **kwargs: Any,
  ):
    # `fmt` and `subsystem` are the driver's own rather than firmware parameters, so they are taken
    # as `send_command` takes them and never reach the assembler.
    sent.append(assemble_command(module=module, command=command, id_=None, **kwargs))
    return await answer(module=module, command=command, fmt=fmt, subsystem=subsystem, **kwargs)

  driver.send_command = recorded  # type: ignore[assignment]
  return iswap, sent


def jaw_moves(sent: List[str]) -> List[str]:
  """Every jaw move in what was sent."""
  return [command for command in sent if command.startswith("R0GA")]


def moves(sent: List[str]) -> List[str]:
  """Every command in what was sent that puts something somewhere."""
  return [
    command
    for command in sent
    if command[:4] in ("R0YA", "R0ZA", "R0PA", "R0GA") or command[:4] in ("C0JY", "C0JZ")
  ]


class TestParking(unittest.IsolatedAsyncioTestCase):
  """Whether the drives are in the positions parking leaves them in."""

  async def test_the_arm_is_parked_when_every_drive_is_at_its_parking_position(self):
    iswap, _ = await gripper()
    c = iswap.configuration
    assert (
      c.rotation_drive_predefined_y_positions_increments is not None
      and c.rotation_drive_predefined_z_positions_increments is not None
      and c.rotation_drive_predefined_increments is not None
      and c.wrist_drive_predefined_increments is not None
      and c.gripper_drive_predefined_increments is not None
    )

    with patch.object(
      iswap,
      "request_joint_state",
      new=AsyncMock(
        return_value={
          iSWAPAxis.Y: c.y_increments_to_mm(
            c.rotation_drive_predefined_y_positions_increments.parking
          ),
          iSWAPAxis.Z: c.z_increments_to_mm(
            c.rotation_drive_predefined_z_positions_increments.parking
          )
          + c.rotation_drive_z_offset_above_finger,
          iSWAPAxis.ROTATION: c.rotation_drive_increments_to_angle(
            c.rotation_drive_predefined_increments.parking
          ),
          iSWAPAxis.WRIST: c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.parking),
          iSWAPAxis.GRIPPER: c.gripper_increments_to_mm(c.gripper_drive_predefined_increments.home),
        }
      ),
    ):
      self.assertTrue(await iswap.request_parked())

  async def test_the_arm_is_not_parked_when_a_drive_is_moved(self):
    iswap, _ = await gripper()
    c = iswap.configuration
    assert c.rotation_drive_predefined_y_positions_increments is not None
    joints = await iswap.request_joint_state()
    joints[iSWAPAxis.Y] = c.y_increments_to_mm(
      c.rotation_drive_predefined_y_positions_increments.parking - 10
    )
    with patch.object(iswap, "request_joint_state", new=AsyncMock(return_value=joints)):
      self.assertFalse(await iswap.request_parked())

  async def test_z_above_the_parking_position_is_still_parked(self):
    iswap, _ = await gripper()
    c = iswap.configuration
    assert c.rotation_drive_predefined_z_positions_increments is not None
    joints = await iswap.request_joint_state()
    joints[iSWAPAxis.Z] = (
      c.z_increments_to_mm(c.rotation_drive_predefined_z_positions_increments.parking + 10)
      + c.rotation_drive_z_offset_above_finger
    )
    with patch.object(iswap, "request_joint_state", new=AsyncMock(return_value=joints)):
      self.assertTrue(await iswap.request_parked())


class TestJawMoves(unittest.IsolatedAsyncioTestCase):
  """What a jaw move puts on the wire."""

  async def test_a_jaw_move_carries_the_width_it_was_asked_for(self):
    """The width reaches the drive as the position it is driven to. Asserted on the payload rather
    than on the model, because the two are set from different values: the model is told the width
    the caller asked for whatever the drive was sent, so a target lost between them shows up here
    and nowhere else. Both ends of the travel, so a move carrying a constant would fail one."""
    iswap, sent = await gripper()
    c = iswap.configuration

    await iswap.gripper_open()
    await iswap.gripper_close()

    opening, closing = jaw_moves(sent)
    self.assertIn(f"ga{c.gripper_range_increments[1]:05}", opening)
    self.assertIn(f"ga{c.gripper_range_increments[0]:05}", closing)

  async def test_a_close_the_caller_did_not_time_runs_at_half_speed(self):
    """A close carries half the configured default when the caller names no speed, since this
    command feels nothing and meets whatever is there at whatever it was driven at. An opening move
    carries the whole default, which is what separates the two."""
    iswap, sent = await gripper()
    c = iswap.configuration

    await iswap.gripper_open()
    await iswap.gripper_close()

    opening, closing = jaw_moves(sent)
    self.assertIn(f"gv{c.gripper_speed_default_increments:04}", opening)
    self.assertIn(f"gv{c.gripper_speed_default_increments // 2:04}", closing)


class TestYMoves(unittest.IsolatedAsyncioTestCase):
  """What a Y move does before it is sure it can run."""

  async def test_a_refused_y_move_leaves_the_deck_alone(self):
    """Making space moves the channels, so every argument is checked before it runs: a speed the
    drive will not take has to be refused with nothing moved, rather than with the deck rearranged
    for a command that never went out. Driven with a target the channels are in the way of, since
    one they already clear makes space without moving anything and would pass either way."""
    iswap, sent = await gripper()
    pipettes = iswap.arm.pipettes
    assert pipettes is not None
    before = (await pipettes.request_y_positions())[0]

    with self.assertRaises(ValueError):
      await iswap.rotation_drive_move_to_y_position(460.0, make_space=True, speed=500.0)

    self.assertEqual((await pipettes.request_y_positions())[0], before)
    self.assertEqual(moves(sent), [])

  async def test_a_y_move_that_carries_the_arm_behind_the_rail_is_refused(self):
    """The arm rides the carriage, so moving it back carries the pose with it. Turned to rotation
    -90 deg with the wrist at -140 deg, the grip centre stands clear with the drive at 450 mm, and
    137 mm behind the rotation drive's back stop once the drive is there."""
    iswap, sent = await gripper()
    c = iswap.configuration
    assert c.rotation_drive_y_max is not None
    iswap.update_location_by_reference_point(y=450.0)
    iswap.rotation_drive_update_angle(-90.0)
    iswap.wrist_drive_update_angle(-140.0)
    iswap._check_pose_reachable(-90.0, -140.0)

    with self.assertRaises(ValueError):
      await iswap.rotation_drive_move_to_y_position(c.rotation_drive_y_max)
    self.assertEqual(moves(sent), [])

  async def test_a_y_move_that_keeps_the_arm_clear_goes_ahead(self):
    """Pointing to the front, nothing the arm carries reaches behind the drive at any Y."""
    iswap, sent = await gripper()
    c = iswap.configuration
    assert c.rotation_drive_y_max is not None
    iswap.rotation_drive_update_angle(0.0)
    iswap.wrist_drive_update_angle(-45.0)

    await iswap.rotation_drive_move_to_y_position(c.rotation_drive_y_max)
    self.assertEqual(len([m for m in moves(sent) if m.startswith("R0YA")]), 1)

  async def test_only_park_puts_the_arm_in_its_parking_position(self):
    """The parking pose leaves the wrist joint behind the rotation drive's back stop, which only the
    park command may do. A parked arm moves forward along Y, and is refused a move to the back stop
    that would leave it in that pose."""
    iswap, sent = await gripper()
    c = iswap.configuration
    assert c.rotation_drive_y_max is not None
    self.assertEqual(await iswap.rotation_drive_request_y_position(), c.rotation_drive_y_max)

    with self.assertRaises(ValueError):
      await iswap.rotation_drive_move_to_y_position(c.rotation_drive_y_max)
    self.assertEqual(moves(sent), [])

    await iswap.rotation_drive_move_to_y_position(c.rotation_drive_y_max - 5.0)
    self.assertEqual(len([m for m in moves(sent) if m.startswith("R0YA")]), 1)


class TestRotationWithoutADeck(unittest.IsolatedAsyncioTestCase):
  """A driver given no deck models no arm: a relative angle needs none, an absolute one is on it."""

  async def test_a_relative_rotation_goes_ahead(self):
    iswap, sent = await gripper()
    iswap._driver.deck = None

    await iswap.rotate_to_angles(rotation_relative_angle="front", raise_features=False)

    self.assertEqual(len([move for move in moves(sent) if move.startswith("R0PA")]), 1)

  async def test_an_absolute_rotation_is_refused_before_anything_moves(self):
    iswap, sent = await gripper()
    iswap._driver.deck = None

    with self.assertRaises(RuntimeError):
      await iswap.rotate_to_angles(rotation_absolute_angle="front", raise_features=False)

    self.assertEqual(moves(sent), [])


class TestTheModelIsSavedWhole(unittest.IsolatedAsyncioTestCase):
  """A deck carrying the iSWAP is read back with the arm as it was: its parts and the angles its
  drives last reported from `serialize`, and the gripper's jaw width from the state."""

  async def test_a_deck_carrying_the_iswap_deserializes(self):
    iswap, _ = await gripper()
    deck = iswap._driver.deck
    head = iswap.resource
    assert deck is not None and head is not None

    loaded = Resource.deserialize(deck.serialize())
    loaded.load_all_state(deck.serialize_all_state())

    self.assertEqual(loaded.get_resource(head.name).serialize(), head.serialize())


class TestPosesAgainstTheRail(unittest.IsolatedAsyncioTestCase):
  """What the X-arm at the back of the deck lets the arm reach."""

  async def test_a_carriage_on_its_own_back_stop_may_still_turn_sideways(self):
    """Parked at the back, the arm lying along the deck reaches nothing behind the carriage, so the
    pose stands. It used to be refused: the limit came from a conversion rounded to two places and
    the position from one rounded again to a single place, leaving the carriage a hundredth of a
    millimetre past its own maximum."""
    iswap, _ = await gripper()
    c = iswap.configuration
    assert c.rotation_drive_predefined_increments is not None
    assert c.wrist_drive_predefined_increments is not None

    self.assertEqual(await iswap.rotation_drive_request_y_position(), c.rotation_drive_y_max)
    for stop in ("left", "right"):
      angle = c.rotation_drive_increments_to_angle(
        c.rotation_drive_predefined_increments.left
        if stop == "left"
        else c.rotation_drive_predefined_increments.right
      )
      straight = c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.straight)
      iswap._check_pose_reachable(angle, straight)

  async def test_a_pose_that_reaches_behind_the_rail_is_still_refused(self):
    """The slack is half an increment, not a licence: link 2 folded square backwards puts the grip
    centre 137.7 mm behind the carriage, where the X-arm is."""
    iswap, _ = await gripper()
    c = iswap.configuration
    assert c.rotation_drive_predefined_increments is not None
    assert c.wrist_drive_predefined_increments is not None

    angle = c.rotation_drive_increments_to_angle(c.rotation_drive_predefined_increments.right)
    wrist = c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.left)
    with self.assertRaises(ValueError):
      iswap._check_pose_reachable(angle, wrist)

  async def test_a_grip_centre_just_in_front_of_the_rail_is_allowed(self):
    """Rotation -3.5 deg with the wrist at 121 deg puts the grip centre 6.2 mm in front of the
    carriage's back stop. Measuring the tool from the gripper's corner rather than its wrist put it
    6.2 mm behind, and refused a pose the arm can reach."""
    iswap, _ = await gripper()
    c = iswap.configuration

    self.assertEqual(await iswap.rotation_drive_request_y_position(), c.rotation_drive_y_max)
    iswap._check_pose_reachable(-3.5, 121.0)


class TestToolCentrePoint(unittest.IsolatedAsyncioTestCase):
  """Where the kinematics put the grip centre, against the arm the model builds."""

  async def test_the_grip_centre_is_the_tool_length_from_the_wrist(self):
    """The firmware reports the tool length from the wrist joint, so a pose worked out at any angles
    carries the grip centre exactly that far past it."""
    iswap, _ = await gripper()
    c = iswap.configuration
    assert c.tool_length is not None

    for rotation in (-90.0, 0.0, 45.0, 90.0):
      for wrist in (-135.0, -45.0, 45.0, 121.0):
        with self.subTest(rotation=rotation, wrist=wrist):
          pose = iswap._compute_pose_at_angles(rotation, wrist)
          w, t = pose.wrist_joint_location, pose.gripper_center_location
          self.assertAlmostEqual(math.hypot(t.x - w.x, t.y - w.y), c.tool_length, places=2)

  async def test_the_pose_puts_the_grip_centre_where_the_gripper_has_it(self):
    """`request_pose` and the gripper resource describe the same arm from the same joints, so they
    agree on where it grips. They parted by 13 mm when the tool was measured from the gripper's
    corner instead of the joint it hangs on at link 1's far end."""
    iswap, _ = await gripper()
    g = cast(MechanicalGripper, iswap.gripper)

    pose = await iswap.request_pose()
    turned = g.get_absolute_rotation().get_rotation_matrix()
    # The grip centre is stated from the joint the gripper hangs on, so that joint carries it across.
    model = g.get_absolute_location() + Coordinate(
      *matrix_vector_multiply_3x3(turned, (g.proximal_joint + g.tool_center_point).vector())
    )
    self.assertAlmostEqual(pose.gripper_center_location.x, model.x, places=2)
    self.assertAlmostEqual(pose.gripper_center_location.y, model.y, places=2)


class TestLostSteps(unittest.IsolatedAsyncioTestCase):
  """What a drive whose counters have parted tells whoever asks."""

  async def test_a_width_read_says_when_the_drive_has_lost_steps(self):
    """The two counters part when the drive has been driven into something, and that is the only
    sign of it. A width read goes through them rather than off the wire on its own, so a caller who
    only ever asks how wide the jaws are is still told."""
    iswap, _ = await gripper()
    parted = iswap.configuration.gripper_counter_drift_increments + 100

    async def counters_apart(**kwargs):
      return {"rg": [13100, 13100 - parted]}

    iswap._driver.send_command = counters_apart  # type: ignore[assignment]
    with self.assertLogs("pylabrobot.hamilton.star.driver.features.iswap", "WARNING") as logged:
      await iswap.gripper_request_width()
    self.assertIn("lost steps", "".join(logged.output))


class TestGripperDirections(unittest.IsolatedAsyncioTestCase):
  """Where a named gripper direction sends the wrist."""

  async def test_every_named_pose_lands_on_a_stored_stop(self):
    """Three rotation stops against four directions, each resolving to one of the four increments
    this arm stores for its wrist. Nothing is pushed there: the conversion interpolates against the
    same stops, so a stop's own angle converts back to its own increment. A conversion anchored on
    the motor's zero instead would miss two of the four by around a degree, which is more than a
    rounding tolerance would carry."""
    iswap, _ = await gripper()
    c = iswap.configuration
    assert c.rotation_drive_predefined_increments is not None
    assert c.wrist_drive_predefined_increments is not None
    stored = {
      c.wrist_drive_predefined_increments.right,
      c.wrist_drive_predefined_increments.straight,
      c.wrist_drive_predefined_increments.left,
      c.wrist_drive_predefined_increments.reverse,
    }

    for rotation, rotation_increments in (
      ("left", c.rotation_drive_predefined_increments.left),
      ("front", c.rotation_drive_predefined_increments.front),
      ("right", c.rotation_drive_predefined_increments.right),
    ):
      for direction in ("right", "back", "left", "front"):
        increments = iswap._resolve_gripper_direction_increments(direction, rotation_increments)
        self.assertIn(increments, stored, f"{rotation}/{direction}")


class TestParked(unittest.IsolatedAsyncioTestCase):
  """Parked is every drive on the stop the firmware parks it against, worked out from the pose."""

  async def test_it_never_asks_the_master_for_its_parked_flag(self):
    """`C0 RG` is answered wrongly by the firmware, so nothing may read it."""
    iswap, sent = await gripper()

    await iswap.request_parked()

    self.assertEqual([command for command in sent if command.startswith("C0RG")], [])

  async def test_a_parked_arm_is_parked_and_a_moved_one_is_not(self):
    iswap, _ = await gripper()
    c = iswap.configuration

    await iswap.park()
    self.assertTrue(await iswap.request_parked())

    assert c.rotation_drive_predefined_y_positions_increments is not None
    await iswap.rotation_drive_move_to_y_position(
      c.y_increments_to_mm(c.rotation_drive_predefined_y_positions_increments.parking) - 50.0
    )
    self.assertFalse(await iswap.request_parked())

  async def test_the_jaws_are_part_of_it(self):
    """Parking closes them, and the gripper's table names that stop its home."""
    iswap, _ = await gripper()
    c = iswap.configuration

    await iswap.park()
    assert c.gripper_drive_predefined_increments is not None
    home = c.gripper_increments_to_mm(c.gripper_drive_predefined_increments.home)
    await iswap.gripper_move_to_jaw_position(home + 5.0)

    self.assertFalse(await iswap.request_parked())

  async def test_a_drive_within_the_tolerance_still_counts_as_parked(self):
    iswap, _ = await gripper()
    c = iswap.configuration
    await iswap.park()

    # Seated on the model rather than moved to: the parking stop is past the far end of the Y the
    # drive takes commands for, so it cannot be sent there.
    assert c.rotation_drive_predefined_y_positions_increments is not None
    stop = c.rotation_drive_predefined_y_positions_increments.parking
    iswap.update_location_by_reference_point(y=c.y_increments_to_mm(stop + 2))
    self.assertTrue(await iswap.request_parked())
    iswap.update_location_by_reference_point(y=c.y_increments_to_mm(stop + 20))
    self.assertFalse(await iswap.request_parked())

  async def test_a_parked_arm_above_its_z_stop_is_parked_and_below_it_is_not(self):
    """Parked from 284 mm, the device stands at `rz` 26660 against a stop of 25400."""
    iswap, _ = await gripper()
    c = iswap.configuration
    await iswap.park()
    assert c.rotation_drive_predefined_z_positions_increments is not None
    stop = c.rotation_drive_predefined_z_positions_increments.parking
    offset = c.rotation_drive_z_offset_above_finger

    iswap.update_location_by_reference_point(z=c.z_increments_to_mm(stop + 1260) + offset)
    self.assertTrue(await iswap.request_parked())
    iswap.update_location_by_reference_point(z=c.z_increments_to_mm(stop - 20) + offset)
    self.assertFalse(await iswap.request_parked())


class TestSafeZ(unittest.IsolatedAsyncioTestCase):
  """What the move every lateral move waits on costs."""

  async def test_going_to_safe_z_reads_the_drive_once(self):
    """The Z move reads the drive back and records it, so the height comes off the model rather
    than from a second `RZ` for the same answer. Asserted on the count because that is the whole
    of it, and on the value because a model read that had drifted would be worse than the read it
    saves."""
    iswap, sent = await gripper()

    height = await iswap.rotation_drive_move_to_safe_z_height()

    self.assertEqual(len([command for command in sent if command.startswith("R0RZ")]), 1)
    self.assertEqual(height, await iswap.rotation_drive_request_z_position())


if __name__ == "__main__":
  unittest.main()
