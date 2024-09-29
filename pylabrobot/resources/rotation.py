import math

from pylabrobot.utils.linalg import matrix_multiply_3x3


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

  def __str__(self) -> str:
    return f"Rotation(x={self.x}, y={self.y}, z={self.z})"

  def __add__(self, other) -> "Rotation":
    return Rotation(x=self.x + other.x, y=self.y + other.y, z=self.z + other.z)

  @staticmethod
  def deserialize(data) -> "Rotation":
    return Rotation(data["x"], data["y"], data["z"])

  def __repr__(self) -> str:
    return self.__str__()
