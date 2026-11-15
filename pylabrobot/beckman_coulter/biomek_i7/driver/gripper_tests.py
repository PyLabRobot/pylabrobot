"""The gripper's operations on the simulator: a TiterplateLid taken off its plate at P17 and
put back, by both pods."""

import asyncio
import unittest
from typing import Dict, List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import GripCheckError
from pylabrobot.beckman_coulter.biomek_i7.driver.features.gripper import (
  Gripper,
  biomek_grip_direction,
  held_labware_of,
)
from pylabrobot.beckman_coulter.biomek_i7.layouts import place_labware
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import biomek_labware
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_biomek,
)
from pylabrobot.resources import Plate, beckman_coulter_biomek_96_wellplate_flat

# Biomek's TiterplateLid widths (open 9.401 cm, close 7.592 cm) on the lid, which PyLabRobot sizes
# to its plate's 8.56 cm: per call, as the gripper's defaults are Biomek's margins for labware.
LID_Y_CLEARANCE, LID_SQUEEZE_MM = 4.205, 4.84


class GripperTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Debug mode makes a simulated Home All ~100x slower; see homing_tests.py.
    asyncio.get_running_loop().set_debug(False)
    self.biomek = generic_biomek()
    await self.biomek.setup(precautionary_discard=False)
    self.addAsyncCleanup(self.biomek.stop)
    place_labware(
      self.biomek.deck,
      [{"position": "P17", "class": "BCFlat96", "name": "plate", "lid": "TiterplateLid"}],
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

  async def test_the_left_pod_takes_the_lid_off_and_back_without_checks(self) -> None:
    _, checks = await self.lid_off_and_on("left")
    self.assertEqual(
      checks, []
    )  # Biomek's configuration has the left pod's grip checks off (SensorEnabled False)

  async def test_the_right_pod_takes_the_lid_off_and_back_with_checks(self) -> None:
    _, checks = await self.lid_off_and_on("right")
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
    self.assertIsNone(gripper._pod._held_labware)  # counted from the close, dropped with the grip

  async def test_a_plate_moves_between_positions(self) -> None:
    place_labware(self.biomek.deck, [{"position": "P16", "class": "BCFlat96", "name": "p2"}])
    plate = self.biomek.deck.get_resource("p2")
    await self.biomek.driver.arm("left").move_aside()  # type: ignore[union-attr]
    gripper = self.gripper("right")
    target = self.biomek.deck.get_position("P12")
    await gripper.move_resource(plate, target, pickup_direction="left")
    self.assertIs(plate.parent, target)


class GripDefaultsTests(unittest.TestCase):
  def test_what_is_held_records_the_definitions_it_was_sized_from(self) -> None:
    plate = biomek_labware("BCFlat96", "plate", lid="default")
    held = held_labware_of(plate)
    self.assertEqual(
      held.source,
      "beckman_coulter_biomek_96_wellplate_flat 127.762 x 85.471 x 14.224 mm; "
      f"lid beckman_coulter_biomek_lid_titerplatelid {plate.lid.get_size_x():g} x "
      f"{plate.lid.get_size_y():g} x {plate.lid.get_size_z():g} mm",
    )

  def test_grip_sides_follow_a1(self) -> None:
    plate = beckman_coulter_biomek_96_wellplate_flat("plate")
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
