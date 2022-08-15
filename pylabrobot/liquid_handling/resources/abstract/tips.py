""" Abstract base class for Tips resources. """

from abc import ABCMeta

from typing import List, Union

import pylabrobot.utils

from .resource import Resource, Coordinate
from .tip_type import TipType


class Tip(Resource):
  def __init__(self, name: str, size_x: float, size_y: float, tip_type: TipType,
    location: Coordinate = ..., category: str = "tip"):
    super().__init__(name, size_x, size_y, tip_type.tip_length, location, category)


class Tips(Resource, metaclass=ABCMeta):
  """ Abstract base class for Tips resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    tip_type: TipType,
    dx: float,
    dy: float,
    dz: float,
    tip_size_x: float,
    tip_size_y: float,
    num_tips_x: int,
    num_tips_y: int,
    location: Coordinate = Coordinate(None, None, None)
  ):
    super().__init__(name, size_x, size_y, size_z, location=location + Coordinate(dx, dy, dz),
                     category="tips")
    self.tip_type = tip_type
    self.dx = dx
    self.dy = dy
    self.dz = dz

    self.tip_size_x = tip_size_x
    self.tip_size_y = tip_size_y
    self.num_tips_x = num_tips_x
    self.num_tips_y = num_tips_y

    self._tips = []
    for i in range(self.num_tips_x):
      for j in range(self.num_tips_y):
        tip = Tip(f"{self.name}_{i}_{j}", tip_size_x, tip_size_y, tip_type=tip_type,
          location=Coordinate(i * tip_size_x, j * -tip_size_y, 0))
        self.assign_child_resource(tip)
        self._tips.append(tip)

  def serialize(self):
    return dict(
      **super().serialize(),
      tip_type=self.tip_type.serialize(),
      dx=self.dx,
      dy=self.dy,
      dz=self.dz
    )

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self.get_size_x()}, "
            f"size_y={self.get_size_y()}, size_z={self.get_size_z()}, tip_type={self.tip_type}, "
            f"dx={self.dx}, dy={self.dy}, dz={self.dz}, location={self.location})")


  def get_item(self, identifier: Union[str, int]) -> Tip:
    """ Get the item with the given identifier.

    Args:
      identifier: The identifier of the tip. Either a string or an integer. If an integer, it is
        the index of the tip in the list of tips (counted from 0, top to bottom, left to right).
        If a string, it uses transposed MS Excel style notation, e.g. "A1" for the first tip, "B1"
        for the tip below that, etc.

    Returns:
      The tip with the given identifier.

    Raises:
      IndexError: If the identifier is out of range. The range is 0 to (num_tips_x * num_tips_y -
        1).
    """

    if isinstance(identifier, str):
      row, column = pylabrobot.utils.string_to_position(identifier)
      identifier = row + column * self.num_tips_y

    if not 0 <= identifier < (self.num_tips_x * self.num_tips_y):
      raise IndexError(f"Tip with identifier '{identifier}' does not exist on "
                        "tips '{self.name}'.")

    return self._tips[identifier]

  def get_tips(self, identifier: Union[str, List[int]]) -> List[Tip]:
    """ Get the tips with the given identifier.

    Args:
      identifier: The identifier of the tips. Either a string or a list of integers. If a string,
        it uses transposed MS Excel style notation, e.g. "A1" for the first tip, "B1" for the tip
        below that, etc. Regions of tips can be specified using a colon, e.g. "A1:H1" for the first
        column. If a list of integers, it is the indices of the tips in the list of tips (counted
        from 0, top to bottom, left to right).

    Returns:
      The tips with the given identifier.

    Examples:
      Getting the tips with identifiers "A1" through "E1":

        >>> tips.get_tips("A1:E1")

        [<Tip A1>, <Tip B1>, <Tip C1>, <Tip D1>, <Tip E1>]

      Getting the tips with identifiers 0 through 4:

        >>> tips.get_tips(range(5))

        [<Tip A1>, <Tip B1>, <Tip C1>, <Tip D1>, <Tip E1>]
    """

    if isinstance(identifier, str):
      identifier = pylabrobot.utils.string_to_indices(identifier)

    return [self.get_tip(i) for i in identifier]
