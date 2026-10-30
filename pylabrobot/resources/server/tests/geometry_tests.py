"""The store's geometry against PLR's own: every answer `Resource` gives, the store gives too.

Each test builds a tree with PLR's classes, defines it as a store would hold it, and asks both the
same question. Where PLR refuses, the store refuses with a `ServerError`.
"""

from __future__ import annotations

import math
import random
import unittest
import warnings
from typing import Dict, Iterable, List, Optional, Tuple

from pylabrobot.lib.pick_and_place.locations import (
  get_grip_height,
  get_grip_location,
  get_pick_location,
  get_place_location,
  get_rotation_wrt,
)
from pylabrobot.lib.spatial.occupancy import get_resource_at_location
from pylabrobot.resources import (
  Container,
  Coordinate,
  Lid,
  Plate,
  Resource,
  ResourceHolder,
  ResourceStack,
  Rotation,
  TipRack,
  TipSpot,
  Trash,
)
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.plate_adapter import PlateAdapter
from pylabrobot.resources.server import geometry as g
from pylabrobot.resources.server.definitions import definition_from_resource, kind_of
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.library import labware_factories
from pylabrobot.resources.server.tests.scenes import SCENES, Scene, build_all
from pylabrobot.resources.server.tree import Node, build_tree, to_definition
from pylabrobot.resources.server.v1 import types_pb2 as pb
from pylabrobot.resources.tip_rack import resting_location

ANCHORS_X = {pb.ANCHOR_X_LEFT: "l", pb.ANCHOR_X_CENTER: "c", pb.ANCHOR_X_RIGHT: "r"}
ANCHORS_Y = {pb.ANCHOR_Y_FRONT: "f", pb.ANCHOR_Y_CENTER: "c", pb.ANCHOR_Y_BACK: "b"}
ANCHORS_Z = {pb.ANCHOR_Z_BOTTOM: "b", pb.ANCHOR_Z_CENTER: "c", pb.ANCHOR_Z_TOP: "t"}
ALL_ANCHORS = [(x, y, z) for x in ANCHORS_X for y in ANCHORS_Y for z in ANCHORS_Z]
FEW_ANCHORS = [
  (pb.ANCHOR_X_LEFT, pb.ANCHOR_Y_FRONT, pb.ANCHOR_Z_BOTTOM),
  (pb.ANCHOR_X_CENTER, pb.ANCHOR_Y_CENTER, pb.ANCHOR_Z_TOP),
  (pb.ANCHOR_X_RIGHT, pb.ANCHOR_Y_BACK, pb.ANCHOR_Z_CENTER),
]
ITEM_KINDS = {
  pb.RESOURCE_KIND_WELL,
  pb.RESOURCE_KIND_TIP_SPOT,
  pb.RESOURCE_KIND_TIP,
  pb.RESOURCE_KIND_TUBE,
}


def plr_z(z: pb.AnchorZ) -> str:
  return "cavity_bottom" if z == pb.ANCHOR_Z_CAVITY_BOTTOM else ANCHORS_Z[z]


def anchors_for(node: Node) -> List[Tuple[pb.AnchorX, pb.AnchorY, pb.AnchorZ]]:
  """The anchors worth asking a resource for: all of them, but few for the many items of a rack,
  and its cavity bottom for a container that has one."""
  anchors = list(FEW_ANCHORS if node.kind in ITEM_KINDS else ALL_ANCHORS)
  if node.kind in g.CONTAINER_KINDS and node.facts.container.HasField("material_z_thickness"):
    anchors.append((pb.ANCHOR_X_CENTER, pb.ANCHOR_Y_CENTER, pb.ANCHOR_Z_CAVITY_BOTTOM))
  return anchors


class Pair:
  """A PLR tree and the store's tree defined from it, matched by name."""

  def __init__(self, root: Resource):
    self.root = root
    self.node_root = build_tree(definition_from_resource(root))
    self.resources: Dict[str, Resource] = {r.name: r for r in [root] + root.get_all_children()}
    self.nodes: Dict[str, Node] = {n.name: n for n in self.node_root.walk()}

  def pairs(self) -> Iterable[Tuple[Resource, Node]]:
    for name, node in self.nodes.items():
      yield self.resources[name], node


class GeometryTestCase(unittest.TestCase):
  """Comparisons that report which resource disagreed."""

  def assertCoordinate(self, ours: Coordinate, theirs: Coordinate, what: str, tol: float = 1e-9):
    for axis, a, b in zip("xyz", ours, theirs):
      self.assertTrue(math.isclose(a, b, abs_tol=tol), f"{what}: {axis} {a} != {b}")

  def assertSize(self, ours: g.Size, theirs: Tuple[float, float, float], what: str):
    for axis, a, b in zip("xyz", ours, theirs):
      self.assertTrue(math.isclose(a, b, abs_tol=1e-9), f"{what}: size {axis} {a} != {b}")

  def assertRotation(self, ours: Rotation, theirs: Rotation, what: str):
    for row_a, row_b in zip(ours.get_rotation_matrix(), theirs.get_rotation_matrix()):
      for a, b in zip(row_a, row_b):
        self.assertTrue(math.isclose(a, b, abs_tol=1e-9), f"{what}: rotation {ours} != {theirs}")
    for axis, a, b in zip("xyz", (ours.x, ours.y, ours.z), (theirs.x, theirs.y, theirs.z)):
      self.assertTrue(math.isclose(a, b, abs_tol=1e-7), f"{what}: angle {axis} {a} != {b}")

  def assertSameRefusal(self, ours, theirs, what: str):
    """Both answer alike, or both refuse."""
    try:
      expected = theirs()
    except (ValueError, AssertionError, TypeError, RuntimeError, NotImplementedError):
      with self.assertRaises(ServerError, msg=f"{what}: PLR refused, the store did not"):
        ours()
      return None
    got = ours()
    if isinstance(expected, Coordinate):
      self.assertCoordinate(got, expected, what)
    else:
      self.assertTrue(math.isclose(got, expected, abs_tol=1e-9), f"{what}: {got} != {expected}")
    return got

  def check_tree(self, pair: Pair, frame_name: Optional[str] = None) -> None:
    """Every size, rotation, location, centre and highest point in a tree, as PLR has it."""
    frame = pair.resources[frame_name] if frame_name else None
    node_frame = pair.nodes[frame_name] if frame_name else None
    for resource, node in pair.pairs():
      what = f"{node.name} ({pb.ResourceKind.Name(node.kind)})"
      self.assertSize(
        g.size(node), (resource.get_size_x(), resource.get_size_y(), resource.get_size_z()), what
      )
      self.assertSize(
        g.absolute_size(node),
        (
          resource.get_absolute_size_x(),
          resource.get_absolute_size_y(),
          resource.get_absolute_size_z(),
        ),
        what,
      )
      self.assertRotation(g.absolute_rotation(node), resource.get_absolute_rotation(), what)
      self.assertCoordinate(g.center(node), resource.center(), f"{what} centre")
      if resource.location is None:
        with self.assertRaises(ServerError):
          g.absolute_location(node)
        continue
      for x, y, z in anchors_for(node):
        self.assertCoordinate(
          g.absolute_location(node, x, y, z),
          resource.get_absolute_location(ANCHORS_X[x], ANCHORS_Y[y], plr_z(z)),
          f"{what} at {x}{y}{z}",
        )
        if frame is not None and node_frame is not None and resource.is_in_subtree_of(frame):
          self.assertCoordinate(
            g.location_wrt(node, node_frame, x, y, z),
            resource.get_location_wrt(frame, ANCHORS_X[x], ANCHORS_Y[y], plr_z(z)),
            f"{what} in {frame_name} at {x}{y}{z}",
          )
      if node.kind not in ITEM_KINDS:
        self.assertTrue(
          math.isclose(g.highest_point(node), resource.get_highest_known_point(), abs_tol=1e-9),
          f"{what} highest point",
        )


class SceneTests(GeometryTestCase):
  """Sizes, rotations and locations of everything in each scene."""

  scenes: List[Scene]

  @classmethod
  def setUpClass(cls):
    cls.scenes = build_all()

  def test_scenes(self):
    for scene in self.scenes:
      with self.subTest(scene=scene.name):
        self.check_tree(Pair(scene.root), frame_name=scene.frame.name)

  def test_location_between_any_two(self):
    rng = random.Random(1)
    for scene in self.scenes:
      pair = Pair(scene.root)
      names = sorted(pair.nodes)
      for _ in range(400):
        a, b = rng.choice(names), rng.choice(names)
        resource, other = pair.resources[a], pair.resources[b]
        node, frame = pair.nodes[a], pair.nodes[b]
        self.assertSameRefusal(
          lambda: g.location_wrt(node, frame, pb.ANCHOR_X_CENTER, pb.ANCHOR_Y_BACK),
          lambda: resource.get_location_wrt(other, "c", "b"),
          f"{a} in {b}",
        )
        self.assertRotation(
          g.rotation_wrt(node, frame), get_rotation_wrt(resource, other), f"{a} turned in {b}"
        )

  def test_cavity_bottom_of_what_is_not_a_container(self):
    pair = Pair(self.scenes[0].root)
    for resource, node in pair.pairs():
      if not isinstance(resource, Container) and resource.location is not None:
        with self.assertRaises(ServerError):
          g.absolute_location(node, z=pb.ANCHOR_Z_CAVITY_BOTTOM)

  def test_tracks(self):
    pair = Pair(self.scenes[0].root)
    deck = pair.resources["deck"]
    assert isinstance(deck, HamiltonDeck)
    for track in range(-4, deck.num_tracks + 2):
      self.assertCoordinate(
        g.track_location(pair.nodes["deck"], track), deck.track_to_location(track), f"{track}"
      )


LABWARE_SHARDS = 8


class LabwareTests(GeometryTestCase):
  """Every labware definition in PLR, alone and seated in a turned, tilted frame."""

  def check_labware(self, shard: int) -> None:
    """One of `LABWARE_SHARDS` shares of the labware, so test runners can spread them over cores."""
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      for qualified, factory in labware_factories()[shard::LABWARE_SHARDS]:
        with self.subTest(labware=qualified):
          alone = factory(name="labware")
          # PLR leaves a deck's own top out of its highest point only when it is named "deck".
          name = "deck" if isinstance(alone, Deck) else "labware"
          self.check_tree(Pair(factory(name=name)))

          frame = Resource("frame", size_x=400, size_y=400, size_z=400, rotation=Rotation(z=-30))
          site = Resource("site", size_x=200, size_y=200, size_z=10, rotation=Rotation(x=3, z=90))
          frame.assign_child_resource(site, location=Coordinate(200, 10, 20))
          site.assign_child_resource(factory(name=name), location=Coordinate(5, 6, 7))
          self.check_tree(Pair(frame), frame_name="frame")

  def test_labware_0(self):
    self.check_labware(0)

  def test_labware_1(self):
    self.check_labware(1)

  def test_labware_2(self):
    self.check_labware(2)

  def test_labware_3(self):
    self.check_labware(3)

  def test_labware_4(self):
    self.check_labware(4)

  def test_labware_5(self):
    self.check_labware(5)

  def test_labware_6(self):
    self.check_labware(6)

  def test_labware_7(self):
    self.check_labware(7)

  def test_every_resource_has_its_kind(self):
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      for qualified, factory in labware_factories():
        built = factory(name="labware")
        for resource in [built] + built.get_all_children():
          if type(resource) is Resource or Resource in type(resource).__bases__:
            continue  # A resource of its own, a device body say: a box.
          self.assertNotEqual(
            kind_of(resource), pb.RESOURCE_KIND_RESOURCE, f"{qualified}: {type(resource)}"
          )

  def test_definition_round_trip(self):
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      for qualified, factory in labware_factories():
        definition = definition_from_resource(factory(name="labware"))
        self.assertEqual(to_definition(build_tree(definition)), definition, qualified)


def bare_container() -> Container:
  """A container with neither data nor functions."""
  return Container("bare", size_x=10, size_y=10, size_z=10, material_z_thickness=1)


class HeightVolumeTests(GeometryTestCase):
  """Volume and height through `height_volume_data`, as PLR interpolates them."""

  def test_every_container_with_data(self):
    checked = 0
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      for qualified, factory in labware_factories():
        pair = Pair(factory(name="labware"))
        seen_models = set()
        for resource, node in pair.pairs():
          if not isinstance(resource, Container) or resource.height_volume_data is None:
            continue
          if resource.height_volume_data and id(resource.height_volume_data) in seen_models:
            continue
          seen_models.add(id(resource.height_volume_data))
          heights = sorted(resource.height_volume_data)
          volumes = [resource.height_volume_data[h] for h in heights]
          probes = heights + [(a + b) / 2 for a, b in zip(heights, heights[1:])]
          probes += [heights[0] - 0.1, heights[-1] + 0.1, heights[-1] * 0.37]
          for height in probes:
            self.assertSameRefusal(
              lambda: g.volume_from_height(node, height),
              lambda: resource.compute_volume_from_height(height),
              f"{qualified} {node.name} at {height} mm",
            )
          v_probes = volumes + [(a + b) / 2 for a, b in zip(volumes, volumes[1:])]
          v_probes += [volumes[0] - 1, volumes[-1] + 1]
          for volume in v_probes:
            self.assertSameRefusal(
              lambda: g.height_from_volume(node, volume),
              lambda: resource.compute_height_from_volume(volume),
              f"{qualified} {node.name} holding {volume} uL",
            )
          checked += 1
    self.assertGreater(checked, 50)

  def test_container_without_data(self):
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      pair = Pair(bare_container())
    with self.assertRaises(ServerError) as refused:
      g.volume_from_height(pair.node_root, 1.0)
    self.assertEqual(refused.exception.problem, pb.PROBLEM_NO_HEIGHT_VOLUME_DATA)


class SeatingTests(GeometryTestCase):
  """Where each kind of parent seats each kind of child."""

  scenes: List[Scene]

  @classmethod
  def setUpClass(cls):
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      cls.scenes = build_all()

  def movable(self, pair: Pair) -> List[Tuple[Resource, Node]]:
    return [
      (resource, node)
      for resource, node in pair.pairs()
      if isinstance(resource, (Plate, Lid, TipRack, ResourceStack, Trash))
      or (isinstance(resource, Container) and resource.parent is not None)
      and not isinstance(resource.parent, (Plate,))
    ]

  def test_holders(self):
    for scene in self.scenes:
      pair = Pair(scene.root)
      holders = [(r, n) for r, n in pair.pairs() if isinstance(r, ResourceHolder)]
      for holder, holder_node in holders:
        for child, child_node in self.movable(pair)[:25]:
          self.assertSameRefusal(
            lambda: g.holder_child_location(holder_node, child_node),
            lambda: holder.get_default_child_location(child),
            f"{child.name} into {holder.name}",
          )

  def test_stacks(self):
    for scene in self.scenes:
      pair = Pair(scene.root)
      for stack, stack_node in [(r, n) for r, n in pair.pairs() if isinstance(r, ResourceStack)]:
        for child, child_node in self.movable(pair):
          if child.rotation.x or child.rotation.y:
            continue
          self.assertSameRefusal(
            lambda: g.stack_child_location(stack_node, child_node),
            lambda: stack.get_new_child_location(child),
            f"{child.name} onto {stack.name}",
          )

  def test_plate_adapters(self):
    checked = 0
    for scene in self.scenes:
      pair = Pair(scene.root)
      adapters = [(r, n) for r, n in pair.pairs() if isinstance(r, PlateAdapter)]
      plates = [(r, n) for r, n in pair.pairs() if isinstance(r, Plate)]
      checked += len(adapters) * len(plates)
      for adapter, adapter_node in adapters:
        for plate, plate_node in plates:
          self.assertSameRefusal(
            lambda: g.plate_adapter_location(adapter_node, plate_node),
            lambda: adapter.compute_plate_location(plate),
            f"{plate.name} onto {adapter.name}",
          )
    self.assertGreater(checked, 10)

  def test_lids(self):
    for scene in self.scenes:
      pair = Pair(scene.root)
      lids = [(r, n) for r, n in pair.pairs() if isinstance(r, Lid)]
      self.assertTrue(lids)
      for parent, parent_node in pair.pairs():
        if not isinstance(parent, (Plate, TipRack, Container)):
          continue
        for lid, lid_node in lids:
          self.assertCoordinate(
            g.lid_location(parent_node, lid_node),
            parent.get_lid_location(lid),
            f"{lid.name} on {parent.name}",
          )

  def test_tips_rest_in_their_spots(self):
    pair = Pair(self.scenes[0].root)
    checked = 0
    for spot, spot_node in pair.pairs():
      if isinstance(spot, TipSpot) and spot.tip is not None:
        tip_node = pair.nodes[spot.tip.name]
        expected = resting_location(spot, spot.tip)
        self.assertCoordinate(g.resting_location(spot_node, tip_node), expected, spot.name)
        seated = g.default_child_location(spot_node, tip_node)
        assert seated is not None
        self.assertCoordinate(seated, expected, spot.name)
        checked += 1
    self.assertGreater(checked, 100)

  def test_tools_hang_on_their_shafts(self):
    pair = Pair(self.scenes[0].root)
    checked = 0
    for shaft, shaft_node in pair.pairs():
      if isinstance(shaft, TipMountingShaft) and shaft.tip is not None:
        tool_node = pair.nodes[shaft.tip.name]
        location = shaft.tip.location
        assert location is not None
        self.assertCoordinate(g.mount_location(shaft_node, tool_node), location, shaft.name)
        checked += 1
    self.assertEqual(checked, 2)

  def test_children_where_their_parents_seat_them(self):
    """A child assigned without a location sits where `default_child_location` says.

    A stack is left out: PLR sinks a stack onto a plate holder's pedestal only when it already
    holds a plate as it is assigned there, so where one sits depends on the order it was built in.
    """
    for scene in self.scenes:
      pair = Pair(scene.root)
      for resource, node in pair.pairs():
        parent = node.parent
        if parent is None or resource.location is None:
          continue
        seated_by_parent = isinstance(resource.parent, (ResourceHolder, ResourceStack))
        seated_by_parent |= isinstance(resource, Lid) or isinstance(resource.parent, PlateAdapter)
        if not seated_by_parent or isinstance(resource.parent, ResourceStack):
          continue
        if isinstance(resource, ResourceStack):
          continue
        seated = g.default_child_location(parent, node)
        assert seated is not None
        self.assertCoordinate(seated, resource.location, f"{resource.name}")


class MotionTests(GeometryTestCase):
  """Turning, pivoting, jaws, and device positions."""

  def test_turning(self):
    rng = random.Random(2)
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      scenes = build_all()
    for scene in scenes:
      pair = Pair(scene.root)
      located = sorted(name for name, r in pair.resources.items() if r.location is not None)
      for _ in range(60):
        name = rng.choice(located)
        resource, node = pair.resources[name], pair.nodes[name]
        by = Rotation(x=rng.choice([0, 0, 15]), y=rng.choice([0, 0, -40]), z=rng.uniform(-200, 200))
        pivot = rng.choice([None, Coordinate(rng.uniform(0, 50), rng.uniform(0, 50), 3)])
        saved = resource.rotation
        saved_angles, saved_location = (saved.x, saved.y, saved.z), resource.location

        ours = g.turned_by(node.rotation, by)
        ours_location = node.location if pivot is None else g.pivoted_location(node, ours, pivot)
        resource.rotate(x=by.x, y=by.y, z=by.z, pivot_coordinate=pivot)
        self.assertRotation(ours, resource.rotation, f"{name} turned by {by}")
        assert ours_location is not None and resource.location is not None
        self.assertCoordinate(ours_location, resource.location, f"{name} turned about {pivot}")

        to_z = rng.uniform(-400, 400)
        resource.rotation = Rotation(*saved_angles)
        resource.location = saved_location
        ours = g.turned_to(node.rotation, None, None, to_z)
        ours_location = node.location if pivot is None else g.pivoted_location(node, ours, pivot)
        resource.rotate_to(z=to_z, pivot_coordinate=pivot)
        self.assertRotation(ours, resource.rotation, f"{name} turned to {to_z}")
        assert ours_location is not None and resource.location is not None
        self.assertCoordinate(ours_location, resource.location, f"{name} turned to, about {pivot}")
        resource.rotation = Rotation(*saved_angles)
        resource.location = saved_location

  def test_jaws(self):
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      pair = Pair(build_all()[0].root)
    gripper = pair.resources["iswap_gripper"]
    assert isinstance(gripper, MechanicalGripper)
    for width in (60.0, 60.5, 87.3, 140.0, 59.9, 140.1):
      try:
        gripper.jaw_width = width
      except ValueError:
        with self.assertRaises(ServerError):
          g.jaw_locations(pair.nodes["iswap_gripper"], width)
        continue
      moved = g.jaw_locations(pair.nodes["iswap_gripper"], width)
      self.assertEqual(len(moved), 2)
      for part, location in moved:
        expected = pair.resources[part.name].location
        assert expected is not None
        self.assertCoordinate(location, expected, f"{part.name} at {width}")

  def test_device_positions_round_trip(self):
    """A part moved to where its reference point is reported then reports that point."""
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      pair = Pair(build_all()[0].root)
    deck = pair.nodes["deck"]
    rng = random.Random(3)
    for name in ("channel_0", "channel_3", "head96", "iswap_link_1"):
      node = pair.nodes[name]
      reference = Coordinate(4.5, 4.5, -10.0)
      for _ in range(20):
        y, z = rng.uniform(0, 600), rng.uniform(100, 300)
        node.own.location.CopyFrom(
          pb.Vec3(**dict(zip("xyz", g.location_from_reference(node, deck, None, y, z, reference))))
        )
        reported = g.location_wrt(node, deck) + reference
        self.assertAlmostEqual(reported.y, y, places=3)
        self.assertAlmostEqual(reported.z, z, places=3)


class PlacementTests(GeometryTestCase):
  """Where a resource goes when placed, and where it is gripped."""

  def check_place_locations(self, scene_name: str) -> None:
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      scenes = [SCENES[scene_name]()]
    for scene in scenes:
      pair = Pair(scene.root)
      frame, frame_node = scene.frame, pair.nodes[scene.frame.name]
      movable = [
        (r, n)
        for r, n in pair.pairs()
        if isinstance(r, (Plate, Lid, TipRack, ResourceStack)) and r.location is not None
      ]
      destinations = [
        (r, n)
        for r, n in pair.pairs()
        if isinstance(r, (ResourceHolder, ResourceStack, PlateAdapter, Trash, Plate))
        and r.is_in_subtree_of(frame)
      ]
      for resource, node in movable[:12]:
        for destination, destination_node in destinations[:30]:
          for turn in (0, 90, 180, 270):
            if turn and isinstance(resource, ResourceStack):
              continue  # PLR cannot copy a stack to turn it.
            self.assertSameRefusal(
              lambda: g.place_location(node, destination_node, frame_node, turn),
              lambda: get_place_location(
                resource, destination, frame, rotation_applied_by_move=turn
              ),
              f"{resource.name} into {destination.name} turned {turn}",
            )
        target = Coordinate(300, 200, 150)
        self.assertCoordinate(
          g.place_location(node, target, frame_node),
          get_place_location(resource, target, frame),
          "",
        )

  def test_grips(self):
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      scenes = build_all()
    for scene in scenes:
      pair = Pair(scene.root)
      frame, frame_node = scene.frame, pair.nodes[scene.frame.name]
      for resource, node in pair.pairs():
        if not isinstance(resource, (Plate, Lid, TipRack)) or not resource.is_in_subtree_of(frame):
          continue
        for top, bottom in ((5.0, None), (None, 3.0), (None, None), (500.0, None)):
          height = self.assertSameRefusal(
            lambda: g.grip_height(node, top, bottom),
            lambda: get_grip_height(
              resource, pickup_distance_from_top=top, pickup_distance_from_bottom=bottom
            ),
            f"{resource.name} gripped {top} {bottom}",
          )
          if height is None:
            continue
          location = get_pick_location(resource, frame)
          self.assertCoordinate(
            g.grip_location(node, g.location_wrt(node, frame_node), frame_node, height),
            get_grip_location(resource, location, frame, height),
            f"{resource.name} grip",
          )

  def test_place_locations_star_deck(self):
    self.check_place_locations("star_deck")

  def test_place_locations_turned_deck(self):
    self.check_place_locations("turned_deck")

  def test_place_locations_tilted(self):
    self.check_place_locations("tilted")


class OccupancyTests(GeometryTestCase):
  """What is at a point, as `get_resource_at_location` finds it."""

  def check_points(self, scene_name: str) -> None:
    """Points inside resources, on their faces, between them, and with what holds them left out."""
    rng = random.Random(4)
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      scenes = [SCENES[scene_name]()]
    for scene in scenes:
      pair = Pair(scene.root)
      frame, frame_node = scene.frame, pair.nodes[scene.frame.name]
      inside = [
        r for r in frame.get_all_children() if r.location is not None and r.get_size_z() > 0
      ]
      for _ in range(20):
        target = rng.choice(inside)
        corner = target.get_location_wrt(frame)
        jitter = Coordinate(rng.uniform(-3, 8), rng.uniform(-3, 8), rng.uniform(-1, 4))
        point = rng.choice([corner, corner + jitter, target.get_location_wrt(frame, "c", "c", "c")])
        choices: List[List[Resource]] = [[], [target]]
        if target.parent is not None and target.parent is not frame:
          choices.append([target.parent])
        exclude = rng.choice(choices)
        theirs = get_resource_at_location(point, frame, exclude=exclude)
        ours = g.resource_at(frame_node, point, exclude=[pair.nodes[r.name] for r in exclude])
        self.assertEqual(
          None if ours is None else ours.name,
          None if theirs is None else theirs.name,
          f"{scene.name} at {point} leaving out {[r.name for r in exclude]}",
        )

  def test_points_star_deck(self):
    self.check_points("star_deck")

  def test_points_turned_deck(self):
    self.check_points("turned_deck")

  def test_points_tilted(self):
    self.check_points("tilted")


if __name__ == "__main__":
  unittest.main()
