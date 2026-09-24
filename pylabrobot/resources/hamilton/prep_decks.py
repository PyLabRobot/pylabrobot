"""Hamilton Prep deck."""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.resources.carrier import ResourceHolder
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck, _built
from pylabrobot.resources.errors import NoLocationError
from pylabrobot.resources.hamilton.core_grippers import (
  HamiltonCoreGrippers,
  prep_core_gripper_holder,
)
from pylabrobot.resources.hamilton.tip_creators import hamilton_teaching_needle_300uL
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.trough import Trough

logger = logging.getLogger(__name__)

# How tall the deck's working volume is, in mm, from the deck surface. The channels travel from 18.03
# to 167.5 mm and the deck configuration reports the same Z range, as read off PRPAA1087 on
# 2026-09-13, so the top of that travel is the top of the deck.
PREP_DECK_SIZE_Z = 167.5

# The plate holders' grid, probed on PRPAA1087 on 2026-09-20.
PREP_FIRST_SLOT_LOCATION = Coordinate(-4.20, -1.52, 0.0)
PREP_SPOT_PITCH_X = 140.0
PREP_SPOT_PITCH_Y = 95.0

# The steel block between the two columns, measured off the deck: a 6 x 6 mm top 22 mm up, flaring
# to 8.49 mm below 14 mm. Placed by that top's left front corner, from this deck's origin.
PREP_CALIBRATION_BLOCK_LOCATION = Coordinate(129.55, 183.73, 0.0)

# The bin under the tip drop, beside the waste block: its left face on the block's right face, its
# front 35 mm in front of the block's and its top 4 mm below the block's top.
PREP_WASTE_BIN_SIZE = (134.0, 280.0, 122.0)
PREP_WASTE_BIN_LOCATION = Coordinate(282.25 + 12.5, -4.25 - 35.0, 75.0 - 4.0 - 122.0)


def hamilton_prep_resourceholder(name: str) -> ResourceHolder:
  """A PREP deck's labware spot: four corner clips around an insert pedestal, as measured.

  What stands here is centred between the clips, on the moat around the pedestal: the pedestal's
  top is 4.5 mm above the deck and the moat is a millimetre below that, probed on PRPAA1087 at
  0.89, 0.94, 0.82 and 0.79 across the four spots.

  Everything the Prep carries reaches the moat floor - a tip rack's tray and a plate with a
  millimetre or more under its wells alike - so the seat is that floor rather than the pedestal.
  A plate whose wells sit flush with its own base would come to rest on the pedestal instead, a
  millimetre higher than this puts it.
  """
  size_x, size_y = 133.5, 91.5
  return ResourceHolder(
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=13.5,
    child_location=Coordinate(
      x=(size_x - 127.76) / 2,
      y=(size_y - 85.48) / 2,
      z=3.5,
    ),
    model="hamilton_prep_resourceholder",
  )


class PrepDeck(Deck):
  """Hamilton PREP deck: labware spots, waste block, waste bin, liquid waste container, teaching
  needle, CoRe gripper holder, and waste positions.

  Geometry aligns with the prep_tcp / MLPrep DeckConfiguration teaching site and waste
  sites used by :class:`~pylabrobot.hamilton.prep.driver.features.pipettes.Pipettes`
  (``waste_rear``, ``waste_front``, ``waste_mph``). Validate coordinates on hardware
  (plastic mounts, calibration) before production use.

  This is **not** a :class:`HamiltonSTARDeck` (rails/teaching rack layout differ).
  """

  safe_deck_height: float = 75.0
  """Up to this height, in mm, a resource is under a traversing channel whatever tip it carries: the
  stop discs at Z safety, 167.5, less the 87.1 mm a 1000 uL tip reaches below them, with margin.
  Higher may be safe, depending on the tip."""

  def get_component_name(self, name: str) -> str:
    """Qualify a built-in component name with this deck's optional device prefix."""
    return name if self._name_prefix is None else f"{self._name_prefix}_{name}"

  def _check_safe_deck_height(self, resource: Resource) -> None:
    """Warn when a resource the device does not carry stands above `safe_deck_height`."""
    up: Optional[Resource] = resource
    while up is not None and up is not self:
      if up.category == "x_arm" or isinstance(up, HeadTool):
        return
      up = up.parent
    for each in [resource, *resource.get_all_children()]:
      try:
        z_top = each.get_location_wrt(self, z="top").z
      except NoLocationError:
        continue
      if z_top > self.safe_deck_height:
        logger.warning(
          "Resource '%s' stands %s mm high on the deck, above the %s mm that is safe under any tip.",
          each.name,
          z_top,
          self.safe_deck_height,
        )

  def update_safe_deck_height_from_tips(self, stop_disc_z_max: float, margin: float = 5.0) -> float:
    """Set `safe_deck_height` from the longest tip this deck holds, then check what stands on it.

    Called only when wanted: the class default holds for the longest tip the device can use. See
    `get_safe_deck_height_from_tips` for the arguments. The height set, in mm.
    """
    # Here, not at the top: lib/spatial imports resources, whose package imports this deck.
    from pylabrobot.lib.spatial.clearance import get_safe_deck_height_from_tips

    self.safe_deck_height = get_safe_deck_height_from_tips(self, stop_disc_z_max, margin)
    for child in self.children:
      self._check_safe_deck_height(child)
    return self.safe_deck_height

  def __init__(
    self,
    name: str = "deck",
    size_x: float = 300.0,
    size_y: float = 394.0,
    size_z: float = PREP_DECK_SIZE_Z,
    origin: Coordinate = Coordinate.zero(),
    category: str = "deck",
    with_spots: bool = True,
    with_calibration_block: bool = True,
    with_waste_block: bool = True,
    with_waste_bin: bool = True,
    with_waste_positions: bool = True,
    with_core_grippers: bool = True,
    name_prefix: Optional[str] = None,
  ):
    """A Prep deck of the given size, carrying the parts a Prep has.

    Each `with_` says whether to build that part. A deck read back from a file builds none of
    them: they are its saved children, as they are on a `HamiltonSTARDeck`.

    Args:
      name_prefix: prefix for component names, without a trailing underscore. None keeps
        standalone component names. The deck itself keeps its explicitly supplied name.
    """
    self._name_prefix = name_prefix
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      origin=origin,
      category=category,
    )
    if with_spots:
      for column in range(2):
        for row in range(4):
          x = PREP_FIRST_SLOT_LOCATION.x + column * PREP_SPOT_PITCH_X
          y = PREP_FIRST_SLOT_LOCATION.y + row * PREP_SPOT_PITCH_Y
          spot = hamilton_prep_resourceholder(name=self.get_component_name(f"spot_{column}_{row}"))
          self.assign_child_resource(spot, location=Coordinate(x, y, 0))

    if with_calibration_block:
      self.assign_child_resource(
        Resource(
          name=self.get_component_name("calibration_block"),
          size_x=6.0,
          size_y=6.0,
          size_z=22.0,
          category="calibration_block",
          model="hamilton_prep_calibration_block",
        ),
        location=PREP_CALIBRATION_BLOCK_LOCATION,
      )

    if with_waste_block:
      self._build_waste_block(with_core_grippers=with_core_grippers)

    if with_waste_bin:
      # Its handle reaches 22 mm in front of it, as the model shows.
      size_x, size_y, size_z = PREP_WASTE_BIN_SIZE
      self.assign_child_resource(
        Resource(
          name=self.get_component_name("waste_bin"),
          size_x=size_x,
          size_y=size_y,
          size_z=size_z,
          category="waste_bin",
          model="hamilton_prep_waste_bin",
        ),
        location=PREP_WASTE_BIN_LOCATION,
      )

    if with_waste_positions:
      # PRPAA1087's waste sites (DeckConfiguration); the driver moves them to the connected
      # device's at setup.
      for waste_name, y_pos in [("waste_rear", 30.0), ("waste_front", 10.0), ("waste_mph", 112.0)]:
        self.assign_child_resource(
          Trash(
            name=self.get_component_name(waste_name),
            size_x=6.0,
            size_y=6.0,
            size_z=0.0,
            category="waste_position",
          ),
          location=Coordinate(x=287.0, y=y_pos, z=68.4),
        )

    self.register_did_assign_resource_callback(self._check_safe_deck_height)

  def _build_waste_block(self, with_core_grippers: bool) -> None:
    """The waste block, and what stands on it: the trough, the teaching needle, the tool holder."""

    # Where tips are dropped, as on the STAR's waste block, carrying the liquid waste trough, the
    # teaching needle and the CoRe gripper mount. From the deck's front edge at Y -3 to 2 mm in
    # front of the gripper mount's back edge (Y 286.5).
    # 12.5 x 292 x 75 measured off the block; 13 x 287.5 x 73 was the estimate it replaces.
    waste_block = Trash(
      name=self.get_component_name("waste_block"),
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
    # device's at setup. Z is where the needle's collar rests, as every tip spot's is: the height
    # the device takes it from and puts it back at, leaving its body from 23.85 to 83.75.
    teaching_needle_spot = TipSpot(
      name=self.get_component_name("teaching_needle"),
      size_x=6.0,
      size_y=6.0,
      make_tip=hamilton_teaching_needle_300uL,
      size_z=0.0,
      category="teaching_needle",
    )
    waste_block.assign_child_resource(
      teaching_needle_spot,
      location=Coordinate(x=4.46, y=217.29, z=75.75),
    )
    # The needle stands in it, as the needles stand in a STAR deck's rack: it is part of the deck,
    # not something a run puts there.
    teaching_needle_spot.tracker.add_tip(
      teaching_needle_spot.make_tip(), origin=teaching_needle_spot, commit=True
    )

    if with_core_grippers:
      # Measured off the block: the holder overhangs its left face by 4 mm, its flat 40 mm up with
      # a rail to 55 between the two tools.
      waste_block.assign_child_resource(
        prep_core_gripper_holder(name=self.get_component_name("core_grippers")),
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
      "with_waste_bin": False,
      "with_waste_positions": False,
      "with_core_grippers": False,
      "name_prefix": self._name_prefix,
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
  def waste_bin(self) -> Optional[Resource]:
    """The bin beside the waste block, or None if this deck was built without it."""
    return _built(self.children, "waste_bin", Resource)

  @property
  def liquid_waste_container(self) -> Optional[Trough]:
    """The liquid waste trough on the waste block, or None if this deck carries none."""
    block = self.waste_block
    return None if block is None else _built(block.children, "liquid_waste_container", Trough)

  @property
  def teaching_needle_spot(self) -> Optional[TipSpot]:
    """Where the teaching needle stands, or None if this deck carries none."""
    block = self.waste_block
    return None if block is None else _built(block.children, "teaching_needle", TipSpot)

  @property
  def core_gripper_holder(self) -> Optional[HamiltonCoreGrippers]:
    """The CO-RE gripper holder on the waste block, or None if this deck carries none."""
    block = self.waste_block
    return None if block is None else _built(block.children, "core_grippers", HamiltonCoreGrippers)

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
      name: what to call it.
      x: where the arm is now, in mm, at its reference point.
      z: the height it rides at, in mm on this deck, to the underside of its body.
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
