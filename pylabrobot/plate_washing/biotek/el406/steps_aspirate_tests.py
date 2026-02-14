# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Aspirate operations.

This module contains tests for aspirate-related step methods:
- aspirate (M_ASPIRATE)
- strip_aspirate (M_ASPIRATE_STRIP)
"""

import unittest

from pylabrobot.plate_washing.biotek.el406 import (
  BioTekEL406Backend,
)
from pylabrobot.plate_washing.biotek.el406.mock_tests import MockFTDI


class TestEL406BackendAspirate(unittest.IsolatedAsyncioTestCase):
  """Test EL406 aspirate functionality."""

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_aspirate_sends_command(self):
    """Aspirate should send correct command."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_aspirate()
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_aspirate_with_travel_rate(self):
    """Aspirate should accept string travel rate."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_aspirate(travel_rate="5")
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_aspirate_with_cell_wash_rate(self):
    """Aspirate should accept cell wash travel rate."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_aspirate(travel_rate="2 CW")
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_aspirate_validates_travel_rate(self):
    """Aspirate should reject invalid travel rate strings."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(travel_rate="10")
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(travel_rate="5 CW")
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(travel_rate="bad")

  async def test_aspirate_validates_delay_ms(self):
    """Aspirate delay must be 0-5000 ms."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(delay_ms=5001)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(delay_ms=-1)

  async def test_aspirate_validates_vacuum_time(self):
    """Vacuum filtration time must be 5-999 seconds."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(vacuum_filtration=True, vacuum_time_sec=4)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(vacuum_filtration=True, vacuum_time_sec=1000)

  async def test_aspirate_validates_offsets(self):
    """Aspirate should validate X/Y/Z offset ranges."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(offset_x=61)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(offset_x=-61)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(offset_y=41)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(offset_y=-41)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(offset_z=0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(offset_z=211)

  async def test_aspirate_validates_secondary_offsets(self):
    """Secondary aspirate offsets should be validated when enabled."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(secondary_aspirate=True, secondary_x=61)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(secondary_aspirate=True, secondary_y=-41)
    with self.assertRaises(ValueError):
      await self.backend.manifold_aspirate(secondary_aspirate=True, secondary_z=0)

  async def test_aspirate_vacuum_filtration(self):
    """Aspirate with vacuum filtration should send command."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_aspirate(vacuum_filtration=True, vacuum_time_sec=30)
    self.assertGreater(len(self.backend.io.written_data), initial_count)


class TestAspirateCommandEncoding(unittest.TestCase):
  """Test aspirate command binary encoding.

  Wire format (22 bytes):
    [0]     plate type prefix (0x04=96-well)
    [1]     vacuum_filtration
    [2-3]   time_value (delay_ms or vacuum_time_sec) LE
    [4]     travel_rate byte
    [5]     x_offset (signed byte)
    [6]     y_offset (signed byte)
    [7-8]   z_offset LE
    [9]     secondary_mode
    [10]    secondary_x (signed byte)
    [11]    secondary_y (signed byte)
    [12-13] secondary_z LE
    [14-15] reserved
    [16-17] column_mask
    [18-21] padding
  """

  def setUp(self):
    self.backend = BioTekEL406Backend()

  def test_aspirate_command_defaults(self):
    """Default aspirate: no vacuum, rate 3, delay 0, z=30."""
    cmd = self.backend._build_aspirate_command()
    self.assertEqual(len(cmd), 22)
    self.assertEqual(cmd[0], 0x04)
    self.assertEqual(cmd[1], 0)  # no vacuum
    self.assertEqual(cmd[2], 0)  # delay low
    self.assertEqual(cmd[3], 0)  # delay high
    self.assertEqual(cmd[4], 3)  # travel rate "3" -> byte 3
    self.assertEqual(cmd[5], 0)  # x
    self.assertEqual(cmd[6], 0)  # y
    self.assertEqual(cmd[7], 30)  # z low
    self.assertEqual(cmd[8], 0)  # z high
    self.assertEqual(cmd[9], 0)  # secondary mode None
    self.assertEqual(cmd[10], 0)  # sec x
    self.assertEqual(cmd[11], 0)  # sec y
    self.assertEqual(cmd[12], 30)  # sec z low
    self.assertEqual(cmd[13], 0)  # sec z high
    self.assertEqual(cmd[14], 0)  # reserved
    self.assertEqual(cmd[15], 0)  # reserved
    self.assertEqual(cmd[16], 0xFF)  # well mask (all 12 cols)
    self.assertEqual(cmd[17], 0x0F)
    self.assertEqual(cmd[18:22], bytes(4))  # padding

  def test_aspirate_command_vacuum_filtration(self):
    """Vacuum filtration flag at byte 1."""
    cmd = self.backend._build_aspirate_command(vacuum_filtration=True, time_value=30)
    self.assertEqual(cmd[1], 1)
    # time_value=30 at bytes 2-3
    self.assertEqual(cmd[2], 30)
    self.assertEqual(cmd[3], 0)

  def test_aspirate_command_delay_encoding(self):
    """Delay encoded as LE uint16 at bytes 2-3."""
    cmd = self.backend._build_aspirate_command(time_value=5000)
    # 5000 = 0x1388
    self.assertEqual(cmd[2], 0x88)
    self.assertEqual(cmd[3], 0x13)

  def test_aspirate_command_travel_rate(self):
    """Travel rate byte at position 4."""
    # Normal rate "5" -> byte 5
    cmd = self.backend._build_aspirate_command(travel_rate_byte=5)
    self.assertEqual(cmd[4], 5)
    # CW rate "2 CW" -> byte 8
    cmd = self.backend._build_aspirate_command(travel_rate_byte=8)
    self.assertEqual(cmd[4], 8)

  def test_aspirate_command_negative_offset_x(self):
    """X offset at byte 5, signed byte encoding."""
    cmd = self.backend._build_aspirate_command(offset_x=-30)
    # -30 as unsigned byte = 226 = 0xE2
    self.assertEqual(cmd[5], 226)

  def test_aspirate_command_positive_offset_y(self):
    """Y offset at byte 6."""
    cmd = self.backend._build_aspirate_command(offset_y=5)
    self.assertEqual(cmd[6], 5)

  def test_aspirate_command_z_offset(self):
    """Z offset as LE uint16 at bytes 7-8."""
    cmd = self.backend._build_aspirate_command(offset_z=121)
    self.assertEqual(cmd[7], 121)
    self.assertEqual(cmd[8], 0)

  def test_aspirate_command_secondary_mode(self):
    """Secondary mode byte at position 9."""
    cmd = self.backend._build_aspirate_command(secondary_mode=1)
    self.assertEqual(cmd[9], 1)

  def test_aspirate_command_secondary_offsets(self):
    """Secondary X/Y/Z offsets at positions 10-13."""
    cmd = self.backend._build_aspirate_command(
      secondary_x=-5,
      secondary_y=3,
      secondary_z=45,
    )
    # sec_x = -5 -> 0xFB
    self.assertEqual(cmd[10], 0xFB)
    self.assertEqual(cmd[11], 3)
    self.assertEqual(cmd[12], 45)
    self.assertEqual(cmd[13], 0)

  def test_aspirate_command_column_mask_all(self):
    """Column mask at bytes 16-17 is always all-selected for manifold aspirate."""
    cmd = self.backend._build_aspirate_command()
    self.assertEqual(cmd[16], 0xFF)  # all 12 columns
    self.assertEqual(cmd[17], 0x0F)

  def test_aspirate_command_length(self):
    """Aspirate command should be exactly 22 bytes."""
    cmd = self.backend._build_aspirate_command()
    self.assertEqual(len(cmd), 22)


if __name__ == "__main__":
  unittest.main()
