"""Hamilton Prep deck."""

from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.resources.carrier import ResourceHolder
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton.core_grippers import prep_core_gripper_mount
from pylabrobot.resources.hamilton.tip_creators import hamilton_teaching_needle_300uL
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.trough import Trough

# How tall the deck's working volume is, in mm, from the deck surface. The channels travel from 18.03
# to 167.5 mm and the deck configuration reports the same Z range, as read off PRPAA1087 on
# 2026-09-13, so the top of that travel is the top of the deck.
PREP_DECK_SIZE_Z = 167.5


class PrepDeck(Deck):
  """Hamilton PREP deck: labware spots, waste block, liquid waste container, teaching needle, and waste positions.

  Geometry aligns with the prep_tcp / MLPrep DeckConfiguration teaching site and waste
  sites used by :class:`~pylabrobot.hamilton.prep.driver.features.pipettes.Pipettes`
  (``waste_rear``, ``waste_front``, ``waste_mph``). Validate coordinates on hardware
  (plastic mounts, calibration) before production use.

  This is **not** a :class:`HamiltonSTARDeck` (rails/teaching rack layout differ).
  """

  def get_component_name(self, name: str) -> str:
    """Qualify a built-in component name with this deck's optional device prefix."""
    return name if self._name_prefix is None else f"{self._name_prefix}_{name}"

  def __init__(
    self,
    name: str = "deck",
    size_x: float = 300.0,
    size_y: float = 394.0,
    size_z: float = PREP_DECK_SIZE_Z,
    origin: Coordinate = Coordinate.zero(),
    category: str = "deck",
    with_core_grippers: bool = False,
    name_prefix: Optional[str] = None,
  ):
    """Build a deck, optionally prefixing its built-in components with a device name.

    Args:
      name_prefix: prefix for component names, without a trailing underscore. None keeps
        standalone component names. The deck itself keeps its explicitly supplied name.
    """
    self._name_prefix = name_prefix
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, origin=origin, category=category
    )
    spots_list: List[ResourceHolder] = []
    for column in range(2):
      for row in range(4):
        x = column * 140
        y = row * 95.125
        spot = ResourceHolder(
          name=self.get_component_name(f"spot_{column}_{row}"),
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

    # Where tips are dropped, as on the STAR's waste block, carrying the liquid waste trough, the
    # teaching needle and the CoRe gripper mount. From the deck's front edge at Y -3 to 2 mm in
    # front of the gripper mount's back edge (Y 286.5).
    waste_block = Trash(
      name=self.get_component_name("waste_block"), size_x=13, size_y=287.5, size_z=73
    )
    self.assign_child_resource(waste_block, location=Coordinate(280.3, -3, 0))

    # The liquid waste trough, part of the standard deck. As wide as the waste block, and filling
    # the gap from the tip drop area's back edge (132.7 mm from the block's front) to the teaching
    # needle's front edge, half the waste block's height with its top level with the block's top.
    # Its depth and height are not measured, and its volume is the box's.
    tip_drop_size_y = 132.7
    liquid_waste_size_y = 214.29 - (-3 + tip_drop_size_y)
    liquid_waste_size_z = waste_block.get_absolute_size_z() / 2
    liquid_waste_container = Trough(
      name=self.get_component_name("liquid_waste_container"),
      size_x=waste_block.get_absolute_size_x(),
      size_y=liquid_waste_size_y,
      size_z=liquid_waste_size_z,
      max_volume=waste_block.get_absolute_size_x() * liquid_waste_size_y * liquid_waste_size_z,
    )
    waste_block.assign_child_resource(
      liquid_waste_container,
      location=Coordinate(
        0, tip_drop_size_y, waste_block.get_absolute_size_z() - liquid_waste_size_z
      ),
    )

    # The teaching needle, the one STAR decks carry. On the deck, X and Y (284.76, 214.29) are
    # PRPAA1087's 6 x 6 mm deck site (DeckConfiguration); the driver moves it to the connected
    # device's at setup. Z is not measured.
    teaching_tip_spot = TipSpot(
      name=self.get_component_name("teaching_tip"),
      size_x=6.0,
      size_y=6.0,
      make_tip=hamilton_teaching_needle_300uL,
      size_z=0.0,
      category="teaching_tip",
    )
    waste_block.assign_child_resource(
      teaching_tip_spot,
      location=Coordinate(x=4.46, y=217.29, z=23.85),
    )

    if with_core_grippers:
      # From the Prep PR (#1196), at (290, 266.5, 62.5) on the deck; not measured on a device. The
      # device reports no gripper position.
      waste_block.assign_child_resource(
        prep_core_gripper_mount(name=self.get_component_name("core_grippers")),
        location=Coordinate(9.7, 269.5, 62.5),
      )

    # PRPAA1087's waste sites (DeckConfiguration); the driver moves them to the connected device's at setup.
    for waste_name, y_pos in [("waste_rear", 30.0), ("waste_front", 10.0), ("waste_mph", 112.0)]:
      waste = Trash(
        name=self.get_component_name(waste_name),
        size_x=6.0,
        size_y=6.0,
        size_z=0.0,
        category="waste_position",
      )
      self.assign_child_resource(
        waste,
        location=Coordinate(x=287.0, y=y_pos, z=68.4),
      )

  def serialize(self) -> dict:
    """Serialize the deck and its component naming prefix."""
    return {**super().serialize(), "name_prefix": self._name_prefix}

  def get_or_create_x_arm(
    self,
    name: str,
    x: float,
    z: float,
    size_x: float,
    size_y: float,
    size_z: float,
    reference_point_from_left: float,
    model: str,
    appearance: Optional[Dict[str, Any]] = None,
    reference_y_range: Optional[Tuple[float, float]] = None,
  ) -> Resource:
    """Get, or create once, the deck-owned X-arm resource called `name`.

    As `HamiltonDeck.get_or_create_x_arm` does for a STAR: created as a child the first time and reused
    thereafter, and placed so its reference point sits at the arm's current x. Its back is flush with
    the back of the device carrying the deck, which being deeper than the deck puts its front at a
    negative y. It rides at the height it is given.

    Args:
      name: what to call it.
      x: where the arm is now, in mm, at its reference point.
      z: the height it rides at, in mm: the top of the channels' travel.
      size_x: how wide the arm is, in mm.
      size_y: how deep the arm is, in mm.
      size_z: how tall the arm is, in mm.
      reference_point_from_left: where its x refers to, in mm from its left edge. Negative when the
        point is to the arm's left.
      model: which arm this is.
      appearance: how a viewer should draw it - `color`, `metalness`, `roughness` - or None for
        the viewer's own default.
      reference_y_range: the Y its reference point reaches, in mm on the deck, or None for the
        arm's whole depth.

    Returns:
      The arm resource, whether it was just created or already there.
    """
    if self.has_resource(name):
      return self.get_resource(name)
    x_arm = Resource(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="x_arm",
      model=model,
    )
    # What x refers to, stated on the resource as the STAR's arm states it.
    x_arm.reference_point = {"x": reference_point_from_left}  # type: ignore[attr-defined]
    if appearance is not None:
      x_arm.appearance = dict(appearance)  # type: ignore[attr-defined]
    # The back of the arm at the back of the device. A deck standing on its own has no device to measure
    # from, and keeps its own back edge.
    device = self.parent
    if device is None or self.location is None:
      y = self.get_absolute_size_y() - size_y
    else:
      y = device.get_absolute_size_y() - self.location.y - size_y
    # The reach, as the viewer draws it: from the arm's front edge.
    if reference_y_range is not None:
      low, high = reference_y_range
      x_arm.reference_point["y_range"] = [low - y, high - y]  # type: ignore[attr-defined]
    self.assign_child_resource(x_arm, location=Coordinate(x - reference_point_from_left, y, z))
    return x_arm

  def __getitem__(self, key: int) -> ResourceHolder:
    """Labware spot by index 0–7 (column-major: ``spot_0_0`` … ``spot_1_3``)."""
    return self.spots[key]

  def __setitem__(self, key: int, value: Resource):
    """Assign a resource to labware spot ``key`` (0–7)."""
    self.spots[key].assign_child_resource(value)
