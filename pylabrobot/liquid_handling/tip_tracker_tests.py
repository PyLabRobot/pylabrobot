import unittest

from pylabrobot.liquid_handling.errors import (
  ChannelHasTipError,
  ChannelHasNoTipError,
  TipSpotHasTipError,
  TipSpotHasNoTipError,
)
from pylabrobot.liquid_handling.resources import HTF_L
from pylabrobot.liquid_handling.standard import TipOp, Pickup, Drop
from pylabrobot.liquid_handling.tip_tracker import TipTracker, ChannelTipTracker, SpotTipTracker


class LaxTipTracker(TipTracker):
  """ A tip tracker that doesn't do any validation. """

  @property
  def current_tip(self): # like channel
    if len(self.pending) > 0:
      if isinstance(self.pending[-1], Pickup):
        return self.pending[-1].resource
      return None
    if len(self.ops) == 0:
      return None
    if isinstance(self.ops[-1], Pickup):
      return self.ops[-1].resource
    return None

  def validate(self, op: TipOp):
    pass


class TestTipTracker(unittest.TestCase):
  """ Test the shared aspects of the tip tracker, like transactions. """

  def setUp(self) -> None:
    super().setUp()
    self.tip = HTF_L("tip").get_item("A1")

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
    op = Pickup(self.tip)
    tracker.queue_op(op)
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.current_tip, self.tip)
    self.assertEqual(tracker.pending, [op])
    self.assertEqual(tracker.ops, [])

    tracker.commit()
    self.assertEqual(tracker.has_tip, True)
    self.assertEqual(tracker.current_tip, self.tip)
    self.assertEqual(tracker.pending, [])
    self.assertEqual(tracker.ops, [op])


class TestChannelTipTracker(unittest.TestCase):
  """ Tests for the channel tip tracker. """

  def setUp(self) -> None:
    super().setUp()
    self.tip = HTF_L("tip").get_item("A1")

  def test_pickup_with_tip(self):
    tracker = ChannelTipTracker(start_with_tip=True)
    with self.assertRaises(ChannelHasTipError):
      tracker.queue_op(Pickup(resource=self.tip))

  def test_drop_with_tip(self):
    tracker = ChannelTipTracker(start_with_tip=True)
    tracker.queue_op(Drop(resource=self.tip))

  def test_drop_without_tip(self):
    tracker = ChannelTipTracker()
    with self.assertRaises(ChannelHasNoTipError):
      tracker.queue_op(Drop(resource=self.tip))

  def test_pickup_drop(self):
    tracker = ChannelTipTracker()
    tracker.queue_op(Pickup(resource=self.tip))
    tracker.queue_op(Drop(resource=self.tip))
    tracker.commit()
    self.assertEqual(tracker.ops, [Pickup(resource=self.tip), Drop(resource=self.tip)])


class TestSpotTipTracker(unittest.TestCase):
  """ Tests for the tip spot tip tracker. """

  def setUp(self) -> None:
    super().setUp()
    self.tip = HTF_L("tip").get_item("A1")

  def test_pickup_without_tip(self):
    tracker = SpotTipTracker()
    with self.assertRaises(TipSpotHasNoTipError):
      tracker.queue_op(Pickup(resource=self.tip))

  def test_drop_without_tip(self):
    tracker = SpotTipTracker()
    tracker.queue_op(Drop(resource=self.tip))
    self.assertEqual(tracker.pending, [Drop(resource=self.tip)])

  def test_drop_with_tip(self):
    tracker = SpotTipTracker(start_with_tip=True)
    with self.assertRaises(TipSpotHasTipError):
      tracker.queue_op(Drop(resource=self.tip))

  def test_drop_pickup(self):
    tracker = SpotTipTracker()
    tracker.queue_op(Drop(resource=self.tip))
    tracker.queue_op(Pickup(resource=self.tip))
    self.assertEqual(tracker.pending, [Pickup(resource=self.tip), Drop(resource=self.tip)])
