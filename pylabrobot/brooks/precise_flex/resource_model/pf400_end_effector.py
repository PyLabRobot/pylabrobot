"""The PreciseFlex 400's end-effector: the gripper its wrist joint turns.

The gripper is a cuboid in its own frame, located by its left front bottom corner, in mm, lying
along its x from the wrist joint to the point it grips at. Its drive moves two jaws, and a finger
is bolted to each. Sizes are measured off the manufacturer's model; how far it reaches and how far
its fingers open are what the controller reports.
"""

from typing import Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.resource import Resource

# Each body by the gripper Brooks fits it to: the IntelliGuide s23 servo gripper, and the v23 with
# cameras.
GRIPPER_BODY_SIZE = {"s23": (115.5, 134.0, 36.5), "v23": (127.0, 134.0, 55.4)}
# The wrist joint within the gripper: centred across it, in the flange plane just above its body.
GRIPPER_JOINT = {"s23": Coordinate(35.0, 67.0, 37.5), "v23": Coordinate(35.0, 67.0, 56.4)}

GRIPPER_JAW_SIZE = (22.0, 43.2, 37.2)
# Where a jaw starts along the gripper and above its underside. Across it is wherever the drive is.
# Its box is the thicker jaw's; the thinner one ends at the same front and bottom.
GRIPPER_JAW_LOCATION = {"s23": Coordinate(91.6, 0.0, -3.2), "v23": Coordinate(91.6, 0.0, 15.7)}

# Each gripper's own fingers: the s23's, and the v23's plate fingers.
GRIPPER_FINGER_SIZE = {"s23": (107.8, 11.5, 22.6), "v23": (120.3, 11.5, 33.1)}
# Where the first finger is bolted on its jaw: its rear set into the jaw's outer end, from below.
GRIPPER_FINGER_LOCATION = {"s23": Coordinate(2.0, 32.2, -16.6), "v23": Coordinate(2.0, 32.2, -27.1)}

# How far below the flange plane the fingers grip: at the middle of their height.
GRIPPER_TOOL_CENTER_POINT_Z = {"s23": -46.0, "v23": -51.25}


def gripper(
  name: str,
  tool_length: float,
  jaw_range: Tuple[float, float],
  *,
  intelliguide: str,
) -> MechanicalGripper:
  """The gripper: the wrist joint to the centre the fingers hold a plate at.

  Args:
    name: what to call this one.
    tool_length: the wrist joint to the grip centre, in mm, as the controller reports it.
    jaw_range: how far apart the fingers stand, closed and open, in mm.
    intelliguide: which gripper Brooks fitted: "s23", or "v23" with cameras.

  Returns:
    The gripper.
  """
  model = "brooks_pf400_gripper"
  size = GRIPPER_BODY_SIZE[intelliguide]
  # The two sides are mirror images, so each has its own mesh; each gripper has its own fingers.
  # Left is the gripper's +y side.
  jaws, fingers = (
    tuple(
      Resource(
        name=f"{name}_{part}_{side}",
        size_x=size[0],
        size_y=size[1],
        size_z=size[2],
        category=part,
        model=f"{model}_{part}{kind}_{side}",
      )
      for side in ("left", "right")
    )
    for part, size, kind in (
      ("jaw", GRIPPER_JAW_SIZE, ""),
      ("finger", GRIPPER_FINGER_SIZE[intelliguide], f"_{intelliguide}"),
    )
  )
  return MechanicalGripper(
    name=name,
    proximal_joint=GRIPPER_JOINT[intelliguide],
    tool_center_point=Coordinate(tool_length, 0.0, GRIPPER_TOOL_CENTER_POINT_Z[intelliguide]),
    body=Resource(
      name=f"{name}_body",
      size_x=size[0],
      size_y=size[1],
      size_z=size[2],
      category="body",
      model=f"{model}_body_{intelliguide}",
    ),
    body_location=Coordinate.zero(),
    fingers=fingers,
    finger_location=GRIPPER_FINGER_LOCATION[intelliguide],
    jaws=jaws,
    jaw_location=GRIPPER_JAW_LOCATION[intelliguide],
    jaw_range=jaw_range,
    model=model,
  )
