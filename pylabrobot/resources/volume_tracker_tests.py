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

  def test_validation_preserves_pending_volume_and_callbacks(self):
    """Read-only checks use the pending volume without changing or notifying it."""
    tracker = VolumeTracker(thing="test", max_volume=100, initial_volume=60)
    tracker.remove_liquid(20)
    calls = []
    tracker.register_callback(lambda: calls.append(tracker.get_used_volume()))
    tracker.validate_remove_liquid(40)
    tracker.validate_add_liquid(60)
    with self.assertRaises(TooLittleLiquidError):
      tracker.validate_remove_liquid(41)
    with self.assertRaises(TooLittleVolumeError):
      tracker.validate_add_liquid(61)
    self.assertEqual(tracker.volume, 60)
    self.assertEqual(tracker.get_used_volume(), 40)
    self.assertEqual(calls, [])

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

  def test_a_rollback_tells_the_callbacks(self):
    """A failed operation is rolled back, and a listener is told the volume it last saw is gone."""
    tracker = VolumeTracker(thing="test", max_volume=100, initial_volume=60)
    seen: list = []
    tracker.register_callback(lambda: seen.append(tracker.pending_volume))
    tracker.remove_liquid(volume=20)
    tracker.rollback()
    self.assertEqual(seen, [40, 60])
