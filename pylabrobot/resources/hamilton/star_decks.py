"""Hamilton STAR, STARlet and STARplus decks."""

from typing import Literal, Optional, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.core_grippers import (
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
    name="deck",
    category: str = "deck",
    origin: Coordinate = Coordinate.zero(),
    with_waste_block: bool = True,
    with_trash: bool = True,
    with_trash96: bool = True,
    with_teaching_rack: bool = True,
    core_grippers: Optional[
      Literal["1000uL-at-waste", "1000uL-5mL-on-waste"]
    ] = "1000uL-5mL-on-waste",
    model: Optional[str] = None,
    num_rails: Optional[int] = None,
  ) -> None:
    """Create a new STAR(let) deck of the given size.

    `with_trash` and `with_teaching_rack` require `with_waste_block` to be true. `num_rails` is
    deprecated: it counted two more than `num_tracks`.
    """

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
    )

    if with_trash96:
      # got this location from a .lay file, but will probably need to be adjusted by the user.
      trash96 = Trash("trash_core96", size_x=122.4, size_y=82.6, size_z=0)  # size of tiprack
      self.assign_child_resource(
        resource=trash96,
        location=Coordinate(x=-42.0 - 16.2, y=120.3 - 14.3, z=216.4),
      )

    if with_waste_block:
      waste_block = Resource(name="waste_block", size_x=30, size_y=445.2, size_z=100)
      self.assign_child_resource(
        waste_block,
        location=Coordinate(x=self.track_to_location(self.num_tracks + 1).x, y=115.0, z=100),
      )

      # assign trash area, positioned 25mm to the right of the waste block
      # only run if the waste block is actually assigned.
      if with_trash:
        if with_waste_block:
          waste_block_x = self.get_resource("waste_block").get_location_wrt(self).x
        else:
          # Fallback: anchor to the rightmost rail when no waste block is present.
          waste_block_x = self.track_to_location(self.num_tracks + 1).x

        trash_x = waste_block_x + 25

        self.assign_child_resource(
          resource=Trash("trash", size_x=0, size_y=241.2, size_z=0),
          location=Coordinate(x=trash_x, y=190.6, z=137.1),
        )

      if with_teaching_rack:
        tip_spots = [
          TipSpot(
            name=f"teaching_tip_rack_tip_spot_{i}",
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

        teaching_tip_rack = TipRack(
          name="teaching_tip_rack",
          size_x=9,
          size_y=9 * 8,
          size_z=50.4,
          ordered_items={f"{letter}1": tip_spots[idx] for idx, letter in enumerate("ABCDEFGH")},
          with_tips=True,
          model="hamilton_teaching_tip_rack",
        )
        waste_block.assign_child_resource(
          teaching_tip_rack, location=Coordinate(x=5.9, y=346.1, z=0)
        )
    else:
      if with_trash:
        raise RuntimeError("Trash area cannot be created when no waste block is present.")
      if with_teaching_rack:
        raise RuntimeError("Teaching rack cannot be created when no waste block is present.")

    if core_grippers == "1000uL-at-waste":  # "at waste"
      x: float = 1338 if self.num_tracks == STAR_NUM_TRACKS else 798
      waste_block.assign_child_resource(
        hamilton_core_gripper_1000ul_at_waste(),
        location=Coordinate(x=x, y=105.550 - 26 - 9.5, z=205) - waste_block.location,
      )
    elif core_grippers == "1000uL-5mL-on-waste":  # "on waste"
      x = 1337.5 if self.num_tracks == STAR_NUM_TRACKS else 797.5
      waste_block.assign_child_resource(
        hamilton_core_gripper_1000ul_5ml_on_waste(),
        location=Coordinate(x=x, y=125 - 18 - 21.5, z=205) - waste_block.location,
      )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "with_waste_block": False,  # data encoded as child. (not very pretty to have this key though...)
      "with_teaching_rack": False,  # data encoded as child. (not very pretty to have this key though...)
      "core_grippers": None,  # data encoded as child. (not very pretty to have this key though...)
    }

  def track_to_location(self, track: int) -> Coordinate:
    x = 100.0 + (track - 1) * _TRACK_WIDTH
    return Coordinate(x=x, y=63, z=100)

  def get_trash_area96(self) -> Trash:
    if not self.has_resource("trash_core96"):
      raise RuntimeError(
        "Trash area for 96-well plates was not created. Initialize with `with_trash96=True`."
      )
    return cast(Trash, self.get_resource("trash_core96"))

  def clear(self, include_trash: bool = False):
    """Clear the deck, removing all resources except the trash areas and the waste block."""
    children_names = [child.name for child in self.children]
    for resource_name in children_names:
      resource = self.get_resource(resource_name)
      if isinstance(resource, Trash) and not include_trash:
        continue
      if resource.name == "waste_block":
        continue
      resource.unassign()


def STARLetDeck(
  origin: Coordinate = Coordinate.zero(),
  with_trash: bool = True,
  with_trash96: bool = True,
  with_teaching_rack: bool = True,
  core_grippers: Optional[
    Literal["1000uL-at-waste", "1000uL-5mL-on-waste"]
  ] = "1000uL-5mL-on-waste",
) -> HamiltonSTARDeck:
  """Create a new STARLet deck."""

  return HamiltonSTARDeck(
    num_tracks=30,
    size_x=1005.0,
    size_y=653.5,
    size_z=334.7,
    origin=origin,
    with_trash=with_trash,
    with_trash96=with_trash96,
    with_teaching_rack=with_teaching_rack,
    core_grippers=core_grippers,
  )


def STARDeck(
  origin: Coordinate = Coordinate.zero(),
  with_trash: bool = True,
  with_trash96: bool = True,
  with_teaching_rack: bool = True,
  core_grippers: Optional[
    Literal["1000uL-at-waste", "1000uL-5mL-on-waste"]
  ] = "1000uL-5mL-on-waste",
) -> HamiltonSTARDeck:
  """Create a new STAR deck."""

  return HamiltonSTARDeck(
    num_tracks=54,
    size_x=1545.0,
    size_y=653.5,
    size_z=334.7,
    origin=origin,
    with_trash=with_trash,
    with_trash96=with_trash96,
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
  origin: Coordinate = Coordinate.zero(),
  with_trash: bool = True,
  with_trash96: bool = True,
  with_teaching_rack: bool = True,
  core_grippers: Optional[
    Literal["1000uL-at-waste", "1000uL-5mL-on-waste"]
  ] = "1000uL-5mL-on-waste",
) -> HamiltonSTARDeck:
  """Create a new STARplus deck.

  Sizes derived from the STARlet and STAR decks and the manufacturer's machine widths.
  """

  return HamiltonSTARDeck(
    num_tracks=76,
    size_x=2040.0,
    size_y=653.5,
    size_z=334.7,
    origin=origin,
    with_trash=with_trash,
    with_trash96=with_trash96,
    with_teaching_rack=with_teaching_rack,
    core_grippers=core_grippers,
  )
