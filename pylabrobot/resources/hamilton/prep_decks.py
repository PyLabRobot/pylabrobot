"""Hamilton Prep deck."""

from typing import List

from pylabrobot.resources.carrier import ResourceHolder
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton.core_grippers import prep_core_gripper_mount
from pylabrobot.resources.hamilton.tip_creators import hamilton_tip_300uL_filter
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trash import Trash


# How tall the deck's working volume is, in mm, from the deck surface. The channels travel from 18.03
# to 167.5 mm and the deck configuration reports the same Z range, as read off PRPAA1087 on
# 2026-09-13, so the top of that travel is the top of the deck.
PREP_DECK_SIZE_Z = 167.5


class PrepDeck(Deck):
  """Hamilton PREP deck: labware spots, trash, teaching tip site, and waste positions.

  Geometry aligns with the prep_tcp / MLPrep DeckConfiguration teaching site and waste
  sites used by :class:`~pylabrobot.hamilton.prep.driver.features.channels.PrepChannels`
  (``waste_rear``, ``waste_front``, ``waste_mph``). Validate coordinates on hardware
  (plastic mounts, calibration) before production use.

  This is **not** a :class:`HamiltonSTARDeck` (rails/teaching rack layout differ).
  """

  def __init__(
    self,
    name: str = "deck",
    size_x: float = 300.0,
    size_y: float = 394.0,
    size_z: float = PREP_DECK_SIZE_Z,
    origin: Coordinate = Coordinate.zero(),
    category: str = "deck",
    with_core_grippers: bool = False,
  ):
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, origin=origin, category=category
    )
    if with_core_grippers:
      self.assign_child_resource(prep_core_gripper_mount(), location=Coordinate(290, 266.5, 62.5))
    spots_list: List[ResourceHolder] = []
    for column in range(2):
      for row in range(4):
        x = column * 140
        y = row * 95.125
        spot = ResourceHolder(
          name=f"spot_{column}_{row}",
          size_x=127.76,
          size_y=92,
          size_z=12.5,
          child_location=Coordinate(
            0, 1.5, 3.75
          ),  # Adjusted for plastic corner mounts; validate on hardware
        )
        self.assign_child_resource(spot, location=Coordinate(x, y, 0))
        spots_list.append(spot)
    self.spots: List[ResourceHolder] = spots_list

    trash = Trash(name="trash", size_x=13, size_y=132.7, size_z=73)
    self.assign_child_resource(trash, location=Coordinate(280.3, -3, 0))

    teaching_tip_spot = TipSpot(
      name="teaching_tip",
      size_x=6.0,
      size_y=6.0,
      make_tip=hamilton_tip_300uL_filter,
      size_z=0.0,
      category="teaching_tip",
    )
    self.assign_child_resource(
      teaching_tip_spot,
      location=Coordinate(x=284.76, y=214.29, z=23.85),
    )

    for waste_name, y_pos in [("waste_rear", 30.0), ("waste_front", 10.0), ("waste_mph", 112.0)]:
      waste = Trash(
        name=waste_name,
        size_x=6.0,
        size_y=6.0,
        size_z=0.0,
        category="waste_position",
      )
      self.assign_child_resource(
        waste,
        location=Coordinate(x=286.8, y=y_pos, z=68.4),
      )

  def __getitem__(self, key: int) -> ResourceHolder:
    """Labware spot by index 0–7 (column-major: ``spot_0_0`` … ``spot_1_3``)."""
    return self.spots[key]

  def __setitem__(self, key: int, value: Resource):
    """Assign a resource to labware spot ``key`` (0–7)."""
    self.spots[key].assign_child_resource(value)
