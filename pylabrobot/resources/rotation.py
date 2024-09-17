import math
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.utils.linalg import matrix_multiply_3x3
import unittest
from pylabrobot.resources.rotation import Rotation


class Rotation:
  """ Represents a 3D rotation. """

  def __init__(self, x: float = 0, y: float = 0, z: float = 0):
    self.x = x  # around x-axis, roll
    self.y = y  # around y-axis, pitch
    self.z = z  # around z-axis, yaw

  def get_rotation_matrix(self):
    # Create rotation matrices for each axis
    Rz = ([
      [math.cos(math.radians(self.z)), -math.sin(math.radians(self.z)), 0],
      [math.sin(math.radians(self.z)), math.cos(math.radians(self.z)), 0],
      [0, 0, 1]
    ])
    Ry = ([
      [math.cos(math.radians(self.y)), 0, math.sin(math.radians(self.y))],
      [0, 1, 0],
      [-math.sin(math.radians(self.y)), 0, math.cos(math.radians(self.y))]
    ])
    Rx = ([
      [1, 0, 0],
      [0, math.cos(math.radians(self.x)), -math.sin(math.radians(self.x))],
      [0, math.sin(math.radians(self.x)), math.cos(math.radians(self.x))]
    ])
    # Combine rotations: The order of multiplication matters and defines the behavior significantly.
    # This is a common order: Rz * Ry * Rx
    return matrix_multiply_3x3(matrix_multiply_3x3(Rz, Ry), Rx)

  def get_rotation_matrix_around_origin(self, origin: Coordinate):
    rotation_matrix = self.get_rotation_matrix()

    return [
      [
        rotation_matrix[0][0],
        rotation_matrix[0][1],
        rotation_matrix[0][2],
        origin.x * (1 - rotation_matrix[0][0]) -
        origin.y * rotation_matrix[0][1] -
        origin.z * rotation_matrix[0][2]
      ], # transforms & translates x axis
      [
        rotation_matrix[1][0],
        rotation_matrix[1][1],
        rotation_matrix[1][2],
        origin.y * (1 - rotation_matrix[1][1]) -
        origin.x * rotation_matrix[1][0] -
        origin.z * rotation_matrix[1][2]
      ], # transforms & translates y axis
      [
        rotation_matrix[2][0],
        rotation_matrix[2][1],
        rotation_matrix[2][2],
        origin.z * (1 - rotation_matrix[2][2]) -
        origin.x * rotation_matrix[2][0] -
        origin.y * rotation_matrix[2][1]
      ], # transforms & translates z axis
      [0, 0, 0, 1]  # affine transformation for 3D coordinates
    ]

  def __str__(self) -> str:
    return f"Rotation(x={self.x}, y={self.y}, z={self.z})"

  def __add__(self, other) -> "Rotation":
    return Rotation(x=self.x + other.x, y=self.y + other.y, z=self.z + other.z)

  @staticmethod
  def deserialize(data) -> "Rotation":
    return Rotation(data["x"], data["y"], data["z"])




class TestRotation(unittest.TestCase):

  def test_get_rotation_matrix_around_origin(self):
    rotation = Rotation(x=90, y=0, z=0)
    origin = Coordinate(x=1, y=1, z=1)

    expected_matrix = [
      [1, 0, 0, 0],
      [0, 0, -1, 2],
      [0, 1, 0, 0],
      [0, 0, 0, 1]
    ]

    rotation_matrix = rotation.get_rotation_matrix_around_origin(origin)

    for i in range(4):
      for j in range(4):
        self.assertAlmostEqual(rotation_matrix[i][j], expected_matrix[i][j], places=6)

if __name__ == '__main__':
  unittest.main()