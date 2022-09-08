
from abc import ABCMeta
from typing import Union, TypeVar, Generic, List, Callable, Optional

import pylabrobot.utils

from .coordinate import Coordinate
from .resource import Resource


T = TypeVar("T")


class ItemizedResource(Resource, Generic[T], metaclass=ABCMeta):
  """ Base class for Itemized resources.

  .. note::
    This class is not meant to be used directly, but rather to be subclassed, most commonly by
    :class:`pylabrobot.liquid_handling.resources.abstract.Plate` and
    :class:`pylabrobot.liquid_handling.resources.abstract.Tips`.

  Subclasses are items that have a number of equally spaced child resources, e.g. a plate with
  wells or a tip resource with tips.
  """

  def __init__(self, name: str, size_x: float, size_y: float, size_z: float,
                num_items_x: int, num_items_y: int, create_item: Callable[[int, int], T],
                location: Coordinate = Coordinate(None, None, None),
                category: Optional[str] = None):
    super().__init__(name, size_x, size_y, size_z, location=location, category=category)
    self.num_items_x = num_items_x
    self.num_items_y = num_items_y

    self._items = []
    for i in range(num_items_x):
      for j in range(num_items_y):
        item = create_item(i, j)
        self._items.append(item)
        self.assign_child_resource(item)

  def serialize(self) -> dict:
    """ Serialize the resource. """

    return {
      **super().serialize(),
      "num_items_x": self.num_items_x,
      "num_items_y": self.num_items_y,
    }

  @classmethod
  def deserialize(cls, data: dict):
    """ Deserialize the resource. """
    # Children are created by us, so we don't need to deserialize them.
    data["children"] = []
    return super().deserialize(data)

  def __getitem__(self, identifier: Union[str, List[int], slice]) -> List[T]:
    """ Get the items with the given identifier.

    This is a convenience method for getting the items with the given identifier. It is equivalent
    to :meth:`get_items`, but adds support for slicing and supports single items in the same
    functional call. Note that the return type will always be a list, even if a single item is
    requested.
    """

    if isinstance(identifier, str):
      if ":" in identifier:
        identifier = pylabrobot.utils.string_to_indices(identifier)
      else:
        identifier = [pylabrobot.utils.string_to_index(identifier)]
    elif isinstance(identifier, int):
      identifier = [identifier]
    elif isinstance(identifier, slice):
      if isinstance(identifier.start, str):
        identifier.start = pylabrobot.utils.string_to_index(identifier.start)
      if isinstance(identifier.stop, str):
        identifier.stop = pylabrobot.utils.string_to_index(identifier.stop)
      identifier = range(identifier.start, identifier.stop)

    return self.get_items(identifier)

  def get_item(self, identifier: Union[str, int]) -> T:
    """ Get the item with the given identifier.

    Args:
      identifier: The identifier of the item. Either a string or an integer. If an integer, it is
        the index of the item in the list of items (counted from 0, top to bottom, left to right).
        If a string, it uses transposed MS Excel style notation, e.g. "A1" for the first item, "B1"
        for the item below that, etc.

    Returns:
      The item with the given identifier.

    Raises:
      IndexError: If the identifier is out of range. The range is 0 to (num_items_x * num_items_y -
        1).
    """

    if isinstance(identifier, str):
      row, column = pylabrobot.utils.string_to_position(identifier)
      identifier = row + column * self.num_items_y

    if not 0 <= identifier < (self.num_items_x * self.num_items_y):
      raise IndexError(f"Item with identifier '{identifier}' does not exist on "
                       f"plate '{self.name}'.")

    return self._items[identifier]

  def get_items(self, identifier: Union[str, List[int]]) -> List[T]:
    """ Get the items with the given identifier.

    Args:
      identifier: The identifier of the items. Either a string or a list of integers. If a string,
        it uses transposed MS Excel style notation, e.g. "A1" for the first item, "B1" for the item
        below that, etc. Regions of items can be specified using a colon, e.g. "A1:H1" for the first
        column. If a list of integers, it is the indices of the items in the list of items (counted
        from 0, top to bottom, left to right).

    Returns:
      The items with the given identifier.

    Examples:
      Getting the items with identifiers "A1" through "E1":

        >>> items.get_items("A1:E1")

        [<Item A1>, <Item B1>, <Item C1>, <Item D1>, <Item E1>]

      Getting the items with identifiers 0 through 4:

        >>> items.get_items(range(5))

        [<Item A1>, <Item B1>, <Item C1>, <Item D1>, <Item E1>]
    """

    if isinstance(identifier, str):
      identifier = pylabrobot.utils.string_to_indices(identifier)

    return [self.get_item(i) for i in identifier]
