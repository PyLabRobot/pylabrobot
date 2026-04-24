# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Dispense operations."""

from pylabrobot.plate_washing.biotek.el406 import ExperimentalBioTekEL406Backend
from pylabrobot.plate_washing.biotek.el406.mock_tests import PT96, EL406TestCase


class TestEL406BackendDispense(EL406TestCase):
  """Test EL406 manifold dispense functionality."""

  async def test_dispense_sends_command(self):
    """Dispense should send correct command."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.manifold_dispense(PT96, volume=300.0, buffer="A", flow_rate=5)

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_dispense_validates_volume(self):
    """Dispense should validate volume range (25-3000)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=0.0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=24.0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=3001.0)

  async def test_dispense_validates_flow_rate(self):
    """Dispense should validate flow rate (1-11)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, flow_rate=0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, flow_rate=12)

  async def test_dispense_flow_rate_1_2_requires_vacuum_delay(self):
    """Flow rates 1-2 (cell wash) require vacuum_delay_volume > 0."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, flow_rate=1)  # no vacuum
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, flow_rate=2)  # no vacuum
    # With vacuum delay, should work
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.manifold_dispense(PT96, volume=300.0, flow_rate=1, vacuum_delay_volume=100.0)
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.manifold_dispense(PT96, volume=300.0, flow_rate=2, vacuum_delay_volume=100.0)

  async def test_dispense_validates_offset_x(self):
    """Dispense should validate X offset (-60..60)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, offset_x=-61)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, offset_x=61)

  async def test_dispense_validates_offset_y(self):
    """Dispense should validate Y offset (-40..40)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, offset_y=-41)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, offset_y=41)

  async def test_dispense_validates_offset_z(self):
    """Dispense should validate Z offset (1-210)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, offset_z=0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, offset_z=211)

  async def test_dispense_validates_pre_dispense_volume(self):
    """Dispense should validate pre-dispense volume (0 or 25-3000)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, pre_dispense_volume=10.0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, pre_dispense_volume=3001.0)

  async def test_dispense_validates_pre_dispense_flow_rate(self):
    """Dispense should validate pre-dispense flow rate (3-11)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, pre_dispense_flow_rate=2)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, pre_dispense_flow_rate=12)

  async def test_dispense_validates_vacuum_delay_volume(self):
    """Dispense should validate vacuum delay volume (0-3000)."""
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, vacuum_delay_volume=-1.0)
    with self.assertRaises(ValueError):
      await self.backend.manifold_dispense(PT96, volume=300.0, vacuum_delay_volume=3001.0)

  async def test_dispense_accepts_flow_rate_range(self):
    """Dispense should accept flow rates 1-11 (1-2 with vacuum delay)."""
    # Flow rates 3-11 work without vacuum
    for flow_rate in range(3, 12):
      self.backend.io.set_read_buffer(b"\x06" * 100)
      await self.backend.manifold_dispense(PT96, volume=300.0, flow_rate=flow_rate)
    # Flow rates 1-2 work with vacuum delay
    for flow_rate in [1, 2]:
      self.backend.io.set_read_buffer(b"\x06" * 100)
      await self.backend.manifold_dispense(
        PT96, volume=300.0, flow_rate=flow_rate, vacuum_delay_volume=100.0
      )

  async def test_dispense_accepts_all_buffers(self):
    """Dispense should accept buffers A, B, C, D."""
    for buffer in ["A", "B", "C", "D"]:
      self.backend.io.set_read_buffer(b"\x06" * 100)
      await self.backend.manifold_dispense(PT96, volume=300.0, buffer=buffer)

  async def test_dispense_accepts_volume_boundaries(self):
    """Dispense should accept volume at boundaries (25 and 3000)."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.manifold_dispense(PT96, volume=25.0)
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.manifold_dispense(PT96, volume=3000.0)

  async def test_dispense_accepts_pre_dispense_zero(self):
    """Pre-dispense volume of 0 should be accepted (disabled)."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.manifold_dispense(PT96, volume=300.0, pre_dispense_volume=0.0)

  async def test_dispense_raises_when_device_not_initialized(self):
    """Dispense should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()
    with self.assertRaises(RuntimeError):
      await backend.manifold_dispense(PT96, volume=300.0)

  async def test_dispense_raises_on_timeout(self):
    """Dispense should raise TimeoutError when device does not respond."""
    self.backend.timeout = 0.01
    self.backend.io.set_read_buffer(b"")
    with self.assertRaises(TimeoutError):
      await self.backend.manifold_dispense(PT96, volume=300.0)


class TestEL406BackendSyringeDispense(EL406TestCase):
  """Test EL406 syringe dispense functionality.

  The syringe dispense operation uses the syringe pump to dispense liquid
  to wells. This provides more precise volume control than peristaltic
  dispensing.
  """

  async def test_syringe_dispense_sends_command(self):
    """syringe_dispense should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", flow_rate=2)

    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_syringe_dispense_validates_volume(self):
    """syringe_dispense should validate volume is positive."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=0.0, syringe="A")

    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=-100.0, syringe="A")

  async def test_syringe_dispense_validates_syringe(self):
    """syringe_dispense should validate syringe selection."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="Z")

    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="C")

  async def test_syringe_dispense_validates_flow_rate(self):
    """syringe_dispense should validate flow rate (1-5)."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", flow_rate=0)

    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", flow_rate=6)

  async def test_syringe_dispense_validates_pump_delay(self):
    """syringe_dispense should validate pump_delay (0-5.0 seconds)."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", pump_delay=-1.0)

    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", pump_delay=6.0)

  async def test_syringe_dispense_raises_when_device_not_initialized(self):
    """syringe_dispense should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.syringe_dispense(PT96, volume=50.0, syringe="A")

  async def test_syringe_dispense_accepts_flow_rate_range(self):
    """syringe_dispense should accept flow rates 1-5."""
    for flow_rate in range(1, 6):
      self.backend.io.set_read_buffer(b"\x06" * 100)
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", flow_rate=flow_rate)

  async def test_syringe_dispense_accepts_pump_delay_range(self):
    """syringe_dispense should accept pump_delay 0-5.0 seconds."""
    for pump_delay in [0, 0.1, 5.0]:
      self.backend.io.set_read_buffer(b"\x06" * 100)
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", pump_delay=pump_delay)

  async def test_syringe_dispense_with_offsets(self):
    """syringe_dispense should accept X, Y, Z offsets."""
    initial_count = len(self.backend.io.written_data)
    await self.backend.syringe_dispense(
      PT96,
      volume=50.0,
      syringe="A",
      flow_rate=2,
      offset_x=10,
      offset_z=336,
    )
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_syringe_dispense_with_columns(self):
    """syringe_dispense should accept column list (1-indexed)."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.syringe_dispense(
      PT96,
      volume=50.0,
      syringe="A",
      columns=[1, 2, 3],
    )

  async def test_syringe_dispense_columns_none_means_all(self):
    """columns=None should select all columns."""
    self.backend.io.set_read_buffer(b"\x06" * 100)
    await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", columns=None)

  async def test_syringe_dispense_validates_columns(self):
    """syringe_dispense should validate column numbers."""
    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", columns=[0])

    with self.assertRaises(ValueError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A", columns=[13])

  async def test_syringe_dispense_raises_on_timeout(self):
    """syringe_dispense should raise TimeoutError when device does not respond."""
    self.backend.timeout = 0.01
    self.backend.io.set_read_buffer(b"")  # No ACK response
    with self.assertRaises(TimeoutError):
      await self.backend.syringe_dispense(PT96, volume=50.0, syringe="A")
