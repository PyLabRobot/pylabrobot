"""What the STAR arm's movers sweep through, and what a command would hit.

The arm's checks share one scene of what stands still (`scene`), one shape for what is let off
what (`Exemptions`), one way to speak of what a command means to touch (`_allowed`,
`held_allowed`), and one account of what is mounted on the X-arm (`mounted_groups`). The iSWAP's
plan checks (`iswap_collisions`) and the pipette and head checks here are built on them.

`check_pipette_move` and `check_head_move` sweep what a pipette or head command moves - the
channels, each on its own way in Y and Z, the head as one rigid body - against what stands around
them and against each other, so that anything in the way of a command is found as geometry rather
than assumed away.
"""

from __future__ import annotations

import dataclasses
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from pylabrobot.hamilton.star.driver.features.head import Head
from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.star.resource_model import iSWAPHead
from pylabrobot.resources.collision import (
  Collision,
  Group,
  Piece,
  Pose,
  Segment,
  StaticScene,
  check,
  declared_hulls,
  on_axes,
  solid_pieces,
)
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource

# Resources that are enclosures, not solids: the X-arm travels into the left extension housing, and
# the model has the 96-head inside it whenever the arm is far enough left.
ENCLOSURES = ("left_extension_housing",)
# Two channels' moves within this much of each other, in mm, are one move: the row shifting together.
DELTA_TOLERANCE = 1e-6


@dataclasses.dataclass
class Exemptions:
  """Which pairs of a check's groups are let off each other.

  Nothing in a command moves the parts of one mechanism relative to each other, nor things mounted
  together that the command does not drive apart; they would be reported against each other
  wherever they stand close, which says nothing.
  """

  machine: Set[str] = dataclasses.field(default_factory=set)
  """Groups that are one mechanism: allowed to touch each other."""
  carriage: Set[str] = dataclasses.field(default_factory=set)
  """Groups mounted together on the X-arm that the command does not move relative to each other."""
  body: Optional[str] = None
  """The X-arm's own body, if it has a group: too coarsely boxed to judge the parts against, it is
  checked only against what stands still."""
  held: Optional[str] = None
  """The name of what the gripper holds, which rides the gripper and so is let off the machine."""

  def between(self, a: str, b: str) -> bool:
    """Whether two of the groups are checked against each other."""
    if self.body is not None and self.body in (a, b):
      return False
    if a in self.machine and b in self.machine:
      return False
    if a in self.carriage and b in self.carriage:
      return False
    if self.held is not None and {a, b} & self.machine and self.held in (a, b):
      return False
    return True


def _allowed(touches: Sequence[Resource]) -> List[Resource]:
  """Of what a command means to touch, what the moving parts may meet: all but what has a shape of
  its own (declared hulls). A box stands for something only roughly - a plate holder the fingers
  reach into - so meeting it means nothing; a shape is what is there, and the mover must keep clear
  of it even on its way to touch what it is meant to."""
  return [r for r in touches if declared_hulls(r) is None]


def held_allowed(
  held: Optional[Resource], touches: Sequence[Resource]
) -> Dict[str, List[Resource]]:
  """What the gripper holds may meet all the command means to touch, shapes too: it is a box, so
  its meeting the nest it goes into - a plate's wells in a thermocycler's block - means nothing."""
  if held is None or declared_hulls(held) is not None:
    return {}
  return {held.name: list(touches)}


_SCENES: Dict[int, StaticScene] = {}


def scene(root: Resource, hollow: Sequence[str] = ()) -> StaticScene:
  """The kept solid pieces of `root`, so that a check works out only what changed since the last."""
  key = id(root)
  kept = _SCENES.get(key)
  if kept is None or kept.root is not root or tuple(kept.hollow) != tuple(hollow):
    kept = _SCENES[key] = StaticScene(root, hollow)
  return kept


def root_of(resource: Resource) -> Resource:
  """The top of the tree `resource` stands in: what everything around it is under."""
  root = resource
  while root.parent is not None:
    root = root.parent
  return root


def still(end: float) -> List[Segment]:
  """Standing still for `end` units of time: one command, or a plan's steps."""
  return [Segment([Pose()], 0.0, 0.0, end)]


def mounted_groups(
  arm: Resource,
  end: float,
  segments: Optional[Mapping[str, List[Segment]]] = None,
  carrying: Optional[Resource] = None,
  held: Optional[Resource] = None,
  frame: Optional[List[Segment]] = None,
) -> List[Group]:
  """Every group on the X-arm: the iSWAP's parts, what it holds, each mounted rider, and the arm's
  own body. A group is given the segments named for it - a rider by its resource's name, a part of
  the iSWAP by `"column"`, `"link"`, `"gripper"`, `"finger 0"`, `"finger 1"`, what is held by
  `"held"` - and stands still where none are.

  Args:
    arm: the X-arm's resource, which everything mounted hangs from.
    end: how long the check's time runs, in units of one command or plan step.
    segments: how each group moves, as poses relative to where it stands now.
    carrying: what the gripper holds, left out of its pieces so they do not change shape with it.
    held: what the gripper holds or is picking up, a group of its own.
    frame: the carriage's own segments, which every group rides.
  """
  moving = segments or {}
  standing = still(end)

  def pieces(root: Resource, *leave_out: Resource) -> List[Piece]:
    return solid_pieces(root, {id(r) for r in leave_out})

  column = next((c for c in arm.children if isinstance(c, iSWAPHead)), None)
  link = next((c for c in column.children if isinstance(c, LinkBody)), None) if column else None
  gripper = (
    next((c for c in link.children if isinstance(c, MechanicalGripper)), None) if link else None
  )
  fingers = tuple(gripper.fingers) if gripper is not None else ()

  groups: List[Group] = []
  if column is not None:
    groups.append(
      Group(
        "iSWAP column",
        pieces(column, *([link] if link is not None else [])),
        moving.get("column", standing),
        frame,
      )
    )
  if link is not None:
    groups.append(
      Group(
        "iSWAP link 1",
        pieces(link, *([gripper] if gripper is not None else [])),
        moving.get("link", standing),
        frame,
      )
    )
  if gripper is not None:
    groups.append(
      Group(
        "iSWAP gripper",
        pieces(gripper, *fingers, *([carrying] if carrying is not None else [])),
        moving.get("gripper", standing),
        frame,
      )
    )
  if held is not None:
    groups.append(Group(held.name, pieces(held), moving.get("held", standing), frame))
  for k, finger in enumerate(fingers):
    groups.append(
      Group(f"iSWAP {finger.name}", pieces(finger), moving.get(f"finger {k}", standing), frame)
    )
  groups.append(Group("X-arm", pieces(arm, *arm.children), moving.get("X-arm", standing), frame))
  for rider in arm.children:
    if rider is column:
      continue
    groups.append(Group(rider.name, pieces(rider), moving.get(rider.name, standing), frame))
  return groups


def iswap_names(groups: Sequence[Group]) -> Set[str]:
  """The groups that are the iSWAP's own parts: one mechanism, allowed to touch itself."""
  return {g.name for g in groups if g.name.startswith("iSWAP ")}


def _check_ride(
  arm: Resource,
  segments: Mapping[str, List[Segment]],
  touch: Sequence[Resource],
  clearance: float,
  root: Resource,
  carriage: Sequence[str] = (),
) -> List[Collision]:
  """A pipette or head command's check: its movers swept against what stands around the arm and
  against each other.

  The iSWAP's parts are let off each other - nothing in the command moves them relative to each
  other - and the arm's own body is judged against nothing that rides it. The riders are checked
  against each other, less those the command carries along together (`carriage`).
  """
  groups = mounted_groups(arm, 1.0, segments)
  exemptions = Exemptions(machine=iswap_names(groups), carriage=set(carriage), body="X-arm")
  standing = scene(root, ENCLOSURES).obstacles([arm])
  return check(
    root, groups, clearance, _allowed(touch), standing, between_groups=exemptions.between
  )


def check_pipette_move(
  pipettes: Pipettes,
  y: Optional[Mapping[int, float]] = None,
  z: Optional[Mapping[int, float]] = None,
  touch: Sequence[Resource] = (),
  clearance: float = 0.0,
  root: Optional[Resource] = None,
) -> List[Collision]:
  """Where a pipette command's channels would come within `clearance` mm of what stands around
  them, or of each other.

  The command is one unit of time, and every channel it moves sweeps its whole way in it: the check
  is conservative about the order the drives settle in. Y and Z are where the drives report each
  channel's stop disc, in mm on the deck, as `move_to_y_positions` and
  `move_stop_disc_to_z_positions` take them. A tip mounted on a channel sweeps with it. Channels
  commanded by the same amount move as one rigid body and are let off each other.

  Args:
    pipettes: the channels' feature, with the arm where the command starts.
    y: where each channel's reference point is going, by channel.
    z: where each channel's stop disc is going, by channel.
    touch: what the command means to touch - a rack's tip spots, a labware's wells: never reported,
      unless it has a shape of its own.
    clearance: how close is too close, in mm. 0 reports only what meets.
    root: what stands around the arm; the whole tree the arm stands in when None.

  Raises:
    RuntimeError: If no channels are modelled, or a channel to be moved is not, so its way is not
      known.
  """
  if not pipettes.resources:
    raise RuntimeError("no channels are modelled, so what they sweep is not known")
  targets: Dict[int, List[Optional[float]]] = {}
  for channel, target in (y or {}).items():
    targets.setdefault(channel, [None, None])[0] = target
  for channel, target in (z or {}).items():
    targets.setdefault(channel, [None, None])[1] = target
  arm = pipettes.resources[0].parent
  if arm is None:
    raise RuntimeError("the channels are not on an arm, so what they sweep is not known")
  deltas: Dict[str, Tuple[float, float]] = {}
  for channel, (to_y, to_z) in targets.items():
    here = pipettes.get_reference_point_location(channel)
    if here is None:
      raise RuntimeError(f"channel {channel} is not modelled, so what it sweeps is not known")
    dy = 0.0 if to_y is None else to_y - here.y
    dz = 0.0 if to_z is None else to_z - here.z
    deltas[pipettes.resources[channel].name] = (dy, dz)
  # Channels commanded by the same amount move as one rigid body - the row shifting together - so
  # nothing in the command moves them relative to each other, and they are let off each other.
  shared: List[Tuple[Tuple[float, float], List[str]]] = []
  for name, delta in deltas.items():
    for common, names in shared:
      if all(abs(delta[k] - common[k]) <= DELTA_TOLERANCE for k in (0, 1)):
        names.append(name)
        break
    else:
      shared.append((delta, [name]))
  segments: Dict[str, List[Segment]] = {}
  carriage: Set[str] = set()
  for delta, names in shared:
    segment = on_axes((0.0, delta[0], delta[1]), 0.0, 1.0)
    for name in names:
      segments[name] = segment
    if len(names) > 1:
      carriage.update(names)
  return _check_ride(
    arm, segments, touch, clearance, root if root is not None else root_of(arm), sorted(carriage)
  )


def check_head_move(
  head: Head,
  y: Optional[float] = None,
  z: Optional[float] = None,
  touch: Sequence[Resource] = (),
  clearance: float = 0.0,
  root: Optional[Resource] = None,
) -> List[Collision]:
  """Where a head command would come within `clearance` mm of what stands around it, or of what
  rides the arm with it.

  The command is one unit of time, and the head - one rigid body, its tips with it - sweeps its
  whole way in it. Y is where channel A1 is going and Z where the head's lowest fixed feature is
  going, in mm on the deck, as the head's drives report them.

  Args:
    head: the head's feature, with the arm where the command starts.
    y: where channel A1 is going, in mm on the deck.
    z: where the head's lowest fixed feature is going, in mm on the deck.
    touch: what the command means to touch - a rack's tip spots, a labware's wells: never
      reported, unless it has a shape of its own.
    clearance: how close is too close, in mm. 0 reports only what meets.
    root: what stands around the arm; the whole tree the arm stands in when None.

  Raises:
    RuntimeError: If the head is not modelled, so its way is not known.
  """
  resource = head.resource
  if resource is None or resource.location is None or resource.parent is None:
    raise RuntimeError("the head is not modelled, so what it sweeps is not known")
  here = head.get_reference_point_location()
  if here is None:
    raise RuntimeError("the head is not modelled, so what it sweeps is not known")
  dy = 0.0 if y is None else y - here.y
  dz = 0.0 if z is None else z - here.z
  arm = resource.parent
  return _check_ride(
    arm,
    {resource.name: on_axes((0.0, dy, dz), 0.0, 1.0)},
    touch,
    clearance,
    root if root is not None else root_of(arm),
  )


def describe(collisions: Sequence[Collision]) -> str:
  return "\n".join(str(c) for c in collisions) or "nothing in the way"
