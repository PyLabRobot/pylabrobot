import unittest
from typing import Sequence, Tuple

from pylabrobot.brooks.precise_flex.resource_model import pf400_end_effector, pf400_manipulator
from pylabrobot.brooks.precise_flex.tests.device_tests import built

Outline = Sequence[Tuple[float, float]]

# Each outline, its part's cuboid, and how far the outline may stand outside that cuboid, in mm.
OUTLINES: Tuple[Tuple[str, Outline, Tuple[float, ...], float], ...] = (
  ("column", pf400_manipulator.Z_COLUMN_OUTLINE, pf400_manipulator.Z_COLUMN_SIZE_XY, 0.5),
  *(
    (
      f"body {intelliguide}",
      pf400_end_effector.GRIPPER_BODY_OUTLINE[intelliguide],
      pf400_end_effector.GRIPPER_BODY_SIZE[intelliguide],
      0.5,
    )
    for intelliguide in ("s23", "v23")
  ),
)


def area(outline: Outline) -> float:
  following = list(outline[1:]) + list(outline[:1])
  return sum(x_1 * y_2 - x_2 * y_1 for (x_1, y_1), (x_2, y_2) in zip(outline, following)) / 2


class TestOutlines(unittest.TestCase):
  """A part seen from above: within its cuboid, and convex, as the clearance needs it."""

  def test_an_outline_lies_about_its_parts_cuboid(self):
    for name, outline, size, outside in OUTLINES:
      with self.subTest(part=name):
        for x, y in outline:
          self.assertTrue(-outside <= x <= size[0] + outside, (x, y))
          self.assertTrue(-outside <= y <= size[1] + outside, (x, y))

  def test_an_outline_runs_counter_clockwise_and_never_turns_back(self):
    link_2 = built().driver.arm._get_link_2_outline()
    for name, outline in (
      *((name, outline) for name, outline, _, _ in OUTLINES),
      ("link 2", link_2),
    ):
      with self.subTest(part=name):
        self.assertGreater(area(outline), 0.0)
        count = len(outline)
        for index in range(count):
          (x_1, y_1), (x_2, y_2), (x_3, y_3) = (
            outline[(index + step) % count] for step in range(3)
          )
          self.assertGreaterEqual((x_2 - x_1) * (y_3 - y_2) - (y_2 - y_1) * (x_3 - x_2), 0.0)
