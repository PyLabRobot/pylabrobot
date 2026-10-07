"""Collision checks: the exact test against known answers, the tree against brute force, the solids a
tree is made of, and sweeps that catch what their ends miss."""

import itertools
import math
import random
import time
import unittest
from typing import List, Tuple

from pylabrobot.resources.collision import (
  CONTACT,
  Group,
  Obstacles,
  Piece,
  Pose,
  Segment,
  check,
  declared_hulls,
  distance,
  moving,
  on_axes,
  profiled,
  solid_pieces,
  straight,
  turning,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb
from pylabrobot.resources.hamilton.tip_carriers import hamilton_tip_carrier_L5
from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_1000uL
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.rotation import Rotation

Vec = Tuple[float, float, float]


def box(lo: Vec, size: Vec, turn: float = 0.0) -> List[Vec]:
  """A box's corners, turned `turn` degrees about its own centre."""
  c = tuple(lo[k] + size[k] / 2 for k in range(3))
  pose = Pose(turn, c)  # type: ignore[arg-type]
  return [
    pose.apply((lo[0] + dx, lo[1] + dy, lo[2] + dz))
    for dx in (0, size[0])
    for dy in (0, size[1])
    for dz in (0, size[2])
  ]


def separated_by_an_axis(a: List[Vec], b: List[Vec]) -> bool:
  """The separating axis test for two boxes: the oracle GJK is held to."""

  def axes(points: List[Vec]) -> List[Vec]:
    o = points[0]
    return [
      tuple(points[i][k] - o[k] for k in range(3))  # type: ignore[misc]
      for i in (4, 2, 1)  # the x, y and z edges from the first corner, by `box`'s order
    ]

  def cross(u: Vec, v: Vec) -> Vec:
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])

  candidates = axes(a) + axes(b) + [cross(u, v) for u in axes(a) for v in axes(b)]
  for axis in candidates:
    if sum(c * c for c in axis) < 1e-9:
      continue
    pa = [sum(p[k] * axis[k] for k in range(3)) for p in a]
    pb = [sum(p[k] * axis[k] for k in range(3)) for p in b]
    if max(pa) < min(pb) or max(pb) < min(pa):
      return True
  return False


def aabb_gap(a_lo: Vec, a_size: Vec, b_lo: Vec, b_size: Vec) -> float:
  gaps = [
    max(0.0, b_lo[k] - (a_lo[k] + a_size[k]), a_lo[k] - (b_lo[k] + b_size[k])) for k in range(3)
  ]
  return math.sqrt(sum(g * g for g in gaps))


def block(name: str, lo: Vec, size: Vec) -> Piece:
  return Piece(Resource(name, *size), box(lo, size))


class DistanceTests(unittest.TestCase):
  def test_the_distance_between_boxes_is_the_one_their_faces_say(self):
    rng = random.Random(1)
    for _ in range(300):
      a_lo = tuple(rng.uniform(-50, 50) for _ in range(3))
      b_lo = tuple(rng.uniform(-50, 50) for _ in range(3))
      a_size = tuple(rng.uniform(1, 40) for _ in range(3))
      b_size = tuple(rng.uniform(1, 40) for _ in range(3))
      expected = aabb_gap(a_lo, a_size, b_lo, b_size)  # type: ignore[arg-type]
      found = distance(box(a_lo, a_size), box(b_lo, b_size))  # type: ignore[arg-type]
      self.assertAlmostEqual(found, expected, places=5)

  def test_turned_boxes_meet_exactly_when_no_axis_separates_them(self):
    rng = random.Random(2)
    met = 0
    for _ in range(400):
      a = box(
        tuple(rng.uniform(-30, 30) for _ in range(3)),  # type: ignore[arg-type]
        tuple(rng.uniform(5, 40) for _ in range(3)),  # type: ignore[arg-type]
        rng.uniform(0, 180),
      )
      b = box(
        tuple(rng.uniform(-30, 30) for _ in range(3)),  # type: ignore[arg-type]
        tuple(rng.uniform(5, 40) for _ in range(3)),  # type: ignore[arg-type]
        rng.uniform(0, 180),
      )
      apart = distance(a, b)
      if apart > 1e-6:
        self.assertTrue(separated_by_an_axis(a, b))
      elif apart == 0.0:
        met += 1
        self.assertFalse(separated_by_an_axis(a, b))
    self.assertGreater(met, 50)  # both answers were tried

  def test_stopping_early_still_says_far_enough(self):
    a, b = box((0, 0, 0), (10, 10, 10)), box((100, 0, 0), (10, 10, 10))
    self.assertGreater(distance(a, b, stop_beyond=5.0), 5.0)


class PoseTests(unittest.TestCase):
  def test_poses_compose_and_undo(self):
    a = Pose(30.0, (10.0, 5.0, 0.0), (1.0, 2.0, 3.0))
    b = Pose(-75.0, (-4.0, 8.0, 0.0), (0.5, -1.0, 0.0))
    p = (3.0, -7.0, 2.0)
    both = a.then(b).apply(p)
    self.assertTrue(all(abs(u - v) < 1e-9 for u, v in zip(both, b.apply(a.apply(p)))))
    back = a.inverse().apply(a.apply(p))
    self.assertTrue(all(abs(u - v) < 1e-9 for u, v in zip(back, p)))
    slid = a.after(2.0, -1.0).apply(p)
    self.assertTrue(all(abs(u - v) < 1e-9 for u, v in zip(slid, a.apply((5.0, -8.0, 2.0)))))


class ObstacleTreeTests(unittest.TestCase):
  def test_the_tree_finds_what_brute_force_finds_and_looks_at_little(self):
    rng = random.Random(3)
    pieces = [
      block(f"b{i}", (rng.uniform(0, 1000), rng.uniform(0, 600), rng.uniform(0, 200)), (20, 20, 20))
      for i in range(3000)
    ]
    tree = Obstacles(pieces)
    for _ in range(50):
      lo = (rng.uniform(0, 1000), rng.uniform(0, 600), rng.uniform(0, 200))
      hi = (lo[0] + 40, lo[1] + 40, lo[2] + 40)
      tree.visited = 0
      found = {id(p) for p in tree.near(lo, hi)}
      brute = {
        id(p) for p in pieces if all(p.lo[k] <= hi[k] and lo[k] <= p.hi[k] for k in range(3))
      }
      self.assertEqual(found, brute)
      self.assertLess(tree.visited, 300)  # of ~1500 nodes over 3000 pieces


class SolidTests(unittest.TestCase):
  def carrier(self) -> Tuple[Resource, Plate]:
    """A carrier 100 mm tall with a flat site 80 mm up, and a plate sitting 3 mm into the site."""
    carrier = Resource("carrier", 140, 100, 100)
    site = ResourceHolder("site", 128, 86, 0)
    carrier.assign_child_resource(site, location=Coordinate(5, 5, 80))
    plate = Cor_96_wellplate_360ul_Fb("plate")
    site.assign_child_resource(plate, location=Coordinate(0, 0, -3))
    return carrier, plate

  def test_a_carrier_is_solid_up_to_what_it_holds_and_a_plate_is_solid_whole(self):
    carrier, plate = self.carrier()
    by = {p.resource.name: p for p in solid_pieces(carrier)}
    self.assertEqual(set(by), {"carrier", "plate"})  # no site, no wells
    self.assertAlmostEqual(by["carrier"].hi[2], 77.0 - CONTACT)
    self.assertAlmostEqual(by["plate"].lo[2], 77.0 + CONTACT)

  def test_an_empty_carrier_is_solid_up_to_its_site(self):
    carrier, plate = self.carrier()
    plate.unassign()
    (piece,) = solid_pieces(carrier)
    self.assertAlmostEqual(piece.hi[2], 80.0 - CONTACT)

  def test_a_carrier_whose_model_declares_hulls_is_its_shape(self):
    # A tip carrier is walls and a roof around five pockets, not the slab its box suggests: what
    # its model declares stands where the model does, pockets and all.
    carrier = hamilton_tip_carrier_L5("carrier")
    carrier[0] = hamilton_96_tiprack_1000uL("rack")
    pieces = [p for p in solid_pieces(carrier) if p.resource is carrier]
    hulls = declared_hulls(carrier)
    self.assertIsNotNone(hulls)
    assert hulls is not None
    self.assertEqual(len(pieces), len(hulls))

    def solid_at(point: Vec) -> bool:
      return any(distance([point], piece.points) <= 0.0 for piece in pieces)

    self.assertTrue(solid_at((4.0, 200.0, 112.0)))  # a side wall, above the slab's top
    self.assertFalse(solid_at((67.0, 51.9, 64.0)))  # in the first pocket, under the rack

  def test_something_hung_below_is_solid_and_so_is_what_it_hangs_from(self):
    channel = Resource("channel", 9, 9, 140)
    channel.assign_child_resource(Resource("shaft", 7, 7, 8), location=Coordinate(1, 1, -8))
    self.assertEqual({p.resource.name for p in solid_pieces(channel)}, {"channel", "shaft"})

  def test_a_lid_seated_on_a_plate_is_solid_with_it(self):
    carrier, plate = self.carrier()
    lid = Lid("lid", plate.get_size_x(), plate.get_size_y(), 10.0, nesting_z_height=2.0)
    plate.assign_child_resource(lid)
    pieces = {p.resource.name: p for p in solid_pieces(carrier)}
    self.assertEqual(set(pieces), {"carrier", "plate", "lid"})
    # The lid sits on the plate's top, sunk by its nesting height, above where the plate itself sits.
    self.assertAlmostEqual(pieces["lid"].lo[2], 77.0 + 14.2 - 2.0 + CONTACT)

  def test_an_enclosure_is_not_solid(self):
    housing = Resource("housing", 200, 200, 200, category="housing")
    self.assertEqual(solid_pieces(housing, hollow=["housing"]), [])
    self.assertEqual(len(solid_pieces(housing)), 1)

  def test_a_turned_resource_is_solid_where_it_is_turned_to(self):
    plate = Cor_96_wellplate_360ul_Fb("plate")
    holder = Resource("holder", 300, 300, 1)
    holder.assign_child_resource(plate, location=Coordinate(150, 0, 1))
    plate.rotation = Rotation(z=90)
    piece = next(p for p in solid_pieces(holder) if p.resource is plate)
    self.assertAlmostEqual(piece.hi[0] - piece.lo[0], plate.get_size_y() - 2 * CONTACT, places=6)


class SweepTests(unittest.TestCase):
  def test_a_straight_move_catches_a_wall_both_its_ends_clear(self):
    wall = Obstacles([block("wall", (50, -100, -100), (2, 200, 200))])
    piece = [block("m", (0, 0, 0), (10, 10, 10))]
    root = Resource("r", 1, 1, 1)
    # Looked at only where it starts and where it ends, it passes through unseen...
    ends = Group("ends", piece, [Segment([Pose()]), Segment([Pose(shift=(100, 0, 0))])])
    self.assertEqual(check(root, [ends], obstacles=wall), [])
    # ...swept, it does not.
    swept = Group("swept", piece, straight((100, 0, 0)))
    self.assertEqual([c.obstacle.name for c in check(root, [swept], obstacles=wall)], ["wall"])

  def test_resting_on_something_and_leaving_it_is_not_a_collision(self):
    floor = Piece(Resource("floor", 100, 100, 10), box((0, 0, 0), (100, 100, 10)))
    floor.points = box(
      (CONTACT, CONTACT, CONTACT), (100 - 2 * CONTACT, 100 - 2 * CONTACT, 10 - 2 * CONTACT)
    )
    floor.lo, floor.hi = (CONTACT,) * 3, (100 - CONTACT, 100 - CONTACT, 10 - CONTACT)
    thing = Piece(
      Resource("thing", 10, 10, 10),
      box((20 + CONTACT, 20 + CONTACT, 10 + CONTACT), (10 - 2 * CONTACT,) * 3),
    )
    up = Group("up", [thing], straight((0, 0, 50)))
    self.assertEqual(check(Resource("r", 1, 1, 1), [up], obstacles=Obstacles([floor])), [])

  def test_clearance_reports_what_passes_too_close(self):
    post = block("post", (0, 13, 0), (10, 10, 10))
    past = Group("past", [block("m", (-50, 0, 0), (10, 10, 10))], straight((100, 0, 0)))
    root = Resource("r", 1, 1, 1)
    self.assertEqual(check(root, [past], obstacles=Obstacles([post])), [])
    (near,) = check(root, [past], clearance=5.0, obstacles=Obstacles([post]))
    self.assertAlmostEqual(near.gap, 3.0, places=6)

  def test_a_meeting_is_walked_to_where_it_happens(self):
    wall = Obstacles([block("wall", (48, -100, -100), (2, 200, 200))])
    piece = [block("m", (0, 0, 0), (10, 10, 10))]
    swept = Group("swept", piece, straight((100, 0, 0)))
    root = Resource("r", 1, 1, 1)
    (hit,) = check(root, [swept], obstacles=wall)
    # The mover meets the wall face on, its front at x 48: 38 mm of the 100 mm way in.
    at, when = hit.at, hit.when
    assert at is not None and when is not None
    self.assertAlmostEqual(when, 0.38, places=2)
    self.assertAlmostEqual(at.shift[0], 38.0, places=1)

  def test_a_way_its_hull_only_leans_on_stands_as_the_sweep_said(self):
    # The swept hull of the box spans corners the way itself never visits: a wall beside the way,
    # met by the hull alone, is reported without a place where the mover was brought to it.
    wall = Obstacles([block("wall", (55, -100, -100), (2, 200, 200))])
    piece = [block("m", (0, 0, 0), (10, 10, 10))]
    moved = Group("moved", piece, on_axes((100, 100, 0)))
    root = Resource("r", 1, 1, 1)
    (hit,) = check(root, [moved], obstacles=wall)
    self.assertIsNotNone(hit)
    self.assertIsNone(hit.at)
    self.assertIsNone(hit.when)

  def test_moves_on_their_own_axes_are_taken_as_the_box_they_span(self):
    # Something going 100 along X and 100 along Y, each on its own profile, may pass the corner.
    corner = block("corner", (101, 0, 0), (10, 10, 10))
    root = Resource("r", 1, 1, 1)
    piece = [block("m", (0, 0, 0), (10, 10, 10))]
    diagonal = Group("diagonal", piece, straight((100, 100, 0)))
    self.assertEqual(check(root, [diagonal], obstacles=Obstacles([corner])), [])
    either_first = Group("axes", piece, on_axes((100, 100, 0)))
    self.assertEqual(len(check(root, [either_first], obstacles=Obstacles([corner]))), 1)

  def test_a_turn_catches_what_lies_on_its_arc_and_not_what_lies_beyond(self):
    bar = [block("bar", (0, -2, 0), (100, 4, 4))]
    root = Resource("r", 1, 1, 1)
    at = 95 * math.cos(math.radians(45))
    on_arc = block("on arc", (at - 1, at - 1, 0), (2, 2, 4))
    beyond = block("beyond", (120 * 0.7071 - 1, 120 * 0.7071 - 1, 0), (2, 2, 4))
    for step in (5.0, 90.0):  # finely cut, or one arc grown by all it strays
      swing = Group("swing", bar, turning(bar, (0, 0, 0), 90.0, step=step))
      found = {c.obstacle.name for c in check(root, [swing], obstacles=Obstacles([on_arc, beyond]))}
      self.assertEqual(found, {"on arc"}, f"step {step}")
    # Ends only, not grown: the arc's middle is missed. Why the slack is there.
    ends = Group("ends", bar, [turning(bar, (0, 0, 0), 90.0, step=90.0)[0]])
    ends.segments[0].slack = 0.0
    self.assertEqual(check(root, [ends], obstacles=Obstacles([on_arc])), [])

  def test_things_meant_to_be_touched_are_not_reported(self):
    target = block("target", (50, 0, 0), (10, 10, 10))
    through = Group("through", [block("m", (0, 0, 0), (10, 10, 10))], straight((100, 0, 0)))
    root = Resource("r", 1, 1, 1)
    self.assertEqual(
      check(root, [through], allow=[target.resource], obstacles=Obstacles([target])), []
    )


class TimeTests(unittest.TestCase):
  def channels(self, lead_start: float, follow_start: float):
    lead = [block("lead", (0, 0, 0), (9, 9, 100))]
    follow = [block("follow", (0, -20, 0), (9, 9, 100))]
    return [
      Group("lead", lead, profiled((0, 40, 0), speed=200, acceleration=800, start=lead_start)),
      Group(
        "follow", follow, profiled((0, 40, 0), speed=200, acceleration=800, start=follow_start)
      ),
    ]

  def test_one_following_another_into_its_place_is_not_a_collision(self):
    # The lead leaves before the follower arrives: their sweeps overlap, but never at once.
    self.assertEqual(
      check(Resource("r", 1, 1, 1), self.channels(0.0, 0.5), obstacles=Obstacles([])), []
    )

  def test_arriving_before_the_other_has_left_is(self):
    hits = check(Resource("r", 1, 1, 1), self.channels(0.4, 0.0), obstacles=Obstacles([]))
    self.assertEqual({(c.mover.name, c.obstacle.name) for c in hits}, {("lead", "follow")})


class ScaleTests(unittest.TestCase):
  def test_a_sweep_among_thousands_is_quick_and_tests_few_exactly(self):
    rng = random.Random(4)
    root = Resource("deck", 2000, 2000, 10)
    for i, (x, y) in enumerate(itertools.product(range(0, 1900, 30), range(0, 1900, 30))):
      root.assign_child_resource(
        Resource(f"post{i}", 10, 10, rng.uniform(20, 80)), location=Coordinate(x, y, 10)
      )
    t = time.perf_counter()
    pieces = solid_pieces(root)
    tree = Obstacles(pieces)
    built = time.perf_counter() - t
    carried = Resource("carried", 120, 80, 20)
    carried.location = Coordinate(100, 100, 100)
    above = moving("above", [carried], straight((1500, 1000, 0)))
    t = time.perf_counter()
    tree.visited = 0
    self.assertEqual(check(root, [above], obstacles=tree), [])
    checked = time.perf_counter() - t
    self.assertGreater(len(pieces), 4000)
    self.assertLess(checked, 1.0)
    self.assertLess(built, 10.0)


if __name__ == "__main__":
  unittest.main()
