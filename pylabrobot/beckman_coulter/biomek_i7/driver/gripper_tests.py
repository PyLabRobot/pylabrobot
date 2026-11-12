"""The gripper's operations on the simulator, against Biomek's logged lid grips at P17 (both pods,
a transfection method, 2026-09-22): TiterplateLid on a Thermo96VB_PP."""

import asyncio
import unittest
from typing import Dict, List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import GripCheckError
from pylabrobot.beckman_coulter.biomek_i7.driver.features.gripper import (
  SQUEEZE_MM,
  Y_CLEARANCE,
  Gripper,
  biomek_grip_direction,
)
from pylabrobot.beckman_coulter.biomek_i7.layouts import place_labware
from pylabrobot.resources import Plate, beckman_coulter_biomek_96_wellplate_vb
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_biomek,
)

# Biomek's TiterplateLid widths (open 9.401 cm, close 7.592 cm) on the lid, which PyLabRobot sizes
# to its plate's 8.56 cm: per call, as the gripper's defaults are Biomek's margins for labware.
LID_Y_CLEARANCE, LID_SQUEEZE_MM = 4.205, 4.84

# The logged grips' counts (X, GY, GR at the grip; GG open; GZ down; GG close), host_command.
LOGGED = {
  "left": {"X": 241420, "GR": -141581, "GG open": 62705, "GZ": -21774, "GG close": 29537},
  "right": {
    "X": -50856,
    "GY": 89997,
    "GR": -209637,
    "GG open": 62594,
    "GZ": -21576,
    "GG close": 29427,
  },
}


class GripperTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Debug mode makes a simulated Home All ~100x slower; see homing_tests.py.
    asyncio.get_running_loop().set_debug(False)
    self.biomek = generic_biomek()
    await self.biomek.setup(precautionary_discard=False)
    self.addAsyncCleanup(self.biomek.stop)
    place_labware(
      self.biomek.deck,
      [{"position": "P17", "class": "Thermo96VB_PP", "name": "plate", "lid": "TiterplateLid"}],
    )
    plate = self.biomek.deck.get_resource("plate")
    assert isinstance(plate, Plate) and plate.lid is not None
    self.plate, self.lid = plate, plate.lid

  def record(self, side: str) -> List[Tuple[Dict[str, int], Dict[str, float]]]:
    pod = self.biomek.driver.arm(side)  # type: ignore[union-attr]
    moves: List[Tuple[Dict[str, int], Dict[str, float]]] = []
    original = pod.move_axes

    async def move_axes(targets, speed=1000, forces=None, sensors=None):  # type: ignore[no-untyped-def]
      moves.append((dict(targets), dict(forces or {})))
      return await original(targets, speed, forces, sensors)

    pod.move_axes = move_axes  # type: ignore[method-assign]
    return moves

  def gripper(self, side: str) -> Gripper:
    gripper = self.biomek.driver.arm(side).gripper  # type: ignore[union-attr]
    assert gripper is not None
    return gripper

  def checks(self, side: str) -> List[str]:
    gripper = self.gripper(side)
    found: List[str] = []
    original = gripper.check_grip

    async def check_grip(expect_labware: bool) -> None:
      found.append("G" if expect_labware else "U")
      await original(expect_labware)

    gripper.check_grip = check_grip  # type: ignore[method-assign]
    return found

  async def lid_off_and_on(self, side: str) -> Tuple[List, List[str]]:
    other = "right" if side == "left" else "left"
    await self.biomek.driver.arm(other).move_aside()  # type: ignore[union-attr]
    gripper = self.gripper(side)
    moves, checks = self.record(side), self.checks(side)
    await gripper.pick_up_resource(self.lid, y_clearance=LID_Y_CLEARANCE, squeeze_mm=LID_SQUEEZE_MM)
    self.assertIs(gripper.held_resource, self.lid)
    self.assertIsNone(self.plate.lid)
    # In the tree it hangs from the gripper's hand, lifted clear of its plate.
    self.assertIs(self.lid.parent, self.biomek.gripper_models[side].hand)
    device = self.biomek
    plate_top = self.plate.get_location_wrt(device).z + self.plate.get_size_z()
    self.assertGreater(self.lid.get_location_wrt(device).z, plate_top)
    lid_x = self.lid.get_location_wrt(device, x="c").x
    self.assertAlmostEqual(lid_x, self.plate.get_location_wrt(device, x="c").x, delta=0.5)
    await gripper.return_resource()
    self.assertIs(self.plate.lid, self.lid)
    self.assertIsNone(gripper.held_resource)
    return moves, checks

  def assert_logged(self, side: str, moves: List) -> None:
    logged = LOGGED[side]
    close = next(t for t, f in moves if "GG" in f)
    i = next(i for i, (t, f) in enumerate(moves) if t is close)
    self.assertEqual(close["GG"], logged["GG close"])
    self.assertEqual(moves[i - 1][0], {"GZ": logged["GZ"]})
    self.assertEqual(moves[i - 2][0], {"GG": logged["GG open"]})
    travel = moves[i - 3][0]
    for axis in ("X", "GY", "GR"):
      if axis in logged:
        self.assertAlmostEqual(travel[axis], logged[axis], delta=3, msg=axis)

  async def test_the_left_pod_takes_the_lid_as_logged_without_checks(self) -> None:
    moves, checks = await self.lid_off_and_on("left")
    self.assert_logged("left", moves)
    self.assertEqual(
      checks, []
    )  # Biomek's configuration has the left pod's grip checks off (SensorEnabled False)

  async def test_the_right_pod_takes_the_lid_as_logged_with_checks(self) -> None:
    moves, checks = await self.lid_off_and_on("right")
    self.assert_logged("right", moves)
    self.assertEqual("".join(checks), "UGGGGU")

  async def test_delid_move_the_plate_and_relid(self) -> None:
    # The validation notebook's sequence: the left pod takes the lid and moves aside holding it
    # (its gripper kept below the 96-head), the right pod moves the plate to P16, the left pod
    # puts the lid back on it there.
    driver = self.biomek.driver
    assert driver is not None
    left, right = driver.arm("left"), driver.arm("right")
    await right.move_aside()
    await self.gripper("left").pick_up_resource(
      self.lid, y_clearance=LID_Y_CLEARANCE, squeeze_mm=LID_SQUEEZE_MM
    )
    await left.move_aside()
    p16 = self.biomek.deck.get_position("P16")
    await self.gripper("right").move_resource(self.plate, p16)
    self.assertIs(self.plate.parent, p16)
    # Turned to 180 degrees the right hand reaches 9.2 cm past its bridge, over the left pod's
    # P16: aside and parked (GR 90) it is clear.
    await right.move_aside()
    await right.park()
    await self.gripper("left").place_resource(self.plate)
    self.assertIs(self.plate.lid, self.lid)
    self.assertIsNone(self.gripper("left").held_resource)
    await self.biomek.park()

  async def test_a_close_on_nothing_is_refused_on_every_pod(self) -> None:
    await self.biomek.driver.arm("right").move_aside()  # type: ignore[union-attr]
    gripper = self.gripper("left")
    self.biomek.driver.simulated_grip = None  # type: ignore[union-attr]  # nothing between the fingers
    with self.assertRaises(GripCheckError):
      await gripper.pick_up_resource(
        self.lid, y_clearance=LID_Y_CLEARANCE, squeeze_mm=LID_SQUEEZE_MM
      )

  async def test_a_plate_moves_between_positions(self) -> None:
    place_labware(self.biomek.deck, [{"position": "P16", "class": "Thermo96VB_PP", "name": "p2"}])
    plate = self.biomek.deck.get_resource("p2")
    await self.biomek.driver.arm("left").move_aside()  # type: ignore[union-attr]
    gripper = self.gripper("right")
    target = self.biomek.deck.get_position("P12")
    await gripper.move_resource(plate, target, pickup_direction="left")
    self.assertIs(plate.parent, target)


class GripDefaultsTests(unittest.TestCase):
  def test_the_default_widths_are_biomeks(self) -> None:
    # Thermo96VB_PP (8.56 cm): Biomek opens to 9.706 cm and closes towards 7.897 cm.
    plate = beckman_coulter_biomek_96_wellplate_vb("plate")
    self.assertAlmostEqual(plate.get_size_y() + 2 * Y_CLEARANCE, 97.06)
    self.assertAlmostEqual(plate.get_size_y() - 2 * SQUEEZE_MM, 78.97)

  def test_grip_sides_follow_a1(self) -> None:
    plate = beckman_coulter_biomek_96_wellplate_vb("plate")
    # Where the fingers point: "A1 near" has the hand on A1's side, the left at rotation 0.
    self.assertEqual(biomek_grip_direction(plate, "A1 near"), "right")
    self.assertEqual(biomek_grip_direction(plate, "A1 away from"), "left")
    self.assertEqual(biomek_grip_direction(plate, "Wide A1 near"), "front")
    plate.rotate(z=180)
    self.assertEqual(biomek_grip_direction(plate, "A1 near"), "left")
    plate.rotate(z=90)
    self.assertEqual(biomek_grip_direction(plate, "A1 near"), "front")
    with self.assertRaises(ValueError):
      biomek_grip_direction(plate, "sideways")


if __name__ == "__main__":
  unittest.main()
