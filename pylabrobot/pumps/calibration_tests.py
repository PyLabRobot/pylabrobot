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
    self.test_list = [1.0, 2.0]
    self.test_dicts = [{0: 1.0, 1: 2.0}, {1: 1.0, 0: 2.0}]
    self.error_dicts = [{0: 1.0, 1: 2.0, 3: 3.0},
                        {0: -1.0, 1: 2.0, 2: 3.0},
                        {-1: 1.0, 0: 1.0, 1: 2.0},
                        {2: 1.0, 5: 1.0, 1: 2.0},
                        {2: 1.0, 3: 1.0, 4: 2.0}]
    self.test_value = 1.0

  def test_load_calibration(self):
    calibration = PumpCalibration.load_calibration()
    self.assertIsNone(calibration.calibration)
    self.assertRaises(ValueError, PumpCalibration.load_calibration, 1.0)
    self.assertRaises(NotImplementedError,
                      PumpCalibration.load_calibration,
                      self.txt_path)

  def test_load_from_json(self):
    calibration = PumpCalibration.load_calibration(self.json_list_path)
    self.assertEqual(calibration[0], 1)
    self.assertEqual(calibration[1], 1)
    calibration = PumpCalibration.load_calibration(self.json_dict_path)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 1.0)
    self.assertRaises(TypeError, PumpCalibration.load_calibration, self.json_null_path)

  def test_load_from_csv(self):
    calibration = PumpCalibration.load_calibration(self.csv_path)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 1.0)

  def test_load_from_dict(self):
    calibration = PumpCalibration.load_calibration(self.test_dicts[0])
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 2.0)
    calibration = PumpCalibration.load_calibration(self.test_dicts[1])
    self.assertEqual(calibration[0], 2.0)
    self.assertEqual(calibration[1], 1.0)
    for error_dict in self.error_dicts:
      self.assertRaises(ValueError, PumpCalibration.load_calibration, error_dict)

  def test_load_from_list(self):
    calibration = PumpCalibration.load_calibration(self.test_list)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 2.0)

  def test_load_from_value(self):
    calibration = PumpCalibration.load_calibration(self.test_value, 2)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 1.0)

  def test_uncalibrated(self):
    calibration = PumpCalibration.uncalibrated()
    self.assertIsNone(calibration.calibration)




