
from abc import ABCMeta
from typing import Literal, Union, TypeVar, Generic, List, Optional, Generator

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
                items: List[List[T]] = None,
                location: Coordinate = Coordinate(None, None, None),
                category: Optional[str] = None):
    """ Initialize an itemized resource

    Args:
      name: The name of the resource.
      size_x: The size of the resource in the x direction.
      size_y: The size of the resource in the y direction.
      size_z: The size of the resource in the z direction.
      items: The items on the resource. See
        :func:`pylabrobot.liquid_handling.resources.abstract.create_equally_spaced`. Note that items
        names will be prefixed with the resource name.
      location: The location of the resource.
      category: The category of the resource.

    Examples:

      Creating a plate with 96 wells with
      :func:`pylabrobot.liquid_handling.resources.abstract.create_equally_spaced`:

        >>> from pylabrobot.liquid_handling.resources import Plate
        >>> plate = Plate("plate", size_x=1, size_y=1, size_z=1, lid_height=10,
        ...   items=create_equally_spaced(Well
        ...     dx=0, dy=0, dz=0, item_size_x=1, item_size_y=1,
        ...     num_items_x=1, num_items_y=1))

      Creating a plate with 1 well with a list:

        >>> from pylabrobot.liquid_handling.resources import Plate
        >>> plate = Plate("plate", size_x=1, size_y=1, size_z=1, lid_height=10,
        ...   items=[[Well("well", size_x=1, size_y=1, size_z=1)]])
    """

    if items is None:
      items = []

    super().__init__(name, size_x, size_y, size_z, location=location, category=category)

    self.num_items_x = len(items)
    self.num_items_y = len(items[0]) if self.num_items_x > 0 else 0

    for row in items:
      for item in row:
        assert not item.name.startswith(self.name)
        item.name = f"{self.name}_{item.name}"
        self.assign_child_resource(item)

  def __getitem__(self, identifier: Union[str, List[int], slice]) -> List[T]:
    """ Get the items with the given identifier.

    This is a convenience method for getting the items with the given identifier. It is equivalent
    to :meth:`get_items`, but adds support for slicing and supports single items in the same
    functional call. Note that the return type will always be a list, even if a single item is
    requested.

    Examples:
      Getting the items with identifiers "A1" through "E1":

        >>> items["A1:E1"]

        [<Item A1>, <Item B1>, <Item C1>, <Item D1>, <Item E1>]

      Getting the items with identifiers 0 through 4 (note that this is the same as above):

        >>> items[range(5)]

        [<Item A1>, <Item B1>, <Item C1>, <Item D1>, <Item E1>]

      Getting items with a slice (note that this is the same as above):

        >>> items[0:5]

        [<Item A1>, <Item B1>, <Item C1>, <Item D1>, <Item E1>]

      Getting a single item:

        >>> items[0]

        [<Item A1>]
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

  def get_item(self, identifier: Optional[Union[str, int]]) -> Optional[T]:
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

    if identifier is None:
      return None
    elif isinstance(identifier, str):
      row, column = pylabrobot.utils.string_to_position(identifier)
      identifier = row + column * self.num_items_y

    if not 0 <= identifier < (self.num_items_x * self.num_items_y):
      raise IndexError(f"Item with identifier '{identifier}' does not exist on "
                       f"plate '{self.name}'.")

    return self.children[identifier]

  def get_items(self, identifier: Union[Optional[str], List[Optional[int]]]) -> List[Optional[T]]:
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
    elif identifier is None:
      return [None]

    return [self.get_item(i) for i in identifier]

  @property
  def num_items(self) -> int:
    """ The number of items on this resource. """
    return self.num_items_x * self.num_items_y

  def traverse(
    self,
    batch_size: int,
    direction: Literal["up", "down", "right", "left",
                       "snake_up", "snake_down", "snake_left", "snake_right"],
    repeat: bool = False,
  ) -> Generator[T, None, None]:
    """ Traverse the items in the plate.

    Directions `"down"`, `"snake_down"`, `"right"`, and `"snake_right"` start at the top left item
    (A1). Directions `"up"` and `"snake_up"` start at the bottom left (H1). Directions `"left"`
    and `"snake_left"` start at the top right (A12).

    The snake directions alternate between going in the given direction and going in the opposite
    direction. For example, `"snake_down"` will go from A1 to H1, then H2 to A2, then A3 to H3, etc.

    With `repeat=False`, if the batch size does not divide the number of items evenly, the last
    batch will be smaller than the others. With `repeat=True`, the batch would contain the same
    number of items, and batch would be padded with items from the beginning of the plate. For
    example, if the plate has 96 items and the batch size is 5, the first batch would be `[A1, B1,
    C1, D1, E1]`, and the 20th batch would be `[H12, A1, B1, C1, D1]`, and the 21st batch would
    be `[E1, F1, G1, H1, A2]`.

    Args:
      batch_size: The number of items to return in each batch.
      direction: The direction to traverse the items. Can be one of "up", "down", "right", "left",
        "snake_up", "snake_down", "snake_left" or "snake_right".
      repeat: Whether to repeat the traversal when the end of the plate is reached.

    Returns:
      A list of items.

    Raises:
      ValueError: If the direction is not valid.

    Examples:
      Traverse the items in the plate from top to bottom, in batches of 3:

        >>> items.traverse(batch_size=3, direction="down", repeat=False)

        [[<Item A1>, <Item B1>, <Item C1>], [<Item D1>, <Item E1>, <Item F1>], ...]

      Traverse the items in the plate from left to right, in batches of 5, repeating the traversal
      when the end of the plate is reached:

        >>> items.traverse(batch_size=5, direction="right", repeat=True)

        [[<Item A1>, <Item A2>, <Item A3>], [<Item A4>, <Item A5>, <Item A6>], ...]
    """

    def make_generator(indices, batch_size, repeat):
      """ Make a generator from a list, that returns items in batches, optionally repeating """

      # If we're repeating, we need to make a copy of the indices
      if repeat:
        indices = indices.copy()

      start = 0

      while True:
        if (len(indices)-start) < batch_size: # not enough items left
          if repeat:
            # if we're repeating, shift the indices and start over
            indices = indices[start:] + indices[:start]
            start = 0
          else:
            if start != len(indices):
              # there are items left, so yield last (partial) batch
              batch = indices[start:]
              batch = [self.get_item(i) for i in batch]
              yield batch
            break

        batch = indices[start:start+batch_size]
        batch = [self.get_item(i) for i in batch]
        yield batch
        start += batch_size

    if direction == "up":
      # start at the bottom, and go up in each column
      indices = [(8*y+x) for y in range(12) for x in range(7, -1, -1)]
    elif direction == "down":
      # start at the top, and go down in each column. This is how the items are stored in the
      # list, so no need to do anything special.
      indices = list(range(self.num_items))
    elif direction == "right":
      # Start at the top left, and go right in each row
      indices = [(8*y+x) for x in range(8) for y in range(0, 12)]
    elif direction == "left":
      # Start at the top right, and go left in each row
      indices = [(8*y+x) for x in range(8) for y in range(11, -1, -1)]
    elif direction == "snake_right":
      top_right = 88
      indices = []
      for x in range(8):
        if x%2==0:
          # even rows go left to right
          indices.extend((8*y+x) for y in range(0, 12))
        else:
          # odd rows go right to left
          indices.extend((top_right+x-8*y) for y in range(0, 12))
    elif direction == "snake_down":
      top_right = 88
      indices = []
      for x in range(12):
        if x%2==0:
          # even columns go top to bottom
          indices.extend(8*x+y for y in range(0, 8))
        else:
          # odd columns go bottom to top
          indices.extend(8*x+(7-y) for y in range(0, 8))
    elif direction == "snake_left":
      top_right = 88
      indices = []
      for x in range(8):
        if x%2==0:
          # even rows go right to left
          indices.extend((8*y+x) for y in range(11, -1, -1))
        else:
          # odd rows go left to right
          indices.extend((top_right+x-8*y) for y in range(11, -1, -1))
    elif direction == "snake_up":
      top_right = 88
      indices = []
      for x in range(12):
        if x%2==0:
          # even columns go bottom to top
          indices.extend(8*x+y for y in range(7, -1, -1))
        else:
          # odd columns go top to bottom
          indices.extend(8*x+(7-y) for y in range(7, -1, -1))
    else:
      raise ValueError(f"Invalid direction '{direction}'.")

    return make_generator(indices, batch_size, repeat)


def create_equally_spaced(
    klass: T,
    num_items_x: int, num_items_y: int,
    dx: float, dy: float, dz: float,
    item_size_x: float, item_size_y: float,
    **kwargs
) -> List[List[T]]:
  """ Make equally spaced resources.

  See :class:`ItemizedResource` for more details.

  Args:
    klass: The class of the resource to create
    num_items_x: The number of items in the x direction
    num_items_y: The number of items in the y direction
    dx: The center for items in the left column
    dy: The center for items in the top row
    dz: The z coordinate for all items
    item_size_x: The size of the items in the x direction
    item_size_y: The size of the items in the y direction
    **kwargs: Additional keyword arguments to pass to the resource constructor

  Returns:
    A list of lists of resources. The outer list contains the columns, and the inner list contains
    the items in each column.
  """

  # TODO: It probably makes more sense to transpose this.

  items = []
  for i in range(num_items_x):
    items.append([])
    for j in range(num_items_y):
      name = f"{klass.__name__.lower()}_{i}_{j}"
      item = klass(
        name=name,
        location=Coordinate(
          x=dx + i * item_size_x,
          y=dy + (num_items_y-j-1) * item_size_y,
          z=dz),
        size_x=item_size_x,
        size_y=item_size_y,
        **kwargs
      )
      items[i].append(item)

  return items
