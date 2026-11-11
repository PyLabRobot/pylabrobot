"""Pipetting channels, and the rigid grids some devices carry them in."""

from collections import OrderedDict
from typing import Any, Dict, List, Literal, Mapping, Optional, cast, get_args

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.head_tool import HeadTool, move_tool
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.resource import Resource
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
  stop disc, the collar its drive positions by, and reserves "tip cone" for the tip's own geometry.
  What is invariant is that the shaft carries its channel through to the tip.

  A device whose channels move independently carries these one each. A device whose channels move
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
    cross_section_type: str = CrossSectionType.CIRCLE.value,
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
      cross_section_type: its shape across, as `serialize` writes it. Always a circle; taken so a
        serialized shaft deserializes.

    Raises:
      ValueError: If the tip pickup mode is not one this models, or the cross section is not a
        circle.
    """
    if tip_pickup_mode not in get_args(TipPickupMode):
      raise ValueError(
        f"unknown tip_pickup_mode {tip_pickup_mode!r}, expected one of {get_args(TipPickupMode)}"
      )
    if cross_section_type != CrossSectionType.CIRCLE.value:
      raise ValueError(f"a tip mounting shaft is round, not {cross_section_type!r}")
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

  def _comparable_children(self) -> List[Resource]:
    """Everything but the tool it is carrying, which is state."""
    return [child for child in self.children if not isinstance(child, HeadTool)]

  def mount_tip(self, tip: HeadTool) -> None:
    """Take a tip onto this shaft, once the device has confirmed the pickup.

    What is placed on the shaft's axis is the tool's pick-up location, `fitting_depth` above the
    shaft's end - not the tool's origin. A resource is placed by its origin, so the offset between
    the two is taken off, turned by however the tool is turned: a CO-RE grip tool parked facing the
    other way is picked up by the same point on it, and a tip, which is never turned, is unaffected.

    Args:
      tip: the tip that was collected. It is reparented here.

    Raises:
      RuntimeError: If this shaft is already carrying a tip.
    """
    if self.has_tip():
      raise RuntimeError(f"{self.name} is already carrying {self.children[0].name}")
    grip = (tip.pick_up_location or tip.get_anchor("c", "c", "t")).rotated(tip.rotation)
    location = Coordinate(
      x=self.get_size_x() / 2 - grip.x,
      y=self.get_size_y() / 2 - grip.y,
      z=tip.fitting_depth - grip.z,
    )
    move_tool(tip, lambda: self.assign_child_resource(tip, location=location))

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

  def tip_bottom(self) -> Optional[Coordinate]:
    """The carried tool's bottom relative to the shaft's end, or None if empty."""
    tip = self.tip
    if tip is None:
      return None
    return Coordinate(0.0, 0.0, cast(Coordinate, tip.location).z)

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

    Returns:
      The spacing, in mm.

    Raises:
      ValueError: If the pipette has a single row or column, which has nothing to be spaced from.
    """
    return self.item_dx if self.num_items_x > 1 else self.item_dy

  @property
  def tip_pickup_mode(self) -> TipPickupMode:
    """How its channels hold onto a tip.

    Read from the channels rather than kept alongside them, so there is nothing to disagree with.

    Returns:
      The mode its channels use.

    Raises:
      ValueError: If the pipette has no channels to read it from.
    """
    return self.get_item(0).tip_pickup_mode

  def serialize(self) -> dict:
    """What its size and its channels do not say: where it is measured from, and whether they can

    Returns:
      The serialized resource, with those two fields added.
    be worked one at a time."""
    return {
      **super().serialize(),
      "reference_point": self.reference_point.serialize(),
      "independent_channel_actuation": self.independent_channel_actuation,
    }
