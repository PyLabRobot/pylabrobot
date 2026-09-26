import unittest

from pylabrobot.resources.errors import (
  TooLittleLiquidError,
  TooLittleVolumeError,
)
from pylabrobot.resources.volume_tracker import VolumeTracker


class TestVolumeTracker(unittest.TestCase):
  """Test for the tip volume tracker"""

  def test_init(self):
    tracker = VolumeTracker(thing="test", max_volume=100)
    self.assertEqual(tracker.get_free_volume(), 100)
    self.assertEqual(tracker.get_used_volume(), 0)

    tracker.set_volume(20)
    self.assertEqual(tracker.get_free_volume(), 80)
    self.assertEqual(tracker.get_used_volume(), 20)

  def test_add_liquid(self):
    tracker = VolumeTracker(thing="test", max_volume=100)

    tracker.add_liquid(volume=20)
    self.assertEqual(tracker.get_used_volume(), 20)
    self.assertEqual(tracker.get_free_volume(), 80)

    tracker.commit()
    self.assertEqual(tracker.get_used_volume(), 20)
    self.assertEqual(tracker.get_free_volume(), 80)

    with self.assertRaises(TooLittleVolumeError):
      tracker.add_liquid(volume=100)

  def test_remove_liquid(self):
    tracker = VolumeTracker(thing="test", max_volume=100, initial_volume=60)
    tracker.commit()

    self.assertEqual(tracker.get_used_volume(), 60)
    tracker.remove_liquid(volume=20)
    tracker.commit()
    self.assertEqual(tracker.get_used_volume(), 40)

    with self.assertRaises(TooLittleLiquidError):
      tracker.remove_liquid(volume=100)

  def test_a_callback_registered_twice_is_called_once(self):
    """A tip that enters the same spot again registers the spot's callback again."""
    tracker = VolumeTracker(thing="test", max_volume=100)
    calls: list = []
    callback = lambda: calls.append(1)  # noqa: E731
    tracker.register_callback(callback)
    tracker.register_callback(callback)
    tracker.set_volume(10)
    self.assertEqual(calls, [1])
