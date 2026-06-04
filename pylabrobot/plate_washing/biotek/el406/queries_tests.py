# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Query methods.

This module contains tests for Query methods.
"""

# Import the backend module
from pylabrobot.plate_washing.biotek.el406 import (
  EL406Sensor,
  EL406SyringeManifold,
  EL406WasherManifold,
  ExperimentalBioTekEL406Backend,
)
from pylabrobot.plate_washing.biotek.el406.mock_tests import EL406TestCase, MockFTDI


class TestEL406BackendGetWasherManifold(EL406TestCase):
  """Test EL406 get washer manifold query."""

  async def test_request_washer_manifold_returns_enum(self):
    """request_washer_manifold should return an EL406WasherManifold enum value."""
    self.backend.io.set_read_buffer(bytes([0, 0x06]))

    result = await self.backend.request_washer_manifold()

    self.assertIsInstance(result, EL406WasherManifold)
    self.assertEqual(result, EL406WasherManifold.TUBE_96_DUAL)

  async def test_request_washer_manifold_192_tube(self):
    """request_washer_manifold should correctly identify 192-Tube manifold."""
    self.backend.io.set_read_buffer(bytes([1, 0x06]))

    result = await self.backend.request_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.TUBE_192)

  async def test_request_washer_manifold_128_tube(self):
    """request_washer_manifold should correctly identify 128-Tube manifold."""
    self.backend.io.set_read_buffer(bytes([2, 0x06]))

    result = await self.backend.request_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.TUBE_128)

  async def test_request_washer_manifold_96_tube_single(self):
    """request_washer_manifold should correctly identify 96-Tube Single manifold."""
    self.backend.io.set_read_buffer(bytes([3, 0x06]))

    result = await self.backend.request_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.TUBE_96_SINGLE)

  async def test_request_washer_manifold_deep_pin_96(self):
    """request_washer_manifold should correctly identify 96 Deep Pin manifold."""
    self.backend.io.set_read_buffer(bytes([4, 0x06]))

    result = await self.backend.request_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.DEEP_PIN_96)

  async def test_request_washer_manifold_not_installed(self):
    """request_washer_manifold should correctly identify when not installed."""
    self.backend.io.set_read_buffer(bytes([255, 0x06]))

    result = await self.backend.request_washer_manifold()

    self.assertEqual(result, EL406WasherManifold.NOT_INSTALLED)

  async def test_request_washer_manifold_sends_correct_command(self):
    """request_washer_manifold should send the correct command byte."""
    self.backend.io.set_read_buffer(bytes([0, 0x06]))

    await self.backend.request_washer_manifold()

    last_command = self.backend.io.written_data[-1]
    self.assertEqual(last_command[2], 0xD8)

  async def test_request_washer_manifold_raises_when_device_not_initialized(self):
    """request_washer_manifold should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()

    with self.assertRaises(RuntimeError):
      await backend.request_washer_manifold()

  async def test_request_washer_manifold_raises_on_timeout(self):
    """request_washer_manifold should raise TimeoutError when device does not respond."""
    self.backend.timeout = 0.01
    self.backend.io.set_read_buffer(b"")  # No response

    with self.assertRaises(TimeoutError):
      await self.backend.request_washer_manifold()

  async def test_request_washer_manifold_invalid_value(self):
    """request_washer_manifold should raise ValueError for unknown manifold type."""
    self.backend.io.set_read_buffer(bytes([100, 0x06]))

    with self.assertRaises(ValueError) as ctx:
      await self.backend.request_washer_manifold()

    self.assertIn("100", str(ctx.exception))
    self.assertIn("Unknown", str(ctx.exception))


class TestEL406BackendGetSyringeManifold(EL406TestCase):
  """Test EL406 get syringe manifold query."""

  async def test_request_syringe_manifold_returns_enum(self):
    """request_syringe_manifold should return an EL406SyringeManifold enum value."""
    self.backend.io.set_read_buffer(bytes([1, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertIsInstance(result, EL406SyringeManifold)
    self.assertEqual(result, EL406SyringeManifold.TUBE_16)

  async def test_request_syringe_manifold_not_installed(self):
    """request_syringe_manifold should correctly identify when not installed."""
    self.backend.io.set_read_buffer(bytes([0, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.NOT_INSTALLED)

  async def test_request_syringe_manifold_tube_32_large_bore(self):
    """request_syringe_manifold should correctly identify 32-Tube Large Bore manifold."""
    self.backend.io.set_read_buffer(bytes([2, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.TUBE_32_LARGE_BORE)

  async def test_request_syringe_manifold_tube_32_small_bore(self):
    """request_syringe_manifold should correctly identify 32-Tube Small Bore manifold."""
    self.backend.io.set_read_buffer(bytes([3, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.TUBE_32_SMALL_BORE)

  async def test_request_syringe_manifold_tube_16_7(self):
    """request_syringe_manifold should correctly identify 16-Tube 7 manifold."""
    self.backend.io.set_read_buffer(bytes([4, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.TUBE_16_7)

  async def test_request_syringe_manifold_tube_8(self):
    """request_syringe_manifold should correctly identify 8-Tube manifold."""
    self.backend.io.set_read_buffer(bytes([5, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.TUBE_8)

  async def test_request_syringe_manifold_plate_6_well(self):
    """request_syringe_manifold should correctly identify 6 Well Plate manifold.

    Manifold type 6 has the same value as ACK_BYTE, so a framed response is needed.
    """
    self.backend.io.set_query_response(bytes([6]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.PLATE_6_WELL)

  async def test_request_syringe_manifold_plate_12_well(self):
    """request_syringe_manifold should correctly identify 12 Well Plate manifold."""
    self.backend.io.set_read_buffer(bytes([7, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.PLATE_12_WELL)

  async def test_request_syringe_manifold_plate_24_well(self):
    """request_syringe_manifold should correctly identify 24 Well Plate manifold."""
    self.backend.io.set_read_buffer(bytes([8, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.PLATE_24_WELL)

  async def test_request_syringe_manifold_plate_48_well(self):
    """request_syringe_manifold should correctly identify 48 Well Plate manifold."""
    self.backend.io.set_read_buffer(bytes([9, 0x06]))

    result = await self.backend.request_syringe_manifold()

    self.assertEqual(result, EL406SyringeManifold.PLATE_48_WELL)

  async def test_request_syringe_manifold_sends_correct_command(self):
    """request_syringe_manifold should send the correct command byte."""
    self.backend.io.set_read_buffer(bytes([0, 0x06]))

    await self.backend.request_syringe_manifold()

    last_command = self.backend.io.written_data[-1]
    self.assertEqual(last_command[2], 0xBB)

  async def test_request_syringe_manifold_raises_when_device_not_initialized(self):
    """request_syringe_manifold should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()

    with self.assertRaises(RuntimeError):
      await backend.request_syringe_manifold()

  async def test_request_syringe_manifold_raises_on_timeout(self):
    """request_syringe_manifold should raise TimeoutError when device does not respond."""
    self.backend.timeout = 0.01
    self.backend.io.set_read_buffer(b"")  # No response

    with self.assertRaises(TimeoutError):
      await self.backend.request_syringe_manifold()

  async def test_request_syringe_manifold_invalid_value(self):
    """request_syringe_manifold should raise ValueError for unknown manifold type."""
    self.backend.io.set_read_buffer(bytes([100, 0x06]))

    with self.assertRaises(ValueError) as ctx:
      await self.backend.request_syringe_manifold()

    self.assertIn("100", str(ctx.exception))
    self.assertIn("Unknown", str(ctx.exception))


class TestEL406BackendGetSerialNumber(EL406TestCase):
  """Test EL406 get serial number query."""

  async def test_request_serial_number_various_formats(self):
    """request_serial_number should handle various serial number formats."""
    # Test with different serial number formats
    test_cases = [
      (b"SN123456", "SN123456"),
      (b"EL406-A1B2C3", "EL406-A1B2C3"),
      (b"12345", "12345"),
      (b"ABC", "ABC"),
    ]

    for response_data, expected_serial in test_cases:
      self.backend.io.set_query_response(response_data)
      result = await self.backend.request_serial_number()
      self.assertEqual(result, expected_serial)

  async def test_request_serial_number_sends_correct_command(self):
    """request_serial_number should send the correct command bytes."""
    self.backend.io.set_query_response(b"SN123")

    await self.backend.request_serial_number()

    last_command = self.backend.io.written_data[-1]
    self.assertEqual(last_command[2], 0x00)
    self.assertEqual(last_command[3], 0x01)

  async def test_request_serial_number_raises_when_device_not_initialized(self):
    """request_serial_number should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()

    with self.assertRaises(RuntimeError):
      await backend.request_serial_number()

  async def test_request_serial_number_raises_on_timeout(self):
    """request_serial_number should raise TimeoutError when device does not respond."""
    self.backend.timeout = 0.01
    self.backend.io.set_read_buffer(b"")  # No response

    with self.assertRaises(TimeoutError):
      await self.backend.request_serial_number()

  async def test_request_serial_number_empty_response(self):
    """request_serial_number should handle empty serial (just ACK)."""
    self.backend.io.set_read_buffer(b"\x06")

    result = await self.backend.request_serial_number()

    self.assertEqual(result, "")


class TestEL406BackendGetSensorEnabled(EL406TestCase):
  """Test EL406 get sensor enabled query."""

  async def test_request_sensor_enabled_returns_true_when_enabled(self):
    """request_sensor_enabled should return True when sensor is enabled."""
    self.backend.io.set_query_response(bytes([1]))

    result = await self.backend.request_sensor_enabled(EL406Sensor.WASTE)

    self.assertTrue(result)

  async def test_request_sensor_enabled_returns_false_when_disabled(self):
    """request_sensor_enabled should return False when sensor is disabled."""
    self.backend.io.set_query_response(bytes([0]))

    result = await self.backend.request_sensor_enabled(EL406Sensor.FLUID)

    self.assertFalse(result)

  async def test_request_sensor_enabled_sends_correct_command(self):
    """request_sensor_enabled should send the correct command byte."""
    self.backend.io.set_query_response(bytes([1]))

    await self.backend.request_sensor_enabled(EL406Sensor.VACUUM)

    header = self.backend.io.written_data[-2]
    self.assertEqual(header[2], 0xD2)

  async def test_request_sensor_enabled_sends_sensor_type(self):
    """request_sensor_enabled should include sensor type in command data."""
    self.backend.io.set_query_response(bytes([1]))

    await self.backend.request_sensor_enabled(EL406Sensor.WASTE)

    header = self.backend.io.written_data[-2]
    data = self.backend.io.written_data[-1]
    full_command = header + data
    self.assertEqual(len(full_command), 12)
    self.assertEqual(full_command[2], 0xD2)
    self.assertEqual(full_command[11], 1)  # WASTE = 1

  async def test_request_sensor_enabled_sensor_types_in_command(self):
    """request_sensor_enabled should send correct sensor type byte for each sensor."""
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
      await self.backend.request_sensor_enabled(sensor)

      data = self.backend.io.written_data[-1]
      self.assertEqual(
        data[0], expected_byte, f"Sensor {sensor.name} should send byte {expected_byte}"
      )

  async def test_request_sensor_enabled_raises_when_device_not_initialized(self):
    """request_sensor_enabled should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()

    with self.assertRaises(RuntimeError):
      await backend.request_sensor_enabled(EL406Sensor.VACUUM)

  async def test_request_sensor_enabled_raises_on_timeout(self):
    """request_sensor_enabled should raise TimeoutError when device does not respond."""
    self.backend.timeout = 0.01
    self.backend.io.set_read_buffer(b"")  # No response

    with self.assertRaises(TimeoutError):
      await self.backend.request_sensor_enabled(EL406Sensor.VACUUM)


class TestGetSyringeBoxInfo(EL406TestCase):
  """Test request_syringe_box_info functionality."""

  async def test_request_syringe_box_info_parses_response_correctly(self):
    """request_syringe_box_info should correctly parse box_type and box_size."""
    self.backend.io.set_read_buffer(b"\x02\x64\x06")
    result = await self.backend.request_syringe_box_info()
    self.assertEqual(result["box_type"], 2)
    self.assertEqual(result["box_size"], 100)
    self.assertTrue(result["installed"])

  async def test_request_syringe_box_info_not_installed(self):
    """request_syringe_box_info should report not installed when box_type is 0."""
    self.backend.io.set_read_buffer(b"\x00\x00\x06")
    result = await self.backend.request_syringe_box_info()
    self.assertEqual(result["box_type"], 0)
    self.assertFalse(result["installed"])

  async def test_request_syringe_box_info_sends_command(self):
    """request_syringe_box_info should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    self.backend.io.set_read_buffer(b"\x01\x32\x06")
    await self.backend.request_syringe_box_info()
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_request_syringe_box_info_raises_when_device_not_initialized(self):
    """request_syringe_box_info should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()
    with self.assertRaises(RuntimeError):
      await backend.request_syringe_box_info()


class TestGetPeristalticInstalled(EL406TestCase):
  """Test request_peristaltic_installed functionality."""

  async def test_request_peristaltic_installed_true_when_installed(self):
    """request_peristaltic_installed should return True when pump is installed."""
    self.backend.io.set_read_buffer(b"\x01\x06")  # Installed
    result = await self.backend.request_peristaltic_installed(selector=0)
    self.assertTrue(result)

  async def test_request_peristaltic_installed_false_when_not_installed(self):
    """request_peristaltic_installed should return False when pump is not installed."""
    self.backend.io.set_read_buffer(b"\x00\x06")  # Not installed
    result = await self.backend.request_peristaltic_installed(selector=0)
    self.assertFalse(result)

  async def test_request_peristaltic_installed_validates_selector(self):
    """request_peristaltic_installed should validate selector value."""
    with self.assertRaises(ValueError):
      await self.backend.request_peristaltic_installed(selector=-1)

  async def test_request_peristaltic_installed_accepts_valid_selectors(self):
    """request_peristaltic_installed should accept valid selector values."""
    for selector in [0, 1]:  # Primary and secondary
      self.backend.io.set_read_buffer(b"\x01\x06")
      # Should not raise
      result = await self.backend.request_peristaltic_installed(selector=selector)
      self.assertIsInstance(result, bool)

  async def test_request_peristaltic_installed_sends_command(self):
    """request_peristaltic_installed should send a command to the device."""
    initial_count = len(self.backend.io.written_data)
    self.backend.io.set_read_buffer(b"\x01\x06")
    await self.backend.request_peristaltic_installed(selector=0)
    self.assertGreater(len(self.backend.io.written_data), initial_count)

  async def test_request_peristaltic_installed_includes_selector_in_command(self):
    """request_peristaltic_installed should include selector in command."""
    self.backend.io.set_read_buffer(b"\x01\x06")
    await self.backend.request_peristaltic_installed(selector=1)
    last_command = self.backend.io.written_data[-1]
    # Selector should be in the command
    self.assertIn(1, list(last_command))

  async def test_request_peristaltic_installed_raises_when_device_not_initialized(self):
    """request_peristaltic_installed should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()
    with self.assertRaises(RuntimeError):
      await backend.request_peristaltic_installed(selector=0)


class TestGetInstrumentSettings(EL406TestCase):
  """Test request_instrument_settings functionality."""

  def _build_multi_query_buffer(self):
    """Build mock buffer with 5 sequential query responses for request_instrument_settings."""
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

  async def _enter_lifespan(self, stack):
    await super()._enter_lifespan(stack)
    self.backend.io.read_buffer = self._build_multi_query_buffer()

  async def test_request_instrument_settings_returns_dict(self):
    """request_instrument_settings should return a dictionary."""
    result = await self.backend.request_instrument_settings()
    self.assertIsInstance(result, dict)

  async def test_request_instrument_settings_queries_hardware(self):
    """request_instrument_settings should query multiple hardware settings."""
    result = await self.backend.request_instrument_settings()
    self.assertIn("washer_manifold", result)
    self.assertIn("syringe_manifold", result)
    self.assertIn("syringe_box", result)
    self.assertIn("peristaltic_pump_1", result)
    self.assertIn("peristaltic_pump_2", result)

  async def test_request_instrument_settings_returns_correct_values(self):
    """request_instrument_settings should return correct hardware configuration."""
    result = await self.backend.request_instrument_settings()
    self.assertEqual(result["washer_manifold"], EL406WasherManifold.TUBE_96_DUAL)
    self.assertEqual(result["syringe_manifold"], EL406SyringeManifold.TUBE_32_LARGE_BORE)
    self.assertEqual(result["syringe_box"]["installed"], True)
    self.assertEqual(result["syringe_box"]["box_type"], 1)
    self.assertEqual(result["syringe_box"]["box_size"], 100)
    self.assertEqual(result["peristaltic_pump_1"], True)
    self.assertEqual(result["peristaltic_pump_2"], False)

  async def test_request_instrument_settings_raises_when_device_not_initialized(self):
    """request_instrument_settings should raise RuntimeError if device not initialized."""
    backend = ExperimentalBioTekEL406Backend()
    with self.assertRaises(RuntimeError):
      await backend.request_instrument_settings()
