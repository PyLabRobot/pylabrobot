"""The CO-RE gripper tools and mounts for a Hamilton STAR."""

from __future__ import annotations

from typing import Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.hamilton_tool import HamiltonTool
from pylabrobot.resources.hamilton.tip_creators import TipPickupMethod, TipSize
from pylabrobot.resources.resource import Resource

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


class HamiltonCoreGrippers(Resource):
  def __init__(
    self,
    name: str,
    back_channel_y_center: float,
    front_channel_y_center: float,
    size_x: float,
    size_y: float,
    size_z: float,
    model,
    rotation=None,
    category="core_grippers",
    barcode=None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
      barcode=barcode,
    )
    self.back_channel_y_center = back_channel_y_center
    self.front_channel_y_center = front_channel_y_center

  @property
  def front_tool(self) -> HamiltonCoreGripperTool:
    """The front tool stored on this mount."""
    tool = self.get_resource(f"{self.name}_front")
    assert isinstance(tool, HamiltonCoreGripperTool)
    return tool

  @property
  def back_tool(self) -> HamiltonCoreGripperTool:
    """The back tool stored on this mount."""
    tool = self.get_resource(f"{self.name}_back")
    assert isinstance(tool, HamiltonCoreGripperTool)
    return tool

  def serialize(self):
    return {
      **super().serialize(),
      "back_channel_y_center": self.back_channel_y_center,
      "front_channel_y_center": self.front_channel_y_center,
    }


def prep_core_gripper_mount() -> HamiltonCoreGrippers:
  """CORE gripper mount for PREP decks. Assign at Coordinate(290, 266.5, 62).

  Physical rear paddle at (290, 257.5, 62), front at (290, 275.5, 62).
  front_channel_y_center / back_channel_y_center are named for the PREP command
  (front_channel_position_y, rear_channel_position_y) so the correct paddle is used.
  """
  return HamiltonCoreGrippers(
    name="core_grippers",
    back_channel_y_center=9.0,
    front_channel_y_center=-9.0,
    size_x=20.0,
    size_y=20.0,
    size_z=24.0,
    model="prep_core_gripper_mount",
  )


def hamilton_core_gripper_1000ul_at_waste(name: str = "core_grippers") -> HamiltonCoreGrippers:
  # inner hole diameter is 8.6mm
  # distance from base of rack to outer base of containers: -7mm
  # left outer edge of rack is 22.5mm
  # front outer edge of rack is 9.5mm

  mount = HamiltonCoreGrippers(
    name=name,
    size_x=45,  # from venus
    size_y=45,  # from venus
    size_z=24,  # from venus
    back_channel_y_center=26 + 9.5,
    front_channel_y_center=0 + 9.5,
    model=hamilton_core_gripper_1000ul_at_waste.__name__,
  )
  mount.assign_child_resource(
    hamilton_core_gripper_tool(name=f"{mount.name}_front"),
    location=Coordinate(x=-18.0, y=5.25, z=-2.0),
  )
  mount.assign_child_resource(
    hamilton_core_gripper_tool(name=f"{mount.name}_back"),
    location=Coordinate(x=-18.0, y=31.25, z=-2.0),
  )
  return mount


def hamilton_core_gripper_1000ul_5ml_on_waste(name: str = "core_grippers") -> HamiltonCoreGrippers:
  # distance from base of rack to outer base of containers: 0mm
  # inner hole diameter is 8.6mm
  # left outer edge of rack is 19.5mm
  # front outer edge of rack is 39.5mm

  mount = HamiltonCoreGrippers(
    name=name,
    size_x=39,  # from venus
    size_y=61,  # from venus
    size_z=19.5,  # measured
    back_channel_y_center=18 + 21.5,
    front_channel_y_center=0 + 21.5,
    model=hamilton_core_gripper_1000ul_5ml_on_waste.__name__,
  )
  mount.assign_child_resource(
    hamilton_core_gripper_tool(name=f"{mount.name}_front"),
    location=Coordinate(x=-18.0, y=17.25, z=2.5),
  )
  mount.assign_child_resource(
    hamilton_core_gripper_tool(name=f"{mount.name}_back"),
    location=Coordinate(x=-18.0, y=35.25, z=2.5),
  )
  return mount
