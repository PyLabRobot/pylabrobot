"""A pod's gripper: the four gripper axes (GY, GG, GZ, GR), on a pod whose declared configuration
describes a gripper. Reached as
`driver.left_x_arm.gripper` / `driver.right_x_arm.gripper`.

The gripper's axes are the pod's: it moves through `Pod.move_axes` with the rest of the pod (a grip
is GG moved force-limited), and what it holds widens the pod's range on the X rail and counts in its
own collision check. This feature moves labware (`pick_up_resource`, `place_resource`,
`move_resource`: the STAR's and the Prep's CoRe-gripper calls, with a `direction` named as STAR's
iSWAP names one), checks
grips, and tells the rail what is held.

A grip, as Biomek's logs have it: travel over the labware with the gripper up and turned to the
grip direction; open; down to the grip height; close force-limited (it stops on the labware); lift
it straight out until it clears what it sat in by Biomek's gripper margin
(`AdditionalSafeHeightGripper` + `AvoidanceMargin`); carry it at the labware's speed; come down
over the destination; open; rise. Where Biomek checks the grip (`AG` / `AU`: the pod's
`SensorEnabled`), it checks before the pickup, after the close, after each move while holding, and
after the release.
"""

import logging
import math
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, Union

from pylabrobot.beckman_coulter.biomek_i7 import targeting, units
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import GripCheckError
from pylabrobot.beckman_coulter.biomek_i7.units import MM_PER_CM
from pylabrobot.beckman_coulter.biomek_i7.units import optional_cm as _cm
from pylabrobot.lib.pick_and_place.assignment import assign_placed_resource
from pylabrobot.lib.pick_and_place.locations import (
  get_pick_location,
  get_place_location,
  get_rotation_wrt,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.lid import Lid, Liddable
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.rotation import Rotation
from pylabrobot.beckman_coulter.biomek_i7.driver.planning import traverse

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.deck import BiomekI7Deck
  from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
    DeviceConfiguration,
    PodConfiguration,
  )
  from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod

logger = logging.getLogger(__name__)

# GG's control effort below this means the gripper holds labware, as the Master Controller judges
# a grip (`AG` / `AU`).
GRIPPED_LABWARE_LIMIT = -200


# A grip direction: where the fingers point, as STAR's iSWAP names it (its
# `GRIPPER_DECK_DIRECTIONS`): degrees on the deck, counterclockwise from +x seen from above. The
# hand is on the other side of the labware: "right" grips with the hand on its left. (The legacy
# `GripDirection` named the hand's side, half a turn from this.)
GripperDirection = Literal["right", "back", "left", "front"]
GRIPPER_DECK_DIRECTIONS: Dict[GripperDirection, float] = {
  "right": 0.0,
  "back": 90.0,
  "left": 180.0,
  "front": -90.0,
}
# GR for each direction: Biomek's angles turn the other way (`units.axis_to_biomek`), so its 90
# points the fingers to the front.
GRIP_ANGLES: Dict[GripperDirection, float] = {
  "right": 0.0,
  "front": 90.0,
  "left": 180.0,
  "back": -90.0,
}
GRIP_DIRECTIONS: Dict[float, GripperDirection] = {gr: name for name, gr in GRIP_ANGLES.items()}


def gripper_direction(direction: Union[GripperDirection, float]) -> GripperDirection:
  """A direction's name, given its name or its degrees on the deck (STAR's iSWAP takes either).

  Raises:
    ValueError: for an unknown name, or degrees that are not a multiple of 90: the fingers close
      across a side of the labware.
  """
  if isinstance(direction, str):
    if direction not in GRIPPER_DECK_DIRECTIONS:
      raise ValueError(f"{direction!r} is not one of {tuple(GRIPPER_DECK_DIRECTIONS)}")
    return direction
  for name, angle in GRIPPER_DECK_DIRECTIONS.items():
    if (direction - angle) % 360 == 0:
      return name
  raise ValueError(f"{direction} degrees: a grip direction is a multiple of 90 degrees")


# A close that ends within this of its target (cm) met nothing: the logged grips stop 0.3-0.9 cm
# wider than their target, on the labware.
EMPTY_GRIP_MARGIN = 0.05

# Biomek's grip widths, per side: its labware classes open the fingers 1.146 cm wider than the
# labware and close them towards 0.663 cm narrower (`unsqueeze`, `squeeze`).
Y_CLEARANCE = 5.73
SQUEEZE_MM = 3.315


# Where the fingers point (`direction`) for each of Biomek's grip sides on labware at rotation 0, A1
# at its back left: "A1 near" has the hand on A1's side, the left, so the fingers point right.
GRIP_SIDES: Dict[str, GripperDirection] = {
  "A1 near": "right",
  "A1 away from": "left",
  "Wide A1 near": "front",
  "Wide A1 away from": "back",
}
# A direction turned a quarter counterclockwise seen from above: right goes to the back.
_QUARTER_TURN: Dict[GripperDirection, GripperDirection] = {
  "right": "back",
  "back": "left",
  "left": "front",
  "front": "right",
}


def biomek_grip_direction(resource: Resource, grip_side: str) -> GripperDirection:
  """Where the fingers point on the deck (`direction`) for a Biomek method step's `GripSide` on
  `resource`: "A1 near" (the hand on A1's side), "A1 away from", and the "Wide" ones (the fingers
  across the labware's long side). A1 is at the back left of labware at rotation 0, so "A1 near"
  puts the hand on the left there and the fingers point right; a turned labware turns it.

  Raises:
    ValueError: for an unknown side, or a rotation that is not a multiple of 90 degrees.
  """
  if grip_side not in GRIP_SIDES:
    raise ValueError(f"unknown grip side {grip_side!r}; one of {sorted(GRIP_SIDES)}")
  turn = resource.get_absolute_rotation().z % 360
  if turn % 90:
    raise ValueError(f"{resource.name} is turned {turn} degrees; grips are along its sides")
  side = GRIP_SIDES[grip_side]
  for _ in range(int(turn // 90)):
    side = _QUARTER_TURN[side]
  return side


@dataclass(frozen=True)
class GripPlan:
  """A grip or a release, in Biomek's units: the pod's X and the gripper's GY, GZ (the grip
  height) and GR (cm, degrees), the widths GG opens to and closes towards (cm), the GZ it lifts to
  clear what the labware sat in, what the rail counts while it is held, its width between the
  fingers (cm) and its rotation on the deck (degrees, in the tree). Biomek-native, as every plan:
  what the driver sends, for comparing with Biomek's logs (`units.py`)."""

  x: float
  gy: float
  gz: float
  gr: float
  open_width: float
  close_width: float
  lift: float
  held: g.HeldLabware
  width: float
  rotation: float


def held_labware_of(
  resource: Resource,
  across: Literal["x", "y"] = "y",
  offset_x: float = 0.0,
  offset_y: float = 0.0,
  grip_height: float = 0.0,
) -> g.HeldLabware:
  """What the rail and the pod's own collision check count for `resource` held by the gripper:
  its footprint, the lid on it as a stacked layer (as Biomek counts a gripped stack), and its
  height, in cm, from their labware definitions (recorded in `source`).

  Args:
    resource: what is held. A lid seated on it (`resource.lid`) is the layer above it.
    across: the resource's axis between the fingers: `y` when the fingers close on its front and
      back, `x` on its sides. The other runs along the fingers.
    offset_x, offset_y: how far the resource's centre is from the pads' centre, along and across
      the fingers, cm (Biomek's `GrippedLabwareOffsets`).
    grip_height: how far above its bottom the fingertips are, mm (negative below it).
  """

  def footprint(r: Resource) -> List[float]:
    size = [r.get_size_x() / MM_PER_CM, r.get_size_y() / MM_PER_CM]
    return size if across == "y" else size[::-1]

  def definition(r: Resource) -> str:
    """The labware definition `r` was made from, and its size (mm)."""
    name = r.model or type(r).__name__
    return f"{name} {r.get_size_x():g} x {r.get_size_y():g} x {r.get_size_z():g} mm"

  sources = [definition(resource)]

  length, width = footprint(resource)
  layers = []
  top = resource.get_size_z()
  lid = getattr(resource, "lid", None)
  if lid is not None and lid.location is not None:
    corner = [lid.location.x / MM_PER_CM, lid.location.y / MM_PER_CM]
    dx, dy = corner if across == "y" else corner[::-1]
    layers.append(g.StackedLayer(dx, dy, *footprint(lid)))
    top = max(top, lid.location.z + lid.get_size_z())
    sources.append(f"lid {definition(lid)}")
  return g.HeldLabware(
    length,
    width,
    offset_x,
    offset_y,
    tuple(layers),
    height=top / MM_PER_CM,
    grip_offset=grip_height / MM_PER_CM,
    source="; ".join(sources),
  )


class Gripper:
  """The gripper on one pod."""

  # Where the fingers point when an operation names no direction (STAR P18); None: the pod's own
  # (`_direction`).
  default_direction: Optional[Union[GripperDirection, float]] = None

  def __init__(self, pod: "Pod"):
    self._pod = pod
    self._held: Optional[Resource] = None
    self._held_plan: Optional[GripPlan] = None
    self._held_checks = False
    self._held_speed = 1000
    self._taken_from: Tuple[Optional[Resource], Optional[Coordinate]] = (None, None)

  @property
  def default_minimum_traverse_height(self) -> traverse.TraverseDefault:
    """How the gripper (and what it holds) travels by default: "safe", Biomek's safe height over the way (the tallest
    thing under it, plus Biomek's margins); "max", as high as it goes; or a height, mm of
    its lowest point above the deck. A call's `minimum_traverse_height_start` / `_end` overrides
    it. It starts as Biomek's setting for the pod (`AlwaysMaxZ`); the deck check refuses any travel
    too low, whatever the height."""
    return self._pod.traverse_defaults["gripper"]

  @default_minimum_traverse_height.setter
  def default_minimum_traverse_height(self, value: traverse.TraverseDefault) -> None:
    self._pod.traverse_defaults["gripper"] = traverse.traverse_default(value)

  def __str__(self) -> str:
    return f"{self._pod}'s gripper"

  @property
  def arm(self) -> "Pod":
    """The pod this gripper is on (STAR's `feature.arm`)."""
    return self._pod

  @property
  def held_labware(self) -> Optional[g.HeldLabware]:
    """What the gripper holds, as the rail counts it; None for nothing. The pod cannot sense it:
    it is what was last set."""
    return self._pod._held_labware

  def set_held_labware(self, held: Optional[g.HeldLabware]) -> None:
    """Tell the rail what the gripper now holds (None: nothing), so the pod's range counts it."""
    self._pod._held_labware = held
    self._pod._positions_changed()

  async def request_control_effort(self, axis: str = "GG") -> int:
    """How hard a motor is working to hold its position, in the drive's units. Moves nothing."""
    reply = await self._pod.board.send(p.get_control_effort(self._pod.axis(axis).index))
    return p.parse_s32(reply.payload)

  async def check_grip(self, expect_labware: bool) -> None:
    """Check that the gripper does (`expect_labware`) or does not hold labware, from GG's control
    effort: the Master Controller's `AG` / `AU`. Moves nothing.

    Raises:
      GripCheckError: if it does not match.
    """
    effort = await self.request_control_effort("GG")
    logger.info(
      "%s %s: GG control effort %d", self._pod.component, "AG" if expect_labware else "AU", effort
    )
    holds = effort < GRIPPED_LABWARE_LIMIT
    if holds != expect_labware:
      what = "holds no labware" if expect_labware else "still holds labware"
      raise GripCheckError(f"{self._pod}: the gripper {what} (GG control effort {effort})")

  # -- moving labware -----------------------------------------------------------------------------

  @property
  def held_resource(self) -> Optional[Resource]:
    """The resource the gripper holds (`pick_up_resource`), or None."""
    return self._held

  @property
  def held_plan(self) -> Optional[GripPlan]:
    """How the held resource was gripped, or None."""
    return self._held_plan

  def _context(self) -> Tuple["BiomekI7Deck", "DeviceConfiguration", "PodConfiguration"]:
    driver, declared = self._pod.driver, self._pod.declared
    if driver is None or driver.deck is None or declared is None:
      raise RuntimeError(f"{self} needs a deck and a declared configuration (use `BiomekI7`)")
    return driver.deck, driver.declared, declared

  def _direction(self, direction: Optional[Union[GripperDirection, float]]) -> GripperDirection:
    """`direction`, else `default_direction`, else the pod's own: the left pod's fingers point
    right (its hand on the labware's left), the right pod's left, as Biomek's methods grip on
    this instrument ("A1 near" on labware at rotation 0).

    Raises:
      ValueError: see `gripper_direction`.
    """
    if direction is not None:
      return gripper_direction(direction)
    if self.default_direction is not None:
      return gripper_direction(self.default_direction)
    return "right" if self._pod.side == "left" else "left"

  def _grip_point(
    self,
    resource: Resource,
    origin: Coordinate,
    rotation: float,
    offset: Coordinate,
    pickup_distance_from_top: Optional[float],
    pickup_distance_from_bottom: Optional[float],
  ) -> Tuple[Coordinate, float]:
    """Where the pads' centre goes (deck mm, the fingertips' height as its z) for `resource` with
    its origin at `origin` turned `rotation` degrees, and the grip height (mm above its bottom).

    Raises:
      ValueError: for both distances given, or neither and no `preferred_pickup_location`.
    """
    size = Coordinate(resource.get_size_x(), resource.get_size_y(), resource.get_size_z())
    point = resource.preferred_pickup_location
    if pickup_distance_from_top is not None and pickup_distance_from_bottom is not None:
      raise ValueError("give pickup_distance_from_top or pickup_distance_from_bottom, not both")
    if pickup_distance_from_top is not None:
      height = size.z - pickup_distance_from_top
    elif pickup_distance_from_bottom is not None:
      height = pickup_distance_from_bottom
    elif point is not None:
      height = point.z
    else:
      raise ValueError(
        f"{resource.name} has no preferred_pickup_location; give pickup_distance_from_top or "
        "pickup_distance_from_bottom"
      )
    xy = Coordinate(size.x / 2, size.y / 2, 0) if point is None else Coordinate(point.x, point.y, 0)
    turned = xy.rotated(Rotation(z=rotation))
    return origin + Coordinate(turned.x, turned.y, height) + offset, height

  def _pose(self, pads: "targeting.BiomekPoint", gr: float) -> Tuple[float, float, float]:
    """X, GY and GZ (Biomek cm) that put the pads' centre at `pads` (its z the fingertips) at GR
    `gr`: about the rotation centre (`pod_geometry`), the pads `PadsCenterToRotationCenter` along
    the fingers and `FingerCenterOffset` across them."""
    _, _, declared = self._context()
    geo, model = declared.geometry, g.GripperModel.from_bif(declared.gripper_model)
    r, f = model.pads_center_to_rotation_center, model.finger_center_offset
    c, s = math.cos(math.radians(gr)), math.sin(math.radians(gr))
    cx, cy = pads.x - (r * c - f * s), pads.y - (r * s + f * c)
    return (
      cx - geo.gripper_x_offset + r,
      cy - geo.gripper_y_offset + f,
      pads.z - geo.gripper_z_offset,
    )

  def _to_biomek(
    self, point: Coordinate, where: Union[Resource, Coordinate]
  ) -> "targeting.BiomekPoint":
    """A deck point (mm) in Biomek's frame (cm), corrected by this pod's calibration of the deck
    position `where` stands on (or is)."""
    deck, configuration, _ = self._context()
    b = targeting.to_biomek(point)
    position = None if isinstance(where, Coordinate) else targeting.deck_position_of(deck, where)
    if position is None:
      return b
    dx, dy, dz = targeting.position_correction(deck, configuration, position, self._pod.pod_number)
    return targeting.BiomekPoint(b.x + dx, b.y + dy, b.z + dz)

  def _checks(self, check_grip: Optional[bool]) -> bool:
    _, _, declared = self._context()
    enabled = bool(declared.settings["SensorEnabled"])
    if check_grip is None:
      return enabled
    if check_grip and not enabled:
      logger.warning(
        "%s: grip checks asked for; Biomek's configuration has this pod's grip sensor "
        "(SensorEnabled) off",
        self,
      )
    return check_grip

  def plan_pick_up(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
    pickup_distance_from_bottom: Optional[float] = None,
    *,
    direction: Optional[Union[GripperDirection, float]] = None,
    y_clearance: float = Y_CLEARANCE,
    squeeze_mm: float = SQUEEZE_MM,
  ) -> GripPlan:
    """Where and how a pickup of `resource` goes (see `pick_up_resource`). Moves nothing.

    Raises:
      ValueError: for a resource not on the deck, a grip height neither given nor preferred, widths
        the fingers cannot open or close to, or a pose outside the axes' travel.
    """
    deck, _, _ = self._context()
    direction = self._direction(direction)
    rotation = get_rotation_wrt(resource, deck).z
    origin = get_pick_location(resource, deck)
    pads, height = self._grip_point(
      resource, origin, rotation, offset, pickup_distance_from_top, pickup_distance_from_bottom
    )
    return self._plan(
      resource, pads, height, direction, rotation, y_clearance, squeeze_mm, resource
    )

  def _plan(
    self,
    resource: Resource,
    pads: Coordinate,
    height: float,
    direction: GripperDirection,
    rotation: float,
    y_clearance: float,
    squeeze_mm: float,
    where: Union[Resource, Coordinate],
  ) -> GripPlan:
    _, _, declared = self._context()
    if y_clearance < 0 or squeeze_mm < 0:
      raise ValueError(f"y_clearance and squeeze_mm must be 0 or more: {y_clearance}, {squeeze_mm}")
    gr = GRIP_ANGLES[direction]
    # The fingers close across the resource's own y when they point along its x.
    across_y = (gr - rotation) % 180 == 0
    width = resource.get_size_y() if across_y else resource.get_size_x()
    open_cm = (width + 2 * y_clearance) / MM_PER_CM
    close_cm = (width - 2 * squeeze_mm) / MM_PER_CM
    low, high = declared.limits["GG"]
    if not low <= close_cm < open_cm <= high:
      raise ValueError(
        f"{self}: the fingers open {low}-{high} cm; {resource.name} needs {close_cm:.3f} to "
        f"{open_cm:.3f}"
      )
    x, gy, gz = self._pose(self._to_biomek(pads, where), gr)
    for axis, value in (("X", x), ("GY", gy), ("GZ", gz), ("GR", gr)):
      lo, hi = declared.limits[axis]
      if not lo <= value <= hi:
        raise ValueError(f"{self}: {axis} {value:.3f} is outside its travel {lo}-{hi}")
    # Lifted out straight until it clears what it sat in (a lid its plate) by Biomek's margin.
    nest = resource.nesting_z_height if isinstance(resource, Lid) else 0.0
    lift = gz + nest / MM_PER_CM + declared.geometry.additional_safe_height_gripper
    lift += declared.geometry.avoidance_margin
    held = held_labware_of(resource, "y" if across_y else "x", grip_height=height)
    return GripPlan(
      x,
      gy,
      gz,
      gr,
      open_cm,
      close_cm,
      min(lift, declared.limits["GZ"][1]),
      held,
      width / MM_PER_CM,
      rotation,
    )

  def _held_field(self, z_speed: Optional[float]) -> int:
    """The speed field the gripper moves at while it holds labware: `z_speed`, mm/s of GZ (None:
    full speed). The same fraction of their maximum drives the other axes of those moves, as
    Biomek's labware speed limit does.

    Raises:
      ValueError: for a speed not above 0, or above GZ's maximum.
    """
    if z_speed is None:
      return units.FULL_SPEED
    return units.rate_to_field(self._pod, "GZ", z_speed)

  async def _check(self, enabled: bool, expect_labware: bool) -> None:
    if enabled:
      await self.check_grip(expect_labware)

  def _travel_targets(self, plan: GripPlan) -> Dict[str, int]:
    _, _, d = self._context()
    return {
      "X": d.to_counts("X", plan.x),
      "GY": d.to_counts("GY", plan.gy),
      "GR": d.to_counts("GR", plan.gr),
    }

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
    pickup_distance_from_bottom: Optional[float] = None,
    *,
    direction: Optional[Union[GripperDirection, float]] = None,
    minimum_traverse_height_start: Optional[float] = None,
    y_clearance: float = Y_CLEARANCE,
    squeeze_mm: float = SQUEEZE_MM,
    grip_strength: Optional[float] = None,
    z_speed: Optional[float] = None,
    check_grip: Optional[bool] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Grip `resource` where the tree has it and lift it out: the STAR's and the Prep's
    `pick_up_resource`, with a `direction`.

    The gripper travels over it with its fingers pointing in `direction`, opens to its width plus
    `y_clearance` either side, comes down, and closes towards its width less `squeeze_mm` either
    side under `grip_strength`: the close stops on the resource. It then lifts it straight up until
    it clears what it sat in (a lid its plate) by Biomek's gripper margin, and holds it. The pads go
    to its `preferred_pickup_location` (its centre when it has none), the fingertips that far above
    its bottom (negative below it, as Biomek grips plates). The default widths are Biomek's.

    Args:
      resource: what to grip.
      offset: added to where the pads go, mm.
      pickup_distance_from_top, pickup_distance_from_bottom: where the fingertips go, mm below its
        top or above its bottom; at most one. Neither: `preferred_pickup_location`'s z.
      direction: where the fingers point, as STAR's iSWAP names it: "right", "back", "left",
        "front" (`GripperDirection`), or degrees on the deck, counterclockwise from +x (a multiple
        of 90). The hand is on the other side: "right" grips with the hand on the labware's left.
        None: `default_direction`, else the pod's own ("right" on the left pod, "left" on the
        right pod).
      minimum_traverse_height_start: the channels' lowest point while traveling to it, mm, as
        `Pod._move_over` takes it; the gripper travels at its ceiling.
      y_clearance: how far each finger opens past it, mm. Biomek's is `Y_CLEARANCE`.
      squeeze_mm: how far past its walls each finger may close, mm. Biomek's is `SQUEEZE_MM`.
      grip_strength: the close's force limit, in Biomek's units (its `SF`); None is the gripper's
        `SqueezeForce`.
      z_speed: how fast it lifts and lowers the labware once held, mm/s of GZ (STAR's CoRe
        grippers' `z_speed`); its travel holding it moves at the same fraction of each axis's
        maximum (Biomek's labware speed limit). None: full speed.
      check_grip: check the grip from GG's control effort (`AG` / `AU`); None as Biomek does on
        this pod (its grip sensor setting, `SensorEnabled`).
      minimum_traverse_height_end: the channels' lowest point after, mm.

    Raises:
      RuntimeError: if the gripper already holds something.
      ValueError: see `plan_pick_up`.
      GripCheckError: if the fingers closed on nothing (they reached their close target; on every
        pod) or a grip check fails; the gripper is left where it is.
    """
    if self._held is not None:
      raise RuntimeError(f"{self} already holds {self._held.name}; place it first")
    direction = self._direction(direction)
    plan = self.plan_pick_up(
      resource, offset, pickup_distance_from_top, pickup_distance_from_bottom,
      direction=direction, y_clearance=y_clearance, squeeze_mm=squeeze_mm,
    )  # fmt: skip
    _, _, d = self._context()
    pod = self._pod
    checks = self._checks(check_grip)
    held_speed = self._held_field(z_speed)
    force = d.settings["GripperSqueezeForce"] if grip_strength is None else grip_strength
    logger.info("%s pick up %s (fingers %s)", pod.component, resource.name, direction)
    await self._check(checks, expect_labware=False)
    # The approach is planned, nothing exempt; the get's own moves work in the resource's position.
    targets = self._travel_targets(plan)
    await pod._travel_gripper(targets, minimum_traverse_height=_cm(minimum_traverse_height_start))
    with pod.working_at(resource):
      await pod.move_axes({"GG": d.to_counts("GG", plan.open_width)})
      await pod.move_axes({"GZ": d.to_counts("GZ", plan.gz)})
      self._simulate(plan.width)
      # What it closes on counts as part of the pod from the close on (on the rail, and against
      # the pod's own parts and the deck): it moves with the fingers once they close.
      self.set_held_labware(plan.held)
      try:
        await pod.move_axes({"GG": d.to_counts("GG", plan.close_width)}, forces={"GG": force})
        await self._check_stopped_width(plan)
        await self._check(checks, expect_labware=True)
      except BaseException:
        self.set_held_labware(None)
        raise
      self._hold(resource, plan, checks, held_speed)
      await pod.move_axes({"GZ": d.to_counts("GZ", plan.lift)}, held_speed)
      await self._check(checks, expect_labware=True)
      # Then up to the height it travels at, still part of the get, as Biomek's get does.
      await pod._raise_gripper(held_speed)
      if minimum_traverse_height_end is not None:
        await pod._move_over(
          {}, held_speed, minimum_traverse_height=_cm(minimum_traverse_height_end)
        )

  async def _check_stopped_width(self, plan: GripPlan) -> None:
    """The close is force-limited and stops on what it grips; one that reached its target closed
    on nothing. Read GG back (the force left it unknown) and refuse that, on every pod."""
    pod = self._pod
    await pod._read_unknown_positions()
    _, _, d = self._context()
    counts = pod.cpositions["GG"][0]
    if counts is None:
      raise GripCheckError(f"{self}: GG's position after the grip is not known")
    stopped = d.to_value("GG", counts)
    logger.info(
      "%s GG stopped at %.3f cm (closing towards %.3f)", pod.component, stopped, plan.close_width
    )
    if stopped - plan.close_width < EMPTY_GRIP_MARGIN:
      raise GripCheckError(
        f"{self}: the fingers closed to {stopped:.3f} cm, their target: nothing between them"
      )

  def _simulate(self, width: Optional[float]) -> None:
    """In simulation, put labware `width` cm wide between the fingers (None: nothing)."""
    driver = self._pod.driver
    if driver is not None and driver.simulated_grip is not None:
      driver.simulated_grip(self._pod, width)

  def _hold(self, resource: Resource, plan: GripPlan, checks: bool, speed: int) -> None:
    self._taken_from = (resource.parent, resource.location)
    if resource.parent is not None:
      resource.unassign()
    self._held, self._held_plan = resource, plan
    self._held_checks, self._held_speed = checks, speed
    self.set_held_labware(plan.held)  # the device hangs it from the gripper's model

  def plan_place(
    self,
    to: Union[Resource, Coordinate],
    offset: Coordinate = Coordinate.zero(),
    *,
    direction: Optional[Union[GripperDirection, float]] = None,
    y_clearance: Optional[float] = None,
  ) -> Tuple[GripPlan, Coordinate, float]:
    """Where a place of the held resource into `to` goes, where its origin ends up (deck mm) and
    how far the move turns it (degrees, in the tree). Moves nothing.

    Raises:
      RuntimeError: if nothing is held.
      ValueError: if `to` refuses it, or the pose is outside the axes' travel.
    """
    held, picked = self._held, self._held_plan
    if held is None or picked is None:
      raise RuntimeError(f"{self} holds nothing; pick_up_resource first")
    deck, _, _ = self._context()
    direction = GRIP_DIRECTIONS[picked.gr] if direction is None else gripper_direction(direction)
    # A turn of the hand turns what it holds; Biomek's Y runs to the front, so the other way in
    # the tree.
    turn = (picked.gr - GRIP_ANGLES[direction]) % 360
    if isinstance(to, Coordinate):
      deck.check_can_drop_resource_here(held)
    else:
      to.check_can_drop_resource_here(held)
    rotation = (picked.rotation + turn) % 360
    # Reckoned from the pickup: the gripper may have turned it since (as it hangs now, at the
    # gripper's own angle), so the turn from where it hangs.
    now = get_rotation_wrt(held, deck).z
    location = get_place_location(held, to, deck, rotation_applied_by_move=rotation - now)
    pads, height = self._grip_point(
      held, location, rotation, offset, None, picked.held.grip_offset * MM_PER_CM
    )
    clearance = (
      (picked.open_width - picked.width) * MM_PER_CM / 2 if y_clearance is None else y_clearance
    )
    squeeze = (picked.width - picked.close_width) * MM_PER_CM / 2
    plan = self._plan(held, pads, height, direction, rotation, clearance, squeeze, to)
    return plan, location, turn

  async def place_resource(
    self,
    to: Union[Resource, Coordinate],
    offset: Coordinate = Coordinate.zero(),
    *,
    direction: Optional[Union[GripperDirection, float]] = None,
    minimum_traverse_height_start: Optional[float] = None,
    y_clearance: Optional[float] = None,
    z_speed: Optional[float] = None,
    check_grip: Optional[bool] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Put the held resource into `to` (a resource, or a deck `Coordinate` for its origin); the
    tree follows once it is let go: the STAR's and the Prep's `place_resource`.

    It travels over `to`, comes down to where it was gripped, opens (to its pickup width, or its
    width plus `y_clearance` either side), and the gripper rises. A `direction` other than the
    pickup's turns it.

    Args:
      z_speed: as `pick_up_resource`'s; None is the pickup's.
      check_grip: as `pick_up_resource`'s; None is the pickup's.
      the others: as `pick_up_resource`'s.

    Raises:
      RuntimeError: if nothing is held.
      ValueError: see `plan_place`.
      GripCheckError: if a grip check fails.
    """
    plan, location, turn = self.plan_place(to, offset, direction=direction, y_clearance=y_clearance)
    held = self._held
    if held is None:
      raise RuntimeError(f"{self}: holds nothing to place")
    deck, _, d = self._context()
    pod = self._pod
    checks = self._held_checks if check_grip is None else self._checks(check_grip)
    held_speed = self._held_speed if z_speed is None else self._held_field(z_speed)
    logger.info("%s place %s", pod.component, held.name)
    # The approach is planned, nothing exempt; the put's own moves work in the destination.
    targets = self._travel_targets(plan)
    await pod._travel_gripper(
      targets, held_speed, minimum_traverse_height=_cm(minimum_traverse_height_start)
    )
    with pod.working_at(*self._places(to)):
      await self._check(checks, expect_labware=True)
      await pod.move_axes({"GZ": d.to_counts("GZ", plan.gz)}, held_speed)
      await self._check(checks, expect_labware=True)
      await pod.move_axes({"GG": d.to_counts("GG", plan.open_width)})
      self._simulate(None)
      picked = self._held_plan
      if picked is None:
        raise RuntimeError(f"{self}: has no plan for what it holds")
      self._held = self._held_plan = None
      self.set_held_labware(None)
      # Off the gripper's model, turned as it was on the deck: the move adds its own turn.
      if held.parent is not None:
        held.unassign()
      held.rotation = Rotation(z=picked.rotation)
      assign_placed_resource(held, to, deck, location=location, rotation_applied_by_move=turn)
      await self._check(checks, expect_labware=False)
      await pod.move_axes({"GZ": d.to_counts("GZ", plan.lift)})
      await pod._raise_gripper()  # up to its travel height, still part of the put
      if minimum_traverse_height_end is not None:
        await pod._move_over({}, minimum_traverse_height=_cm(minimum_traverse_height_end))

  def _places(self, to: Union[Resource, Coordinate]) -> Tuple[Resource, ...]:
    """What a place into `to` works in, for the deck check: `to`, or the deck position under a
    deck `Coordinate`."""
    if not isinstance(to, Coordinate):
      return (to,)
    deck, _, _ = self._context()
    return tuple(
      p
      for p in deck.get_all_children()
      if isinstance(p, ResourceHolder)
      and _contains(p.get_location_wrt(deck), p.get_absolute_size_x(), p.get_absolute_size_y(), to)
    )[:1]

  async def drop_resource(self, *args: Any, **kwargs: Any) -> None:
    """Deprecated: use `place_resource`."""
    warnings.warn(
      "`drop_resource` is deprecated; use `place_resource`.", DeprecationWarning, stacklevel=2
    )
    await self.place_resource(*args, **kwargs)

  async def return_resource(
    self,
    offset: Coordinate = Coordinate.zero(),
    *,
    direction: Optional[Union[GripperDirection, float]] = None,
    minimum_traverse_height_start: Optional[float] = None,
    y_clearance: Optional[float] = None,
    z_speed: Optional[float] = None,
    check_grip: Optional[bool] = None,
    minimum_traverse_height_end: Optional[float] = None,
  ) -> None:
    """Put the held resource back where `pick_up_resource` took it from (a lid on its plate): STAR's
    `return_resource`.

    Args:
      offset: as `place_resource` takes it, and every other argument too.

    Raises:
      RuntimeError: if nothing is held, or it was taken from no parent.
    """
    parent, location = self._taken_from if self._held is not None else (None, None)
    if parent is None:
      raise RuntimeError(f"{self}: nothing to return it to")
    to: Union[Resource, Coordinate]
    if isinstance(parent, (ResourceHolder, Liddable)):
      to = parent
    else:
      deck, _, _ = self._context()
      to = parent.get_location_wrt(deck) + (location or Coordinate.zero())
    await self.place_resource(
      to, offset, direction=direction, minimum_traverse_height_start=minimum_traverse_height_start,
      y_clearance=y_clearance, z_speed=z_speed, check_grip=check_grip,
      minimum_traverse_height_end=minimum_traverse_height_end,
    )  # fmt: skip

  async def move_resource(
    self,
    resource: Resource,
    to: Union[Resource, Coordinate],
    *,
    pickup_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
    pickup_distance_from_bottom: Optional[float] = None,
    pickup_direction: Optional[Union[GripperDirection, float]] = None,
    place_offset: Coordinate = Coordinate.zero(),
    place_direction: Optional[Union[GripperDirection, float]] = None,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_during: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    y_clearance: float = Y_CLEARANCE,
    squeeze_mm: float = SQUEEZE_MM,
    grip_strength: Optional[float] = None,
    z_speed: Optional[float] = None,
    check_grip: Optional[bool] = None,
  ) -> None:
    """Pick `resource` up and place it into `to`: the Prep's `move_resource`, with pickup and place
    directions. The arguments are `pick_up_resource`'s and `place_resource`'s."""
    await self.pick_up_resource(
      resource, pickup_offset, pickup_distance_from_top, pickup_distance_from_bottom,
      direction=pickup_direction, minimum_traverse_height_start=minimum_traverse_height_start,
      y_clearance=y_clearance, squeeze_mm=squeeze_mm, grip_strength=grip_strength, z_speed=z_speed,
      check_grip=check_grip, minimum_traverse_height_end=minimum_traverse_height_during,
    )  # fmt: skip
    await self.place_resource(
      to, place_offset, direction=place_direction,
      minimum_traverse_height_start=minimum_traverse_height_during,
      minimum_traverse_height_end=minimum_traverse_height_end,
    )  # fmt: skip


def _contains(corner: Coordinate, size_x: float, size_y: float, point: Coordinate) -> bool:
  return corner.x <= point.x <= corner.x + size_x and corner.y <= point.y <= corner.y + size_y
