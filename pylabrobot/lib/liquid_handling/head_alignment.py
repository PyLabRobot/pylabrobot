"""Align a fixed-grid head with a grid of the same pitch: which item is under which channel.

A head channel and the item of its name share a row and a column, so a tip rack or a plate of
the head's size is the head's own grid. One channel placed over one item fixes the alignment.
"""

from typing import List, Optional, TypeVar

from pylabrobot.resources import Coordinate, ItemizedResource, Resource

T = TypeVar("T", bound=Resource)


def get_items_under_channels(
  grid: ItemizedResource[T], channel: str, item: str
) -> List[Optional[T]]:
  """The item under each head channel with `channel` over `item`, in channel order.

  Args:
    grid: a tip rack or a plate with the head's rows and columns.
    channel: the head channel, e.g. A1.
    item: the item it is placed over, e.g. A10.

  Returns:
    One entry per channel; None for a channel past the grid's edge.
  """
  over, under = grid.get_item(item), grid.get_item(channel)
  row_shift = grid.get_child_row(over) - grid.get_child_row(under)
  column_shift = grid.get_child_column(over) - grid.get_child_column(under)
  items: List[Optional[T]] = []
  for named in grid.get_all_items():
    row = grid.get_child_row(named) + row_shift
    column = grid.get_child_column(named) + column_shift
    inside = 0 <= row < grid.num_items_y and 0 <= column < grid.num_items_x
    items.append(grid.get_item((row, column)) if inside else None)
  return items


def get_shift_with_channel_over(
  grid: ItemizedResource[T], channel: str, item: str, deck: Resource
) -> Coordinate:
  """How far head channel A1 stands from item A1 with `channel` over `item`, in deck mm.

  Args:
    grid: a tip rack or a plate with the head's rows and columns.
    channel: the head channel, e.g. A1.
    item: the item it is placed over, e.g. A10.
    deck: the deck the grid stands on.
  """
  over, under = grid.get_item(item), grid.get_item(channel)
  return over.get_location_wrt(deck) - under.get_location_wrt(deck)
