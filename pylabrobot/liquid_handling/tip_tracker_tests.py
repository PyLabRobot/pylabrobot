import unittest

from pylabrobot.liquid_handling.errors import (
  ChannelHasTipError,
  ChannelHasNoTipError,
  TipSpotHasTipError,
  TipSpotHasNoTipError,
)
from pylabrobot.resources import HTF_L
from pylabrobot.liquid_handling.standard import TipOp, Pickup, Drop
from pylabrobot.liquid_handling.tip import Tip
from pylabrobot.liquid_handling.tip_tracker import ChannelTipTracker, SpotTipTracker


class LaxTipTracker(ChannelTipTracker):
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
    op = Pickup(self.tip_spot, self.tip)
    tracker.queue_op(op)
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.pending, [op])
    self.assertEqual(tracker.history, [])

    tracker.commit()
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.pending, [])
    self.assertEqual(tracker.history, [op])


class TestChannelTipTracker(unittest.TestCase):
  """ Tests for the channel tip tracker. """

  def setUp(self) -> None:
    super().setUp()
    self.tip_rack = HTF_L("tip")
    self.tip_spot = self.tip_rack.get_item("A1")
    self.tip = self.tip_rack.get_tip("A1")

  def test_pickup_with_tip(self):
    tracker = ChannelTipTracker()
    tracker.set_tip(Tip(False, 10, 10, 10))
    with self.assertRaises(ChannelHasTipError):
      tracker.queue_op(Pickup(resource=self.tip_spot, tip=self.tip))

  def test_drop_with_tip(self):
    tracker = ChannelTipTracker()
    tracker.set_tip(Tip(False, 10, 10, 10))
    tracker.queue_op(Drop(resource=self.tip_spot, tip=self.tip))

  def test_drop_without_tip(self):
    tracker = ChannelTipTracker()
    with self.assertRaises(ChannelHasNoTipError):
      tracker.queue_op(Drop(resource=self.tip_spot, tip=self.tip))

  def test_pickup_drop(self):
    tracker = ChannelTipTracker()
    tracker.queue_op(Pickup(resource=self.tip_spot, tip=self.tip))
    tracker.queue_op(Drop(resource=self.tip_spot, tip=self.tip))
    tracker.commit()
    self.assertEqual(tracker.history, [
      Pickup(resource=self.tip_spot, tip=self.tip),
      Drop(resource=self.tip_spot, tip=self.tip)])


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
      tracker.queue_op(Pickup(resource=self.tip_spot, tip=self.tip))

  def test_drop_without_tip(self):
    tracker = SpotTipTracker()
    tracker.queue_op(Drop(resource=self.tip_spot, tip=self.tip))
    self.assertEqual(tracker.pending, [Drop(resource=self.tip_spot, tip=self.tip)])

  def test_drop_with_tip(self):
    tracker = SpotTipTracker()
    tracker.set_tip(Tip(False, 10, 10, 10))
    with self.assertRaises(TipSpotHasTipError):
      tracker.queue_op(Drop(resource=self.tip_spot, tip=self.tip))

  def test_drop_pickup(self):
    tracker = SpotTipTracker()
    tracker.queue_op(Drop(resource=self.tip_spot, tip=self.tip))
    tracker.queue_op(Pickup(resource=self.tip_spot, tip=self.tip))
    self.assertEqual(tracker.pending, [
      Pickup(resource=self.tip_spot, tip=self.tip),
      Drop(resource=self.tip_spot, tip=self.tip)])
