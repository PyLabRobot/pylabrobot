import os
import unittest

import pylabrobot
from pylabrobot.pumps.calibration import PumpCalibration


plr_directory = os.path.join(pylabrobot.__path__[0], "testing", "test_data")


class TestCalibration(unittest.TestCase):
  """ Tests for the PumpCalibration class.  """

  def test_load_calibration_value(self):
    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration(1.0)

  def test_load_from_txt(self):
    txt_path = os.path.join(plr_directory, "test_calibration.txt")
    with self.assertRaises(NotImplementedError):
      PumpCalibration.load_calibration(txt_path)

  def test_load_from_json(self):
    json_list_path = os.path.join(plr_directory, "test_calibration_list.json")
    calibration = PumpCalibration.load_calibration(json_list_path)
    self.assertEqual(calibration[0], 1)
    self.assertEqual(calibration[1], 1)

    json_dict_path = os.path.join(plr_directory, "test_calibration_dict.json")
    calibration = PumpCalibration.load_calibration(json_dict_path)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 1.0)

    json_null_path = os.path.join(plr_directory, "test_calibration_null.json")
    self.assertRaises(TypeError, PumpCalibration.load_calibration, json_null_path)

  def test_load_from_csv(self):
    csv_path = os.path.join(plr_directory, "test_calibration.csv")
    calibration = PumpCalibration.load_calibration(csv_path)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 1.0)

    csv_path = os.path.join(plr_directory, "test_calibration_three_columns.csv")
    self.assertRaises(ValueError, PumpCalibration.load_calibration, csv_path)

  def test_load_from_dict(self):
    calibration = PumpCalibration.load_calibration({0: 1.0, 1: 2.0})
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 2.0)

    calibration = PumpCalibration.load_calibration({1: 1.0, 0: 2.0})
    self.assertEqual(calibration[0], 2.0)
    self.assertEqual(calibration[1], 1.0)

  def test_load_from_dict_errors(self):
    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration({0: 1.0, 1: 2.0, 3: 3.0}) # missing key 2

    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration({0: -1.0, 1: 2.0, 2: 3.0}) # negative value

    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration({-1: 1.0, 0: 1.0, 1: 2.0}) # negative key

    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration({2: 1.0, 5: 1.0, 1: 2.0}) # missing key 0

    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration({2: 1.0, 3: 1.0, 4: 2.0}) # missing key 0, 1

  def test_load_from_list(self):
    test_list = [1.0, 2.0]
    calibration = PumpCalibration.load_calibration(test_list)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 2.0)

  def test_load_from_value(self):
    test_value = 1.0
    calibration = PumpCalibration.load_calibration(test_value, 2)
    self.assertEqual(calibration[0], 1.0)
    self.assertEqual(calibration[1], 1.0)

  def test_calibration_mode(self):
    calibration = PumpCalibration([1.0, 2.0], calibration_mode="revolutions")
    self.assertEqual(calibration.calibration_mode, "revolutions")

    calibration = PumpCalibration.load_calibration({0: 1.0, 1: 2.0}, calibration_mode="revolutions")
    self.assertEqual(calibration.calibration_mode, "revolutions")

    calibration = PumpCalibration.load_calibration(1.0, 2)
    self.assertEqual(calibration.calibration_mode, "duration")

  def test_calibration_mode_errors(self):
    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration([1.0, 2.0],
                                       calibration_mode="invalid") # type: ignore[arg-type]

    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration({0: 1.0, 1: 2.0},
                                       calibration_mode="invalid") # type: ignore[arg-type]

    with self.assertRaises(ValueError):
      PumpCalibration.load_calibration(1.0, 2,
                                       calibration_mode="invalid") # type: ignore[arg-type]
