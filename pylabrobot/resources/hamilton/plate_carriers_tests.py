import unittest

from pylabrobot.resources.hamilton import PLT_CAR_L5AC_A00, hamilton_plate_carrier_L5_ac


class PlateCarrierNameTests(unittest.TestCase):
  def test_the_old_carrier_name_still_works(self):
    with self.assertWarns(DeprecationWarning):
      old = PLT_CAR_L5AC_A00("carrier")
    self.assertEqual(old, hamilton_plate_carrier_L5_ac("carrier"))
