import unittest

from pylabrobot.resources import Tip, HTF_L
from pylabrobot.liquid_handling.channel_tip_tracker import (
  ChannelTipTracker,
  ChannelHasTipError,
  ChannelHasNoTipError,
)
from pylabrobot.liquid_handling.standard import Pickup, Drop


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
      tracker.queue_pickup(Pickup(resource=self.tip_spot, tip=self.tip))

  def test_drop_with_tip(self):
    tracker = ChannelTipTracker()
    tracker.set_tip(Tip(False, 10, 10, 10))
    tracker.queue_drop(Drop(resource=self.tip_spot, tip=self.tip))

  def test_drop_without_tip(self):
    tracker = ChannelTipTracker()
    with self.assertRaises(ChannelHasNoTipError):
      tracker.queue_drop(Drop(resource=self.tip_spot, tip=self.tip))

  def test_pickup_drop(self):
    tracker = ChannelTipTracker()
    tracker.queue_pickup(Pickup(resource=self.tip_spot, tip=self.tip))
    tracker.queue_drop(Drop(resource=self.tip_spot, tip=self.tip))
    tracker.commit()
    self.assertEqual(tracker.history, [
      Pickup(resource=self.tip_spot, tip=self.tip),
      Drop(resource=self.tip_spot, tip=self.tip)])
