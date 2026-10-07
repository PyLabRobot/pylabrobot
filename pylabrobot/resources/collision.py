"""Collision checks of motions against a resource tree: what moves, swept, against what stands.

A motion is checked without checking everything against everything. What moves is known - the
groups a command moves, each rigid - so each group's solid pieces are swept along its way, and only
the pieces standing still that a sweep's bounding box meets, found in a bounding-volume tree, are
tested exactly. Two groups moving at once are tested against each other only over the same slice of
time.

Solids. Every resource is a box - its size, where it is and how it is turned - and it is taken to
be solid or not by how it holds its children:

- a resource with no children is solid, unless it is flat (a site, a trash's opening);
- a plate, a tip rack, any itemized resource, is solid as a whole: its items are inside it, but what
  sits in its items - a tip in a tip spot - is looked at too, as is what sits on it - a lid seated
  on a plate;
- a resource whose children lie outside its box - a channel with its tip mounting shaft below it, a
  finger with its pad - is solid, and so are its children;
- a resource whose children lie inside its box - a deck, a carrier, an arm - is a frame: only its
  base, from its bottom up to the lowest thing it holds, is solid (a carrier's body under its
  sites, the deck's slab), and its children are looked at in turn.

Sweeps. A group's way is cut into segments; each segment is the convex hull of the group's pieces
at a few poses, grown by a slack. The hull is exact for a straight move (its two ends) and for moves
on independent axes (the corners of the box they span); a turn is cut into short arcs, each grown
by the most any point strays from the chord - `turn_slack`.

The exact test is GJK: the distance between two convex hulls, each grown by a radius. Things are in
collision when closer than the clearance asked for; things that touch - a plate resting on its site
- are not, by `CONTACT` of slack.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import math
import os
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.resource import Resource

Vec = Tuple[float, float, float]

# How far two things may overlap and still only touch, in mm: a plate resting on a site, a finger on
# the side of what it grips. Every solid is drawn in by this much on each face, so things that touch
# stay a little apart and only things that go into each other meet.
CONTACT = 0.05


# -- vectors -------------------------------------------------------------------------------------


def _sub(a: Vec, b: Vec) -> Vec:
  """`a` less `b`, by coordinate."""
  return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec, b: Vec) -> Vec:
  """`a` plus `b`, by coordinate."""
  return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _dot(a: Vec, b: Vec) -> float:
  """The dot product of `a` and `b`."""
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _scale(a: Vec, k: float) -> Vec:
  """`a` with each coordinate multiplied by `k`."""
  return (a[0] * k, a[1] * k, a[2] * k)


def _vec(c: Coordinate) -> Vec:
  """`c` as a plain tuple."""
  return (c.x, c.y, c.z)


# -- poses ---------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Pose:
  """A rigid move: a turn of `turn` degrees about the vertical through `pivot`, then a shift."""

  turn: float = 0.0
  pivot: Vec = (0.0, 0.0, 0.0)
  shift: Vec = (0.0, 0.0, 0.0)

  def apply(self, p: Vec) -> Vec:
    """`p` carried by this pose: turned about its pivot, then shifted."""
    if self.turn:
      c, s = math.cos(math.radians(self.turn)), math.sin(math.radians(self.turn))
      x, y = p[0] - self.pivot[0], p[1] - self.pivot[1]
      p = (self.pivot[0] + c * x - s * y, self.pivot[1] + s * x + c * y, p[2])
    return _add(p, self.shift)

  def apply_all(self, points: Sequence[Vec]) -> List[Vec]:
    """Every point of `points`, carried by this pose: `apply` for many at once."""
    return [self.apply(p) for p in points]

  def _linear(self) -> Tuple[float, Vec]:
    """This pose as a turn about the origin and a shift: p -> R p + t."""
    moved = self.apply((0.0, 0.0, 0.0))
    return self.turn, moved

  def then(self, other: "Pose") -> "Pose":
    """This pose, followed by `other`."""
    turn, t = self._linear()
    return Pose(turn + other.turn, (0.0, 0.0, 0.0), other.apply(t))

  def inverse(self) -> "Pose":
    """The pose that undoes this one."""
    turn, t = self._linear()
    return Pose(-turn, (0.0, 0.0, 0.0), _scale(Pose(-turn).apply(t), -1.0))

  def after(self, x: float, y: float, z: float = 0.0) -> "Pose":
    """This pose, taken after a shift by (`x`, `y`, `z`) where things are now."""
    d = (x, y, z)
    return Pose(self.turn, _sub(self.pivot, d), _add(self.shift, d))

  @staticmethod
  def shifted(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> "Pose":
    """A shift by (`x`, `y`, `z`), no turn."""
    return Pose(shift=(x, y, z))


STILL = Pose()


# -- solid pieces --------------------------------------------------------------------------------


@dataclasses.dataclass
class Piece:
  """One convex solid: the corners of a box, and the resource it stands for."""

  resource: Resource
  points: List[Vec]

  def __post_init__(self) -> None:
    self.lo, self.hi = _bounds(self.points)


def _bounds(points: Iterable[Vec]) -> Tuple[Vec, Vec]:
  """The lowest and highest corner of `points`, by coordinate."""
  xs, ys, zs = zip(*points)
  return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _corners(
  resource: Resource, z_from: float = 0.0, z_to: Optional[float] = None, inset: float = 0.0
) -> List[Vec]:
  """The eight corners of `resource`'s box - or of the slab of it from `z_from` to `z_to` above its
  bottom - absolute, each face moved in by `inset`."""
  origin = _absolute(resource)
  rotation = resource.get_absolute_rotation()
  sx, sy, sz = resource.get_size_x(), resource.get_size_y(), resource.get_size_z()
  top = sz if z_to is None else z_to

  def span(a: float, b: float) -> Tuple[float, float]:
    return (a + inset, b - inset) if b - a > 2 * inset else ((a + b) / 2, (a + b) / 2)

  return [
    _vec(origin + Coordinate(x, y, z).rotated(rotation))
    for x in span(0.0, sx)
    for y in span(0.0, sy)
    for z in span(z_from, top)
  ]


def _absolute(resource: Resource) -> Coordinate:
  """Where `resource` is: absolute, or - in a tree whose root has not been put anywhere - in the
  root's own terms."""
  top = resource
  while top.parent is not None:
    top = top.parent
  if top.location is not None:
    return resource.get_absolute_location()
  return Coordinate.zero() if resource is top else resource.get_location_wrt(top)


def _flat(resource: Resource) -> bool:
  """Whether `resource` is thin enough on every axis to be a surface rather than a solid."""
  return min(resource.get_size_x(), resource.get_size_y(), resource.get_size_z()) <= CONTACT


# -- declared hulls ------------------------------------------------------------------------------
# A box is too coarse for a device with a cavity - a thermocycler whose plate sits in a recess, a
# body rising behind it - so a model may ship a convex decomposition beside its file:
# `<model>.collision.json`, `{"units": "mm", "hulls": [[[x, y, z], ...], ...]}` in the resource's
# own frame (front-left-bottom corner, Z up). A resource whose model has one is those hulls.

HULL_SUFFIX = ".collision.json"
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@functools.lru_cache(maxsize=None)
def _hull_files() -> Dict[str, str]:
  """Every hull file under the package, by the model it is for."""
  found: Dict[str, str] = {}
  for directory, _, files in os.walk(_PACKAGE_ROOT):
    for name in files:
      if name.endswith(HULL_SUFFIX):
        found[name[: -len(HULL_SUFFIX)]] = os.path.join(directory, name)
  return found


@functools.lru_cache(maxsize=None)
def _hulls_for_model(model: str) -> Optional[Tuple[Tuple[Vec, ...], ...]]:
  """The hulls of the model's file, scaled to mm, or None where the model ships none."""
  path = _hull_files().get(model)
  if path is None:
    return None
  with open(path, encoding="utf-8") as f:
    data = json.load(f)
  units = data.get("units", "mm")
  if units not in ("mm", "m"):
    raise ValueError(f"{path}: hulls are in {units!r}, and only 'mm' or 'm' are known")
  scale = {"mm": 1.0, "m": 1000.0}[units]
  return tuple(
    tuple((p[0] * scale, p[1] * scale, p[2] * scale) for p in hull) for hull in data["hulls"]
  )


def declared_hulls(resource: Resource) -> Optional[Tuple[Tuple[Vec, ...], ...]]:
  """The convex hulls `resource`'s model declares, in its own frame in mm, or None."""
  model = resource.model
  return _hulls_for_model(model) if model else None


def _hull_pieces(resource: Resource, hulls: Sequence[Sequence[Vec]]) -> List["Piece"]:
  """Each declared hull as a piece of `resource`, at `resource`'s place and turned as it is."""
  origin = _absolute(resource)
  rotation = resource.get_absolute_rotation()
  return [
    Piece(resource, [_vec(origin + Coordinate(*p).rotated(rotation)) for p in hull])
    for hull in hulls
  ]


def solid_pieces(
  root: Resource,
  leave_out: Optional[Set[int]] = None,
  hollow: Iterable[str] = (),
  reuse: Optional["_Reuse"] = None,
) -> List[Piece]:
  """Every solid piece under `root` (itself included), as the module docstring sets out, less
  anything in `leave_out` (resource ids) and what is under it.

  Args:
    hollow: categories of resource that are enclosures - a housing something travels into - and
      so not solid themselves, whatever their children.
    reuse: asked for a child's pieces, and for the lowest thing it holds, before the walk goes into
      it, with a way to work them out (`StaticScene` keeps them between checks).
  """
  leave_out = leave_out or set()
  enclosures = set(hollow)
  boxes: Dict[int, Tuple[Vec, Vec]] = {}
  lows: Dict[int, Optional[float]] = {}

  def box(resource: Resource) -> Tuple[Vec, Vec]:
    """`resource`'s box, worked out once."""
    if id(resource) not in boxes:
      boxes[id(resource)] = _bounds(_corners(resource))
    return boxes[id(resource)]

  def inside(child: Resource, parent: Resource) -> bool:
    """Whether `child` is held within `parent`'s box - its centre is - rather than hung on it."""
    (c_lo, c_hi), (p_lo, p_hi) = box(child), box(parent)
    return all(p_lo[k] - CONTACT <= (c_lo[k] + c_hi[k]) / 2 <= p_hi[k] + CONTACT for k in range(3))

  def lowest(resource: Resource) -> Optional[float]:
    """The lowest bottom of anything `resource` holds, left out things aside."""
    if id(resource) not in lows:
      if reuse is not None and resource is not root:
        lows[id(resource)] = reuse.lowest(resource, lambda: lowest_here(resource))
      else:
        lows[id(resource)] = lowest_here(resource)
    return lows[id(resource)]

  def lowest_here(resource: Resource) -> Optional[float]:
    """The lowest bottom of anything `resource` holds, worked out here."""
    low: Optional[float] = None
    for child in resource.children:
      if id(child) in leave_out:
        continue
      if not _flat(child) or not child.children:
        z = box(child)[0][2]
        low = z if low is None else min(low, z)
      below = lowest(child)
      if below is not None:
        low = below if low is None else min(low, below)
    return low

  def visit(resource: Resource, into: List[Piece]) -> None:
    """Walk `resource` for its pieces, through the kept-scene cache where there is one."""
    if id(resource) in leave_out:
      return
    if reuse is not None and resource is not root:
      into.extend(reuse.pieces(resource, lambda: work_out(resource)))
    else:
      visit_here(resource, into)

  def work_out(resource: Resource) -> List[Piece]:
    """`resource`'s pieces, walked here."""
    found: List[Piece] = []
    visit_here(resource, found)
    return found

  def visit_here(resource: Resource, into: List[Piece]) -> None:
    """Walk `resource` for its pieces by the module docstring's rules, its children after it."""
    children = [c for c in resource.children if id(c) not in leave_out]
    if resource.category in enclosures:
      for child in children:
        visit(child, into)
      return
    hulls = declared_hulls(resource)
    if hulls is not None:
      into.extend(_hull_pieces(resource, hulls))
      for child in children:
        visit(child, into)
      return
    if isinstance(resource, ItemizedResource):
      if not _flat(resource):
        into.append(Piece(resource, _corners(resource, inset=CONTACT)))
      for item in children:
        if isinstance(item, Lid):
          # A lid sits on the resource, not in its grid, and above its box: its own box is what is
          # there.
          visit(item, into)
          continue
        for held in item.children:
          visit(held, into)
      return
    if not children:
      if not _flat(resource):
        into.append(Piece(resource, _corners(resource, inset=CONTACT)))
      return
    if _flat(resource):
      pass
    elif any(inside(c, resource) for c in children):
      low = lowest(resource)
      base = (low - box(resource)[0][2]) if low is not None else resource.get_size_z()
      if base > 2 * CONTACT:
        top = min(base, resource.get_size_z())
        into.append(Piece(resource, _corners(resource, 0.0, top, CONTACT)))
    else:
      into.append(Piece(resource, _corners(resource, inset=CONTACT)))
    for child in children:
      visit(child, into)

  out: List[Piece] = []
  visit(root, out)
  return out


class _Reuse:
  """What `solid_pieces` asks before it walks into a resource: its pieces, and the lowest thing it
  holds, either kept from before or worked out with the function given."""

  def pieces(self, resource: Resource, work_out: Callable[[], List[Piece]]) -> List[Piece]:
    return work_out()

  def lowest(self, resource: Resource, work_out: Callable[[], Optional[float]]) -> Optional[float]:
    return work_out()


def _rotation(resource: Resource) -> Tuple[float, float, float]:
  """`resource`'s rotation as a plain tuple."""
  r = resource.rotation
  return (r.x, r.y, r.z)


class StaticScene(_Reuse):
  """The solid pieces of a tree, kept between checks: a check works out only what has changed
  since the last - a plate put down, a lid taken off - and takes the rest as it was.

  What a resource's pieces come from is its own box, how it stands (where it is, how it is turned,
  in absolute terms) and the same of everything under it; that is its key. A resource whose key is
  unchanged has the pieces it had. Anything with something left out of the check under it is worked
  out afresh, its unchanged parts still taken as they were.

  Items of an itemized resource - a plate's wells, a rack's spots - are taken to stay where their
  resource has them; only what they hold is looked at.
  """

  def __init__(self, root: Resource, hollow: Iterable[str] = ()):
    self.root = root
    self.hollow = tuple(hollow)
    self._kept: Dict[int, Tuple[Resource, tuple, List[Piece]]] = {}
    self._lows: Dict[int, Tuple[Resource, tuple, Optional[float]]] = {}
    self._keys: Dict[int, Optional[tuple]] = {}
    self._leave_out: Set[int] = set()
    self.worked_out = 0  # resources whose pieces were not kept, over the scene's life

  def _shape(self, resource: Resource) -> Optional[tuple]:
    """What `resource`'s pieces come from, under where it stands: None if anything under it is
    left out."""
    if id(resource) in self._keys:
      return self._keys[id(resource)]
    key: Optional[tuple]
    if id(resource) in self._leave_out:
      key = None
    else:
      if isinstance(resource, ItemizedResource):
        under = [h for item in resource.children for h in item.children]
      else:
        under = resource.children
      shapes = [self._shape(c) for c in under]
      if any(s is None for s in shapes):
        key = None
      else:
        loc = resource.location
        key = (
          id(resource),
          resource.name,
          (loc.x, loc.y, loc.z) if loc is not None else None,
          _rotation(resource),
          (resource.get_size_x(), resource.get_size_y(), resource.get_size_z()),
          resource.category,
          resource.model,
          len(resource.children),
          tuple(shapes),
        )
    self._keys[id(resource)] = key
    return key

  def _key(self, resource: Resource) -> Optional[tuple]:
    shape = self._shape(resource)
    if shape is None:
      return None
    where = _absolute(resource)
    turn = resource.get_absolute_rotation()
    return ((where.x, where.y, where.z), (turn.x, turn.y, turn.z), shape)

  def pieces(self, resource: Resource, work_out: Callable[[], List[Piece]]) -> List[Piece]:
    """`resource`'s pieces, kept from the last asking where nothing about it has changed."""
    key = self._key(resource)
    kept = self._kept.get(id(resource))
    if key is not None and kept is not None and kept[0] is resource and kept[1] == key:
      return kept[2]
    self.worked_out += 1
    found = work_out()
    if key is not None:
      self._kept[id(resource)] = (resource, key, found)
    return found

  def lowest(self, resource: Resource, work_out: Callable[[], Optional[float]]) -> Optional[float]:
    """The lowest thing `resource` holds, kept from the last asking where nothing has changed."""
    key = self._key(resource)
    kept = self._lows.get(id(resource))
    if key is not None and kept is not None and kept[0] is resource and kept[1] == key:
      return kept[2]
    low = work_out()
    if key is not None:
      self._lows[id(resource)] = (resource, key, low)
    return low

  def solid_pieces(self, leave_out: Iterable[Resource] = ()) -> List[Piece]:
    """Every solid piece under the root, less `leave_out` and what is under it."""
    self._leave_out = {id(r) for r in leave_out}
    self._keys = {}
    try:
      return solid_pieces(self.root, self._leave_out, self.hollow, reuse=self)
    finally:
      self._keys = {}

  def obstacles(self, leave_out: Iterable[Resource] = ()) -> "Obstacles":
    """The root's solid pieces as an obstacle tree, less `leave_out` and what is under it."""
    return Obstacles(self.solid_pieces(leave_out))


# -- the exact test: GJK distance ----------------------------------------------------------------


def _support(points: Sequence[Vec], d: Vec) -> Vec:
  """The point of `points` furthest along `d`."""
  best, best_dot = points[0], _dot(points[0], d)
  for p in points[1:]:
    k = _dot(p, d)
    if k > best_dot:
      best, best_dot = p, k
  return best


def _solve(g: List[List[float]], b: List[float]) -> Optional[List[float]]:
  """Solve the linear system `g` Â· x = `b` by Gaussian elimination, or None where it is singular."""
  n = len(b)
  m = [row[:] + [b[i]] for i, row in enumerate(g)]
  for col in range(n):
    pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
    if abs(m[pivot][col]) < 1e-12:
      return None
    m[col], m[pivot] = m[pivot], m[col]
    for r in range(n):
      if r != col:
        f = m[r][col] / m[col][col]
        for c in range(col, n + 1):
          m[r][c] -= f * m[col][c]
  return [m[i][n] / m[i][i] for i in range(n)]


def _closest_on_simplex(simplex: List[Vec]) -> Tuple[Vec, List[Vec]]:
  """The point of the simplex's hull nearest the origin, and the smallest face it lies on."""
  best: Optional[Tuple[float, Vec, List[Vec]]] = None
  n = len(simplex)
  for mask in range(1, 1 << n):
    face = [simplex[i] for i in range(n) if mask >> i & 1]
    p0 = face[0]
    edges = [_sub(p, p0) for p in face[1:]]
    if edges:
      mu = _solve([[_dot(a, b) for b in edges] for a in edges], [-_dot(a, p0) for a in edges])
      if mu is None or any(m < -1e-12 for m in mu) or sum(mu) > 1 + 1e-12:
        continue
      point = p0
      for m, e in zip(mu, edges):
        point = _add(point, _scale(e, m))
    else:
      point = p0
    d2 = _dot(point, point)
    if (
      best is None
      or d2 < best[0] - 1e-15
      or (abs(d2 - best[0]) <= 1e-15 and len(face) < len(best[2]))
    ):
      best = (d2, point, face)
  assert best is not None
  return best[1], best[2]


def distance(a: Sequence[Vec], b: Sequence[Vec], stop_beyond: float = math.inf) -> float:
  """The distance between the convex hulls of two point sets; 0 if they meet. Stops early, with a
  lower bound, once the hulls are sure to be further apart than `stop_beyond`."""
  v = _sub(a[0], b[0])
  simplex: List[Vec] = []
  for _ in range(64):
    vv = _dot(v, v)
    if vv < 1e-18:
      return 0.0
    w = _sub(_support(a, _scale(v, -1.0)), _support(b, v))
    vw = _dot(v, w)
    if vw > 0 and vw * vw > stop_beyond * stop_beyond * vv:
      return vw / math.sqrt(vv)  # a separating plane further than asked for
    if vv - vw <= 1e-9 * max(vv, 1.0):
      return math.sqrt(vv)
    simplex.append(w)
    v, simplex = _closest_on_simplex(simplex)
    if len(simplex) == 4:
      return 0.0
  return math.sqrt(_dot(v, v))


# -- the bounding-volume tree --------------------------------------------------------------------


class _Node:
  __slots__ = ("lo", "hi", "left", "right", "pieces")

  def __init__(self, pieces: List[Piece]):
    self.lo, self.hi = _bounds([p.lo for p in pieces] + [p.hi for p in pieces])
    self.left: Optional[_Node] = None
    self.right: Optional[_Node] = None
    self.pieces: List[Piece] = []
    if len(pieces) <= 4:
      self.pieces = pieces
      return
    axis = max(range(3), key=lambda k: self.hi[k] - self.lo[k])
    pieces = sorted(pieces, key=lambda p: p.lo[axis] + p.hi[axis])
    half = len(pieces) // 2
    self.left, self.right = _Node(pieces[:half]), _Node(pieces[half:])


def _meets(lo: Vec, hi: Vec, lo2: Vec, hi2: Vec) -> bool:
  """Whether the box from `lo` to `hi` meets the box from `lo2` to `hi2`, by coordinate."""
  return all(lo[k] <= hi2[k] and lo2[k] <= hi[k] for k in range(3))


class Obstacles:
  """The pieces that stand still, in a bounding-volume tree."""

  def __init__(self, pieces: List[Piece]):
    self.pieces = pieces
    self._root = _Node(pieces) if pieces else None
    self.visited = 0  # boxes looked at, to show the tree keeps queries small

  def near(self, lo: Vec, hi: Vec) -> List[Piece]:
    """The pieces whose boxes meet the box from `lo` to `hi`."""
    found: List[Piece] = []
    stack = [self._root] if self._root is not None else []
    while stack:
      node = stack.pop()
      self.visited += 1
      if not _meets(lo, hi, node.lo, node.hi):
        continue
      if node.left is None:
        found.extend(p for p in node.pieces if _meets(lo, hi, p.lo, p.hi))
      else:
        stack.append(node.left)
        if node.right is not None:
          stack.append(node.right)
    return found


# -- motions -------------------------------------------------------------------------------------


@dataclasses.dataclass
class Segment:
  """A stretch of a group's way: the hull of its pieces at `poses`, grown by `slack`, taken over
  `start` to `end` in time."""

  poses: List[Pose]
  slack: float = 0.0
  start: float = 0.0
  end: float = 1.0


def _through_poses(points: Sequence[Vec], poses: Sequence[Pose]) -> List[Vec]:
  """`points` carried by every pose: the hull inputs along a segment's way."""
  return [carried for pose in poses for carried in pose.apply_all(points)]


def _carried(points: Sequence[Vec], poses: Sequence[Pose], frame: Sequence[Pose]) -> List[Vec]:
  """`points` carried by every pose of the way, as the world sees them through every pose of the
  frame: the way itself, then where the frame holds it."""
  own = [pose.apply_all(points) for pose in poses]
  return [seen for at in own for where in frame for seen in where.apply_all(at)]


@dataclasses.dataclass
class Group:
  """Things that move together, rigidly: their solid pieces as they are now, and their way, as
  segments of poses relative to now.

  A group may also ride a frame that moves it without this group's own segments saying so - parts
  mounted on one carriage, say, carried by its X while each keeps its own way besides. The frame is
  the carriage's own segments. Where a group's frame is asked for, its poses are relative to the
  frame, and anything the group is checked against sees the frame applied; two groups on one frame
  are compared relative to it, which says exactly what one does to the other.
  """

  name: str
  pieces: List[Piece]
  segments: List[Segment]
  frame: Optional[List[Segment]] = None

  def swept(self, k: int) -> List[Tuple[Piece, List[Vec], float]]:
    """Each piece's hull over segment `k`, and its slack."""
    segment = self.segments[k]
    return [
      (piece, _through_poses(piece.points, segment.poses), segment.slack) for piece in self.pieces
    ]

  def frame_poses(self, segment: Segment) -> List[Pose]:
    """The frame's poses over the stretch `segment` is taken over: its way while it is moving, and
    where it last came to rest while it is not."""
    if not self.frame:
      return [STILL]
    poses: List[Pose] = []
    rest: Pose = STILL
    for f in self.frame:
      if min(f.end, segment.end) > max(f.start, segment.start):
        poses += f.poses
      elif f.end <= segment.start:
        rest = f.poses[-1]
    return poses or [rest]

  def swept_in_place(self, k: int) -> List[Tuple[Piece, List[Vec], float]]:
    """Each piece's hull over segment `k` as the world sees it, the frame applied."""
    segment = self.segments[k]
    frame = self.frame_poses(segment)
    return [
      (piece, _carried(piece.points, segment.poses, frame), segment.slack) for piece in self.pieces
    ]

  def _frame_at(self, t: float) -> Pose:
    """The pose of the frame at time `t`: its way while it is moving, where it last came to rest
    while it is not, and where it starts before its way begins."""
    if not self.frame:
      return STILL
    rest = STILL
    for segment in self.frame:
      if segment.start <= t <= segment.end and segment.end > segment.start:
        f = (t - segment.start) / (segment.end - segment.start)
        return _pose_between(segment.poses[0], segment.poses[-1], f)
      if segment.end <= t:
        rest = segment.poses[-1]
    return rest

  def pose_at(self, t: float, relative: bool = False) -> Pose:
    """The group's pose at time `t`, as the world sees it: its own way between the segment's poses,
    and the frame it rides carrying it. `relative` leaves the frame off - its own way alone, for a
    pair judged relative to the frame they share. Before its way, where it stands; after it, where
    its way ends."""
    rest = STILL
    for segment in self.segments:
      if (
        segment.start <= t <= segment.end
        and segment.end > segment.start
        and math.isfinite(segment.end - segment.start)
      ):
        f = (t - segment.start) / (segment.end - segment.start)
        own = (
          _pose_between(segment.poses[0], segment.poses[-1], f)
          if len(segment.poses) == 2
          else segment.poses[round(f * (len(segment.poses) - 1))]
        )
        return own if relative else own.then(self._frame_at(t))
      if segment.end <= t and math.isfinite(segment.end):
        rest = segment.poses[-1]
    return rest if relative else rest.then(self._frame_at(t))


def moving(
  name: str,
  resources: Iterable[Resource],
  segments: List[Segment],
  leave_out: Iterable[Resource] = (),
) -> Group:
  """A group of `resources`, each with everything under it but `leave_out`, moving along
  `segments`."""
  out = {id(r) for r in leave_out}
  return Group(name, [p for r in resources for p in solid_pieces(r, out)], segments)


def straight(shift: Vec, start: float = 0.0, end: float = 1.0) -> List[Segment]:
  """A straight move by `shift`: exact."""
  return [Segment([STILL, Pose(shift=shift)], 0.0, start, end)]


def on_axes(shift: Vec, start: float = 0.0, end: float = 1.0) -> List[Segment]:
  """A move by `shift` with each axis on its own profile, so along no known line: the box it spans."""
  corners = {(x, y, z) for x in (0.0, shift[0]) for y in (0.0, shift[1]) for z in (0.0, shift[2])}
  return [Segment([Pose(shift=c) for c in sorted(corners)], 0.0, start, end)]


def turn_slack(radius: float, degrees: float) -> float:
  """How far from the chord a point `radius` from a pivot strays, turning `degrees`."""
  return radius * (1.0 - math.cos(math.radians(abs(degrees)) / 2.0))


def turning(
  pieces: Sequence[Piece],
  pivot: Vec,
  degrees: float,
  step: float = 5.0,
  start: float = 0.0,
  end: float = 1.0,
  shift: Vec = (0.0, 0.0, 0.0),
) -> List[Segment]:
  """A turn of `degrees` about the vertical through `pivot` - shifted by `shift` along the way, in
  step - cut into arcs of at most `step` degrees, each grown by the most any piece strays."""
  n = max(1, math.ceil(abs(degrees) / step))
  radius = max(
    (math.hypot(p[0] - pivot[0], p[1] - pivot[1]) for piece in pieces for p in piece.points),
    default=0.0,
  )
  slack = turn_slack(radius, degrees / n)
  poses = [Pose(degrees * k / n, pivot, _scale(shift, k / n)) for k in range(n + 1)]
  return [
    Segment(
      [poses[k], poses[k + 1]],
      slack,
      start + (end - start) * k / n,
      start + (end - start) * (k + 1) / n,
    )
    for k in range(n)
  ]


def trapezoid(
  distance: float, speed: float, acceleration: Optional[float]
) -> Tuple[float, Callable[[float], float]]:
  """A trapezoidal (or triangular) profile over `distance`: its duration, and the distance covered
  by a time."""
  d = abs(distance)
  if d == 0 or speed <= 0:
    return 0.0, lambda t: 0.0
  if not acceleration:
    return d / speed, lambda t: min(d, max(0.0, t) * speed)
  ramp = speed / acceleration
  if acceleration * ramp * ramp >= d:  # never reaches speed
    ramp = math.sqrt(d / acceleration)
    speed = acceleration * ramp
    cruise = 0.0
  else:
    cruise = (d - acceleration * ramp * ramp) / speed
  total = 2 * ramp + cruise

  def covered(t: float) -> float:
    t = min(max(t, 0.0), total)
    if t < ramp:
      return 0.5 * acceleration * t * t
    if t < ramp + cruise:
      return 0.5 * acceleration * ramp * ramp + speed * (t - ramp)
    left = total - t
    return d - 0.5 * acceleration * left * left

  return total, covered


def profiled(
  shift: Vec,
  speed: float,
  acceleration: Optional[float] = None,
  start: float = 0.0,
  slices: int = 8,
) -> List[Segment]:
  """A straight move by `shift` on a trapezoidal profile starting at `start` s, cut into `slices`
  of equal time, so that two things moving at once are compared only while both are there."""
  length = math.sqrt(_dot(shift, shift))
  total, covered = trapezoid(length, speed, acceleration)
  if length == 0:
    return [Segment([STILL], 0.0, start, start)]
  unit = _scale(shift, 1.0 / length)
  times = [total * k / slices for k in range(slices + 1)]
  poses = [Pose(shift=_scale(unit, covered(t))) for t in times]
  return [
    Segment([poses[k], poses[k + 1]], 0.0, start + times[k], start + times[k + 1])
    for k in range(slices)
  ]


# -- the check -----------------------------------------------------------------------------------


@dataclasses.dataclass
class Collision:
  mover: Resource
  obstacle: Resource
  group: str
  segment: int
  gap: float  # how far apart they come, in mm: 0 when they meet
  other_group: Optional[str] = None
  when: Optional[float] = None
  """How far into the way's time the mover was when it first met, in the check's own units - one
  command or plan step is one - worked out by walking the way finely. None where the sweep met
  without a sampled pose doing so, and where both parties are on their way."""
  at: Optional[Pose] = None
  """The mover's pose then, relative to where it stands: what brings it to the meeting."""

  def __str__(self) -> str:
    against = f" (moving with {self.other_group})" if self.other_group else ""
    way = f", at {self.when:.2f} into the way" if self.when is not None else ""
    return (
      f"{self.mover.name} ({self.group}, segment {self.segment}{way}) comes within {self.gap:.2f} "
      f"mm of {self.obstacle.name}{against}"
    )


# How finely a reported sweep is walked to find where the meeting happens: the most shift or turn
# between one sampled pose and the next, and the most samples one stretch is walked in (a very long
# way is walked coarser than this, rather than in more steps).
SAMPLE_SHIFT = 1.0
SAMPLE_TURN_DEG = 1.0
SAMPLE_MOST = 512


def _pose_between(p0: Pose, p1: Pose, f: float) -> Pose:
  """The way between two poses, taken straight: a turn about the origin and a shift, each eased.
  Exact where the move is a shift; where it turns, this strays from the true turn about the pivot
  by as much as the pivot is far from the origin, which the walk that uses it only asks for where
  the meeting is, not whether it is."""
  turn0, shift0 = p0.turn, p0.apply((0.0, 0.0, 0.0))
  turn1, shift1 = p1.turn, p1.apply((0.0, 0.0, 0.0))
  return Pose(
    turn0 + (turn1 - turn0) * f,
    (0.0, 0.0, 0.0),
    (
      shift0[0] + (shift1[0] - shift0[0]) * f,
      shift0[1] + (shift1[1] - shift0[1]) * f,
      shift0[2] + (shift1[2] - shift0[2]) * f,
    ),
  )


def _samples(segment: Segment) -> List[Tuple[float, Pose]]:
  """A segment's way, finely: how far into its time, and the pose then.

  The way is walked at about a millimetre or a degree a step. A segment of two poses moves from one
  to the other. A segment of more poses spans a box on independent axes, along no known line, so
  its way is walked straight from pose to pose: where the drive actually goes is the box's to hide,
  and the walk only says where along such a line the meeting would be.
  """
  most = len(segment.poses) - 1
  out: List[Tuple[float, Pose]] = []
  for k in range(most):
    a, b = segment.poses[k], segment.poses[k + 1]
    span = max(
      math.hypot(
        b.shift[0] - a.shift[0],
        b.shift[1] - a.shift[1],
        b.shift[2] - a.shift[2],
      )
      / SAMPLE_SHIFT,
      abs(b.turn - a.turn) / SAMPLE_TURN_DEG,
      1.0,
    )
    n = min(math.ceil(span), SAMPLE_MOST)
    for j in range(n):
      out.append(((k + j / n) / most, _pose_between(a, b, j / n)))
  out.append((1.0, segment.poses[-1]))
  return out


def check(
  root: Resource,
  groups: Sequence[Group],
  clearance: float = 0.0,
  allow: Iterable[Resource] = (),
  obstacles: Optional[Obstacles] = None,
  between_groups: Union[bool, Callable[[str, str], bool]] = True,
  allow_for: Optional[Mapping[str, Iterable[Resource]]] = None,
) -> List[Collision]:
  """Every place where something moving in `groups` comes within `clearance` of something standing
  still under `root`, or of something in another group at the same time.

  Args:
    root: what the moves happen in, a deck or everything around it.
    groups: what moves, and how.
    clearance: how close is too close, in mm. 0 reports only things that meet.
    allow: things meant to be touched - what is picked up, where it is put down: never reported.
    obstacles: the pieces standing still, if already worked out (`Obstacles(solid_pieces(...))`,
      leaving out everything in `groups`).
    between_groups: whether the groups are checked against each other too, over the times they
      share; a callable of two group names asks per pair - parts of one machine that nothing here
      moves relative to each other are let off.
    allow_for: more things meant to be touched, by group name: by that group only.

  A meeting against what stands still is walked finely once it is found: the way is sampled, and
  the collision carries where the mover first met (`when`, `at`), so what it ran into can be
  brought to where the meeting happened and held there. Between groups, the meeting stands as the
  sweep found it - except where one group stands still while the other passes, which is walked
  finely as well.
  """
  allowed = {id(r) for r in allow}
  if obstacles is None:
    leave_out = {id(piece.resource) for g in groups for piece in g.pieces}
    obstacles = Obstacles(solid_pieces(root, leave_out))
  reach = max(clearance, 0.0)
  found: List[Collision] = []
  reported: Dict[Tuple[int, int, str, Optional[str]], Collision] = {}

  def report(
    mover: Piece,
    obstacle: Resource,
    group: str,
    k: int,
    gap: float,
    other: Optional[str] = None,
    when: Optional[float] = None,
    at: Optional[Pose] = None,
  ) -> None:
    """Say one meeting of `mover` and `obstacle` in `group`, keeping the closest of a pair."""
    key = (id(mover.resource), id(obstacle), group, other)
    earlier = reported.get(key)
    if earlier is not None and earlier.gap <= gap:
      return
    if earlier is not None:
      found.remove(earlier)
    reported[key] = Collision(mover.resource, obstacle, group, k, max(gap, 0.0), other, when, at)
    found.append(reported[key])

  def contact(
    piece: Piece, group: Group, k: int, other: Piece, relative: bool = False
  ) -> Tuple[Optional[float], Optional[Pose]]:
    """Where along the segment's way the piece first meets what stands against it, walked finely.

    The sweep that reported the meeting is the hull of the whole way; the way itself may clear what
    its hull only leans on. Nothing found says so, and the meeting stands as the sweep said it. The
    pose is the group's as the world sees it, the frame it rides included - or its own way alone,
    where `relative` says the other's points are relative to the same frame.
    """
    if k >= len(group.segments):
      return None, None
    segment = group.segments[k]
    if not math.isfinite(segment.end - segment.start):
      return None, None  # standing before or after its way, not a stretch of it
    span = segment.end - segment.start
    for f, _ in _samples(segment):
      t = segment.start + f * span
      pose = group.pose_at(t, relative=relative)
      if distance(pose.apply_all(piece.points), other.points) <= 0.0:
        return t, pose
    return None, None

  def swept_entries(
    group: Group, grow_by: float, in_place: bool
  ) -> List[Tuple[int, Piece, List[Vec], float, Vec, Vec]]:
    """Each piece's hull over each segment, with its bounds grown by `grow_by`."""
    out = []
    for k in range(len(group.segments)):
      for piece, hull, slack in group.swept_in_place(k) if in_place else group.swept(k):
        lo, hi = _bounds(hull)
        grow = slack + grow_by
        out.append((k, piece, hull, slack, _sub(lo, (grow,) * 3), _add(hi, (grow,) * 3)))
    return out

  # Each group's whole way is swept first, and the tree asked once, for what stands anywhere near
  # it; each stretch of the way is then compared with only those. What a group's frame carries is
  # part of its way as the world sees it.
  for group in groups:
    also = {id(r) for r in allow_for.get(group.name, ())} if allow_for else set()
    swept = swept_entries(group, reach, in_place=True)
    if not swept:
      continue
    lo = (min(w[4][0] for w in swept), min(w[4][1] for w in swept), min(w[4][2] for w in swept))
    hi = (max(w[5][0] for w in swept), max(w[5][1] for w in swept), max(w[5][2] for w in swept))
    skip = allowed | also
    shortlist = [o for o in obstacles.near(lo, hi) if id(o.resource) not in skip]
    for k, piece, hull, slack, lo, hi in swept:
      for other in shortlist:
        if not _meets(lo, hi, other.lo, other.hi):
          continue
        gap = distance(hull, other.points, stop_beyond=slack + reach + 1.0) - slack
        if gap <= 0.0 or gap < reach:
          when, at = contact(piece, group, k, other)
          report(piece, other.resource, group.name, k, gap, when=when, at=at)

  # Groups against each other, only over the times both segments cover - each group standing where
  # it starts before its first segment, and where it ends after its last. Two groups on one frame
  # are compared relative to it, which is exact; a pair on different frames is compared as the
  # world sees both. A pair the caller lets off is not swept at all; the rest are cut down by
  # their bounds before the exact test.
  if between_groups is False:
    return found
  ask = between_groups if callable(between_groups) else (lambda a, b: True)

  def timeline(g: Group) -> Group:
    """`g` standing where it starts before its way and where it ends after it, so a pair is
    compared over every time."""
    if not g.segments:
      return Group(g.name, g.pieces, [Segment([STILL], 0.0, -math.inf, math.inf)], g.frame)
    first, last = g.segments[0], g.segments[-1]
    return Group(
      g.name,
      g.pieces,
      [Segment(first.poses[:1], 0.0, -math.inf, first.start)]
      + g.segments
      + [Segment(last.poses[-1:], 0.0, last.end, math.inf)],
      g.frame,
    )

  def pair(a: Group, b: Group) -> Optional[Tuple[Group, list, Group, list, bool]]:
    """The two groups' timelines and swept hulls, or None the caller lets them off. The hulls are
    the world's own unless the two ride one frame, where they are relative to it - `together` says
    which."""
    if not ask(a.name, b.name):
      return None
    ta, tb = timeline(a), timeline(b)
    together = a.frame is not None and a.frame is b.frame
    return (
      ta,
      swept_entries(ta, 0.0, in_place=not together),
      tb,
      swept_entries(tb, 0.0, in_place=not together),
      together,
    )

  pairs = [
    p
    for i in range(len(groups))
    for j in range(i + 1, len(groups))
    for p in [pair(groups[i], groups[j])]
    if p is not None
  ]
  for a, swept_a, b, swept_b, together in pairs:
    for ka, pa, hull_a, slack_a, lo_a, hi_a in swept_a:
      sa = a.segments[ka]
      for kb, pb, hull_b, slack_b, lo_b, hi_b in swept_b:
        sb = b.segments[kb]
        if min(sa.end, sb.end) <= max(sa.start, sb.start):
          continue
        if not _meets(lo_a, hi_a, lo_b, hi_b):
          continue
        gap = distance(hull_a, hull_b) - slack_a - slack_b
        if gap <= 0.0 or gap < reach:
          # Where the other stands where it stood - still, or before or after its own way - the
          # meeting is walked finely as well; both on their way, it stands as the sweep found it.
          # A pair on one frame is walked relative to it, as their sweeps were.
          when = at = None
          if all(pose.turn == 0.0 and pose.shift == (0.0, 0.0, 0.0) for pose in sb.poses):
            when, at = contact(pa, a, ka, pb, relative=together)
          report(pa, pb.resource, a.name, ka, gap, b.name, when=when, at=at)
  return found
