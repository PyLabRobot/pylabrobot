"""The CO-RE grippers a Hamilton STAR parks on its waste block."""

# TODO: add new quad-core gripper definitions when they are released by Hamilton.

from typing import List

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.core_gripper_tools import hamilton_core_gripper_tool
from pylabrobot.resources.head_tool import HeadTool
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

  def comparable_children(self) -> List[Resource]:
    """Everything but the tools parked here, which are state."""
    return [child for child in self.children if not isinstance(child, HeadTool)]

  def serialize(self):
    """Serialize the grippers. The tools parked here are state, not serialized children."""
    data = {
      **super().serialize(),
      "back_channel_y_center": self.back_channel_y_center,
      "front_channel_y_center": self.front_channel_y_center,
    }
    children = [child.serialize() for child in self.comparable_children()]
    if children:
      data["children"] = children
    else:
      data.pop("children", None)
    return data


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


def hamilton_core_gripper_1000ul_at_waste() -> HamiltonCoreGrippers:
  # inner hole diameter is 8.6mm
  # distance from base of rack to outer base of containers: -7mm
  # left outer edge of rack is 22.5mm
  # front outer edge of rack is 9.5mm

  return HamiltonCoreGrippers(
    name="core_grippers",
    size_x=45,  # from venus
    size_y=45,  # from venus
    size_z=24,  # from venus
    back_channel_y_center=26 + 9.5,
    front_channel_y_center=0 + 9.5,
    model=hamilton_core_gripper_1000ul_at_waste.__name__,
  )


def hamilton_core_gripper_1000ul_5ml_on_waste() -> HamiltonCoreGrippers:
  # distance from base of rack to outer base of containers: 0mm
  # inner hole diameter is 8.6mm
  # left outer edge of rack is 19.5mm
  # front outer edge of rack is 39.5mm

  grippers = HamiltonCoreGrippers(
    name="core_grippers",
    size_x=39,  # from venus
    size_y=61,  # from venus
    size_z=19.5,  # measured
    back_channel_y_center=18 + 21.5,
    front_channel_y_center=0 + 21.5,
    model=hamilton_core_gripper_1000ul_5ml_on_waste.__name__,
  )

  # The two tools stand parked in the holder, collars on the holder's centre x and on the channel
  # y centres, pins facing each other. Their tops are 34.5 mm above the holder's base: probed at
  # 235.0 with the base at 200.5.
  tool_top = 34.5
  front = hamilton_core_gripper_tool(name="core_grippers_tool_front")
  pick_up = front.pick_up_location
  grippers.assign_child_resource(
    front,
    location=Coordinate(
      x=grippers.get_size_x() / 2 - pick_up.x,
      y=grippers.front_channel_y_center - pick_up.y,
      z=tool_top - pick_up.z,
    ),
  )
  # Turned about its own origin, so its origin lands on the far corner.
  back = hamilton_core_gripper_tool(name="core_grippers_tool_back")
  back.rotate(z=180)
  grippers.assign_child_resource(
    back,
    location=Coordinate(
      x=grippers.get_size_x() / 2 + pick_up.x,
      y=grippers.back_channel_y_center + pick_up.y,
      z=tool_top - pick_up.z,
    ),
  )
  return grippers
