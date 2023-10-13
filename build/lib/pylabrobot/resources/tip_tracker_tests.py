import unittest

from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_tracker import TipTracker, HasTipError, NoTipError


class TestTipTracker(unittest.TestCase):
  """ Test the shared aspects of the tip tracker, like transactions. """

  def setUp(self) -> None:
    super().setUp()
    self.tip = Tip(has_filter=False, total_tip_length=10, maximal_volume=10, fitting_depth=10)

  def test_init(self):
    tracker = TipTracker(thing="tester")
    self.assertEqual(tracker.has_tip, False)

  def test_add_tip(self):
    tracker = TipTracker(thing="tester")
    tracker.add_tip(self.tip)
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.get_tip(), self.tip)

    with self.assertRaises(HasTipError):
      tracker.add_tip(self.tip)

  def test_remove_tip(self):
    tracker = TipTracker(thing="tester")
    tracker.add_tip(self.tip)
    tracker.remove_tip()
    tracker.commit()
    self.assertEqual(tracker.has_tip, False)

    with self.assertRaises(NoTipError):
      tracker.get_tip()
