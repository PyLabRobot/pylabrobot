import unittest

import pytest

from pylabrobot.opentrons.catalogue import CATALOGUE_LOAD_NAMES, is_catalogue_labware


class CatalogueTests(unittest.TestCase):
  def test_a_shipped_name_is_recognised(self):
    self.assertTrue(is_catalogue_labware("opentrons_96_wellplate_200ul_pcr_full_skirt"))

  def test_a_name_outside_the_catalogue_is_not(self):
    self.assertFalse(is_catalogue_labware("my_custom_plate"))

  def test_the_list_is_not_accidentally_empty(self):
    # An empty catalogue would silently route every resource to a synthesized
    # definition instead of the vendor's, which changes gripper grip heights.
    self.assertGreater(len(CATALOGUE_LOAD_NAMES), 100)


class CatalogueMatchesOpentronsTests(unittest.TestCase):
  """The list is a copy, so prove it still matches what it was copied from.

  Skipped unless ``opentrons-shared-data`` is installed; PyLabRobot does not
  depend on it.
  """

  def test_the_load_names_match_opentrons_own_definitions(self):
    pytest.importorskip("opentrons_shared_data")
    from opentrons_shared_data.labware import list_definitions

    theirs = {load_name for load_name, _version, _schema in list_definitions()}
    self.assertEqual(CATALOGUE_LOAD_NAMES, theirs)


if __name__ == "__main__":
  unittest.main()
