"""Moving plates and lids with the iSWAP, planned in Python from its primitive moves.

The firmware's own plate commands (`C0 PP`, `C0 PR`, `C0 PM`) take a plate, a grip direction and a
few heights, and decide everything else: which of the three ways the arm can reach a grip it folds
into, the order its drives move in, and how. What they do is known only from their parameters. This
module does the same job from the moves the driver already has - the X-arm, the elbow's Y and Z,
both joints at once, the jaws - so every step is known, can be read before it runs, and can be
changed.

The plan follows the phases `C0 PP`'s parameters describe: the jaws open to the plate's width and a
margin (`open_gripper_position`), the arm rises to its traverse height
(`minimum_traverse_height_at_beginning_of_a_command`) and travels there, it comes down to the
gripping height (`z_position`), the jaws close on the plate with a force-sensed width window
(`plate_width`, `plate_width_tolerance`, `grip_strength`), and it rises to where the command ends
(`z_position_at_the_command_end`). Putting a plate down is the same travel and descent, the jaws
opening instead. The defaults are legacy's (`STARBackend.pick_up_resource`).

Which way round the arm reaches is worked out, not left to the firmware: with the two links known
(`link_1_length`, the gripper's `tool_center_point`), a grip centre and the direction the gripper
faces fix the wrist and the elbow for each of the three elbow stops, and the plan takes the one that
the drives can reach, the driver's pose check passes, and is nearest where the joints are now.

The resource tree follows the plate: once the jaws have closed on it, it hangs from the gripper
where it is; once they open, it is placed where PyLabRobot places anything on what it was put down
on - a site, a plate adapter, a stack, a plate for a lid - as the arm capability once did.

Before a plan runs, what it sweeps is checked against what stands around the arm and what rides the
X-arm with it (`iswap_collisions.check_plan`); a plan that would hit something is refused with an
`iSWAPCollisionError`, before anything moves.
"""

import asyncio
import dataclasses
import math
from typing import Any, Awaitable, Callable, List, Literal, Optional, Sequence, Tuple, Union, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.plate_adapter import PlateAdapter
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.trash import Trash

GripDirection = Literal["front", "back", "left", "right"]

# The side of the resource the arm grips from, as legacy's `GripDirection` means it and the firmware
# numbers it (`C0 PP gr`: 1 = -Y, 2 = +X, 3 = +Y, 4 = -X): the side the wrist is on. The gripper then
# faces the other way - a front grip faces +Y, deck degrees counter-clockwise from +X, as
# `GRIPPER_DECK_DIRECTIONS` states them.
GRIPPER_FACING: dict = {"front": 90.0, "right": 180.0, "back": -90.0, "left": 0.0}

# The elbow stops, as the deck angle link 1 lies along: the drive's own angle less a quarter turn.
ELBOW_STOPS: dict = {"left": -180.0, "front": -90.0, "right": 0.0}

# Legacy's defaults (`STARBackend.pick_up_resource`), in mm.
OPEN_MARGIN = 3.0  # the jaws open this much wider than the plate
WIDTH_TOLERANCE = 2.0
GRIP_STRENGTH = 4
PICKUP_DISTANCE_FROM_TOP = 5.0  # when the resource states no `preferred_pickup_location`
# How far below a lid's skirt a lidded plate is gripped, in mm. A lid comes down over the plate's
# sides by its `nesting_z_height`; jaws closing within that close on the lid, not the plate.
BELOW_LID = 2.0


def _norm(degrees: float) -> float:
  """An angle in (-180, 180]."""
  return (degrees + 180.0) % 360.0 - 180.0 if (degrees + 180.0) % 360.0 else 180.0


def placement(
  deck: Resource, resource: Resource, destination: Union[Resource, Coordinate], turned_by: float
) -> Tuple[float, Coordinate]:
  """Which way `resource` ends up turned against `destination`, and where its corner lands on the
  deck - as PyLabRobot places it there (`place`). Shared by the iSWAP and the CO-RE gripper."""
  after = resource.get_absolute_rotation().z + turned_by
  if isinstance(destination, Coordinate):
    return after, destination
  wrt_destination = after - destination.get_absolute_rotation().z
  turned = resource.rotated(z=wrt_destination - resource.rotation.z)
  base = destination.get_location_wrt(deck)
  dest_rotation = destination.get_absolute_rotation()
  if isinstance(destination, ResourceStack):
    local = destination.get_new_child_location(turned)
  elif isinstance(destination, ResourceHolder):
    local = destination.get_default_child_location(turned)
  elif isinstance(destination, PlateAdapter) and isinstance(resource, Plate):
    local = destination.compute_plate_location(cast(Plate, turned))
  elif isinstance(destination, Plate) and isinstance(resource, Lid):
    local = destination.get_lid_location(cast(Lid, turned))
  else:
    local = Coordinate.zero()
  return wrt_destination, base + local.rotated(dest_rotation)


def place(
  deck: Resource, resource: Resource, destination: Union[Resource, Coordinate], rotation: float
) -> None:
  """Put `resource` on what it was let go over, as PyLabRobot places it there."""
  resource.unassign()
  resource.rotation = Rotation(z=rotation % 360)
  if isinstance(destination, Coordinate):
    deck.assign_child_resource(
      resource, location=destination - (deck.location or Coordinate.zero())
    )
  elif isinstance(destination, (ResourceHolder, ResourceStack)):
    destination.assign_child_resource(resource)
  elif isinstance(destination, PlateAdapter) and isinstance(resource, Plate):
    destination.assign_child_resource(
      resource, location=destination.compute_plate_location(resource)
    )
  elif isinstance(destination, Plate) and isinstance(resource, Lid):
    destination.assign_child_resource(resource)
  elif isinstance(destination, Trash):
    pass
  else:
    destination.assign_child_resource(resource, location=Coordinate.zero())


def _rotate(point: Coordinate, degrees: float) -> Coordinate:
  a = math.radians(degrees)
  return Coordinate(
    point.x * math.cos(a) - point.y * math.sin(a),
    point.x * math.sin(a) + point.y * math.cos(a),
    point.z,
  )


# -- the plan ------------------------------------------------------------------------------------


@dataclasses.dataclass
class Rise:
  """The grip centre to a height, moving nothing else. Down is the same step: a lower height."""

  z: float
  """Where the grip centre goes, in mm on the deck."""
  speed: Optional[float] = None
  acceleration: Optional[float] = None


@dataclasses.dataclass
class Travel:
  """Across, at the height the arm is at: the X-arm, the elbow along Y, and both joints.

  The X-arm is a drive of its own and travels while the iSWAP moves; the elbow's Y and the joints
  share one module, so one comes after the other, in `order`.
  """

  elbow_x: float
  """Where the elbow goes along X, in mm on the deck."""
  elbow_y: float
  """Where the elbow goes along Y, in mm on the deck."""
  elbow_angle: float
  """The elbow drive's angle, in its own degrees: 0 at its front stop."""
  wrist_angle: float
  """The wrist drive's angle, in its own degrees."""
  order: Literal["translate_first", "turn_first", "turn_at"] = "translate_first"
  turn_y: Optional[float] = None
  """For `turn_at`: the Y the elbow turns at, on the way from where it is to `elbow_y`."""
  x_acceleration_level: int = 3
  y_speed: Optional[float] = None
  elbow_speed: Optional[float] = None
  wrist_speed: Optional[float] = None
  elbow_acceleration: Optional[float] = None
  wrist_acceleration: Optional[float] = None


@dataclasses.dataclass
class Open:
  """The jaws to a width."""

  width: float
  speed: Optional[float] = None
  acceleration: Optional[float] = None


@dataclasses.dataclass
class Grip:
  """The jaws closed on the resource, stopping on it, and the resource hung from the gripper."""

  resource: Resource
  width: float
  """How wide the resource is between the jaws, in mm."""
  strength: int = GRIP_STRENGTH
  tolerance: float = WIDTH_TOLERANCE
  from_top: float = PICKUP_DISTANCE_FROM_TOP
  """How far below its top the jaws hold it, in mm: kept, so it is put down held the same way."""
  offset: Coordinate = dataclasses.field(default_factory=Coordinate.zero)


@dataclasses.dataclass
class Release:
  """The jaws opened, and the resource placed on what it was put down on."""

  resource: Resource
  destination: Union[Resource, Coordinate]
  rotation_wrt_destination: float
  """Which way the resource ends up turned against its destination, in degrees."""
  width: float
  """How wide the jaws open, in mm."""


Step = Union[Rise, Travel, Open, Grip, Release]


@dataclasses.dataclass
class Reach:
  """One way the arm can put its grip centre somewhere, facing a direction."""

  elbow: str
  elbow_angle: float
  wrist_angle: float
  elbow_x: float
  elbow_y: float
  elbow_z: float


@dataclasses.dataclass
class Plan:
  """What a pick-up or a put-down does, step by step. Read it, change it, then `execute` it."""

  steps: List[Step]
  reach: Reach
  facing: float
  """Which way the gripper faces, deck degrees."""

  def describe(self) -> str:
    lines = [
      f"elbow {self.reach.elbow} ({self.reach.elbow_angle:.1f} deg), wrist "
      f"{self.reach.wrist_angle:.1f} deg, gripper facing {self.facing:.0f} deg"
    ]
    for step in self.steps:
      if isinstance(step, Rise):
        lines.append(f"  grip centre to z {step.z:.1f}")
      elif isinstance(step, Travel):
        lines.append(
          f"  travel: elbow to x {step.elbow_x:.1f}, y {step.elbow_y:.1f}; joints to "
          f"{step.elbow_angle:.1f} / {step.wrist_angle:.1f} deg ({step.order}"
          + (f" at y {step.turn_y:.1f})" if step.order == "turn_at" else ")")
        )
      elif isinstance(step, Open):
        lines.append(f"  jaws open to {step.width:.1f} mm")
      elif isinstance(step, Grip):
        lines.append(f"  jaws close on {step.resource.name} ({step.width:.1f} mm), hang it")
      elif isinstance(step, Release):
        where = (
          step.destination if isinstance(step.destination, Coordinate) else step.destination.name
        )
        lines.append(f"  jaws open to {step.width:.1f} mm, {step.resource.name} onto {where}")
    return "\n".join(lines)


# -- the transport --------------------------------------------------------------------------------


class iSWAPCollisionError(RuntimeError):
  """A plan would bring the arm, or what it holds, into something: `collisions` says what."""

  def __init__(self, plan: "Plan", collisions: Sequence[Any]):
    self.plan = plan
    self.collisions = list(collisions)
    lines = "\n  ".join(str(c) for c in self.collisions)
    super().__init__(f"the iSWAP would hit something:\n  {lines}")


@dataclasses.dataclass
class _Held:
  resource: Resource
  facing: float
  """Which way the gripper faced when it took hold, deck degrees."""
  offset: Coordinate
  pickup_distance_from_top: float
  width: float


class iSWAPTransport:
  """Plates and lids moved by an iSWAP, from its primitive moves, with the tree kept in step.

  Args:
    iswap: the STAR's iSWAP feature, set up.
    traverse_height: where the grip centre travels, in mm on the deck: one fixed height, high
      enough for anything, not worked out from what is on the deck. The iSWAP's own
      `default_minimum_traverse_height` when None (legacy sends 280 mm).
    clear_the_arm: whether every plan first clears the deck volume for the iSWAP (`make_space`: the
      channels to Z safety and moved aside in Y, the heads raised), as the firmware's own plate
      commands do on every move. The channels ride the same X-arm; anything that moved them since
      the last plan - a CO-RE move, a head's step - leaves them in the iSWAP's way otherwise.
    check_collisions: whether a plan is checked for what it would hit before it runs.
    clearance: how close to anything a plan may come, in mm, when checked. 0 refuses only what
      meets.
  """

  def __init__(
    self,
    iswap: Any,
    traverse_height: Optional[float] = None,
    clear_the_arm: bool = True,
    check_collisions: bool = True,
    clearance: float = 0.0,
  ):
    self.iswap = iswap
    self.clear_the_arm = clear_the_arm
    self.check_collisions = check_collisions
    self.clearance = clearance
    self.traverse_height = (
      iswap.default_minimum_traverse_height if traverse_height is None else traverse_height
    )
    self._held: Optional[_Held] = None
    # Told what a refused plan would hit, before the refusal is raised. Set by whoever draws it.
    self.collision_reporter: Optional[Callable[[Sequence[Any]], Awaitable[None]]] = None

  # -- what is where ----------------------------------------------------------------------------

  @property
  def deck(self) -> Resource:
    deck = self.iswap._driver.deck
    if deck is None:
      raise RuntimeError("the iSWAP's driver was given no deck, so nothing is placed")
    return cast(Resource, deck)

  @property
  def holding(self) -> Optional[Resource]:
    return None if self._held is None else self._held.resource

  def _lengths(self) -> Tuple[float, float, float]:
    c = self.iswap.configuration
    if c.link_1_length is None or c.wrist_drive_predefined_increments is None:
      raise RuntimeError("the iSWAP's link length and wrist stops are read at setup")
    straight = c.wrist_increments_to_deg(c.wrist_drive_predefined_increments.straight)
    return c.link_1_length, self.iswap.gripper.tool_center_point.x, straight

  def reaches(self, grip: Coordinate, facing: float) -> List[Reach]:
    """Every way the arm can put its grip centre at `grip`, facing `facing`, that it can reach.

    For each elbow stop, the wrist sits a tool's length back from the grip centre along the way the
    gripper faces, and the elbow a link's length back from the wrist along the way link 1 lies. A
    way is kept when both joints are inside their travel, the elbow's X, Y and Z inside the drives'
    reach - Y no further forward than the channels can be packed out of its way - and the driver's
    own pose check passes.
    """
    c = self.iswap.configuration
    link_1, tool, straight = self._lengths()
    found = []
    for name, link_angle in ELBOW_STOPS.items():
      elbow_angle = link_angle + 90.0
      wrist_angle = _norm(facing - link_angle + straight)
      lo, hi = c.wrist_range_increments
      if not c.wrist_increments_to_deg(lo) <= wrist_angle <= c.wrist_increments_to_deg(hi):
        continue
      lo, hi = c.elbow_range_increments
      if (
        not c.elbow_drive_increments_to_angle(lo)
        <= elbow_angle
        <= c.elbow_drive_increments_to_angle(hi)
      ):
        continue
      wrist = Coordinate(
        grip.x - tool * math.cos(math.radians(facing)),
        grip.y - tool * math.sin(math.radians(facing)),
        grip.z,
      )
      elbow = Coordinate(
        wrist.x - link_1 * math.cos(math.radians(link_angle)),
        wrist.y - link_1 * math.sin(math.radians(link_angle)),
        grip.z + c.elbow_z_offset_above_finger,
      )
      # The channels stand in front of the iSWAP on the same Y: the elbow comes no further forward
      # than they can be packed out of its way (`elbow_y_min`), whatever the drive's own travel.
      if c.elbow_y_min is not None and elbow.y < c.elbow_y_min:
        continue
      try:
        for axis, value in (("x", elbow.x), ("y", elbow.y), ("z", elbow.z)):
          self.iswap._check_reachable(axis, round(value, 1))
        self.iswap._check_pose_reachable(elbow_angle, wrist_angle, y=elbow.y)
      except ValueError:
        continue
      found.append(Reach(name, elbow_angle, wrist_angle, elbow.x, elbow.y, elbow.z))
    return found

  def _ranked(self, reaches: Sequence[Reach], elbow: Optional[str]) -> List[Reach]:
    """The ways to reach, the one to try first first: nearest the joints as they are, so the arm
    sweeps as little as it can, and the front stop first among equals, since it keeps link 1 over
    the arm's own Y rather than out over the deck. Only `elbow`'s, when it is named."""
    if not reaches:
      raise ValueError("the arm cannot reach that grip facing that way from any elbow stop")
    if elbow is not None:
      named = [r for r in reaches if r.elbow == elbow]
      if not named:
        raise ValueError(
          f"the arm cannot reach that grip with the elbow {elbow}; it can with "
          f"{', '.join(r.elbow for r in reaches)}"
        )
      return named
    now_elbow = self.iswap.elbow_drive_get_angle() or 0.0
    now_wrist = self.iswap.wrist_drive_get_angle() or 0.0

    def cost(r: Reach) -> Tuple[float, int]:
      turn = max(abs(r.elbow_angle - now_elbow), abs(r.wrist_angle - now_wrist))
      return (round(turn, 1), 0 if r.elbow == "front" else 1)

    return sorted(reaches, key=cost)

  # A turn is checked at this many points along its way, not only where it ends: both joints move
  # at once, in a straight line in joint space, and the arm can sweep behind the rail part way.
  SWEEP_SAMPLES = 36

  def _holds(self, elbow: float, wrist: float, y: float) -> bool:
    try:
      self.iswap._check_pose_reachable(elbow, wrist, y=y)
      return True
    except ValueError:
      return False

  def _sweep_clear(self, elbow: float, wrist: float, reach: Reach, y: float) -> bool:
    n = self.SWEEP_SAMPLES
    return all(
      self._holds(
        elbow + (reach.elbow_angle - elbow) * k / n, wrist + (reach.wrist_angle - wrist) * k / n, y
      )
      for k in range(n + 1)
    )

  def _travel(self, reach: Reach) -> Optional[Travel]:
    """How to get to `reach` at the height the arm is at, or None if no way clears.

    Along Y first and turned at the target, if the joints as they are can make that trip and the
    turn clears there; turned first, where the drive is, if that clears; else along to a Y where
    the arm clears the rail whichever way it points - no further back than both links' length in
    front of the drive's back stop - turned there, and on to the target.
    """
    elbow = self.iswap.elbow_drive_get_angle()
    wrist = self.iswap.wrist_drive_get_angle()
    drive = self.iswap.elbow_get_reference_point_location()
    travel = Travel(reach.elbow_x, reach.elbow_y, reach.elbow_angle, reach.wrist_angle)
    if elbow is None or wrist is None or drive is None:
      travel.order = "turn_first"
      return travel
    if self._holds(elbow, wrist, reach.elbow_y) and self._sweep_clear(
      elbow, wrist, reach, reach.elbow_y
    ):
      return travel
    if self._sweep_clear(elbow, wrist, reach, drive.y):
      travel.order = "turn_first"
      return travel
    c = self.iswap.configuration
    link_1, tool, _ = self._lengths()
    if c.elbow_y_max is not None:
      safe = max(c.elbow_y_min, min(drive.y, reach.elbow_y, c.elbow_y_max - link_1 - tool))
      if self._holds(elbow, wrist, safe) and self._sweep_clear(elbow, wrist, reach, safe):
        travel.order, travel.turn_y = "turn_at", round(safe, 1)
        return travel
    return None

  def _arm_points(self, reach: Optional[Reach] = None) -> List[Coordinate]:
    """The elbow, the wrist and the grip centre - where they are, or where `reach` puts them."""
    link_1, tool, straight = self._lengths()
    if reach is None:
      drive = self.iswap.elbow_get_reference_point_location()
      elbow_angle = self.iswap.elbow_drive_get_angle() or 0.0
      wrist_angle = self.iswap.wrist_drive_get_angle() or 0.0
      if drive is None:
        return []
      x, y = drive.x, drive.y
    else:
      x, y, elbow_angle, wrist_angle = (
        reach.elbow_x,
        reach.elbow_y,
        reach.elbow_angle,
        reach.wrist_angle,
      )
    link = math.radians(elbow_angle - 90.0)
    grip = link + math.radians(wrist_angle - straight)
    wx, wy = x + link_1 * math.cos(link), y + link_1 * math.sin(link)
    return [
      Coordinate(x, y, 0),
      Coordinate(wx, wy, 0),
      Coordinate(wx + tool * math.cos(grip), wy + tool * math.sin(grip), 0),
    ]

  def _reach_and_travel(
    self, grip: Coordinate, facing: float, elbow: Optional[str]
  ) -> Tuple[Reach, Travel]:
    reaches = self._ranked(self.reaches(grip, facing), elbow)
    for reach in reaches:
      travel = self._travel(reach)
      if travel is not None:
        return reach, travel
    raise ValueError(
      "every way the arm reaches that grip sweeps it behind the X-arm's rail on the way: tried "
      f"the elbow {', '.join(r.elbow for r in reaches)}. Move the arm forward first"
    )

  # -- planning ---------------------------------------------------------------------------------

  def _grip_point(self, resource: Resource, offset: Coordinate, from_top: float) -> Coordinate:
    centre = resource.center().rotated(resource.get_absolute_rotation())
    top = resource.get_location_wrt(self.deck, "l", "f", "b") + centre + offset
    return Coordinate(top.x, top.y, top.z + resource.get_absolute_size_z() - from_top)

  @staticmethod
  def _width_across(resource: Resource, facing: float) -> float:
    """How wide the resource is between jaws that close across the way the gripper faces."""
    along_x = abs(math.cos(math.radians(facing))) > 0.5
    return resource.get_absolute_size_y() if along_x else resource.get_absolute_size_x()

  @staticmethod
  def _from_top(resource: Resource, pickup_distance_from_top: Optional[float]) -> float:
    """How far below its top `resource` is gripped: as asked, its preferred pickup location, or 5 mm
    - and for a plate with a lid on, below the lid's skirt, where the jaws meet the plate."""
    lid = getattr(resource, "lid", None)
    if not isinstance(lid, Lid):
      lid = None
    skirt = lid.nesting_z_height if lid is not None else None
    if pickup_distance_from_top is not None:
      if lid is not None and skirt is not None and pickup_distance_from_top < skirt:
        raise ValueError(
          f"{resource.name} has {lid.name} on it, which comes {skirt} mm down its sides: jaws "
          f"{pickup_distance_from_top} mm below its top close on the lid. Grip it more than "
          f"{skirt} mm down, or take the lid off first"
        )
      return pickup_distance_from_top
    if resource.preferred_pickup_location is not None:
      from_top = resource.get_size_z() - resource.preferred_pickup_location.z
    else:
      from_top = PICKUP_DISTANCE_FROM_TOP
    if skirt is not None:
      from_top = max(from_top, skirt + BELOW_LID)
    return from_top

  def plan_pick_up(
    self,
    resource: Resource,
    direction: Union[GripDirection, float] = "front",
    pickup_distance_from_top: Optional[float] = None,
    offset: Coordinate = Coordinate.zero(),
    elbow: Optional[str] = None,
    traverse_height: Optional[float] = None,
    end_height: Optional[float] = None,
    width: Optional[float] = None,
    open_margin: float = OPEN_MARGIN,
    grip_strength: int = GRIP_STRENGTH,
    width_tolerance: float = WIDTH_TOLERANCE,
  ) -> Plan:
    """Plan picking `resource` up, as `C0 PP` would: open, rise, travel, descend, grip, rise.

    Args:
      resource: what to pick up. Turned only about Z, by a quarter turn or several.
      direction: the side it is gripped from - where the wrist is - or deck degrees the gripper
        faces.
      pickup_distance_from_top: how far below its top the jaws hold it, in mm. Its
        `preferred_pickup_location` when None, else 5 mm.
      offset: added to where the jaws hold it, in mm.
      elbow: which elbow stop to reach from, `left`, `front` or `right`. The nearest the joints
        are now when None.
      traverse_height: where the grip centre travels, in mm. The transport's when None.
      end_height: where the grip centre ends, in mm. The traverse height when None.
      width: how wide it is between the jaws, in mm. Its size across the grip when None.
      open_margin: how much wider than that the jaws open to take it, in mm.
      grip_strength: 0 to 9.
      width_tolerance: how far off `width` it may turn out to be, in mm.
    """
    if self._held is not None:
      raise RuntimeError(f"already holding {self._held.resource.name}")
    rotation = resource.get_absolute_rotation()
    if rotation.x or rotation.y or rotation.z % 90:
      raise ValueError(
        f"{resource.name} is turned {rotation}; only quarter turns about Z are gripped"
      )
    facing = GRIPPER_FACING[direction] if isinstance(direction, str) else float(direction)
    from_top = self._from_top(resource, pickup_distance_from_top)
    grip = self._grip_point(resource, offset, from_top)
    reach, travel = self._reach_and_travel(grip, facing, elbow)
    across = self._width_across(resource, facing) if width is None else width
    traverse = self.traverse_height if traverse_height is None else traverse_height
    end = traverse if end_height is None else end_height
    steps: List[Step] = [
      Open(across + open_margin),
      Rise(max(traverse, self._grip_z_now())),
      travel,
      Rise(grip.z),
      Grip(resource, across, grip_strength, width_tolerance, from_top, offset),
      Rise(end),
    ]
    return Plan(steps, reach, facing)

  def plan_drop(
    self,
    destination: Union[Resource, Coordinate],
    direction: Optional[Union[GripDirection, float]] = None,
    offset: Optional[Coordinate] = None,
    elbow: Optional[str] = None,
    traverse_height: Optional[float] = None,
    end_height: Optional[float] = None,
    open_margin: float = OPEN_MARGIN,
  ) -> Plan:
    """Plan putting down what is held, as `C0 PR` would: rise, travel, descend, open, rise.

    Args:
      destination: what to put it on - a site, a plate adapter, a stack, a plate for a lid, the
        trash - or a place on the deck, its left front bottom corner.
      direction: the side it is let go of from. The side it was gripped from when None: a different
        side turns it by the difference.
      offset: added to where the jaws let go of it. The pick-up's when None.
      elbow, traverse_height, end_height, open_margin: as `plan_pick_up`.
    """
    held = self._held
    if held is None:
      raise RuntimeError("nothing is held")
    resource = held.resource
    facing = (
      held.facing
      if direction is None
      else (GRIPPER_FACING[direction] if isinstance(direction, str) else float(direction))
    )
    turned_by = _norm(facing - held.facing)
    if isinstance(destination, Resource):
      destination.check_can_drop_resource_here(resource)
    rotation_wrt_destination, corner = self._placement(resource, destination, turned_by)
    centre = resource.center().rotated(Rotation(z=resource.get_absolute_rotation().z + turned_by))
    at = corner + centre + (held.offset if offset is None else offset)
    grip = Coordinate(
      at.x, at.y, at.z + resource.get_absolute_size_z() - held.pickup_distance_from_top
    )
    reach, travel = self._reach_and_travel(grip, facing, elbow)
    traverse = self.traverse_height if traverse_height is None else traverse_height
    end = traverse if end_height is None else end_height
    steps: List[Step] = [
      Rise(max(traverse, self._grip_z_now())),
      travel,
      Rise(grip.z),
      Release(resource, destination, rotation_wrt_destination, held.width + open_margin),
      Rise(end),
    ]
    return Plan(steps, reach, facing)

  def _grip_z_now(self) -> float:
    drive = self.iswap.elbow_get_reference_point_location()
    if drive is None:
      raise RuntimeError("the iSWAP is not modelled")
    return float(drive.z - self.iswap.configuration.elbow_z_offset_above_finger)

  def _placement(
    self, resource: Resource, destination: Union[Resource, Coordinate], turned_by: float
  ) -> Tuple[float, Coordinate]:
    """As `placement`, on this transport's deck."""
    return placement(self.deck, resource, destination, turned_by)

  # -- doing it ---------------------------------------------------------------------------------

  def collisions(self, plan: Plan) -> List[Any]:
    """What `plan` would hit, from where the arm is now (`iswap_collisions.check_plan`)."""
    from pylabrobot.hamilton.star.driver.features.iswap_collisions import check_plan

    return check_plan(self, plan, self.clearance)

  async def execute(self, plan: Plan) -> None:
    """Carry a plan out, step by step: the arm cleared first, then refused, before the iSWAP moves,
    if it would hit anything and collisions are checked - against the channels where clearing left
    them."""
    if self.clear_the_arm:
      await self.iswap.make_space()
    if self.check_collisions:
      found = self.collisions(plan)
      if found:
        if self.collision_reporter is not None:
          await self.collision_reporter(found)
        raise iSWAPCollisionError(plan, found)
    for step in plan.steps:
      await self._do(step, plan)

  async def _do(self, step: Step, plan: Plan) -> None:
    iswap = self.iswap
    c = iswap.configuration
    if isinstance(step, Rise):
      await iswap.elbow_move_to_z_position(
        round(step.z + c.elbow_z_offset_above_finger, 1),
        speed=step.speed,
        acceleration=step.acceleration,
      )
    elif isinstance(step, Travel):

      async def along_and_turn() -> None:
        async def along() -> None:
          await iswap.elbow_move_to_y_position(round(step.elbow_y, 1), speed=step.y_speed)

        async def turn() -> None:
          await iswap.rotate_to_angles(
            elbow_relative_angle=step.elbow_angle,
            gripper_relative_angle=step.wrist_angle,
            elbow_speed=step.elbow_speed,
            wrist_speed=step.wrist_speed,
            elbow_acceleration=step.elbow_acceleration,
            wrist_acceleration=step.wrist_acceleration,
          )

        if step.order == "turn_at" and step.turn_y is not None:
          await iswap.elbow_move_to_y_position(round(step.turn_y, 1), speed=step.y_speed)
          await turn()
          await along()
          return
        first, then = (along, turn) if step.order == "translate_first" else (turn, along)
        await first()
        await then()

      # The X-arm is a drive of its own: it travels while the iSWAP moves along and turns.
      await asyncio.gather(
        iswap.elbow_move_to_x_position(
          round(step.elbow_x, 1), acceleration_level=step.x_acceleration_level
        ),
        along_and_turn(),
      )
    elif isinstance(step, Open):
      await iswap.gripper_move_to_jaw_position(
        step.width, speed=step.speed, acceleration=step.acceleration
      )
    elif isinstance(step, Grip):
      await iswap.gripper_close_with_force_sensed_width_window(
        step.width, grip_strength=step.strength, width_tolerance=step.tolerance
      )
      self._hang(step.resource)
      self._held = _Held(step.resource, plan.facing, step.offset, step.from_top, step.width)
    elif isinstance(step, Release):
      await iswap.gripper_move_to_jaw_position(step.width)
      self._place(step.resource, step.destination, step.rotation_wrt_destination)
      self._held = None

  def _hang(self, resource: Resource) -> None:
    """Hang `resource` from the gripper where it is, so it rides with the arm."""
    gripper = self.iswap.gripper
    deck = self.deck
    turned = gripper.get_absolute_rotation().z
    local = _rotate(resource.get_location_wrt(deck) - gripper.get_location_wrt(deck), -turned)
    rotation = resource.get_absolute_rotation().z - turned
    resource.unassign()
    resource.rotation = Rotation(z=rotation % 360)
    gripper.assign_child_resource(resource, location=local)

  def _place(
    self, resource: Resource, destination: Union[Resource, Coordinate], rotation: float
  ) -> None:
    """As `place`, on this transport's deck."""
    place(self.deck, resource, destination, rotation)

  # -- the whole move ---------------------------------------------------------------------------

  async def pick_up_resource(self, resource: Resource, **kwargs: Any) -> Plan:
    """Plan a pick-up with `plan_pick_up`'s arguments, and carry it out."""
    plan = self.plan_pick_up(resource, **kwargs)
    await self.execute(plan)
    return plan

  async def drop_resource(self, destination: Union[Resource, Coordinate], **kwargs: Any) -> Plan:
    """Plan a put-down with `plan_drop`'s arguments, and carry it out."""
    plan = self.plan_drop(destination, **kwargs)
    await self.execute(plan)
    return plan

  async def move_resource(
    self,
    resource: Resource,
    to: Union[Resource, Coordinate],
    pickup_direction: Union[GripDirection, float] = "front",
    drop_direction: Optional[Union[GripDirection, float]] = None,
    **kwargs: Any,
  ) -> None:
    """Pick `resource` up and put it down on `to`, as `C0 PP` then `C0 PR`."""
    await self.pick_up_resource(resource, direction=pickup_direction, **kwargs)
    await self.drop_resource(to, direction=drop_direction)
