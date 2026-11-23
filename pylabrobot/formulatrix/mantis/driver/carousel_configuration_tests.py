"""Serial-matched carousel geometry and motion settings from the OEM SQLite schema."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from .carousel_configuration import CarouselConfiguration
from .errors import MantisConfigurationError

SETTINGS = {
  "LC3.StepsPerRev": "1653.3333333333333",
  "LC3.RightFront.DefaultRotation": "98.9052517798639",
  "LC3.PrepositionDegree": "2",
  "LC3.HomingVelocity.Low": "800",
  "LC3.HomingVelocity.High": "1000",
  "LC3.HomingAcceleration": "3000",
  "LC3.RotateVelocity": "800",
  "LC3.RotateAcceleration": "3000",
}


class CarouselConfigurationTests(unittest.TestCase):
  def setUp(self):
    """Create two carousel calibrations so selecting the unit and location is observable."""
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.path = str(Path(directory.name) / "data.db")
    database = sqlite3.connect(self.path)
    try:
      database.executescript(
        "CREATE TABLE Config (Key TEXT PRIMARY KEY, Value TEXT);"
        "CREATE TABLE LC3 (SerialNumber TEXT, LastLocation TEXT, LastCarouselId INTEGER);"
        "CREATE TABLE Carousel (Id INTEGER, Capacity INTEGER);"
        "CREATE TABLE CarouselTuningInfo (LC3SerialNumber TEXT, LC3Location TEXT, "
        "CarouselId INTEGER, LC3Origin REAL);"
        "INSERT INTO LC3 VALUES ('L-000143', 'RightFront', 2);"
        "INSERT INTO Carousel VALUES (2, 24);"
        "INSERT INTO CarouselTuningInfo VALUES ('L-000143', 'RightFront', 2, 102.627260269657);"
        "INSERT INTO CarouselTuningInfo VALUES ('L-000143', 'LeftFront', 2, 10);"
      )
      database.executemany("INSERT INTO Config VALUES (?, ?)", SETTINGS.items())
      database.commit()
    finally:
      database.close()

  def test_matching_serial_location_and_carousel_with_degree_units(self):
    """The saved hardware assignment selects the calibrated origin and converts motor rates."""
    original = Path(self.path).read_bytes()
    config = CarouselConfiguration.from_oem_database(path=self.path, device_id="L-000143")
    self.assertEqual(config.capacity, 24)
    self.assertAlmostEqual(config.slot_one_position, 102.627260269657)
    self.assertEqual(config.preposition, 2)
    self.assertAlmostEqual(config.homing_slow_speed, 174.19354838709677)
    self.assertAlmostEqual(config.default_acceleration, 653.2258064516129)
    self.assertEqual(Path(self.path).read_bytes(), original)

  def test_wrong_serial_does_not_borrow_another_units_calibration(self):
    """A missing hardware assignment is an error."""
    with self.assertRaisesRegex(MantisConfigurationError, "expected one calibrated carousel"):
      CarouselConfiguration.from_oem_database(path=self.path, device_id="L-000999")

  def test_missing_or_invalid_motion_setting_does_not_use_a_default(self):
    """Geometry and motion settings must all be present and finite."""
    for value in (None, "nan", "-1", "0"):
      with self.subTest(value=value):
        database = sqlite3.connect(self.path)
        try:
          database.execute("UPDATE Config SET Value=? WHERE Key='LC3.StepsPerRev'", (value,))
          database.commit()
        finally:
          database.close()
        with self.assertRaises(MantisConfigurationError):
          CarouselConfiguration.from_oem_database(path=self.path, device_id="L-000143")

  def test_missing_database_is_not_created(self):
    """Opening an invalid path must not create a replacement OEM database."""
    path = self.path + ".missing"
    with self.assertRaises(MantisConfigurationError):
      CarouselConfiguration.from_oem_database(path=path, device_id="L-000143")
    self.assertFalse(Path(path).exists())
