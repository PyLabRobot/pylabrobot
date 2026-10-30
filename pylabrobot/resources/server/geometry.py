"""The geometry a store computes, from the tree alone.

Every answer is decided by a resource's kind, its attributes and where it hangs, as
`Resource` and its subclasses compute it. Locations are `Coordinate`s, so they are rounded as
PLR rounds them, and rotations are `Rotation`s, composed as PLR composes them.
"""

from __future__ import annotations

import math
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.tree import Node, copy_tree, to_coordinate, to_rotation_message
from pylabrobot.resources.server.v1 import types_pb2 as pb
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3

K = pb.ResourceKind

CONTAINER_KINDS = frozenset(
  {
    K.RESOURCE_KIND_CONTAINER,
    K.RESOURCE_KIND_WELL,
    K.RESOURCE_KIND_TROUGH,
    K.RESOURCE_KIND_TUBE,
    K.RESOURCE_KIND_PETRI_DISH,
    K.RESOURCE_KIND_TRASH,
  }
)
LIDDABLE_KINDS = CONTAINER_KINDS | {K.RESOURCE_KIND_PLATE, K.RESOURCE_KIND_TIP_RACK}
HOLDER_KINDS = frozenset(
  {
    K.RESOURCE_KIND_RESOURCE_HOLDER,
    K.RESOURCE_KIND_PLATE_HOLDER,
    K.RESOURCE_KIND_EMBEDDED_TIP_RACK_HOLDER,
  }
)
TOOL_KINDS = frozenset({K.RESOURCE_KIND_TIP, K.RESOURCE_KIND_HEAD_TOOL})

_IDENTITY = (1.0, 0.0, 0.0, 0.0)


class Size(NamedTuple):
  """How far a resource reaches along each of its own axes, in mm."""

  x: float
  y: float
  z: float


# -- sizes ---------------------------------------------------------------------------------------


def lid_of(node: Node) -> Optional[Node]:
  """The lid seated on a resource: its first child that is a lid."""
  return next((child for child in node.children if child.kind == K.RESOURCE_KIND_LID), None)


def _stacking_z_height(node: Node) -> Optional[float]:
  """How far a plate or standing tip rack adds to a stack of its own kind, if it says."""
  if node.kind == K.RESOURCE_KIND_PLATE and node.facts.plate.HasField("stacking_z_height"):
    return node.facts.plate.stacking_z_height
  if node.kind == K.RESOURCE_KIND_TIP_RACK and node.facts.tip_rack.HasField("stacking_z_height"):
    return node.facts.tip_rack.stacking_z_height
  return None


def _is_standing_tip_rack(node: Node) -> bool:
  return node.kind == K.RESOURCE_KIND_TIP_RACK and node.facts.tip_rack.style in (
    pb.TIP_RACK_STYLE_STANDING,
    pb.TIP_RACK_STYLE_NESTED,
  )


def _occupied_height(node: Node) -> float:
  """The height a resource takes up in a stack, with its lid on."""
  lid = lid_of(node)
  if node.kind in (K.RESOURCE_KIND_PLATE, K.RESOURCE_KIND_TIP_RACK) and lid is not None:
    return size(node).z + size(lid).z - lid.facts.lid.nesting_z_height
  return size(node).z


def nesting_overlap(stack: Node, upper: Node, lower: Optional[Node]) -> float:
  """How far `upper` sinks into `lower` in a stack growing in z; 0 where they do not nest.

  A bare plate on a bare plate, or a standing tip rack on a bare standing tip rack, nests when the
  upper one states its stacking height.
  """
  if stack.facts.stack.direction != pb.STACK_DIRECTION_Z or lower is None:
    return 0.0
  stacking = _stacking_z_height(upper)
  if stacking is None or lid_of(lower) is not None:
    return 0.0
  if upper.kind == K.RESOURCE_KIND_PLATE and lower.kind == K.RESOURCE_KIND_PLATE:
    return size(upper).z - stacking
  if _is_standing_tip_rack(upper) and _is_standing_tip_rack(lower):
    return size(upper).z - stacking
  return 0.0


def _stack_size(stack: Node) -> Size:
  """A stack's size, from what it holds."""
  if not stack.children:
    return Size(0.0, 0.0, 0.0)
  direction = stack.facts.stack.direction
  sizes = [size(child) for child in stack.children]
  x = sum(s.x for s in sizes) if direction == pb.STACK_DIRECTION_X else max(s.x for s in sizes)
  y = sum(s.y for s in sizes) if direction == pb.STACK_DIRECTION_Y else max(s.y for s in sizes)
  if direction != pb.STACK_DIRECTION_Z:
    return Size(x, y, max(_occupied_height(child) for child in stack.children))
  z = 0.0
  lower: Optional[Node] = None
  for child in stack.children:
    z += _occupied_height(child) - nesting_overlap(stack, child, lower)
    lower = child
  return Size(x, y, z)


def size(node: Node) -> Size:
  """A resource's size along its own axes: its own, or a stack's from what it holds."""
  if node.kind == K.RESOURCE_KIND_RESOURCE_STACK:
    return _stack_size(node)
  s = node.facts.size
  return Size(s.x, s.y, s.z)


# -- anchors and centres -------------------------------------------------------------------------


def anchor(
  node: Node,
  x: pb.AnchorX = pb.ANCHOR_X_LEFT,
  y: pb.AnchorY = pb.ANCHOR_Y_FRONT,
  z: pb.AnchorZ = pb.ANCHOR_Z_BOTTOM,
) -> Coordinate:
  """A point on a resource, from its left front bottom corner, along its own axes.

  Raises:
    ServerError: The cavity bottom of something that is not a container, or of a container that
      does not say how thick its bottom is.
  """
  s = size(node)
  x_ = {
    pb.ANCHOR_X_UNSPECIFIED: 0.0,
    pb.ANCHOR_X_LEFT: 0.0,
    pb.ANCHOR_X_CENTER: s.x / 2,
    pb.ANCHOR_X_RIGHT: s.x,
  }[x]
  y_ = {
    pb.ANCHOR_Y_UNSPECIFIED: 0.0,
    pb.ANCHOR_Y_FRONT: 0.0,
    pb.ANCHOR_Y_CENTER: s.y / 2,
    pb.ANCHOR_Y_BACK: s.y,
  }[y]
  if z == pb.ANCHOR_Z_CAVITY_BOTTOM:
    if node.kind not in CONTAINER_KINDS:
      raise ServerError(
        pb.PROBLEM_WRONG_KIND, f"'{node.name}' is not a container: it has no cavity", node.name
      )
    if not node.facts.container.HasField("material_z_thickness"):
      raise ServerError(
        pb.PROBLEM_WRONG_KIND,
        f"'{node.name}' does not say how thick its bottom is, so its cavity bottom is unknown",
        node.name,
      )
    return Coordinate(x_, y_, node.facts.container.material_z_thickness)
  z_ = {
    pb.ANCHOR_Z_UNSPECIFIED: 0.0,
    pb.ANCHOR_Z_BOTTOM: 0.0,
    pb.ANCHOR_Z_CENTER: s.z / 2,
    pb.ANCHOR_Z_TOP: s.z,
  }[z]
  return Coordinate(x_, y_, z_)


def center(node: Node) -> Coordinate:
  """The middle of a resource's footprint, on its bottom: `Resource.center()`."""
  s = size(node)
  return Coordinate(s.x / 2, s.y / 2, 0)


# -- where a resource is -------------------------------------------------------------------------


def absolute_rotation(node: Node) -> Rotation:
  """A resource's rotation along its root's axes."""
  if node.parent is None:
    return node.rotation
  return absolute_rotation(node.parent) + node.rotation


def _turn(matrix: Optional[List[List[float]]], vector: Coordinate) -> Coordinate:
  if matrix is None:
    return vector
  return Coordinate(*matrix_vector_multiply_3x3(matrix, vector.vector()))


class _Placed(NamedTuple):
  """Where a resource's origin is along its root's axes, and how it is turned there."""

  position: Coordinate
  rotation: Rotation
  matrix: Optional[List[List[float]]]


Placements = Dict[int, _Placed]
"""Where resources are, kept for the length of one question about many of them, by node id."""


def _placed(node: Node, placements: Placements) -> _Placed:
  """Where a resource's origin is, built on where its parent's is.

  The walk up stops at the first ancestor without a location: that is where the frame starts. Each
  step adds its parent's anchor and its own location, turned, as `get_absolute_location` adds
  them, so the answer is the same to the last bit; it is only not worked out again for a sibling.
  """
  known = placements.get(id(node))
  if known is not None:
    return known
  location = node.location
  assert location is not None
  parent = node.parent
  if parent is None or parent.location is None:
    rotation = absolute_rotation(node)
    matrix = None if rotation._quaternion == _IDENTITY else rotation.get_rotation_matrix()
    placed = _Placed(location, rotation, matrix)
  else:
    above = _placed(parent, placements)
    position = above.position + (
      _turn(above.matrix, anchor(parent)) + _turn(above.matrix, location)
    )
    rotation, matrix = above.rotation, above.matrix
    if node.rotation._quaternion != _IDENTITY:
      rotation = rotation + node.rotation
      matrix = rotation.get_rotation_matrix()
    placed = _Placed(position, rotation, matrix)
  placements[id(node)] = placed
  return placed


def absolute_location(
  node: Node,
  x: pb.AnchorX = pb.ANCHOR_X_LEFT,
  y: pb.AnchorY = pb.ANCHOR_Y_FRONT,
  z: pb.AnchorZ = pb.ANCHOR_Z_BOTTOM,
  placements: Optional[Placements] = None,
) -> Coordinate:
  """Where a point on a resource is, along its root's axes.

  Args:
    node: the resource.
    x: the point across it.
    y: the point along it.
    z: the point up it.
    placements: where resources are, shared by the questions of one request.

  Raises:
    ServerError: The resource has no location.
  """
  if node.location is None:
    raise ServerError(pb.PROBLEM_NO_LOCATION, f"'{node.name}' has no location", node.name)
  placed = _placed(node, {} if placements is None else placements)
  return placed.position + _turn(placed.matrix, anchor(node, x=x, y=y, z=z))


def is_in_subtree_of(node: Node, other: Node) -> bool:
  """Whether `node` is `other` or hangs somewhere below it."""
  current: Optional[Node] = node
  while current is not None:
    if current is other:
      return True
    current = current.parent
  return False


class _FrameView(NamedTuple):
  """A frame's origin along its root's axes, and what turns a vector onto its own axes."""

  origin: Coordinate
  inverse: Optional[List[List[float]]]


def _frame_view(frame: Node, placements: Placements) -> _FrameView:
  origin = (
    absolute_location(frame, placements=placements)
    if frame.location is not None
    else Coordinate(0, 0, 0)
  )
  rotation = absolute_rotation(frame)
  if rotation._quaternion == _IDENTITY:
    return _FrameView(origin, None)
  return _FrameView(origin, [list(row) for row in zip(*rotation.get_rotation_matrix())])


def _seen_from(view: _FrameView, point: Coordinate) -> Coordinate:
  """A point along its root's axes, measured from a frame along the frame's axes."""
  offset = point - view.origin
  if view.inverse is None:
    return offset
  return Coordinate(*matrix_vector_multiply_3x3(view.inverse, offset.vector()))


def location_wrt(
  node: Node,
  frame: Node,
  x: pb.AnchorX = pb.ANCHOR_X_LEFT,
  y: pb.AnchorY = pb.ANCHOR_Y_FRONT,
  z: pb.AnchorZ = pb.ANCHOR_Z_BOTTOM,
  placements: Optional[Placements] = None,
) -> Coordinate:
  """Where a point on a resource is, from `frame`'s left front bottom corner, along its axes.

  Args:
    node: the resource.
    frame: what the point is measured from, an ancestor of the resource or the resource itself.
    x: the point across it.
    y: the point along it.
    z: the point up it.
    placements: where resources are, shared by the questions of one request.

  Raises:
    ServerError: The resource is not in `frame`'s subtree, or has no location.
  """
  if not is_in_subtree_of(node, frame):
    raise ServerError(
      pb.PROBLEM_NOT_IN_FRAME, f"'{node.name}' is not in the subtree of '{frame.name}'", node.name
    )
  placements = {} if placements is None else placements
  view = _frame_view(frame, placements)
  return _seen_from(view, absolute_location(node, x=x, y=y, z=z, placements=placements))


def rotation_wrt(node: Node, frame: Node) -> Rotation:
  """A resource's rotation along `frame`'s axes, angle by angle."""
  turned, along = absolute_rotation(node), absolute_rotation(frame)
  return Rotation(x=turned.x - along.x, y=turned.y - along.y, z=turned.z - along.z)


def absolute_size(node: Node) -> Size:
  """A resource's bounding box once turned, along its root's axes."""
  matrix = absolute_rotation(node).get_rotation_matrix()
  s = size(node)
  corners = [
    Coordinate(*matrix_vector_multiply_3x3(matrix, [cx, cy, cz]))
    for cx, cy, cz in [
      (0, 0, 0),
      (s.x, 0, 0),
      (0, s.y, 0),
      (s.x, s.y, 0),
      (0, 0, s.z),
      (s.x, 0, s.z),
      (0, s.y, s.z),
      (s.x, s.y, s.z),
    ]
  ]
  return Size(
    max(c.x for c in corners) - min(c.x for c in corners),
    max(c.y for c in corners) - min(c.y for c in corners),
    max(c.z for c in corners) - min(c.z for c in corners),
  )


def highest_point(node: Node, placements: Optional[Placements] = None) -> float:
  """The highest top of a resource and everything under it, along its root's axes.

  A deck's own top does not count: what stands on it does.

  Raises:
    ServerError: It, or something under it, has no location.
  """
  placements = {} if placements is None else placements
  highest = (
    0.0
    if node.kind == K.RESOURCE_KIND_DECK
    else absolute_location(node, z=pb.ANCHOR_Z_TOP, placements=placements).z
  )
  for child in node.children:
    highest = max(highest, highest_point(child, placements))
  return highest


# -- where a child is seated ---------------------------------------------------------------------


def rotation_offset(node: Node, rotation: Optional[Rotation] = None) -> Coordinate:
  """How far a resource turned about z must shift for its footprint to start at its parent's corner.

  A resource turns about its own origin, so turned a quarter its footprint swings out of the
  corner it was placed at: `get_child_location`.

  Args:
    node: the resource placed.
    rotation: its rotation, when it is not the one it has.

  Raises:
    ServerError: It is turned about x or y, or not by a multiple of 90 degrees about z.
  """
  rotation = node.rotation if rotation is None else rotation
  if not rotation.y == rotation.x == 0:
    raise ServerError(
      pb.PROBLEM_CANNOT_DROP_HERE,
      f"'{node.name}' is turned about x or y; only a turn about z is seated",
      node.name,
    )
  z = rotation.z % 360
  snapped = round(z / 90) * 90
  if not math.isclose(z, snapped, rel_tol=0, abs_tol=1e-7):
    raise ServerError(
      pb.PROBLEM_CANNOT_DROP_HERE,
      f"'{node.name}' is turned {rotation.z} degrees; only a multiple of 90 is seated",
      node.name,
    )
  s = size(node)
  return {
    0: Coordinate(0, 0, 0),
    90: Coordinate(s.y, 0, 0),
    180: Coordinate(s.x, s.y, 0),
    270: Coordinate(0, s.x, 0),
  }[snapped % 360]


def plate_sinking_depth(holder: Node, plate: Node) -> float:
  """How far a plate sinks onto a plate holder's pedestal: the pedestal's height, at most as deep
  as its wells stand off its bottom.

  Raises:
    ServerError: The plate's wells do not all stand at the same height.
  """
  heights = {
    round(well_location.z, 2)
    for well in plate.walk()
    if well is not plate and well.facts.category == "well"
    for well_location in [well.location]
    if well_location is not None
  }
  if len(heights) != 1:
    raise ServerError(
      pb.PROBLEM_CANNOT_DROP_HERE,
      f"the wells of '{plate.name}' do not all stand at the same height",
      plate.name,
    )
  return min(abs(holder.facts.holder.pedestal_size_z), heights.pop())


def holder_child_location(
  holder: Node, child: Node, child_rotation: Optional[Rotation] = None
) -> Coordinate:
  """Where a holder seats a child: its child location, the plate sunk onto a plate holder's
  pedestal, an embedded tip rack centred over its opening and sunk into it.

  Raises:
    ServerError: An embedded tip rack holder given anything but an embedded tip rack.
  """
  turned = rotation_offset(child, child_rotation)
  child_location = to_coordinate(holder.facts.holder.child_location)
  if holder.kind == K.RESOURCE_KIND_EMBEDDED_TIP_RACK_HOLDER:
    if not (
      child.kind == K.RESOURCE_KIND_TIP_RACK
      and child.facts.tip_rack.style == pb.TIP_RACK_STYLE_EMBEDDED
    ):
      raise ServerError(
        pb.PROBLEM_WRONG_KIND,
        f"'{holder.name}' holds only an embedded tip rack, not '{child.name}'",
        child.name,
      )
    rack_x, rack_y = size(child).x, size(child).y
    rotation_z = (child.rotation if child_rotation is None else child_rotation).z
    if round(rotation_z / 90) % 2:
      rack_x, rack_y = rack_y, rack_x
    holder_size = size(holder)
    return (
      turned
      + Coordinate(
        x=(holder_size.x - rack_x) / 2,
        y=(holder_size.y - rack_y) / 2,
        z=-child.facts.tip_rack.sinking_depth,
      )
      + child_location
    )
  location = turned + child_location
  if holder.kind == K.RESOURCE_KIND_PLATE_HOLDER:
    sinking = 0.0
    if child.kind == K.RESOURCE_KIND_PLATE:
      sinking = plate_sinking_depth(holder, child)
    elif child.kind == K.RESOURCE_KIND_RESOURCE_STACK and child.children:
      if child.children[0].kind == K.RESOURCE_KIND_PLATE:
        sinking = plate_sinking_depth(holder, child.children[0])
    location = location + Coordinate(0, 0, -sinking)
  return location


def stack_child_location(
  stack: Node, child: Node, child_rotation: Optional[Rotation] = None
) -> Coordinate:
  """Where a stack seats a child: at its growing edge, sunk into the one below when they nest."""
  lower = stack.children[-1] if stack.children else None
  s = size(stack)
  direction = stack.facts.stack.direction
  edge = {
    pb.STACK_DIRECTION_X: Coordinate(s.x, 0, 0),
    pb.STACK_DIRECTION_Y: Coordinate(0, s.y, 0),
    pb.STACK_DIRECTION_Z: Coordinate(0, 0, s.z),
  }[direction]
  overlap = Coordinate(0, 0, nesting_overlap(stack, child, lower))
  return rotation_offset(child, child_rotation) + edge - overlap


def _uniform_spacing(values: List[float]) -> Optional[float]:
  if len(values) < 2:
    return None
  differences = {round(values[i + 1] - values[i], 2) for i in range(len(values) - 1)}
  if len(differences) != 1:
    raise ServerError(pb.PROBLEM_CANNOT_DROP_HERE, "well spacing has to be uniform")
  return differences.pop()


def plate_adapter_location(adapter: Node, plate: Node) -> Coordinate:
  """Where a plate adapter seats a plate: its first well's centre over the first hole's.

  Raises:
    ServerError: The plate's wells are not spaced as the adapter's holes are, or not uniformly.
  """
  spec = adapter.facts.plate_adapter
  well_locations = [child.location for child in plate.children]
  xs = sorted({location.x for location in well_locations if location is not None})
  ys = sorted({location.y for location in well_locations if location is not None})
  item_dx, item_dy = _uniform_spacing(xs), _uniform_spacing(ys)
  if (
    item_dx is None
    or item_dy is None
    or (abs(item_dx), abs(item_dy))
    != (
      spec.adapter_hole_dx,
      spec.adapter_hole_dy,
    )
  ):
    raise ServerError(
      pb.PROBLEM_CANNOT_DROP_HERE,
      f"the wells of '{plate.name}' are not spaced as the holes of '{adapter.name}'",
      plate.name,
    )
  well = absolute_size(plate.children[0])
  return Coordinate(
    spec.dx - float(xs[0]) + spec.adapter_hole_size_x / 2 - well.x / 2,
    spec.dy - float(ys[0]) + spec.adapter_hole_size_y / 2 - well.y / 2,
    spec.dz + spec.plate_z_offset,
  )


def lid_location(parent: Node, lid: Node, lid_rotation: Optional[Rotation] = None) -> Coordinate:
  """Where a lid is seated: centred on its parent's top, sunk by its nesting height."""
  return (
    rotation_offset(lid, lid_rotation)
    + anchor(parent, pb.ANCHOR_X_CENTER, pb.ANCHOR_Y_CENTER, pb.ANCHOR_Z_TOP)
    - anchor(lid, pb.ANCHOR_X_CENTER, pb.ANCHOR_Y_CENTER, pb.ANCHOR_Z_BOTTOM)
    - Coordinate(0, 0, lid.facts.lid.nesting_z_height)
  )


def _pick_up_point(tool: Node) -> Coordinate:
  """Where a tool is held: its stated pick-up location, or the centre of its top."""
  spec = tool.facts.head_tool
  if spec.HasField("pick_up_location"):
    return to_coordinate(spec.pick_up_location)
  return anchor(tool, pb.ANCHOR_X_CENTER, pb.ANCHOR_Y_CENTER, pb.ANCHOR_Z_TOP)


def resting_location(spot: Node, tip: Node) -> Coordinate:
  """Where a tip rests in its spot: centred on it, its pick-up point a collar's height above."""
  collar = tip.facts.tip.collar_height if tip.facts.tip.HasField("collar_height") else 0.0
  pick_up = _pick_up_point(tip)
  spot_size = size(spot)
  return Coordinate(
    x=spot_size.x / 2 - pick_up.x,
    y=spot_size.y / 2 - pick_up.y,
    z=collar - pick_up.z,
  )


def mount_location(shaft: Node, tool: Node) -> Coordinate:
  """Where a tool hangs on a shaft: its pick-up point on the shaft's axis, a fitting depth above
  the shaft's end, turned however the tool is turned."""
  grip = _pick_up_point(tool).rotated(tool.rotation)
  shaft_size = size(shaft)
  return Coordinate(
    x=shaft_size.x / 2 - grip.x,
    y=shaft_size.y / 2 - grip.y,
    z=tool.facts.head_tool.fitting_depth - grip.z,
  )


def default_child_location(parent: Node, child: Node) -> Optional[Coordinate]:
  """Where `parent` seats `child` when no location is given, or None when it does not.

  A spot rests a tip by its collar; a shaft mounts a tool; a holder seats at its child location;
  a stack at its edge; a plate adapter aligns a plate's wells over its holes; a trash keeps what
  is thrown in centred on its floor; a lid sits on top.

  Raises:
    ServerError: The parent cannot take a child of this kind.
  """
  if parent.kind == K.RESOURCE_KIND_TIP_SPOT and child.kind == K.RESOURCE_KIND_TIP:
    return resting_location(parent, child)
  if parent.kind == K.RESOURCE_KIND_TIP_MOUNTING_SHAFT and child.kind in TOOL_KINDS:
    return mount_location(parent, child)
  if parent.kind in HOLDER_KINDS:
    return holder_child_location(parent, child)
  if parent.kind == K.RESOURCE_KIND_RESOURCE_STACK:
    return stack_child_location(parent, child)
  if parent.kind == K.RESOURCE_KIND_PLATE_ADAPTER:
    if child.kind != K.RESOURCE_KIND_PLATE:
      raise ServerError(
        pb.PROBLEM_WRONG_KIND, f"only a plate goes onto plate adapter '{parent.name}'", child.name
      )
    return plate_adapter_location(parent, child)
  if parent.kind == K.RESOURCE_KIND_TRASH:
    return trash_location(parent, child)
  if parent.kind in LIDDABLE_KINDS and child.kind == K.RESOURCE_KIND_LID:
    return lid_location(parent, child)
  return None


def trash_location(trash: Node, child: Node) -> Coordinate:
  """Where a trash keeps what is thrown into it: centred on its floor."""
  inside, held = size(trash), size(child)
  return Coordinate((inside.x - held.x) / 2, (inside.y - held.y) / 2, 0)


def track_location(deck: Node, track: int) -> Coordinate:
  """Where a Hamilton deck's track starts, counted from 1, in the deck's frame.

  Raises:
    ServerError: The deck has no tracks.
  """
  if not deck.facts.deck.HasField("hamilton"):
    raise ServerError(pb.PROBLEM_WRONG_KIND, f"'{deck.name}' has no tracks", deck.name)
  spec = deck.facts.deck.hamilton
  origin = to_coordinate(spec.track_origin)
  return Coordinate(origin.x + (track - 1) * spec.track_pitch, origin.y, origin.z)


# -- height and volume ---------------------------------------------------------------------------


def _height_volume_data(container: Node) -> Dict[float, float]:
  """A container's heights and the volumes they hold, checked.

  Raises:
    ServerError: It has fewer than two points, or its volumes do not rise with height.
  """
  points = container.facts.container.height_volume_data
  if container.kind not in CONTAINER_KINDS or len(points) < 2:
    raise ServerError(
      pb.PROBLEM_NO_HEIGHT_VOLUME_DATA,
      f"'{container.name}' has no height-volume data",
      container.name,
    )
  data = {float(point.height): float(point.volume) for point in points}
  volumes = [data[height] for height in sorted(data)]
  if any(volumes[i] >= volumes[i + 1] for i in range(len(volumes) - 1)):
    raise ServerError(
      pb.PROBLEM_NO_HEIGHT_VOLUME_DATA,
      f"the volumes of '{container.name}' do not rise strictly with height",
      container.name,
    )
  return data


def _interpolate(x: float, data: Dict[float, float], name: str) -> float:
  """Piecewise linear, refusing what lies outside the data: `interpolate_1d` with errors."""
  xs = sorted(data)
  ys = [data[k] for k in xs]
  if x < xs[0] or x > xs[-1]:
    raise ServerError(
      pb.PROBLEM_OUTSIDE_HEIGHT_VOLUME_DATA,
      f"{x} is outside the height-volume data of '{name}', {xs[0]} to {xs[-1]}",
      name,
    )
  for i, xv in enumerate(xs):
    if x == xv:
      return ys[i]
  for i in range(1, len(xs)):
    if xs[i] >= x:
      x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
      return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
  raise AssertionError("unreachable: x lies within the data")


def volume_from_height(container: Node, height: float) -> float:
  """The volume a container holds when its liquid stands `height` above its cavity bottom."""
  return _interpolate(height, _height_volume_data(container), container.name)


def height_from_volume(container: Node, volume: float) -> float:
  """How high above its cavity bottom `volume` stands in a container."""
  data = _height_volume_data(container)
  return _interpolate(volume, {v: h for h, v in data.items()}, container.name)


# -- moving and turning --------------------------------------------------------------------------


def turned_by(rotation: Rotation, by: Rotation) -> Rotation:
  """A rotation turned further about its parent's axes: `Resource.rotate`, angles kept to 0-360."""
  turned = Rotation(rotation.x, rotation.y, rotation.z)
  turned._prepend(by)
  return turned


def turned_to(
  rotation: Rotation, x: Optional[float], y: Optional[float], z: Optional[float]
) -> Rotation:
  """A rotation with some of its angles set: `Resource.rotate_to`, angles kept to 0-360."""
  return Rotation(
    x=rotation.x if x is None else x % 360,
    y=rotation.y if y is None else y % 360,
    z=rotation.z if z is None else z % 360,
  )


def pivoted_location(node: Node, rotation: Rotation, pivot: Coordinate) -> Coordinate:
  """Where a resource is once turned to `rotation` about `pivot`, a point in its own frame that
  stays where it was.

  Raises:
    ServerError: The resource has no location to move.
  """
  location = node.location
  if location is None:
    raise ServerError(
      pb.PROBLEM_NO_LOCATION, f"'{node.name}' has no location, so a pivot cannot be held", node.name
    )
  before = absolute_rotation(node).get_rotation_matrix()
  after_rotation = rotation if node.parent is None else absolute_rotation(node.parent) + rotation
  after = after_rotation.get_rotation_matrix()
  was = matrix_vector_multiply_3x3(before, pivot.vector())
  now = matrix_vector_multiply_3x3(after, pivot.vector())
  shift = Coordinate(was[0] - now[0], was[1] - now[1], was[2] - now[2])
  parent = node.parent
  if parent is not None and parent.location is not None:
    turned = absolute_rotation(parent).get_rotation_matrix()
    transpose = [[turned[j][i] for j in range(3)] for i in range(3)]
    shift = Coordinate(*matrix_vector_multiply_3x3(transpose, shift.vector()))
  return location + shift


def jaw_locations(gripper: Node, jaw_width: float) -> List[Tuple[Node, Coordinate]]:
  """Where a gripper's fingers stand, or the jaws carrying them, with `jaw_width` between them.

  Returns:
    Each part that moves, with its location.

  Raises:
    ServerError: The width is outside the jaws' range, or a named part is missing.
  """
  spec = gripper.facts.mechanical_gripper
  if not spec.jaw_width_min <= jaw_width <= spec.jaw_width_max:
    raise ServerError(
      pb.PROBLEM_OUT_OF_RANGE,
      f"the jaws open {spec.jaw_width_min} to {spec.jaw_width_max} mm, not {jaw_width}",
      gripper.name,
    )

  def part(name: str) -> Node:
    found = gripper.find(name)
    if found is None:
      raise ServerError(pb.PROBLEM_NOT_FOUND, f"'{gripper.name}' has no part '{name}'", name)
    return found

  moved: List[Tuple[Node, Coordinate]] = []
  grip_y = spec.proximal_joint.y + spec.tool_center_point.y
  for index, (finger_name, side) in enumerate(zip(spec.fingers, (1.0, -1.0))):
    finger = part(finger_name)
    facing = grip_y + side * jaw_width / 2.0
    corner = facing if side > 0 else facing - size(finger).y
    on_its_carrier = finger.location
    assert on_its_carrier is not None
    if spec.jaws:
      jaw = part(spec.jaws[index])
      here = jaw.location
      assert here is not None
      moved.append((jaw, Coordinate(here.x, corner - on_its_carrier.y, here.z)))
    else:
      moved.append((finger, Coordinate(on_its_carrier.x, corner, on_its_carrier.z)))
  return moved


def reference_point(node: Node) -> Coordinate:
  """Where a device part's drives report it, from its left front bottom corner: a channel's or a
  multi-channel head's stated point, its corner otherwise."""
  if node.kind == K.RESOURCE_KIND_PIPETTE_CHANNEL:
    return to_coordinate(node.facts.pipette_channel.reference_point)
  if node.kind == K.RESOURCE_KIND_N_CHANNEL_PIPETTE:
    return to_coordinate(node.facts.n_channel_pipette.reference_point)
  return Coordinate(0, 0, 0)


def location_from_reference(
  node: Node,
  frame: Node,
  x: Optional[float],
  y: Optional[float],
  z: Optional[float],
  reference: Coordinate,
) -> Coordinate:
  """A device part's location in its parent, from where its reference point is in `frame`.

  Each axis given is converted on its own, so the parent is taken to lie along `frame`'s axes, as
  a channel on its arm does on a deck. An axis not given keeps the location it has.

  Raises:
    ServerError: The part has no parent or no location.
  """
  here, parent = node.location, node.parent
  if here is None or parent is None:
    raise ServerError(
      pb.PROBLEM_NO_LOCATION, f"'{node.name}' has no location in a parent to move it in", node.name
    )
  on_parent = location_wrt(parent, frame)
  return Coordinate(
    here.x if x is None else x - on_parent.x - reference.x,
    here.y if y is None else y - on_parent.y - reference.y,
    here.z if z is None else z - on_parent.z - reference.z,
  )


# -- picking up and placing ----------------------------------------------------------------------


def turned_copy(node: Node, turn_z: float) -> Node:
  """A copy of a resource and everything under it, belonging to no tree, turned further about z:
  `Resource.rotated(z=...)`."""
  copy = copy_tree(node)
  copy.own.rotation.CopyFrom(to_rotation_message(turned_by(copy.rotation, Rotation(z=turn_z))))
  return copy


def rotation_in_destination(
  resource: Node,
  destination: Union[Node, Coordinate],
  frame: Node,
  rotation_applied_by_move: float = 0,
) -> float:
  """The z rotation a resource has against its destination's axes once placed, in degrees."""
  along = frame if isinstance(destination, Coordinate) else destination
  return rotation_wrt(resource, along).z + rotation_applied_by_move


def place_location(
  resource: Node,
  destination: Union[Node, Coordinate],
  frame: Node,
  rotation_applied_by_move: float = 0,
) -> Coordinate:
  """Where a resource's origin goes in `frame` when placed into `destination`.

  A destination seats it as it seats a child, turned as the move turns it; a location is where it
  goes; a trash, and anything that does not seat children, takes it at its own origin.

  Raises:
    ServerError: A stack that does not grow in z, a turn a stack cannot take, or anything but a
      plate onto a plate adapter.
  """
  if isinstance(destination, Coordinate):
    return destination
  rotation = rotation_in_destination(resource, destination, frame, rotation_applied_by_move)
  turn = rotation - resource.rotation.z
  turned = resource if turn == 0 else turned_copy(resource, turn)
  along = rotation_wrt(destination, frame)
  child: Coordinate
  if destination.kind == K.RESOURCE_KIND_RESOURCE_STACK:
    if destination.facts.stack.direction != pb.STACK_DIRECTION_Z:
      raise ServerError(
        pb.PROBLEM_CANNOT_DROP_HERE,
        f"stack '{destination.name}' does not grow in z: a resource is placed only onto one that "
        "does",
        resource.name,
      )
    if rotation % 180 != 0:
      raise ServerError(
        pb.PROBLEM_CANNOT_DROP_HERE,
        f"'{resource.name}' would be turned {rotation} degrees against stack "
        f"'{destination.name}': a stack takes it only turned by a multiple of 180 degrees",
        resource.name,
      )
    child = stack_child_location(destination, turned).rotated(along)
  elif destination.kind == K.RESOURCE_KIND_TRASH:
    child = Coordinate.zero()
  elif destination.kind in HOLDER_KINDS:
    child = holder_child_location(destination, turned).rotated(along)
  elif destination.kind == K.RESOURCE_KIND_PLATE_ADAPTER:
    if turned.kind != K.RESOURCE_KIND_PLATE:
      raise ServerError(
        pb.PROBLEM_WRONG_KIND,
        f"'{resource.name}' is not a plate: only a plate goes onto plate adapter "
        f"'{destination.name}'",
        resource.name,
      )
    child = plate_adapter_location(destination, turned).rotated(along)
  elif destination.kind in LIDDABLE_KINDS and turned.kind == K.RESOURCE_KIND_LID:
    child = lid_location(destination, turned).rotated(along)
  else:
    child = Coordinate.zero()
  return location_wrt(destination, frame) + child


def placed_child_location(destination: Node, frame: Node, location: Coordinate) -> Coordinate:
  """A placed resource's location in its destination, from its origin's location in `frame`."""
  along = rotation_wrt(destination, frame)
  return (location - location_wrt(destination, frame)).rotated(
    Rotation(x=-along.x, y=-along.y, z=-along.z)
  )


def grip_height(
  resource: Node,
  distance_from_top: Optional[float] = None,
  distance_from_bottom: Optional[float] = None,
) -> float:
  """How far above a resource's bottom the fingers close on it. Exactly one distance is given.

  Raises:
    ServerError: Neither or both distances are given, or the grip is outside the resource.
  """
  size_z = absolute_size(resource).z
  if distance_from_top is not None and distance_from_bottom is None:
    height = size_z - distance_from_top
  elif distance_from_bottom is not None and distance_from_top is None:
    height = distance_from_bottom
  else:
    raise ServerError(
      pb.PROBLEM_OUT_OF_RANGE,
      "exactly one of distance_from_top and distance_from_bottom is given",
      resource.name,
    )
  if not 0 <= height <= size_z:
    raise ServerError(
      pb.PROBLEM_OUT_OF_RANGE,
      f"the grip must lie within '{resource.name}', 0 to {size_z} mm above its bottom, is "
      f"{height} mm",
      resource.name,
    )
  return height


def grip_location(resource: Node, location: Coordinate, frame: Node, height: float) -> Coordinate:
  """A resource's centre at the grip height in `frame`, with its origin at `location`."""
  grip = location + center(resource).rotated(rotation_wrt(resource, frame))
  return Coordinate(grip.x, grip.y, grip.z + height)


# -- what is where -------------------------------------------------------------------------------


def _holds(node: Node, view: _FrameView, location: Coordinate, placements: Placements) -> bool:
  """Whether `location` is in a resource's bounding box in a frame, right, back and top excluded."""
  corners = [
    _seen_from(view, absolute_location(node, x, y, z, placements=placements))
    for x in (pb.ANCHOR_X_LEFT, pb.ANCHOR_X_RIGHT)
    for y in (pb.ANCHOR_Y_FRONT, pb.ANCHOR_Y_BACK)
    for z in (pb.ANCHOR_Z_BOTTOM, pb.ANCHOR_Z_TOP)
  ]
  return (
    min(c.x for c in corners) <= location.x < max(c.x for c in corners)
    and min(c.y for c in corners) <= location.y < max(c.y for c in corners)
    and min(c.z for c in corners) <= location.z < max(c.z for c in corners)
  )


def resource_at(
  frame: Node,
  location: Coordinate,
  exclude: Sequence[Node] = (),
  placements: Optional[Placements] = None,
) -> Optional[Node]:
  """The deepest resource under `frame` whose bounding box holds `location`, or None.

  The first found wins among equally deep ones, in `all_children` order.

  Args:
    frame: what the location is measured from, and whose subtree is searched.
    location: the point, along `frame`'s axes from its left front bottom corner.
    exclude: resources to leave out, with everything under them.
    placements: where resources are, shared by the questions of one request.
  """

  def depth_below(node: Node) -> int:
    count, current = 0, node.parent
    while current is not None and current is not frame:
      count, current = count + 1, current.parent
    return count

  placements = {} if placements is None else placements
  view = _frame_view(frame, placements)
  found: Optional[Node] = None
  found_depth = -1
  for node in frame.all_children():
    if any(is_in_subtree_of(node, excluded) for excluded in exclude):
      continue
    if node.location is None or not _holds(node, view, location, placements):
      continue
    depth = depth_below(node)
    if depth > found_depth:
      found, found_depth = node, depth
  return found
