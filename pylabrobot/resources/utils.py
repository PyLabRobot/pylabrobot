import re
from itertools import groupby
from string import ascii_uppercase as LETTERS
from typing import Any, Dict, List, Optional, Type, TypeVar

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

T = TypeVar("T", bound=Resource)


def create_equally_spaced_2d(
  klass: Type[T],
  num_items_x: int,
  num_items_y: int,
  dx: float,
  dy: float,
  dz: float,
  item_dx: float,
  item_dy: float,
  **kwargs,
) -> List[List[T]]:
  """Make equally spaced resources in a 2D grid. Also see :meth:`create_equally_spaced_x` and
  :meth:`create_equally_spaced_y`.

  Args:
    klass: The class of the resource to create
    num_items_x: The number of items in the x direction
    num_items_y: The number of items in the y direction
    dx: The bottom left corner for items in the left column
    dy: The bottom left corner for items in the bottom row
    dz: The z coordinate for all items
    item_dx: The size of the items in the x direction
    item_dy: The size of the items in the y direction
    **kwargs: Additional keyword arguments to pass to the resource constructor

  Returns:
    A list of lists of resources. The outer list contains the columns, and the inner list contains
    the items in each column.
  """

  # TODO: It probably makes more sense to transpose this.

  items: List[List[T]] = []
  for i in range(num_items_x):
    items.append([])
    for j in range(num_items_y):
      name = f"{klass.__name__.lower()}_{i}_{j}"
      item = klass(name=name, **kwargs)
      item.location = Coordinate(
        x=dx + i * item_dx,
        y=dy + (num_items_y - j - 1) * item_dy,
        z=dz,
      )
      items[i].append(item)

  return items


def create_equally_spaced_x(
  klass: Type[T],
  num_items_x: int,
  dx: float,
  dy: float,
  dz: float,
  item_dx: float,
  **kwargs,
) -> List[T]:
  """Make equally spaced resources over the x-axis. See :meth:`create_equally_spaced_2d` for more
  details.

  Args:
    klass: The class of the resource to create
    num_items_x: The number of items in the x direction
    dx: The bottom left corner for items in the left column
    dy: The bottom left corner for items in the bottom row
    dz: The z coordinate for all items
    item_dx: The size of the items in the x direction
    **kwargs: Additional keyword arguments to pass to the resource constructor

  Returns:
    A list of lists of resources.
  """

  items = create_equally_spaced_2d(
    klass=klass,
    num_items_x=num_items_x,
    num_items_y=1,
    dx=dx,
    dy=dy,
    dz=dz,
    item_dx=item_dx,
    item_dy=0,
    **kwargs,
  )
  return [items[i][0] for i in range(num_items_x)]


def create_equally_spaced_y(
  klass: Type[T],
  num_items_y: int,
  dx: float,
  dy: float,
  dz: float,
  item_dy: float,
  **kwargs,
) -> List[T]:
  """Make equally spaced resources over the y-axis. See :meth:`create_equally_spaced_2d` for more
  details.

  Args:
    klass: The class of the resource to create
    num_items_y: The number of items in the y direction
    dx: The bottom left corner for items in the left column
    dy: The bottom left corner for items in the bottom row
    dz: The z coordinate for all items
    item_dy: The size of the items in the y direction
    **kwargs: Additional keyword arguments to pass to the resource constructor

  Returns:
    A list of lists of resources.
  """

  items = create_equally_spaced_2d(
    klass=klass,
    num_items_x=1,
    num_items_y=num_items_y,
    dx=dx,
    dy=dy,
    dz=dz,
    item_dx=0,
    item_dy=item_dy,
    **kwargs,
  )
  return items[0]


def create_ordered_items_2d(
  klass: Type[T],
  num_items_x: int,
  num_items_y: int,
  dx: float,
  dy: float,
  dz: float,
  item_dx: float,
  item_dy: float,
  **kwargs,
) -> Dict[str, T]:
  """Make ordered resources in a 2D grid, with the keys being the identifiers in transposed
  MS-Excel style. This is useful for initializing `ItemizedResource`.

  Args:
    klass: The class of the resource to create
    num_items_x: The number of items in the x direction
    num_items_y: The number of items in the y direction
    dx: The bottom left corner for items in the left column wrt the parent
    dy: The bottom left corner for items in the bottom row wrt the parent
    dz: The z coordinate for all items
    item_dx: The spacing of the items in the x direction (center to center)
    item_dy: The spacing of the items in the y direction (center to center)
    **kwargs: Additional keyword arguments to pass to the resource constructor

  Returns:
    A dict of resources. The keys are the identifiers in transposed MS-Excel format, so the top
    left item is "A1", the item to the bottom is "B1", the item to the right is "A2", and so on.
  """

  items = create_equally_spaced_2d(
    klass=klass,
    num_items_x=num_items_x,
    num_items_y=num_items_y,
    dx=dx,
    dy=dy,
    dz=dz,
    item_dx=item_dx,
    item_dy=item_dy,
    **kwargs,
  )
  keys = [f"{LETTERS[j]}{i + 1}" for i in range(num_items_x) for j in range(num_items_y)]
  for key, item in zip(keys, (item for sublist in items for item in sublist)):
    item.name = f"{klass.__name__.lower()}_{key}"
  return dict(zip(keys, [item for sublist in items for item in sublist]))


U = TypeVar("U", bound=Resource)


def query(
  root: Resource,
  type_: Type[U] = Resource,  # type: ignore
  name: Optional[str] = None,
  x: Optional[float] = None,
  y: Optional[float] = None,
  z: Optional[float] = None,
) -> List[U]:
  """Query resources based on their attributes.

  Args:
    root: The root resource to search
    type_: The type of resources to search for
    name: The regular expression to match the name of the resources
    x: The x-coordinate of the resources
    y: The y-coordinate of the resources
    z: The z-coordinate of the resources
  """
  matched: List[U] = []
  for resource in root.children:
    if type_ is not None and not isinstance(resource, type_):
      continue
    if name is not None and not re.match(name, resource.name):
      continue
    if x is not None and (resource.location is None or resource.location.x != x):
      continue
    if y is not None and (resource.location is None or resource.location.y != y):
      continue
    if z is not None and (resource.location is None or resource.location.z != z):
      continue
    matched.append(resource)

    matched.extend(
      query(
        root=resource,
        type_=type_,
        name=name,
        x=x,
        y=y,
        z=z,
      )
    )
  return matched


R = TypeVar("R", bound=Resource)


def sort_by_xy_and_chunk_by_x(
  resources: list[R],
  max_chunk_size: int,
  sort_chunks_by_size: bool = True,
) -> list[list[R]]:
  """
  Sort resources spatially and partition them into chunks for channel processing.

  Procedure
  ---------
  1. Sort all resources by:
      - x ascending
      - y descending within each x
  2. Group resources into chunks based on identical x values.
  3. Split each chunk into sub-chunks of size <= max_chunk_size.
  4. Optionally sort the resulting sub-chunks by their length (smallest -> largest).

  Example:
    >>> sorted_chunks = sort_by_xy_and_chunk_by_x(well_list, max_chunk_size=8)
    >>> [
    ...   list(
    ...     zip(
    ...       [r.get_identifier() for r in chunk],
    ...       [r.get_absolute_location() for r in chunk],
    ...     )
    ...   )
    ...   for chunk in sorted_chunks
    ... ]
    [[('D1', Coordinate(x=450.9, y=402.3, z=164.45)),
      ('H1', Coordinate(x=450.9, y=366.3, z=164.45)), ...],
    [('D2', Coordinate(x=459.9, y=402.3, z=164.45)), ...]]

  Args:
    resources: List of resources that implement ``.get_absolute_location()``, returning an object with ``x`` and ``y`` attributes.
    max_chunk_size: Maximum allowed size for any produced chunk or sub-chunk.
    sort_chunks_by_size: If True (default), the output list of chunks is sorted by ascending chunk size. If False, chunks retain their original order.

  Returns:
    A list of grouped and sorted resources.
  """

  # 1. & 2.: Sort by x ascending, y descending
  sorted_resources_with_loc = sorted(
    resources,
    key=lambda r: (
      r.get_absolute_location().x,
      -r.get_absolute_location().y,
    ),
  )

  # 3. Group into chunks by x
  grouped_by_x = [
    list(group)
    for _, group in groupby(
      sorted_resources_with_loc,
      key=lambda r: r.get_absolute_location().x,
    )
  ]

  # 4. Split chunks by max_chunk_size
  split_chunks: list[list[Any]] = []
  for chunk in grouped_by_x:
    for i in range(0, len(chunk), max_chunk_size):
      split_chunks.append(chunk[i : i + max_chunk_size])

  # Optional 5: Sort chunks by number of elements
  return sorted(split_chunks, key=len) if sort_chunks_by_size else split_chunks
