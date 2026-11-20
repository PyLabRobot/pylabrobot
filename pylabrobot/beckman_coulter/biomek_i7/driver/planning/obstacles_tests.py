import unittest

from pylabrobot.beckman_coulter.biomek_i7.driver.planning.obstacles import Obstacles, obstacle_part


class WithoutTests(unittest.TestCase):
  def setUp(self) -> None:
    self.structure = (obstacle_part("Shield", (150.0, None), (None, None), (None, None)),)
    self.deck = (
      obstacle_part("P1", (0.0, 12.8), (0.0, 8.6), (-1.0, 17.0)),
      obstacle_part("P1 wall", (0.0, 0.2), (0.0, 8.6), (None, 20.0)),
      obstacle_part("P2", (14.0, 26.8), (0.0, 8.6), (-1.0, 17.0)),
    )
    self.obstacles = Obstacles(self.structure, self.deck, ("P1", "P2"))

  def test_a_step_leaves_out_only_its_holders_box(self) -> None:
    # A step's own moves leave the position they work in out, whole; its own obstacles (a trash's
    # walls) and the instrument's structure stay.
    out = self.obstacles.without(["P1"])
    self.assertEqual([p.name for p in out.deck], ["P1 wall", "P2"])
    self.assertEqual(out.structure, self.structure)
    self.assertEqual(out.holders, ("P2",))

  def test_a_name_that_is_no_holder_leaves_nothing_out(self) -> None:
    self.assertIs(self.obstacles.without(["P1 wall", "nothing"]), self.obstacles)
    self.assertIs(self.obstacles.without([]), self.obstacles)


if __name__ == "__main__":
  unittest.main()
