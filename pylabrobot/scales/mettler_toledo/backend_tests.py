"""Tests for MT-SICS response parsing, validation, and protocol simulation."""

import unittest

from pylabrobot.scales.mettler_toledo.backend import (
  MettlerToledoResponse,
  MettlerToledoWXS205SDUBackend,
)
from pylabrobot.scales.mettler_toledo.errors import MettlerToledoError
from pylabrobot.scales.mettler_toledo.simulator import MettlerToledoSICSSimulator
from pylabrobot.scales.scale import Scale

R = MettlerToledoResponse


class MTSICSResponseParsingTests(unittest.TestCase):
  """Tests for response parsing helpers - no hardware or simulator needed."""

  def setUp(self):
    self.backend = MettlerToledoWXS205SDUBackend.__new__(MettlerToledoWXS205SDUBackend)

  def test_parse_errors_ES_ET_EL(self):
    """General error codes (ES, ET, EL) must raise the correct MettlerToledoError.
    These are the first line of defense against protocol-level failures."""
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("ES", ""))
    self.assertIn("Syntax error", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("ET", ""))
    self.assertIn("Transmission error", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("EL", ""))
    self.assertIn("Logical error", str(ctx.exception))

  def test_parse_errors_status_codes(self):
    """Command-specific status codes (I, L, +, -) must raise descriptive errors.
    These catch device-busy, bad parameters, and overload/underload conditions."""
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "I"))
    self.assertIn("not executable at present", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "L"))
    self.assertIn("incorrect parameter", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "+"))
    self.assertIn("overload", str(ctx.exception))

    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "-"))
    self.assertIn("underload", str(ctx.exception))

  def test_validate_response_rejects_short(self):
    """Responses with fewer fields than expected must be rejected.
    Prevents silent IndexError when accessing data fields."""
    with self.assertRaises(MettlerToledoError):
      MettlerToledoWXS205SDUBackend._validate_response(R("I4", "A"), 3, "I4")

    # should not raise
    MettlerToledoWXS205SDUBackend._validate_response(R("I4", "A", ["B207696838"]), 3, "I4")

  def test_validate_unit_rejects_wrong(self):
    """Non-gram unit responses must be rejected.
    The backend assumes grams throughout - a wrong unit would produce wrong values."""
    with self.assertRaises(MettlerToledoError):
      MettlerToledoWXS205SDUBackend._validate_unit("kg", "S")

    # should not raise
    MettlerToledoWXS205SDUBackend._validate_unit("g", "S")

  def test_parse_errors_passes_valid_success(self):
    """A valid success response (status A) must not raise.
    Ensures the happy path is not accidentally blocked."""
    self.backend._parse_basic_errors(R("Z", "A"))

  def test_parse_errors_weight_response_error(self):
    """S S Error responses (hardware faults detected during weighing) must raise.
    These indicate boot errors, EEPROM failures, etc. on the physical device."""
    with self.assertRaises(MettlerToledoError) as ctx:
      self.backend._parse_basic_errors(R("S", "S", ["Error", "10b"]))
    self.assertIn("EEPROM error", str(ctx.exception))

  def test_dataclass_construction(self):
    """MettlerToledoResponse dataclass must correctly separate command, status, and data.
    This is the foundation for all response access throughout the backend."""
    resp = R("S", "S", ["0.00006", "g"])
    self.assertEqual(resp.command, "S")
    self.assertEqual(resp.status, "S")
    self.assertEqual(resp.data, ["0.00006", "g"])

    # Error-only response (no status)
    resp = R("ES", "")
    self.assertEqual(resp.command, "ES")
    self.assertEqual(resp.status, "")
    self.assertEqual(resp.data, [])


class MTSICSSimulatorTests(unittest.IsolatedAsyncioTestCase):
  """Tests for the MT-SICS protocol simulator.
  These exercise the full stack: send_command -> mock response -> parse -> validate -> return.
  Catches dataclass construction bugs, index mapping errors, and response format mismatches."""

  async def asyncSetUp(self):
    self.backend = MettlerToledoSICSSimulator()
    self.scale = Scale(
      name="test_scale",
      backend=self.backend,
      size_x=0,
      size_y=0,
      size_z=0,
    )
    await self.scale.setup()

  async def test_setup_populates_device_identity(self):
    """setup() must populate device_type, serial_number, capacity, and MT-SICS levels.
    If any of these are missing, downstream methods that depend on them will fail."""
    self.assertEqual(self.backend.device_type, "WXS205SDU")
    self.assertEqual(self.backend.serial_number, "SIM0000001")
    self.assertEqual(self.backend.capacity, 220.0)
    self.assertIn("S", self.backend._supported_commands)
    self.assertIn("M28", self.backend._supported_commands)

  async def test_tare_workflow_through_protocol(self):
    """Full tare workflow through the MT-SICS protocol layer.
    Verifies that mock responses are correctly constructed, parsed through
    _parse_basic_errors, and returned as the right value."""
    self.backend.platform_weight = 50.0
    await self.scale.tare()
    self.backend.sample_weight = 10.0
    weight = await self.scale.read_weight()
    self.assertEqual(weight, 10.0)

  async def test_read_weight_returns_correct_value(self):
    """read_weight must return the net weight (sensor - zero_offset - tare).
    Tests the data[0] index mapping after the dataclass change."""
    self.backend.platform_weight = 25.0
    weight = await self.scale.read_weight(timeout=0)
    self.assertEqual(weight, 25.0)

  async def test_request_capacity_from_i2(self):
    """request_capacity must parse the capacity from the I2 response data[1] field.
    Wrong index mapping would return the device type string instead of a float."""
    capacity = await self.backend.request_capacity()
    self.assertEqual(capacity, 220.0)

  async def test_i50_multi_response(self):
    """I50 returns 3 response lines (B, B, A). send_command must read all of them
    and return the correct remaining range from the first line."""
    self.backend.platform_weight = 50.0
    remaining = await self.backend.request_remaining_weighing_range()
    self.assertEqual(remaining, 170.0)

  async def test_cancel_returns_serial_number(self):
    """cancel() sends @ which responds with I4-style (command echo is I4, not @).
    Must correctly parse the serial number despite the unusual response format."""
    sn = await self.backend.cancel()
    self.assertEqual(sn, "SIM0000001")

  async def test_unknown_command_returns_syntax_error(self):
    """Unknown commands must return ES (syntax error) response.
    Ensures the simulator correctly simulates the device rejecting invalid commands."""
    with self.assertRaises(MettlerToledoError) as ctx:
      await self.backend.send_command("XYZNOTREAL")
    self.assertIn("Syntax error", str(ctx.exception))

  async def test_measure_temperature(self):
    """measure_temperature must return a float from the M28 response.
    Requires M28 in the device's I0 command list."""
    temp = await self.backend.measure_temperature()
    self.assertEqual(temp, 22.5)

  async def test_measure_temperature_blocked_when_unsupported(self):
    """measure_temperature must raise when M28 is not in the device's command list.
    Validates that I0-based command gating works correctly."""
    backend = MettlerToledoSICSSimulator(
      supported_commands={"@", "I0", "I2", "I4", "S", "SI", "Z", "ZI", "T", "TI", "TA", "TAC"},
    )
    scale = Scale(name="limited_scale", backend=backend, size_x=0, size_y=0, size_z=0)
    await scale.setup()
    with self.assertRaises(MettlerToledoError) as ctx:
      await backend.measure_temperature()
    self.assertIn("M28", str(ctx.exception))
    self.assertIn("not implemented", str(ctx.exception))


if __name__ == "__main__":
  unittest.main()
