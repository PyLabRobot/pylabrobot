"""The pod's planned moves on a deck (Biomek's planner, design.md I12): which moves are planned and
exempt nothing, which are an operation's own and work in its position, and the way out of a
position a pod was left standing in."""

import unittest
from typing import List, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver import liquid_tests
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.features.head96 import COLUMNS, ROWS, mandrel_name
from pylabrobot.beckman_coulter.biomek_i7.driver.planning import check, obstacles, pod_model, search
from pylabrobot.beckman_coulter.biomek_i7.driver.pod_move_tests import PodMoveTest
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import biomek_tip
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_configuration
from pylabrobot.resources import cor_96_wellplate_360uL_Fb

Move = Tuple[dict, Tuple[str, ...]]


class PlannedMoveTests(PodMoveTest):
  setup_deck = liquid_tests.LiquidTests.setup_deck

  def record(self, driver, side: str) -> List[Move]:  # type: ignore[no-untyped-def]
    """Each move the pod makes, with the deck positions exempt for it."""
    pod = self.pod(driver, side)
    moves: List[Move] = []
    original = pod.move_axes

    async def move_axes(targets, speed=1000, **kwargs):  # type: ignore[no-untyped-def]
      moves.append((dict(targets), pod._working_at))
      return await original(targets, speed, **kwargs)

    pod.move_axes = move_axes  # type: ignore[method-assign, assignment]
    return moves

  def tips(self, driver) -> None:  # type: ignore[no-untyped-def]
    head = self.pod(driver, "left").head96
    assert head is not None
    head._set_tips(
      {
        mandrel_name(r, c): biomek_tip("T230", f"t_{r}_{c}")
        for r in range(ROWS)
        for c in range(COLUMNS)
      }
    )

  async def test_the_approach_exempts_nothing_and_the_descent_its_position(self) -> None:
    driver, lh = await self.setup_deck()  # type: ignore[misc]
    self.tips(driver)
    moves = self.record(driver, "left")
    await lh.head96.aspirate(lh.deck.get_resource("Reservoir"), 50, liquid_height=2.0)
    travel = [w for t, w in moves if {"X", "Y"} & set(t)]
    self.assertTrue(travel)
    self.assertEqual(set(travel), {()})  # the approach: planned, nothing exempt
    descent = next(i for i, (t, _) in enumerate(moves) if {"X", "Y"} & set(t)) + 1
    self.assertEqual(set(moves[descent][0]), {"Z"})
    self.assertEqual(set(moves[descent][1]), {"P8"})  # the step's own moves: its position

  async def test_an_operation_in_labware_on_no_position_leaves_out_only_that_labware(self) -> None:
    driver, lh = await self.setup_deck()  # type: ignore[misc]
    plate = cor_96_wellplate_360uL_Fb("loose")
    lh.deck.assign_child_resource(plate, location=lh.deck.pin_location(30, 5))
    right = self.pod(driver, "right")
    with right.working_at(plate.get_well("A1")):
      world = right._world(2, right._working_at)
    assert world is not None
    names = {p.name for p in world.deck}
    self.assertNotIn("loose", names)
    self.assertIn("P6", names)

  async def test_a_pod_left_in_a_position_rises_straight_out_of_it(self) -> None:
    driver, lh = await self.setup_deck()  # type: ignore[misc]
    self.tips(driver)
    left = self.pod(driver, "left")
    d = left.declared
    assert d is not None
    reservoir = lh.deck.get_resource("Reservoir")
    plan = lh.head96.plan_liquid(
      "aspirate", reservoir, 10, lh.head96._settings(liquid_height=2.0), 100
    )
    await left._move_over({"X": d.to_counts("X", plan.x), "Y": d.to_counts("Y", plan.y)})
    with left.working_at(reservoir):
      await left.move_axes({"Z": d.to_counts("Z", 10.0)})  # left in it, as by a low end height
    with self.assertRaises(check.DeckCollisionError):
      await left.move_axes({"X": d.to_counts("X", plan.x - 10.0)})  # not straight up: refused
    moves = self.record(driver, "left")
    await left._move_over({"X": d.to_counts("X", plan.x - 10.0)})
    # Out of P8 straight up, exempting only P8, to the travel height; then the travel, nothing
    # exempt.
    self.assertEqual((set(moves[0][0]), moves[0][1]), ({"Z"}, ("P8",)))
    self.assertEqual(moves[0][0]["Z"], d.to_counts("Z", left._traverse_heights()["Z"]))
    self.assertEqual([(set(t), w) for t, w in moves[1:]], [({"X"}, ())])


class RiseAndDescentTests(PodMoveTest):
  """Biomek's rise and descent stages: the search when the straight move up or down is not clear."""

  async def pod_under_a_ledge(self):  # type: ignore[no-untyped-def]
    """The left pod holding a lid at GR 0 (it reaches past the fingertips, to X 66.9 with the pod
    at X 60), a ledge above the lid's far end only: clear of every part where it stands."""
    driver = await self.homed()
    left = self.pod(driver, "left")
    d = left.declared
    assert d is not None and left.gripper is not None
    ledge = obstacles.obstacle_part("ledge", (63.0, 70.0), (44.0, 52.0), (14.5, 16.0))
    driver.obstacles = lambda pod: obstacles.Obstacles((), (ledge,), ())
    await left.move_axes(
      {"X": d.to_counts("X", 60.0), "GY": d.to_counts("GY", 48.0), "GR": d.to_counts("GR", 0.0)}
    )
    await left.move_axes({"GZ": d.to_counts("GZ", 20.0), "GG": d.to_counts("GG", 7.592)})
    left.gripper.set_held_labware(g.HeldLabware(length=12.776, width=8.548, height=0.9))
    return left, d

  async def test_a_rise_blocked_overhead_goes_out_from_under_first(self) -> None:
    left, d = await self.pod_under_a_ledge()
    moves = record(left)
    await left._rise({"GZ": 30.0}, 1000)
    self.assertEqual(moves[-1], {"GZ": d.to_counts("GZ", 30.0)})
    self.assertNotIn("GZ", moves[0])  # out from under the ledge first, then up
    self.assertEqual(left.cpositions["GZ"][1], d.to_counts("GZ", 30.0))

  async def test_a_descent_into_a_box_is_searched_then_refused(self) -> None:
    driver = await self.homed()
    left = self.pod(driver, "left")
    d = left.declared
    assert d is not None
    table = obstacles.obstacle_part("P1", (0.0, 200.0), (0.0, 80.0), (-1.0, 10.0))
    driver.obstacles = lambda pod: obstacles.Obstacles((), (table,), ("P1",))
    await left._read_unknown_positions(pumps=True)  # reading moves nothing
    before = left.cpositions
    moves = record(left)
    with self.assertRaisesRegex(check.DeckCollisionError, "no clear path for the descent"):
      await left._move_over({"X": d.to_counts("X", 60.0)}, z_final=5.0)
    # The call is planned whole before anything moves (as Biomek's planner: no path,
    # nothing moves): the descent has none, so the rise and the travel are not made either.
    self.assertEqual(moves, [])
    self.assertEqual(left.cpositions, before)


class SearchTests(PodMoveTest):
  async def test_the_span8s_search_moves_its_probes_by_y(self) -> None:
    driver = await self.homed()
    right = self.pod(driver, "right")
    d = right.declared
    assert d is not None
    driver.obstacles = lambda pod: obstacles.Obstacles()
    await right.move_axes({"GR": d.to_counts("GR", d.park["GR"])})  # reads every position
    y1 = right.cpositions["Y1"][1]
    target = d.to_counts("Y", d.to_value("Y", y1) + 2.0)
    # A Span-8's Y names probe 1 and moves all eight (`_planned`): the search's Y step as well.
    path = right._search({"Y": target})
    self.assertEqual(path, [{"Y": target}])


class OpenSetTests(unittest.TestCase):
  """Biomek's open set (spec §5.3): the same costs tie; a node rises only past a strictly larger
  cost and sinks below a child that is not larger."""

  def order(self, pushes: List[Tuple[str, float]]) -> List[str]:
    open_set = search.OpenSet()
    for node, cost in pushes:
      open_set.push(node, cost)
    out: List[str] = []
    while open_set:
      popped = open_set.pop()
      assert isinstance(popped, str)
      out.append(popped)
    return out

  def test_cheapest_first_and_equal_costs_in_arrival_order(self) -> None:
    self.assertEqual(self.order([("a", 2.0), ("b", 1.0), ("c", 1.0)]), ["b", "c", "a"])
    self.assertEqual(self.order([("a", 1.0), ("b", 1.0), ("c", 1.0)]), ["a", "b", "c"])

  def test_costs_the_same_value_do_not_pass_each_other(self) -> None:
    # 1e-13 apart: the same value, so the later never rises past the earlier.
    self.assertEqual(self.order([("a", 1.0 + 1e-13), ("b", 1.0)]), ["a", "b"])

  def test_a_cheaper_way_lowers_an_open_node(self) -> None:
    self.assertEqual(self.order([("a", 5.0), ("b", 3.0), ("a", 1.0)]), ["a", "b"])
    self.assertEqual(self.order([("a", 1.0), ("a", 5.0)]), ["a"])  # never raised


class OffQuarterTests(PodMoveTest):
  async def test_a_gripper_off_a_quarter_turn_first_turns_to_one(self) -> None:
    driver = await self.homed()
    right = self.pod(driver, "right")
    d = right.declared
    assert d is not None
    driver.obstacles = lambda pod: obstacles.Obstacles()
    await right.move_axes({"GR": d.to_counts("GR", 45.0)})
    x = d.to_value("X", right.cpositions["X"][1])
    path = right._search({"X": d.to_counts("X", x - 2.0)})
    # Off a quarter turn Biomek's search offers only the turns to the quarters either side.
    self.assertEqual(set(path[0]), {"GR"})
    self.assertIn(round(d.to_value("GR", path[0]["GR"])), (0, 90))
    self.assertEqual(path[-1], {"X": d.to_counts("X", x - 2.0)})


class OtherPodTests(PodMoveTest):
  """The other pod in planning (Biomek's planner, spec §6.2): a wall over all Y and Z."""

  async def test_the_other_pod_is_walled_where_it_stands(self) -> None:
    driver = await self.homed()
    left, right = self.pod(driver, "left"), self.pod(driver, "right")
    await right._read_unknown_positions()
    assert left.rail is not None
    self.assertEqual(left._other_pod_wall(), left.rail.occupied("right"))

  async def test_the_wall_is_in_the_planning_world_only(self) -> None:
    driver = await self.homed()
    left = self.pod(driver, "left")
    await self.pod(driver, "right")._read_unknown_positions()
    driver.obstacles = lambda pod: obstacles.Obstacles()
    world = left._planning_world()
    assert world is not None
    self.assertEqual([p.name for p in world.structure], ["the right pod"])
    self.assertEqual(left._world(left.pod_number), obstacles.Obstacles())


def record(pod) -> List[dict]:  # type: ignore[no-untyped-def]
  """The targets of each move `pod` makes from now on."""
  moves: List[dict] = []
  original = pod.move_axes

  async def move_axes(targets, speed=1000, **kwargs):  # type: ignore[no-untyped-def]
    moves.append(dict(targets))
    return await original(targets, speed, **kwargs)

  pod.move_axes = move_axes
  return moves


class EscapeTests(unittest.TestCase):
  def setUp(self) -> None:
    self.config = generic_configuration().pod("left")
    self.pipettor = pod_model.PipettorState((30.0,), (5.0,), (0.0,), None)

  def world(self, positions: Tuple[str, ...]) -> obstacles.Obstacles:
    box = obstacles.obstacle_part("P1", (0.0, 60.0), (0.0, 60.0), (-1.0, 8.0))
    return obstacles.Obstacles((), (box,), positions)

  def test_a_part_in_a_position_rises_to_its_top(self) -> None:
    out = check.escape(
      self.config, pod_model.PodState(30.0, None, self.pipettor), self.world(("P1",))
    )
    assert out is not None
    self.assertEqual((out.holders, out.gripper), (("P1",), 0.0))
    world = self.world(("P1",))

    def at(z: float) -> List[Tuple[str, str]]:
      state = pod_model.PipettorState((30.0,), (z,), (0.0,), None)
      return check.deck_collisions_at(self.config, pod_model.PodState(30.0, None, state), world)

    self.assertEqual(at(5.0 + out.pipettor[0]), [])
    self.assertNotEqual(at(5.0 + out.pipettor[0] - 0.01), [])

  def test_a_clear_pod_has_nothing_to_leave(self) -> None:
    clear = pod_model.PipettorState((30.0,), (30.0,), (0.0,), None)
    out = check.escape(self.config, pod_model.PodState(30.0, None, clear), self.world(("P1",)))
    self.assertEqual(out, check.Escape((), (0.0,), 0.0))

  def test_no_way_out_of_anything_but_a_position(self) -> None:
    self.assertIsNone(
      check.escape(self.config, pod_model.PodState(30.0, None, self.pipettor), self.world(()))
    )

  def test_only_the_span8_probes_inside_rise(self) -> None:
    # As the logged escapes: probes 2-4 left in the plate rise, the others stay.
    config = generic_configuration().pod("right")
    ys = tuple(20.0 + 0.9 * i for i in range(8))
    zs = (30.0, 5.0, 5.0, 5.0, 30.0, 30.0, 30.0, 30.0)
    state = pod_model.PodState(30.0, None, pod_model.PipettorState(ys, zs, (0.0,) * 8))
    box = obstacles.obstacle_part("P1", (0.0, 60.0), (0.0, 60.0), (-1.0, 8.0))
    out = check.escape(config, state, obstacles.Obstacles((), (box,), ("P1",)))
    assert out is not None
    self.assertEqual(out.holders, ("P1",))
    self.assertEqual([i for i, rise in enumerate(out.pipettor) if rise > 0], [1, 2, 3])


if __name__ == "__main__":
  unittest.main()
