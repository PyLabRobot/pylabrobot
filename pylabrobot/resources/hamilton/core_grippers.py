"""The CO-RE grippers a Hamilton STAR parks on its waste block."""

# TODO: add new quad-core gripper definitions when they are released by Hamilton.

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.core_gripper_tools import (
  HamiltonCoreGripperTool,
  hamilton_core_gripper_tool,
)
from pylabrobot.resources.resource import Resource


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


def hamilton_core_gripper_1000ul_at_waste() -> HamiltonCoreGrippers:
  # inner hole diameter is 8.6mm
  # distance from base of rack to outer base of containers: -7mm
  # left outer edge of rack is 22.5mm
  # front outer edge of rack is 9.5mm

  mount = HamiltonCoreGrippers(
    name="core_grippers",
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


def hamilton_core_gripper_1000ul_5ml_on_waste() -> HamiltonCoreGrippers:
  # distance from base of rack to outer base of containers: 0mm
  # inner hole diameter is 8.6mm
  # left outer edge of rack is 19.5mm
  # front outer edge of rack is 39.5mm

  mount = HamiltonCoreGrippers(
    name="core_grippers",
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
