"""The CO-RE gripper tools and mounts for a Hamilton STAR."""

from __future__ import annotations

from typing import Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.hamilton_tool import HamiltonTool
from pylabrobot.resources.hamilton.tip_creators import TipPickupMethod, TipSize

# TODO: add new quad-core gripper definitions when they are released by Hamilton.


class HamiltonCoreGripperTool(HamiltonTool):
  """A CO-RE grip tool, picked up by a pair of channels to grip a plate."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    grip_line_height: float,
    fitting_depth: float,
    collar_height: Optional[float] = None,
    category: str = "core_gripper_tool",
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
  ):
    """Initialize a CO-RE gripper tool.

    Args:
      grip_line_height: height of the axis through the gripping pins above the tool's bottom, in mm.
    """

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      fitting_depth=fitting_depth,
      category=category,
      model=model,
      pick_up_location=pick_up_location,
    )
    self.grip_line_height = grip_line_height
    self.collar_height = collar_height
    self.tip_size = TipSize.UNDEFINED
    self.pickup_method = TipPickupMethod.OUT_OF_RACK

  def __eq__(self, other: object) -> bool:
    """Compare resource geometry, grip line height, and collar height."""
    return (
      isinstance(other, HamiltonCoreGripperTool)
      and super().__eq__(other)
      and self.grip_line_height == other.grip_line_height
      and self.collar_height == other.collar_height
    )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "grip_line_height": self.grip_line_height,
      "collar_height": self.collar_height,
    }


def hamilton_core_gripper_tool(name: str) -> HamiltonCoreGripperTool:
  """Hamilton CO-RE grip tool, for 1000 uL channels.

  Hamilton cat. no.: 186100 (firmware tip type 14)

  36 x 8.346 x 32 mm, lying along x with its pins pointing +y. The grip line is 2 mm above
  the tool's bottom. The collar is 10 mm tall; its opening is off centre in y, at 4.25.
  """
  return HamiltonCoreGripperTool(
    name=name,
    size_x=36.0,
    size_y=8.346,
    size_z=32.0,
    grip_line_height=2.0,
    fitting_depth=8.0,
    collar_height=10.0,
    model=hamilton_core_gripper_tool.__name__,
    pick_up_location=Coordinate(x=18.0, y=4.25, z=32.0),
  )
