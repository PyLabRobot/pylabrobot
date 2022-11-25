import unittest

from pylabrobot.liquid_handling.errors import (
  ChannelHasTipError,
  ChannelHasNoTipError,
  TipSpotHasTipError,
  TipSpotHasNoTipError,
)
from pylabrobot.liquid_handling.resources import HTF_L
from pylabrobot.liquid_handling.standard import TipOp, Pickup, Drop
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
    self.tip = self.tip_rack.get_item("A1")
    self.tip_type = self.tip_rack.tip_type

  def test_init(self):
    tracker = LaxTipTracker()
    self.assertEqual(tracker.ops, [])
    self.assertEqual(tracker.has_tip, False)

  def test_init_with_tip(self):
    tracker = LaxTipTracker(start_with_tip=True)
    self.assertEqual(tracker.ops, [])
    self.assertEqual(tracker.has_tip, True)

  def test_pickup(self):
    tracker = LaxTipTracker()
    op = Pickup(self.tip, self.tip_type)
    tracker.queue_op(op)
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.pending, [op])
    self.assertEqual(tracker.ops, [])

    tracker.commit()
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.pending, [])
    self.assertEqual(tracker.ops, [op])


class TestChannelTipTracker(unittest.TestCase):
  """ Tests for the channel tip tracker. """

  def setUp(self) -> None:
    super().setUp()
    self.tip_rack = HTF_L("tip")
    self.tip = self.tip_rack.get_item("A1")
    self.tip_type = self.tip_rack.tip_type

  def test_pickup_with_tip(self):
    tracker = ChannelTipTracker(start_with_tip=True)
    with self.assertRaises(ChannelHasTipError):
      tracker.queue_op(Pickup(resource=self.tip, tip_type=self.tip_type))

  def test_drop_with_tip(self):
    tracker = ChannelTipTracker(start_with_tip=True)
    tracker.queue_op(Drop(resource=self.tip, tip_type=self.tip_type))

  def test_drop_without_tip(self):
    tracker = ChannelTipTracker()
    with self.assertRaises(ChannelHasNoTipError):
      tracker.queue_op(Drop(resource=self.tip, tip_type=self.tip_type))

  def test_pickup_drop(self):
    tracker = ChannelTipTracker()
    tracker.queue_op(Pickup(resource=self.tip, tip_type=self.tip_type))
    tracker.queue_op(Drop(resource=self.tip, tip_type=self.tip_type))
    tracker.commit()
    self.assertEqual(tracker.ops, [
      Pickup(resource=self.tip, tip_type=self.tip_type),
      Drop(resource=self.tip, tip_type=self.tip_type)])


class TestSpotTipTracker(unittest.TestCase):
  """ Tests for the tip spot tip tracker. """

  def setUp(self) -> None:
    super().setUp()
    self.tip_rack = HTF_L("tip")
    self.tip = self.tip_rack.get_item("A1")
    self.tip_type = self.tip_rack.tip_type

  def test_pickup_without_tip(self):
    tracker = SpotTipTracker()
    with self.assertRaises(TipSpotHasNoTipError):
      tracker.queue_op(Pickup(resource=self.tip, tip_type=self.tip_type))

  def test_drop_without_tip(self):
    tracker = SpotTipTracker()
    tracker.queue_op(Drop(resource=self.tip, tip_type=self.tip_type))
    self.assertEqual(tracker.pending, [Drop(resource=self.tip, tip_type=self.tip_type)])

  def test_drop_with_tip(self):
    tracker = SpotTipTracker(start_with_tip=True)
    with self.assertRaises(TipSpotHasTipError):
      tracker.queue_op(Drop(resource=self.tip, tip_type=self.tip_type))

  def test_drop_pickup(self):
    tracker = SpotTipTracker()
    tracker.queue_op(Drop(resource=self.tip, tip_type=self.tip_type))
    tracker.queue_op(Pickup(resource=self.tip, tip_type=self.tip_type))
    self.assertEqual(tracker.pending, [
      Pickup(resource=self.tip, tip_type=self.tip_type),
      Drop(resource=self.tip, tip_type=self.tip_type)])
