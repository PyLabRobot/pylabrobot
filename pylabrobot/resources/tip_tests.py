import unittest

from pylabrobot.resources.hamilton import (
  HamiltonTip,
  TipPickupMethod,
  TipSize,
)
from pylabrobot.resources.tip import Tip
from pylabrobot.serializer import deserialize, serialize


class TipTests(unittest.TestCase):
  """Test for tip classes."""

  def test_serialize(self):
    tip = Tip(False, 10.0, 10.0, 1.0, name="test_tip")
    self.assertEqual(
      serialize(tip),
      {
        "type": "Tip",
        "name": "test_tip",
        "has_filter": False,
        "total_tip_length": 10.0,
        "maximal_volume": 10.0,
        "fitting_depth": 1.0,
      },
    )

  def test_deserialize(self):
    tip = Tip(False, 10.0, 10.0, 1.0, name="test_tip")
    self.assertEqual(deserialize(serialize(tip)), tip)

  def test_serialize_subclass(self):
    tip = HamiltonTip(
      False,
      10.0,
      10.0,
      TipSize.HIGH_VOLUME,
      TipPickupMethod.OUT_OF_RACK,
      name="test_tip",
    )
    self.assertEqual(
      tip.serialize(),
      {
        "type": "HamiltonTip",
        "name": "test_tip",
        "has_filter": False,
        "total_tip_length": 10.0,
        "maximal_volume": 10.0,
        "pickup_method": "OUT_OF_RACK",
        "tip_size": "HIGH_VOLUME",
      },
    )

  def test_deserialize_subclass(self):
    tip = HamiltonTip(
      False,
      10.0,
      10.0,
      TipSize.HIGH_VOLUME,
      TipPickupMethod.OUT_OF_RACK,
      name="test_tip",
    )
    self.assertEqual(HamiltonTip.deserialize(tip.serialize()), tip)
