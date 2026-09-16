"""CO-RE gripper tools for Hamilton channels."""

from __future__ import annotations

from typing import Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.hamilton.tip_creators import (
  HamiltonToolDefinition,
  TipPickupMethod,
  TipSize,
)

# The volume the firmware's tip type table carries for a grip tool. A grip tool holds no liquid, but
# the table demands at least 1.0 uL, and the value plays no part in picking the tool up.
GRIP_TOOL_VOLUME = 1.0


class HamiltonCoreGripperTool(HeadTool):
  """A CO-RE grip tool: a paddle a channel picks up, so two channels can grip a plate.

  The working point is the grip line, the axis through the pins that press against the plate, so
  `total_length` runs from the top of the collar to that line rather than to the paddle's lowest
  edge.

  The machine is told about a grip tool through the same tip type table as a tip, so the tool
  states its own entry.
  """

  def __init__(
    self,
    name: Optional[str],
    size_x: float,
    size_y: float,
    size_z: float,
    total_length: float,
    fitting_depth: float,
    collar_height: Optional[float] = None,
    category: str = "core_gripper_tool",
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
  ):
    """Initialize a CO-RE gripper tool.

    Args:
      total_length: distance from the top of the collar to the grip line, in mm.
    """

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      fitting_depth=fitting_depth,
      collar_height=collar_height,
      category=category,
      model=model,
      pick_up_location=pick_up_location,
    )
    self._total_length = total_length

  @property
  def total_length(self) -> float:
    return self._total_length

  def definition(self) -> Tuple[object, ...]:
    return (self.total_length, self.fitting_depth, self._collar_height)

  def hamilton_tool_definition(self) -> HamiltonToolDefinition:
    return HamiltonToolDefinition(
      has_filter=False,
      tip_length=self.extension,
      maximal_volume=GRIP_TOOL_VOLUME,
      tip_size=TipSize.UNDEFINED,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "total_length": self.total_length,
    }


def hamilton_core_gripper_tool(name: Optional[str] = None) -> HamiltonCoreGripperTool:
  """Hamilton CO-RE grip tool, for 1000 uL channels.

  Hamilton cat. no.: 186100 (firmware tip type 14)

  The firmware's tip table gives this tool a length of 30 mm: the distance from the collar's top
  rim to the grip line through the centres of the two pins. The paddle itself is 32 mm tall, its
  lowest edge 2 mm below the grip line, and 36 mm wide; lying along x, with its pins pointing +y,
  it is 36 x 8.346 x 32 mm.

  The collar is the standard 8 mm CO-RE collar (outer diameter 8.2 mm), and its bore steps in
  8.1 mm below the rim, which is the 8 mm fitting depth of a 300 uL or 1000 uL tip. The collar is
  not centred on the paddle's envelope in y - the pins stand out on one side - so its orifice is
  at y 4.25 rather than at half the depth.
  """
  return HamiltonCoreGripperTool(
    name=name,
    size_x=36.0,
    size_y=8.346,
    size_z=32.0,
    total_length=30.0,
    fitting_depth=8.0,
    collar_height=8.0,
    model=hamilton_core_gripper_tool.__name__,
    pick_up_location=Coordinate(x=18.0, y=4.25, z=32.0),
  )
