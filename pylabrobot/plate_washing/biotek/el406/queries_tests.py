# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Query methods.

This module contains tests for Query methods.
"""

import unittest

# Import the backend module (mock is already installed by test_el406_mock import)
# Import the backend module
from pylabrobot.plate_washing.biotek.el406 import (
  BioTekEL406Backend,
  EL406Sensor,
  EL406SyringeManifold,
  EL406WasherManifold,
)
from pylabrobot.plate_washing.biotek.el406.mock_tests import MockFTDI


class TestEL406BackendGetWasherManifold(unittest.IsolatedAsyncioTestCase):
  """Test EL406 get washer manifold query.

  The GetWasherManifoldInstalled operation queries the installed washer manifold type.
  Command byte: 216 (0xD8)

  Response format: [manifold_type_byte, ACK_byte]
  - The device sends the manifold type first, then ACK

  Manifold types (EnumWasherManifold):
    0: 96-Tube Dual
    1: 192-Tube
    2: 128-Tube
    3: 96-Tube Single
    4: 96 Deep Pin
    255: Not Installed
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_get_washer_manifold_returns_enum(self):
    """get_washer_manifold should return an EL406WasherManifold enum value."""
    # Simulate device response: manifold type byte followed by ACK
    # 0 = 96-Tube Dual manifold
    self.backend.io.set_read_buffer(bytes([0, 0x06]))

    result = await self.backend.get_washer_manifold()

    self.assertIsInstance(result, EL406WasherManifold)
    self.assertEqual(result, EL406WasherManifold.TUBE_96_DUAL)

  async def test_get_washer_manifold_192_tube(self):
    """get_washer_manifold should correctly identify 192-Tube manifold."""
    # 1 = 192-Tube manifold
    self.backend.io.set_read_buffer(bytes([1, 0x06]))

    result = await self.backend.get_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.TUBE_192)

  async def test_get_washer_manifold_128_tube(self):
    """get_washer_manifold should correctly identify 128-Tube manifold."""
    # 2 = 128-Tube manifold
    self.backend.io.set_read_buffer(bytes([2, 0x06]))

    result = await self.backend.get_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.TUBE_128)

  async def test_get_washer_manifold_96_tube_single(self):
    """get_washer_manifold should correctly identify 96-Tube Single manifold."""
    # 3 = 96-Tube Single manifold
    self.backend.io.set_read_buffer(bytes([3, 0x06]))

    result = await self.backend.get_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.TUBE_96_SINGLE)

  async def test_get_washer_manifold_deep_pin_96(self):
    """get_washer_manifold should correctly identify 96 Deep Pin manifold."""
    # 4 = 96 Deep Pin manifold
    self.backend.io.set_read_buffer(bytes([4, 0x06]))

    result = await self.backend.get_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.DEEP_PIN_96)

  async def test_get_washer_manifold_not_installed(self):
    """get_washer_manifold should correctly identify when not installed."""
    # 255 = Not Installed
    self.backend.io.set_read_buffer(bytes([255, 0x06]))

    result = await self.backend.get_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.NOT_INSTALLED)

  async def test_get_washer_manifold_sends_correct_command(self):
    """get_washer_manifold should send command byte 216 (0xD8) in framed message."""
    self.backend.io.set_read_buffer(bytes([0, 0x06]))

    await self.backend.get_washer_manifold()

    last_command = self.backend.io.written_data[-1]
    # Command byte is at position 2 in framed message
    self.assertEqual(last_command[2], 0xD8)

  async def test_get_washer_manifold_raises_when_device_not_initialized(self):
    """get_washer_manifold should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.get_washer_manifold()

  async def test_get_washer_manifold_raises_on_timeout(self):
    """get_washer_manifold should raise TimeoutError when device does not respond."""
    self.backend.io.set_read_buffer(b"")  # No response

    with self.assertRaises(TimeoutError):
      await self.backend.get_washer_manifold()

  async def test_get_washer_manifold_invalid_value(self):
    """get_washer_manifold should raise ValueError for unknown manifold type."""
    # 100 is not a valid manifold type
    self.backend.io.set_read_buffer(bytes([100, 0x06]))

    with self.assertRaises(ValueError) as ctx:
      await self.backend.get_washer_manifold()

    self.assertIn("100", str(ctx.exception))
    self.assertIn("Unknown", str(ctx.exception))


class TestEL406BackendGetSyringeManifold(unittest.IsolatedAsyncioTestCase):
  """Test EL406 get syringe manifold query.

  The GetSyringeManifoldInstalled operation queries the installed syringe manifold type.
  Command byte: 187 (0xBB)
  Response byte contains manifold type.

  Response format: [manifold_type_byte, ACK_byte]
  - The device sends the manifold type first, then ACK

  Syringe Manifold types (EL406SyringeManifold enum):
    0: Not Installed
    1: 16-Tube
    2: 32-Tube Large Bore
    3: 32-Tube Small Bore
    4: 16-Tube 7
    5: 8-Tube
    6: 6 Well Plate
    7: 12 Well Plate
    8: 24 Well Plate
    9: 48 Well Plate
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_get_syringe_manifold_returns_enum(self):
    """get_syringe_manifold should return an EL406SyringeManifold enum value."""
    # Simulate device response: manifold type byte followed by ACK
    # 1 = 16-Tube manifold
    self.backend.io.set_read_buffer(bytes([1, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertIsInstance(result, EL406SyringeManifold)
    self.assertEqual(result, EL406SyringeManifold.TUBE_16)

  async def test_get_syringe_manifold_not_installed(self):
    """get_syringe_manifold should correctly identify when not installed."""
    # 0 = Not Installed
    self.backend.io.set_read_buffer(bytes([0, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.NOT_INSTALLED)

  async def test_get_syringe_manifold_tube_32_large_bore(self):
    """get_syringe_manifold should correctly identify 32-Tube Large Bore manifold."""
    # 2 = 32-Tube Large Bore
    self.backend.io.set_read_buffer(bytes([2, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.TUBE_32_LARGE_BORE)

  async def test_get_syringe_manifold_tube_32_small_bore(self):
    """get_syringe_manifold should correctly identify 32-Tube Small Bore manifold."""
    # 3 = 32-Tube Small Bore
    self.backend.io.set_read_buffer(bytes([3, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.TUBE_32_SMALL_BORE)

  async def test_get_syringe_manifold_tube_16_7(self):
    """get_syringe_manifold should correctly identify 16-Tube 7 manifold."""
    # 4 = 16-Tube 7
    self.backend.io.set_read_buffer(bytes([4, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.TUBE_16_7)

  async def test_get_syringe_manifold_tube_8(self):
    """get_syringe_manifold should correctly identify 8-Tube manifold."""
    # 5 = 8-Tube
    self.backend.io.set_read_buffer(bytes([5, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.TUBE_8)

  async def test_get_syringe_manifold_plate_6_well(self):
    """get_syringe_manifold should correctly identify 6 Well Plate manifold.

    This test is critical because manifold type 6 equals ACK_BYTE (0x06).
    The framed protocol handles this by including data length in the header.
    """
    # 6 = 6 Well Plate (same value as ACK_BYTE 0x06)
    # Use set_query_response to properly frame the data byte
    self.backend.io.set_query_response(bytes([6]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.PLATE_6_WELL)

  async def test_get_syringe_manifold_plate_12_well(self):
    """get_syringe_manifold should correctly identify 12 Well Plate manifold."""
    # 7 = 12 Well Plate
    self.backend.io.set_read_buffer(bytes([7, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.PLATE_12_WELL)

  async def test_get_syringe_manifold_plate_24_well(self):
    """get_syringe_manifold should correctly identify 24 Well Plate manifold."""
    # 8 = 24 Well Plate
    self.backend.io.set_read_buffer(bytes([8, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.PLATE_24_WELL)

  async def test_get_syringe_manifold_plate_48_well(self):
    """get_syringe_manifold should correctly identify 48 Well Plate manifold."""
    # 9 = 48 Well Plate
    self.backend.io.set_read_buffer(bytes([9, 0x06]))

    result = await self.backend.get_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.PLATE_48_WELL)

  async def test_get_syringe_manifold_sends_correct_command(self):
    """get_syringe_manifold should send command byte 187 (0xBB) in framed message."""
    self.backend.io.set_read_buffer(bytes([0, 0x06]))

    await self.backend.get_syringe_manifold()

    last_command = self.backend.io.written_data[-1]
    # Command byte is at position 2 in framed message
    self.assertEqual(last_command[2], 0xBB)

  async def test_get_syringe_manifold_raises_when_device_not_initialized(self):
    """get_syringe_manifold should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.get_syringe_manifold()

  async def test_get_syringe_manifold_raises_on_timeout(self):
    """get_syringe_manifold should raise TimeoutError when device does not respond."""
    self.backend.io.set_read_buffer(b"")  # No response

    with self.assertRaises(TimeoutError):
      await self.backend.get_syringe_manifold()

  async def test_get_syringe_manifold_invalid_value(self):
    """get_syringe_manifold should raise ValueError for unknown manifold type."""
    # 100 is not a valid manifold type
    self.backend.io.set_read_buffer(bytes([100, 0x06]))

    with self.assertRaises(ValueError) as ctx:
      await self.backend.get_syringe_manifold()

    self.assertIn("100", str(ctx.exception))
    self.assertIn("Unknown", str(ctx.exception))


class TestEL406BackendGetSerialNumber(unittest.IsolatedAsyncioTestCase):
  """Test EL406 get serial number query.

  The GetInstSerialNumber operation queries the device serial number.
  Command: 256 (0x0100) - 16-bit command sent as [0x00, 0x01] little-endian
  Response: ASCII string followed by ACK (0x06)

  Response format: [ASCII bytes...][ACK_byte]
  - The device sends ASCII bytes of the serial number, then ACK
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_get_serial_number_various_formats(self):
    """get_serial_number should handle various serial number formats."""
    # Test with different serial number formats
    test_cases = [
      (b"SN123456", "SN123456"),
      (b"EL406-A1B2C3", "EL406-A1B2C3"),
      (b"12345", "12345"),
      (b"ABC", "ABC"),
    ]

    for response_data, expected_serial in test_cases:
      self.backend.io.set_query_response(response_data)
      result = await self.backend.get_serial_number()
      self.assertEqual(result, expected_serial)

  async def test_get_serial_number_sends_correct_command(self):
    """get_serial_number should send 16-bit command 256 (0x0100) in framed message."""
    self.backend.io.set_query_response(b"SN123")

    await self.backend.get_serial_number()

    last_command = self.backend.io.written_data[-1]
    # Framed message has command at bytes [2-3] (little-endian)
    self.assertEqual(last_command[2], 0x00)  # Low byte of 256
    self.assertEqual(last_command[3], 0x01)  # High byte of 256

  async def test_get_serial_number_raises_when_device_not_initialized(self):
    """get_serial_number should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.get_serial_number()

  async def test_get_serial_number_raises_on_timeout(self):
    """get_serial_number should raise TimeoutError when device does not respond."""
    self.backend.io.set_read_buffer(b"")  # No response

    with self.assertRaises(TimeoutError):
      await self.backend.get_serial_number()

  async def test_get_serial_number_empty_response(self):
    """get_serial_number should handle empty serial (just ACK)."""
    # Device returns only ACK (empty serial)
    self.backend.io.set_read_buffer(b"\x06")

    result = await self.backend.get_serial_number()

    self.assertEqual(result, "")


class TestEL406BackendGetSensorEnabled(unittest.IsolatedAsyncioTestCase):
  """Test EL406 get sensor enabled query.

  The GetSensorEnabled operation queries whether a specific sensor is enabled.
  Command byte: 210 (0xD2)
  Parameter: sensor type byte (0-5)
  Response: [enabled_byte][ACK_byte]
    - enabled_byte: 0 = disabled, 1 = enabled

  Command format:
    [0] Command byte: 210 (0xD2)
    [1] Sensor type byte: 0-5 (EnumSensor value)

  Response format:
    [0] Enabled byte: 0 = disabled, 1 = enabled
    [1] ACK (0x06)
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_get_sensor_enabled_returns_true_when_enabled(self):
    """get_sensor_enabled should return True when sensor is enabled."""

    # Enabled = 1
    self.backend.io.set_query_response(bytes([1]))

    result = await self.backend.get_sensor_enabled(EL406Sensor.WASTE)

    self.assertTrue(result)

  async def test_get_sensor_enabled_returns_false_when_disabled(self):
    """get_sensor_enabled should return False when sensor is disabled."""

    # Disabled = 0
    self.backend.io.set_query_response(bytes([0]))

    result = await self.backend.get_sensor_enabled(EL406Sensor.FLUID)

    self.assertFalse(result)

  async def test_get_sensor_enabled_sends_correct_command(self):
    """get_sensor_enabled should send command byte 210 (0xD2) in framed message."""

    self.backend.io.set_query_response(bytes([1]))

    await self.backend.get_sensor_enabled(EL406Sensor.VACUUM)

    # Header and data are sent as separate writes
    header = self.backend.io.written_data[-2]
    # Command byte is at position 2 in the 11-byte header
    self.assertEqual(header[2], 0xD2)

  async def test_get_sensor_enabled_sends_sensor_type(self):
    """get_sensor_enabled should include sensor type in command data."""

    self.backend.io.set_query_response(bytes([1]))

    await self.backend.get_sensor_enabled(EL406Sensor.WASTE)

    # Header and data are sent as separate writes
    header = self.backend.io.written_data[-2]
    data = self.backend.io.written_data[-1]
    full_command = header + data
    # Framed message: 11-byte header + 1-byte data (sensor type)
    self.assertEqual(len(full_command), 12)
    self.assertEqual(full_command[2], 0xD2)  # Command byte at position 2
    self.assertEqual(full_command[11], 1)  # WASTE = 1 (data starts at byte 11)

  async def test_get_sensor_enabled_sensor_types_in_command(self):
    """get_sensor_enabled should send correct sensor type byte for each sensor."""

    test_cases = [
      (EL406Sensor.VACUUM, 0),
      (EL406Sensor.WASTE, 1),
      (EL406Sensor.FLUID, 2),
      (EL406Sensor.FLOW, 3),
      (EL406Sensor.FILTER_VAC, 4),
      (EL406Sensor.PLATE, 5),
    ]

    for sensor, expected_byte in test_cases:
      self.backend.io.set_query_response(bytes([1]))
      await self.backend.get_sensor_enabled(sensor)

      # Data byte is in the separate data write (last write)
      data = self.backend.io.written_data[-1]
      self.assertEqual(
        data[0], expected_byte, f"Sensor {sensor.name} should send byte {expected_byte}"
      )

  async def test_get_sensor_enabled_raises_when_device_not_initialized(self):
    """get_sensor_enabled should raise RuntimeError if device not initialized."""

    backend = BioTekEL406Backend()
    # Note: no setup() called

    with self.assertRaises(RuntimeError):
      await backend.get_sensor_enabled(EL406Sensor.VACUUM)

  async def test_get_sensor_enabled_raises_on_timeout(self):
    """get_sensor_enabled should raise TimeoutError when device does not respond."""

    self.backend.io.set_read_buffer(b"")  # No response

    with self.assertRaises(TimeoutError):
      await self.backend.get_sensor_enabled(EL406Sensor.VACUUM)


class TestGetSyringeBoxInfo(unittest.IsolatedAsyncioTestCase):
  """Test get_syringe_box_info functionality.

  get_syringe_box_info retrieves syringe box configuration.
  Response reads two bytes: box_type then box_size.
  Response format: [box_type, box_size, ACK] = 3 bytes
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_get_syringe_box_info_parses_response_correctly(self):
    """get_syringe_box_info should correctly parse box_type and box_size."""
    # Mock response: [box_type=2, box_size=100, ACK]
    self.backend.io.set_read_buffer(b"\x02\x64\x06")
    result = await self.backend.get_syringe_box_info()
    self.assertEqual(result["box_type"], 2)
    self.assertEqual(result["box_size"], 100)
    self.assertTrue(result["installed"])

  async def test_get_syringe_box_info_not_installed(self):
    """get_syringe_box_info should report not installed when box_type is 0."""
    # Mock response: [box_type=0, box_size=0, ACK]
    self.backend.io.set_read_buffer(b"\x00\x00\x06")
    result = await self.backend.get_syringe_box_info()
    self.assertEqual(result["box_type"], 0)
    self.assertFalse(result["installed"])

  async def test_get_syringe_box_info_sends_command(self):
    """get_syringe_box_info should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    self.backend.io.set_read_buffer(b"\x01\x32\x06")
    await self.backend.get_syringe_box_info()
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_get_syringe_box_info_raises_when_device_not_initialized(self):
    """get_syringe_box_info should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    with self.assertRaises(RuntimeError):
      await backend.get_syringe_box_info()


class TestGetPeristalticInstalled(unittest.IsolatedAsyncioTestCase):
  """Test get_peristaltic_installed functionality.

  get_peristaltic_installed checks if a peristaltic pump is installed.
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.set_read_buffer(b"\x06" * 100)

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_get_peristaltic_installed_true_when_installed(self):
    """get_peristaltic_installed should return True when pump is installed."""
    self.backend.io.set_read_buffer(b"\x01\x06")  # Installed
    result = await self.backend.get_peristaltic_installed(selector=0)
    self.assertTrue(result)

  async def test_get_peristaltic_installed_false_when_not_installed(self):
    """get_peristaltic_installed should return False when pump is not installed."""
    self.backend.io.set_read_buffer(b"\x00\x06")  # Not installed
    result = await self.backend.get_peristaltic_installed(selector=0)
    self.assertFalse(result)

  async def test_get_peristaltic_installed_validates_selector(self):
    """get_peristaltic_installed should validate selector value."""
    with self.assertRaises(ValueError):
      await self.backend.get_peristaltic_installed(selector=-1)

  async def test_get_peristaltic_installed_accepts_valid_selectors(self):
    """get_peristaltic_installed should accept valid selector values."""
    for selector in [0, 1]:  # Primary and secondary
      self.backend.io.set_read_buffer(b"\x01\x06")
      # Should not raise
      result = await self.backend.get_peristaltic_installed(selector=selector)
      self.assertIsInstance(result, bool)

  async def test_get_peristaltic_installed_sends_command(self):
    """get_peristaltic_installed should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    self.backend.io.set_read_buffer(b"\x01\x06")
    await self.backend.get_peristaltic_installed(selector=0)
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_get_peristaltic_installed_includes_selector_in_command(self):
    """get_peristaltic_installed should include selector in command."""
    self.backend.io.set_read_buffer(b"\x01\x06")
    await self.backend.get_peristaltic_installed(selector=1)
    last_command = self.backend.io.written_data[-1]
    # Selector should be in the command
    self.assertIn(1, list(last_command))

  async def test_get_peristaltic_installed_raises_when_device_not_initialized(self):
    """get_peristaltic_installed should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    with self.assertRaises(RuntimeError):
      await backend.get_peristaltic_installed(selector=0)


if __name__ == "__main__":
  unittest.main()


class TestGetInstrumentSettings(unittest.IsolatedAsyncioTestCase):
  """Test get_instrument_settings functionality.

  get_instrument_settings queries hardware configuration by calling
  multiple sequential query commands.
  """

  def _build_multi_query_buffer(self):
    """Build mock buffer with 5 sequential framed query responses.

    get_instrument_settings calls in order:
    1. get_washer_manifold -> manifold type byte
    2. get_syringe_manifold -> manifold type byte
    3. get_syringe_box_info -> box_type, box_size
    4. get_peristaltic_installed(0) -> installed byte
    5. get_peristaltic_installed(1) -> installed byte

    Each response is: ACK + 11-byte header + 2-byte prefix + data
    """
    buf = b""
    # 1. washer manifold: TUBE_96_DUAL (0)
    buf += MockFTDI.build_completion_frame(bytes([0x01, 0x00, 0x00]))
    # 2. syringe manifold: TUBE_32_LARGE_BORE (2)
    buf += MockFTDI.build_completion_frame(bytes([0x01, 0x00, 0x02]))
    # 3. syringe box info: box_type=1, box_size=100
    buf += MockFTDI.build_completion_frame(bytes([0x01, 0x00, 0x01, 0x64]))
    # 4. peristaltic 0: installed (1)
    buf += MockFTDI.build_completion_frame(bytes([0x01, 0x00, 0x01]))
    # 5. peristaltic 1: not installed (0)
    buf += MockFTDI.build_completion_frame(bytes([0x01, 0x00, 0x00]))
    return buf

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    self.backend.io = MockFTDI()
    await self.backend.setup()
    self.backend.io.read_buffer = self._build_multi_query_buffer()

  async def asyncTearDown(self):
    if self.backend.io is not None:
      await self.backend.stop()

  async def test_get_instrument_settings_returns_dict(self):
    """get_instrument_settings should return a dictionary."""
    result = await self.backend.get_instrument_settings()
    self.assertIsInstance(result, dict)

  async def test_get_instrument_settings_queries_hardware(self):
    """get_instrument_settings should query multiple hardware settings."""
    result = await self.backend.get_instrument_settings()
    self.assertIn("washer_manifold", result)
    self.assertIn("syringe_manifold", result)
    self.assertIn("syringe_box", result)
    self.assertIn("peristaltic_pump_1", result)
    self.assertIn("peristaltic_pump_2", result)

  async def test_get_instrument_settings_returns_correct_values(self):
    """get_instrument_settings should return correct hardware configuration."""
    result = await self.backend.get_instrument_settings()
    self.assertEqual(result["washer_manifold"], EL406WasherManifold.TUBE_96_DUAL)
    self.assertEqual(result["syringe_manifold"], EL406SyringeManifold.TUBE_32_LARGE_BORE)
    self.assertEqual(result["syringe_box"]["installed"], True)
    self.assertEqual(result["syringe_box"]["box_type"], 1)
    self.assertEqual(result["syringe_box"]["box_size"], 100)
    self.assertEqual(result["peristaltic_pump_1"], True)
    self.assertEqual(result["peristaltic_pump_2"], False)

  async def test_get_instrument_settings_raises_when_device_not_initialized(self):
    """get_instrument_settings should raise RuntimeError if device not initialized."""
    backend = BioTekEL406Backend()
    with self.assertRaises(RuntimeError):
      await backend.get_instrument_settings()
