# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Prime operations.

This module contains tests for prime-related step methods:
- prime (P_PRIME)
- manifold_prime (M_PRIME)
- syringe_prime (S_PRIME)
- auto_clean (M_AUTO_CLEAN)
- strip_prime (M_PRIME_STRIP)
"""

import unittest

from pylabrobot.plate_washing.biotek.el406 import (
  BioTekEL406Backend,
)
from pylabrobot.plate_washing.biotek.el406.mock_tests import MockFTDI


class TestEL406BackendPeristalticPrime(unittest.IsolatedAsyncioTestCase):
  """Test EL406 peristaltic prime functionality."""

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)  # Multiple ACKs

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_peristaltic_prime_sends_correct_command(self):
    """Peristaltic prime should send correct step type and parameters."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.peristaltic_prime(volume=1000.0)

    # Verify a command was sent
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_peristaltic_prime_validates_flow_rate(self):
    """Peristaltic prime should validate flow rate selection."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_prime(flow_rate="Invalid")  # Invalid flow rate

  async def test_peristaltic_prime_validates_volume(self):
    """Peristaltic prime should validate volume range (1-3000 µL)."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_prime(volume=-100.0)
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_prime(volume=0.0)
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_prime(volume=3001.0)

  async def test_peristaltic_prime_accepts_volume_boundaries(self):
    """Peristaltic prime should accept volume at boundaries (1, 3000)."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.peristaltic_prime(volume=1.0)
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.peristaltic_prime(volume=3000.0)

  async def test_peristaltic_prime_validates_duration(self):
    """Peristaltic prime should validate duration range (1-300 seconds)."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_prime(duration=0)
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_prime(duration=-1)
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_prime(duration=301)

  async def test_peristaltic_prime_accepts_duration_boundaries(self):
    """Peristaltic prime should accept duration at boundaries (1, 300)."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.peristaltic_prime(duration=1)
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.peristaltic_prime(duration=300)

  async def test_peristaltic_prime_rejects_both_volume_and_duration(self):
    """Peristaltic prime should reject both volume and duration specified."""
    with self.assertRaises(ValueError):
      await self.backend.peristaltic_prime(volume=100.0, duration=10)


class TestEL406BackendSyringePrime(unittest.IsolatedAsyncioTestCase):
  """Test EL406 syringe prime functionality."""

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_syringe_prime_sends_command(self):
    """syringe_prime should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.syringe_prime(volume=5000.0, syringe="A", flow_rate=5)
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_syringe_prime_validates_volume_too_low(self):
    """syringe_prime should reject volume below 80 uL."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=79.0, syringe="A")

  async def test_syringe_prime_validates_volume_too_high(self):
    """syringe_prime should reject volume above 9999 uL."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=10000.0, syringe="A")

  async def test_syringe_prime_validates_volume_negative(self):
    """syringe_prime should reject negative volume."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=-100.0, syringe="A")

  async def test_syringe_prime_accepts_volume_boundaries(self):
    """syringe_prime should accept volume at boundaries (80, 9999)."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.syringe_prime(volume=80.0, syringe="A")
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.syringe_prime(volume=9999.0, syringe="A")

  async def test_syringe_prime_validates_syringe(self):
    """syringe_prime should validate syringe selection."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="Z")
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="C")

  async def test_syringe_prime_validates_flow_rate(self):
    """syringe_prime should validate flow rate (1-5)."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A", flow_rate=0)
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A", flow_rate=6)

  async def test_syringe_prime_validates_pump_delay(self):
    """syringe_prime should validate pump delay (0-5000 ms)."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A", pump_delay=-1)
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A", pump_delay=5001)

  async def test_syringe_prime_validates_refills(self):
    """syringe_prime should validate refills (1-255)."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A", refills=0)
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A", refills=256)

  async def test_syringe_prime_validates_submerge_duration(self):
    """syringe_prime should validate submerge duration (0-1439 min)."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A", submerge_duration=-1)
    with self.assertRaises(ValueError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A", submerge_duration=1440)

  async def test_syringe_prime_raises_when_device_not_initialized(self):
    """syringe_prime should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    with self.assertRaises(RuntimeError):
      await backend.syringe_prime(volume=5000.0, syringe="A")

  async def test_syringe_prime_accepts_flow_rate_range(self):
    """syringe_prime should accept flow rates 1-5."""
    for flow_rate in range(1, 6):
      self.backend.io.set_read_buffer(b"\x06" * 100)
      await self.backend.syringe_prime(volume=5000.0, syringe="A", flow_rate=flow_rate)

  async def test_syringe_prime_with_refills(self):
    """syringe_prime should accept refills parameter."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.syringe_prime(
      volume=5000.0,
      syringe="A",
      flow_rate=5,
      refills=3,
    )
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_syringe_prime_default_values(self):
    """syringe_prime should use appropriate defaults."""
    await self.backend.syringe_prime(syringe="A")
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_syringe_prime_with_submerge(self):
    """syringe_prime should accept submerge parameters."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.syringe_prime(
      volume=5000.0,
      syringe="A",
      submerge_tips=True,
      submerge_duration=30,
    )

  async def test_syringe_prime_raises_on_timeout(self):
    """syringe_prime should raise TimeoutError when device does not respond."""
    self.backend.io.set_read_buffer(b"")
    with self.assertRaises(TimeoutError):
      await self.backend.syringe_prime(volume=5000.0, syringe="A")


class TestSyringePrimeCommandEncoding(unittest.TestCase):
  """Test syringe prime command binary encoding.

  Protocol format (13 bytes):
    [0]    plate type prefix (0x04=96-well) (step type for syringe operations)
    [1]    Syringe: A=0, B=1
    [2-3]  Volume: 2 bytes, little-endian, in uL
    [4]    Flow rate: 1-5
    [5]    Refills: byte (number of prime cycles)
    [6-7]  Pump delay: 2 bytes, little-endian, in ms
    [8]    Submerge tips (0 or 1)
    [9-10] Submerge duration in minutes (LE uint16)
    [11]   Bottle (ar-1): derived from syringe (A→0, B→2)
    [12]   Padding
  """

  def setUp(self):
    self.backend = BioTekEL406Backend()

  def test_syringe_prime_step_type(self):
    """Syringe prime command should have prefix 0x04."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="A",
      flow_rate=5,
    )
    self.assertEqual(cmd[0], 0x04)

  def test_syringe_prime_syringe_a(self):
    """Syringe prime syringe A should encode as 0."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="A",
      flow_rate=5,
    )
    self.assertEqual(cmd[1], 0)

  def test_syringe_prime_syringe_b(self):
    """Syringe prime syringe B should encode as 1."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="B",
      flow_rate=5,
    )
    self.assertEqual(cmd[1], 1)

  def test_syringe_prime_lowercase_syringe(self):
    """Syringe prime should accept lowercase syringe names."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="b",
      flow_rate=5,
    )
    self.assertEqual(cmd[1], 1)

  def test_syringe_prime_volume_encoding(self):
    """Syringe prime should encode volume as little-endian 2 bytes."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="A",
      flow_rate=5,
    )
    self.assertEqual(cmd[2], 0x88)
    self.assertEqual(cmd[3], 0x13)

  def test_syringe_prime_volume_1000ul(self):
    """Syringe prime with 1000 uL."""
    cmd = self.backend._build_syringe_prime_command(
      volume=1000.0,
      syringe="A",
      flow_rate=5,
    )
    self.assertEqual(cmd[2], 0xE8)
    self.assertEqual(cmd[3], 0x03)

  def test_syringe_prime_flow_rate(self):
    """Syringe prime should encode flow rate as single byte."""
    for rate in [1, 3, 5]:
      cmd = self.backend._build_syringe_prime_command(
        volume=5000.0,
        syringe="A",
        flow_rate=rate,
      )
      self.assertEqual(cmd[4], rate)

  def test_syringe_prime_refills(self):
    """Syringe prime should encode refills as single byte."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="A",
      flow_rate=5,
      refills=3,
    )
    self.assertEqual(cmd[5], 3)

  def test_syringe_prime_default_refills(self):
    """Syringe prime should default to 2 refills."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="A",
      flow_rate=5,
    )
    self.assertEqual(cmd[5], 2)

  def test_syringe_prime_pump_delay(self):
    """Syringe prime should encode pump delay as LE uint16."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="A",
      flow_rate=5,
      pump_delay=500,
    )
    # 500 = 0x01F4 LE
    self.assertEqual(cmd[6], 0xF4)
    self.assertEqual(cmd[7], 0x01)

  def test_syringe_prime_command_length(self):
    """Syringe prime command should have exactly 13 bytes."""
    cmd = self.backend._build_syringe_prime_command(
      volume=5000.0,
      syringe="A",
      flow_rate=5,
    )
    self.assertEqual(len(cmd), 13)

  def test_syringe_prime_full_command(self):
    """Test complete syringe prime command with all parameters."""
    cmd = self.backend._build_syringe_prime_command(
      volume=3000.0,
      syringe="B",
      flow_rate=3,
      refills=4,
      pump_delay=100,
      submerge_tips=True,
      submerge_duration=90,
    )

    self.assertEqual(len(cmd), 13)
    self.assertEqual(cmd[0], 0x04)  # Prefix
    self.assertEqual(cmd[1], 1)  # Syringe B
    self.assertEqual(cmd[2], 0xB8)  # Volume low (3000 = 0x0BB8)
    self.assertEqual(cmd[3], 0x0B)  # Volume high
    self.assertEqual(cmd[4], 3)  # Flow rate
    self.assertEqual(cmd[5], 4)  # Refills
    self.assertEqual(cmd[6], 0x64)  # Delay low (100 = 0x0064)
    self.assertEqual(cmd[7], 0x00)  # Delay high
    self.assertEqual(cmd[8], 1)  # Submerge tips = True
    self.assertEqual(cmd[9], 0x5A)  # Submerge duration low (90 min = 0x005A)
    self.assertEqual(cmd[10], 0x00)  # Submerge duration high
    self.assertEqual(cmd[11], 2)  # Bottle (B → 2)
    self.assertEqual(cmd[12], 0)  # Padding

  def test_syringe_prime_bottle_encoding(self):
    """Test syringe prime encodes bottle from syringe selection."""
    cmd_a = self.backend._build_syringe_prime_command(
      volume=1000.0,
      syringe="A",
      flow_rate=5,
    )
    cmd_b = self.backend._build_syringe_prime_command(
      volume=1000.0,
      syringe="B",
      flow_rate=5,
    )
    self.assertEqual(cmd_a[11], 0)  # A → bottle=0
    self.assertEqual(cmd_b[11], 2)  # B → bottle=2

  def test_syringe_prime_submerge_duration(self):
    """Test syringe prime encodes submerge duration at bytes 9-10."""
    cmd = self.backend._build_syringe_prime_command(
      volume=1000.0,
      syringe="A",
      flow_rate=5,
      refills=2,
      submerge_tips=True,
      submerge_duration=90,
    )
    # 90 minutes = 0x005A LE → [0x5A, 0x00]
    self.assertEqual(cmd[9], 0x5A)
    self.assertEqual(cmd[10], 0x00)

  def test_syringe_prime_submerge_disabled_zeroes_time(self):
    """When submerge_tips=False, time bytes should be zero."""
    cmd = self.backend._build_syringe_prime_command(
      volume=1000.0,
      syringe="A",
      flow_rate=5,
      submerge_tips=False,
      submerge_duration=90,
    )
    self.assertEqual(cmd[8], 0)  # submerge_tips=False
    self.assertEqual(cmd[9], 0)  # time zeroed
    self.assertEqual(cmd[10], 0)

  def test_syringe_prime_submerge_max_duration(self):
    """Test max submerge duration (1439 minutes = 23:59)."""
    cmd = self.backend._build_syringe_prime_command(
      volume=1000.0,
      syringe="A",
      flow_rate=5,
      submerge_tips=True,
      submerge_duration=1439,
    )
    # 1439 = 0x059F LE → [0x9F, 0x05]
    self.assertEqual(cmd[9], 0x9F)
    self.assertEqual(cmd[10], 0x05)


class TestEL406BackendManifoldPrime(unittest.IsolatedAsyncioTestCase):
  """Test EL406 manifold prime functionality.

  The manifold prime operation (eMPrime = 9) fills the wash manifold
  tubing with liquid. This is used to prepare the manifold for washing.
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_manifold_prime_sends_command(self):
    """manifold_prime should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_prime(volume=500.0, buffer="A", flow_rate=9)

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_manifold_prime_validates_volume(self):
    """manifold_prime should validate volume (5-999 mL)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_prime(volume=0.0, buffer="A")

    with self.assertRaises(ValueError):
      await self.backend.manifold_prime(volume=4.0, buffer="A")

    with self.assertRaises(ValueError):
      await self.backend.manifold_prime(volume=1000.0, buffer="A")

    with self.assertRaises(ValueError):
      await self.backend.manifold_prime(volume=-100.0, buffer="A")

  async def test_manifold_prime_validates_buffer(self):
    """manifold_prime should validate buffer selection."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_prime(volume=500.0, buffer="Z")

    with self.assertRaises(ValueError):
      await self.backend.manifold_prime(volume=500.0, buffer="E")

  async def test_manifold_prime_validates_flow_rate(self):
    """manifold_prime should validate flow rate (3-11)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_prime(volume=500.0, buffer="A", flow_rate=2)

    with self.assertRaises(ValueError):
      await self.backend.manifold_prime(volume=500.0, buffer="A", flow_rate=12)

  async def test_manifold_prime_raises_when_device_not_initialized(self):
    """manifold_prime should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.manifold_prime(volume=500.0, buffer="A")

  async def test_manifold_prime_accepts_flow_rate_range(self):
    """manifold_prime should accept flow rates 3-11."""
    for flow_rate in range(3, 12):
      self.backend.io.set_read_buffer(b"\x06" * 100)
      # Should not raise
      await self.backend.manifold_prime(volume=500.0, buffer="A", flow_rate=flow_rate)

  async def test_manifold_prime_default_flow_rate(self):
    """manifold_prime should use default flow rate 9."""
    await self.backend.manifold_prime(volume=500.0, buffer="A")

    # Verify command was sent (flow rate 9 is default, fastest for priming)
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_manifold_prime_raises_on_timeout(self):
    """manifold_prime should raise TimeoutError when device does not respond."""
    self.backend.io.set_read_buffer(b"")  # No ACK response
    with self.assertRaises(TimeoutError):
      await self.backend.manifold_prime(volume=500.0, buffer="A")


class TestManifoldPrimeCommandEncoding(unittest.TestCase):
  """Test manifold prime command binary encoding.

  Protocol format for manifold prime (M_PRIME = 9):
    [0]   Step type: 0x09 (M_PRIME)
    [1]   Buffer letter: A=0x41, B=0x42, C=0x43, D=0x44 (ASCII char)
    [2-3] Volume: 2 bytes, little-endian, in uL
    [4]   Flow rate: 1-9
    [5-6] Low flow volume: 2 bytes, little-endian (default 0)
    [7-8] Duration: 2 bytes, little-endian (default 0)
    [9-12] Padding zeros: 4 bytes
  """

  def setUp(self):
    self.backend = BioTekEL406Backend()

  def test_manifold_prime_step_type(self):
    """Manifold prime command should have step type prefix 0x04."""
    cmd = self.backend._build_manifold_prime_command(
      volume=1000.0,
      buffer="A",
      flow_rate=9,
    )

    self.assertEqual(cmd[0], 0x04)

  def test_manifold_prime_buffer_a(self):
    """Manifold prime buffer A should encode as 'A' (0x41)."""
    cmd = self.backend._build_manifold_prime_command(
      volume=1000.0,
      buffer="A",
      flow_rate=9,
    )

    # Buffer: A = 0x41 (ASCII 'A')
    self.assertEqual(cmd[1], ord("A"))

  def test_manifold_prime_buffer_b(self):
    """Manifold prime buffer B should encode as 'B' (0x42)."""
    cmd = self.backend._build_manifold_prime_command(
      volume=1000.0,
      buffer="B",
      flow_rate=9,
    )

    # Buffer: B = 0x42 (ASCII 'B')
    self.assertEqual(cmd[1], ord("B"))

  def test_manifold_prime_lowercase_buffer(self):
    """Manifold prime should accept lowercase buffer and encode as uppercase."""
    cmd = self.backend._build_manifold_prime_command(
      volume=1000.0,
      buffer="b",
      flow_rate=9,
    )

    # Should encode as uppercase 'B'
    self.assertEqual(cmd[1], ord("B"))

  def test_manifold_prime_volume_encoding(self):
    """Manifold prime should encode volume as little-endian 2 bytes."""
    cmd = self.backend._build_manifold_prime_command(
      volume=1000.0,
      buffer="A",
      flow_rate=9,
    )

    # Volume: 1000 uL = 0x03E8 little-endian = [0xE8, 0x03]
    self.assertEqual(cmd[2], 0xE8)
    self.assertEqual(cmd[3], 0x03)

  def test_manifold_prime_volume_500ul(self):
    """Manifold prime with 500 uL."""
    cmd = self.backend._build_manifold_prime_command(
      volume=500.0,
      buffer="A",
      flow_rate=9,
    )

    # Volume: 500 uL = 0x01F4 little-endian = [0xF4, 0x01]
    self.assertEqual(cmd[2], 0xF4)
    self.assertEqual(cmd[3], 0x01)

  def test_manifold_prime_volume_max(self):
    """Manifold prime with maximum volume (65535 uL)."""
    cmd = self.backend._build_manifold_prime_command(
      volume=65535.0,
      buffer="A",
      flow_rate=9,
    )

    # Volume: 65535 uL = 0xFFFF little-endian = [0xFF, 0xFF]
    self.assertEqual(cmd[2], 0xFF)
    self.assertEqual(cmd[3], 0xFF)

  def test_manifold_prime_flow_rate(self):
    """Manifold prime should encode flow rate as single byte."""
    cmd = self.backend._build_manifold_prime_command(
      volume=1000.0,
      buffer="A",
      flow_rate=7,
    )

    # Flow rate: 7
    self.assertEqual(cmd[4], 7)

  def test_manifold_prime_flow_rate_min(self):
    """Manifold prime should encode minimum flow rate 1."""
    cmd = self.backend._build_manifold_prime_command(
      volume=1000.0,
      buffer="A",
      flow_rate=1,
    )

    self.assertEqual(cmd[4], 1)

  def test_manifold_prime_flow_rate_max(self):
    """Manifold prime should encode maximum flow rate 9."""
    cmd = self.backend._build_manifold_prime_command(
      volume=1000.0,
      buffer="A",
      flow_rate=9,
    )

    self.assertEqual(cmd[4], 9)

  def test_manifold_prime_full_command(self):
    """Test complete manifold prime command with all parameters."""
    cmd = self.backend._build_manifold_prime_command(
      volume=2000.0,
      buffer="B",
      flow_rate=5,
    )

    self.assertEqual(cmd[0], 0x04)  # Step type prefix
    self.assertEqual(cmd[1], ord("B"))  # Buffer B
    self.assertEqual(cmd[2], 0xD0)  # Volume low byte (2000 = 0x07D0)
    self.assertEqual(cmd[3], 0x07)  # Volume high byte
    self.assertEqual(cmd[4], 5)  # Flow rate


class TestEL406BackendAutoClean(unittest.IsolatedAsyncioTestCase):
  """Test EL406 manifold auto-clean functionality.

  The auto-clean operation (eMAutoClean = 10) runs an automatic
  cleaning cycle of the manifold.
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_auto_clean_sends_command(self):
    """auto_clean should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_auto_clean(buffer="A")

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_auto_clean_validates_buffer(self):
    """auto_clean should validate buffer selection."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_auto_clean(buffer="Z")

    with self.assertRaises(ValueError):
      await self.backend.manifold_auto_clean(buffer="E")

  async def test_auto_clean_raises_when_device_not_initialized(self):
    """auto_clean should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.manifold_auto_clean(buffer="A")

  async def test_auto_clean_with_duration(self):
    """auto_clean should accept duration parameter."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_auto_clean(buffer="A", duration=60.0)

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_auto_clean_default_buffer(self):
    """auto_clean should use default buffer A."""
    await self.backend.manifold_auto_clean()

    # Verify command was sent
    self.assertGreater(len(self.backend.io.written_data), 0)

  async def test_auto_clean_raises_on_timeout(self):
    """auto_clean should raise TimeoutError when device does not respond."""
    self.backend.io.set_read_buffer(b"")  # No ACK response
    with self.assertRaises(TimeoutError):
      await self.backend.manifold_auto_clean(buffer="A")

  async def test_auto_clean_validates_negative_duration(self):
    """auto_clean should raise ValueError for negative duration."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_auto_clean(buffer="A", duration=-10.0)


class TestAutoCleanCommandEncoding(unittest.TestCase):
  """Test auto-clean command binary encoding.

  Protocol format for auto-clean (M_AUTO_CLEAN = 10):
    [0]   Step type: 0x0A (M_AUTO_CLEAN)
    [1]   Buffer letter: A=0x41, B=0x42, C=0x43, D=0x44 (ASCII char)
    [2-3] Duration: 2 bytes, little-endian (in seconds or other time unit)
    [4-7] Padding zeros: 4 bytes
  """

  def setUp(self):
    self.backend = BioTekEL406Backend()

  def test_auto_clean_step_type(self):
    """Auto-clean command should have step type prefix 0x04."""
    cmd = self.backend._build_auto_clean_command(buffer="A")

    self.assertEqual(cmd[0], 0x04)

  def test_auto_clean_buffer_a(self):
    """Auto-clean buffer A should encode as 'A' (0x41)."""
    cmd = self.backend._build_auto_clean_command(buffer="A")

    # Buffer: A = 0x41 (ASCII 'A')
    self.assertEqual(cmd[1], ord("A"))

  def test_auto_clean_buffer_b(self):
    """Auto-clean buffer B should encode as 'B' (0x42)."""
    cmd = self.backend._build_auto_clean_command(buffer="B")

    # Buffer: B = 0x42 (ASCII 'B')
    self.assertEqual(cmd[1], ord("B"))

  def test_auto_clean_lowercase_buffer(self):
    """Auto-clean should accept lowercase buffer and encode as uppercase."""
    cmd = self.backend._build_auto_clean_command(buffer="c")

    # Should encode as uppercase 'C'
    self.assertEqual(cmd[1], ord("C"))

  def test_auto_clean_duration_encoding(self):
    """Auto-clean should encode duration as little-endian 2 bytes."""
    cmd = self.backend._build_auto_clean_command(buffer="A", duration=60.0)

    # Duration: 60 seconds = 0x003C little-endian = [0x3C, 0x00]
    self.assertEqual(cmd[2], 0x3C)
    self.assertEqual(cmd[3], 0x00)

  def test_auto_clean_duration_30_seconds(self):
    """Auto-clean with 30 second duration."""
    cmd = self.backend._build_auto_clean_command(buffer="A", duration=30.0)

    # Duration: 30 seconds = 0x001E little-endian = [0x1E, 0x00]
    self.assertEqual(cmd[2], 0x1E)
    self.assertEqual(cmd[3], 0x00)

  def test_auto_clean_duration_zero(self):
    """Auto-clean with zero duration (no additional cleaning time)."""
    cmd = self.backend._build_auto_clean_command(buffer="A", duration=0.0)

    # Duration: 0 = [0x00, 0x00]
    self.assertEqual(cmd[2], 0x00)
    self.assertEqual(cmd[3], 0x00)

  def test_auto_clean_full_command(self):
    """Test complete auto-clean command with all parameters."""
    cmd = self.backend._build_auto_clean_command(
      buffer="B",
      duration=90.0,
    )

    self.assertEqual(cmd[0], 0x04)  # Step type prefix
    self.assertEqual(cmd[1], ord("B"))  # Buffer B
    self.assertEqual(cmd[2], 0x5A)  # Duration low byte (90 = 0x005A)
    self.assertEqual(cmd[3], 0x00)  # Duration high byte

  def test_auto_clean_default_duration(self):
    """Auto-clean without duration should use default 1 minute."""
    cmd = self.backend._build_auto_clean_command(buffer="A")

    # Default duration: 1 = [0x01, 0x00]
    self.assertEqual(cmd[2], 0x01)
    self.assertEqual(cmd[3], 0x00)


if __name__ == "__main__":
  unittest.main()
