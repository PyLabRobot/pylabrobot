import unittest

from .tube import Tube, TubeBottomType


class TubeTests(unittest.TestCase):
  def test_bottom_type_serialization(self):
    tube = Tube(
      name="tube",
      size_x=10,
      size_y=10,
      size_z=75,
      max_volume=4_000,
      bottom_type=TubeBottomType.U,
    )

    serialized = tube.serialize()
    self.assertEqual(serialized["bottom_type"], "U")

    restored = Tube.deserialize(serialized)
    self.assertEqual(restored.bottom_type, TubeBottomType.U)

  def test_deserialize_without_bottom_type(self):
    tube = Tube(name="tube", size_x=10, size_y=10, size_z=75, max_volume=4_000)
    serialized = tube.serialize()
    del serialized["bottom_type"]

    restored = Tube.deserialize(serialized)
    self.assertEqual(restored.bottom_type, TubeBottomType.UNKNOWN)
