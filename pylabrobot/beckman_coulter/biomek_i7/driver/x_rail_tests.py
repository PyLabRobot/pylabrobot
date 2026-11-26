"""The shared X rail: Biomek's pod-against-pod rule with the gripper, the reservations for moves
in flight, waiting versus refusing, and moves refused before anything is sent."""

import asyncio
import unittest
from typing import Optional

from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.homing_tests import SimulatedRunTest
from pylabrobot.beckman_coulter.biomek_i7.driver.x_rail import RailCollisionError, RailPose, XRail
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_configuration

CONFIG = generic_configuration()
LEFT, RIGHT = CONFIG.pod("left"), CONFIG.pod("right")
PODS = {"left": LEFT, "right": RIGHT}
PARK_GG = 7.889  # the park's grip width, cm
PLATE = g.HeldLabware(length=12.776, width=8.548)  # an SBS plate, held lengthwise


def pose(side: str, counts: int, gr: Optional[float] = 0.0, held=None) -> RailPose:
  """A pod at X `counts`, its gripper turned to `gr` degrees (None: not known)."""
  gripper = None if gr is None else g.GripperPose(gr, PARK_GG, held)
  return RailPose(PODS[side].to_value("X", counts), gripper)


def rail() -> XRail:
  return XRail({"left": LEFT, "right": RIGHT})


class PodGeometryTests(unittest.TestCase):
  def test_a_box_turning_covers_every_angle_between(self) -> None:
    box = (1.0, 2.0, 0.0, 0.0)
    self.assertEqual(g.turned_x_extent(box, 0, 0), (1.0, 2.0))
    lo, hi = g.turned_x_extent(box, 180, 180)
    self.assertAlmostEqual(lo, -2.0)
    self.assertAlmostEqual(hi, -1.0)
    lo, hi = g.turned_x_extent(box, 0, 180)
    self.assertAlmostEqual(lo, -2.0)
    self.assertAlmostEqual(hi, 2.0)

  def test_the_right_gripper_reaches_past_its_bridge_towards_the_left_pod(self) -> None:
    model = g.GripperModel.from_bif(RIGHT.gripper_model)
    bridge = g.bridge_x_range(RIGHT.geometry, 100.0)
    facing_left = g.gripper_x_range(RIGHT.geometry, model, 100.0, g.GripperPose(180, PARK_GG))
    self.assertAlmostEqual(bridge[0] - facing_left[0], 9.2, 1)
    holding = g.gripper_x_range(RIGHT.geometry, model, 100.0, g.GripperPose(180, PARK_GG, PLATE))
    self.assertLess(holding[0], facing_left[0] - 4)  # the plate sticks out past the fingers

  def test_a_turn_is_swept_along_its_path(self) -> None:
    model = g.GripperModel.from_bif(RIGHT.gripper_model)
    a, b = (
      g.Gripper(model, g.GripperPose(90, PARK_GG)),
      g.Gripper(model, g.GripperPose(-90, PARK_GG)),
    )
    swept = g.swept_x_range(RIGHT.geometry, 100.0, 100.0, a, b)
    at_zero = g.gripper_x_range(RIGHT.geometry, model, 100.0, g.GripperPose(0, PARK_GG))
    self.assertGreaterEqual(swept[1], at_zero[1])  # passes through 0 degrees on the way


class RailTests(unittest.IsolatedAsyncioTestCase):
  def test_the_bridges_meet_one_grid_cell_short_of_touching(self) -> None:
    """The right gripper facing right (GR 0) stays inside its bridge: the bridges decide. They
    touch at a combined 250,807 counts; Biomek compares edges as 1/16 cm cells, so with the
    right pod here the last move allowed is a combined 250,767."""
    r = rail()
    r.update("right", pose("right", -9258))
    r.check("left", pose("left", 0), pose("left", 241420))  # Biomek's own move
    r.check("left", pose("left", 0), pose("left", 250767 - 9258))
    with self.assertRaises(RailCollisionError):
      r.check("left", pose("left", 0), pose("left", 250768 - 9258))
    with self.assertRaises(RailCollisionError):  # touching, and in the same cell
      r.check("left", pose("left", 0), pose("left", 250807 - 9258))

  def test_the_parked_right_gripper_takes_the_left_pod_closer_in(self) -> None:
    """Parked facing the front (GR 90), the right gripper reaches 0.86 cm past its bridge."""
    r = rail()
    r.update("right", pose("right", -9258, gr=90))
    with self.assertRaises(RailCollisionError):
      r.check("left", pose("left", 0), pose("left", 250807 - 9258))
    r.check("left", pose("left", 0), pose("left", 250807 - 9258 - 2300))

  def test_turning_the_gripper_into_the_other_pod_is_refused(self) -> None:
    r = rail()
    r.update("left", pose("left", int((70 - 10.519) * 2560)))  # deck X 70: bridge to 93.6
    right_x = int((110 - 146.40669375) * 2560)  # deck X 110: bridge from 95.7
    r.check("right", pose("right", right_x, gr=0), pose("right", right_x, gr=0))
    with self.assertRaises(RailCollisionError):
      r.check("right", pose("right", right_x, gr=0), pose("right", right_x, gr=180))

  def test_an_unknown_gripper_pose_counts_every_pose(self) -> None:
    r = rail()
    right_x = int((110 - 146.40669375) * 2560)
    r.update("right", pose("right", right_x, gr=None))
    with self.assertRaises(RailCollisionError):  # it could be facing left
      r.check("left", pose("left", 0), pose("left", int((70 - 10.519) * 2560)))

  def test_either_pod_is_checked(self) -> None:
    # Counts run from each pod's own home: the right pod's -2,560 is its park at the right end.
    r = rail()
    r.update("left", pose("left", 241420))
    r.check("right", pose("right", -2560), pose("right", 241420 - 250717))
    with self.assertRaises(RailCollisionError):
      r.check("right", pose("right", -2560), pose("right", 241420 - 250718))

  def test_a_pod_cannot_pass_through_the_other(self) -> None:
    r = rail()
    r.update("left", pose("left", 100000))
    with self.assertRaises(RailCollisionError):
      r.check("right", pose("right", -2560), pose("right", -230000))

  async def test_crossing_only_a_running_move_waits_for_it(self) -> None:
    r = rail()
    r.update("left", pose("left", 200000))  # far over; retreating home
    r.update("right", pose("right", -2560))
    order = []

    async def right_moves() -> None:
      async with r.moving("right", pose("right", -2560), pose("right", -60000)):
        order.append("right")

    async with r.moving("left", pose("left", 200000), pose("left", 0)):
      task = asyncio.create_task(right_moves())
      await asyncio.sleep(0)
      order.append("left")
    await task
    self.assertEqual(order, ["left", "right"])

  async def test_crossing_where_a_running_move_ends_is_refused(self) -> None:
    r = rail()
    r.update("left", pose("left", 0))
    r.update("right", pose("right", -2560))
    async with r.moving("left", pose("left", 0), pose("left", 200000)):
      with self.assertRaises(RailCollisionError):
        async with r.moving("right", pose("right", -2560), pose("right", -60000)):
          pass

  async def test_a_read_coming_back_mid_move_keeps_the_reservation(self) -> None:
    r = rail()
    r.update("left", pose("left", 0))
    r.update("right", pose("right", -2560))
    async with r.moving("right", pose("right", -2560), pose("right", -150000)):
      r.update("right", pose("right", -2560))
      with self.assertRaises(RailCollisionError):
        r.check("left", pose("left", 0), pose("left", 150000))

  async def test_a_failed_move_keeps_its_reservation_until_read(self) -> None:
    r = rail()
    r.update("left", pose("left", 0))
    r.update("right", pose("right", -2560))
    with self.assertRaises(OSError):
      async with r.moving("left", pose("left", 0), pose("left", 200000)):
        raise OSError("lost the board")
    self.assertEqual(r.occupied("left"), r.swept("left", pose("left", 0), pose("left", 200000)))
    r.update("left", pose("left", 150000))
    self.assertEqual(r.occupied("left"), r.range_at("left", pose("left", 150000)))

  def test_an_unknown_position_allows_only_moving_away(self) -> None:
    r = rail()
    r.update("left", pose("left", 100000))
    r.check("left", pose("left", 100000), pose("left", 50000))
    with self.assertRaisesRegex(RuntimeError, "not known"):
      r.check("left", pose("left", 100000), pose("left", 120000))

  async def test_an_unknown_position_is_read_first(self) -> None:
    r = rail()
    r.update("left", pose("left", 0))

    async def read_right() -> RailPose:
      return pose("right", -2560)

    r.readers["right"] = read_right
    async with r.moving("left", pose("left", 0), pose("left", 100000)):
      pass
    self.assertIsNotNone(r.occupied("right"))

  def test_no_declared_geometry_no_moves(self) -> None:
    r = XRail({"left": LEFT, "right": None})
    with self.assertRaisesRegex(RuntimeError, "no declared geometry"):
      r.check("left", pose("left", 0), pose("left", 1000))

  def test_a_lone_pod_is_free(self) -> None:
    XRail({"left": LEFT}).check("left", pose("left", 0), pose("left", 300000))


class RailOnTheInstrumentTests(SimulatedRunTest):
  """The rail guard on its own: with clearing the way off, a move into the other pod is refused."""

  async def test_a_move_into_the_other_pod_sends_no_motion(self) -> None:
    driver = await self.start(True)
    driver.make_way = False
    await driver.home()
    assert driver.left_x_arm and driver.right_x_arm
    await driver.right_x_arm.move_axes({"X": -100000})
    self.mark(driver)
    with self.assertRaises(RailCollisionError):
      await driver.left_x_arm.move_axes({"X": 200000})
    sent = {m[4] for m in self.usb(driver, "multichannel").requests[self.marks["multichannel"] :]}
    self.assertNotIn(0x40, sent)  # no Set Move Parameters
    self.assertNotIn(0x50, sent)  # no Move All
    await driver.left_x_arm.move_axes({"X": 140000})
    self.assertEqual(driver.left_x_arm.cpositions["X"], (140000, 140000))

  async def test_turning_a_gripper_into_the_other_pod_sends_no_motion(self) -> None:
    driver = await self.start(True)
    driver.make_way = False
    await driver.home()
    assert driver.left_x_arm and driver.right_x_arm
    left, right = driver.left_x_arm, driver.right_x_arm
    await left.move_axes({"X": int((70 - 10.519) * 2560)})
    await right.move_axes({"X": int((110 - 146.40669375) * 2560)})
    facing_left = RIGHT.to_counts("GR", 180.0)
    self.mark(driver)
    with self.assertRaises(RailCollisionError):
      await right.move_axes({"GR": facing_left})
    sent = {m[4] for m in self.usb(driver, "span8").requests[self.marks["span8"] :]}
    self.assertNotIn(0x40, sent)
    self.assertNotIn(0x50, sent)

  async def test_both_pods_moving_at_once_cannot_cross(self) -> None:
    driver = await self.start(True)
    await driver.home()
    assert driver.left_x_arm and driver.right_x_arm and driver.rail
    results = await asyncio.gather(
      driver.left_x_arm.move_axes({"X": 150000}),
      driver.right_x_arm.move_axes({"X": -150000}),
      return_exceptions=True,
    )
    # Which move reaches the rail first is the event loop's choice: the second is refused while the
    # first runs, or, once it has finished, clears the idle pod aside. Either way they never cross.
    for result in results:
      if isinstance(result, BaseException) and not isinstance(result, RailCollisionError):
        raise result
    left, right = driver.rail.occupied("left"), driver.rail.occupied("right")
    assert left is not None and right is not None
    self.assertLess(left[1], right[0])


class ClearTheWayTests(SimulatedRunTest):
  """Biomek's clearing of the way: an idle other pod in a move's way moves aside on its own first, to the
  next 1/16 cm grid line past the move's zone plus 1/64 cm, its gripper turned to the nearest
  quarter."""

  async def pods(self):  # type: ignore[no-untyped-def]
    driver = await self.start(True)
    await driver.home()
    assert driver.left_x_arm and driver.right_x_arm and driver.rail
    return driver, driver.left_x_arm, driver.right_x_arm, driver.rail

  async def test_the_other_pod_moves_to_the_grid_edge_of_the_zone(self) -> None:
    driver, left, right, rail = await self.pods()
    await right.move_axes({"X": -100000})
    start = rail.occupied("left")
    await left.move_axes({"X": 200000})
    assert start is not None
    zone = g.union(start, rail.occupied("left") or start)
    edge = (g.grid_cell(zone[1]) + 1) / 16 + 1 / 64
    low = rail.occupied("right")
    assert low is not None
    self.assertAlmostEqual(low[0], edge, delta=1 / 2560)  # within a count of X
    self.assertEqual(left.cpositions["X"][1], 200000)
    self.assertFalse(g.grid_overlap(rail.occupied("left") or zone, low))

  async def test_a_turn_towards_the_other_pod_moves_it_aside(self) -> None:
    driver, left, right, rail = await self.pods()
    await left.move_axes({"X": int((70 - 10.519) * 2560)})
    await right.move_axes({"X": int((110 - 146.40669375) * 2560)})
    before = left.cpositions["X"][1]
    await right.move_axes({"GR": RIGHT.to_counts("GR", 180.0)})
    self.assertLess(left.cpositions["X"][1], before)  # the left pod went left, out of the turn
    left_range, right_range = rail.occupied("left"), rail.occupied("right")
    assert left_range is not None and right_range is not None
    self.assertFalse(g.grid_overlap(left_range, right_range))

  async def test_the_other_pods_gripper_turns_to_the_nearest_quarter(self) -> None:
    driver, left, right, rail = await self.pods()
    await right.move_axes({"X": -100000, "GR": RIGHT.to_counts("GR", 100.0)})
    await left.move_axes({"X": 200000})
    self.assertAlmostEqual(RIGHT.to_value("GR", right.cpositions["GR"][1]), 90.0, delta=0.01)

  async def test_a_busy_pod_is_not_moved(self) -> None:
    driver, left, right, rail = await self.pods()
    await right.move_axes({"X": -100000})
    with right.working_at("P1"):  # a step of its own under way
      with self.assertRaises(RailCollisionError):
        await left.move_axes({"X": 200000})
    self.assertEqual(right.cpositions["X"][1], -100000)

  async def test_no_way_out_is_refused(self) -> None:
    driver, left, right, rail = await self.pods()
    self.mark(driver)
    with self.assertRaisesRegex(RailCollisionError, "cannot move out of the other pod's way"):
      await left.move_axes({"X": LEFT.to_counts("X", LEFT.limits["X"][1])})
    sent = {m[4] for m in self.usb(driver, "multichannel").requests[self.marks["multichannel"] :]}
    self.assertNotIn(0x50, sent)  # the left pod sent no Move All

  async def test_labware_taken_counts_from_the_next_move_on(self) -> None:
    # What the gripper takes changes the pod's range: the rail counts it only once a move with
    # it is checked (the grip's close, or the first move after), which moves the other pod aside.
    driver, left, right, rail = await self.pods()
    await left.move_axes({"X": int((70 - 10.519) * 2560), "GR": LEFT.to_counts("GR", 0.0)})
    await right.move_axes(
      {"X": int((112 - 146.40669375) * 2560), "GR": RIGHT.to_counts("GR", 180.0)}
    )
    before, left_x = rail.occupied("right"), left.cpositions["X"][1]
    assert right.gripper is not None
    right.gripper.set_held_labware(g.HeldLabware(length=12.776, width=8.548, height=0.9))
    self.assertEqual(rail.occupied("right"), before)  # not yet: no move has been checked with it
    await right.move_axes({"GZ": right.cpositions["GZ"][1] - 100})
    grown = rail.occupied("right")
    assert before is not None and grown is not None
    self.assertLess(grown[0], before[0])  # the lid reaches further left
    self.assertLess(left.cpositions["X"][1], left_x)  # and the left pod moved aside for it
    left_range = rail.occupied("left")
    assert left_range is not None
    self.assertFalse(g.grid_overlap(left_range, grown))

  async def test_off_it_is_the_rail_guards_refusal(self) -> None:
    driver, left, right, rail = await self.pods()
    driver.make_way = False
    await right.move_axes({"X": -100000})
    with self.assertRaises(RailCollisionError):
      await left.move_axes({"X": 200000})
    self.assertEqual(right.cpositions["X"][1], -100000)


if __name__ == "__main__":
  unittest.main()
