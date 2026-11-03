import unittest

from pylabrobot.legacy.tip_tracker import (
  HasTipError,
  NoTipError,
  TipTracker,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.hamilton import HamiltonTip, hamilton_tip_300uL
from pylabrobot.resources.hamilton.tip_creators import TIP_DIAMETER, TipSize
from pylabrobot.resources.opentrons import opentrons_96_tiprack_300ul
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

  def test_load_committed_tip_spot_state_remains_committed(self):
    """A restored occupied spot retains its committed tip and liquid volume."""
    spot = opentrons_96_tiprack_300ul("tips").get_item("A1")
    spot.tracker.get_tip().tracker.set_volume(17)
    state = spot.serialize_state()

    spot.load_state(state)

    self.assertEqual(spot.serialize_state(), state)
    spot.tracker.rollback()
    self.assertEqual(spot.serialize_state(), state)

  def test_load_pending_tip_spot_removal_preserves_rollback(self):
    """Rollback restores a removed tip with its saved liquid volume."""
    spot = opentrons_96_tiprack_300ul("tips").get_item("A1")
    spot.tracker.get_tip().tracker.set_volume(17)
    committed = spot.serialize_state()
    spot.tracker.remove_tip(commit=False)
    pending = spot.serialize_state()

    spot.load_state(pending)

    self.assertFalse(spot.tracker.has_tip)
    self.assertEqual(spot.tracker.get_tip().name, committed["tip"]["name"])
    spot.tracker.rollback()
    self.assertEqual(spot.serialize_state(), committed)

  def test_load_channel_tip_preserves_pending_liquid_state(self):
    """Restoration preserves uncommitted liquid changes until explicit rollback."""
    tracker = TipTracker("channel")
    tracker.add_tip(self.tip)
    self.tip.tracker.set_volume(4)
    self.tip.tracker.add_liquid(3)
    saved = tracker.serialize()

    restored = TipTracker("restored")
    restored.load_state(saved)

    self.assertEqual(restored.serialize(), saved)
    restored.get_tip().tracker.rollback()
    self.assertEqual(restored.get_tip().tracker.get_used_volume(), 4)

  def test_load_pending_pickup_preserves_tip_transaction(self):
    """A pending pickup remains pending and can be rolled back after restoration."""
    tracker = TipTracker("channel")
    tracker.add_tip(self.tip, commit=False)
    saved = tracker.serialize()

    restored = TipTracker("restored")
    restored.load_state(saved)

    self.assertEqual(restored.serialize(), saved)
    self.assertTrue(restored.has_tip)
    with self.assertRaises(NoTipError):
      restored.get_tip()
    restored.rollback()
    self.assertFalse(restored.has_tip)

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
