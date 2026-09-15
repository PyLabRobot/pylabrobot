from __future__ import annotations

from typing import Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.head_tool import HeadTool


class CoreGripperTool(HeadTool):
  """A CO-RE gripper tool: a paddle a channel picks up, so that two channels can grip a plate.

  The working point is the grip line, the axis through the pins that press against the plate, so
  `total_length` runs from the top of the collar to that line rather than to the paddle's lowest
  edge.
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

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "total_length": self.total_length,
    }
