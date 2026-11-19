"""What the iSWAP motion planner checks: every part of the arm, and what else rides the X arm, as
boxes on the deck. Pure arithmetic: nothing is read or moved."""

import math
from typing import Dict, List, Tuple

from pylabrobot.hamilton.star.resource_model.iswap import (
  ELBOW_DRIVE_COLUMN_ABOVE_REPORTED_Z,
  GRIPPER_BODY_SIZE,
  GRIPPER_FINGER_LOCATION,
  GRIPPER_FINGER_SIZE,
  GRIPPER_JOINT,
  GRIPPER_PAD_LOCATION,
  GRIPPER_PAD_SIZE,
  LINK_1_BODY_SIZE,
  LINK_1_JOINT,
)

Box = Tuple[float, float, float, float, float, float, float]
"""An oriented box on the deck: centre x, centre y, half its length along its angle, half its width
across it, the angle in radians counter-clockwise from +x, its bottom z and its top z. In mm."""


def compute_box_along(
  x: float,
  y: float,
  angle: float,
  start: float,
  end: float,
  half_width: float,
  z_low: float,
  z_high: float,
  across: float = 0.0,
) -> Box:
  """A box lying along a direction from a point.

  Args:
    x: the point's x, in mm.
    y: the point's y, in mm.
    angle: the direction, in radians counter-clockwise from +x.
    start: where the box starts along the direction, from the point, in mm.
    end: where it ends, in mm.
    half_width: half its width across the direction, in mm.
    z_low: its bottom, in mm.
    z_high: its top, in mm.
    across: how far its centre line sits to the left of the direction, in mm.
  """
  middle = (start + end) / 2
  cos, sin = math.cos(angle), math.sin(angle)
  return (
    x + cos * middle - sin * across,
    y + sin * middle + cos * across,
    (end - start) / 2,
    half_width,
    angle,
    z_low,
    z_high,
  )


def compute_box_corners(box: Box) -> List[Tuple[float, float]]:
  """A box's four corners seen from above, in mm."""
  x, y, half_length, half_width, angle = box[:5]
  cos, sin = math.cos(angle), math.sin(angle)
  return [
    (
      x + cos * half_length * a - sin * half_width * b,
      y + sin * half_length * a + cos * half_width * b,
    )
    for a, b in ((1, 1), (1, -1), (-1, -1), (-1, 1))
  ]


def compute_boxes_overlap(a: Box, b: Box, clearance: float) -> bool:
  """Whether two boxes come closer than `clearance`, in any direction.

  Z first, as an interval; then the separating axis theorem seen from above, on both boxes' axes.

  Args:
    a: one box.
    b: the other.
    clearance: how far apart they must stay, in mm.
  """
  if a[6] + clearance <= b[5] or b[6] + clearance <= a[5]:
    return False
  corners_a, corners_b = compute_box_corners(a), compute_box_corners(b)
  for angle in (a[4], a[4] + math.pi / 2, b[4], b[4] + math.pi / 2):
    cos, sin = math.cos(angle), math.sin(angle)
    along_a = [cx * cos + cy * sin for cx, cy in corners_a]
    along_b = [cx * cos + cy * sin for cx, cy in corners_b]
    if max(along_a) + clearance <= min(along_b) or max(along_b) + clearance <= min(along_a):
      return False
  return True


def compute_iswap_part_boxes(
  elbow_x: float,
  elbow_y: float,
  elbow_z: float,
  link_1_deck_angle: float,
  gripper_deck_angle: float,
  link_1_length: float,
  jaw_width: float,
  elbow_drive_diameter: float,
  elbow_drive_size_z: float,
) -> Dict[str, Box]:
  """Every part of the iSWAP as a box, at a joint state.

  Sizes and heights are the resource model's (`resource_model.iswap`). Every height is from the
  elbow's bottom, the Z its drive reports.

  Args:
    elbow_x: the elbow's x, in mm.
    elbow_y: the elbow's y, in mm.
    elbow_z: the elbow's bottom, in mm.
    link_1_deck_angle: which way link 1 lies on the deck, in degrees.
    gripper_deck_angle: which way the gripper faces on the deck, in degrees.
    link_1_length: elbow joint to wrist joint, in mm.
    jaw_width: how far apart the fingers' facing surfaces stand, in mm.
    elbow_drive_diameter: how wide the elbow drive is, in mm.
    elbow_drive_size_z: how tall to take the elbow drive, in mm.

  Returns:
    Each part's box, by the part's name.
  """
  a1, a2 = math.radians(link_1_deck_angle), math.radians(gripper_deck_angle)
  wrist_x = elbow_x + link_1_length * math.cos(a1)
  wrist_y = elbow_y + link_1_length * math.sin(a1)
  # Heights, stacked as the model stacks them: the column on top, link 1 under it, the gripper
  # body under link 1, the fingers on the body and the pads below the fingers.
  link_top = elbow_z + ELBOW_DRIVE_COLUMN_ABOVE_REPORTED_Z
  link_bottom = link_top - LINK_1_BODY_SIZE[2]
  body_bottom = link_bottom - GRIPPER_BODY_SIZE[2]
  finger_bottom = body_bottom + GRIPPER_FINGER_LOCATION.z
  pad_bottom = finger_bottom + GRIPPER_PAD_LOCATION.z
  radius = elbow_drive_diameter / 2
  boxes = {
    "elbow drive": (elbow_x, elbow_y, radius, radius, 0.0, link_top, link_top + elbow_drive_size_z),
    "link 1": compute_box_along(
      elbow_x,
      elbow_y,
      a1,
      -LINK_1_JOINT.x,
      LINK_1_BODY_SIZE[0] - LINK_1_JOINT.x,
      LINK_1_BODY_SIZE[1] / 2,
      link_bottom,
      link_top,
    ),
    "gripper body": compute_box_along(
      wrist_x,
      wrist_y,
      a2,
      -GRIPPER_JOINT.x,
      GRIPPER_BODY_SIZE[0] - GRIPPER_JOINT.x,
      GRIPPER_BODY_SIZE[1] / 2,
      body_bottom,
      link_bottom,
    ),
  }
  finger_start = GRIPPER_FINGER_LOCATION.x - GRIPPER_JOINT.x
  pad_start = finger_start + GRIPPER_PAD_LOCATION.x
  for side, sign in (("left", 1.0), ("right", -1.0)):
    # A finger's facing surface stands half the jaw width from the grip centre line.
    boxes[f"{side} finger"] = compute_box_along(
      wrist_x,
      wrist_y,
      a2,
      finger_start,
      finger_start + GRIPPER_FINGER_SIZE[0],
      GRIPPER_FINGER_SIZE[1] / 2,
      finger_bottom,
      finger_bottom + GRIPPER_FINGER_SIZE[2],
      across=sign * (jaw_width / 2 + GRIPPER_FINGER_SIZE[1] / 2),
    )
    boxes[f"{side} pad"] = compute_box_along(
      wrist_x,
      wrist_y,
      a2,
      pad_start,
      pad_start + GRIPPER_PAD_SIZE[0],
      GRIPPER_PAD_SIZE[1] / 2,
      pad_bottom,
      pad_bottom + GRIPPER_PAD_SIZE[2],
      across=sign * (jaw_width / 2 + GRIPPER_PAD_LOCATION.y + GRIPPER_PAD_SIZE[1] / 2),
    )
  return boxes
