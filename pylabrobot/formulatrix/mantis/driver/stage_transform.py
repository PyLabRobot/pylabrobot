"""The stage transform: where a point on the wellplate is in the arm frame. Pure maths.

The wellplate frame's origin is the wellplate's back-left corner, the plate holder's datum, with
x to the right, y to the front and z down from the unit's reference height above the wellplate's
base, in mm. A point is turned and moved by the arm type, moved by the stage origin, its x and y mapped by
the projective transformation matrix and its z tilted; never through a fitted homography.
"""

import math
from typing import Sequence, Tuple

from .errors import MantisKinematicsError


def compute_arm_frame_position(
  *,
  position: Tuple[float, float, float],
  origin: Tuple[float, float, float],
  transformation_matrix: Sequence[Sequence[float]],
  offset_x: Tuple[float, float, float],
  offset_y: Tuple[float, float, float],
  arm_type_rotation_degree: float,
  arm_type_translation: Tuple[float, float, float],
) -> Tuple[float, float, float]:
  """A point of the wellplate frame in the arm frame: x and y in mm, z in mm down from Z's flag.

  Args:
    position: (x, y, z) in the wellplate frame, mm.
    origin: the stage origin, the plate holder's (x, y, z), mm.
    transformation_matrix: the stage's matrix, 3 by 3, projective on x and y.
    offset_x: the stage's x offset; its z is the tilt per mm of x in the wellplate frame.
    offset_y: the stage's y offset; its z is the tilt per mm of y in the wellplate frame.
    arm_type_rotation_degree: the arm type's rotation, degrees, about the wellplate frame's origin.
    arm_type_translation: the arm type's translation, (x, y, z), mm.

  Raises:
    MantisKinematicsError: if the matrix maps the point to infinity.
  """
  x, y, z = position
  angle = math.radians(arm_type_rotation_degree)
  framed_x = x * math.cos(angle) - y * math.sin(angle) + arm_type_translation[0]
  framed_y = x * math.sin(angle) + y * math.cos(angle) + arm_type_translation[1]
  framed_z = z + arm_type_translation[2]
  stage_x, stage_y = framed_x + origin[0], framed_y + origin[1]
  mapped = [row[0] * stage_x + row[1] * stage_y + row[2] for row in transformation_matrix]
  if mapped[2] == 0:
    raise MantisKinematicsError(
      f"the stage transform maps ({x:.3f}, {y:.3f}) mm on the wellplate to infinity"
    )
  return (
    mapped[0] / mapped[2],
    mapped[1] / mapped[2],
    origin[2] + framed_z + framed_x * offset_x[2] + framed_y * offset_y[2],
  )
