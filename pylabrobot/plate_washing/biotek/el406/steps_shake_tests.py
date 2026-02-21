# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Shake operations.

This module contains tests for shake-related step methods:
- shake (SHAKE_SOAK)
"""

import unittest

from pylabrobot.plate_washing.biotek.el406 import (
  BioTekEL406Backend,
)
from pylabrobot.plate_washing.biotek.el406.mock_tests import MockFTDI


class TestEL406BackendShake(unittest.IsolatedAsyncioTestCase):
  """Test EL406 shake functionality."""

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_shake_sends_command(self):
    """Shake should send correct command."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.shake(duration=10, intensity="Medium")

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_shake_validates_intensity(self):
    """Shake should validate intensity value."""
    with self.assertRaises(ValueError):
      await self.backend.shake(duration=10, intensity="invalid")

  async def test_shake_validates_both_zero(self):
    """Shake should raise ValueError when both duration and soak_duration are 0."""
    with self.assertRaises(ValueError):
      await self.backend.shake(duration=0, soak_duration=0)

  async def test_shake_validates_negative_duration(self):
    """Shake should raise ValueError for negative duration."""
    with self.assertRaises(ValueError) as ctx:
      await self.backend.shake(duration=-5)

    self.assertIn("duration", str(ctx.exception).lower())
    self.assertIn("-5", str(ctx.exception))

  async def test_shake_validates_negative_soak(self):
    """Shake should raise ValueError for negative soak_duration."""
    with self.assertRaises(ValueError):
      await self.backend.shake(duration=10, soak_duration=-1)

  async def test_shake_validates_duration_exceeds_max(self):
    """Shake should raise ValueError when duration exceeds 3599s (59:59)."""
    with self.assertRaises(ValueError):
      await self.backend.shake(duration=3600)

  async def test_shake_validates_soak_exceeds_max(self):
    """Shake should raise ValueError when soak_duration exceeds 3599s (59:59)."""
    with self.assertRaises(ValueError):
      await self.backend.shake(duration=10, soak_duration=3600)

  async def test_shake_soak_only(self):
    """Shake with duration=0 and soak_duration>0 should work (soak only)."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.shake(duration=0, soak_duration=10)

    self.assertGreater(len(self.backend.io.written_data), initial_count)


class TestShakeCommandEncoding(unittest.TestCase):
  """Test shake command binary encoding.

  Wire format for shake/soak (12 bytes with plate type prefix (0x04=96-well)):
    Byte structure:
      [0]      plate type prefix (0x04=96-well) (step type marker, required for all step data)
      [1]      (move_home_first AND shake_enabled): 0x00 or 0x01
      [2-3]    Shake duration in TOTAL SECONDS (16-bit little-endian)
      [4]      Frequency/intensity: 0x02=Slow, 0x03=Medium, 0x04=Fast
      [5]      Reserved: always 0x00
      [6-7]    Soak duration in TOTAL SECONDS (16-bit little-endian)
      [8-11]   Padding/reserved: 4 bytes (0x00)

  Field mapping:
    - move_home_first (bool) → byte[1]: combined with shake_enabled for byte[1]
    - shake_enabled (bool) → byte[1]: combined with move_home_first for byte[1]
    - shake duration (total seconds) → bytes[2-3]: 16-bit LE total seconds
    - frequency → byte[4]: (Slow=0x02, Medium=0x03, Fast=0x04)
    - soak duration (total seconds) → bytes[6-7]: 16-bit LE total seconds
  """

  def setUp(self):
    self.backend = BioTekEL406Backend()

  def test_shake_command_basic(self):
    """Basic shake: 10 seconds, medium intensity."""
    cmd = self.backend._build_shake_command(
      shake_duration=10.0,
      soak_duration=0.0,
      intensity="Medium",
      shake_enabled=True,
    )

    # byte[0]: plate type prefix (0x04=96-well)
    self.assertEqual(cmd[0], 0x04)
    # byte[1]: (move_home_first AND shake_enabled) = 0x01
    self.assertEqual(cmd[1], 0x01)
    # bytes[2-3]: shake duration = 10 seconds (0x0a, 0x00 little-endian)
    self.assertEqual(cmd[2], 0x0A)
    self.assertEqual(cmd[3], 0x00)
    # byte[4]: intensity = medium = 0x03
    self.assertEqual(cmd[4], 0x03)
    # byte[5]: reserved = 0x00
    self.assertEqual(cmd[5], 0x00)
    # bytes[6-7]: soak duration = 0
    self.assertEqual(cmd[6], 0x00)
    self.assertEqual(cmd[7], 0x00)
    # bytes[8-11]: padding
    self.assertEqual(cmd[8:12], bytes([0, 0, 0, 0]))

  def test_shake_command_variable_intensity(self):
    """Variable intensity maps to 0x01."""
    cmd = self.backend._build_shake_command(
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Variable",
      shake_enabled=True,
    )

    self.assertEqual(cmd[4], 0x01)  # variable = 0x01

  def test_shake_command_encoding_durations(self):
    """Verify encoding for various shake durations (medium intensity, move_home=True)."""
    cases = [
      (30.0, "04011e000300000000000000"),  # 00:30
      (60.0, "04013c000300000000000000"),  # 01:00
      (300.0, "04012c010300000000000000"),  # 05:00
    ]
    for duration, expected_hex in cases:
      with self.subTest(duration=duration):
        cmd = self.backend._build_shake_command(
          shake_duration=duration,
          soak_duration=0.0,
          intensity="Medium",
          shake_enabled=True,
          move_home_first=True,
        )
        self.assertEqual(cmd, bytes.fromhex(expected_hex))

  def test_shake_command_encoding_shake_disabled(self):
    """Verify encoding: shake_enabled=false with move_home_first=true.

    Wire format: 04 01 00 00 03 00 00 00 00 00 00 00
    - byte[1]=0x01 because move_home_first=True
    - bytes[2-3]=0x0000 because shake_enabled=False (duration=0)
    """
    cmd = self.backend._build_shake_command(
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Medium",
      shake_enabled=False,
      move_home_first=True,
    )

    # byte[1] = move_home_first (0x01), bytes[2-3] = 0 (shake disabled)
    expected = bytes.fromhex("040100000300000000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_move_home_false(self):
    """Verify encoding: move_home_first=false.

    shake_enabled=true, move_home_first=false -> 001e000300000000000000
    Note: byte[1]=0x00 because (false AND true) = false
    Wire format adds plate type prefix (0x04=96-well): 04001e000300000000000000
    """
    cmd = self.backend._build_shake_command(
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=False,
    )

    expected = bytes.fromhex("04001e000300000000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_soak_30s(self):
    """Verify encoding: soak_duration=00:30.

    shake_duration="00:30", soak_duration="00:30" -> 011e0003001e0000000000
    Wire format adds plate type prefix (0x04=96-well): 04011e0003001e0000000000
    """
    cmd = self.backend._build_shake_command(
      shake_duration=30.0,
      soak_duration=30.0,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04011e0003001e0000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_soak_60s(self):
    """Verify encoding: soak_duration=01:00.

    shake_duration="00:30", soak_duration="01:00" -> 011e0003003c0000000000
    Wire format adds plate type prefix (0x04=96-well): 04011e0003003c0000000000
    """
    cmd = self.backend._build_shake_command(
      shake_duration=30.0,
      soak_duration=60.0,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04011e0003003c0000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_slow_frequency(self):
    """Verify encoding: frequency=Slow (3.5 Hz).

    shake_duration="00:30", frequency="Slow (3.5 Hz)" -> 011e000200000000000000
    Wire format adds plate type prefix (0x04=96-well): 04011e000200000000000000
    """
    cmd = self.backend._build_shake_command(
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Slow",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04011e000200000000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_fast_frequency(self):
    """Verify encoding: frequency=Fast (8 Hz).

    shake_duration="00:30", frequency="Fast (8 Hz)" -> 011e000400000000000000
    Wire format adds plate type prefix (0x04=96-well): 04011e000400000000000000
    """
    cmd = self.backend._build_shake_command(
      shake_duration=30.0,
      soak_duration=0.0,
      intensity="Fast",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04011e000400000000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_complex(self):
    """Verify encoding: complex combination.

    shake_duration="05:00", frequency=Slow, soak_duration="02:00"
    -> 012c010200780000000000
    shake = 300s = 0x012c, slow = 0x02, soak = 120s = 0x0078
    Wire format adds plate type prefix (0x04=96-well): 04012c010200780000000000
    """
    cmd = self.backend._build_shake_command(
      shake_duration=300.0,
      soak_duration=120.0,
      intensity="Slow",
      shake_enabled=True,
      move_home_first=True,
    )

    expected = bytes.fromhex("04012c010200780000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_encoding_move_home_false_with_soak(self):
    """Verify encoding: move_home_first=false with soak.

    shake_enabled=true, move_home_first=false, soak_duration="01:00"
    -> 001e0003003c0000000000
    Wire format adds plate type prefix (0x04=96-well): 0400 1e0003003c0000000000
    """
    cmd = self.backend._build_shake_command(
      shake_duration=30.0,
      soak_duration=60.0,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=False,
    )

    expected = bytes.fromhex("04001e0003003c0000000000")
    self.assertEqual(cmd, expected)

  def test_shake_command_max_duration_encoding(self):
    """Max duration (3599s = 59:59) encodes as 0x0E0F LE."""
    cmd = self.backend._build_shake_command(
      shake_duration=3599,
      soak_duration=3599,
      intensity="Medium",
      shake_enabled=True,
      move_home_first=True,
    )

    # 3599 = 0x0E0F -> low=0x0F, high=0x0E
    self.assertEqual(cmd[2], 0x0F)  # shake low
    self.assertEqual(cmd[3], 0x0E)  # shake high
    self.assertEqual(cmd[6], 0x0F)  # soak low
    self.assertEqual(cmd[7], 0x0E)  # soak high
    # Full match against expected encoding
    expected = bytes.fromhex("04010f0e03000f0e00000000")
    self.assertEqual(cmd, expected)


if __name__ == "__main__":
  unittest.main()
