"""Hamilton Prep deck."""

import re
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.resources.carrier import PlateHolder, ResourceHolder
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck, _built
from pylabrobot.resources.hamilton.core_grippers import (
  HamiltonCoreGrippers,
  prep_core_gripper_holder,
)
from pylabrobot.resources.hamilton.tip_creators import hamilton_teaching_needle_300uL
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.trough import Trough

# How tall the deck's working volume is, in mm, from the deck surface. The channels travel from 18.03
# to 167.5 mm and the deck configuration reports the same Z range, as read off PRPAA1087 on
# 2026-09-13, so the top of that travel is the top of the deck.
PREP_DECK_SIZE_Z = 167.5

# The plate holders' grid, measured off the deck. TODO: probe the deck's own origin; the first
# holder still sits at the deck's (0, 0).
PREP_SPOT_PITCH_X = 140.0
PREP_SPOT_PITCH_Y = 95.0

# The steel block between the two columns, measured off the deck: a 6 x 6 mm top 22 mm up, flaring
# to 8.49 mm below 14 mm. Placed by that top's left front corner, from this deck's origin.
PREP_CALIBRATION_BLOCK_LOCATION = Coordinate(133.75, 185.25, 0.0)


def hamilton_prep_plateholder(name: str) -> PlateHolder:
  """A PREP deck's plate holder: four corner clips around an insert pedestal, as measured.

  What stands here is centred between the clips, on the pedestal 4.5 mm above the deck.
  """
  size_x, size_y = 133.5, 91.5
  return PlateHolder(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=13.5,
    child_location=Coordinate(
      x=(size_x - 127.76) / 2,
      y=(size_y - 85.48) / 2,
      z=4.5,  # the pedestal's top. TODO: probe it
    ),
    pedestal_size_z=0,  # a plate rests on the pedestal's rim. TODO: probe a seated plate
    model="hamilton_prep_plateholder",
  )


class PrepDeck(Deck):
  """Hamilton PREP deck: labware spots, waste block, liquid waste container, teaching needle, and waste positions.

  Geometry aligns with the prep_tcp / MLPrep DeckConfiguration teaching site and waste
  sites used by :class:`~pylabrobot.hamilton.prep.driver.features.pipettes.Pipettes`
  (``waste_rear``, ``waste_front``, ``waste_mph``). Validate coordinates on hardware
  (plastic mounts, calibration) before production use.

  This is **not** a :class:`HamiltonSTARDeck` (rails/teaching rack layout differ).
  """

  def __init__(
    self,
    name: str = "Prep_Deck",
    prefix: Optional[str] = None,
    size_x: float = 300.0,
    size_y: float = 394.0,
    size_z: float = PREP_DECK_SIZE_Z,
    origin: Coordinate = Coordinate.zero(),
    category: str = "deck",
    with_spots: bool = True,
    with_calibration_block: bool = True,
    with_waste_block: bool = True,
    with_waste_positions: bool = True,
    with_core_grippers: bool = False,
  ):
    """A Prep deck of the given size, carrying the parts a Prep has.

    Each `with_` says whether to build that part. A deck read back from a file builds none of
    them: they are its saved children, as they are on a `HamiltonSTARDeck`.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      origin=origin,
      category=category,
      prefix=prefix,
    )
    prefix = self.prefix
    if with_spots:
      for column in range(2):
        for row in range(4):
          x = column * PREP_SPOT_PITCH_X
          y = row * PREP_SPOT_PITCH_Y
          spot = hamilton_prep_plateholder(name=f"{prefix}_spot_{column}_{row}")
          self.assign_child_resource(spot, location=Coordinate(x, y, 0))

    if with_calibration_block:
      self.assign_child_resource(
        Resource(
          name=f"{prefix}_calibration_block",
          size_x=6.0,
          size_y=6.0,
          size_z=22.0,
          category="calibration_block",
          model="hamilton_prep_calibration_block",
        ),
        location=PREP_CALIBRATION_BLOCK_LOCATION,
      )

    if with_waste_block:
      self._build_waste_block(prefix, with_core_grippers=with_core_grippers)

    if with_waste_positions:
      # PRPAA1087's waste sites (DeckConfiguration); the driver moves them to the connected
      # device's at setup.
      for waste_name, y_pos in [("waste_rear", 30.0), ("waste_front", 10.0), ("waste_mph", 112.0)]:
        self.assign_child_resource(
          Trash(
            name=f"{prefix}_{waste_name}",
            size_x=6.0,
            size_y=6.0,
            size_z=0.0,
            category="waste_position",
          ),
          location=Coordinate(x=287.0, y=y_pos, z=68.4),
        )

  def _build_waste_block(self, prefix: str, with_core_grippers: bool) -> None:
    """The waste block, and what stands on it: the trough, the teaching needle, the tool holder."""

    # Where tips are dropped, as on the STAR's waste block, carrying the liquid waste trough, the
    # teaching needle and the CoRe gripper mount. From the deck's front edge at Y -3 to 2 mm in
    # front of the gripper mount's back edge (Y 286.5).
    # 12.5 x 292 x 75 measured off the block; 13 x 287.5 x 73 was the estimate it replaces.
    waste_block = Trash(
      name=f"{prefix}_waste_block",
      size_x=12.5,
      size_y=292.0,
      size_z=75.0,
      model="hamilton_prep_wasteblock",
    )
    # Where the block stands for the parked back tool to sit on its nominal centre, x 290.0,
    # y 275.5. Probing it on PRPAA1087 found x within 0.01 mm and y 0.41 mm back, which is that
    # device's calibration rather than the part. 280.3, -3 was the estimate this replaces.
    self.assign_child_resource(waste_block, location=Coordinate(282.25, -4.25, 0))

    # The liquid waste trough, part of the standard deck. As wide as the waste block, and filling
    # the gap from the tip drop area's back edge (132.7 mm from the block's front) to the teaching
    # needle's front edge, its top level with the block's top. Its volume is the box's.
    tip_drop_size_y = 132.7
    liquid_waste_size_y = 214.29 - (-3 + tip_drop_size_y)
    liquid_waste_size_z = 25.0  # measured
    liquid_waste_container = Trough(
      name=f"{prefix}_liquid_waste_container",
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
      name=f"{prefix}_teaching_tip",
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
      # Measured off the block: the holder overhangs its left face by 4 mm, its flat 40 mm up with
      # a rail to 55 between the two tools.
      waste_block.assign_child_resource(
        prep_core_gripper_holder(name=f"{prefix}_core_gripper_holder"),
        location=Coordinate(-4.0, 249.5, 37.0),
      )

  def serialize(self) -> dict:
    """Serialize this deck. What it carries is encoded as children, so a deck read back from this
    builds none of it: the parts come from the children, as on a `HamiltonSTARDeck`."""
    return {
      **super().serialize(),
      "with_spots": False,
      "with_calibration_block": False,
      "with_waste_block": False,
      "with_waste_positions": False,
      "with_core_grippers": False,
    }

  # -- what the deck carries --------------------------------------------------------------------
  # Found among the children rather than held from when they were built, so a deck read back from
  # a file finds them too.

  @property
  def spots(self) -> List[ResourceHolder]:
    """The eight labware spots, column-major: `spot_0_0` first, `spot_1_3` last."""
    numbered = []
    for child in self.children:
      called = re.fullmatch(r"(?:.*_)?spot_(\d+)_(\d+)", child.name)
      if called is not None and isinstance(child, ResourceHolder):
        numbered.append(((int(called.group(1)), int(called.group(2))), child))
    return [spot for _, spot in sorted(numbered, key=lambda pair: pair[0])]

  @property
  def calibration_block(self) -> Optional[Resource]:
    """The steel block between the two columns, or None if this deck was built without it."""
    return _built(self.children, "calibration_block", Resource)

  @property
  def waste_block(self) -> Optional[Trash]:
    """Where tips are dropped, or None if this deck was built without it."""
    return _built(self.children, "waste_block", Trash)

  @property
  def liquid_waste_container(self) -> Optional[Trough]:
    """The liquid waste trough on the waste block, or None if this deck carries none."""
    block = self.waste_block
    return None if block is None else _built(block.children, "liquid_waste_container", Trough)

  @property
  def teaching_tip_spot(self) -> Optional[TipSpot]:
    """Where the teaching needle stands, or None if this deck carries none."""
    block = self.waste_block
    return None if block is None else _built(block.children, "teaching_tip", TipSpot)

  @property
  def core_gripper_holder(self) -> Optional[HamiltonCoreGrippers]:
    """The CO-RE gripper holder on the waste block, or None if this deck carries none."""
    block = self.waste_block
    return (
      None if block is None else _built(block.children, "core_gripper_holder", HamiltonCoreGrippers)
    )

  @property
  def waste_positions(self) -> Dict[str, Trash]:
    """The waste sites, keyed as the driver names them: `waste_rear`, `waste_front`, `waste_mph`."""
    found = {}
    for name in ("waste_rear", "waste_front", "waste_mph"):
      waste = _built(self.children, name, Trash)
      if waste is not None:
        found[name] = waste
    return found

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
      name: what to call it. The deck puts its own prefix in front, so two devices' arms stand in
        one tree.
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
    name = self.prefixed(name)
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
