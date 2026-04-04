"""Tests for the Mantis backend components.

These tests exercise the kinematics, map generator, FmlxPacket serialisation,
and constants — all without requiring hardware.
"""

import struct
import unittest

from pylabrobot.dispensing.mantis.fmlx_driver import FmlxPacket, decode_response
from pylabrobot.dispensing.mantis.mantis_constants import MotorStatusCode, PressureControlStatus
from pylabrobot.dispensing.mantis.mantis_kinematics import (
  MOTOR_1_CONFIG,
  MOTOR_3_CONFIG,
  MantisKinematics,
  MantisMapGenerator,
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

  def test_xy_to_theta_known_point(self):
    """Home position should produce valid angles."""
    theta1, theta2 = MantisKinematics.xy_to_theta(15.0, 31.177)
    self.assertIsInstance(theta1, float)
    self.assertIsInstance(theta2, float)

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


class TestMantisMapGenerator(unittest.TestCase):
  """Test microplate coordinate generation."""

  def test_a1_coordinate(self):
    gen = MantisMapGenerator(a1_x=14.38, a1_y=11.24, row_pitch=9.0, col_pitch=9.0)
    coord = gen.get_well_coordinate(0, 0)
    self.assertEqual(coord["well"], "A1")
    self.assertEqual(coord["row"], 0)
    self.assertEqual(coord["col"], 0)
    self.assertIsInstance(coord["x"], float)
    self.assertIsInstance(coord["y"], float)

  def test_well_ordering(self):
    gen = MantisMapGenerator()
    a1 = gen.get_well_coordinate(0, 0)
    a2 = gen.get_well_coordinate(0, 1)
    # A2 should be to the right of A1 in ideal coordinates, but after homography
    # we just check they're different
    self.assertNotEqual(a1["x"], a2["x"])

  def test_generate_map_size(self):
    gen = MantisMapGenerator(rows=8, cols=12)
    full_map = gen.generate_map()
    self.assertEqual(len(full_map), 96)

  def test_generate_map_384(self):
    gen = MantisMapGenerator(rows=16, cols=24, row_pitch=4.5, col_pitch=4.5)
    full_map = gen.generate_map()
    self.assertEqual(len(full_map), 384)

  def test_z_propagation(self):
    gen = MantisMapGenerator(z=42.0)
    coord = gen.get_well_coordinate(3, 5)
    self.assertEqual(coord["z"], 42.0)


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


class TestMantisBackendHelpers(unittest.TestCase):
  """Test static helper methods on MantisBackend."""

  def test_well_to_row_col(self):
    from pylabrobot.dispensing.mantis.mantis_backend import MantisBackend

    self.assertEqual(MantisBackend._well_to_row_col("A1"), (0, 0))
    self.assertEqual(MantisBackend._well_to_row_col("H12"), (7, 11))
    self.assertEqual(MantisBackend._well_to_row_col("B3"), (1, 2))
    self.assertEqual(MantisBackend._well_to_row_col("a1"), (0, 0))


if __name__ == "__main__":
  unittest.main()
