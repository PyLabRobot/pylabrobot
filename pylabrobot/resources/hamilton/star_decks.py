"""Hamilton STAR, STARlet and STARplus decks."""

import warnings
from typing import Literal, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import _built
from pylabrobot.resources.errors import ResourceNotFoundError
from pylabrobot.resources.hamilton.core_grippers import (
  HamiltonCoreGrippers,
  hamilton_core_gripper_1000ul_5ml_on_waste,
  hamilton_core_gripper_1000ul_at_waste,
)
from pylabrobot.resources.hamilton.hamilton_decks import (
  _TRACK_WIDTH,
  STAR_NUM_TRACKS,
  HamiltonDeck,
)
from pylabrobot.resources.hamilton.tip_creators import hamilton_teaching_needle_300uL
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.trash import Trash


class HamiltonSTARDeck(HamiltonDeck):
  """Base class for a Hamilton STAR(let) deck."""

  _rails_beyond_tracks = 2

  def __init__(
    self,
    num_tracks: Optional[int] = None,
    size_x: Optional[float] = None,
    size_y: Optional[float] = None,
    size_z: Optional[float] = None,
    name: str = "STAR_Deck",
    prefix: Optional[str] = None,
    category: str = "deck",
    origin: Coordinate = Coordinate.zero(),
    with_waste_block: bool = True,
    with_trash: bool = True,
    with_trash96: bool = True,
    with_teaching_needle_rack: bool = True,
    core_grippers: Optional[
      Literal["1000uL-at-waste", "1000uL-5mL-on-waste"]
    ] = "1000uL-5mL-on-waste",
    model: Optional[str] = None,
    num_rails: Optional[int] = None,
    with_teaching_rack: Optional[bool] = None,
  ) -> None:
    """Create a new STAR(let) deck of the given size.

    `with_trash` and `with_teaching_needle_rack` require `with_waste_block` to be true. `prefix` is what
    this deck names what it carries after: the device it belongs to, so two of them stand in one
    tree. It defaults to this deck's own name, without the `_Deck` it ends in. `num_rails` is
    deprecated: it counted two more than `num_tracks`.
    """

    if with_teaching_rack is not None:
      warnings.warn(
        "`with_teaching_rack` is deprecated, use `with_teaching_needle_rack`: what stands in the"
        " rack is a teaching needle, not a tip.",
        DeprecationWarning,
        stacklevel=2,
      )
      with_teaching_needle_rack = with_teaching_rack

    # Defaulted only so a deck saved with `num_rails` can leave out `num_tracks`, which comes first.
    if size_x is None or size_y is None or size_z is None:
      raise TypeError("size_x, size_y and size_z are required")

    super().__init__(
      num_tracks=num_tracks,
      num_rails=None if num_rails is None else num_rails - self._rails_beyond_tracks,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      name=name,
      category=category,
      origin=origin,
      model=model,
      prefix=prefix,
    )
    prefix = self.prefix

    if with_trash96:
      # got this location from a .lay file, but will probably need to be adjusted by the user.
      trash96 = Trash(f"{prefix}_trash_core96", size_x=122.4, size_y=82.6, size_z=0)  # tiprack
      self.assign_child_resource(
        resource=trash96,
        location=Coordinate(x=-42.0 - 16.2, y=120.3 - 14.3, z=216.4),
      )

    if with_waste_block:
      waste_block = Resource(
        name=f"{prefix}_waste_block",
        size_x=30,
        size_y=445.2,
        size_z=100,
        category="waste_block",
      )
      self.assign_child_resource(
        waste_block,
        location=Coordinate(x=self.track_to_location(self.num_tracks + 1).x, y=115.0, z=100),
      )

      # assign trash area, positioned 25mm to the right of the waste block
      # only run if the waste block is actually assigned.
      if with_trash:
        trash_x = waste_block.get_location_wrt(self).x + 25

        self.assign_child_resource(
          resource=Trash(f"{prefix}_trash", size_x=0, size_y=241.2, size_z=0),
          location=Coordinate(x=trash_x, y=190.6, z=137.1),
        )

      if with_teaching_needle_rack:
        tip_spots = [
          TipSpot(
            name=f"{prefix}_teaching_needle_rack_spot_{i}",
            size_x=9.0,
            size_y=9.0,
            size_z=0,
            make_tip=hamilton_teaching_needle_300uL,
          )
          for i in range(8)
        ]
        for i, ts in enumerate(tip_spots):
          # Collar support height; A1 == index 0, topmost tip.
          ts.location = Coordinate(x=0, y=7 * 9 - 9 * i, z=75.0)

        teaching_needle_rack = TipRack(
          name=f"{prefix}_teaching_needle_rack",
          size_x=9,
          size_y=9 * 8,
          size_z=50.4,
          ordered_items={f"{letter}1": tip_spots[idx] for idx, letter in enumerate("ABCDEFGH")},
          with_tips=True,
          model="hamilton_teaching_needle_rack",
        )
        waste_block.assign_child_resource(
          teaching_needle_rack, location=Coordinate(x=5.9, y=346.1, z=0)
        )
    else:
      if with_trash:
        raise RuntimeError("Trash area cannot be created when no waste block is present.")
      if with_teaching_needle_rack:
        raise RuntimeError("Teaching needle rack cannot be created when no waste block is present.")

    # `x` is where the channels take the tools, the holder's centre x; the holder is placed by its
    # left edge.
    if core_grippers == "1000uL-at-waste":  # "at waste"
      x: float = 1338 if self.num_tracks == STAR_NUM_TRACKS else 798
      holder = hamilton_core_gripper_1000ul_at_waste(name=f"{prefix}_core_gripper_holder")
      waste_block.assign_child_resource(
        holder,
        location=Coordinate(x=x - holder.get_size_x() / 2, y=105.550 - 26 - 9.5, z=205)
        - waste_block.location,
      )
    elif core_grippers == "1000uL-5mL-on-waste":  # "on waste"
      x = 1337.5 if self.num_tracks == STAR_NUM_TRACKS else 797.5
      holder = hamilton_core_gripper_1000ul_5ml_on_waste(name=f"{prefix}_core_gripper_holder")
      waste_block.assign_child_resource(
        holder,
        location=Coordinate(x=x - holder.get_size_x() / 2, y=125 - 18 - 21.5, z=200.5)  # probed
        - waste_block.location,
      )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "with_waste_block": False,  # data encoded as child. (not very pretty to have this key though...)
      "with_teaching_needle_rack": False,  # data encoded as child.
      "core_grippers": None,  # data encoded as child. (not very pretty to have this key though...)
    }

  # -- what the deck carries --------------------------------------------------------------------

  @property
  def waste_block(self) -> Optional[Resource]:
    """The waste block on the deck's right, or None if this deck was built without one."""
    return _built(self.children, "waste_block", Resource)

  @property
  def trash96(self) -> Optional[Trash]:
    """Where the 96-head discards tips, or None if this deck was built without it."""
    return _built(self.children, "trash_core96", Trash)

  @property
  def trash(self) -> Optional[Trash]:
    """Where the channels discard tips, or None if this deck was built without it."""
    return _built(self.children, "trash", Trash)

  @property
  def teaching_needle_rack(self) -> Optional[TipRack]:
    """The teaching needles on the waste block, or None if this deck was built without them."""
    block = self.waste_block
    if block is None:
      return None
    # `teaching_tip_rack` is what a deck saved before the needles were named for what they are
    # calls it.
    return _built(block.children, "teaching_needle_rack", TipRack) or _built(
      block.children, "teaching_tip_rack", TipRack
    )

  @property
  def teaching_tip_rack(self) -> Optional[TipRack]:
    """Deprecated: use `teaching_needle_rack`. What stands in it is a needle, not a tip."""
    warnings.warn(
      "HamiltonSTARDeck.teaching_tip_rack is deprecated. Use 'teaching_needle_rack' instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.teaching_needle_rack

  @property
  def core_gripper_holder(self) -> Optional[HamiltonCoreGrippers]:
    """The CO-RE gripper holder on the waste block, or None if this deck carries none."""
    block = self.waste_block
    if block is None:
      return None
    # `core_grippers` is what a deck saved before the holder was named for what it is calls it.
    return _built(block.children, "core_gripper_holder", HamiltonCoreGrippers) or _built(
      block.children, "core_grippers", HamiltonCoreGrippers
    )

  @property
  def core_grippers(self) -> Optional[HamiltonCoreGrippers]:
    """Deprecated: use `core_gripper_holder`. The tools are what the grippers are."""
    warnings.warn(
      "HamiltonSTARDeck.core_grippers is deprecated. Use 'core_gripper_holder' instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return self.core_gripper_holder

  def track_to_location(self, track: int) -> Coordinate:
    x = 100.0 + (track - 1) * _TRACK_WIDTH
    return Coordinate(x=x, y=63, z=100)

  def get_trash_area96(self) -> Trash:
    trash96 = self.trash96
    if trash96 is None:
      raise RuntimeError(
        "Trash area for 96-well plates was not created. Initialize with `with_trash96=True`."
      )
    return trash96

  def get_trash_area(self) -> Trash:
    trash = self.trash
    if trash is None:
      raise ResourceNotFoundError("Trash area not found")
    return trash

  def clear(self, include_trash: bool = False):
    """Clear the deck, removing all resources except the trash areas and the waste block."""
    children_names = [child.name for child in self.children]
    for resource_name in children_names:
      resource = self.get_resource(resource_name)
      if isinstance(resource, Trash) and not include_trash:
        continue
      if resource is self.waste_block:
        continue
      resource.unassign()


def STARLetDeck(
  name: str = "STARlet_Deck",
  prefix: Optional[str] = None,
  origin: Coordinate = Coordinate.zero(),
  with_trash: bool = True,
  with_trash96: bool = True,
  with_teaching_needle_rack: bool = True,
  core_grippers: Optional[
    Literal["1000uL-at-waste", "1000uL-5mL-on-waste"]
  ] = "1000uL-5mL-on-waste",
  with_teaching_rack: Optional[bool] = None,
) -> HamiltonSTARDeck:
  """Create a new STARLet deck."""

  return HamiltonSTARDeck(
    name=name,
    prefix=prefix,
    num_tracks=30,
    size_x=1005.0,
    size_y=653.5,
    size_z=334.7,
    origin=origin,
    with_trash=with_trash,
    with_trash96=with_trash96,
    with_teaching_needle_rack=with_teaching_needle_rack,
    with_teaching_rack=with_teaching_rack,
    core_grippers=core_grippers,
  )


def STARDeck(
  name: str = "STAR_Deck",
  prefix: Optional[str] = None,
  origin: Coordinate = Coordinate.zero(),
  with_trash: bool = True,
  with_trash96: bool = True,
  with_teaching_needle_rack: bool = True,
  core_grippers: Optional[
    Literal["1000uL-at-waste", "1000uL-5mL-on-waste"]
  ] = "1000uL-5mL-on-waste",
  with_teaching_rack: Optional[bool] = None,
) -> HamiltonSTARDeck:
  """Create a new STAR deck."""

  return HamiltonSTARDeck(
    name=name,
    prefix=prefix,
    num_tracks=54,
    size_x=1545.0,
    size_y=653.5,
    size_z=334.7,
    origin=origin,
    with_trash=with_trash,
    with_trash96=with_trash96,
    with_teaching_needle_rack=with_teaching_needle_rack,
    with_teaching_rack=with_teaching_rack,
    core_grippers=core_grippers,
  )


# The STARplus deck. Derived, because we have no STARplus to measure - but the two decks above fix
# it between them. They differ by 24 rails and 540.0 mm,
# which is exactly the 22.5 mm track pitch, so a deck's width and its rail count are the same fact.
# The manufacturer's own models measure the three machines at 1130.0, 1667.0 and 2163.5 mm wide, and
# a deck sits 125.0 and 122.0 mm inside the first two. Taking the same margin for the third gives
# 2040.0 mm, which is 78.00 rails - a whole number, which the neighbouring margins are not.


def STARPlusDeck(
  name: str = "STARplus_Deck",
  prefix: Optional[str] = None,
  origin: Coordinate = Coordinate.zero(),
  with_trash: bool = True,
  with_trash96: bool = True,
  with_teaching_needle_rack: bool = True,
  core_grippers: Optional[
    Literal["1000uL-at-waste", "1000uL-5mL-on-waste"]
  ] = "1000uL-5mL-on-waste",
  with_teaching_rack: Optional[bool] = None,
) -> HamiltonSTARDeck:
  """Create a new STARplus deck.

  Sizes derived from the STARlet and STAR decks and the manufacturer's machine widths.
  """

  return HamiltonSTARDeck(
    name=name,
    prefix=prefix,
    num_tracks=76,
    size_x=2040.0,
    size_y=653.5,
    size_z=334.7,
    origin=origin,
    with_trash=with_trash,
    with_trash96=with_trash96,
    with_teaching_needle_rack=with_teaching_needle_rack,
    with_teaching_rack=with_teaching_rack,
    core_grippers=core_grippers,
  )
