import unittest

from pylabrobot.high_res.sample_storage import high_res_stacker


class HighResStackerTests(unittest.TestCase):
  def test_builds_reported_stacker_geometry(self):
    stacker = high_res_stacker(
      name="stacker_2",
      zero_offset=5,
      slot_height=22.867,
      slot_count=24,
    )

    self.assertEqual(stacker.capacity, 24)
    self.assertEqual(stacker.get_size_x(), 112.3)
    self.assertEqual(stacker.get_size_y(), 146.6)
    self.assertAlmostEqual(stacker.get_size_z(), 5 + 22.867 * 24)
    self.assertEqual(stacker.model, "high_res_stacker")
    self.assertEqual(
      stacker.metadata,
      {"zero_offset": 5, "slot_height": 22.867, "slot_count": 24},
    )

    first = stacker.sites[0]
    last = stacker.sites[23]
    self.assertEqual(first.name, "stacker_2_slot_1")
    self.assertEqual(last.name, "stacker_2_slot_24")
    self.assertEqual(first.get_size_x(), 85.48)
    self.assertEqual(first.get_size_y(), 127.76)
    self.assertEqual(first.get_size_z(), 22.867)
    self.assertEqual(first.pedestal_size_z, 0)
    self.assertIsNotNone(first.location)
    self.assertIsNotNone(last.location)
    assert first.location is not None
    assert last.location is not None
    self.assertAlmostEqual(first.location.x, (112.3 - 85.48) / 2)
    self.assertAlmostEqual(first.location.y, (146.6 - 127.76) / 2)
    self.assertEqual(first.location.z, 5)
    self.assertAlmostEqual(last.location.z, 5 + 22.867 * 23)

  def test_allows_disabled_stacker(self):
    stacker = high_res_stacker(
      name="stacker_1",
      zero_offset=0,
      slot_height=28.94,
      slot_count=0,
    )

    self.assertEqual(stacker.capacity, 0)
    self.assertEqual(stacker.get_size_z(), 0)

  def test_rejects_invalid_geometry(self):
    with self.assertRaisesRegex(ValueError, "zero_offset"):
      high_res_stacker("stacker", zero_offset=-1, slot_height=22.867, slot_count=24)
    with self.assertRaisesRegex(ValueError, "slot_height"):
      high_res_stacker("stacker", zero_offset=0, slot_height=0, slot_count=24)
    with self.assertRaisesRegex(ValueError, "slot_count"):
      high_res_stacker("stacker", zero_offset=0, slot_height=22.867, slot_count=-1)
