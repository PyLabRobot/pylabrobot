import unittest
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation

class TestRotation(unittest.TestCase):
  """ Tests for the Rotation class. """

  def test_get_rotation_matrix(self):
    rotation = Rotation(x=90, y=0, z=0)
    origin = Coordinate(x=1, y=1, z=1)

    expected_matrix = [
      [1, 0, 0],
      [0, 0, -1],
      [0, 1, 0]
    ]

    rotation_matrix = rotation.get_rotation_matrix(origin)

    for i in range(3):
      for j in range(3):
        self.assertAlmostEqual(rotation_matrix[i][j], expected_matrix[i][j], places=6)

if __name__ == "__main__":
  unittest.main()