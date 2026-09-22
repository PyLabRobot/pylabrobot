"""Tests for shared tip / volume / deck resource state helpers."""

from __future__ import annotations

import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.resource_state import (
  VolumeTransferIntent,
  finalize_volume_ops,
  place_resource,
  queue_volume_transfers,
  successes_from_failed_channels,
)
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_tracking import set_tip_tracking
from pylabrobot.resources.volume_tracker import set_volume_tracking
from pylabrobot.resources.well import Well, WellBottomType


def _tip(name: str = "t") -> Tip:
  return Tip(
    diameter=5.0,
    has_filter=False,
    size_z=50,
    maximal_volume=200,
    fitting_depth=10,
    name=name,
  )


class TestResourceStateOutcomes(unittest.TestCase):
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
