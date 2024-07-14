import math

from pylabrobot.utils.linalg import matrix_multiply_3x3


class Rotation:
  """ Represents a 3D rotation. """

  def __init__(self, xy: float = 0, xz: float = 0, yz: float = 0):
    self.xy = xy  # around z-axis, yaw
    self.xz = xz  # around y-axis, pitch
    self.yz = yz  # around x-axis, roll

  def get_rotation_matrix(self):
    # Create rotation matrices for each axis
    Rz = ([
      [math.cos(math.radians(self.xy)), -math.sin(math.radians(self.xy)), 0],
      [math.sin(math.radians(self.xy)), math.cos(math.radians(self.xy)), 0],
      [0, 0, 1]
    ])
    Ry = ([
      [math.cos(math.radians(self.xz)), 0, math.sin(math.radians(self.xz))],
      [0, 1, 0],
      [-math.sin(math.radians(self.xz)), 0, math.cos(math.radians(self.xz))]
    ])
    Rx = ([
      [1, 0, 0],
      [0, math.cos(math.radians(self.yz)), -math.sin(math.radians(self.yz))],
      [0, math.sin(math.radians(self.yz)), math.cos(math.radians(self.yz))]
    ])
    # Combine rotations: The order of multiplication matters and defines the behavior significantly.
    # This is a common order: Rz * Ry * Rx
    return matrix_multiply_3x3(matrix_multiply_3x3(Rz, Ry), Rx)

  def __str__(self) -> str:
    return f"Rotation(xy={self.xy}, xz={self.xz}, yz={self.yz})"

  def __add__(self, other) -> "Rotation":
    return Rotation(self.xy + other.xy, self.xz + other.xz, self.yz + other.yz)

  @staticmethod
  def deserialize(data) -> "Rotation":
    return Rotation(data["xy"], data["xz"], data["yz"])
