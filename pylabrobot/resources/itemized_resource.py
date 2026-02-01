import sys
from abc import ABCMeta
from collections import OrderedDict
from string import ascii_uppercase as LETTERS
from typing import (
  Dict,
  Generator,
  Generic,
  List,
  Optional,
  Sequence,
  Tuple,
  TypeVar,
  Union,
  cast,
)

import pylabrobot.utils

from .resource import Resource

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal


T = TypeVar("T", bound=Resource)


class ItemizedResource(Resource, Generic[T], metaclass=ABCMeta):
  """Base class for Itemized resources.

  This class provides utilities for getting child resources by an identifier. It also restricts the
  child resources to instances of the generic type `T`, specified by the subclass. For example, a
  :class:`pylabrobot.resources.plate.Plate` can only have child resources of type
  :class:`pylabrobot.resources.well.Well`.

  Items are always arranged in a uniform grid with equal spacing between items. The spacing between
  items in the x direction is given by :attr:`item_dx` and the spacing in the y direction is given
  by :attr:`item_dy`.

  .. note::
    This class is not meant to be used directly, but rather to be subclassed, most commonly by
    :class:`pylabrobot.resources.Plate` and :class:`pylabrobot.resources.TipRack`.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, T]] = None,
    ordering: Optional[OrderedDict[str, str]] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    """Initialize an itemized resource

    Args:
      name: The name of the resource.
      size_x: The size of the resource in the x direction.
      size_y: The size of the resource in the y direction.
      size_z: The size of the resource in the z direction.
      ordered_items: The items on the resource, along with their identifier (as keys). See
        :func:`pylabrobot.resources.create_ordered_items_2d`. If this is specified, `ordering` must
        be `None`. Keys must be in transposed MS Excel style notation, e.g. "A1" for the first item,
        "B1" for the item below that, "A2" for the item to the right, etc.
      ordering: The order of the items on the resource. This is a dict of item identifier <> item name.
        If this is specified, `ordered_items` must be `None`. See `ordered_items` for the format of the
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
        item.name = f"{self.name}_{item.name}"  # prefix item name with resource name
        self.assign_child_resource(item, location=item.location)
      self._ordering = OrderedDict(
        (identifier, item.name) for identifier, item in ordered_items.items()
      )
    else:
      if ordering is None:
        raise ValueError("Must specify either `ordered_items` or `ordering`.")
      self._ordering = ordering

    # validate that ordering is in the transposed Excel style notation
    for identifier in self._ordering:
      if identifier[0] not in LETTERS or not identifier[1:].isdigit():
        raise ValueError("Ordering must be in the transposed Excel style notation, e.g. 'A1'.")

  def __getitem__(
    self,
    identifier: Union[str, int, Sequence[int], Sequence[str], slice, range],
  ) -> List[T]:
    """Get the items with the given identifier.

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
      if ":" in identifier:  # multiple # TODO: deprecate this, use `"A1":"E1"` instead (slice)
        return self.get_items(identifier)

      return [self.get_item(identifier)]  # single

    if isinstance(identifier, int):
      return [self.get_item(identifier)]

    if isinstance(identifier, (slice, range)):
      start, stop = identifier.start, identifier.stop
      if isinstance(identifier.start, str):
        start = list(self._ordering.keys()).index(identifier.start)
      elif identifier.start is None:
        start = 0
      if isinstance(identifier.stop, str):
        stop = list(self._ordering.keys()).index(identifier.stop)
      elif identifier.stop is None:
        stop = self.num_items
      identifier = list(range(start, stop, identifier.step or 1))
      return self.get_items(identifier)

    if isinstance(identifier, (list, tuple)):
      return self.get_items(identifier)

    raise TypeError(f"Invalid identifier type: {type(identifier)}")

  def get_item(self, identifier: Union[str, int, Tuple[int, int]]) -> T:
    """Get the item with the given identifier.

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
      identifier = LETTERS[row] + str(column + 1)  # standard transposed-Excel style notation
    if isinstance(identifier, str):
      try:
        identifier = list(self._ordering.keys()).index(identifier)
      except ValueError as e:
        raise IndexError(
          f"Item with identifier '{identifier}' does not exist on " f"resource '{self.name}'."
        ) from e

    if not 0 <= identifier < self.num_items:
      raise IndexError(
        f"Item with identifier '{identifier}' does not exist on " f"resource '{self.name}'."
      )

    # Cast child to item type. Children will always be `T`, but the type checker doesn't know that.
    return cast(T, self.children[identifier])

  def get_items(self, identifiers: Union[str, Sequence[int], Sequence[str]]) -> List[T]:
    """Get the items with the given identifier.

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
    start: Literal["top_left", "bottom_left", "top_right", "bottom_right"],
    direction: Literal[
      "up",
      "down",
      "right",
      "left",
      "snake_up",
      "snake_down",
      "snake_left",
      "snake_right",
    ],
    repeat: bool = False,
  ) -> Generator[List[T], None, None]:
    """Traverse the items in this resource.

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
      direction: The direction to traverse the items. Can be one of "up", "down", "right",
      "left", "snake_up", "snake_down", "snake_left" or "snake_right".
      repeat: Whether to repeat the traversal when the end of the resource is reached.

    Raises:
      ValueError: If the direction is not valid.

    Examples:
      Traverse the items in the resource from top to bottom, in batches of 3:

        >>> items.traverse(batch_size=3, direction="down", start="top_left", repeat=False)

        [[<Item A1>, <Item B1>, <Item C1>], [<Item D1>, <Item E1>, <Item F1>], ...]

      Traverse the items in the resource from left to right, in batches of 5, repeating the
      traversal when the end of the resource is reached:

        >>> items.traverse(batch_size=5, direction="right", start="top_left", repeat=True)

        [[<Item A1>, <Item A2>, <Item A3>], [<Item A4>, <Item A5>, <Item A6>], ...]
    """

    def make_generator(
      items: List[T], batch_size: int, repeat: int
    ) -> Generator[List[T], None, None]:
      """Make a generator from a list, that returns items in batches, optionally repeating"""

      # If we're repeating, we need to make a copy of the items
      if repeat:
        items = items.copy()

      start = 0

      while True:
        if (len(items) - start) < batch_size:  # not enough items left
          if repeat:
            # if we're repeating, shift the items and start over
            items = items[start:] + items[:start]
            start = 0
          else:
            if start != len(items):
              # there are items left, so yield last (partial) batch
              batch = items[start:]
              yield batch
            break

        batch = items[start : start + batch_size]
        yield batch
        start += batch_size

    rows = list(range(self.num_items_y))
    cols = list(range(self.num_items_x))

    # Determine starting rows and cols based on start position
    if "bottom" in start:
      if "down" in direction:
        raise ValueError(f"Cannot start from {start} and go {direction}.")
      rows.reverse()

    if "right" in start:
      if "right" in direction:
        raise ValueError(f"Cannot start from {start} and go {direction}.")
      cols.reverse()

    items: List[T] = []

    if direction in {"up", "down"}:
      for col_idx in cols:
        for row_idx in rows:
          items.append(self.get_item((row_idx, col_idx)))

    elif direction in {"left", "right"}:
      for row_idx in rows:
        for col_idx in cols:
          items.append(self.get_item((row_idx, col_idx)))

    elif direction.startswith("snake_"):
      axis = direction.split("_")[1]

      if axis in {"up", "down"}:
        # Snake up/down: alternate direction for each column
        for i, col_idx in enumerate(cols):
          row_order = rows if i % 2 == 0 else list(reversed(rows))

          for row_idx in row_order:
            items.append(self.get_item((row_idx, col_idx)))

      else:  # snake_left or snake_right
        # Snake left/right: alternate direction for each row
        for i, row_idx in enumerate(rows):
          col_order = cols if i % 2 == 0 else list(reversed(cols))

          for col_idx in col_order:
            items.append(self.get_item((row_idx, col_idx)))

    return make_generator(items, batch_size, repeat)

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(name={self.name!r}, size_x={self._size_x}, "
      f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})"
    )

  @staticmethod
  def _occupied_func(item: T):
    return "O" if item.children else "-"

  def summary(self, occupied_func=None):
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

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "ordering": self._ordering,
    }

  def index_of_item(self, item: T) -> Optional[int]:
    """Return the index of the given item in the resource, or `None` if not found."""
    for i, i_item_name in enumerate(self._ordering.values()):
      if i_item_name == item.name:
        return i
    return None

  def get_child_identifier(self, item: T) -> str:
    """Get the identifier of the item."""
    for identifier, i_item_name in self._ordering.items():
      if i_item_name == item.name:
        return identifier
    raise ValueError(f"Item {item} not found in resource.")

  def get_child_column(self, item: T) -> int:
    """Get the column of the item."""
    identifier = self.get_child_identifier(item)
    return int(identifier[1:]) - 1  # convert to 0-indexed

  def get_child_row(self, item: T) -> int:
    """Get the row of the item."""
    identifier = self.get_child_identifier(item)
    return LETTERS.index(identifier[0])  # convert to 0-indexed

  def get_all_items(self) -> List[T]:
    """Get all items in the resource. Items are in a 1D list, starting from the top left and going
    down, then right."""

    return self.get_items(range(self.num_items))

  def _get_grid_size(self, identifiers) -> Tuple[int, int]:
    """Get the size of the grid from the identifiers, or raise an error if not a full grid."""
    rows_set, columns_set = set(), set()
    for identifier in identifiers:
      rows_set.add(identifier[0])
      columns_set.add(identifier[1:])

    rows, columns = (
      sorted(list(rows_set)),
      sorted(list(columns_set), key=int),
    )

    expected_identifiers = sorted([c + r for c in rows for r in columns])
    if sorted(identifiers) != expected_identifiers:
      raise ValueError(f"Not a full grid: {identifiers}")
    return len(rows), len(columns)

  @property
  def num_items_x(self) -> int:
    """The number of items in the x direction, if the resource is a full grid. If the resource is
    not a full grid, an error will be raised."""
    _, num_items_x = self._get_grid_size(self._ordering)
    return num_items_x

  @property
  def num_items_y(self) -> int:
    """The number of items in the y direction, if the resource is a full grid. If the resource is
    not a full grid, an error will be raised."""
    num_items_y, _ = self._get_grid_size(self._ordering)
    return num_items_y

  @property
  def item_dx(self) -> float:
    """The spacing between items in the x direction."""
    if self.num_items_x < 2:
      raise ValueError("Cannot compute item_dx with fewer than 2 items in the x direction.")
    item_a1 = self.get_item("A1")
    item_a2 = self.get_item("A2")
    if item_a1.location is None or item_a2.location is None:
      raise ValueError("Item locations are not set.")
    return item_a2.location.x - item_a1.location.x

  @property
  def item_dy(self) -> float:
    """The spacing between items in the y direction."""
    if self.num_items_y < 2:
      raise ValueError("Cannot compute item_dy with fewer than 2 items in the y direction.")
    item_a1 = self.get_item("A1")
    item_b1 = self.get_item("B1")
    if item_a1.location is None or item_b1.location is None:
      raise ValueError("Item locations are not set.")
    return item_a1.location.y - item_b1.location.y

  @property
  def items(self) -> List[str]:
    raise NotImplementedError("Deprecated.")

  def column(self, column: int) -> List[T]:
    """Get all items in the given column."""
    return self[column * self.num_items_y : (column + 1) * self.num_items_y]

  def row(self, row: Union[int, str]) -> List[T]:
    """Get all items in the given row.

    Args:
      row: The row index. Either an integer starting at ``0`` or a letter
        ``"A"``-``"P"`` (case insensitive) corresponding to ``0``-``15``.

    Raises:
      ValueError: If ``row`` is a string outside ``"A"``-``"P"``.
    """

    if isinstance(row, str):
      letter = row.upper()
      if letter not in LETTERS[:16]:
        raise ValueError("Row must be between 'A' and 'P'.")
      row = LETTERS.index(letter)

    return self[row :: self.num_items_y]
