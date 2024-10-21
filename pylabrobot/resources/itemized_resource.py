from abc import ABCMeta
import sys
from typing import Dict, Union, Tuple, TypeVar, Generic, List, Optional, Generator, Sequence, cast
from string import ascii_uppercase as LETTERS

import pylabrobot.utils

from .resource import Resource

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


T = TypeVar("T", bound=Resource)


class ItemizedResource(Resource, Generic[T], metaclass=ABCMeta):
  """ Base class for Itemized resources.

  This class provides utilities for getting child resources by an identifier. It also restricts the
  child resources to instances of the generic type `T`, specified by the subclass. For example, a
  :class:`pylabrobot.resources.plate.Plate` can only have child resources of type
  :class:`pylabrobot.resources.well.Well`.

  .. note::
    This class is not meant to be used directly, but rather to be subclassed, most commonly by
    :class:`pylabrobot.resources.Plate` and :class:`pylabrobot.resources.TipRack`.
  """

  def __init__(self, name: str, size_x: float, size_y: float, size_z: float,
                ordered_items: Optional[Dict[str, T]] = None,
                ordering: Optional[List[str]] = None,
                category: Optional[str] = None,
                model: Optional[str] = None):
    """ Initialize an itemized resource

    Args:
      name: The name of the resource.
      size_x: The size of the resource in the x direction.
      size_y: The size of the resource in the y direction.
      size_z: The size of the resource in the z direction.
      ordered_items: The items on the resource, along with their identifier (as keys). See
        :func:`pylabrobot.resources.create_ordered_items_2d`. If this is specified, `ordering` must
        be `None`. Keys must be in transposed MS Excel style notation, e.g. "A1" for the first item,
        "B1" for the item below that, "A2" for the item to the right, etc.
      ordering: The order of the items on the resource. This is a list of identifiers. If this is
        specified, `ordered_items` must be `None`. See `ordered_items` for the format of the
        identifiers.
      category: The category of the resource.

    Examples:

      Creating a plate with 96 wells with
      :func:`pylabrobot.resources.create_ordered_items_2d`:

        >>> from pylabrobot.resources import Plate
        >>> plate = Plate("plate", size_x=1, size_y=1, size_z=1,
        ...   ordered_items=create_ordered_items_2d(Well
        ...     dx=0, dy=0, dz=0, item_size_x=1, item_size_y=1,
        ...     num_items_x=1, num_items_y=1))

      Creating a plate with 1 Well in a dict:

        >>> from pylabrobot.resources import Plate
        >>> plate = Plate("plate", size_x=1, size_y=1, size_z=1,
        ...   ordered_items={"A1": Well("well", size_x=1, size_y=1, size_z=1)})
    """

    super().__init__(name, size_x, size_y, size_z, category=category, model=model)

    if ordered_items is not None:
      if ordering is not None:
        raise ValueError("Cannot specify both `ordered_items` and `ordering`.")
      for item in ordered_items.values():
        if item.location is None:
          raise ValueError("Item location must be specified if supplied at initialization.")
        item.name = f"{self.name}_{item.name}" # prefix item name with resource name
        self.assign_child_resource(item, location=item.location)
      self._ordering = list(ordered_items.keys())
    else:
      if ordering is None:
        raise ValueError("Must specify either `ordered_items` or `ordering`.")
      self._ordering = ordering

    # validate that ordering is in the transposed Excel style notation
    for identifier in self._ordering:
      if not identifier[0] in LETTERS or not identifier[1:].isdigit():
        raise ValueError("Ordering must be in the transposed Excel style notation, e.g. 'A1'.")

  def __getitem__(
    self,
    identifier: Union[str, int, Sequence[int], Sequence[str], slice, range]
  ) -> List[T]:
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
      if ":" in identifier: # multiple # TODO: deprecate this, use `"A1":"E1"` instead (slice)
        return self.get_items(identifier)

      return [self.get_item(identifier)] # single

    if isinstance(identifier, int):
      return [self.get_item(identifier)]

    if isinstance(identifier, (slice, range)):
      start, stop = identifier.start, identifier.stop
      if isinstance(identifier.start, str):
        start = self._ordering.index(identifier.start)
      if isinstance(identifier.stop, str):
        stop = self._ordering.index(identifier.stop)
      identifier = list(range(start, stop))
      return self.get_items(identifier)

    if isinstance(identifier, (list, tuple)):
      return self.get_items(identifier)

    raise TypeError(f"Invalid identifier type: {type(identifier)}")

  def get_item(self, identifier: Union[str, int, Tuple[int, int]]) -> T:
    """ Get the item with the given identifier.

    Args:
      identifier: The identifier of the item. Either a string, an integer, or a tuple. If an
      integer, it is the index of the item in the list of items (counted from 0, top to bottom, left
      to right).  If a string, it uses transposed MS Excel style notation, e.g. "A1" for the first
      item, "B1" for the item below that, etc. If a tuple, it is (row, column).

    Raises:
      IndexError: If the identifier is out of range. The range is 0 to self.num_items-1 (inclusive).
    """

    if isinstance(identifier, tuple):
      row, column = identifier
      identifier = LETTERS[row] + str(column+1) # standard transposed-Excel style notation
    if isinstance(identifier, str):
      try:
        identifier = self._ordering.index(identifier)
      except ValueError as e:
        raise IndexError(f"Item with identifier '{identifier}' does not exist on "
                         f"resource '{self.name}'.") from e

    if not 0 <= identifier < self.num_items:
      raise IndexError(f"Item with identifier '{identifier}' does not exist on "
                       f"resource '{self.name}'.")

    # Cast child to item type. Children will always be `T`, but the type checker doesn't know that.
    return cast(T, self.children[identifier])

  def get_items(self, identifiers: Union[str, Sequence[int], Sequence[str]]) -> List[T]:
    """ Get the items with the given identifier.

    Args:
      identifier: Deprecated. Use `identifiers` instead. # TODO(deprecate-ordered-items)
      identifiers: The identifiers of the items. Either a string range or a list of integers. If a
        string, it uses transposed MS Excel style notation. Regions of items can be specified using
        a colon, e.g. "A1:H1" for the first column. If a list of integers, it is the indices of the
        items in the list of items (counted from 0, top to bottom, left to right).

    Examples:
      Getting the items with identifiers "A1" through "E1":

        >>> items.get_items("A1:E1")

        [<Item A1>, <Item B1>, <Item C1>, <Item D1>, <Item E1>]

      Getting the items with identifiers 0 through 4:

        >>> items.get_items(range(5))

        [<Item A1>, <Item B1>, <Item C1>, <Item D1>, <Item E1>]
    """

    if isinstance(identifiers, str):
      identifiers = pylabrobot.utils.expand_string_range(identifiers)
    return [self.get_item(i) for i in identifiers]

  @property
  def num_items(self) -> int:
    return len(self.children)

  def traverse(
    self,
    batch_size: int,
    direction: Literal["up", "down", "right", "left",
                       "snake_up", "snake_down", "snake_left", "snake_right"],
    repeat: bool = False,
  ) -> Generator[List[T], None, None]:
    """ Traverse the items in this resource.

    Directions `"down"`, `"snake_down"`, `"right"`, and `"snake_right"` start at the top left item
    (A1). Directions `"up"` and `"snake_up"` start at the bottom left (H1). Directions `"left"`
    and `"snake_left"` start at the top right (A12).

    The snake directions alternate between going in the given direction and going in the opposite
    direction. For example, `"snake_down"` will go from A1 to H1, then H2 to A2, then A3 to H3, etc.

    With `repeat=False`, if the batch size does not divide the number of items evenly, the last
    batch will be smaller than the others. With `repeat=True`, the batch would contain the same
    number of items, and batch would be padded with items from the beginning of the resource. For
    example, if the resource has 96 items and the batch size is 5, the first batch would be `[A1,
    B1, C1, D1, E1]`, the 20th batch would be `[H12, A1, B1, C1, D1]`, and the 21st batch would
    be `[E1, F1, G1, H1, A2]`.

    Args:
      batch_size: The number of items to return in each batch.
      direction: The direction to traverse the items. Can be one of "up", "down", "right", "left",
        "snake_up", "snake_down", "snake_left" or "snake_right".
      repeat: Whether to repeat the traversal when the end of the resource is reached.

    Returns:
      A list of items.

    Raises:
      ValueError: If the direction is not valid.

    Examples:
      Traverse the items in the resource from top to bottom, in batches of 3:

        >>> items.traverse(batch_size=3, direction="down", repeat=False)

        [[<Item A1>, <Item B1>, <Item C1>], [<Item D1>, <Item E1>, <Item F1>], ...]

      Traverse the items in the resource from left to right, in batches of 5, repeating the
      traversal when the end of the resource is reached:

        >>> items.traverse(batch_size=5, direction="right", repeat=True)

        [[<Item A1>, <Item A2>, <Item A3>], [<Item A4>, <Item A5>, <Item A6>], ...]
    """

    def make_generator(indices, batch_size, repeat) -> Generator[List[T], None, None]:
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

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})")

  @staticmethod
  def _occupied_func(item: T):
    return "O" if item.children else "-"

  def make_grid(self, occupied_func=None):
    # The "occupied_func" is a function that checks if a resource has something in it,
    # and returns a single character representing its status.
    if occupied_func is None:
      occupied_func = self._occupied_func

    # Make a title with summary information.
    info_str = repr(self)

    if self.num_items_y > len(LETTERS):
      # TODO: This will work up to 384-well plates.
      return info_str + " (too many rows to print)"

    # Calculate the maximum number of digits required for any column index.
    max_digits = len(str(self.num_items_x))

    # Create the header row with numbers aligned to the columns.
    # Use right-alignment specifier.
    header_row = "    " + " ".join(f"{i+1:<{max_digits}}" for i in range(self.num_items_x))

    # Create the item grid with resource absence/presence information.
    item_grid = [
      [occupied_func(self.get_item((i, j))) for j in range(self.num_items_x)]
      for i in range(self.num_items_y)
    ]
    spacer = " " * max(1, max_digits)
    item_list = [LETTERS[i] + ":  " + spacer.join(row) for i, row in enumerate(item_grid)]
    item_text = "\n".join(item_list)

    # Simple footer with dimensions.
    footer_text = f"{self.num_items_x}x{self.num_items_y} {self.__class__.__name__}"

    return info_str + "\n" + header_row + "\n" + item_text + "\n" + footer_text

  def print_grid(self, occupied_func=None):
    print(self.make_grid(occupied_func=occupied_func))

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "ordering": self._ordering,
    }

  def index_of_item(self, item: T) -> Optional[int]:
    """ Return the index of the given item in the resource, or `None` if the resource was not found.
    """
    for i, i_item in enumerate(self.children):
      if i_item == item:
        return i
    return None

  def get_all_items(self) -> List[T]:
    """ Get all items in the resource. Items are in a 1D list, starting from the top left and going
    down, then right. """

    return self.get_items(range(self.num_items))

  def _get_grid_size(self, identifiers) -> Tuple[int, int]:
    """ Get the size of the grid from the identifiers, or raise an error if not a full grid. """
    rows_set, columns_set = set(), set()
    for identifier in identifiers:
      rows_set.add(identifier[0])
      columns_set.add(identifier[1:])

    rows, columns = sorted(list(rows_set)), sorted(list(columns_set), key=int)

    expected_identifiers = sorted([c + r for c in rows for r in columns])
    if sorted(identifiers) != expected_identifiers:
      raise ValueError(f"Not a full grid: {identifiers}")
    return len(rows), len(columns)

  @property
  def num_items_x(self) -> int:
    """ The number of items in the x direction, if the resource is a full grid. If the resource is
    not a full grid, an error will be raised. """
    _, num_items_x = self._get_grid_size(self._ordering)
    return num_items_x

  @property
  def num_items_y(self) -> int:
    """ The number of items in the y direction, if the resource is a full grid. If the resource is
    not a full grid, an error will be raised. """
    num_items_y, _ = self._get_grid_size(self._ordering)
    return num_items_y

  @property
  def items(self) -> List[str]:
    raise NotImplementedError("Deprecated.")
