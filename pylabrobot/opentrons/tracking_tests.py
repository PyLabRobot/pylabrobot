"""Read-only transfer checks account for shared and disabled volume trackers."""

import unittest

from pylabrobot.opentrons.tracking import validate_liquid_transfer
from pylabrobot.resources.errors import TooLittleLiquidError, TooLittleVolumeError
from pylabrobot.resources.volume_tracker import VolumeTracker, set_volume_tracking


class TransferValidationTests(unittest.TestCase):
  """Preflight checks must respect per-channel transfers without mutating resources."""

  def setUp(self):
    """Enable volume checks for each test."""
    set_volume_tracking(True)
    self.addCleanup(set_volume_tracking, False)

  def test_shared_source_checks_total_draw(self):
    """Two nozzles drawing from one reservoir consume their combined volume."""
    source = VolumeTracker("source", 100, 80)
    tips = [VolumeTracker("tip", 100) for _ in range(2)]
    with self.assertRaises(TooLittleLiquidError):
      validate_liquid_transfer([source, source], tips, 50)
    self.assertEqual(source.get_used_volume(), 80)
    self.assertEqual([tip.get_used_volume() for tip in tips], [0, 0])

  def test_shared_destination_checks_total_dispense(self):
    """Two nozzles dispensing to one well share its remaining capacity."""
    tips = [VolumeTracker("tip", 100, 50) for _ in range(2)]
    destination = VolumeTracker("destination", 80)
    with self.assertRaises(TooLittleVolumeError):
      validate_liquid_transfer(tips, [destination, destination], 50)
    self.assertEqual(destination.get_used_volume(), 0)
    self.assertEqual([tip.get_used_volume() for tip in tips], [50, 50])

  def test_tracker_on_both_sides_uses_net_change(self):
    """Liquid received by an earlier nozzle is available to a later nozzle."""
    first = VolumeTracker("first", 100, 50)
    second = VolumeTracker("second", 100)
    validate_liquid_transfer([first, second], [second, first], 50)
    self.assertEqual(first.get_used_volume(), 50)
    self.assertEqual(second.get_used_volume(), 0)

  def test_disabled_tracking_skips_volume_limits(self):
    """Disabled trackers and global tracking settings do not constrain a transfer."""
    source = VolumeTracker("source", 100)
    destination = VolumeTracker("destination", 100)
    source.disable()
    validate_liquid_transfer([source], [destination], 50)
    source.enable()
    set_volume_tracking(False)
    validate_liquid_transfer([source], [destination], 200)
    self.assertEqual(source.get_used_volume(), 0)
    self.assertEqual(destination.get_used_volume(), 0)
