"""Tests for BioTek EL406 plate washer backend - Helper functions.

This module contains tests for Helper functions.
"""

import unittest

# Import the backend module (mock is already installed by test_el406_mock import)
from pylabrobot.plate_washing.biotek.el406 import (
  BioTekEL406Backend,
)
from pylabrobot.plate_washing.biotek.el406.helpers import encode_column_mask


class TestHelperFunctions(unittest.TestCase):
  """Test helper functions for encoding."""

  def setUp(self):
    self.backend = BioTekEL406Backend()

  def test_encode_volume_little_endian(self):
    """Volume should be encoded as little-endian 2 bytes."""
    # Test helper method if it exists, otherwise test via command building
    cmd = self.backend._build_dispense_command(
      volume=1000.0,
      buffer="A",
      flow_rate=5,
      offset_x=0,
      offset_y=0,
      offset_z=100,
    )
    # Current format: [0]=0x04, [1]=buffer, [2-3]=volume
    # 1000 = 0x03E8, little-endian = [0xE8, 0x03]
    self.assertEqual(cmd[2], 0xE8)
    self.assertEqual(cmd[3], 0x03)

  def test_encode_signed_byte_positive(self):
    """Positive offset should encode correctly."""
    cmd = self.backend._build_aspirate_command(
      time_value=1000,
      travel_rate_byte=3,
      offset_x=50,
      offset_y=30,
      offset_z=29,
    )
    # Current format: [5]=offset_x, [6]=offset_y (signed bytes)
    self.assertEqual(cmd[5], 50)
    self.assertEqual(cmd[6], 30)

  def test_encode_signed_byte_negative(self):
    """Negative offset should encode as two's complement."""
    cmd = self.backend._build_aspirate_command(
      time_value=1000,
      travel_rate_byte=3,
      offset_x=-30,
      offset_y=-50,
      offset_z=29,
    )
    # Current format: [5]=offset_x, [6]=offset_y (signed bytes)
    # -30 as unsigned byte: 256 - 30 = 226 = 0xE2
    self.assertEqual(cmd[5], 226)
    # -50 as unsigned byte: 256 - 50 = 206 = 0xCE
    self.assertEqual(cmd[6], 206)


class TestColumnMaskEncoding(unittest.TestCase):
  """Test column mask encoding helper function.

  Column mask encodes 48 column selections into 6 bytes (48 bits).
  - columns is a list of column indices (0-47)
  - Each index sets the corresponding bit to 1
  - Bytes are in little-endian order
  """

  def test_encode_column_mask_none_returns_all_ones(self):
    """encode_column_mask(None) should return all 1s (all wells selected)."""

    mask = encode_column_mask(None)

    self.assertEqual(len(mask), 6)
    # All 48 bits set = 6 bytes of 0xFF
    self.assertEqual(mask, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))

  def test_encode_column_mask_empty_list_returns_all_zeros(self):
    """encode_column_mask([]) should return all 0s (no wells selected)."""

    mask = encode_column_mask([])

    self.assertEqual(len(mask), 6)
    self.assertEqual(mask, bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))

  def test_encode_column_mask_single_well_0(self):
    """encode_column_mask([0]) should set bit 0 only."""

    mask = encode_column_mask([0])

    # Well 0 = bit 0 = 0b00000001 = 0x01 in byte 0
    self.assertEqual(mask[0], 0x01)
    self.assertEqual(mask[1:], bytes([0x00, 0x00, 0x00, 0x00, 0x00]))

  def test_encode_column_mask_single_well_7(self):
    """encode_column_mask([7]) should set bit 7 only."""

    mask = encode_column_mask([7])

    # Well 7 = bit 7 = 0b10000000 = 0x80 in byte 0
    self.assertEqual(mask[0], 0x80)
    self.assertEqual(mask[1:], bytes([0x00, 0x00, 0x00, 0x00, 0x00]))

  def test_encode_column_mask_single_well_8(self):
    """encode_column_mask([8]) should set bit 0 in byte 1."""

    mask = encode_column_mask([8])

    # Well 8 = bit 8 = bit 0 of byte 1 = 0x01
    self.assertEqual(mask[0], 0x00)
    self.assertEqual(mask[1], 0x01)
    self.assertEqual(mask[2:], bytes([0x00, 0x00, 0x00, 0x00]))

  def test_encode_column_mask_single_well_47(self):
    """encode_column_mask([47]) should set bit 7 in byte 5."""

    mask = encode_column_mask([47])

    # Well 47 = bit 47 = bit 7 of byte 5 = 0x80
    self.assertEqual(mask[:5], bytes([0x00, 0x00, 0x00, 0x00, 0x00]))
    self.assertEqual(mask[5], 0x80)

  def test_encode_column_mask_multiple_wells(self):
    """encode_column_mask with multiple wells should set multiple bits."""

    # Wells 0, 1, 2, 3 = bits 0-3 in byte 0 = 0b00001111 = 0x0F
    mask = encode_column_mask([0, 1, 2, 3])

    self.assertEqual(mask[0], 0x0F)
    self.assertEqual(mask[1:], bytes([0x00, 0x00, 0x00, 0x00, 0x00]))

  def test_encode_column_mask_wells_in_different_bytes(self):
    """encode_column_mask with wells spanning multiple bytes."""

    # Wells 0 (byte 0, bit 0), 8 (byte 1, bit 0), 16 (byte 2, bit 0)
    mask = encode_column_mask([0, 8, 16])

    self.assertEqual(mask[0], 0x01)
    self.assertEqual(mask[1], 0x01)
    self.assertEqual(mask[2], 0x01)
    self.assertEqual(mask[3:], bytes([0x00, 0x00, 0x00]))

  def test_encode_column_mask_all_48_wells(self):
    """encode_column_mask with all 48 wells should return all 1s."""

    all_wells = list(range(48))
    mask = encode_column_mask(all_wells)

    self.assertEqual(mask, bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))

  def test_encode_column_mask_first_row_96_plate(self):
    """encode_column_mask for first row of 96-well plate (wells 0-11)."""

    # For 48-well selection, first 12 wells would be wells 0-11
    mask = encode_column_mask(list(range(12)))

    # Wells 0-7 = byte 0 = 0xFF
    # Wells 8-11 = bits 0-3 of byte 1 = 0x0F
    self.assertEqual(mask[0], 0xFF)
    self.assertEqual(mask[1], 0x0F)
    self.assertEqual(mask[2:], bytes([0x00, 0x00, 0x00, 0x00]))

  def test_encode_column_mask_out_of_range_raises(self):
    """encode_column_mask should raise ValueError for well index >= 48."""

    with self.assertRaises(ValueError) as ctx:
      encode_column_mask([48])

    self.assertIn("48", str(ctx.exception))

  def test_encode_column_mask_negative_raises(self):
    """encode_column_mask should raise ValueError for negative well index."""

    with self.assertRaises(ValueError) as ctx:
      encode_column_mask([-1])

    self.assertIn("-1", str(ctx.exception))

  def test_encode_column_mask_duplicate_wells_handled(self):
    """encode_column_mask should handle duplicate column indices."""

    # Duplicates should just set the same bit twice (no effect)
    mask = encode_column_mask([0, 0, 0])

    self.assertEqual(mask[0], 0x01)
    self.assertEqual(mask[1:], bytes([0x00, 0x00, 0x00, 0x00, 0x00]))

  def test_encode_column_mask_unsorted_wells(self):
    """encode_column_mask should handle unsorted column indices."""

    # Order shouldn't matter
    mask = encode_column_mask([3, 0, 2, 1])

    self.assertEqual(mask[0], 0x0F)
    self.assertEqual(mask[1:], bytes([0x00, 0x00, 0x00, 0x00, 0x00]))
