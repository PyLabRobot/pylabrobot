import unittest

from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.resource import Resource


class TestTipMountingShaft(unittest.TestCase):
  """A shaft is saved with what its size does not say, and read back from it."""

  def test_a_serialized_shaft_deserializes(self):
    shaft = TipMountingShaft(name="shaft", tip_pickup_mode="core")
    self.assertEqual(Resource.deserialize(shaft.serialize()).serialize(), shaft.serialize())

  def test_a_shaft_is_round(self):
    with self.assertRaises(ValueError):
      TipMountingShaft(name="shaft", tip_pickup_mode="core", cross_section_type="rectangle")


if __name__ == "__main__":
  unittest.main()
