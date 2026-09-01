"""Tests for shared tip / volume / deck resource state helpers."""

from __future__ import annotations

import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_state import (
  TipDropIntent,
  TipPickupIntent,
  VolumeTransferIntent,
  finalize_tip_ops,
  finalize_volume_ops,
  place_resource,
  queue_tip_drops,
  queue_tip_pickups,
  queue_volume_transfers,
  successes_from_failed_channels,
)
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.tip_tracker import TipTracker, set_tip_tracking
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.volume_tracker import set_volume_tracking
from pylabrobot.resources.well import Well, WellBottomType


def _tip(name: str = "t") -> Tip:
  return Tip(
    has_filter=False,
    total_tip_length=50,
    maximal_volume=200,
    fitting_depth=10,
    name=name,
  )


def _spot(name: str = "spot") -> TipSpot:
  spot = TipSpot(name=name, size_x=9, size_y=9, size_z=0, make_tip=_tip)
  spot.tracker.add_tip(spot.make_tip(), origin=spot, commit=True)
  return spot


class TestResourceStateTips(unittest.TestCase):
  def setUp(self) -> None:
    set_tip_tracking(True)
    set_volume_tracking(False)

  def tearDown(self) -> None:
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_pickup_commit_clears_spot_and_mounts_channel(self) -> None:
    spot = _spot()
    channel = TipTracker(thing="ch0")
    tip = spot.get_tip()
    intents = [TipPickupIntent(channel=0, tip_spot=spot, tip=tip, channel_tracker=channel)]
    queue_tip_pickups(intents)
    finalize_tip_ops(intents, {0: True})
    self.assertFalse(spot.has_tip())
    self.assertTrue(channel.has_tip)
    self.assertIs(channel.get_tip(), tip)

  def test_pickup_rollback_restores_spot(self) -> None:
    spot = _spot()
    channel = TipTracker(thing="ch0")
    tip = spot.get_tip()
    intents = [TipPickupIntent(channel=0, tip_spot=spot, tip=tip, channel_tracker=channel)]
    queue_tip_pickups(intents)
    finalize_tip_ops(intents, {0: False})
    self.assertTrue(spot.has_tip())
    self.assertFalse(channel.has_tip)

  def test_drop_to_spot_and_trash(self) -> None:
    spot = _spot("src")
    dest = TipSpot(name="dest", size_x=9, size_y=9, size_z=0, make_tip=_tip)
    trash = Trash(name="trash", size_x=10, size_y=10, size_z=10)
    channel = TipTracker(thing="ch0")
    tip = spot.get_tip()
    pick = [TipPickupIntent(channel=0, tip_spot=spot, tip=tip, channel_tracker=channel)]
    queue_tip_pickups(pick)
    finalize_tip_ops(pick, {0: True})

    drop_spot = [TipDropIntent(channel=0, destination=dest, tip=tip, channel_tracker=channel)]
    queue_tip_drops(drop_spot)
    finalize_tip_ops(drop_spot, {0: True})
    self.assertTrue(dest.has_tip())
    self.assertFalse(channel.has_tip)

    tip2 = dest.get_tip()
    pick2 = [TipPickupIntent(channel=0, tip_spot=dest, tip=tip2, channel_tracker=channel)]
    queue_tip_pickups(pick2)
    finalize_tip_ops(pick2, {0: True})
    drop_trash = [TipDropIntent(channel=0, destination=trash, tip=tip2, channel_tracker=channel)]
    queue_tip_drops(drop_trash)
    finalize_tip_ops(drop_trash, {0: True})
    self.assertFalse(channel.has_tip)
    self.assertFalse(dest.has_tip())

  def test_successes_from_failed_channels(self) -> None:
    self.assertEqual(
      successes_from_failed_channels([0, 1], {1: Exception("x")}),
      {0: True, 1: False},
    )


class TestResourceStateVolume(unittest.TestCase):
  def setUp(self) -> None:
    set_volume_tracking(True)
    set_tip_tracking(False)

  def tearDown(self) -> None:
    set_volume_tracking(False)
    set_tip_tracking(False)

  def test_aspirate_commit(self) -> None:
    well = Well(
      name="w",
      size_x=9,
      size_y=9,
      size_z=10,
      bottom_type=WellBottomType.FLAT,
      max_volume=200,
    )
    well.tracker.set_volume(100)
    tip = _tip()
    intents = [
      VolumeTransferIntent(
        channel=0,
        container=well,
        tip=tip,
        volume_ul=25,
        direction="aspirate",
      )
    ]
    queue_volume_transfers(intents)
    finalize_volume_ops(intents, {0: True})
    self.assertAlmostEqual(well.tracker.get_used_volume(), 75)
    self.assertAlmostEqual(tip.tracker.get_used_volume(), 25)


class TestPlaceResource(unittest.TestCase):
  def test_place_onto_holder(self) -> None:
    holder_a = ResourceHolder(name="a", size_x=100, size_y=100, size_z=10)
    holder_b = ResourceHolder(name="b", size_x=100, size_y=100, size_z=10)
    plate = Resource(name="p", size_x=127, size_y=85, size_z=14)
    holder_a.assign_child_resource(plate)
    place_resource(plate, holder_b)
    self.assertIs(holder_b.resource, plate)
    self.assertIsNone(holder_a.resource)
    self.assertEqual(plate.location, Coordinate.zero())


if __name__ == "__main__":
  unittest.main()
