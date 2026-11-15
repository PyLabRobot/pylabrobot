import unittest
from typing import List, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.planning import check, pod_model
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.obstacles import Obstacles, obstacle_part
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import generic_configuration

Point = Tuple[float, float]


def inside(point: Point, outline: Sequence[Point]) -> bool:
  """Whether `point` is inside the convex `outline` (either winding)."""
  signs = set()
  for (ax, ay), (bx, by) in zip(outline, [*outline[1:], outline[0]]):
    cross = (bx - ax) * (point[1] - ay) - (by - ay) * (point[0] - ax)
    signs.add(cross > 0)
  return len(signs) == 1


class TurnSweepTests(unittest.TestCase):
  """Our sweep of a gripper turn is never smaller than Biomek's: Biomek bounds a turn between two
  quarter turns by the axis-aligned box over each turning corner's arc (its corners at both ends
  and the point at its radius on each axis it crosses), and any other turn by the exact turning
  box."""

  def setUp(self) -> None:
    self.config = generic_configuration().pod("right")
    held = g.HeldLabware(length=12.776, width=8.548, height=1.5)
    self.turn = [pod_model.GripperState(40.0, 30.0, gr, 9.0, held) for gr in (0.0, 90.0)]
    self.x = 100.0
    # The probes high and at the back, out of the way.
    self.pipettor = pod_model.PipettorState(
      tuple(10.0 + 0.9 * i for i in range(8)), (39.0,) * 8, (0.0,) * 8
    )

  def parts_at(self, gr: float) -> List[pod_model.Part]:
    a, _ = self.turn
    return pod_model.gripper_parts(
      self.config, self.x, pod_model.GripperState(a.gy, a.gz, gr, a.gg, a.held)
    )

  def biomeks_box(self) -> Tuple[float, float, float, float]:
    """The box over the held labware's corners' arcs from 0 to 90 degrees (dense sampling of
    each corner's arc reaches its bound within a micron)."""
    xs: List[float] = []
    ys: List[float] = []
    for step in range(9001):
      held = next(p for p in self.parts_at(step / 100) if p.name == "held labware")
      xs += [px for px, _ in held.outline]
      ys += [py for _, py in held.outline]
    return min(xs), max(xs), min(ys), max(ys)

  def in_biomeks_box_only(self) -> Optional[Point]:
    """A corner of Biomek's box (0.1 cm in) that no part of the gripper reaches on its way round."""
    x0, x1, y0, y1 = self.biomeks_box()
    swept = [p.outline for gr in [i / 4 for i in range(361)] for p in self.parts_at(gr)]
    corners = [(x, y) for x in (x0 + 0.1, x1 - 0.1) for y in (y0 + 0.1, y1 - 0.1)]
    return next((c for c in corners if not any(inside(c, outline) for outline in swept)), None)

  def test_a_quarter_turn_is_bounded_at_least_by_biomeks_box(self) -> None:
    point = self.in_biomeks_box_only()
    assert point is not None
    held = next(p for p in self.parts_at(0.0) if p.name == "held labware")
    box = obstacle_part(
      "in Biomek's box only",
      (point[0] - 0.05, point[0] + 0.05),
      (point[1] - 0.05, point[1] + 0.05),
      (held.z_low, held.z_low + 0.5),
    )
    start, end = (pod_model.PodState(self.x, s, self.pipettor) for s in self.turn)
    found = check.deck_collisions(self.config, start, end, Obstacles((), (box,), ()))
    self.assertTrue(found, f"a turn through {point} is not refused")

  def test_biomeks_box_holds_every_pose_of_the_turn(self) -> None:
    # The reference itself: every sampled pose of the held labware lies in the box.
    x0, x1, y0, y1 = self.biomeks_box()
    for gr in (0.0, 30.0, 45.0, 90.0):
      held = next(p for p in self.parts_at(gr) if p.name == "held labware")
      for px, py in held.outline:
        self.assertTrue(x0 - 1e-9 <= px <= x1 + 1e-9 and y0 - 1e-9 <= py <= y1 + 1e-9)
    self.assertGreater((x1 - x0) * (y1 - y0), 0.0)

  def test_quarter_turns_read_back_from_counts_are_quarter_turns(self) -> None:
    # A pose read from the boards: 0 and 90 degrees within a count, not exactly.
    a, b = (self.config.to_value("GR", self.config.to_counts("GR", gr)) for gr in (0.0, 90.0))
    self.assertNotEqual((a, b), (0.0, 90.0))
    turn = [pod_model.GripperState(s.gy, s.gz, gr, s.gg, s.held) for s, gr in zip(self.turn, (a, b))]
    start, end = (pod_model.PodState(self.x, s, self.pipettor) for s in turn)
    self.assertTrue(check.quarter_turn_parts(self.config, start, end))

  def test_part_of_biomeks_box_is_never_reached_by_the_turn(self) -> None:
    # What the test above needs: a place in Biomek's box that the turning gripper never covers.
    self.assertIsNotNone(self.in_biomeks_box_only())


class BundleTests(unittest.TestCase):
  """A Span-8's tubing bundle meets only what the gripper holds, as Biomek pairs it."""

  def test_only_held_labware_meets_the_bundle(self) -> None:
    square = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0))
    bundle = pod_model.Part("tubing bundle", square, 10.0, pod_model.OPEN)
    held = pod_model.Part("held labware", square, 9.0, 11.0)
    fingers = pod_model.Part("fingers", square, 9.0, 11.0)
    self.assertEqual(check._own_pairs([held], [], bundle), [("held labware", "tubing bundle")])
    self.assertEqual(check._own_pairs([fingers], [], bundle), [])
    below = pod_model.Part("held labware", square, 8.0, 9.5)
    self.assertEqual(check._own_pairs([below], [], bundle), [])

  def test_only_a_span8_has_a_bundle(self) -> None:
    pipettor = pod_model.PipettorState(tuple(10.0 + 0.9 * i for i in range(8)), (30.0,) * 8, (0.0,) * 8)
    config = generic_configuration()
    self.assertIsNotNone(pod_model.bundle_part(config.pod("right"), 100.0, pipettor))
    head = pod_model.PipettorState((30.0,), (30.0,), (0.0,), None)
    self.assertIsNone(pod_model.bundle_part(config.pod("left"), 100.0, head))


class PlanningPadTests(unittest.TestCase):
  """The planner checks the gripper padded below by `AdditionalSafeHeightGripper`; a move check
  does not."""

  def test_the_planner_keeps_the_gripper_its_pad_above_a_box(self) -> None:
    config = generic_configuration().pod("right")
    pad = pod_model.planning_pad(config)
    self.assertGreater(pad, 0.3)
    pipettor = pod_model.PipettorState(tuple(10.0 + 0.9 * i for i in range(8)), (39.0,) * 8, (0.0,) * 8)
    grip = pod_model.GripperState(40.0, 30.0, 0.0, 9.0, None)
    start, end = (pod_model.PodState(x, grip, pipettor) for x in (100.0, 120.0))
    bottom = min(p.z_low for p in pod_model.gripper_parts(config, 100.0, grip))
    # A box under the whole travel, its top 0.3 cm below the gripper's bottom.
    box = obstacle_part("P1", (60.0, 180.0), (0.0, 80.0), (bottom - 5.0, bottom - 0.3))
    world = Obstacles((), (box,), ())
    self.assertEqual(check.deck_collisions(config, start, end, world), [])
    self.assertFalse(check.move_clear(config, start, end, world))


if __name__ == "__main__":
  unittest.main()
