# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Shake operations.

This module contains tests for shake-related step methods:
- shake (SHAKE_SOAK)
"""

import unittest

from pylabrobot.plate_washing.biotek.el406 import ExperimentalBioTekEL406Backend
from pylabrobot.plate_washing.biotek.el406.mock_tests import PT96, EL406TestCase


class TestEL406BackendShake(EL406TestCase):
  """Test EL406 shake functionality."""

  async def test_shake_sends_command(self):
    """Shake should send correct command."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.shake(PT96, duration=10, intensity="Medium")

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_shake_validates_intensity(self):
    """Shake should validate intensity value."""
    with self.assertRaises(ValueError):
      await self.backend.shake(PT96, duration=10, intensity="invalid")

  async def test_shake_validates_both_zero(self):
    """Shake should raise ValueError when both duration and soak_duration are 0."""
    with self.assertRaises(ValueError):
      await self.backend.shake(PT96, duration=0, soak_duration=0)

  async def test_shake_validates_negative_duration(self):
    """Shake should raise ValueError for negative duration."""
    with self.assertRaises(ValueError) as ctx:
      await self.backend.shake(PT96, duration=-5)

    self.assertIn("duration", str(ctx.exception).lower())
    self.assertIn("-5", str(ctx.exception))

  async def test_shake_validates_negative_soak(self):
    """Shake should raise ValueError for negative soak_duration."""
    with self.assertRaises(ValueError):
      await self.backend.shake(PT96, duration=10, soak_duration=-1)

  async def test_shake_validates_duration_exceeds_max(self):
    """Shake should raise ValueError when duration exceeds 3599s (59:59)."""
    with self.assertRaises(ValueError):
      await self.backend.shake(PT96, duration=3600)

  async def test_shake_validates_soak_exceeds_max(self):
    """Shake should raise ValueError when soak_duration exceeds 3599s (59:59)."""
    with self.assertRaises(ValueError):
      await self.backend.shake(PT96, duration=10, soak_duration=3600)

  async def test_shake_soak_only(self):
    """Shake with duration=0 and soak_duration>0 should work (soak only)."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.shake(PT96, duration=0, soak_duration=10)

    self.assertGreater(len(self.backend.io.written_data), initial_count)


class TestShakeCommandEncoding(unittest.TestCase):
  """Test shake command binary encoding."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_shake_command_basic(self):
    """Basic shake: 10 seconds, medium intensity."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=10.0,
      soak_duration=0.0,
      intensity="Medium",
      shake_enabled=True,
    )

    self.assertEqual(cmd[0], 0x04)
    self.assertEqual(cmd[1], 0x01)
    self.assertEqual(cmd[2], 0x0A)
    self.assertEqual(cmd[3], 0x00)
    self.assertEqual(cmd[4], 0x03)
    self.assertEqual(cmd[5], 0x00)
    self.assertEqual(cmd[6], 0x00)
    self.assertEqual(cmd[7], 0x00)
    self.assertEqual(cmd[8:12], bytes([0, 0, 0, 0]))

  def test_shake_command_variable_intensity(self):
    """Variable intensity encoding."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Variable",
      shake_enabled=True,
    )

    self.assertEqual(cmd[4], 0x01)

  def test_shake_command_encoding_durations(self):
    """Verify encoding for various shake durations."""
    cases = [
      (30.0, "04011e000300000000000000"),  # 00:30
      (60.0, "04013c000300000000000000"),  # 01:00
      (300.0, "04012c010300000000000000"),  # 05:00
    ]
    for duration, expected_hex in cases:
      with self.subTest(duration=duration):
        cmd = self.backend._build_shake_command(
          PT96,
          shake_duration=duration,
          soak_duration=0.0,
          intensity="Medium",
          shake_enabled=True,
          move_home_first=True,
        )
        self.assertEqual(cmd, bytes.fromhex(expected_hex))

  def test_shake_command_encoding_shake_disabled(self):
    """Shake disabled should zero the duration."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Medium",
      shake_enabled=False,
      move_home_first=True,
    )

    expected = bytes.fromhex("040100000300000000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_move_home_false(self):
    """Verify encoding with move_home_first=false."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=False,
    )

    expected = bytes.fromhex("04001e000300000000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_soak_30s(self):
    """Verify encoding with 30s soak duration."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=30.0,
      soak_duration=30.0,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04011e0003001e0000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_soak_60s(self):
    """Verify encoding with 60s soak duration."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=30.0,
      soak_duration=60.0,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04011e0003003c0000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_slow_frequency(self):
    """Verify encoding with slow intensity."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Slow",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04011e000200000000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_fast_frequency(self):
    """Verify encoding with fast intensity."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Fast",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04011e000400000000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_complex(self):
    """Verify encoding with combined shake, soak, and slow intensity."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=300.0,
      soak_duration=120.0,
      intensity="Slow",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04012c010200780000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_move_home_false_with_soak(self):
    """Verify encoding with move_home_first=false and soak."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=30.0,
      soak_duration=60.0,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=False,
    )

    expected = bytes.fromhex("04001e0003003c0000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_max_duration_encoding(self):
    """Verify encoding with maximum duration (3599s)."""
    cmd = self.backend._build_shake_command(
      PT96,
      shake_duration=3599,
      soak_duration=3599,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04010f0e03000f0e00000000")
    self.assertEqual(cmd, expected)
