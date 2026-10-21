"""Offline tests for selecting a Flex from mDNS advertisements."""

import unittest
from unittest.mock import patch

from pylabrobot.io.mdns import MDNSService
from pylabrobot.opentrons import find_flex_ip, find_ot2_ip


class FlexDiscoveryTests(unittest.TestCase):
  """Discovery selects only unambiguous Flex addresses."""

  def setUp(self) -> None:
    """Replace network discovery with advertisements."""
    patcher = patch("pylabrobot.opentrons.discovery.discover_mdns")
    self.discover = patcher.start()
    self.addCleanup(patcher.stop)
    self.flex = MDNSService("Studio45", "192.0.2.1", 31950, {b"robotModel": b"OT-3 Standard"})

  def test_ot2_uses_shared_discovery_with_its_own_model_filter(self):
    self.discover.return_value = [
      self.flex,
      MDNSService("OT2", "192.0.2.3", 31950, {b"robotModel": b"OT-2"}),
    ]
    self.assertEqual(find_ot2_ip(), "192.0.2.3")
    self.assertEqual(find_flex_ip(), "192.0.2.1")

  def test_filters_other_devices(self) -> None:
    """Ignore printers, OT-2s, wrong ports, and missing or malformed models."""
    self.discover.return_value = [
      MDNSService("Printer", "192.0.2.2", 80, {}),
      MDNSService("OT2", "192.0.2.3", 31950, {b"robotModel": b"OT-2"}),
      MDNSService("Other", "192.0.2.4", 80, {b"robotModel": b"OT-3 Standard"}),
      MDNSService("Unknown", "192.0.2.5", 31950, {b"robotModel": None}),
      MDNSService("Malformed", "192.0.2.6", 31950, {b"robotModel": b"\xff"}),
      self.flex,
      self.flex,
    ]
    self.assertEqual(find_flex_ip(timeout=2), "192.0.2.1")
    self.discover.assert_called_once_with("_http._tcp.local.", timeout=2)

  def test_multiple_robots_require_selection(self) -> None:
    """Report candidates instead of selecting a robot arbitrarily."""
    self.discover.return_value = [
      self.flex,
      MDNSService("Second", "192.0.2.2", 31950, {b"robotModel": b"OT-3"}),
    ]
    with self.assertRaisesRegex(RuntimeError, "Multiple Flex addresses.*Studio45"):
      find_flex_ip()
    self.assertEqual(find_flex_ip(name="Second"), "192.0.2.2")
    with self.assertRaisesRegex(RuntimeError, "No Flex named 'Missing'"):
      find_flex_ip(name="Missing")

  def test_multiple_addresses_for_one_name_are_ambiguous(self) -> None:
    """A name alone cannot resolve multiple matching IP addresses."""
    self.discover.return_value = [
      self.flex,
      MDNSService("Studio45", "192.0.2.2", 31950, self.flex.properties),
    ]
    with self.assertRaisesRegex(RuntimeError, "Multiple Flex addresses"):
      find_flex_ip(name="Studio45")

  def test_no_robot(self) -> None:
    """An empty discovery result gives an actionable error."""
    self.discover.return_value = []
    with self.assertRaisesRegex(RuntimeError, "No Flex found"):
      find_flex_ip()
