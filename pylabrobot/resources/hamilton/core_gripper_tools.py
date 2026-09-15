"""CO-RE gripper tools for Hamilton channels."""

from typing import Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.core_gripper_tool import CoreGripperTool
from pylabrobot.resources.hamilton.tip_creators import (
  HamiltonToolDefinition,
  TipPickupMethod,
  TipSize,
)

# The volume the firmware's tip type table carries for a grip tool. A grip tool holds no liquid, but
# the table demands at least 1.0 uL, and the value plays no part in picking the tool up.
GRIP_TOOL_VOLUME = 1.0


class HamiltonCoreGripperTool(CoreGripperTool):
  """A CO-RE grip tool on a Hamilton channel.

  The machine is told about a grip tool through the same tip type table as a tip, so the tool
  states its own entry.
  """

  def hamilton_tool_definition(self) -> HamiltonToolDefinition:
    return HamiltonToolDefinition(
      has_filter=False,
      tip_length=self.extension,
      maximal_volume=GRIP_TOOL_VOLUME,
      tip_size=TipSize.UNDEFINED,
      pickup_method=TipPickupMethod.OUT_OF_RACK,
    )


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
