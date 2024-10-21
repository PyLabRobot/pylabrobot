import unittest

from pylabrobot.resources.tip import Tip
from pylabrobot.resources.ml_star import HamiltonTip, TipSize, TipPickupMethod
from pylabrobot.serializer import serialize, deserialize


class TipTests(unittest.TestCase):
  """ Test for tip classes. """

  def test_serialize(self):
    tip = Tip(False, 10.0, 10.0, 1.0)
    self.assertEqual(serialize(tip), {
      "type": "Tip",
      "has_filter": False,
      "total_tip_length": 10.0,
      "maximal_volume": 10.0,
      "fitting_depth": 1.0
    })

  def test_deserialize(self):
    tip = Tip(False, 10.0, 10.0, 1.0)
    self.assertEqual(deserialize(serialize(tip)), tip)

  def test_serialize_subclass(self):
    tip = HamiltonTip(False, 10.0, 10.0, TipSize.HIGH_VOLUME, TipPickupMethod.OUT_OF_RACK)
    self.assertEqual(tip.serialize(), {
      "type": "HamiltonTip",
      "has_filter": False,
      "total_tip_length": 10.0,
      "maximal_volume": 10.0,
      "pickup_method": "OUT_OF_RACK",
      "tip_size": "HIGH_VOLUME"
    })

  def test_deserialize_subclass(self):
    tip = HamiltonTip(False, 10.0, 10.0, TipSize.HIGH_VOLUME, TipPickupMethod.OUT_OF_RACK)
    self.assertEqual(HamiltonTip.deserialize(tip.serialize()), tip)
