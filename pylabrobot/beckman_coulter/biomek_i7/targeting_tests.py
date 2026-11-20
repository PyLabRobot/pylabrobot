"""Targeting: from labware on the deck to where a pod's first channel goes, against Biomek's logged
moves (2026-10-20). The plate is Biomek's own class (`BCFlat96`)."""

import unittest

from pylabrobot.beckman_coulter.biomek_i7 import targeting
from pylabrobot.beckman_coulter.biomek_i7.resource_model.labware import biomek_labware
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_configuration,
  generic_deck,
)
from pylabrobot.resources import Plate, beckman_coulter_biomek_96_tiprack_1070uL


def bc_flat_96(name: str) -> Plate:
  plate = biomek_labware("BCFlat96", name)
  assert isinstance(plate, Plate)
  return plate


class TargetingTests(unittest.TestCase):
  def setUp(self) -> None:
    self.deck = generic_deck()
    self.config = generic_configuration()

  def test_a_position_centres_its_labware(self) -> None:
    rack = beckman_coulter_biomek_96_tiprack_1070uL("tips")
    site = self.deck.get_position("TL1")
    site.assign_child_resource(rack)
    assert rack.location is not None
    self.assertAlmostEqual(rack.location.x, (site.get_size_x() - rack.get_size_x()) / 2)
    self.assertAlmostEqual(rack.location.y, (site.get_size_y() - rack.get_size_y()) / 2)

  def test_tips_and_a_plate_where_biomek_moved(self) -> None:
    rack = beckman_coulter_biomek_96_tiprack_1070uL("tips")
    self.deck.get_position("TL1").assign_child_resource(rack)
    plate = bc_flat_96("plate")
    self.deck.get_position("P6").assign_child_resource(plate)
    for pod, resource, logged in (
      (1, rack.get_item("A1"), (12.701, 37.45)),  # the Multichannel's 96-tip load
      (1, plate.get_well("A1"), (56.456, 47.864)),  # its dispense into the plate
      (2, plate.get_well("A1"), (56.46, 47.864)),  # the Span-8's probe 1 over the same well
    ):
      point = targeting.target(self.deck, self.config, pod, resource)
      with self.subTest(pod=pod, resource=resource.name):
        # PyLabRobot's labware definitions differ from Biomek's classes by up to 0.1 mm.
        self.assertAlmostEqual(point.x, logged[0], delta=0.015)
        self.assertAlmostEqual(point.y, logged[1], delta=0.015)

  def test_each_pod_uses_its_own_corner(self) -> None:
    plate = bc_flat_96("plate")
    self.deck.get_position("P9").assign_child_resource(plate)
    a1 = plate.get_well("A1")
    one = targeting.target(self.deck, self.config, 1, a1)
    two = targeting.target(self.deck, self.config, 2, a1)
    corners = self.config.deck_positions["P9"]
    self.assertAlmostEqual(two.x - one.x, corners.corner(2)[0] - corners.corner(1)[0])
    self.assertAlmostEqual(two.z - one.z, corners.corner(2)[2] - corners.corner(1)[2])

  def test_a_resource_on_no_position_is_not_corrected(self) -> None:
    rack = beckman_coulter_biomek_96_tiprack_1070uL("tips")
    self.deck.assign_child_resource(rack, location=self.deck.pin_location(30, 5))
    self.assertIsNone(targeting.deck_position_of(self.deck, rack))
    point = targeting.target(self.deck, self.config, 1, rack)
    self.assertEqual(point, targeting.to_biomek(rack.get_location_wrt(self.deck, "c", "c", "b")))
