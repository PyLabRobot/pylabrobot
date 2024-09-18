import math
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.utils.linalg import matrix_multiply_3x3

class Rotation:
  """ Represents a 3D rotation. """

  def __init__(self, x: float = 0, y: float = 0, z: float = 0):
    self.x = x 
    self.y = y
    self.z = z

  def get_rotation_and_translation(self, origin: Coordinate = Coordinate.zero()):
    # rotation matrices for each axis
    Rz = [
      [math.cos(math.radians(self.z)), -math.sin(math.radians(self.z)), 0],
      [math.sin(math.radians(self.z)), math.cos(math.radians(self.z)), 0],
      [0, 0, 1]
    ]
    Ry = [
      [math.cos(math.radians(self.y)), 0, math.sin(math.radians(self.y))],
      [0, 1, 0],
      [-math.sin(math.radians(self.y)), 0, math.cos(math.radians(self.y))]
    ]
    Rx = [
      [1, 0, 0],
      [0, math.cos(math.radians(self.x)), -math.sin(math.radians(self.x))],
      [0, math.sin(math.radians(self.x)), math.cos(math.radians(self.x))]
    ]
    # Combine rotations: The order of multiplication matters and defines the behavior significantly.
    # This is a common order: Rz * Ry * Rx
    rotation_matrix = matrix_multiply_3x3(matrix_multiply_3x3(Rz, Ry), Rx)

    origin_vector = origin.vector()
    rotated_origin = [
      sum(rotation_matrix[i][j] * origin_vector[j] for j in range(3)) for i in range(3)
    ]
    translation = [
      rotated_origin[0] - origin.x,
      rotated_origin[1] - origin.y,
      rotated_origin[2] - origin.z
    ]

    return rotation_matrix, translation

  def __str__(self) -> str:
    return f"Rotation(x={self.x}, y={self.y}, z={self.z})"

  def __add__(self, other) -> "Rotation":
    return Rotation(x=self.x + other.x, y=self.y + other.y, z=self.z + other.z)

  @staticmethod
  def deserialize(data) -> "Rotation":
    return Rotation(data["x"], data["y"], data["z"])