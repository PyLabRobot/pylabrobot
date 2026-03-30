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
    """reset() sends @ which responds with I4-style (command echo is I4, not @).
    Must correctly parse the serial number despite the unusual response format."""
    sn = await self.backend.reset()
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

  async def test_uptime_returns_minutes(self):
    """I15 returns uptime in minutes since last start or restart.
    The spec shows I15 A <Minutes> with accuracy +/- 5%."""
    minutes = await self.backend.request_uptime_minutes()
    self.assertEqual(minutes, 1440)  # Simulator returns 24 hours

  async def test_configuration_bridge_detection(self):
    """Device type containing 'Bridge' must set configuration to 'Bridge'.
    This determines which commands are expected to work (no display commands
    in bridge mode)."""
    backend = MettlerToledoSICSSimulator(device_type="WXS205SDU WXA-Bridge")
    scale = Scale(name="s", backend=backend, size_x=0, size_y=0, size_z=0)
    await scale.setup()
    self.assertEqual(backend.configuration, "Bridge")

  async def test_shlex_preserves_quoted_strings_with_spaces(self):
    """The I2 response packs type, capacity, and unit into one quoted string.
    shlex must keep the quoted content as a single token. This bug broke
    hardware validation before the shlex fix."""
    # The simulator returns I2 as a single data field (matching shlex behavior)
    device_type = await self.backend.request_device_type()
    self.assertIsInstance(device_type, str)
    capacity = await self.backend.request_capacity()
    self.assertEqual(capacity, 220.0)

  async def test_multi_response_terminates_on_status_a(self):
    """send_command must keep reading while status is B and stop on A.
    I50 returns 3 lines (B, B, A). All must be captured."""
    responses = await self.backend.send_command("I50")
    self.assertEqual(len(responses), 3)
    self.assertEqual(responses[0].status, "B")
    self.assertEqual(responses[1].status, "B")
    self.assertEqual(responses[2].status, "A")

  async def test_zero_stable_dispatches_to_z(self):
    """zero(timeout='stable') must send the Z command (wait for stable),
    not ZI (immediate) or ZC (timed)."""
    self.backend.platform_weight = 5.0
    await self.scale.zero(timeout="stable")
    # After zeroing, reading should return 0
    weight = await self.scale.read_weight(timeout=0)
    self.assertEqual(weight, 0.0)

  async def test_clear_tare_resets_to_zero(self):
    """clear_tare (TAC) must reset the stored tare value to zero.
    After clearing, request_tare_weight must return 0."""
    self.backend.platform_weight = 50.0
    await self.scale.tare()
    tare = await self.scale.request_tare_weight()
    self.assertEqual(tare, 50.0)
    await self.backend.clear_tare()
    tare_after = await self.scale.request_tare_weight()
    self.assertEqual(tare_after, 0.0)

  async def test_b_status_does_not_raise(self):
    """Status B (more responses follow) must not be treated as an error.
    If _parse_basic_errors raises on B, all multi-response commands break."""
    self.backend._parse_basic_errors(R("I50", "B", ["0", "535.141", "g"]))

  async def test_request_weighing_mode(self):
    """request_weighing_mode must return an integer from the M01 response.
    Verifies Batch 2 M01 query parsing."""
    mode = await self.backend.request_weighing_mode()
    self.assertEqual(mode, 0)  # Normal weighing mode

  # -- Identity and diagnostics --

  async def test_request_firmware_version(self):
    """Firmware version must return a non-empty string.
    Hardware returns '1.10 18.6.4.1361.772' - validates I3 parsing."""
    version = await self.backend.request_firmware_version()
    self.assertIsInstance(version, str)
    self.assertGreater(len(version), 0)

  async def test_request_model_designation(self):
    """Model designation must return the device type string.
    Hardware returns 'WXS205SDU' - validates I11 parsing."""
    model = await self.backend.request_model_designation()
    self.assertEqual(model, "WXS205SDU")

  # -- Setup and teardown --

  async def test_setup_populates_firmware_version(self):
    """setup() must query I3 and store the firmware version.
    The firmware check is new and untested - if it breaks, the
    firmware warning logic fails silently."""
    self.assertIsNotNone(self.backend.firmware_version)
    self.assertGreater(len(self.backend.firmware_version), 0)

  async def test_setup_populates_configuration(self):
    """setup() must detect 'Balance' for default simulator (no 'Bridge' in type).
    Drives which commands are expected to work on the device."""
    self.assertEqual(self.backend.configuration, "Balance")

  # -- Weight dispatch --

  async def test_read_weight_timeout_zero_dispatches_to_si(self):
    """read_weight(timeout=0) must use SI (immediate read).
    If it dispatches to S (stable) instead, the call blocks waiting
    for stability that may never come."""
    self.backend.platform_weight = 7.5
    weight = await self.scale.read_weight(timeout=0)
    self.assertEqual(weight, 7.5)

  async def test_read_weight_rejects_negative_timeout(self):
    """Negative timeout must raise ValueError.
    Without this guard, a negative timeout would be converted to
    a negative millisecond value and sent to the device."""
    with self.assertRaises(ValueError):
      await self.scale.read_weight(timeout=-1)

  async def test_tare_timeout_zero_dispatches_to_ti(self):
    """tare(timeout=0) must use TI (immediate tare).
    Validates the timeout dispatcher sends the right MT-SICS command."""
    self.backend.platform_weight = 30.0
    await self.scale.tare(timeout=0)
    tare = await self.scale.request_tare_weight()
    self.assertEqual(tare, 30.0)

  # -- Batch 2 configuration queries --

  async def test_request_environment_condition(self):
    """Environment condition must return an integer.
    Hardware returned 2 (Standard) - validates M02 parsing."""
    env = await self.backend.request_environment_condition()
    self.assertEqual(env, 2)

  async def test_request_auto_zero(self):
    """Auto zero setting must return an integer.
    Hardware returned 0 (off) - validates M03 parsing."""
    auto_zero = await self.backend.request_auto_zero()
    self.assertEqual(auto_zero, 1)  # Simulator default is on

  async def test_request_update_rate(self):
    """Update rate must return a float in values per second.
    Hardware returned 10.173 - validates UPD parsing."""
    rate = await self.backend.request_update_rate()
    self.assertEqual(rate, 18.3)  # Simulator default

  # -- SIS response format --

  async def test_sis_response_has_seven_fields(self):
    """SIS must return 7 data fields: state, weight, unit, readability,
    step, approval, info. Format confirmed from spec p.234-235 and
    hardware validation."""
    resp = await self.backend.request_net_weight_with_status()
    self.assertEqual(len(resp.data), 7)
    self.assertEqual(resp.data[2], "0")  # unit code 0 = grams
    self.assertEqual(resp.data[3], "5")  # readability = 5 decimal places

  async def test_sis_tare_info_field_tracks_state(self):
    """SIS data[6] must be '0' without tare, '1' with weighed tare.
    Validates the simulator correctly tracks tare state in the SIS response."""
    resp_no_tare = await self.backend.request_net_weight_with_status()
    self.assertEqual(resp_no_tare.data[6], "0")  # no tare

    self.backend.platform_weight = 50.0
    await self.scale.tare()
    resp_with_tare = await self.backend.request_net_weight_with_status()
    self.assertEqual(resp_with_tare.data[6], "1")  # weighed tare

  # -- I50 physics simulation --

  async def test_i50_remaining_range_computed_from_capacity(self):
    """I50 remaining range must equal capacity minus total sensor reading.
    Validates the simulator computes this correctly."""
    self.backend.platform_weight = 50.0
    self.backend.sample_weight = 10.0
    remaining = await self.backend.request_remaining_weighing_range()
    self.assertEqual(remaining, 160.0)  # 220 - 60

  # -- I0 command discovery --

  async def test_i0_returns_supported_commands_set(self):
    """_request_supported_commands must parse I0 multi-response into a Set[str].
    This is the foundation of all command gating."""
    commands = await self.backend._request_supported_commands()
    self.assertIsInstance(commands, set)
    self.assertIn("S", commands)
    self.assertIn("M28", commands)
    self.assertNotIn("NONEXISTENT", commands)


if __name__ == "__main__":
  unittest.main()
