"""Models: definitions as they are kept, with uniform grids given once.

A definition whose items are alike but for where they are gives them once, as an `ItemGrid`, and
`expand_grids` makes every item from it. Every store expands a definition before it builds a tree
from it, so a stored resource's items are always its children. `compact_grids` finds such grids in a
definition, so a model is written with them.
"""

from __future__ import annotations

from typing import List

from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.v1 import types_pb2 as pb
from pylabrobot.resources.utils import row_index_to_label

# How far apart two locations may be and still be the same, in mm.
_TOLERANCE = 1e-6
# Decimals a grid's spacing and offset are kept to.
_DECIMALS = 6


def identifier_of(column: int, row: int) -> str:
  """The identifier of the item in a column and row, "A1" for the first."""
  return f"{row_index_to_label(row)}{column + 1}"


def _replace_in_names(definition: pb.ResourceDefinition, old: str, new: str) -> None:
  attributes = definition.attributes
  attributes.name = attributes.name.replace(old, new)
  if attributes.HasField("tip_spot"):
    prototype = attributes.tip_spot.prototype_tip
    prototype.name = prototype.name.replace(old, new)
  for child in definition.children:
    _replace_in_names(child, old, new)


def _grid_items(spec: pb.ItemizedSpec) -> List[pb.ResourceDefinition]:
  """Every item a grid makes, column-major."""
  grid = spec.grid
  items = []
  for column in range(spec.num_items_x):
    for row in range(spec.num_items_y):
      item = pb.ResourceDefinition()
      item.CopyFrom(grid.item)
      identifier = identifier_of(column, row)
      item.attributes.identifier = identifier
      item.attributes.location.CopyFrom(
        pb.Vec3(
          x=grid.offset.x + column * grid.item_dx,
          y=grid.offset.y + (spec.num_items_y - row - 1) * grid.item_dy,
          z=grid.offset.z,
        )
      )
      _replace_in_names(item, "{identifier}", identifier)
      items.append(item)
  return items


def expand_grids(definition: pb.ResourceDefinition) -> None:
  """Make the items of every grid in a definition, in place.

  Raises:
    ServerError: A grid in a resource that also lists items, or one with no items.
  """
  attributes = definition.attributes
  if attributes.HasField("itemized") and attributes.itemized.HasField("grid"):
    spec = attributes.itemized
    if any(child.attributes.identifier for child in definition.children):
      raise ServerError(
        pb.PROBLEM_WRONG_KIND,
        f"'{attributes.name}' gives its items both as a grid and as children",
        attributes.name,
      )
    if spec.num_items_x < 1 or spec.num_items_y < 1:
      raise ServerError(
        pb.PROBLEM_OUT_OF_RANGE,
        f"'{attributes.name}' has a grid of {spec.num_items_x} by {spec.num_items_y} items",
        attributes.name,
      )
    others = list(definition.children)
    del definition.children[:]
    definition.children.extend(_grid_items(spec))
    definition.children.extend(others)
    spec.ClearField("grid")
  for child in definition.children:
    expand_grids(child)


def same_definitions(expanded: pb.ResourceDefinition, original: pb.ResourceDefinition) -> bool:
  """Whether two definitions are equal, with locations equal to within a millionth of a mm."""
  if len(expanded.children) != len(original.children):
    return False
  a, b = expanded.attributes, original.attributes
  if a.HasField("location") != b.HasField("location"):
    return False
  if a.HasField("location"):
    for axis in "xyz":
      if abs(getattr(a.location, axis) - getattr(b.location, axis)) > _TOLERANCE:
        return False
  shallow_a, shallow_b = pb.ResourceDefinition(), pb.ResourceDefinition()
  for shallow, full in ((shallow_a, expanded), (shallow_b, original)):
    shallow.attributes.CopyFrom(full.attributes)
    shallow.attributes.ClearField("location")
    if full.HasField("initial_state"):
      shallow.initial_state.CopyFrom(full.initial_state)
  if shallow_a != shallow_b:
    return False
  return all(same_definitions(x, y) for x, y in zip(expanded.children, original.children))


def _as_grid(definition: pb.ResourceDefinition) -> bool:
  """Give a definition's items as a grid, in place, when they are one. Whether it did."""
  attributes = definition.attributes
  if not attributes.HasField("itemized") or attributes.itemized.HasField("grid"):
    return False
  spec = attributes.itemized
  nx, ny = spec.num_items_x, spec.num_items_y
  children = list(definition.children[: nx * ny])
  others = list(definition.children[nx * ny :])
  if nx < 1 or ny < 1 or len(children) != nx * ny:
    return False
  if any(other.attributes.identifier for other in others):
    return False
  if not all(child.attributes.HasField("location") for child in children):
    return False
  first = children[0].attributes
  item_dx = round(children[ny].attributes.location.x - first.location.x, _DECIMALS) if nx > 1 else 0
  item_dy = round(first.location.y - children[1].attributes.location.y, _DECIMALS) if ny > 1 else 0
  offset = pb.Vec3(
    x=round(first.location.x, _DECIMALS),
    y=round(first.location.y - (ny - 1) * item_dy, _DECIMALS),
    z=round(first.location.z, _DECIMALS),
  )
  item = pb.ResourceDefinition()
  item.CopyFrom(children[0])
  item.attributes.ClearField("location")
  item.attributes.identifier = ""
  _replace_in_names(item, first.identifier, "{identifier}")
  if first.identifier == "" or "{identifier}" not in item.attributes.name:
    return False

  compact = pb.ResourceDefinition()
  compact.CopyFrom(definition)
  del compact.children[:]
  compact.children.extend(others)
  compact.attributes.itemized.grid.CopyFrom(
    pb.ItemGrid(item=item, offset=offset, item_dx=item_dx, item_dy=item_dy)
  )
  expanded, original = pb.ResourceDefinition(), pb.ResourceDefinition()
  expanded.CopyFrom(compact)
  expand_grids(expanded)
  original.CopyFrom(definition)
  expand_grids(original)
  if not same_definitions(expanded, original):
    return False
  definition.CopyFrom(compact)
  return True


def compact_grids(definition: pb.ResourceDefinition) -> None:
  """Give every uniform grid of items in a definition as an `ItemGrid`, in place. Items that are
  not alike, or not evenly spaced, stay listed."""
  for child in definition.children:
    compact_grids(child)
  _as_grid(definition)
