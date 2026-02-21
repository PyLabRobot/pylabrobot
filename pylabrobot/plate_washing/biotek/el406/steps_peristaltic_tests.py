# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Peristaltic pump operations.

This module contains tests for peristaltic pump-related step methods:
- peristaltic_dispense (P_DISPENSE)
- peristaltic_purge (P_PURGE)
"""

import unittest

from pylabrobot.plate_washing.biotek.el406 import (
  BioTekEL406Backend,
  EL406PlateType,
)
from pylabrobot.plate_washing.biotek.el406.mock_tests import MockFTDI


class TestEL406BackendPeristalticDispense(unittest.IsolatedAsyncioTestCase):
  """Test EL406 peristaltic dispense functionality.

  The peristaltic dispense operation (ePDispense = 1) uses the peristaltic pump
  to dispense liquid to wells. This is different from manifold dispense (M_DISPENSE).
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_peristaltic_dispense_sends_command(self):
    """peristaltic_dispense should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.peristaltic_dispense(volume=300.0, flow_rate="Medium")

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_peristaltic_dispense_validates_volume(self):
    """peristaltic_dispense should validate volume is positive."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_dispense(volume=0.0)

    with self.assertRaises(ValueError):
      await self.backend.peristaltic_dispense(volume=-100.0)

  async def test_peristaltic_dispense_accepts_various_flow_rates(self):
    """peristaltic_dispense should accept all valid flow rate strings."""
    for fr in ["Low", "Medium", "High"]:
      self.backend.io.set_read_buffer(b"\x06" * 100)
      await self.backend.peristaltic_dispense(volume=300.0, flow_rate=fr)

  async def test_peristaltic_dispense_raises_when_device_not_initialized(self):
    """peristaltic_dispense should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.peristaltic_dispense(volume=300.0)

  async def test_peristaltic_dispense_with_pre_dispense_volume(self):
    """peristaltic_dispense should accept optional pre-dispense volume."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.peristaltic_dispense(
      volume=300.0,
      flow_rate="Medium",
      pre_dispense_volume=50.0,
    )
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_peristaltic_dispense_with_offsets(self):
    """peristaltic_dispense should accept X, Y, Z offsets."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.peristaltic_dispense(
      volume=300.0,
      flow_rate="Medium",
      offset_x=10,
      offset_y=-5,
      offset_z=336,
    )
    self.assertGreater(len(self.backend.io.written_data), initial_count)


class TestPeristalticDispenseCommandEncoding(unittest.TestCase):
  """Test peristaltic dispense command binary encoding.

  Protocol format for peristaltic dispense (P_DISPENSE = 1):
    [0]   Step type: 0x01 (P_DISPENSE)
    [1-2] Volume: 2 bytes, little-endian, in uL
    [3]   Buffer valve: A=0, B=1, C=2, D=3
    [4]   Cassette type: byte (default 0)
    [5]   Offset X: signed byte (-128 to +127)
    [6]   Offset Y: signed byte (-128 to +127)
    [7-8] Offset Z: 2 bytes, little-endian
    [9-10] Prime volume: 2 bytes, little-endian
    [11]  Flow rate: 1-9
  """

  def setUp(self):
    self.backend = BioTekEL406Backend()

  def test_peristaltic_dispense_step_type(self):
    """Peristaltic dispense command should have step type prefix 0x04."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
    )

    self.assertEqual(cmd[0], 0x04)

  def test_peristaltic_dispense_volume_encoding(self):
    """Peristaltic dispense should encode volume as little-endian 2 bytes."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
    )

    # Volume: 300 uL = 0x012C little-endian = [0x2C, 0x01]
    self.assertEqual(cmd[1], 0x2C)
    self.assertEqual(cmd[2], 0x01)

  def test_peristaltic_dispense_volume_1000ul(self):
    """Peristaltic dispense with 1000 uL."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=1000.0,
      flow_rate=5,
    )

    # Volume: 1000 uL = 0x03E8 little-endian = [0xE8, 0x03]
    self.assertEqual(cmd[1], 0xE8)
    self.assertEqual(cmd[2], 0x03)

  def test_peristaltic_dispense_flow_rate_at_byte3(self):
    """Peristaltic dispense flow rate should be at byte 3."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
    )

    self.assertEqual(cmd[3], 5)

  def test_peristaltic_dispense_cassette_at_byte4(self):
    """Peristaltic dispense cassette type should be at byte 4."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      cassette="5uL",
    )

    # 5uL cassette = 2
    self.assertEqual(cmd[4], 2)

  def test_peristaltic_dispense_offset_z(self):
    """Peristaltic dispense should encode Z offset as little-endian 2 bytes."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      offset_z=336,
    )

    # Offset Z: 336 = 0x0150 little-endian = [0x50, 0x01]
    self.assertEqual(cmd[7], 0x50)
    self.assertEqual(cmd[8], 0x01)

  def test_peristaltic_dispense_offset_x_positive(self):
    """Peristaltic dispense should encode positive X offset at byte 5."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      offset_x=50,
    )

    self.assertEqual(cmd[5], 50)

  def test_peristaltic_dispense_offset_x_negative(self):
    """Peristaltic dispense should encode negative X offset as two's complement at byte 5."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      offset_x=-30,
    )

    # -30 as unsigned byte = 256 - 30 = 226 = 0xE2
    self.assertEqual(cmd[5], 226)

  def test_peristaltic_dispense_offset_y_negative(self):
    """Peristaltic dispense should encode negative Y offset as two's complement at byte 6."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      offset_y=-20,
    )

    # -20 as unsigned byte = 256 - 20 = 236 = 0xEC
    self.assertEqual(cmd[6], 236)

  def test_peristaltic_dispense_pre_dispense_volume(self):
    """Peristaltic dispense should encode prime volume as little-endian 2 bytes."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      pre_dispense_volume=50.0,
    )

    # Prime volume: 50 uL = 0x0032 little-endian = [0x32, 0x00]
    self.assertEqual(cmd[9], 0x32)
    self.assertEqual(cmd[10], 0x00)

  def test_peristaltic_dispense_num_pre_dispenses_default(self):
    """Peristaltic dispense should encode default num_pre_dispenses (2) at byte 11."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=7,
    )

    # num_pre_dispenses default: 2
    self.assertEqual(cmd[11], 2)

  def test_peristaltic_dispense_num_pre_dispenses_1(self):
    """Peristaltic dispense should encode num_pre_dispenses=1 at byte 11."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=1,
      num_pre_dispenses=1,
    )

    self.assertEqual(cmd[11], 1)

  def test_peristaltic_dispense_num_pre_dispenses_5(self):
    """Peristaltic dispense should encode num_pre_dispenses=5 at byte 11."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=9,
      num_pre_dispenses=5,
    )

    self.assertEqual(cmd[11], 5)

  def test_peristaltic_dispense_full_command(self):
    """Test complete peristaltic dispense command with all parameters.

    Wire format:
      [0]     plate type prefix (0x04=96-well) (step type marker)
      [1-2]   Volume: 2 bytes, little-endian, in uL
      [3]     Flow rate
      [4]     Offset X: signed byte
      [5]     Offset Y: signed byte
      [6]     Reserved
      [7-8]   Offset Z: 2 bytes, little-endian
      [9-10]  Pre-dispense volume: 2 bytes, little-endian
      [11]    Number of pre-dispenses
      [12-17] Well mask: 6 bytes
      [18]    Reserved
      [19]    Quadrant
    """
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=500.0,
      flow_rate=7,
      offset_x=10,
      offset_y=-5,
      offset_z=400,
      pre_dispense_volume=25.0,
    )

    self.assertEqual(cmd[0], 0x04)  # Step type prefix
    self.assertEqual(cmd[1], 0xF4)  # Volume low byte (500 = 0x01F4)
    self.assertEqual(cmd[2], 0x01)  # Volume high byte
    self.assertEqual(cmd[3], 7)  # Flow rate
    self.assertEqual(cmd[4], 0)  # Cassette (default Any=0)
    self.assertEqual(cmd[5], 10)  # Offset X
    self.assertEqual(cmd[6], 251)  # Offset Y (-5 as unsigned = 251)
    self.assertEqual(cmd[7], 0x90)  # Offset Z low byte (400 = 0x0190)
    self.assertEqual(cmd[8], 0x01)  # Offset Z high byte
    self.assertEqual(cmd[9], 25)  # Pre-dispense volume low byte
    self.assertEqual(cmd[10], 0)  # Pre-dispense volume high byte
    self.assertEqual(cmd[11], 2)  # Number of pre-dispenses (default)


class TestPeristalticDispenseColumnsAndRows(unittest.IsolatedAsyncioTestCase):
  """Test peristaltic_dispense with columns and rows parameters."""

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_peristaltic_dispense_accepts_columns(self):
    """peristaltic_dispense should accept columns parameter (1-indexed)."""
    await self.backend.peristaltic_dispense(
      volume=300.0,
      flow_rate="Medium",
      columns=[1, 2, 3, 4],
    )
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_peristaltic_dispense_accepts_all_columns_96well(self):
    """peristaltic_dispense should accept columns 1-12 for 96-well plate."""
    await self.backend.peristaltic_dispense(
      volume=300.0,
      flow_rate="Medium",
      columns=list(range(1, 13)),
    )
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_peristaltic_dispense_validates_column_range_96well(self):
    """peristaltic_dispense should reject column 0 and 13 for 96-well plate."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_dispense(
        volume=300.0,
        columns=[0],  # Invalid — 1-indexed
      )
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_dispense(
        volume=300.0,
        columns=[13],  # Invalid — max 12 for 96-well
      )

  async def test_peristaltic_dispense_none_columns_means_all(self):
    """peristaltic_dispense with columns=None should dispense to all columns."""
    await self.backend.peristaltic_dispense(
      volume=300.0,
      flow_rate="Medium",
      columns=None,
    )
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_peristaltic_dispense_validates_row_range_96well(self):
    """peristaltic_dispense should reject row > 1 for 96-well plate."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_dispense(
        volume=300.0,
        rows=[2],  # Invalid — only 1 row group for 96-well
      )

  async def test_peristaltic_dispense_accepts_row_1_96well(self):
    """peristaltic_dispense should accept row 1 for 96-well plate."""
    await self.backend.peristaltic_dispense(
      volume=300.0,
      flow_rate="Medium",
      rows=[1],
    )
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_peristaltic_dispense_default_z_96well(self):
    """peristaltic_dispense should default offset_z to 336 for 96-well."""
    # offset_z=None → uses plate_type_default_z → 336 for 96-well
    await self.backend.peristaltic_dispense(
      volume=300.0,
      flow_rate="Medium",
    )
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_peristaltic_dispense_columns_and_rows(self):
    """peristaltic_dispense should accept both columns and rows."""
    await self.backend.peristaltic_dispense(
      volume=300.0,
      flow_rate="Medium",
      columns=[1, 3, 5],
      rows=[1],
    )
    self.assertGreater(len(self.backend.io.written_data), 0)


class TestPeristalticDispenseCommandEncodingWithMasks(unittest.TestCase):
  """Test peristaltic dispense command encoding with well and row masks.

  Protocol format:
    [0]     plate type prefix (0x04=96-well)
    [1-2]   Volume (LE)
    [3]     Flow rate (0=Low, 1=Med, 2=High)
    [4]     Cassette type
    [5]     Offset X (signed byte)
    [6]     Offset Y (signed byte)
    [7-8]   Offset Z (LE)
    [9-10]  Pre-dispense volume (LE)
    [11]    Num pre-dispenses
    [12-17] Well mask: 6 bytes (48 bits, 1=selected)
    [18]    Row mask: 1 byte (4 bits, INVERTED: 0=selected)
    [19]    Pump (1=Primary, 2=Secondary)
  """

  def setUp(self):
    self.backend = BioTekEL406Backend()

  def test_peristaltic_dispense_command_with_column_mask_length(self):
    """Command with well mask should be 24 bytes."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      column_mask=[0, 1, 2, 3],
    )
    self.assertEqual(len(cmd), 24)

  def test_peristaltic_dispense_command_column_mask_encoding(self):
    """Command should correctly encode well mask at bytes 12-17."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      column_mask=[0, 1, 2, 3],
    )

    # Wells 0-3 = bits 0-3 of byte 12 = 0x0F
    self.assertEqual(cmd[12], 0x0F)
    self.assertEqual(cmd[13:18], bytes([0x00, 0x00, 0x00, 0x00, 0x00]))

  def test_peristaltic_dispense_command_pump_at_byte19(self):
    """Pump should be at byte 19 (1=Primary, 2=Secondary)."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      pump=2,  # Secondary
    )
    self.assertEqual(cmd[19], 2)

  def test_peristaltic_dispense_command_none_column_mask_all_wells(self):
    """Command with None column_mask should encode all wells (0xFF * 6)."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      column_mask=None,
    )
    self.assertEqual(cmd[12:18], bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))

  def test_peristaltic_dispense_command_default_row_mask(self):
    """Default rows=None should encode 0x00 (all selected, inverted)."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
    )
    self.assertEqual(cmd[18], 0x00)

  def test_peristaltic_dispense_command_default_pump(self):
    """Default pump should be 1 (Primary)."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
    )
    self.assertEqual(cmd[19], 1)

  def test_peristaltic_dispense_command_empty_column_mask(self):
    """Command with empty column_mask should encode no wells (0x00 * 6)."""
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      column_mask=[],
    )
    self.assertEqual(cmd[12:18], bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))

  def test_peristaltic_dispense_command_rows_inverted_encoding(self):
    """Row mask uses inverted encoding: 0=selected, 1=deselected."""
    # Use 1536-well plate type which supports 4 row groups
    self.backend.plate_type = EL406PlateType.PLATE_1536_WELL
    # Select rows 1 and 2 → bits 0,1 cleared, bits 2,3 set → 0x0C
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      rows=[1, 2],
    )
    self.assertEqual(cmd[18], 0x0C)

  def test_peristaltic_dispense_command_complex_column_mask(self):
    """Command with complex well mask spanning multiple bytes."""
    # Wells 0, 8, 16, 24, 32, 40 = bit 0 of each of the 6 bytes
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=300.0,
      flow_rate=5,
      column_mask=[0, 8, 16, 24, 32, 40],
    )

    self.assertEqual(cmd[12], 0x01)
    self.assertEqual(cmd[13], 0x01)
    self.assertEqual(cmd[14], 0x01)
    self.assertEqual(cmd[15], 0x01)
    self.assertEqual(cmd[16], 0x01)
    self.assertEqual(cmd[17], 0x01)

  def test_peristaltic_dispense_command_both_masks(self):
    """Command with column_mask and rows."""
    # Use 1536-well plate type which supports 4 row groups
    self.backend.plate_type = EL406PlateType.PLATE_1536_WELL
    cmd = self.backend._build_peristaltic_dispense_command(
      volume=500.0,
      flow_rate=7,
      column_mask=[0, 47],  # First and last wells
      rows=[1, 2, 3, 4],  # All rows selected
      pump=2,
    )

    self.assertEqual(cmd[0], 0x00)  # 1536-well plate type
    self.assertEqual(cmd[3], 7)  # Flow rate

    # Well mask: well 0 = bit 0 of byte 12, well 47 = bit 7 of byte 17
    self.assertEqual(cmd[12], 0x01)
    self.assertEqual(cmd[13:17], bytes([0x00, 0x00, 0x00, 0x00]))
    self.assertEqual(cmd[17], 0x80)

    # Row mask: all 4 rows selected → inverted = 0x00
    self.assertEqual(cmd[18], 0x00)
    # Pump at byte 19
    self.assertEqual(cmd[19], 2)


class TestEL406BackendPeristalticPurge(unittest.IsolatedAsyncioTestCase):
  """Test EL406 peristaltic purge functionality.

  The peristaltic purge operation uses the peristaltic pump to expel/clear
  liquid from the fluid lines. This is used for cleaning or changing buffers.

  Current API:
    peristaltic_purge(volume, flow_rate="High", cassette="Any")
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_peristaltic_purge_sends_command(self):
    """peristaltic_purge should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.peristaltic_purge(volume=1000.0, flow_rate="High")

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_peristaltic_purge_validates_volume(self):
    """peristaltic_purge should validate volume range (1-3000 µL)."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_purge(volume=0.0)
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_purge(volume=-100.0)
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_purge(volume=3001.0)

  async def test_peristaltic_purge_accepts_volume_boundaries(self):
    """peristaltic_purge should accept volume at boundaries (1, 3000)."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.peristaltic_purge(volume=1.0)
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.peristaltic_purge(volume=3000.0)

  async def test_peristaltic_purge_validates_duration(self):
    """peristaltic_purge should validate duration range (1-300 seconds)."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_purge(duration=0)
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_purge(duration=-1)
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_purge(duration=301)

  async def test_peristaltic_purge_accepts_duration_boundaries(self):
    """peristaltic_purge should accept duration at boundaries (1, 300)."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.peristaltic_purge(duration=1)
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.peristaltic_purge(duration=300)

  async def test_peristaltic_purge_rejects_both_volume_and_duration(self):
    """peristaltic_purge should reject both volume and duration specified."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_purge(volume=100.0, duration=10)

  async def test_peristaltic_purge_validates_flow_rate(self):
    """peristaltic_purge should validate flow rate is Low/Medium/High."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_purge(volume=1000.0, flow_rate="Invalid")

  async def test_peristaltic_purge_raises_when_device_not_initialized(self):
    """peristaltic_purge should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.peristaltic_purge(volume=1000.0)

  async def test_peristaltic_purge_accepts_all_flow_rates(self):
    """peristaltic_purge should accept flow rates Low, Medium, High."""
    for flow_rate in ["Low", "Medium", "High"]:
      self.backend.io.set_read_buffer(b"\x06" * 100)
      # Should not raise
      await self.backend.peristaltic_purge(volume=500.0, flow_rate=flow_rate)

  async def test_peristaltic_purge_default_flow_rate(self):
    """peristaltic_purge should use default flow rate High."""
    await self.backend.peristaltic_purge(volume=1000.0)

    # Verify command was sent
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_peristaltic_purge_raises_on_timeout(self):
    """peristaltic_purge should raise TimeoutError when device does not respond."""
    self.backend.io.set_read_buffer(b"")  # No ACK response
    with self.assertRaises(TimeoutError):
      await self.backend.peristaltic_purge(volume=1000.0)


if __name__ == "__main__":
  unittest.main()
