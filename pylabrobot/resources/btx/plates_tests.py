import unittest

from pylabrobot.resources import BTX_96_wellplate_125ul_Fb_2mm


class TestBTX96Wellplate2mm(unittest.TestCase):
  def test_geometry(self):
    plate = BTX_96_wellplate_125ul_Fb_2mm(name="btx_plate")

    a1 = plate.get_well("A1")
    h1 = plate.get_well("H1")
    a12 = plate.get_well("A12")

    assert a1.location is not None
    assert h1.location is not None
    assert a12.location is not None

    self.assertEqual(plate.get_size_x(), 127.8)
    self.assertEqual(plate.get_size_y(), 85.5)
    self.assertEqual(plate.get_size_z(), 15.9)

    self.assertAlmostEqual(a1.location.x + a1.get_size_x() / 2, 14.3)
    self.assertAlmostEqual(a1.location.y + a1.get_size_y() / 2, 74.0)
    self.assertAlmostEqual(h1.location.y, 7.0)
    self.assertAlmostEqual(a12.location.x + a12.get_size_x(), 114.3)
    self.assertEqual(a1.max_volume, 160)
