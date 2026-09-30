import math

from pylabrobot.serializer import SerializableMixin

# Quaternions use (w, x, y, z); Euler angles retain the Rz * Ry * Rx convention.
_Quaternion = tuple[float, float, float, float]


def _normalize_quaternion(quaternion: _Quaternion) -> _Quaternion:
  """Return a unit-length quaternion."""
  norm = math.sqrt(sum(component * component for component in quaternion))
  w, x, y, z = quaternion
  return w / norm, x / norm, y / norm, z / norm


def _multiply_quaternions(left: _Quaternion, right: _Quaternion) -> _Quaternion:
  """Compose two quaternions using the Hamilton product."""
  left_w, left_x, left_y, left_z = left
  right_w, right_x, right_y, right_z = right
  return _normalize_quaternion(
    (
      left_w * right_w - left_x * right_x - left_y * right_y - left_z * right_z,
      left_w * right_x + left_x * right_w + left_y * right_z - left_z * right_y,
      left_w * right_y - left_x * right_z + left_y * right_w + left_z * right_x,
      left_w * right_z + left_x * right_y - left_y * right_x + left_z * right_w,
    )
  )


def _quaternion_from_euler(x: float, y: float, z: float) -> _Quaternion:
  """Convert roll, pitch, and yaw in degrees to a quaternion."""
  half_roll = math.radians(x) / 2
  half_pitch = math.radians(y) / 2
  half_yaw = math.radians(z) / 2
  cos_roll, sin_roll = math.cos(half_roll), math.sin(half_roll)
  cos_pitch, sin_pitch = math.cos(half_pitch), math.sin(half_pitch)
  cos_yaw, sin_yaw = math.cos(half_yaw), math.sin(half_yaw)
  return _normalize_quaternion(
    (
      cos_roll * cos_pitch * cos_yaw + sin_roll * sin_pitch * sin_yaw,
      sin_roll * cos_pitch * cos_yaw - cos_roll * sin_pitch * sin_yaw,
      cos_roll * sin_pitch * cos_yaw + sin_roll * cos_pitch * sin_yaw,
      cos_roll * cos_pitch * sin_yaw - sin_roll * sin_pitch * cos_yaw,
    )
  )


def _quaternion_to_euler(quaternion: _Quaternion) -> tuple[float, float, float]:
  """Convert a quaternion to roll, pitch, and yaw in degrees."""
  w, x, y, z = quaternion
  sin_pitch = max(-1.0, min(1.0, 2 * (w * y - z * x)))
  roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
  pitch = math.asin(sin_pitch)
  yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
  return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def _nearest_equivalent_angle(angle: float, reference: float) -> float:
  """Shift an angle by full turns so it stays close to a reference value."""
  equivalent = angle + 360 * round((reference - angle) / 360)
  if math.isclose(equivalent, reference, rel_tol=0.0, abs_tol=1e-12):
    return reference
  return equivalent


class Rotation(SerializableMixin):
  """Represents a 3D rotation."""

  def __init__(self, x: float = 0, y: float = 0, z: float = 0):
    self._x = x  # around x-axis, roll
    self._y = y  # around y-axis, pitch
    self._z = z  # around z-axis, yaw
    self._quaternion = _quaternion_from_euler(x=x, y=y, z=z)

  @classmethod
  def _from_quaternion(
    cls, quaternion: _Quaternion, reference: tuple[float, float, float]
  ) -> "Rotation":
    """Create a rotation using the equivalent Euler angles closest to a reference."""
    quaternion = _normalize_quaternion(quaternion)
    w, x, y, z = quaternion
    sin_pitch = 2 * (w * y - z * x)
    cos_pitch = math.hypot(1 - 2 * (y * y + z * z), 2 * (x * y + w * z))
    if cos_pitch < 1e-12:
      coupled_angle = math.degrees(2 * math.atan2(x, w))
      if sin_pitch > 0:
        difference = _nearest_equivalent_angle(coupled_angle, reference[0] - reference[2])
        euler = (
          (reference[0] + reference[2] + difference) / 2,
          _nearest_equivalent_angle(90, reference[1]),
          (reference[0] + reference[2] - difference) / 2,
        )
      else:
        total = _nearest_equivalent_angle(coupled_angle, reference[0] + reference[2])
        euler = (
          (reference[0] - reference[2] + total) / 2,
          _nearest_equivalent_angle(-90, reference[1]),
          (reference[2] - reference[0] + total) / 2,
        )
      rotation = cls(x=euler[0], y=euler[1], z=euler[2])
      rotation._quaternion = quaternion
      return rotation

    principal = _quaternion_to_euler(quaternion)
    alternate = (principal[0] + 180, 180 - principal[1], principal[2] + 180)
    candidates = [
      tuple(_nearest_equivalent_angle(angle, target) for angle, target in zip(candidate, reference))
      for candidate in (principal, alternate)
    ]
    x, y, z = min(
      candidates,
      key=lambda candidate: sum(
        (angle - target) ** 2 for angle, target in zip(candidate, reference)
      ),
    )
    rotation = cls(x=x, y=y, z=z)
    rotation._quaternion = quaternion
    return rotation

  def get_rotation_matrix(self) -> list[list[float]]:
    """Return the rotation as a 3x3 matrix."""
    w, x, y, z = self._quaternion
    return [
      [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
      [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
      [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ]

  def serialize(self) -> dict:
    """Serialize using the public Euler-angle representation."""
    return {"x": self.x, "y": self.y, "z": self.z, "type": self.__class__.__name__}

  def __str__(self) -> str:
    return f"Rotation(x={self.x}, y={self.y}, z={self.z})"

  def __add__(self, other: "Rotation") -> "Rotation":
    """Compose rotations so the resulting matrix is ``self * other``."""
    reference = (self.x + other.x, self.y + other.y, self.z + other.z)
    return self._from_quaternion(
      _multiply_quaternions(self._quaternion, other._quaternion), reference=reference
    )

  def _prepend(self, other: "Rotation") -> None:
    """Apply another rotation in the current reference frame while keeping this instance."""
    combined = other + self
    self._x = combined.x % 360
    self._y = combined.y % 360
    self._z = combined.z % 360
    self._quaternion = combined._quaternion

  def __repr__(self) -> str:
    return self.__str__()

  @property
  def x(self) -> float:
    """Rotation around the x-axis in degrees (roll)."""
    return self._x

  @x.setter
  def x(self, value: float) -> None:
    self._x = value
    self._quaternion = _quaternion_from_euler(x=self._x, y=self._y, z=self._z)

  @property
  def y(self) -> float:
    """Rotation around the y-axis in degrees (pitch)."""
    return self._y

  @y.setter
  def y(self, value: float) -> None:
    self._y = value
    self._quaternion = _quaternion_from_euler(x=self._x, y=self._y, z=self._z)

  @property
  def z(self) -> float:
    """Rotation around the z-axis in degrees (yaw)."""
    return self._z

  @z.setter
  def z(self, value: float) -> None:
    self._z = value
    self._quaternion = _quaternion_from_euler(x=self._x, y=self._y, z=self._z)

  @property
  def roll(self) -> float:
    return self.x

  @property
  def pitch(self) -> float:
    return self.y

  @property
  def yaw(self) -> float:
    return self.z
