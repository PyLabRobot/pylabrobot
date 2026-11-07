from typing import Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.hamilton_decks import (
  _TRACK_WIDTH,
  HamiltonDeck,
)
from pylabrobot.resources.trash import Trash


class VantageDeck(HamiltonDeck):
  """A Hamilton Vantage deck."""

  def __init__(
    self,
    size: float,
    name="deck",
    category: str = "deck",
    origin: Coordinate = Coordinate.zero(),
    with_trash: bool = True,
    model: Optional[str] = None,
  ) -> None:
    """Create a new Vantage deck of the given size.

    TODO: parameters for setting up the Entry Exit module, waste, etc.

    Args:
      size: The size of the deck to create. Must be 1.3 or 2.0 (meters).
    """

    # Unfortunately, float is not supported as a Literal type, so we have to use a runtime check.
    if size == 1.3:
      # Curiously stored in ML_STAR2.deck in HAMILTON\\Config after editing the deck to 1.3m using
      # the HxConfigEditor.
      size_x = 1237.5
      super().__init__(
        num_tracks=54,
        size_x=size_x,
        size_y=653.5,
        size_z=900.0,
        name=name,
        category=category,
        origin=origin,
        model=model,
      )
      self.size = 1.3

      if with_trash:
        trash_x = size_x - 480  # works with vantage 1.3 (480) (used to be 460)

        # an experimentally informed guess.
        self.assign_child_resource(
          resource=Trash("trash", size_x=0, size_y=260, size_z=0),
          location=Coordinate(x=trash_x, y=185.6, z=137.1),
        )  # z I am not sure about
    elif size == 2.0:
      # A 2.0 m Vantage reports 84 tracks in its machine configuration, on the same 22.5 mm
      # raster as the 1.3 m deck.
      size_x = (84 + 1) * _TRACK_WIDTH  # 1912.5
      super().__init__(
        num_tracks=84,
        size_x=size_x,
        size_y=653.5,
        size_z=900.0,
        name=name,
        category=category,
        origin=origin,
        model=model,
      )
      self.size = 2.0

      if with_trash:
        # x and z are where a 2.0 m Vantage ejects tips into the waste when initializing the
        # pipetting channels (`A1PMDI xp13845 ... tz1235`: eject x 1384.5, tip-end deposit
        # z 123.5, with the channels spread between y 201.6 and 389.1). y is the same front
        # edge as on the 1.3 m deck.
        self.assign_child_resource(
          resource=Trash("trash", size_x=0, size_y=260, size_z=0),
          location=Coordinate(x=1384.5, y=185.6, z=123.5),
        )
    else:
      raise ValueError(f"Invalid deck size: {size}")

  def track_to_location(self, track: int) -> Coordinate:
    x = 32.5 + (track - 1) * _TRACK_WIDTH
    return Coordinate(x=x, y=63, z=100)

  def serialize(self) -> dict:
    super_serialized = super().serialize()
    # Drop the keys `__init__` derives from `size`, and the `HamiltonDeck` keys it does not take.
    for key in [
      "size_x",
      "size_y",
      "size_z",
      "num_tracks",
      "num_rails",
      "with_trash96",
      "core_grippers",
    ]:
      super_serialized.pop(key, None)
    return {"size": self.size, **super_serialized}
