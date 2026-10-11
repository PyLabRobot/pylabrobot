import unittest

from pylabrobot.legacy.tip_tracker import (
  HasTipError,
  NoTipError,
  TipTracker,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.hamilton import HamiltonTip, hamilton_tip_300uL
from pylabrobot.resources.hamilton.tip_creators import TIP_DIAMETER, TipSize
from pylabrobot.resources.tip import Tip


class TestTipTracker(unittest.TestCase):
  """Test the shared aspects of the tip tracker, like transactions."""

  def setUp(self) -> None:
    super().setUp()
    self.tip = Tip(
      has_filter=False,
      maximal_volume=10,
      fitting_depth=10,
      name="test_tip",
      diameter=TIP_DIAMETER[TipSize.STANDARD_VOLUME],
      size_z=10,
    )

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

  def test_load_resource_tip_state(self):
    """Saved tips use the resource loader, including rotation and metadata."""
    tip = hamilton_tip_300uL("tip")
    tip.location = Coordinate(1, 2, 3)
    tip.rotate(z=90)
    tip.metadata = {"batch": "example"}
    tracker = TipTracker("source")
    tracker.add_tip(tip)

    restored = TipTracker("restored")
    restored.load_state(tracker.serialize())
    restored_tip = restored.get_tip()
    self.assertIsInstance(restored_tip, HamiltonTip)
    self.assertEqual(restored_tip.rotation.serialize(), tip.rotation.serialize())
    self.assertEqual(restored_tip.metadata, tip.metadata)
    self.assertIsNone(restored_tip.location)
    restored.commit()
    self.assertEqual(restored.get_tip(), restored_tip)

  def test_load_empty_state(self):
    """An empty saved tracker clears committed and pending tips."""
    tracker = TipTracker("tracker")
    tracker.add_tip(self.tip)
    tracker.load_state({"tip": None, "pending_tip": None})
    self.assertFalse(tracker.has_tip)
    with self.assertRaises(NoTipError):
      tracker.get_tip()

  def test_a_pending_removal_keeps_the_tip_readable_until_it_commits(self):
    """A liquid handler removes a spot's tip before its backend asks the spot which tip it is."""
    tracker = TipTracker(thing="tester")
    tracker.add_tip(self.tip)
    tracker.remove_tip(commit=False)
    self.assertEqual(tracker.has_tip, False)
    self.assertEqual(tracker.get_tip(), self.tip)
    tracker.commit()
    with self.assertRaises(NoTipError):
      tracker.get_tip()
