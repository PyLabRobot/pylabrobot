"""What holds a head tool: a spot in a rack, or the shaft of a channel."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.resource import Resource


@runtime_checkable
class TipHolder(Protocol):
  """Something that carries a head tool by its collar.

  A tip spot and a channel's mounting shaft hold a tool the same way and differ only in how deep
  they grip it, so both state where the tool they carry sits.
  """

  def tip_location(self, tool: HeadTool) -> Coordinate:
    """Where the tool sits in this holder, relative to the holder's own corner."""
    ...


def collar_seat(holder: Resource, tool: HeadTool, seat_depth: float) -> Coordinate:
  """Where a holder gripping `seat_depth` of a tool's collar carries it.

  The tool hangs on its own axis, centred on the holder, with the top of its collar `seat_depth`
  above the holder's reference plane - so a spot grips a tip by the height of its collar, which
  comes to rest on the rim of the hole, and a channel grips it by how far it reaches inside.

  Args:
    holder: the spot or shaft carrying the tool.
    tool: the tool it carries.
    seat_depth: how much of the tool's collar the holder grips, in mm.
  """
  return Coordinate(
    x=(holder.get_size_x() - tool.get_size_x()) / 2,
    y=(holder.get_size_y() - tool.get_size_y()) / 2,
    z=seat_depth - tool.total_length,
  )
