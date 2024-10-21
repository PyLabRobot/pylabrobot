import unittest

from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.volume_tracker import VolumeTracker
from pylabrobot.resources.errors import TooLittleLiquidError, TooLittleVolumeError


class TestVolumeTracker(unittest.TestCase):
  """ Test for the tip volume tracker """

  def test_init(self):
    tracker = VolumeTracker(max_volume=100)
    self.assertEqual(tracker.get_free_volume(), 100)
    self.assertEqual(tracker.get_used_volume(), 0)

    tracker.set_liquids([(None, 20)])
    self.assertEqual(tracker.get_free_volume(), 80)
    self.assertEqual(tracker.get_used_volume(), 20)

  def test_add_liquid(self):
    tracker = VolumeTracker(max_volume=100)

    tracker.add_liquid(liquid=None, volume=20)
    self.assertEqual(tracker.get_used_volume(), 20)
    self.assertEqual(tracker.get_free_volume(), 80)

    tracker.commit()
    self.assertEqual(tracker.get_used_volume(), 20)
    self.assertEqual(tracker.get_free_volume(), 80)

    with self.assertRaises(TooLittleVolumeError):
      tracker.add_liquid(liquid=None, volume=100)

  def test_remove_liquid(self):
    tracker = VolumeTracker(max_volume=100)
    tracker.add_liquid(liquid=None, volume=60)
    tracker.commit()

    self.assertEqual(tracker.get_used_volume(), 60)
    tracker.remove_liquid(volume=20)
    tracker.commit()
    self.assertEqual(tracker.get_used_volume(), 40)

    with self.assertRaises(TooLittleLiquidError):
      tracker.remove_liquid(volume=100)

  def test_get_liquids(self):
    tracker = VolumeTracker(max_volume=200)
    tracker.add_liquid(liquid=None, volume=60)
    tracker.add_liquid(liquid=Liquid.WATER, volume=60)
    tracker.commit()

    liquids = tracker.get_liquids(top_volume=100)
    self.assertEqual(liquids, [(Liquid.WATER, 60), (None, 40)])

    liquids = tracker.get_liquids(top_volume=50)
    self.assertEqual(liquids, [(Liquid.WATER, 50)])

    liquids = tracker.get_liquids(top_volume=60)
    self.assertEqual(liquids, [(Liquid.WATER, 60)])

    with self.assertRaises(TooLittleLiquidError):
      tracker.get_liquids(top_volume=600)
