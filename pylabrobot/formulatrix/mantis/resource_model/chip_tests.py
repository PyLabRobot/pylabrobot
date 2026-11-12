"""Chip tests: the chip types' identities, and chip spots placed from the manufacturer's model."""

import json
import math
import unittest

from pylabrobot.formulatrix.mantis import Mantis
from pylabrobot.formulatrix.mantis.resource_model import (
  CHIP_SLOT_RACKS,
  MANIFEST,
  MANTIS_CHIP_FACTORIES,
  RFID_CHIP_TYPE_CODES,
  MantisChip,
  get_chip_factory,
  mantis_low_volume_hi_fi_chip,
)
from pylabrobot.resources import Resource


class ChipTypeTests(unittest.TestCase):
  def test_each_factory_builds_its_chip_type(self):
    for chip_type, factory in MANTIS_CHIP_FACTORIES.items():
      with self.subTest(chip_type=chip_type):
        chip = factory(name="chip")
        self.assertEqual(chip.chip_type, chip_type)
        self.assertEqual(chip.model, f"mantis_{chip_type}_chip")
        self.assertIs(get_chip_factory(chip_type=chip_type), factory)

  def test_one_code_per_chip_type(self):
    self.assertEqual(sorted(RFID_CHIP_TYPE_CODES), list(range(11)))
    self.assertEqual(set(RFID_CHIP_TYPE_CODES.values()), set(MANTIS_CHIP_FACTORIES))
    # The codes read off a tag.
    self.assertEqual(RFID_CHIP_TYPE_CODES[0x07], "high_volume_hi_fi")
    self.assertEqual(RFID_CHIP_TYPE_CODES[0x08], "low_volume_hi_fi")

  def test_an_unknown_chip_type_refuses(self):
    with self.assertRaisesRegex(ValueError, "chip_type must be one of"):
      get_chip_factory(chip_type="dome")

  def test_a_chip_serializes_and_deserializes(self):
    chip = mantis_low_volume_hi_fi_chip(name="chip")
    loaded = Resource.deserialize(json.loads(json.dumps(chip.serialize())))
    assert isinstance(loaded, MantisChip)
    self.assertEqual(
      (loaded.chip_type, loaded.rfid_chip_type_code, loaded.sequence_folder, loaded.dispense_type),
      ("low_volume_hi_fi", 0x08, "LowVolumeHi-Fi", "standard"),
    )


class ChipSpotTests(unittest.TestCase):
  def setUp(self):
    self.mantis = Mantis(simulation=True)

  def test_a_spot_per_input_slot_in_its_rack(self):
    self.assertEqual(sorted(self.mantis.chip_spots), [1, 2, 3, 4, 5, 6])
    for slot, spot in self.mantis.chip_spots.items():
      with self.subTest(slot=slot):
        racks = {"left": self.mantis.left_chip_rack, "right": self.mantis.right_chip_rack}
        self.assertIs(spot.parent, racks[CHIP_SLOT_RACKS[slot]])

  def test_each_spots_axis_and_closed_end_are_the_models(self):
    shape = MANIFEST["chip_slot"]
    for slot, spot in self.mantis.chip_spots.items():
      with self.subTest(slot=slot):
        entry = MANIFEST["chip_slots"][str(slot)]
        self.assertAlmostEqual(spot.get_absolute_rotation().z, entry["axis"] - 90.0)
        # Half the slot's width across and its end's radius along: the closed end's centre.
        axis = math.radians(entry["axis"])
        end_x, end_y = entry["closed_end_centre"]
        assert spot.parent is not None and spot.parent.location is not None
        assert spot.location is not None
        corner = spot.parent.location + spot.location  # in the device frame
        across = (math.sin(axis), -math.cos(axis))
        along = (math.cos(axis), math.sin(axis))
        half = shape["width"] / 2
        self.assertAlmostEqual(
          corner.x + half * across[0] + shape["end_radius"] * along[0], end_x, places=3
        )
        self.assertAlmostEqual(
          corner.y + half * across[1] + shape["end_radius"] * along[1], end_y, places=3
        )

  def test_a_chip_sits_in_its_spot(self):
    spot = self.mantis.chip_spots[6]
    spot.assign_child_resource(mantis_low_volume_hi_fi_chip(name="chip"))
    chip = spot.resource
    assert isinstance(chip, MantisChip) and chip.location is not None
    self.assertAlmostEqual(
      chip.location.x, (MANIFEST["chip_slot"]["width"] - chip.get_size_x()) / 2
    )
    self.assertEqual(chip.location.y, 0.0)


if __name__ == "__main__":
  unittest.main()
