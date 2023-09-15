import unittest

from pylabrobot.pumps.calibration import PumpCalibration

import pylabrobot
import os

plr_directory = os.path.join(pylabrobot.__path__[0], "testing", "test_data")


class TestCalibration(unittest.TestCase):
  """
    Tests for the PumpCalibration class.
  """

  def setUp(self) -> None:
    self.json_list_path = os.path.join(plr_directory, "test_calibration_list.json")
    self.json_dict_path = os.path.join(plr_directory, "test_calibration_dict.json")
    self.json_null_path = os.path.join(plr_directory, "test_calibration_null.json")
    self.csv_path = os.path.join(plr_directory, "test_calibration.csv")
    self.txt_path = os.path.join(plr_directory, "test_calibration.txt")

  def test_load_calibration(self):
    calibration = PumpCalibration.load_calibration()
    self.assertIsNone(calibration)
    calibration = PumpCalibration.load_calibration([1.0, 2.0])
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 2.0)
    calibration = PumpCalibration.load_calibration({0: 1.0, 1: 2.0})
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 2.0)
    calibration = PumpCalibration.load_calibration({0: 1.0, 1: 2.0})
    self.assertEqual(calibration[0], 2.0)
    self.assertEqual(calibration[1], 1.0)
    self.assertRaises(ValueError, PumpCalibration.load_calibration, {0: 1.0, 1: 2.0, 3: 3.0})
    self.assertRaises(ValueError, PumpCalibration.load_calibration, {0: -1.0, 1: 2.0, 2: 3.0})
    self.assertRaises(ValueError, PumpCalibration.load_calibration, {-1: 1.0, 0: 1.0, 1: 2.0})
    self.assertRaises(ValueError, PumpCalibration.load_calibration, {0: 1.0, 1: 1.0, 2: 2.0})
    calibration = PumpCalibration.load_calibration(1.0, num_items=2)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 1.0)
    calibration = PumpCalibration.load_calibration(self.csv_path)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 2.0)
    calibration = PumpCalibration.load_calibration(self.json_list_path)
    self.assertEqual(calibration[0], 1)
    self.assertEqual(calibration[1], 1)
    calibration = PumpCalibration.load_calibration(self.json_dict_path)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 1.0)
    self.assertRaises(TypeError, PumpCalibration.load_calibration, self.json_null_path)
    self.assertRaises(NotImplementedError,
                      PumpCalibration.load_calibration,
                      self.txt_path)
