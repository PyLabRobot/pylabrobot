import unittest

from pylabrobot.resources.rotation import Rotation
from pylabrobot.serializer import deserialize
from pylabrobot.utils.linalg import matrix_multiply_3x3


class TestRotation(unittest.TestCase):
  def assertMatricesAlmostEqual(self, actual, expected):
    for actual_row, expected_row in zip(actual, expected):
      for actual_value, expected_value in zip(actual_row, expected_row):
        self.assertAlmostEqual(actual_value, expected_value)

  def test_add_composes_rotations_in_order(self):
    first = Rotation(x=90)
    second = Rotation(z=90)

    combined = first + second

    self.assertMatricesAlmostEqual(
      combined.get_rotation_matrix(),
      matrix_multiply_3x3(first.get_rotation_matrix(), second.get_rotation_matrix()),
    )

  def test_add_identity_preserves_euler_representation(self):
    rotation = Rotation(x=-180, y=100, z=270)

    combined = rotation + Rotation()

    self.assertEqual((combined.x, combined.y, combined.z), (-180, 100, 270))

  def test_add_identity_preserves_euler_representation_at_gimbal_lock(self):
    for x, pitch, z in ((30, -90, 20), (-180, -90, -165), (30, 90, 20)):
      with self.subTest(x=x, pitch=pitch, z=z):
        rotation = Rotation(x=x, y=pitch, z=z)

        combined = rotation + Rotation()

        self.assertAlmostEqual(combined.x, x)
        self.assertEqual(combined.y, pitch)
        self.assertAlmostEqual(combined.z, z)

  def test_component_assignment_updates_quaternion(self):
    rotation = Rotation(y=20, z=30)

    rotation.x = 10

    self.assertMatricesAlmostEqual(
      rotation.get_rotation_matrix(), Rotation(x=10, y=20, z=30).get_rotation_matrix()
    )

  def test_serialize_preserves_euler_shape(self):
    rotation = Rotation(x=-180, y=100, z=270)

    serialized = rotation.serialize()

    self.assertEqual(serialized, {"x": -180, "y": 100, "z": 270, "type": "Rotation"})
    restored = deserialize(serialized)
    self.assertIsInstance(restored, Rotation)
    self.assertMatricesAlmostEqual(restored.get_rotation_matrix(), rotation.get_rotation_matrix())

  def test_composed_rotation_serialization_round_trip(self):
    combined = Rotation(x=490.472537, y=270, z=263.015663) + Rotation(
      x=-336.171880, y=89.999999, z=-47.350878
    )

    restored = deserialize(combined.serialize())

    self.assertIsInstance(restored, Rotation)
    self.assertMatricesAlmostEqual(restored.get_rotation_matrix(), combined.get_rotation_matrix())

  def test_inverse_undoes_the_rotation(self):
    for rotation in (Rotation(x=90), Rotation(x=30, y=-20, z=110), Rotation(x=-180, y=100, z=270)):
      with self.subTest(rotation=rotation):
        self.assertMatricesAlmostEqual(
          (rotation + rotation.inverse()).get_rotation_matrix(), Rotation().get_rotation_matrix()
        )

  def test_inverse_about_one_axis_negates_the_angle(self):
    for angle in (-450, -90, -37.5, 0, 12.3, 90, 180, 270, 450):
      with self.subTest(angle=angle):
        inverse = Rotation(z=angle).inverse()
        self.assertEqual((inverse.x, inverse.y, inverse.z), (0, 0, -angle))

  def test_relative_to_composed_onto_the_frame_gives_the_rotation(self):
    for frame, child in (
      (Rotation(x=90), Rotation(z=90)),
      (Rotation(y=45), Rotation(x=30, z=-60)),
      (Rotation(x=30, y=-20, z=110), Rotation(x=-15, y=70, z=200)),
    ):
      with self.subTest(frame=frame, child=child):
        turned = frame + child
        relative = turned.relative_to(frame)
        self.assertMatricesAlmostEqual(
          (frame + relative).get_rotation_matrix(), turned.get_rotation_matrix()
        )
        self.assertMatricesAlmostEqual(relative.get_rotation_matrix(), child.get_rotation_matrix())

  def test_relative_to_about_z_is_the_difference_of_the_angles(self):
    for turned in (-450, -90, 0, 37.5, 90, 450):
      for frame in (-270, -0.1, 0, 90, 180.1, 360):
        with self.subTest(turned=turned, frame=frame):
          relative = Rotation(z=turned).relative_to(Rotation(z=frame))
          self.assertEqual((relative.x, relative.y, relative.z), (0, 0, turned - frame))
