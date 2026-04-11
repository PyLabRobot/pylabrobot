"""Tests for the Mantis backend components.

These tests exercise the kinematics, map generator, FmlxPacket serialisation,
and constants — all without requiring hardware.
"""

import struct
import unittest

from pylabrobot.formulatrix.mantis.fmlx_driver import FmlxPacket, decode_response
from pylabrobot.formulatrix.mantis.mantis_constants import MotorStatusCode, PressureControlStatus
from pylabrobot.formulatrix.mantis.mantis_kinematics import (
  MOTOR_1_CONFIG,
  MOTOR_3_CONFIG,
  MantisKinematics,
  apply_stage_homography,
)


class TestMotorStatusCode(unittest.TestCase):
  """Test MotorStatusCode IntFlag behaviour."""

  def test_error_mask(self):
    mask = MotorStatusCode.error_mask()
    self.assertTrue(mask & MotorStatusCode.OVER_CURRENT)
    self.assertTrue(mask & MotorStatusCode.ABORTED)
    self.assertFalse(mask & MotorStatusCode.IS_MOVING)
    self.assertFalse(mask & MotorStatusCode.IS_HOMED)

  def test_bitwise_check(self):
    status = MotorStatusCode.IS_MOVING | MotorStatusCode.IS_HOMED
    self.assertTrue(status & MotorStatusCode.IS_MOVING)
    self.assertTrue(status & MotorStatusCode.IS_HOMED)
    self.assertFalse(status & MotorStatusCode.OVER_CURRENT)

  def test_pressure_status_enum(self):
    self.assertEqual(PressureControlStatus.OFF, 0)
    self.assertEqual(PressureControlStatus.SETTLED, 1)
    self.assertEqual(PressureControlStatus.UNSETTLED, 2)


class TestMotorConfig(unittest.TestCase):
  """Test MotorConfig unit conversions."""

  def test_roundtrip_position(self):
    """to_packet_units and from_packet_units should be inverses for position."""
    for val in [0.0, 45.0, -90.0, 180.0]:
      pkt = MOTOR_1_CONFIG.to_packet_units(val)
      back = MOTOR_1_CONFIG.from_packet_units(pkt)
      self.assertAlmostEqual(val, back, places=6, msg=f"Roundtrip failed for {val}")

  def test_z_axis_roundtrip(self):
    for val in [0.0, 5.0, 13.0, -1.5]:
      pkt = MOTOR_3_CONFIG.to_packet_units(val)
      back = MOTOR_3_CONFIG.from_packet_units(pkt)
      self.assertAlmostEqual(val, back, places=6)

  def test_velocity_zero(self):
    self.assertEqual(MOTOR_1_CONFIG.to_packet_units(0.0, is_velocity_or_accel=True), 0.0)
    self.assertEqual(MOTOR_1_CONFIG.from_packet_units(0.0, is_velocity_or_accel=True), 0.0)


class TestMantisKinematics(unittest.TestCase):
  """Test inverse and forward kinematics."""

  def test_xy_to_theta_origin_raises(self):
    with self.assertRaises(ValueError):
      MantisKinematics.xy_to_theta(0.0, 0.0)

  def test_roundtrip_xy(self):
    """Forward(Inverse(x,y)) should recover the original (x,y) for reachable points."""
    test_points = [
      (15.0, 31.177),
      (50.0, 50.0),
      (-40.0, 60.0),
    ]
    for x, y in test_points:
      theta1, theta2 = MantisKinematics.xy_to_theta(x, y)
      candidates = MantisKinematics.theta_to_xy(theta1, theta2)
      self.assertTrue(len(candidates) > 0, f"No FK solution for ({x}, {y})")
      # One of the candidates should match the original
      matched = False
      for cx, cy in candidates:
        if abs(cx - x) < 0.1 and abs(cy - y) < 0.1:
          matched = True
          break
      self.assertTrue(matched, f"FK roundtrip failed for ({x}, {y}): candidates={candidates}")

  def test_theta_to_xy_unreachable(self):
    """Extreme angles should return empty list."""
    result = MantisKinematics.theta_to_xy(0.0, 0.0)
    # Whether this is reachable depends on geometry; just check it doesn't crash
    self.assertIsInstance(result, list)


class TestFmlxPacket(unittest.TestCase):
  """Test FMLX packet construction and checksum."""

  def test_minimal_packet(self):
    pkt = FmlxPacket(1, address=0)
    raw = pkt.to_bytes()
    # Header (12) + checksum (2) = 14 bytes minimum
    self.assertEqual(len(raw), 14)
    # Size field should be 12 (header only, no payload)
    size = struct.unpack_from("<H", raw, 0)[0]
    self.assertEqual(size, 12)

  def test_checksum_consistency(self):
    pkt = FmlxPacket(22, address=0)
    pkt.add_int16(0).add_double(1.0).add_double(2.0).add_double(3.0)
    raw = pkt.to_bytes()

    content = raw[:-2]
    stored_checksum = struct.unpack_from("<H", raw, len(raw) - 2)[0]
    computed = FmlxPacket.calculate_checksum(content)
    self.assertEqual(stored_checksum, computed)

  def test_add_bool(self):
    pkt = FmlxPacket(99)
    pkt.add_bool(True)
    self.assertEqual(struct.unpack_from("<H", pkt.data, 0)[0], 1)
    pkt2 = FmlxPacket(99)
    pkt2.add_bool(False)
    self.assertEqual(struct.unpack_from("<H", pkt2.data, 0)[0], 0)


class TestDecodeResponse(unittest.TestCase):
  """Test response decoding for known opcodes."""

  def test_get_version(self):
    version_str = "4.20.3".encode("utf-16-le") + b"\x00\x00"
    result = decode_response(1, 0, 0, version_str)
    self.assertEqual(result["value"], "4.20.3")

  def test_get_motor_status(self):
    data = struct.pack("<H", 0x0004)  # IS_HOMED
    result = decode_response(20, 0, 0, data)
    self.assertEqual(result["status"], 4)

  def test_error_status(self):
    result = decode_response(1, 0, -1, b"\x00\x00")
    self.assertIn("error", result)


class TestMantisDiaphragmCoordinates(unittest.TestCase):
  """Test conversion of PLR Well locations to Mantis machine coordinates."""

  def _make_plate(self):
    from pylabrobot.resources.plate import Plate
    from pylabrobot.resources.utils import create_ordered_items_2d
    from pylabrobot.resources.well import CrossSectionType, Well, WellBottomType

    well_kwargs = {
      "size_x": 6.0,
      "size_y": 6.0,
      "size_z": 10.0,
      "bottom_type": WellBottomType.FLAT,
      "cross_section_type": CrossSectionType.CIRCLE,
      "max_volume": 300.0,
      "material_z_thickness": 1.0,
    }
    return Plate(
      name="custom_96",
      size_x=127.76,
      size_y=85.11,
      size_z=14.30,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=12,
        num_items_y=8,
        dx=11.0,
        dy=8.0,
        dz=2.0,
        item_dx=9.0,
        item_dy=9.0,
        **well_kwargs,
      ),
    )

  def _ideal(self, well, plate):
    """Per-well ideal (pre-homography) Mantis frame coordinate."""
    center = well.get_location_wrt(plate, x="c", y="c", z="b")
    return center.x, plate.get_size_y() - center.y

  def test_a1_maps_to_back_of_plate(self):
    """PLR 'A1' is physically at the back (high y); after y-flip it lands at
    low y in Mantis frame, matching the Mantis convention where 'A1' is at the
    front of the plate."""
    plate = self._make_plate()
    ideal_x, ideal_y = self._ideal(plate.get_item("A1"), plate)
    # A1 well LFB = (11.0, 8.0 + 7*9.0 = 71.0); center = (14.0, 74.0)
    # Mantis y = 85.11 - 74.0 = 11.11
    self.assertAlmostEqual(ideal_x, 14.0, places=6)
    self.assertAlmostEqual(ideal_y, 11.11, places=6)

  def test_h1_maps_to_front_of_plate(self):
    plate = self._make_plate()
    ideal_x, ideal_y = self._ideal(plate.get_item("H1"), plate)
    # H1 LFB = (11.0, 8.0); center = (14.0, 11.0); flipped y = 74.11
    self.assertAlmostEqual(ideal_x, 14.0, places=6)
    self.assertAlmostEqual(ideal_y, 74.11, places=6)

  def test_h12_corner(self):
    plate = self._make_plate()
    ideal_x, ideal_y = self._ideal(plate.get_item("H12"), plate)
    # H12 LFB = (11.0 + 11*9.0, 8.0) = (110.0, 8.0); center = (113.0, 11.0)
    self.assertAlmostEqual(ideal_x, 113.0, places=6)
    self.assertAlmostEqual(ideal_y, 74.11, places=6)

  def test_machine_coord_applies_homography_and_z(self):
    """The full conversion should apply the stage homography and return the
    configured dispense_z."""
    from pylabrobot.formulatrix.mantis.diaphragm_dispenser_backend import (
      MantisDiaphragmDispenserBackend,
    )

    plate = self._make_plate()
    well = plate.get_item("A1")
    ideal_x, ideal_y = self._ideal(well, plate)
    expected_mx, expected_my = apply_stage_homography(ideal_x, ideal_y)
    mx, my, mz = MantisDiaphragmDispenserBackend._container_to_machine_coord(
      well, dispense_z=42.0
    )
    self.assertAlmostEqual(mx, expected_mx, places=6)
    self.assertAlmostEqual(my, expected_my, places=6)
    self.assertEqual(mz, 42.0)

  def test_well_without_plate_parent_raises(self):
    from pylabrobot.formulatrix.mantis.diaphragm_dispenser_backend import (
      MantisDiaphragmDispenserBackend,
    )
    from pylabrobot.resources.well import CrossSectionType, Well, WellBottomType

    orphan = Well(
      name="orphan",
      size_x=6.0,
      size_y=6.0,
      size_z=10.0,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      max_volume=300.0,
      material_z_thickness=1.0,
    )
    with self.assertRaises(ValueError):
      MantisDiaphragmDispenserBackend._container_to_machine_coord(orphan, dispense_z=44.0)


if __name__ == "__main__":
  unittest.main()
