from __future__ import annotations

import importlib
import logging
import warnings
from abc import ABCMeta
from typing import Optional, cast

from pylabrobot.resources.carrier import Carrier, ResourceHolder
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.errors import NoLocationError
from pylabrobot.resources.hamilton.core_grippers import HamiltonCoreGrippers
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.trash import Trash

logger = logging.getLogger(__name__)

STARLET_NUM_TRACKS = 30
STAR_NUM_TRACKS = 54
STARPLUS_NUM_TRACKS = 76

# Which frame a deck belongs to, from how many tracks it has. The three are the same deck at three
# lengths, built by three factories rather than three classes, so the track count is the only thing
# that tells them apart - and parts that are cut to the frame's length differ between them and need
# saying which one they are. A count nobody has named leaves those parts unnamed rather than
# claiming to be a frame they are not.
FRAME_BY_NUM_TRACKS = {
  STARLET_NUM_TRACKS: "starlet",
  STAR_NUM_TRACKS: "star",
  STARPLUS_NUM_TRACKS: "starplus",
}


_TRACK_WIDTH = 22.5  # space between rails (mm)

# How far in front of the back of the device the X-arm's own back edge stands, in mm. Measured on
# the manufacturer's model: the chassis reaches to 51.06 and the arm's carriage to 35.56, and the
# chassis's depth there - 785.79 - is what the device resource says to the decimal, so the two
# frames line up and the difference is the arm's own setback.
ARM_BACK_FROM_DEVICE_BACK = 15.5

# How often a track gets a number on a part that states its own grid. The same rule the derived
# grids use: the first, and then every fifth.
DEFAULT_TRACK_LABEL_EVERY = 5

# Where a carrier's own front edge sits on any Hamilton deck, in mm.
_CARRIER_Y = 63.0

# What closes the front of the deck, in front of the first carrier row. A deck has one or the other:
# the panel is what is fitted where there is no autoload, and an autoload's belt frame stands in the
# same band and takes its place. A resource carries one mesh, so which of the two a deck shows is a
# fact about the deck rather than a part standing on it.
FRONT_PANEL_MODEL = "{frame}_deck_front_top_cover"
AUTOLOAD_BELT_MODEL = "{frame}_autoload_tray_belt_frame"

# Parts of the DEVICE that happen to hang off the deck, as opposed to things placed ON it. They are
# fitted where the instrument puts them, not assigned to rails, so they cannot occupy a rail and
# must not be treated as though they do - a fitted autoload otherwise makes rail 1 unassignable,
# because the sled's box reaches over the deck's front edge and up past a carrier's height.
_DEVICE_PARTS = frozenset({"autoload_sled", "autoload_loading_tray"})


def track_for_x_coordinate(x: float) -> int:
  """Which track an x coordinate falls on.

  Args:
    x: the coordinate, in this deck's own frame.

  Returns:
    The track, counted from 1.
  """
  return int((x - 100.0) / _TRACK_WIDTH) + 1


def rails_for_x_coordinate(x: float) -> int:
  """Deprecated. Use `track_for_x_coordinate`.

  Args:
    x: the coordinate, in this deck's own frame.

  Returns:
    What `track_for_x_coordinate` returns for it.
  """
  warnings.warn(
    "`rails_for_x_coordinate` is deprecated, use `track_for_x_coordinate`: a track is the part of"
    " the deck, and a rail is part of a carrier.",
    DeprecationWarning,
    stacklevel=2,
  )
  return track_for_x_coordinate(x)


def _resolve_num_tracks(num_tracks: Optional[int], num_rails: Optional[int]) -> int:
  """The track count, from whichever argument carried it.

  Args:
    num_tracks: the count.
    num_rails: the same count under its old name.

  Returns:
    The count.

  Raises:
    TypeError: If neither was given.
    ValueError: If both were given.
  """
  if num_tracks is not None:
    if num_rails is not None:
      raise ValueError("pass num_tracks, not both num_tracks and num_rails")
    return num_tracks
  if num_rails is None:
    raise TypeError("num_tracks is required")
  warnings.warn(
    "`num_rails` is deprecated, use `num_tracks`: a track is the part of the deck, and a rail is"
    " part of a carrier.",
    DeprecationWarning,
    stacklevel=3,
  )
  return num_rails


class HamiltonDeck(Deck, metaclass=ABCMeta):
  """Hamilton decks. Currently only STARLet, STAR and Vantage are supported."""

  def __init__(
    self,
    num_tracks: Optional[int] = None,
    size_x: Optional[float] = None,
    size_y: Optional[float] = None,
    size_z: Optional[float] = None,
    name: str = "deck",
    category: str = "deck",
    origin: Coordinate = Coordinate.zero(),
    num_rails: Optional[int] = None,
    model: Optional[str] = None,
  ):
    # What `@abstractmethod` refused before either could be left to the other: a deck with neither.
    if (
      type(self).track_to_location is HamiltonDeck.track_to_location
      and type(self).rails_to_location is HamiltonDeck.rails_to_location
    ):
      raise TypeError(f"{type(self).__name__} must implement track_to_location")

    # First, where `num_rails` was. Defaulted only so `num_rails=` can be given in its place.
    if size_x is None or size_y is None or size_z is None:
      raise TypeError("size_x, size_y and size_z are required")

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      origin=origin,
    )
    # `Deck` takes no model, so it is set here rather than passed up.
    self.model = model
    self.num_tracks = _resolve_num_tracks(num_tracks, num_rails)
    # A deck restored from a serialization arrives with the panel it was saved with; one built from
    # scratch works out which it is. Either way, fitting an autoload swaps it.
    if model is None:
      self._declare_front_panel()

    self.register_did_assign_resource_callback(self._check_safe_z_height)

  def _declare_front_panel(self) -> None:
    """Say which mesh closes the front of this deck: the panel, or the belt that replaces it.

    Called when the deck is built and again when an autoload is fitted to it, since fitting one is
    what swaps the two. A frame nobody has named leaves the deck without a model, as it leaves any
    other part cut to a frame's length unnamed.
    """
    frame = FRAME_BY_NUM_TRACKS.get(self.num_tracks)
    if frame is None:
      self.model = None
      return
    fitted = any(child.category in _DEVICE_PARTS for child in self.children)
    self.model = (AUTOLOAD_BELT_MODEL if fitted else FRONT_PANEL_MODEL).format(frame=frame)

  def track_to_location(self, track: int) -> Coordinate:
    """Where a track starts on this deck.

    A subclass implements this, or `rails_to_location` as it did before the rename.

    Args:
      track: the track, counted from 1.

    Returns:
      Its position, in this deck's own frame.
    """
    return self.rails_to_location(track)

  def rails_to_location(self, rails: int) -> Coordinate:
    """Deprecated. Use `track_to_location`.

    Args:
      rails: the track, counted from 1.

    Returns:
      What `track_to_location` returns for it.
    """
    warnings.warn(
      "`rails_to_location` is deprecated, use `track_to_location`: a track is the part of the deck,"
      " and a rail is part of a carrier.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.track_to_location(rails)

  # STAR decks counted two more rails than they have tracks, which is what `num_rails` said and
  # what `rails=` placement was bounded by. Kept for those deprecated names only.
  _rails_beyond_tracks = 0

  @property
  def num_rails(self) -> int:
    """Deprecated. Use `num_tracks`, which a STAR deck counts two fewer of."""
    warnings.warn(
      "`num_rails` is deprecated, use `num_tracks`: a track is the part of the deck, and a rail is"
      " part of a carrier.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.num_tracks + self._rails_beyond_tracks

  def compute_right_track_of_carrier(self, carrier: Carrier) -> int:
    """The last track a carrier covers, from where it sits on this deck.

    Args:
      carrier: the carrier, which must be on this deck.

    Returns:
      The track, counted from 1.
    """
    end_x = carrier.get_location_wrt(self).x + carrier.get_absolute_size_x()
    return track_for_x_coordinate(end_x) - 1

  def get_carrier_at_track(self, track: int) -> Carrier:
    """The carrier covering a track, from where the carriers sit on this deck.

    A carrier covers every track from the one it is placed at to
    `compute_right_track_of_carrier`, so a six-track carrier at track 15 answers for 15 to 20. This
    finds it from any of them, which is what lets a caller name a carrier by the track it was put
    at while the autoload addresses it by its rightmost.

    Args:
      track: any track the carrier covers, counted from 1.

    Returns:
      The carrier there.

    Raises:
      ValueError: If no carrier on this deck covers that track.
    """
    for child in self.children:
      if not isinstance(child, Carrier):
        continue
      left = track_for_x_coordinate(child.get_location_wrt(self).x)
      if left <= track <= self.compute_right_track_of_carrier(child):
        return child
    raise ValueError(f"no carrier on this deck covers track {track}")

  def get_or_create_x_arm(
    self,
    name: str,
    x: float,
    size_x: float,
    reference_point_from_left: float,
    model: str,
  ) -> Resource:
    """Get, or create once, the deck-owned X-arm resource called `name`.

    The deck owns it: created as a child the first time and reused thereafter, so repeated setups
    do not duplicate it. It is placed so its reference point sits at the arm's current x.

    The arm is wider than the width its drive reports, which begins at the arm's left edge and
    stops short of its right end. So the two are given separately: how much room the part takes,
    and where along it the drive's position refers to.

    Args:
      name: what to call it, e.g. "left_x_arm".
      x: where the arm is now, in mm, at its reference point.
      size_x: how wide the arm is, in mm, end to end.
      reference_point_from_left: how far along it, from its left edge in mm, the drive's position
        refers to - the middle of the reported width on a large arm, its right end on a small one.
      model: which arm this is.

    Returns:
      The arm resource, whether it was just created or already there.
    """
    if self.has_resource(name):
      return self.get_resource(name)
    # The arm rides at the channel stop-disk safety height, level with the raised stop discs so it
    # clears them as it travels.
    arm_z, size_z, size_y = 334.7, 140.0, 712.0
    x_arm = Resource(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="x_arm",
      model=model,
    )
    # What the drive's x actually refers to. Stated on the resource so anything reading it - the
    # placement below, and a viewer drawing where the arm is reported to be - works from the arm's
    # own frame rather than assuming the middle of the box.
    x_arm.reference_point = {"x": reference_point_from_left}  # type: ignore[attr-defined]
    # Place it so its reference point lands at the arm's current x, and its back edge where the arm
    # actually stands: a fixed distance in front of the back of the device carrying the deck. It
    # used to line up with the back of the DECK, which is not the same thing - the deck resource is
    # 653.5 mm deep where the deck it models is 773 - and that put the arm 19 mm too far forward.
    # Being deeper than the deck, the arm reaches in front of the deck's front edge, which is why y
    # comes out negative. It sits above the deck plane, so it does not occupy the footprint of the
    # carriers beneath it.
    #
    # A deck standing on its own has no device to measure from, and keeps its own back edge.
    device = self.parent
    if device is None:
      y = self.get_absolute_size_y() - size_y
    else:
      back_of_device = (device.get_absolute_size_y() - self.location.y) if self.location else 0.0
      y = back_of_device - ARM_BACK_FROM_DEVICE_BACK - size_y
    self.assign_child_resource(x_arm, location=Coordinate(x - reference_point_from_left, y, arm_z))
    return x_arm

  def get_or_create_autoload_sled(
    self, name: str, x: float, reference_point_from_left: float
  ) -> Resource:
    """Get, or create once, the deck-owned autoload sled.

    The deck owns it: created as a child the first time and reused thereafter, so repeated setups
    do not duplicate it.

    Args:
      name: where the carrier-handling wheel is, in mm, on this deck. The wheel is the point the
        drive reports, so the sled is placed around it.
      x: where the wheel is, in mm, on this deck.
      reference_point_from_left: how far the point the drive reports - the carrier-handling
        wheel - sits from the sled's left edge, in mm.

    Returns:
      The sled resource, whether it was just created or already there.
    """
    if self.has_resource(name):
      return self.get_resource(name)
    # The whole part, transport and barcode reader. The 316.2 this replaces came off the
    # manufacturer's model, whose left end carried a thin tab that the sled does not have; the
    # extra 35.3 mm put the part's own corner that far left of where it stands, and everything
    # measured from that corner with it.
    size_x, size_y, size_z = 280.9, 109.5, 215.3
    # Against a carrier's own front edge, and the deck's work surface.
    ahead_of_carrier_y, above_deck_z = 92.7, 0.5
    sled = Resource(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="autoload_sled",
      model="hamilton_star_autoload_sled",
    )
    # What the drive's x actually refers to. The sled is placed around the carrier-handling wheel,
    # so its own origin is not what the device reports - saying where the wheel sits within it is
    # what lets anything reading this resource put the two together, a viewer included.
    sled.reference_point = {  # type: ignore[attr-defined]
      "x": reference_point_from_left
    }
    self.assign_child_resource(
      sled,
      location=Coordinate(
        x - reference_point_from_left,
        _CARRIER_Y - ahead_of_carrier_y,
        above_deck_z,
      ),
    )
    self._declare_front_panel()
    return sled

  def get_or_create_autoload_loading_tray(self, name: str) -> Resource:
    """Get, or create once, the deck-owned loading tray the autoload draws carriers from.

    It is placed against the deck features it lines up with: its left edge sits 104 mm left of the
    first carrier, and its front edge 380 mm in front of a carrier's. It reaches the same 104 mm
    short of the deck's right edge, so its width follows from the deck. Created as a child the
    first time and reused thereafter, so repeated setups do not duplicate it.

    Its own track markings line up with the deck's, so a carrier put on the tray at a track goes to
    that same track on the deck.

    Args:
      name: what to call it.

    Returns:
      The tray resource, whether it was just created or already there.
    """
    if self.has_resource(name):
      return self.get_resource(name)
    # Measured against the two things on the deck it lines up with: where the first carrier starts,
    # and a carrier's front edge. It insets the same amount from the deck's right edge as from its
    # left, which is what sizes it.
    # The height is the tray plate's own top - what a carrier stands on, and what its track
    # markings are cut into. Read off the part: the plate tops out 98.0 mm above the tray's floor
    # on all three frames, and the track guides stand on it from there. It is 2 mm below the deck's
    # own work surface, which is what lets a carrier come off the tray and onto the deck.
    from_first_carrier_x, front_ahead_y, back_ahead_y, size_z = 104.0, 380.0, 132.0, 98.0
    left = self.track_to_location(1).x - from_first_carrier_x
    # The tray runs the length of the deck, so it is a different part on each frame rather than one
    # part fitted to all three, and it says which frame it is. The sled that runs along it is one
    # part everywhere and does not.
    frame = FRAME_BY_NUM_TRACKS.get(self.num_tracks)
    tray = Resource(
      name=name,
      size_x=self.get_absolute_size_x() - from_first_carrier_x - left,
      size_y=front_ahead_y - back_ahead_y,
      size_z=size_z,
      category="autoload_loading_tray",
      model=f"hamilton_{frame}_autoload_loading_tray" if frame else None,
    )
    # The tray's own track markings, stated rather than derived: they line up with the deck's, so
    # they are the deck's tracks read in the tray's frame. Nothing about the tray itself says where
    # they are, which is why it has to be told.
    #
    # The marks run the tray's full depth and sit on its top surface, since a carrier is placed on
    # the tray by the same marks it is placed on the deck by.
    first, second = self.track_to_location(1), self.track_to_location(2)
    tray.position_grid = {  # type: ignore[attr-defined]
      "axis": "x",
      "count": self.num_tracks,
      "spacing": round(second.x - first.x, 4),
      "origin": [round(first.x - left, 4), 0.0, size_z],
      "extent": round(front_ahead_y - back_ahead_y, 4),
      "label_every": DEFAULT_TRACK_LABEL_EVERY,
      "label": "track",
    }
    self.assign_child_resource(tray, location=Coordinate(left, _CARRIER_Y - front_ahead_y, 0.0))
    self._declare_front_panel()
    return tray

  def serialize(self) -> dict:
    """Serialize this deck."""
    return {
      **super().serialize(),
      # `Deck` drops the model on the way past, on the grounds that a deck does not usually have
      # one. This one does: the strip that closes the front of it is a part like any other, and
      # which part it is depends on whether an autoload is fitted.
      **({"model": self.model} if self.model is not None else {}),
      "num_tracks": self.num_tracks,
      "with_trash": False,  # data encoded as child. (not very pretty to have this key though...)
      "with_trash96": False,
      "core_grippers": None,  # data encoded as child. (not very pretty to have this key though...)
    }

  def _check_safe_z_height(self, resource: Resource):
    """Check for this resource, and all its children, that the z location is not too high."""

    # TODO: maybe these are parameters per HamiltonDeck that we can take as attributes.
    Z_MOVEMENT_LIMIT = 245
    Z_GRAB_LIMIT = 285

    def check_z_height(resource: Resource):
      # What the device carries, including a tool on a channel, is above the deck by design.
      if resource.category in ("x_arm", "head96") or isinstance(resource, HeadTool):
        return

      try:
        z_top = resource.get_location_wrt(self, z="top").z
      except NoLocationError:
        # if a resource has no location, we cannot check its z height
        # this is fine, because it's a convenience feature and not critical
        return

      if z_top > Z_MOVEMENT_LIMIT:
        logger.warning(
          "Resource '%s' is very high on the deck: %s mm. Be careful when traversing the deck.",
          resource.name,
          z_top,
        )

      if z_top > Z_GRAB_LIMIT:
        logger.warning(
          "Resource '%s' is very high on the deck: %s mm. Be careful when grabbing this resource.",
          resource.name,
          z_top,
        )

      for child in resource.comparable_children():
        check_z_height(child)

    check_z_height(resource)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = False,
    track: Optional[int] = None,
    rails: Optional[int] = None,
    replace=False,
    ignore_collision=False,
  ):
    """Assign a new deck resource.

    The identifier will be the Resource.name, which must be unique amongst previously assigned
    resources.

    Note that some resources, such as tips on a tip carrier or plates on a plate carrier must
    be assigned directly to the tip or plate carrier respectively. See TipCarrier and PlateCarrier
    for details.

    Given a track, the absolute (x, y, z) coordinates are computed from it.

    Args:
      resource: A Resource to assign to this liquid handler.
      location: Where to put it, relative to this deck. Either this or `track`, not both.
      reassign: If True, reassign the resource if it is already assigned. If False, raise a
        `ValueError` if the resource is already assigned.
      track: The leftmost track the resource covers, counted from 1 as the markings on the device
        are, and down to -4 for the supports left of the first one. Either this or `location`, not
        both.
      rails: Deprecated, use `track`.
      replace: Replace the resource with the same name that was previously assigned, if it exists.
        If a resource is assigned with the same name and replace is False, a ValueError
        will be raised.
      ignore_collision: If True, ignore collision detection.

    Raises:
      ValueError: If a resource is assigned with the same name and replace is `False`.
    """

    # TODO: many things here should be moved to Resource and Deck, instead of just STARLetDeck

    # `rails=` keeps the bounds it had, so a layout that placed before still places.
    beyond = 0
    if rails is not None:
      if track is not None:
        raise ValueError("pass track, not both track and rails")
      warnings.warn(
        "`rails` is deprecated, use `track`: a track is the part of the deck, and a rail is part"
        " of a carrier.",
        DeprecationWarning,
        stacklevel=2,
      )
      track = rails
      beyond = self._rails_beyond_tracks

    if track is not None and not -4 <= track <= self.num_tracks + beyond:
      raise ValueError(f"Track must be between -4 and {self.num_tracks + beyond}.")

    # Check if resource exists.
    if self.has_resource(resource.name):
      if replace:
        # unassign first, so we don't have problems with location checking later.
        cast(Resource, self.get_resource(resource.name)).unassign()
      else:
        raise ValueError(f"Resource with name '{resource.name}' already defined.")

    if track is not None:
      resource_location = self.track_to_location(track)
    elif location is not None:
      resource_location = location
    else:
      raise ValueError("Either track or location must be provided.")

    def should_check_collision(res: Resource) -> bool:
      """Determine if collision detection should be performed for this resource."""
      if isinstance(res, (HamiltonCoreGrippers, Trash)):
        return False
      # A part of the deck itself takes no part in the check, in either direction: it is already
      # skipped as something to collide with, and it is where it is whatever stands on the deck.
      # The autoload's sled reaches into the front of a carrier's footprint, which is how it pulls
      # one in, so a deck with carriers on it would otherwise refuse to place its own sled.
      if res.category in _DEVICE_PARTS:
        return False
      return True

    if not ignore_collision and should_check_collision(resource):
      if resource_location is not None:  # collision detection
        if (
          resource_location.x + resource.get_absolute_size_x()
          > self.track_to_location(self.num_tracks + 1 + beyond).x
          and track is not None
        ):
          raise ValueError(
            f"Resource with width {resource.get_absolute_size_x()} does not fit at track {track}."
          )

        # Check if there is space for this new resource.
        for og_resource in self.children:
          if og_resource.category in _DEVICE_PARTS:
            continue
          og_x = cast(Coordinate, og_resource.location).x
          og_y = cast(Coordinate, og_resource.location).y
          og_z = cast(Coordinate, og_resource.location).z

          # A resource is not allowed to overlap with another resource. Resources overlap when
          # their bounding boxes intersect on all three axes. The z axis is included so a resource
          # above the deck plane does not block placement beneath it.
          x_overlap = any(
            [
              og_x <= resource_location.x < og_x + og_resource.get_absolute_size_x(),
              og_x
              < resource_location.x + resource.get_absolute_size_x()
              < og_x + og_resource.get_absolute_size_x(),
            ]
          )
          y_overlap = any(
            [
              og_y <= resource_location.y < og_y + og_resource.get_absolute_size_y(),
              og_y
              < resource_location.y + resource.get_absolute_size_y()
              < og_y + og_resource.get_absolute_size_y(),
            ]
          )
          z_overlap = (
            og_z < resource_location.z + resource.get_absolute_size_z()
            and resource_location.z < og_z + og_resource.get_absolute_size_z()
          )
          if x_overlap and y_overlap and z_overlap:
            raise ValueError(
              f"Location {resource_location} is already occupied by resource '{og_resource.name}'."
            )

    return super().assign_child_resource(resource, location=resource_location, reassign=reassign)

  def summary(self) -> str:
    """Return a summary of the deck.

    Example:
      Printing a summary of the deck layout:

      >>> print(deck.summary())
      Rail     Resource                   Type                Coordinates (mm)
      =============================================================================================
      (1)  ├── tip_car                    TIP_CAR_480_A00     (x: 100.000, y: 240.800, z: 164.450)
           │   ├── tip_rack_01            STF                 (x: 117.900, y: 240.000, z: 100.000)
    """

    if len(self.get_all_resources()) == 0:
      raise ValueError(
        "This liquid editor does not have any resources yet. "
        "Build a layout first by calling `assign_child_resource()`. "
      )

    # don't print these
    exclude_categories = {
      "well",
      "tube",
      "tip_spot",
      "resource_holder",
      "plate_holder",
    }

    def find_longest_child_name(resource: Resource, depth=0, depth_weight=4):
      """DFS to find longest child name, and depth of that child, excluding excluded categories"""
      longest, longest_depth = (
        (len(resource.name), depth) if resource.category not in exclude_categories else (0, 0)
      )
      new_depth = depth + 1 if resource.category not in exclude_categories else depth
      return max(
        [(longest + longest_depth * depth_weight)]
        + [find_longest_child_name(c, new_depth) for c in resource.comparable_children()]
      )

    def find_longest_type_name(resource: Resource):
      """DFS to find the longest type name"""
      longest = (
        len(resource.__class__.__name__) if resource.category not in exclude_categories else 0
      )
      return max(
        [longest] + [find_longest_type_name(child) for child in resource.comparable_children()]
      )

    # Calculate the maximum lengths of the resource name and type for proper alignment
    max_name_length = find_longest_child_name(self)
    max_type_length = find_longest_type_name(self)

    # Find column lengths
    rail_column_length = 6
    name_column_length = max(
      max_name_length + 4, 30
    )  # 4 per depth (by find_longest_child), 4 extra
    type_column_length = max_type_length + 1
    location_column_length = 30

    # Print header
    summary_ = (
      "Rail".ljust(rail_column_length)
      + "Resource".ljust(name_column_length)
      + "Type".ljust(type_column_length)
      + "Coordinates (mm)".ljust(location_column_length)
      + "\n"
    )
    total_length = (
      rail_column_length + name_column_length + type_column_length + location_column_length
    )
    summary_ += "=" * total_length + "\n"

    def make_tree_part(depth: int) -> str:
      tree_part = "├── "
      for _ in range(depth):
        tree_part = "│   " + tree_part
      return tree_part

    def print_empty_spot_line(depth=0) -> str:
      r_summary = " " * rail_column_length
      tree_part = make_tree_part(depth)
      r_summary += (tree_part + "<empty>").ljust(name_column_length)
      return r_summary

    def print_resource_line(resource: Resource, depth=0) -> str:
      r_summary = ""

      # Print rail
      if depth == 0:
        rails = track_for_x_coordinate(resource.get_location_wrt(self).x)
        r_summary += f"({rails})".ljust(rail_column_length)
      else:
        r_summary += " " * rail_column_length

      # Print resource name
      tree_part = make_tree_part(depth)
      r_summary += (tree_part + resource.name).ljust(name_column_length)

      # Print resource type
      r_summary += resource.__class__.__name__.ljust(type_column_length)

      # Print resource location
      try:
        x, y, z = resource.get_location_wrt(self)
        location = f"({x:07.3f}, {y:07.3f}, {z:07.3f})"
      except NoLocationError:
        location = "Undefined"
      r_summary += location.ljust(location_column_length)

      return r_summary

    def print_tree(resource: Resource, depth=0):
      r_summary = print_resource_line(resource, depth=depth)

      # What a holder carries is state, so the deck's layout leaves it out.
      for child in resource.comparable_children():
        if isinstance(child, ResourceHolder):
          r_summary += "\n"
          if child.resource is not None:
            r_summary += print_tree(child.resource, depth=depth + 1)
          else:
            r_summary += print_empty_spot_line(depth=depth + 1)
        elif child.category not in exclude_categories:
          r_summary += "\n"
          r_summary += print_tree(child, depth=depth + 1)

      return r_summary

    # Sort resources by rails, left to right in reality.
    sorted_resources = sorted(self.children, key=lambda r: r.get_location_wrt(self).x)

    # Print table body.
    summary_ += print_tree(sorted_resources[0]) + "\n"
    for resource in sorted_resources[1:]:
      summary_ += "      │\n"
      summary_ += print_tree(resource)
      summary_ += "\n"

    # Truncate trailing whitespace from each line
    summary_ = "\n".join([line.rstrip() for line in summary_.split("\n")])

    return summary_


# Names this module had before the STAR decks moved to `star_decks` and rails became tracks. Kept
# importable, with the values they had, so code written against them keeps working.
_MOVED = {
  "HamiltonSTARDeck": "star_decks",
  "STARDeck": "star_decks",
  "STARLetDeck": "star_decks",
  "hamilton_core_gripper_1000ul_at_waste": "core_grippers",
  "hamilton_core_gripper_1000ul_5ml_on_waste": "core_grippers",
}
_OLD_CONSTANTS = {
  "_RAILS_WIDTH": 22.5,
  "STARLET_NUM_RAILS": 32,
  "STARLET_SIZE_X": 1005,
  "STARLET_SIZE_Y": 653.5,
  "STARLET_SIZE_Z": 900,
  "STAR_NUM_RAILS": 56,
  "STAR_SIZE_X": 1545,
  "STAR_SIZE_Y": 653.5,
  "STAR_SIZE_Z": 900,
}


def __getattr__(name: str):
  if name in _MOVED:
    return getattr(importlib.import_module(f"pylabrobot.resources.hamilton.{_MOVED[name]}"), name)
  if name in _OLD_CONSTANTS:
    return _OLD_CONSTANTS[name]
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
