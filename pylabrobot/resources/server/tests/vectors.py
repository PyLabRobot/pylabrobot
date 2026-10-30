"""Geometry test vectors: PLR's answers, as data any store implementation can check itself against.

`vectors/geometry.json` holds cases built from the scenes in `scenes.py`. Every expected value is
computed by PLR's `Resource` classes, not by the store. A case is:

- `name`: the scene's name.
- `frame`: the name of the resource commands measure from, the deck.
- `tree`: the whole tree, a `ResourceDefinition` in proto3 JSON.
- `locations`: `{resource, frame?, anchor, location}`: where an anchor of a resource is, along the
  frame's axes from its left front bottom corner. Without `frame`, along the root's.
- `resources`: `{resource, size, absoluteSize, absoluteRotation, rotationInFrame, highestPoint,
  center}` for each resource with a location, `highestPoint` left out for items.
- `heightVolume`: `{resource, height | volume, result}`: a volume from a height above the cavity
  bottom, or the height of a volume.
- `seats`: `{parent, child, location}`: where `parent` seats `child` given no location, computed
  from where `child` is before it moves.
- `turns`: `{resource, by | to, pivot?, rotation, location}`: a resource turned by angles on top
  of its own, or to angles (an axis left out keeps its angle), about a pivot in its own frame.
- `jaws`: `{gripper, jawWidth, parts: [{resource, location}]}`: where a gripper's moving parts
  stand with its jaws that far apart.
- `placements`: `{resource, destination | destinationLocation, rotationAppliedByMove, location}`:
  where a resource's origin goes in the frame when placed.
- `grips`: `{resource, distanceFromTop | distanceFromBottom, gripHeight, gripLocation}`: where a
  resource is held, picked up where it is.
- `occupancy`: `{location, exclude, found}`: the deepest resource in the frame whose bounding box
  holds the point, right, back and top faces excluded; `found` is null when none does.
- `tracks`: `{track, location}`: where a Hamilton deck's track starts.

Anything PLR refuses is recorded as `"refused": true` in place of the result.

Numbers agree within `tolerance` mm, degrees or uL. PLR rounds each coordinate it makes to 4
decimals, so an implementation that does not will differ by a few ten-thousandths. Angles are
Euler angles in degrees, and agree modulo 360.

Regenerate after a change to PLR's geometry with
`python -m pylabrobot.resources.server.tests.vectors`.
"""

from __future__ import annotations

import json
import math
import random
import warnings
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from google.protobuf import json_format

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
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.plate_adapter import PlateAdapter
from pylabrobot.resources.server import geometry as g
from pylabrobot.resources.server.definitions import definition_from_resource
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.tests.scenes import Scene, build_all
from pylabrobot.resources.server.tree import Node, build_tree
from pylabrobot.resources.server.v1 import types_pb2 as pb
from pylabrobot.resources.tip_rack import resting_location

PATH = Path(__file__).with_name("vectors") / "geometry.json"
TOLERANCE = 1e-3
FORMAT = 1

_X = {"l": "ANCHOR_X_LEFT", "c": "ANCHOR_X_CENTER", "r": "ANCHOR_X_RIGHT"}
_Y = {"f": "ANCHOR_Y_FRONT", "c": "ANCHOR_Y_CENTER", "b": "ANCHOR_Y_BACK"}
_Z = {
  "b": "ANCHOR_Z_BOTTOM",
  "c": "ANCHOR_Z_CENTER",
  "t": "ANCHOR_Z_TOP",
  "cavity_bottom": "ANCHOR_Z_CAVITY_BOTTOM",
}
_REFUSALS = (ValueError, AssertionError, TypeError, RuntimeError, NotImplementedError)
_ITEMS = (ItemizedResource, TipSpot)


def _vec(c: Coordinate) -> Dict[str, float]:
  return {"x": c.x, "y": c.y, "z": c.z}


def _rot(r: Rotation) -> Dict[str, float]:
  return {"x": r.x, "y": r.y, "z": r.z}


def _attempt(compute: Callable[[], Any], key: str, encode: Callable[[Any], Any]) -> Dict[str, Any]:
  """`{key: encoded answer}`, or `{"refused": true}` where PLR refuses."""
  try:
    return {key: encode(compute())}
  except _REFUSALS:
    return {"refused": True}


def _is_item(resource: Resource) -> bool:
  return resource.parent is not None and isinstance(resource.parent, _ITEMS)


def _under_stack_top(resource: Resource) -> bool:
  """Whether it is in a stack growing in z, under the top item: the store picks up only the top."""
  stack = resource.parent
  return (
    isinstance(stack, ResourceStack)
    and stack.direction == "z"
    and stack.children[-1] is not resource
  )


def _sampled(resources: List[Resource], every: int) -> List[Resource]:
  """All but the items of racks and plates, and every so many of those."""
  items = [r for r in resources if _is_item(r)]
  return [r for r in resources if not _is_item(r)] + items[::every]


def _plr_seat(parent: Resource, child: Resource) -> Coordinate:
  """Where PLR seats `child` in a holder, a stack or a plate adapter given no location."""
  if isinstance(parent, ResourceHolder):
    return parent.get_default_child_location(child)
  if isinstance(parent, ResourceStack):
    return parent.get_new_child_location(child)
  assert isinstance(parent, PlateAdapter) and isinstance(child, Plate)
  return parent.compute_plate_location(child)


def _scene_case(scene: Scene, rng: random.Random) -> Dict[str, Any]:
  """One scene's case: its tree, and PLR's answers about it."""
  root, frame = scene.root, scene.frame
  everything = [root] + root.get_all_children()
  located = [r for r in everything if r.location is not None]
  in_frame = [r for r in located if r.is_in_subtree_of(frame)]
  case: Dict[str, Any] = {
    "name": scene.name,
    "frame": frame.name,
    "tree": json_format.MessageToDict(definition_from_resource(root)),
  }

  locations = []
  for resource in _sampled(in_frame, 13):
    zs = ["b", "t", "c"] + (["cavity_bottom"] if isinstance(resource, Container) else [])
    for x, y, z in [("l", "f", zs[0]), ("c", "c", zs[1]), ("r", "b", zs[2])] + [
      ("c", "c", z) for z in zs[3:]
    ]:
      anchor = {"x": _X[x], "y": _Y[y], "z": _Z[z]}
      locations.append(
        {"resource": resource.name, "frame": frame.name, "anchor": anchor}
        | _attempt(lambda: resource.get_location_wrt(frame, x, y, z), "location", _vec)
      )
      locations.append(
        {"resource": resource.name, "anchor": anchor}
        | _attempt(lambda: resource.get_absolute_location(x, y, z), "location", _vec)
      )
  case["locations"] = locations

  case["resources"] = [
    {
      "resource": r.name,
      "size": {"x": r.get_size_x(), "y": r.get_size_y(), "z": r.get_size_z()},
      "absoluteSize": {
        "x": r.get_absolute_size_x(),
        "y": r.get_absolute_size_y(),
        "z": r.get_absolute_size_z(),
      },
      "absoluteRotation": _rot(r.get_absolute_rotation()),
      "rotationInFrame": _rot(get_rotation_wrt(r, frame)),
      "center": _vec(r.center()),
    }
    | ({} if _is_item(r) else {"highestPoint": r.get_highest_known_point()})
    for r in _sampled(in_frame, 11)
  ]

  height_volume = []
  for container in [r for r in everything if isinstance(r, Container) and r.height_volume_data]:
    if _is_item(container) and rng.random() > 0.05:
      continue
    heights = sorted(container.height_volume_data or {})
    for height in [heights[0] - 0.1, heights[1] / 3, heights[-1], heights[-1] + 0.1]:
      height_volume.append(
        {"resource": container.name, "height": height}
        | _attempt(lambda: container.compute_volume_from_height(height), "result", float)
      )
    for volume in [container.compute_volume_from_height(heights[-1]) * 0.41, -1.0]:
      height_volume.append(
        {"resource": container.name, "volume": volume}
        | _attempt(lambda: container.compute_height_from_volume(volume), "result", float)
      )
  case["heightVolume"] = height_volume

  movable = [
    r
    for r in located
    if isinstance(r, (Plate, Lid, TipRack, ResourceStack, Trash))
    or isinstance(r, Container)
    and not _is_item(r)
  ]
  seats = []
  for parent in located:
    if not isinstance(parent, (ResourceHolder, ResourceStack, PlateAdapter)):
      continue
    for child in rng.sample(movable, min(6, len(movable))):
      if child.rotation.x or child.rotation.y:
        continue
      seats.append(
        {"parent": parent.name, "child": child.name}
        | _attempt(lambda: _plr_seat(parent, child), "location", _vec)
      )
  for lidded in [r for r in located if isinstance(r, (Plate, TipRack))][:6]:
    for lid in [r for r in everything if isinstance(r, Lid)][:2]:
      seats.append(
        {"parent": lidded.name, "child": lid.name, "location": _vec(lidded.get_lid_location(lid))}
      )
  spots = [r for r in everything if isinstance(r, TipSpot) and r.tip is not None]
  for spot in spots[:: max(1, len(spots) // 8)]:
    tip = spot.tip
    assert tip is not None
    seats.append(
      {"parent": spot.name, "child": tip.name, "location": _vec(resting_location(spot, tip))}
    )
  for shaft in [r for r in everything if isinstance(r, TipMountingShaft) and r.tip is not None]:
    tool = shaft.tip
    assert tool is not None and tool.location is not None
    seats.append({"parent": shaft.name, "child": tool.name, "location": _vec(tool.location)})
  case["seats"] = seats

  turns = []
  for resource in rng.sample(located, min(30, len(located))):
    pivot = rng.choice([None, Coordinate(rng.uniform(0, 40), rng.uniform(0, 40), 2.5)])
    saved, location = resource.rotation, resource.location
    angles = (saved.x, saved.y, saved.z)
    if rng.random() < 0.5:
      by = Rotation(x=rng.choice([0, 12]), y=rng.choice([0, -30]), z=rng.uniform(-200, 200))
      resource.rotate(x=by.x, y=by.y, z=by.z, pivot_coordinate=pivot)
      turn: Dict[str, Any] = {"by": _rot(by)}
    else:
      to_z = rng.uniform(-400, 400)
      resource.rotate_to(z=to_z, pivot_coordinate=pivot)
      turn = {"to": {"z": to_z}}
    assert resource.location is not None
    turns.append(
      {"resource": resource.name}
      | turn
      | ({} if pivot is None else {"pivot": _vec(pivot)})
      | {"rotation": _rot(resource.rotation), "location": _vec(resource.location)}
    )
    resource.rotation = Rotation(*angles)
    resource.location = location
  case["turns"] = turns

  jaws = []
  for gripper in [r for r in everything if isinstance(r, MechanicalGripper)]:
    for width in (gripper.jaw_range[0], 87.3, gripper.jaw_range[1], gripper.jaw_range[1] + 0.1):
      try:
        gripper.jaw_width = width
      except ValueError:
        jaws.append({"gripper": gripper.name, "jawWidth": width, "refused": True})
        continue
      parts = gripper.jaws or gripper.fingers
      jaws.append(
        {
          "gripper": gripper.name,
          "jawWidth": width,
          "parts": [
            {"resource": p.name, "location": _vec(p.location or Coordinate.zero())} for p in parts
          ],
        }
      )
  case["jaws"] = jaws

  placements = []
  pickable = [r for r in in_frame if isinstance(r, (Plate, Lid, TipRack)) and not _is_item(r)]
  destinations = [
    r
    for r in in_frame
    if isinstance(r, (ResourceHolder, ResourceStack, PlateAdapter, Trash, Plate))
  ]
  for resource in pickable[:8]:
    for destination in rng.sample(destinations, min(10, len(destinations))):
      turn_by = rng.choice([0, 90, 180, 270])
      placements.append(
        {
          "resource": resource.name,
          "destination": destination.name,
          "rotationAppliedByMove": turn_by,
        }
        | _attempt(
          lambda: get_place_location(
            resource, destination, frame, rotation_applied_by_move=turn_by
          ),
          "location",
          _vec,
        )
      )
    target = Coordinate(rng.uniform(0, 500), rng.uniform(0, 400), rng.uniform(50, 200))
    placements.append(
      {"resource": resource.name, "destinationLocation": _vec(target), "rotationAppliedByMove": 0}
      | {"location": _vec(get_place_location(resource, target, frame))}
    )
  case["placements"] = placements

  grips = []
  for resource in [r for r in pickable if not _under_stack_top(r)][:10]:
    for key, distance in (("distanceFromTop", 4.0), ("distanceFromBottom", 2.5)):
      try:
        height = get_grip_height(
          resource,
          pickup_distance_from_top=distance if key == "distanceFromTop" else None,
          pickup_distance_from_bottom=distance if key == "distanceFromBottom" else None,
        )
      except _REFUSALS:
        grips.append({"resource": resource.name, key: distance, "refused": True})
        continue
      location = get_grip_location(resource, get_pick_location(resource, frame), frame, height)
      grips.append(
        {"resource": resource.name, key: distance}
        | {"gripHeight": height, "gripLocation": _vec(location)}
      )
  case["grips"] = grips

  occupancy = []
  solid = [r for r in in_frame if r is not frame and r.get_size_z() > 0]
  for _ in range(25):
    inside = rng.choice(solid)
    corner = inside.get_location_wrt(frame)
    point = rng.choice(
      [
        corner,
        corner + Coordinate(rng.uniform(-3, 8), rng.uniform(-3, 8), rng.uniform(-1, 4)),
        inside.get_location_wrt(frame, "c", "c", "c"),
      ]
    )
    exclude = rng.choice([[], [inside]])
    found = get_resource_at_location(point, frame, exclude=exclude)
    occupancy.append(
      {
        "location": _vec(point),
        "exclude": [r.name for r in exclude],
        "found": None if found is None else found.name,
      }
    )
  case["occupancy"] = occupancy

  if isinstance(frame, HamiltonDeck):
    case["tracks"] = [
      {"track": track, "location": _vec(frame.track_to_location(track))}
      for track in range(-4, frame.num_tracks + 2, 3)
    ]
  return case


def generate() -> Dict[str, Any]:
  """Every case, from the scenes as they are built now."""
  rng = random.Random(1)
  with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    cases = [_scene_case(scene, rng) for scene in build_all()]
  return {"format": FORMAT, "tolerance": TOLERANCE, "cases": cases}


def dumps(vectors: Dict[str, Any]) -> str:
  """The file's text: one line per entry, so a change reads as a diff."""
  lines = ["{", f'"format": {vectors["format"]},', f'"tolerance": {vectors["tolerance"]},']
  lines.append('"cases": [')
  for c, case in enumerate(vectors["cases"]):
    lines.append("{")
    keys = list(case)
    for k, key in enumerate(keys):
      value = case[key]
      end = "," if k < len(keys) - 1 else ""
      if isinstance(value, list):
        lines.append(f"{json.dumps(key)}: [")
        lines += [
          json.dumps(entry, sort_keys=True) + ("," if e < len(value) - 1 else "")
          for e, entry in enumerate(value)
        ]
        lines.append("]" + end)
      else:
        lines.append(f"{json.dumps(key)}: {json.dumps(value, sort_keys=True)}{end}")
    lines.append("}" + ("," if c < len(vectors["cases"]) - 1 else ""))
  lines += ["]", "}"]
  return "\n".join(lines) + "\n"


def load() -> Dict[str, Any]:
  loaded: Dict[str, Any] = json.loads(PATH.read_text())
  return loaded


# -- checking the store's geometry against them --------------------------------------------------


def close(a: float, b: float) -> bool:
  return math.isclose(a, b, rel_tol=0, abs_tol=TOLERANCE)


def close_vec(ours: Any, expected: Dict[str, float]) -> bool:
  return all(close(o, expected.get(axis, 0.0)) for o, axis in zip(ours, "xyz"))


def close_angles(ours: Rotation, expected: Dict[str, float]) -> bool:
  def wrap(angle: float) -> float:
    return (angle + 180) % 360 - 180

  return all(
    abs(wrap(o - expected.get(axis, 0.0))) <= TOLERANCE
    for o, axis in zip((ours.x, ours.y, ours.z), "xyz")
  )


def _anchor(entry: Dict[str, Any]) -> Tuple[pb.AnchorX, pb.AnchorY, pb.AnchorZ]:
  anchor = json_format.ParseDict(entry["anchor"], pb.Anchor())
  return anchor.x, anchor.y, anchor.z


def check_case(case: Dict[str, Any]) -> List[str]:
  """What the store's geometry gets wrong in a case: one line per disagreement."""
  root = build_tree(json_format.ParseDict(case["tree"], pb.ResourceDefinition()))
  nodes: Dict[str, Node] = {node.name: node for node in root.walk()}
  frame = nodes[case["frame"]]
  placements: g.Placements = {}
  wrong: List[str] = []

  def expect(what: str, entry: Dict[str, Any], key: str, compute: Callable[[], Any], same) -> None:
    try:
      ours = compute()
    except ServerError as refused:
      if not entry.get("refused"):
        wrong.append(f"{what}: refused ({refused}), expected {entry.get(key)}")
      return
    if entry.get("refused"):
      wrong.append(f"{what}: answered {ours}, expected a refusal")
    elif not same(ours, entry[key]):
      wrong.append(f"{what}: {ours} != {entry[key]}")

  for entry in case["locations"]:
    node = nodes[entry["resource"]]
    x, y, z = _anchor(entry)
    if "frame" in entry:
      in_frame = nodes[entry["frame"]]
      expect(
        f"{node.name} at {entry['anchor']} in {in_frame.name}",
        entry,
        "location",
        lambda: g.location_wrt(node, in_frame, x, y, z, placements),
        close_vec,
      )
    else:
      expect(
        f"{node.name} at {entry['anchor']}",
        entry,
        "location",
        lambda: g.absolute_location(node, x, y, z, placements),
        close_vec,
      )

  for entry in case["resources"]:
    node = nodes[entry["resource"]]
    checks: List[Tuple[str, Any, Callable[[Any, Any], bool]]] = [
      ("size", g.size(node), close_vec),
      ("absoluteSize", g.absolute_size(node), close_vec),
      ("absoluteRotation", g.absolute_rotation(node), close_angles),
      ("rotationInFrame", g.rotation_wrt(node, frame), close_angles),
      ("center", g.center(node), close_vec),
    ]
    if "highestPoint" in entry:
      checks.append(("highestPoint", g.highest_point(node, placements), close))
    for key, ours, same in checks:
      if not same(ours, entry[key]):
        wrong.append(f"{node.name} {key}: {ours} != {entry[key]}")

  for entry in case["heightVolume"]:
    node = nodes[entry["resource"]]
    if "height" in entry:
      expect(
        f"{node.name} at {entry['height']} mm",
        entry,
        "result",
        lambda: g.volume_from_height(node, entry["height"]),
        close,
      )
    else:
      expect(
        f"{node.name} holding {entry['volume']} uL",
        entry,
        "result",
        lambda: g.height_from_volume(node, entry["volume"]),
        close,
      )

  for entry in case["seats"]:
    parent, child = nodes[entry["parent"]], nodes[entry["child"]]
    expect(
      f"{child.name} seated in {parent.name}",
      entry,
      "location",
      lambda: _seat(parent, child),
      close_vec,
    )

  for entry in case["turns"]:
    node = nodes[entry["resource"]]
    if "by" in entry:
      by = entry["by"]
      rotation = g.turned_by(node.rotation, Rotation(by.get("x", 0), by.get("y", 0), by["z"]))
    else:
      to = entry["to"]
      rotation = g.turned_to(node.rotation, to.get("x"), to.get("y"), to.get("z"))
    location = node.location
    if "pivot" in entry:
      pivot = entry["pivot"]
      location = g.pivoted_location(
        node, rotation, Coordinate(pivot.get("x", 0), pivot.get("y", 0), pivot.get("z", 0))
      )
    if not close_angles(rotation, entry["rotation"]):
      wrong.append(f"{node.name} turned: {rotation} != {entry['rotation']}")
    if location is None or not close_vec(location, entry["location"]):
      wrong.append(f"{node.name} turned to {location}, expected {entry['location']}")

  for entry in case["jaws"]:
    gripper = nodes[entry["gripper"]]

    def standing(moved: List[Tuple[Node, Coordinate]], expected: List[Dict[str, Any]]) -> bool:
      return len(moved) == len(expected) and all(
        part.name == want["resource"] and close_vec(location, want["location"])
        for (part, location), want in zip(moved, expected)
      )

    expect(
      f"{gripper.name} open {entry['jawWidth']}",
      entry,
      "parts",
      lambda: g.jaw_locations(gripper, entry["jawWidth"]),
      standing,
    )

  for entry in case["placements"]:
    node = nodes[entry["resource"]]
    destination: Any = (
      nodes[entry["destination"]]
      if "destination" in entry
      else Coordinate(**{a: entry["destinationLocation"].get(a, 0.0) for a in "xyz"})
    )
    expect(
      f"{node.name} into {entry.get('destination', entry.get('destinationLocation'))}",
      entry,
      "location",
      lambda: g.place_location(node, destination, frame, entry["rotationAppliedByMove"]),
      close_vec,
    )

  for entry in case["grips"]:
    node = nodes[entry["resource"]]
    top, bottom = entry.get("distanceFromTop"), entry.get("distanceFromBottom")
    expect(
      f"{node.name} gripped {top} from the top, {bottom} from the bottom",
      entry,
      "gripHeight",
      lambda: g.grip_height(node, top, bottom),
      close,
    )
    if not entry.get("refused"):
      height = g.grip_height(node, top, bottom)
      location = g.grip_location(node, g.location_wrt(node, frame), frame, height)
      if not close_vec(location, entry["gripLocation"]):
        wrong.append(f"{node.name} grip at {location}, expected {entry['gripLocation']}")

  for entry in case["occupancy"]:
    point = Coordinate(**{a: entry["location"].get(a, 0.0) for a in "xyz"})
    found = g.resource_at(frame, point, [nodes[name] for name in entry["exclude"]], placements)
    if (None if found is None else found.name) != entry["found"]:
      wrong.append(f"at {point}: {None if found is None else found.name} != {entry['found']}")

  for entry in case.get("tracks", []):
    location = g.track_location(frame, entry["track"])
    if not close_vec(location, entry["location"]):
      wrong.append(f"track {entry['track']}: {location} != {entry['location']}")
  return wrong


def _seat(parent: Node, child: Node) -> Coordinate:
  location = g.default_child_location(parent, child)
  if location is None:
    raise ServerError(pb.PROBLEM_CANNOT_DROP_HERE, f"'{parent.name}' does not seat children")
  return location


if __name__ == "__main__":
  PATH.parent.mkdir(exist_ok=True)
  PATH.write_text(dumps(generate()))
  print(f"wrote {PATH}")
