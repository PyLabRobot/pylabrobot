# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Wash operations."""

import unittest

from pylabrobot.plate_washing.biotek.el406 import ExperimentalBioTekEL406Backend
from pylabrobot.plate_washing.biotek.el406.mock_tests import (
  PT96,
  PT384,
  PT384PCR,
  PT1536,
  PT1536F,
  EL406TestCase,
)


class TestEL406BackendWash(EL406TestCase):
  """Test EL406 wash functionality (consolidated wash method)."""

  async def test_wash_sends_command(self):
    """Wash should send correct command."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_wash(PT96, cycles=1, dispense_volume=300.0)

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_wash_validates_cycles(self):
    """Wash should validate cycle count."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, cycles=0)  # Zero cycles

  async def test_wash_validates_buffer(self):
    """Wash should validate buffer selection."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, buffer="Z")

  async def test_wash_validates_dispense_flow_rate(self):
    """Wash should validate dispense flow rate range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, dispense_flow_rate=0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, dispense_flow_rate=10)

  async def test_wash_validates_travel_rate(self):
    """Wash should validate aspirate travel rate range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, aspirate_travel_rate=0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, aspirate_travel_rate=10)

  async def test_wash_validates_pre_dispense_flow_rate(self):
    """Wash should validate pre-dispense flow rate range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, pre_dispense_flow_rate=0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, pre_dispense_flow_rate=10)

  async def test_wash_validates_dispense_x(self):
    """Wash should validate dispense X offset range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, dispense_x=-200)

  async def test_wash_validates_dispense_y(self):
    """Wash should validate dispense Y offset range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, dispense_y=200)

  async def test_wash_with_all_new_params(self):
    """Wash should accept all new parameters."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_wash(
      PT96,
      cycles=3,
      buffer="B",
      dispense_volume=200.0,
      dispense_flow_rate=5,
      dispense_x=10,
      dispense_y=-5,
      dispense_z=200,
      aspirate_travel_rate=5,
      aspirate_z=40,
      pre_dispense_flow_rate=7,
      aspirate_delay=1.0,
      aspirate_x=15,
      aspirate_y=-10,
      final_aspirate=False,
      pre_dispense_volume=100.0,
      vacuum_delay_volume=50.0,
      soak_duration=30,
      shake_duration=10,
      shake_intensity="Fast",
    )
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_wash_validates_aspirate_delay(self):
    """Wash should validate aspirate delay range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, aspirate_delay=-1.0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, aspirate_delay=70.0)

  async def test_wash_validates_aspirate_x(self):
    """Wash should validate aspirate X offset range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, aspirate_x=-200)

  async def test_wash_validates_aspirate_y(self):
    """Wash should validate aspirate Y offset range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, aspirate_y=200)

  async def test_wash_validates_pre_dispense_volume(self):
    """Wash should validate pre-dispense volume (0 or 25-3000)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, pre_dispense_volume=10.0)  # Below 25
    # 0 should be allowed (disabled)
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_wash(PT96, pre_dispense_volume=0.0)
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_wash_validates_vacuum_delay_volume(self):
    """Wash should validate vacuum delay volume range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, vacuum_delay_volume=-1.0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, vacuum_delay_volume=4000.0)

  async def test_wash_validates_soak_duration(self):
    """Wash should validate soak duration range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, soak_duration=-1)
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, soak_duration=4000)

  async def test_wash_validates_shake_duration(self):
    """Wash should validate shake duration range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, shake_duration=-1)
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, shake_duration=4000)

  async def test_wash_validates_shake_intensity(self):
    """Wash should validate shake intensity string."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, shake_intensity="InvalidIntensity")


class TestWashCompositeCommandEncoding(unittest.TestCase):
  """Test wash composite command encoding."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_composite_command_length(self):
    """Composite wash command should produce the expected payload length."""
    cmd = self.backend._build_wash_composite_command(PT96)
    self.assertEqual(len(cmd), 102)

  def test_composite_command_aspirate_sections(self):
    """Aspirate sections should encode travel rate and Z offsets."""
    cmd = self.backend._build_wash_composite_command(PT96, aspirate_travel_rate=5, aspirate_z=40)
    # Aspirate section 1 (final aspirate, mirrors primary Z)
    self.assertEqual(cmd[29], 5)  # travel rate (propagated)
    self.assertEqual(cmd[32], 0x28)  # Z low (40)
    self.assertEqual(cmd[33], 0x00)  # Z high
    self.assertEqual(cmd[37], 0x28)  # secondary Z low (mirrors primary)
    self.assertEqual(cmd[38], 0x00)  # secondary Z high

    # Aspirate section 2 (primary aspirate, user params)
    self.assertEqual(cmd[49], 0x00)  # leading padding
    self.assertEqual(cmd[50], 5)  # travel rate
    self.assertEqual(cmd[53], 0x28)  # Z low (40)
    self.assertEqual(cmd[58], 0x1D)  # secondary Z low (default 29, independent)

  def test_composite_command_final_section(self):
    """Final section should have shake intensity at the expected position."""
    cmd = self.backend._build_wash_composite_command(PT96, aspirate_travel_rate=3)
    self.assertEqual(cmd[90], 3)  # shake intensity (default Medium=3)
    self.assertEqual(cmd[91], 0x00)

  def test_composite_command_final_aspirate_flag(self):
    """Final aspirate flag should be encoded in the header."""
    cmd_on = self.backend._build_wash_composite_command(PT96, final_aspirate=True)
    self.assertEqual(cmd_on[2], 0x01)

    cmd_off = self.backend._build_wash_composite_command(PT96, final_aspirate=False)
    self.assertEqual(cmd_off[2], 0x00)

  def test_composite_command_pre_dispense_volume(self):
    """Pre-dispense volume should be encoded in both dispense sections."""
    cmd = self.backend._build_wash_composite_command(PT96, pre_dispense_volume=100.0)
    # Dispense1
    self.assertEqual(cmd[15], 0x64)  # 100 low
    self.assertEqual(cmd[16], 0x00)  # 100 high
    # Dispense2
    self.assertEqual(cmd[76], 0x64)
    self.assertEqual(cmd[77], 0x00)

  def test_composite_command_vacuum_delay_volume(self):
    """Vacuum delay volume should be encoded in both dispense sections."""
    cmd = self.backend._build_wash_composite_command(PT96, vacuum_delay_volume=200.0)
    # Dispense1
    self.assertEqual(cmd[18], 0xC8)  # 200 low
    self.assertEqual(cmd[19], 0x00)  # 200 high
    # Dispense2
    self.assertEqual(cmd[79], 0xC8)
    self.assertEqual(cmd[80], 0x00)

  def test_composite_command_aspirate_delay(self):
    """Final aspirate section should always have delay=0."""
    cmd = self.backend._build_wash_composite_command(PT96, aspirate_delay_ms=1000)
    self.assertEqual(cmd[30], 0x00)
    self.assertEqual(cmd[31], 0x00)

  def test_composite_command_aspirate_offsets(self):
    """Aspirate X/Y offsets should only appear in the primary aspirate section."""
    cmd = self.backend._build_wash_composite_command(PT96, aspirate_x=15, aspirate_y=-10)
    # Final aspirate: X/Y fixed at 0
    self.assertEqual(cmd[34], 0x00)
    self.assertEqual(cmd[35], 0x00)
    # Primary aspirate
    self.assertEqual(cmd[51], 15)
    self.assertEqual(cmd[52], 0xF6)  # -10 two's complement

  def test_composite_command_shake_duration(self):
    """Shake duration should be encoded correctly."""
    cmd = self.backend._build_wash_composite_command(PT96, shake_duration=30)
    self.assertEqual(cmd[88], 30)
    self.assertEqual(cmd[89], 0x00)

  def test_composite_command_shake_intensity(self):
    """Shake intensity should be encoded correctly for each level."""
    cmd_fast = self.backend._build_wash_composite_command(
      PT96, shake_duration=10, shake_intensity="Fast"
    )
    self.assertEqual(cmd_fast[90], 0x04)

    cmd_slow = self.backend._build_wash_composite_command(
      PT96, shake_duration=10, shake_intensity="Slow"
    )
    self.assertEqual(cmd_slow[90], 0x02)

    cmd_var = self.backend._build_wash_composite_command(
      PT96, shake_duration=10, shake_intensity="Variable"
    )
    self.assertEqual(cmd_var[90], 0x01)

  def test_composite_command_shake_intensity_default_when_disabled(self):
    """Shake intensity should stay at default when shake_duration=0."""
    cmd = self.backend._build_wash_composite_command(PT96, shake_duration=0, shake_intensity="Fast")
    self.assertEqual(cmd[90], 0x03)

  def test_composite_command_soak_duration(self):
    """Soak duration should be encoded correctly."""
    cmd = self.backend._build_wash_composite_command(PT96, soak_duration=90)
    self.assertEqual(cmd[92], 90)
    self.assertEqual(cmd[93], 0x00)

  def test_composite_command_soak_duration_large(self):
    """Large soak duration should encode correctly as 16-bit LE."""
    cmd = self.backend._build_wash_composite_command(PT96, soak_duration=3599)
    self.assertEqual(cmd[92], 0x0F)
    self.assertEqual(cmd[93], 0x0E)

  def test_composite_command_all_new_params(self):
    """All new parameters set to non-default values should produce correct output."""
    cmd = self.backend._build_wash_composite_command(
      PT96,
      cycles=5,
      buffer="B",
      dispense_volume=500.0,
      dispense_flow_rate=5,
      dispense_x=10,
      dispense_y=-5,
      dispense_z=200,
      aspirate_travel_rate=5,
      aspirate_z=40,
      pre_dispense_flow_rate=7,
      aspirate_delay_ms=2000,
      aspirate_x=-20,
      aspirate_y=15,
      final_aspirate=False,
      pre_dispense_volume=150.0,
      vacuum_delay_volume=100.0,
      soak_duration=60,
      shake_duration=30,
      shake_intensity="Slow",
    )
    self.assertEqual(len(cmd), 102)
    # Header
    self.assertEqual(cmd[2], 0x00)  # final_aspirate=False
    self.assertEqual(cmd[4], 0x0F)  # sector_mask low
    self.assertEqual(cmd[5], 0x00)  # sector_mask high
    self.assertEqual(cmd[6], 5)  # cycles
    # Dispense1
    self.assertEqual(cmd[15], 150 & 0xFF)  # pre_dispense_volume
    self.assertEqual(cmd[16], 0x00)
    self.assertEqual(cmd[17], 7)  # pre_dispense_flow_rate
    self.assertEqual(cmd[18], 100)  # vacuum_delay_volume
    self.assertEqual(cmd[19], 0x00)
    # Final aspirate uses fixed defaults
    self.assertEqual(cmd[30], 0x00)  # delay always 0
    self.assertEqual(cmd[31], 0x00)
    self.assertEqual(cmd[34], 0x00)  # X fixed 0
    self.assertEqual(cmd[35], 0x00)  # Y fixed 0
    # Primary aspirate
    self.assertEqual(cmd[51], 0xEC)  # x=-20 two's complement
    self.assertEqual(cmd[52], 15)  # y=15
    # Shake/soak
    self.assertEqual(cmd[88], 30)  # shake duration
    self.assertEqual(cmd[89], 0x00)
    self.assertEqual(cmd[90], 0x02)  # Slow
    self.assertEqual(cmd[92], 60)  # soak duration
    self.assertEqual(cmd[93], 0x00)


class TestWashMoveHomeFirst(unittest.TestCase):
  """Test move_home_first parameter in wash command."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_move_home_default_disabled(self):
    """move_home_first should default to False."""
    cmd = self.backend._build_wash_composite_command(PT96)
    self.assertEqual(cmd[87], 0x00)

  def test_move_home_enabled(self):
    """move_home_first=True should set the move-home flag."""
    cmd = self.backend._build_wash_composite_command(PT96, move_home_first=True)
    self.assertEqual(cmd[87], 0x01)

  def test_move_home_does_not_affect_other_bytes(self):
    """Enabling move_home_first should only change one byte."""
    cmd_off = self.backend._build_wash_composite_command(PT96, move_home_first=False)
    cmd_on = self.backend._build_wash_composite_command(PT96, move_home_first=True)
    # Only byte [87] should differ
    diffs = [i for i in range(102) if cmd_off[i] != cmd_on[i]]
    self.assertEqual(diffs, [87])

  def test_move_home_with_shake_and_soak(self):
    """move_home_first should coexist with shake/soak parameters."""
    cmd = self.backend._build_wash_composite_command(
      PT96, move_home_first=True, shake_duration=15, shake_intensity="Fast", soak_duration=45
    )
    self.assertEqual(cmd[87], 0x01)  # move_home
    self.assertEqual(cmd[88], 15)  # shake duration low
    self.assertEqual(cmd[89], 0x00)  # shake duration high
    self.assertEqual(cmd[90], 0x04)  # Fast intensity
    self.assertEqual(cmd[92], 45)  # soak duration low
    self.assertEqual(cmd[93], 0x00)  # soak duration high


class TestWashSecondaryAspirate(unittest.TestCase):
  """Test secondary aspirate parameters in wash command."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_secondary_aspirate_disabled_default(self):
    """Secondary aspirate offsets should use defaults when disabled."""
    cmd = self.backend._build_wash_composite_command(PT96, aspirate_z=40)
    # Final aspirate: sec_z mirrors final_asp_z
    self.assertEqual(cmd[37], 0x28)  # secondary Z = 40
    self.assertEqual(cmd[38], 0x00)
    self.assertEqual(cmd[39], 0x00)  # secondary mode disabled
    # Primary aspirate: sec_z = default 29, mode = 0
    self.assertEqual(cmd[58], 0x1D)  # secondary Z = 29 (default)
    self.assertEqual(cmd[59], 0x00)
    self.assertEqual(cmd[55], 0x00)  # secondary mode disabled

  def test_secondary_aspirate_enabled(self):
    """When secondary_aspirate=True, primary aspirate gets secondary Z and mode enabled."""
    cmd = self.backend._build_wash_composite_command(
      PT96, aspirate_z=40, secondary_aspirate=True, secondary_z=100
    )
    # Final aspirate: secondary mode stays off by default
    self.assertEqual(cmd[32], 0x28)  # primary Z = 40
    self.assertEqual(cmd[34], 0x00)  # secondary mode off
    self.assertEqual(cmd[37], 0x28)  # secondary Z mirrors final_asp_z, not secondary_z
    # Primary aspirate: user params
    self.assertEqual(cmd[53], 0x28)  # primary Z = 40
    self.assertEqual(cmd[54], 0x00)
    self.assertEqual(cmd[55], 0x01)  # secondary mode enabled
    self.assertEqual(cmd[58], 0x64)  # secondary Z = 100
    self.assertEqual(cmd[59], 0x00)


class TestWashPreDispenseFlowRateEncoding(unittest.TestCase):
  """Test pre_dispense_flow_rate encoding in wash command."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_pre_dispense_flow_rate_encoding(self):
    """pre_dispense_flow_rate should encode at correct positions."""
    cmd = self.backend._build_wash_composite_command(PT96, pre_dispense_flow_rate=7)
    self.assertEqual(cmd[17], 7)  # Dispense1
    self.assertEqual(cmd[78], 7)  # Dispense2


class TestWashSecondaryXY(unittest.TestCase):
  """Test secondary aspirate X/Y parameters in wash command."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_secondary_xy_default_zero(self):
    """Secondary X/Y should default to 0 and not affect baseline output."""
    cmd = self.backend._build_wash_composite_command(PT96)
    # Final aspirate: always fixed 0
    self.assertEqual(cmd[40], 0x00)  # secondary X
    self.assertEqual(cmd[41], 0x00)  # secondary Y
    # Primary aspirate
    self.assertEqual(cmd[56], 0x00)  # secondary X
    self.assertEqual(cmd[57], 0x00)  # secondary Y

  def test_secondary_xy_encoded_when_enabled(self):
    """Secondary X/Y should be encoded in primary aspirate when secondary_aspirate=True."""
    cmd = self.backend._build_wash_composite_command(
      PT96, secondary_aspirate=True, secondary_x=15, secondary_y=-10, secondary_z=50
    )
    # Final aspirate: always fixed 0
    self.assertEqual(cmd[40], 0x00)
    self.assertEqual(cmd[41], 0x00)
    # Primary aspirate
    self.assertEqual(cmd[56], 15)
    self.assertEqual(cmd[57], 0xF6)  # -10 two's complement

  def test_secondary_xy_zero_when_disabled(self):
    """Secondary X/Y should be 0 when secondary_aspirate=False, even if values set."""
    cmd = self.backend._build_wash_composite_command(
      PT96, secondary_aspirate=False, secondary_x=15, secondary_y=-10
    )
    self.assertEqual(cmd[40], 0x00)  # final aspirate (always fixed)
    self.assertEqual(cmd[41], 0x00)
    self.assertEqual(cmd[56], 0x00)  # primary aspirate secondary X
    self.assertEqual(cmd[57], 0x00)  # primary aspirate secondary Y


class TestWashBottomWash(unittest.TestCase):
  """Test bottom wash parameters in wash command."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_bottom_wash_disabled_dispense1_mirrors_main(self):
    """When bottom_wash=False, Dispense1 should mirror main dispense volume/flow."""
    cmd = self.backend._build_wash_composite_command(
      PT96, dispense_volume=500.0, dispense_flow_rate=5
    )
    # Dispense1
    self.assertEqual(cmd[8], 0xF4)  # 500 low
    self.assertEqual(cmd[9], 0x01)  # 500 high
    self.assertEqual(cmd[10], 5)  # flow rate
    # Dispense2
    self.assertEqual(cmd[69], 0xF4)  # 500 low
    self.assertEqual(cmd[70], 0x01)  # 500 high
    self.assertEqual(cmd[71], 5)  # flow rate

  def test_bottom_wash_enabled_dispense1_uses_bottom_params(self):
    """When bottom_wash=True, Dispense1 should use bottom wash volume/flow."""
    cmd = self.backend._build_wash_composite_command(
      PT96,
      dispense_volume=300.0,
      dispense_flow_rate=7,
      bottom_wash=True,
      bottom_wash_volume=200.0,
      bottom_wash_flow_rate=5,
    )
    # Dispense1: bottom wash params
    self.assertEqual(cmd[8], 0xC8)  # 200 low
    self.assertEqual(cmd[9], 0x00)  # 200 high
    self.assertEqual(cmd[10], 5)  # bottom wash flow rate
    # Dispense2: main params
    self.assertEqual(cmd[69], 0x2C)  # 300 low
    self.assertEqual(cmd[70], 0x01)  # 300 high
    self.assertEqual(cmd[71], 7)  # main flow rate


class TestWashBottomWashValidation(EL406TestCase):
  """Test bottom wash parameter validation."""

  async def test_bottom_wash_validates_volume(self):
    """Bottom wash should validate volume range (25-3000)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, bottom_wash=True, bottom_wash_volume=10.0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, bottom_wash=True, bottom_wash_volume=0.0)

  async def test_bottom_wash_validates_flow_rate(self):
    """Bottom wash should validate flow rate range."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(
        PT96, bottom_wash=True, bottom_wash_volume=200.0, bottom_wash_flow_rate=0
      )

  async def test_bottom_wash_sends_command(self):
    """Bottom wash should send a command successfully."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_wash(
      PT96, bottom_wash=True, bottom_wash_volume=200.0, bottom_wash_flow_rate=5
    )
    self.assertGreater(len(self.backend.io.written_data), initial_count)


class TestWashPreDispenseBetweenCycles(unittest.TestCase):
  """Test pre-dispense between cycles parameters in wash command."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_midcyc_disabled_dispense2_uses_main_pre_dispense(self):
    """When midcyc volume=0, Dispense2 pre-dispense mirrors main pre-dispense."""
    cmd = self.backend._build_wash_composite_command(
      PT96, pre_dispense_volume=100.0, pre_dispense_flow_rate=7
    )
    # Dispense1
    self.assertEqual(cmd[15], 100)
    self.assertEqual(cmd[16], 0x00)
    self.assertEqual(cmd[17], 7)
    # Dispense2
    self.assertEqual(cmd[76], 100)
    self.assertEqual(cmd[77], 0x00)
    self.assertEqual(cmd[78], 7)

  def test_midcyc_enabled_dispense2_uses_midcyc_values(self):
    """When midcyc volume>0, Dispense2 pre-dispense uses midcyc values."""
    cmd = self.backend._build_wash_composite_command(
      PT96,
      pre_dispense_volume=100.0,
      pre_dispense_flow_rate=7,
      pre_dispense_between_cycles_volume=50.0,
      pre_dispense_between_cycles_flow_rate=5,
    )
    # Dispense1: main pre-dispense values
    self.assertEqual(cmd[15], 100)
    self.assertEqual(cmd[16], 0x00)
    self.assertEqual(cmd[17], 7)
    # Dispense2: midcyc values override
    self.assertEqual(cmd[76], 50)
    self.assertEqual(cmd[77], 0x00)
    self.assertEqual(cmd[78], 5)


class TestWashPreDispenseBetweenCyclesValidation(EL406TestCase):
  """Test pre-dispense between cycles validation."""

  async def test_midcyc_validates_volume(self):
    """Pre-dispense between cycles should validate volume (0 or 25-3000)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, pre_dispense_between_cycles_volume=10.0)

  async def test_midcyc_validates_flow_rate(self):
    """Pre-dispense between cycles should validate flow rate."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(
        PT96, pre_dispense_between_cycles_volume=50.0, pre_dispense_between_cycles_flow_rate=0
      )

  async def test_midcyc_sends_command(self):
    """Pre-dispense between cycles should send a command successfully."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_wash(
      PT96, pre_dispense_between_cycles_volume=50.0, pre_dispense_between_cycles_flow_rate=9
    )
    self.assertGreater(len(self.backend.io.written_data), initial_count)


class TestWashCaptureVectors(unittest.TestCase):
  """Byte-exact match against reference captures from real EL406 hardware."""

  def setUp(self):
    self.backend = ExperimentalBioTekEL406Backend()

  def test_baseline(self):
    """Baseline wash: 3 cycles, flow 7, Z=121, travel 3, asp Z=29."""
    expected = bytes.fromhex(
      "040001000f0003412c01070000790000000900000000000000000000000300001d000000001d"
      "0000000000000000000000000300001d000000001d000000000000000000412c010700007900"
      "0000090000000000000000000000030000000000000000000000"
    )
    cmd = self.backend._build_wash_composite_command(PT96)
    self.assertEqual(cmd, expected)

  def test_aspirate_xyz_capture(self):
    """Aspirate with Z=28, X=5, Y=10."""
    expected = bytes.fromhex(
      "040001000f0003412c01070000790000000900000000000000000000000300001c000000001c"
      "00000000000000000000000003050a1c000000001d000000000000000000412c010700007900"
      "0000090000000000000000000000030000000000000000000000"
    )
    cmd = self.backend._build_wash_composite_command(
      PT96, aspirate_z=28, aspirate_x=5, aspirate_y=10
    )
    self.assertEqual(cmd, expected)

  def test_secondary_aspirate_capture(self):
    """Secondary aspirate enabled."""
    expected = bytes.fromhex(
      "040001000f0003412c01070000790000000900000000000000000000000300001d000000001d"
      "0000000000000000000000000300001d000100001d000000000000000000412c010700007900"
      "0000090000000000000000000000030000000000000000000000"
    )
    cmd = self.backend._build_wash_composite_command(PT96, secondary_aspirate=True)
    self.assertEqual(cmd, expected)

  def test_final_secondary_aspirate_capture(self):
    """Final secondary aspirate."""
    expected = bytes.fromhex(
      "040001000f0002412c010700007900000009000000000000000000000003"
      "00001d000100002800000000000000000000000003"
      "00001d000000001d000000000000000000"
      "412c0107000079000000090000000000000000000000"
      "030000000000000000000000"
    )
    cmd = self.backend._build_wash_composite_command(
      PT96, cycles=2, buffer="A", final_secondary_aspirate=True, final_secondary_z=40
    )
    self.assertEqual(cmd, expected)

  def test_bottom_wash_capture(self):
    """Bottom wash with vol=200, flow=5."""
    expected = bytes.fromhex(
      "040101000f000341c800050000790000000900000000000000000000000300001d000000001d"
      "0000000000000000000000000300001d000000001d000000000000000000412c010700007900"
      "0000090000000000000000000000030000000000000000000000"
    )
    cmd = self.backend._build_wash_composite_command(
      PT96, bottom_wash=True, bottom_wash_volume=200.0, bottom_wash_flow_rate=5
    )
    self.assertEqual(cmd, expected)

  def test_pre_dispense_between_cycles_capture(self):
    """Pre-dispense between cycles with vol=50."""
    expected = bytes.fromhex(
      "040001000f0003412c01070000790000000900000000000000000000000300001d000000001d"
      "0000000000000000000000000300001d000000001d000000000000000000412c010700007900"
      "3200090000000000000000000000030000000000000000000000"
    )
    cmd = self.backend._build_wash_composite_command(
      PT96, pre_dispense_between_cycles_volume=50.0, pre_dispense_between_cycles_flow_rate=9
    )
    self.assertEqual(cmd, expected)

  def test_aspirate_delay_capture(self):
    """Aspirate delay on 384-well plate."""
    capture_hex = (
      "010001000f0003"
      "4164000700007800000009000000000000000000020003000016000000001600000000000000"
      "000000010003000016000000001600000000000000000041640007000078000000090000"
      "000000000000000000030000000000000000000000"
    )
    expected = bytes.fromhex(capture_hex)
    cmd = self.backend._build_wash_composite_command(
      PT384,
      cycles=3,
      sector_mask=0x0F,
      buffer="A",
      dispense_volume=100.0,
      dispense_flow_rate=7,
      dispense_z=120,
      aspirate_travel_rate=3,
      aspirate_z=22,
      pre_dispense_flow_rate=9,
      aspirate_delay_ms=1,
      final_aspirate_delay_ms=2,
      secondary_z=22,
    )
    self.assertEqual(cmd, expected)

  def test_p384_sector_plate_format_capture(self):
    """384-well sector wash with different sector masks and formats."""
    # Plate format, sector_mask=0x0E (Q2+Q3+Q4), 1 cycle
    cap0 = bytes.fromhex(
      "010001000e00014164000700007800000009000000000000000000000003000016000000001600"
      "000000000000000000000003000016000000001600000000000000000041640007000078000000"
      "090000000000000000000000030000000000000000000000"
    )
    cmd0 = self.backend._build_wash_composite_command(
      PT384,
      cycles=1,
      sector_mask=0x0E,
      buffer="A",
      dispense_volume=100.0,
      dispense_flow_rate=7,
      dispense_z=120,
      aspirate_travel_rate=3,
      aspirate_z=22,
      pre_dispense_flow_rate=9,
      secondary_z=22,
    )
    self.assertEqual(cmd0, cap0)

    # Sector format, sector_mask=0x0F, 1 cycle
    cap2 = bytes.fromhex(
      "010001010f00014164000700007800000009000000000000000000000003000016000000001600"
      "000000000000000000000003000016000000001600000000000000000041640007000078000000"
      "090000000000000000000000030000000000000000000000"
    )
    cmd2 = self.backend._build_wash_composite_command(
      PT384,
      cycles=1,
      sector_mask=0x0F,
      buffer="A",
      dispense_volume=100.0,
      dispense_flow_rate=7,
      dispense_z=120,
      aspirate_travel_rate=3,
      aspirate_z=22,
      pre_dispense_flow_rate=9,
      secondary_z=22,
      wash_format="Sector",
    )
    self.assertEqual(cmd2, cap2)


class TestWash384WellPlateSupport(unittest.TestCase):
  """Test 384-well plate support: plate_type prefix, wash_format, sector_mask."""

  def test_384_well_plate_type_byte(self):
    """384-well backend should produce the correct plate type prefix."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT384)
    self.assertEqual(cmd[0], 0x01)

  def test_96_well_plate_type_byte(self):
    """96-well backend (default) should produce the correct plate type prefix."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT96)
    self.assertEqual(cmd[0], 0x04)

  def test_wash_format_plate_default(self):
    """Default wash_format='Plate' should encode as 0."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT96)
    self.assertEqual(cmd[3], 0x00)

  def test_wash_format_sector(self):
    """wash_format='Sector' should encode as 1."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT96, wash_format="Sector")
    self.assertEqual(cmd[3], 0x01)

  def test_cycles_at_byte6(self):
    """cycles should be encoded at the expected position."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT96, cycles=5)
    self.assertEqual(cmd[6], 5)

  def test_cycles_default(self):
    """Default cycles=3 should be encoded correctly."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT96)
    self.assertEqual(cmd[6], 3)

  def test_sector_mask_le_encoding(self):
    """Sector mask should be encoded as 16-bit LE."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT96, sector_mask=0x0E)
    self.assertEqual(cmd[4], 0x0E)
    self.assertEqual(cmd[5], 0x00)

  def test_384_well_full_combination(self):
    """384-well with Sector format and custom sector mask."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(
      PT384, wash_format="Sector", cycles=1, sector_mask=0x0E, aspirate_travel_rate=3
    )
    self.assertEqual(cmd[0], 0x01)  # plate type
    self.assertEqual(cmd[3], 0x01)  # wash format = Sector
    self.assertEqual(cmd[4], 0x0E)  # sector mask low
    self.assertEqual(cmd[5], 0x00)  # sector mask high
    self.assertEqual(cmd[6], 1)  # cycles
    self.assertEqual(cmd[29], 3)  # final aspirate travel rate
    self.assertEqual(cmd[50], 3)  # primary aspirate travel rate
    self.assertEqual(len(cmd), 102)


class TestWash384WellValidation(EL406TestCase):
  """Test validation of 384-well parameters in wash() API."""

  async def test_wash_format_invalid(self):
    """wash() should reject invalid wash_format values."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, wash_format="Invalid")

  async def test_sectors_invalid(self):
    """wash() should reject out-of-range sector/quadrant values."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, sectors=[0])  # Must be 1-4
    with self.assertRaises(ValueError):
      await self.backend.manifold_wash(PT96, sectors=[5])  # Must be 1-4

  async def test_wash_with_384_params_sends_command(self):
    """wash() should accept and send command with 384-well params."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_wash(PT96, wash_format="Sector", sectors=[2, 3, 4], cycles=1)
    self.assertGreater(len(self.backend.io.written_data), initial_count)


class TestWashPlateTypeDefaults(unittest.TestCase):
  """Test plate-type-aware defaults for wash parameters."""

  def test_96_well_defaults(self):
    """96-well plate should use 96-well defaults (300uL, dispZ=121, aspZ=29)."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT96)
    # dispense_volume=300
    self.assertEqual(cmd[8], 0x2C)
    self.assertEqual(cmd[9], 0x01)
    # dispense_z=121
    self.assertEqual(cmd[13], 0x79)
    self.assertEqual(cmd[14], 0x00)
    # aspirate_z=29
    self.assertEqual(cmd[53], 0x1D)
    self.assertEqual(cmd[54], 0x00)
    # secondary_z=29
    self.assertEqual(cmd[58], 0x1D)
    self.assertEqual(cmd[59], 0x00)

  def test_384_well_defaults(self):
    """384-well plate should use 384-well defaults (100uL, dispZ=120, aspZ=22)."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT384)
    self.assertEqual(cmd[0], 0x01)  # plate type
    # dispense_volume=100
    self.assertEqual(cmd[8], 0x64)
    self.assertEqual(cmd[9], 0x00)
    # dispense_z=120
    self.assertEqual(cmd[13], 0x78)
    self.assertEqual(cmd[14], 0x00)
    # aspirate_z=22
    self.assertEqual(cmd[53], 0x16)
    self.assertEqual(cmd[54], 0x00)
    # secondary_z=22
    self.assertEqual(cmd[58], 0x16)
    self.assertEqual(cmd[59], 0x00)
    # Dispense2 mirrors
    self.assertEqual(cmd[69], 0x64)  # vol low
    self.assertEqual(cmd[74], 0x78)  # disp_z low

  def test_384_pcr_defaults(self):
    """384 PCR plate should use its specific defaults (100uL, dispZ=83, aspZ=2)."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT384PCR)
    self.assertEqual(cmd[0], 0x02)  # plate type
    self.assertEqual(cmd[8], 0x64)  # vol=100 low
    self.assertEqual(cmd[13], 0x53)  # dispense_z=83
    self.assertEqual(cmd[53], 0x02)  # aspirate_z=2
    self.assertEqual(cmd[58], 0x02)  # secondary_z=2

  def test_1536_well_defaults(self):
    """1536-well plate should use its specific defaults."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT1536)
    self.assertEqual(cmd[0], 0x00)  # plate type
    # dispense_volume=100
    self.assertEqual(cmd[8], 0x64)
    self.assertEqual(cmd[9], 0x00)
    # dispense_z=94
    self.assertEqual(cmd[13], 0x5E)
    self.assertEqual(cmd[14], 0x00)
    # aspirate_z=42
    self.assertEqual(cmd[53], 0x2A)
    self.assertEqual(cmd[54], 0x00)

  def test_1536_flange_defaults(self):
    """1536 flange plate should use its specific defaults."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT1536F)
    self.assertEqual(cmd[0], 0x0E)  # plate type
    # dispense_volume=100
    self.assertEqual(cmd[8], 0x64)
    self.assertEqual(cmd[9], 0x00)
    # dispense_z=93
    self.assertEqual(cmd[13], 0x5D)
    self.assertEqual(cmd[14], 0x00)
    # aspirate_z=13
    self.assertEqual(cmd[53], 0x0D)
    self.assertEqual(cmd[54], 0x00)

  def test_explicit_values_override_plate_defaults(self):
    """Explicit parameter values should override plate-type defaults."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(
      PT384, dispense_volume=500.0, dispense_z=200, aspirate_z=50, secondary_z=30
    )
    # dispense_volume=500 overrides 100
    self.assertEqual(cmd[8], 0xF4)  # 500 low
    self.assertEqual(cmd[9], 0x01)  # 500 high
    # dispense_z=200 overrides 120
    self.assertEqual(cmd[13], 0xC8)  # 200
    # aspirate_z=50 overrides 22
    self.assertEqual(cmd[53], 0x32)  # 50
    # secondary_z=30 overrides 22
    self.assertEqual(cmd[58], 0x1E)  # 30

  def test_secondary_z_independent_of_aspirate_z(self):
    """secondary_z default should be plate-type default, NOT user aspirate_z."""
    backend = ExperimentalBioTekEL406Backend()
    cmd = backend._build_wash_composite_command(PT96, aspirate_z=40)
    # aspirate_z=40 (user override)
    self.assertEqual(cmd[53], 0x28)  # aspirate_z = 40
    # secondary_z should still be 29 (plate-type default), NOT 40
    self.assertEqual(cmd[58], 0x1D)  # secondary_z = 29

  def test_all_plate_types_produce_102_bytes(self):
    """Every plate type should produce exactly 102 bytes with defaults."""
    backend = ExperimentalBioTekEL406Backend()
    plate_types = [PT96, PT384, PT384PCR, PT1536, PT1536F]
    expected_prefixes = {
      "test_96": 0x04,
      "test_384": 0x01,
      "test_384_pcr": 0x02,
      "test_1536": 0x00,
      "test_1536_flange": 0x0E,
    }
    for plate in plate_types:
      cmd = backend._build_wash_composite_command(plate)
      self.assertEqual(len(cmd), 102, f"Wrong length for {plate.name}")
      self.assertEqual(cmd[0], expected_prefixes[plate.name], f"Wrong prefix for {plate.name}")
