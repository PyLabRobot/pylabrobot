"""The CO-RE grippers a Hamilton STAR parks on its waste block."""

import warnings

# TODO: add new quad-core gripper definitions when they are released by Hamilton.
from typing import List

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.core_gripper_tools import (
  HamiltonCoreGripperTool,
  hamilton_core_gripper_tool,
)
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.resource import Resource


def _tool_name(holder_name: str, side: str) -> str:
  """What a tool parked in the holder called `holder_name` is called.

  A tool is not a holder: it sits in one. So it takes the holder's prefix - the device that owns
  them both - rather than the holder's own name.
  """
  prefix = holder_name
  for suffix in ("core_gripper_holder", "core_grippers"):
    if prefix.endswith(suffix):
      prefix = prefix[: -len(suffix)]
      break
  return f"{prefix}core_gripper_tool_{side}"


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

  @property
  def front_tool(self) -> HamiltonCoreGripperTool:
    """The front tool parked in this holder."""
    tool = self.get_resource(_tool_name(self.name, "front"))
    assert isinstance(tool, HamiltonCoreGripperTool)
    return tool

  @property
  def back_tool(self) -> HamiltonCoreGripperTool:
    """The back tool parked in this holder."""
    tool = self.get_resource(_tool_name(self.name, "back"))
    assert isinstance(tool, HamiltonCoreGripperTool)
    return tool

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


def prep_core_gripper_holder(name: str = "core_gripper_holder") -> HamiltonCoreGrippers:
  """The holder a PREP parks its CO-RE grip tools in, measured off the block it stands on.

  front_channel_y_center / back_channel_y_center are named for the PREP command
  (front_channel_position_y, rear_channel_position_y) so the correct paddle is used.
  """
  size_x, size_y, size_z = 23.5, 42.5, 18.0
  holder = HamiltonCoreGrippers(
    name=name,
    # the two tools stand 18 mm apart, centred on the holder
    back_channel_y_center=size_y / 2 + 9.0,
    front_channel_y_center=size_y / 2 - 9.0,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    model="prep_core_gripper_holder",
  )

  # A tool standing here has its top 30 mm above the holder's flat, measured. The flat is 3 mm up
  # from the holder's base; the rail between the two tools stands higher.
  flat_z = 3.0
  tool_top = flat_z + 30.0
  front = hamilton_core_gripper_tool(name=_tool_name(name, "front"))
  pick_up = front.pick_up_location or front.get_anchor("c", "c", "t")
  holder.assign_child_resource(
    front,
    location=Coordinate(
      x=size_x / 2 - pick_up.x,
      y=holder.front_channel_y_center - pick_up.y,
      z=tool_top - pick_up.z,
    ),
  )
  back = hamilton_core_gripper_tool(name=_tool_name(name, "back"))
  back.rotate(z=180)
  holder.assign_child_resource(
    back,
    location=Coordinate(
      x=size_x / 2 + pick_up.x,
      y=holder.back_channel_y_center + pick_up.y,
      z=tool_top - pick_up.z,
    ),
  )
  return holder


def prep_core_gripper_mount() -> HamiltonCoreGrippers:
  """Deprecated alias for `prep_core_gripper_holder`."""
  warnings.warn(
    "prep_core_gripper_mount is deprecated. Use 'prep_core_gripper_holder' instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return prep_core_gripper_holder(name="core_grippers")


def hamilton_core_gripper_1000ul_at_waste(
  name: str = "core_gripper_holder",
) -> HamiltonCoreGrippers:
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
    hamilton_core_gripper_tool(name=_tool_name(name, "front")),
    location=Coordinate(x=-18.0, y=5.25, z=-2.0),
  )
  mount.assign_child_resource(
    hamilton_core_gripper_tool(name=_tool_name(name, "back")),
    location=Coordinate(x=-18.0, y=31.25, z=-2.0),
  )
  return mount


def hamilton_core_gripper_1000ul_5ml_on_waste(
  name: str = "core_gripper_holder",
) -> HamiltonCoreGrippers:
  # distance from base of rack to outer base of containers: 0mm
  # inner hole diameter is 8.6mm
  # left outer edge of rack is 19.5mm
  # front outer edge of rack is 39.5mm

  grippers = HamiltonCoreGrippers(
    name=name,
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
  front = hamilton_core_gripper_tool(name=_tool_name(name, "front"))
  pick_up = front.pick_up_location or front.get_anchor("c", "c", "t")
  grippers.assign_child_resource(
    front,
    location=Coordinate(
      x=grippers.get_size_x() / 2 - pick_up.x,
      y=grippers.front_channel_y_center - pick_up.y,
      z=tool_top - pick_up.z,
    ),
  )
  # Turned about its own origin, so its origin lands on the far corner.
  back = hamilton_core_gripper_tool(name=_tool_name(name, "back"))
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
