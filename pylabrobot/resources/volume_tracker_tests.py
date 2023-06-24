import unittest

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
    self.assertEqual(tracker.get_used_volume(), 60)
    self.assertEqual(tracker.get_free_volume(), 40)

    with self.assertRaises(TooLittleLiquidError):
      tracker.remove_liquid(volume=100)
