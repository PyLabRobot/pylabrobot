"""The Biomek i7 deck: a plate with a grid of pins that ALPs are placed on.

Biomek places an ALP by a pin: `column` is the pin column of its left pins, `row` the pin row of its
front pins. Biomek's own frame has X to the right and Y towards the front, in centimetres; this
deck's frame is PyLabRobot's, millimetres with Y towards the back, origin at the front left corner:

  x = X + FRAME_X
  y = FRAME_Y - Y
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List

from pylabrobot.beckman_coulter.biomek_i7.resource_model.alps import ALP_TYPES, BiomekALP
from pylabrobot.beckman_coulter.biomek_i7.resource_model.units import cm

# The frame's conversions live in `units.py`; re-exported here, where they were first.
from pylabrobot.beckman_coulter.biomek_i7.units import FRAME_X as FRAME_X
from pylabrobot.beckman_coulter.biomek_i7.units import FRAME_Y as FRAME_Y
from pylabrobot.beckman_coulter.biomek_i7.units import biomek_x as biomek_x
from pylabrobot.beckman_coulter.biomek_i7.units import biomek_y as biomek_y
from pylabrobot.beckman_coulter.biomek_i7.units import deck_x as deck_x
from pylabrobot.beckman_coulter.biomek_i7.units import deck_y as deck_y
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.resource_holder import ResourceHolder

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import DeviceConfiguration

logger = logging.getLogger(__name__)

# Pin pitch, both ways. Biomek_i7.bif: DeckPitch; also every ALP definition's pin spacing.
PIN_PITCH = cm(2.0828)

# Where pin column 0 and pin row 0 are in Biomek's frame. Fitted over every ALP placed on this
# unit's three decks (Biomek_i7.bif Decks/*/Groups: X1 + Pins.OffsetX against column, and
# Y1 + Pins.OffsetY + (Pins.RowCount - 1) x DeckPitch against row): all within 0.1 cm, which is the
# per-ALP calibration these files carry.
PIN_COLUMN_0_X = cm(-1.0827)
PIN_ROW_0_Y = cm(3.128)

# The deck's extent in that frame. Chosen to enclose what this unit's layouts place, from the left
# trash bin to the right of the Span-8 wash station and from the front ALPs to the back of the
# ELx405; the deck plate itself has not been measured.
DECK_SIZE_X = cm(160.0)
DECK_SIZE_Y = cm(86.0)
# Working height above the deck: what the viewer draws the deck's space up to. Not a Biomek value.
DECK_SIZE_Z = cm(90.0)


class BiomekI7Deck(Deck):
  """The deck of a Biomek i7: ALPs placed by pin, and their positions found by Biomek's names."""

  def __init__(self, name: str = "deck", **kwargs: Any):
    """`kwargs`: what `serialize` writes besides the name (the deck's fixed size and category);
    taken so a saved deck loads (`Resource.load_from_json_file`), and otherwise ignored."""
    super().__init__(
      name=name,
      size_x=DECK_SIZE_X,
      size_y=DECK_SIZE_Y,
      size_z=DECK_SIZE_Z,
      category="deck",
    )

  @property
  def alps(self) -> Dict[str, BiomekALP]:
    """The ALPs on the deck, by name."""
    return {c.name: c for c in self.children if isinstance(c, BiomekALP)}

  @staticmethod
  def pin_location(column: int, row: int) -> Coordinate:
    """Where pin (`column`, `row`) is on this deck."""
    return Coordinate(
      FRAME_X + PIN_COLUMN_0_X + column * PIN_PITCH,
      FRAME_Y - (PIN_ROW_0_Y + row * PIN_PITCH),
      0,
    )

  def alp_location(self, alp: BiomekALP, column: int, row: int) -> Coordinate:
    """Where `alp`'s front left corner goes with its left pins in `column` and front pins in `row`."""
    pin = self.pin_location(column, row)
    rows = alp.pin_count[1]
    # The ALP's back left pin, from its back left corner, is its pin offset; its front pins are
    # rows - 1 pitches in front of that, and its front edge the rest of its depth further.
    back_edge_to_front_pins = alp.pin_offset[1] + (rows - 1) * PIN_PITCH
    front_pins_to_front_edge = alp.get_size_y() - back_edge_to_front_pins
    return Coordinate(pin.x - alp.pin_offset[0], pin.y - front_pins_to_front_edge, 0)

  def assign_alp(self, alp: BiomekALP, column: int, row: int) -> None:
    """Place `alp` with its left pins in pin `column` and its front pins in pin `row`."""
    self.assign_child_resource(alp, location=self.alp_location(alp, column, row))

  @classmethod
  def from_configuration(
    cls, configuration: "DeviceConfiguration", name: str = "deck"
  ) -> "BiomekI7Deck":
    """The deck a declared configuration lays out (`deck_layout`, imported from the instrument
    file's default deck), each ALP by its pin column and row with its positions' names.

    Where the instrument file has an ALP's left edge (`X1`) whole pin pitches from where its
    column puts it, the column is corrected to match: the file lists the Span-8 wash station at
    column 71 and places it one pitch to the left, on all three decks of the file this was written
    against, so the column is its pin offset's, not a placement. An ALP type this package has no
    model for is left out, with a warning.
    """
    deck = cls(name=name)
    for placed in configuration.deck_layout:
      factory = ALP_TYPES.get(placed.alp_type)
      if factory is None:
        logger.warning(
          "%s: no model for ALP type %r; left off the deck", placed.name, placed.alp_type
        )
        continue
      alp = factory(placed.name, list(placed.positions))
      column = placed.column
      if placed.x1 is not None:
        left = biomek_x(deck.alp_location(alp, column, placed.row).x) / 10  # cm
        steps = round((placed.x1 - left) * 10 / PIN_PITCH)
        if steps:
          logger.info(
            "%s: placed %+d columns from its listed %d, at its X1", alp.name, steps, column
          )
          column += steps
      deck.assign_alp(alp, column=column, row=placed.row)
    return deck

  def get_position(self, name: str) -> ResourceHolder:
    """A deck position by Biomek's name for it (`P1`, `TL1`, `TR1`)."""
    resource = self.get_resource(name)
    if not isinstance(resource, ResourceHolder):
      raise TypeError(f"{name} is a {type(resource).__name__}, not a deck position")
    return resource

  @property
  def position_names(self) -> List[str]:
    """Every deck position Biomek names on this deck, across all its ALPs."""
    return [site.name for alp in self.alps.values() for site in alp.sites.values()]

  def summary(self) -> str:
    """One line per ALP, left to right and back to front: what it is, where, its positions."""
    lines = []
    for alp in sorted(
      self.alps.values(), key=lambda a: (a.get_location_wrt(self).x, -a.get_location_wrt(self).y)
    ):
      loc = alp.get_location_wrt(self)
      sites = " ".join(s.name for s in alp.sites.values())
      lines.append(
        f"{alp.name:20s} {alp.alp_type or '':17s} x={loc.x:7.1f} y={loc.y:6.1f}  {sites}"
      )
    return "\n".join(lines)
