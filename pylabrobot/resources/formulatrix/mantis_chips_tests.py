import json
import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.formulatrix.mantis_chips import MantisChip, MantisChipNozzle
from pylabrobot.resources.resource import Resource


def make_chip(name: str = "chip") -> MantisChip:
  return MantisChip(
    name=name,
    size_x=10.0,
    size_y=20.0,
    size_z=5.0,
    chip_type="low_volume_hi_fi",
    dispense_type="drops",
    diaphragm_volumes=(0.1, 0.5),
    minimum_volume=0.1,
    hold_up_volume=60.0,
    maximum_pulses_per_second={0.1: 10.0, 0.5: 7.0},
    fluid_channel_diameter=0.25,
    default_prime_volume={"tube": 100.4, "pipette_tip": 6.0},
    default_predispense_volume={"tube": 0.6, "pipette_tip": 0.6},
    model="test_chip",
  )


class MantisChipTests(unittest.TestCase):
  def test_serialize_round_trip_through_json(self):
    chip = make_chip()
    data = json.loads(json.dumps(chip.serialize()))
    restored = Resource.deserialize(data)
    self.assertIsInstance(restored, MantisChip)
    self.assertEqual(restored, chip)
    assert isinstance(restored, MantisChip)
    self.assertEqual(restored.maximum_pulses_per_second, {0.1: 10.0, 0.5: 7.0})
    self.assertEqual(restored.diaphragm_volumes, (0.1, 0.5))

  def test_rfid_tag_is_state_not_definition(self):
    chip, other = make_chip(), make_chip()
    chip.rfid_tag = {"serial": "S", "chip_type": "low_volume_hi_fi", "chip_type_code": 8}
    self.assertEqual(chip, other)
    self.assertNotIn("rfid_tag", chip.serialize())
    other.load_state(json.loads(json.dumps(chip.serialize_state())))
    self.assertEqual(other.rfid_tag, chip.rfid_tag)

  def test_rfid_tag_none_until_read(self):
    chip = make_chip()
    self.assertIsNone(chip.rfid_tag)
    chip.load_state({})
    self.assertIsNone(chip.rfid_tag)

  def test_differs_by_chip_type_data(self):
    chip, other = make_chip(), make_chip()
    other.hold_up_volume = 200.0
    self.assertNotEqual(chip, other)


class MantisChipNozzleTests(unittest.TestCase):
  def make_nozzle(self, name: str = "nozzle") -> MantisChipNozzle:
    return MantisChipNozzle(
      name=name, size_x=4.0, size_y=4.0, size_z=2.0, inner_diameter=0.2, color="blue"
    )

  def test_nozzle_none_until_fitted(self):
    chip = make_chip()
    self.assertIsNone(chip.nozzle)
    nozzle = self.make_nozzle()
    chip.assign_child_resource(resource=nozzle, location=Coordinate(x=3.0, y=8.0, z=-2.0))
    self.assertIs(chip.nozzle, nozzle)
    chip.unassign_child_resource(nozzle)
    self.assertIsNone(chip.nozzle)

  def test_chip_with_nozzle_round_trip_through_json(self):
    chip = make_chip()
    chip.assign_child_resource(
      resource=self.make_nozzle(), location=Coordinate(x=3.0, y=8.0, z=-2.0)
    )
    restored = Resource.deserialize(json.loads(json.dumps(chip.serialize())))
    assert isinstance(restored, MantisChip)
    self.assertEqual(restored, chip)
    assert restored.nozzle is not None
    self.assertEqual(restored.nozzle.inner_diameter, 0.2)
    self.assertIsNone(restored.nozzle.material)

  def test_chips_differ_by_nozzle(self):
    chip, other = make_chip(), make_chip()
    chip.assign_child_resource(
      resource=self.make_nozzle(), location=Coordinate(x=3.0, y=8.0, z=-2.0)
    )
    other_nozzle = MantisChipNozzle(
      name="nozzle", size_x=4.0, size_y=4.0, size_z=2.0, inner_diameter=0.5, color="green"
    )
    other.assign_child_resource(resource=other_nozzle, location=Coordinate(x=3.0, y=8.0, z=-2.0))
    self.assertNotEqual(chip, other)
