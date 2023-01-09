import unittest

from pylabrobot.liquid_handling.standard import TipOp, Pickup, Drop
from pylabrobot.resources import HTF_L
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_tracker import (
  SpotTipTracker,
  TipSpotHasTipError,
  TipSpotHasNoTipError,
)

class LaxTipTracker(SpotTipTracker):
  """ A tip tracker that doesn't do any validation. """

  def validate(self, op: TipOp):
    pass


class TestTipTracker(unittest.TestCase):
  """ Test the shared aspects of the tip tracker, like transactions. """

  def setUp(self) -> None:
    super().setUp()
    self.tip_rack = HTF_L("tip")
    self.tip_spot = self.tip_rack.get_item("A1")
    self.tip = self.tip_rack.get_tip("A1")

  def test_init(self):
    tracker = LaxTipTracker()
    self.assertEqual(tracker.history, [])
    self.assertEqual(tracker.has_tip, False)

  def test_init_with_tip(self):
    tracker = LaxTipTracker()
    tracker.set_tip(Tip(False, 10, 10, 10))
    self.assertEqual(tracker.history, [])
    self.assertEqual(tracker.has_tip, True)

  def test_pickup(self):
    tracker = LaxTipTracker()
    op = Drop(self.tip_spot, self.tip)
    tracker.queue_drop(op)
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.pending, [op])
    self.assertEqual(tracker.history, [])

    tracker.commit()
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.pending, [])
    self.assertEqual(tracker.history, [op])


class TestSpotTipTracker(unittest.TestCase):
  """ Tests for the tip spot tip tracker. """

  def setUp(self) -> None:
    super().setUp()
    self.tip_rack = HTF_L("tip")
    self.tip_spot = self.tip_rack.get_item("A1")
    self.tip = self.tip_rack.get_tip("A1")

  def test_pickup_without_tip(self):
    tracker = SpotTipTracker()
    with self.assertRaises(TipSpotHasNoTipError):
      tracker.queue_pickup(Pickup(resource=self.tip_spot, tip=self.tip))

  def test_drop_without_tip(self):
    tracker = SpotTipTracker()
    tracker.queue_drop(Drop(resource=self.tip_spot, tip=self.tip))
    self.assertEqual(tracker.pending, [Drop(resource=self.tip_spot, tip=self.tip)])

  def test_drop_with_tip(self):
    tracker = SpotTipTracker()
    tracker.set_tip(Tip(False, 10, 10, 10))
    with self.assertRaises(TipSpotHasTipError):
      tracker.queue_drop(Drop(resource=self.tip_spot, tip=self.tip))

  def test_drop_pickup(self):
    tracker = SpotTipTracker()
    tracker.queue_drop(Drop(resource=self.tip_spot, tip=self.tip))
    tracker.queue_pickup(Pickup(resource=self.tip_spot, tip=self.tip))
    self.assertEqual(tracker.pending, [
      Pickup(resource=self.tip_spot, tip=self.tip),
      Drop(resource=self.tip_spot, tip=self.tip)])
