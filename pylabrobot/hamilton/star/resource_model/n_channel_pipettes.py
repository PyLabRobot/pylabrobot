"""Pipetting channels, and the rigid grids some machines carry them in."""

from collections import OrderedDict
from typing import Any, Dict, Literal, Mapping, Optional, get_args

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import CrossSectionType

TipPickupMode = Literal["friction", "core"]
"""How a channel holds onto a tip.

`friction` presses the channel's cone into the tip and relies on the interference fit, so a tip is
seated by pushing down onto it and shed by pushing it off against something. `core` seats the
channel inside the tip and expands a compressed o-ring into its collar, so the grip is made and
released mechanically rather than by force - which is why a pipette that engages this way has a
squeezer drive, and why it can put tips back on a rack rather than only discarding them."""

SHAFT_DIAMETER = 7.0
"""How wide a tip mounting shaft is, in mm."""

SHAFT_LENGTH = 8.0
"""How far a tip mounting shaft reaches below the pipette carrying it, in mm."""


class TipMountingShaft(Resource):
  """The end of one pipetting channel, where a tip is mounted and sealed.

  Named as the patent literature names it. Vendors do not agree: Hamilton's firmware names only the
  stop disk, the collar its drive positions by, and reserves "tip cone" for the tip's own geometry.
  What is invariant is that the shaft carries its channel through to the tip.

  A machine whose channels move independently carries these one each. A machine whose channels move
  as one carries them inside an `NChannelPipette`.

  Round, and modelled as a cylinder: it is a shaft, and a tip is sealed onto it by turning around
  its axis. A collected tip is a child of the shaft carrying it, which keeps the two together as
  the shaft moves.
  """

  def __init__(
    self,
    name: str,
    tip_pickup_mode: TipPickupMode,
    size_x: float = SHAFT_DIAMETER,
    size_y: float = SHAFT_DIAMETER,
    size_z: float = SHAFT_LENGTH,
    category: str = "tip_mounting_shaft",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: what to call this one.
      tip_pickup_mode: how it holds onto a tip.
      size_x: how wide it is across, in mm. Its diameter, since it is round.
      size_y: how deep it is, in mm. Its diameter again, for the same reason.
      size_z: how far it reaches below whatever carries it, in mm.
      category: what kind of resource this is.
      model: which channel this is.

    Raises:
      ValueError: If the tip pickup mode is not one this models.
    """
    if tip_pickup_mode not in get_args(TipPickupMode):
      raise ValueError(
        f"unknown tip_pickup_mode {tip_pickup_mode!r}, expected one of {get_args(TipPickupMode)}"
      )
    self.tip_pickup_mode = tip_pickup_mode
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )

  @property
  def tip(self) -> Optional[Resource]:
    """The tip this shaft is carrying, or None if it is empty."""
    return self.children[0] if self.children else None

  def has_tip(self) -> bool:
    """Whether this shaft is carrying a tip."""
    return len(self.children) > 0

  def mount_tip(self, tip: Resource) -> None:
    """Take a tip onto this shaft, hanging below it by however far it stands proud.

    Call this once the machine has confirmed the pickup: a shaft that is given a tip it did not
    manage to collect reports one it is not holding.

    Args:
      tip: the tip that was collected. It is reparented here, so it leaves wherever it was.

    Raises:
      RuntimeError: If this shaft is already carrying a tip.
    """
    if self.has_tip():
      raise RuntimeError(f"{self.name} is already carrying {self.children[0].name}")
    self.assign_child_resource(tip, location=Coordinate(0.0, 0.0, -tip.get_absolute_size_z()))

  def release_tip(self) -> Resource:
    """Let go of the tip this shaft is carrying.

    Reparenting it is the caller's: a tip put back on a rack belongs to its spot, and one dropped
    in the waste belongs nowhere.

    Returns:
      The tip that was released.

    Raises:
      RuntimeError: If this shaft is not carrying one.
    """
    tip = self.tip
    if tip is None:
      raise RuntimeError(f"{self.name} is not carrying a tip")
    self.unassign_child_resource(tip)
    return tip

  def tip_bottom(self) -> Coordinate:
    """Where the bottom of what this shaft carries is, relative to the channel.

    The shaft's own reference point when it is empty, and the end of the tip when it is not,
    which is what has to clear the deck.
    """
    tip = self.tip
    return Coordinate.zero() if tip is None else Coordinate(0.0, 0.0, -tip.get_absolute_size_z())

  def serialize(self) -> dict:
    """What its size does not say: how it holds a tip, and that it is round rather than a box."""
    return {
      **super().serialize(),
      "tip_pickup_mode": self.tip_pickup_mode,
      "cross_section_type": CrossSectionType.CIRCLE.value,
    }


class NChannelPipette(ItemizedResource[TipMountingShaft]):
  """A rigid grid of pipetting channels that move as one.

  Only for channels that share their drives: where each moves on its own, it is a
  `TipMountingShaft` in its own right and there is nothing for this to wrap.

  The channels are the items, so where any one of them is follows from where the pipette is and how
  the grid is spaced - `item_dx` and `item_dy` are that spacing, and `channel_pitch` is the word the
  drivers use for it. Located like any resource, by its left front bottom corner; where the drives
  report it - which need not be a channel at all - is `reference_point`.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    reference_point: Coordinate,
    ordered_items: Optional[Dict[str, TipMountingShaft]] = None,
    ordering: Optional[OrderedDict[str, str]] = None,
    independent_channel_actuation: bool = False,
    category: str = "n_channel_pipette",
    model: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
  ):
    """
    Args:
      name: what to call this one.
      size_x: how wide the pipette is, in mm.
      size_y: how deep it is, in mm.
      size_z: how tall it is, from its lowest fixed feature to its top, in mm.
      reference_point: the point the drives report and commands name, from the left front bottom
        corner. Usually where a tip is picked up - the axis of the first shaft, at the end of it -
        but a pipette is free to be measured from anywhere.
      ordered_items: its channels, keyed by identifier.
      ordering: the channels it already has, when one is being rebuilt rather than built.
      independent_channel_actuation: whether its channels can be worked one at a time rather than
        only all together. False unless each has its own actuation.
      category: what kind of resource this is.
      model: which pipette this is.
      metadata: anything else worth keeping with it.
    """
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      ordered_items=ordered_items,
      ordering=ordering,
      category=category,
      model=model,
      metadata=metadata,
    )
    self.reference_point = reference_point
    self.independent_channel_actuation = independent_channel_actuation

  @property
  def num_channels(self) -> int:
    """How many channels this pipette has."""
    return self.num_items

  @property
  def channel_pitch(self) -> float:
    """The centre-to-centre spacing of the channels, in mm.

    Raises:
      ValueError: If the pipette has a single row or column, which has nothing to be spaced from.
    """
    return self.item_dx if self.num_items_x > 1 else self.item_dy

  @property
  def tip_pickup_mode(self) -> TipPickupMode:
    """How its channels hold onto a tip.

    Read from the channels rather than kept alongside them, so there is nothing to disagree with.

    Raises:
      ValueError: If the pipette has no channels to read it from.
    """
    return self.get_item(0).tip_pickup_mode

  def serialize(self) -> dict:
    """What its size and its channels do not say: where it is measured from, and whether they can
    be worked one at a time."""
    return {
      **super().serialize(),
      "reference_point": self.reference_point.serialize(),
      "independent_channel_actuation": self.independent_channel_actuation,
    }


def _rigid_head(
  name: str,
  columns: int,
  rows: int,
  pitch: float,
  model: str,
  size_x: Optional[float],
  size_y: Optional[float],
  size_z: float,
  dx: Optional[float],
  dy: Optional[float],
  dz: float,
) -> NChannelPipette:
  """One of the rigid heads: a grid the instrument knows, in a body it does not.

  What the instrument tells us is the grid - how many channels, at what pitch. The body around it
  is a measurement of a particular head, never derived from the pitch. Left unmeasured, the resource
  spans the array and nothing more.

  Args:
    name: what to call this one.
    columns: how many channels across.
    rows: how many channels deep.
    pitch: their centre-to-centre spacing, in mm.
    model: which head this is.
    size_x: how wide the head's body is, in mm. None spans the channel array instead.
    size_y: how deep the body is, in mm. None spans the channel array instead.
    size_z: how tall it is, in mm. Zero models it as its channel plane.
    dx: how far channel A1 sits from the body's left edge, in mm. None centres the array.
    dy: how far channel A1 sits from its front edge, in mm. None centres the array.
    dz: how far channel A1 sits above its bottom, in mm.

  Returns:
    The pipette.
  """
  array_x, array_y = (columns - 1) * pitch, (rows - 1) * pitch
  size_x = array_x if size_x is None else size_x
  size_y = array_y if size_y is None else size_y
  # Centred in the body unless placed explicitly. A1 is the back row, so it sits a full array
  # depth behind the front margin.
  dx = (size_x - array_x) / 2 if dx is None else dx
  dy = (size_y - array_y) / 2 + array_y if dy is None else dy
  return NChannelPipette(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    # Where a tip is picked up: the axis of shaft A1, at the end of it. That end is a shaft's length
    # below the plane the body starts at, which is what the shafts hang from.
    reference_point=Coordinate(dx, dy, dz - SHAFT_LENGTH),
    ordered_items=create_ordered_items_2d(
      TipMountingShaft,
      name_prefix=name,
      num_items_x=columns,
      num_items_y=rows,
      # A channel position is an axis, and these place a corner, so each shaft is set back by its
      # own radius to leave its axis where the grid says the channel is.
      dx=dx - SHAFT_DIAMETER / 2,
      dy=dy - array_y - SHAFT_DIAMETER / 2,
      # The shafts are what reaches lowest, so they hang below the plane the body starts at rather
      # than standing on it: their own length below `dz`, which puts their ends at the bottom of
      # everything and the body a shaft's length clear of it.
      dz=dz - SHAFT_LENGTH,
      item_dx=pitch,
      item_dy=pitch,
      tip_pickup_mode="core",
    ),
    category=model,
    model=model,
  )


def head96(
  name: str,
  size_x: Optional[float] = 160.0,
  size_y: Optional[float] = 120.0,
  size_z: float = 0.0,
  dx: Optional[float] = None,
  dy: Optional[float] = None,
  dz: float = 0.0,
) -> NChannelPipette:
  """The 96-head: 96 channels on a 12 by 8 grid at 9 mm, moving as one.

  The drives report channel A1, so that is what `reference_point` is. The body is measured, and the
  channel array sits centred in it - which is where the margins on every side come from.

  Args:
    name: what to call this one.
    size_x: how wide the head's body is, in mm. None spans the channel array instead.
    size_y: how deep the body is, in mm. None spans the channel array instead.
    size_z: how tall it is, from its stop disk to its top, in mm. Zero leaves it unmodelled.
    dx: how far channel A1 sits from the body's left edge, in mm. None centres the array.
    dy: how far channel A1 sits from its front edge, in mm. None centres the array.
    dz: how far channel A1 sits above its bottom, in mm.

  Returns:
    The pipette.
  """
  return _rigid_head(name, 12, 8, 9.0, "head96", size_x, size_y, size_z, dx, dy, dz)


def head384(
  name: str,
  size_x: Optional[float] = 160.0,
  size_y: Optional[float] = 120.0,
  size_z: float = 0.0,
  dx: Optional[float] = None,
  dy: Optional[float] = None,
  dz: float = 0.0,
) -> NChannelPipette:
  """The 384-head: 384 channels on a 24 by 16 grid at 4.5 mm, moving as one.

  Measured from channel A1, and sharing the 96-head's body, which the two are built around. Its
  array is denser, so it leaves wider margins in the same envelope.

  Args:
    name: what to call this one.
    size_x: how wide the head's body is, in mm. None spans the channel array instead.
    size_y: how deep the body is, in mm. None spans the channel array instead.
    size_z: how tall it is, from its collar bearing to its top, in mm. Zero leaves it unmodelled.
    dx: how far channel A1 sits from the body's left edge, in mm. None centres the array.
    dy: how far channel A1 sits from its front edge, in mm. None centres the array.
    dz: how far channel A1 sits above its bottom, in mm.

  Returns:
    The pipette.
  """
  return _rigid_head(name, 24, 16, 4.5, "head384", size_x, size_y, size_z, dx, dy, dz)
