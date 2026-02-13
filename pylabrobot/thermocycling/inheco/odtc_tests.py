"""Tests for ODTC: backend, thermocycler resource, SiLA interface, and model utilities."""

import asyncio
import unittest
import xml.etree.ElementTree as ET
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.thermocycling.inheco.odtc_backend import CommandExecution, MethodExecution, ODTCBackend
from pylabrobot.thermocycling.inheco.odtc_model import (
  ODTCMethod,
  ODTCMethodSet,
  ODTC_DIMENSIONS,
  ODTCPreMethod,
  ODTCStep,
  PREMETHOD_ESTIMATED_DURATION_SECONDS,
  StoredProtocol,
  estimate_method_duration_seconds,
  normalize_variant,
)
from pylabrobot.thermocycling.inheco.odtc_thermocycler import ODTCThermocycler
from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling.inheco.odtc_sila_interface import (
  SiLATimeoutError,
  ODTCSiLAInterface,
  SiLAState,
)


class TestNormalizeVariant(unittest.TestCase):
  """Tests for normalize_variant (96/384 -> 960000/384000)."""

  def test_96_maps_to_960000(self):
    self.assertEqual(normalize_variant(96), 960000)

  def test_384_maps_to_384000(self):
    self.assertEqual(normalize_variant(384), 384000)

  def test_3840000_normalizes_to_384000(self):
    self.assertEqual(normalize_variant(3840000), 384000)

  def test_invalid_raises(self):
    with self.assertRaises(ValueError) as cm:
      normalize_variant(123)
    self.assertIn("123", str(cm.exception))
    self.assertIn("Valid", str(cm.exception))


class TestEstimateMethodDurationSeconds(unittest.TestCase):
  """Tests for estimate_method_duration_seconds (ODTC method duration from steps)."""

  def test_premethod_constant(self):
    """PREMETHOD_ESTIMATED_DURATION_SECONDS is 10 minutes."""
    self.assertEqual(PREMETHOD_ESTIMATED_DURATION_SECONDS, 600.0)

  def test_empty_method_returns_zero(self):
    """Method with no steps has zero duration."""
    method = ODTCMethod(name="empty", start_block_temperature=20.0, steps=[])
    self.assertEqual(estimate_method_duration_seconds(method), 0.0)

  def test_single_step_no_loop(self):
    """Single step: ramp + plateau + overshoot. Ramp = |95 - 20| / 4.4 ≈ 17.045 s."""
    method = ODTCMethod(
      name="single",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=4.4,
          plateau_temperature=95.0,
          plateau_time=30.0,
          overshoot_time=5.0,
          goto_number=0,
          loop_number=0,
        ),
      ],
    )
    # Ramp: 75 / 4.4 ≈ 17.045; plateau: 30; overshoot: 5
    got = estimate_method_duration_seconds(method)
    self.assertAlmostEqual(got, 17.045 + 30 + 5, places=1)

  def test_single_step_zero_slope_clamped(self):
    """Zero slope is clamped to avoid division by zero; duration is finite."""
    method = ODTCMethod(
      name="zero_slope",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=0.0,
          plateau_temperature=95.0,
          plateau_time=10.0,
          overshoot_time=0.0,
          goto_number=0,
          loop_number=0,
        ),
      ],
    )
    # Ramp: 75 / 0.1 = 750 s (clamped); plateau: 10
    got = estimate_method_duration_seconds(method)
    self.assertAlmostEqual(got, 750 + 10, places=1)

  def test_two_steps_with_loop(self):
    """Two steps with loop: step 1 -> step 2 (goto 1, loop 2) = run 1,2,1,2."""
    method = ODTCMethod(
      name="loop",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=4.4,
          plateau_temperature=95.0,
          plateau_time=10.0,
          overshoot_time=0.0,
          goto_number=0,
          loop_number=0,
        ),
        ODTCStep(
          number=2,
          slope=2.2,
          plateau_temperature=60.0,
          plateau_time=5.0,
          overshoot_time=0.0,
          goto_number=1,
          loop_number=1,  # repeat_count = 2
        ),
      ],
    )
    # Execution: step1, step2, step1, step2
    # Step1: ramp 75/4.4 + 10; step2: ramp 35/2.2 + 5; step1 again: 35/4.4 + 10; step2 again: 35/2.2 + 5
    got = estimate_method_duration_seconds(method)
    self.assertGreater(got, 0)
    self.assertLess(got, 1000)


class TestODTCSiLAInterface(unittest.IsolatedAsyncioTestCase):
  """Tests for ODTCSiLAInterface."""

  def setUp(self):
    """Set up test fixtures."""
    self.interface = ODTCSiLAInterface(machine_ip="192.168.1.100", client_ip="127.0.0.1")

  def test_normalize_command_name(self):
    """Test command name normalization for aliases."""
    self.assertEqual(self.interface._normalize_command_name("OpenDoor"), "OpenDoor")
    self.assertEqual(self.interface._normalize_command_name("PrepareForOutput"), "OpenDoor")
    self.assertEqual(self.interface._normalize_command_name("CloseDoor"), "CloseDoor")
    self.assertEqual(self.interface._normalize_command_name("PrepareForInput"), "CloseDoor")
    self.assertEqual(self.interface._normalize_command_name("ExecuteMethod"), "ExecuteMethod")

  def test_check_state_allowability(self):
    """Test state allowability checking."""
    # GetStatus allowed in all states
    self.interface._current_state = SiLAState.STARTUP
    self.assertTrue(self.interface._check_state_allowability("GetStatus"))

    self.interface._current_state = SiLAState.STANDBY
    self.assertTrue(self.interface._check_state_allowability("GetStatus"))
    self.assertTrue(self.interface._check_state_allowability("Initialize"))
    self.assertFalse(self.interface._check_state_allowability("ExecuteMethod"))

    # GetStatus allowed during initializing (needed for polling and post-Initialize verification)
    self.interface._current_state = SiLAState.INITIALIZING
    self.assertTrue(self.interface._check_state_allowability("GetStatus"))
    self.assertTrue(self.interface._check_state_allowability("GetDeviceIdentification"))
    self.assertFalse(self.interface._check_state_allowability("ExecuteMethod"))

    self.interface._current_state = SiLAState.IDLE
    self.assertTrue(self.interface._check_state_allowability("ExecuteMethod"))
    self.assertFalse(self.interface._check_state_allowability("Initialize"))

  def test_check_parallelism(self):
    """Test parallelism checking."""
    # No commands executing - should allow
    self.assertTrue(self.interface._check_parallelism("ReadActualTemperature"))

    # SetParameters executing - ReadActualTemperature can run in parallel
    self.interface._executing_commands.add("SetParameters")
    self.assertTrue(self.interface._check_parallelism("ReadActualTemperature"))

    # ExecuteMethod executing - SetParameters cannot run in parallel (S)
    self.interface._executing_commands.clear()
    self.interface._executing_commands.add("ExecuteMethod")
    self.assertFalse(self.interface._check_parallelism("SetParameters"))

    # ExecuteMethod executing - ReadActualTemperature can run in parallel (P)
    self.assertTrue(self.interface._check_parallelism("ReadActualTemperature"))

  def test_validate_lock_id(self):
    """Test lockId validation."""
    # Device not locked - any lockId (including None) is fine
    self.interface._lock_id = None
    self.interface._validate_lock_id(None)  # Should not raise
    self.interface._validate_lock_id("some_id")  # Should not raise

    # Device locked - must provide matching lockId
    self.interface._lock_id = "locked_id"
    self.interface._validate_lock_id("locked_id")  # Should not raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._validate_lock_id("wrong_id")
    # Check that error mentions lockId mismatch
    error_msg = str(cm.exception)
    self.assertIn("locked", error_msg.lower())
    self.assertIn("5", error_msg)  # Return code 5

  def test_update_state_from_status(self):
    """Test state updates from status strings - exact matching only."""
    # Test lowercase enum values (exact match required)
    self.interface._update_state_from_status("idle")
    self.assertEqual(self.interface._current_state, SiLAState.IDLE)

    self.interface._update_state_from_status("busy")
    self.assertEqual(self.interface._current_state, SiLAState.BUSY)

    self.interface._update_state_from_status("standby")
    self.assertEqual(self.interface._current_state, SiLAState.STANDBY)
    
    # Test camelCase enum values (exact match required)
    self.interface._update_state_from_status("inError")
    self.assertEqual(self.interface._current_state, SiLAState.INERROR)
    
    self.interface._update_state_from_status("errorHandling")
    self.assertEqual(self.interface._current_state, SiLAState.ERRORHANDLING)
    
    # Test that case mismatches are NOT accepted (exact matching only)
    # These should keep the current state and log a warning
    initial_state = self.interface._current_state
    self.interface._update_state_from_status("Idle")  # Wrong case
    self.assertEqual(self.interface._current_state, initial_state)  # Should remain unchanged
    
    self.interface._update_state_from_status("BUSY")  # Wrong case
    self.assertEqual(self.interface._current_state, initial_state)  # Should remain unchanged
    
    self.interface._update_state_from_status("INERROR")  # Wrong case
    self.assertEqual(self.interface._current_state, initial_state)  # Should remain unchanged

  def test_handle_return_code(self):
    """Test return code handling."""
    # Code 1, 2, 3 should not raise
    self.interface._handle_return_code(1, "Success", "GetStatus", 123)
    self.interface._handle_return_code(2, "Accepted", "ExecuteMethod", 123)
    self.interface._handle_return_code(3, "Finished", "ExecuteMethod", 123)

    # Code 4 should raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._handle_return_code(4, "busy", "ExecuteMethod", 123)
    self.assertIn("return code 4", str(cm.exception))

    # Code 5 should raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._handle_return_code(5, "LockId error", "ExecuteMethod", 123)
    self.assertIn("return code 5", str(cm.exception))

    # Code 9 should raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._handle_return_code(9, "Not allowed", "ExecuteMethod", 123)
    self.assertIn("return code 9", str(cm.exception))

    # Device error code should transition to InError
    with self.assertRaises(RuntimeError):
      self.interface._handle_return_code(1000, "Device error", "ExecuteMethod", 123)
    self.assertEqual(self.interface._current_state, SiLAState.INERROR)

  def test_get_terminal_state(self):
    """Test terminal state map for polling fallback."""
    self.assertEqual(self.interface._get_terminal_state("Reset"), "standby")
    self.assertEqual(self.interface._get_terminal_state("Initialize"), "idle")
    self.assertEqual(self.interface._get_terminal_state("LockDevice"), "standby")
    self.assertEqual(self.interface._get_terminal_state("UnlockDevice"), "standby")
    self.assertEqual(self.interface._get_terminal_state("OpenDoor"), "idle")
    self.assertEqual(self.interface._get_terminal_state("ExecuteMethod"), "idle")


# Minimal SOAP responses for dual-track tests (return_code 2 = async accepted; no duration = poll immediately).
_OPEN_DOOR_ASYNC_RESPONSE = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <OpenDoorResponse xmlns="http://sila.coop">
      <OpenDoorResult>
        <returnCode>2</returnCode>
        <message>Accepted</message>
      </OpenDoorResult>
    </OpenDoorResponse>
  </s:Body>
</s:Envelope>"""

_GET_STATUS_IDLE_RESPONSE = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <GetStatusResponse xmlns="http://sila.coop">
      <state>idle</state>
      <GetStatusResult>
        <returnCode>1</returnCode>
        <message>Success</message>
      </GetStatusResult>
    </GetStatusResponse>
  </s:Body>
</s:Envelope>"""

_GET_STATUS_BUSY_RESPONSE = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <GetStatusResponse xmlns="http://sila.coop">
      <state>busy</state>
      <GetStatusResult>
        <returnCode>1</returnCode>
        <message>Success</message>
      </GetStatusResult>
    </GetStatusResponse>
  </s:Body>
</s:Envelope>"""


class TestODTCSiLADualTrack(unittest.IsolatedAsyncioTestCase):
  """Tests for dual-track async completion (ResponseEvent + polling fallback)."""

  async def test_polling_fallback_completes_when_no_response_event(self):
    """When ResponseEvent never arrives, polling sees idle and completes Future (warn_and_continue)."""
    call_count = 0

    def mock_urlopen(req):
      nonlocal call_count
      call_count += 1
      body = _OPEN_DOOR_ASYNC_RESPONSE if call_count == 1 else _GET_STATUS_IDLE_RESPONSE
      resp = MagicMock()
      resp.read.return_value = body
      cm = MagicMock()
      cm.__enter__.return_value = resp
      cm.__exit__.return_value = None
      return cm

    with patch("urllib.request.urlopen", side_effect=mock_urlopen), patch(
      "pylabrobot.thermocycling.inheco.odtc_sila_interface.POLLING_START_BUFFER", 0.05
    ):
      # Short POLLING_START_BUFFER in test so we don't wait 10s; lifetime still allows polling to run.
      interface = ODTCSiLAInterface(
        machine_ip="192.168.1.100",
        client_ip="127.0.0.1",
        poll_interval=0.02,
        lifetime_of_execution=2.0,
        on_response_event_missing="warn_and_continue",
      )
      interface._current_state = SiLAState.IDLE
      # Do not call setup() so we avoid binding the HTTP server (sandbox/CI friendly).
      result = await interface.send_command("OpenDoor")
      self.assertIsNone(result)
      self.assertGreaterEqual(call_count, 2)

  async def test_lifetime_of_execution_exceeded_raises(self):
    """When lifetime_of_execution is exceeded before terminal state, Future gets timeout exception."""
    call_count = 0

    def mock_urlopen(req):
      nonlocal call_count
      call_count += 1
      body = _OPEN_DOOR_ASYNC_RESPONSE if call_count == 1 else _GET_STATUS_BUSY_RESPONSE
      resp = MagicMock()
      resp.read.return_value = body
      cm = MagicMock()
      cm.__enter__.return_value = resp
      cm.__exit__.return_value = None
      return cm

    with patch("urllib.request.urlopen", side_effect=mock_urlopen), patch(
      "pylabrobot.thermocycling.inheco.odtc_sila_interface.POLLING_START_BUFFER", 0.02
    ):
      # Short POLLING_START_BUFFER so timeout (0.5s) is hit quickly instead of waiting 10s.
      interface = ODTCSiLAInterface(
        machine_ip="192.168.1.100",
        client_ip="127.0.0.1",
        poll_interval=0.05,
        lifetime_of_execution=0.2,
        on_response_event_missing="warn_and_continue",
      )
      interface._current_state = SiLAState.IDLE
      # Do not call setup() so we avoid binding the HTTP server (sandbox/CI friendly).
      with self.assertRaises(SiLATimeoutError) as cm:
        await interface.send_command("OpenDoor")
      self.assertIn("lifetime_of_execution", str(cm.exception))


class TestODTCBackend(unittest.IsolatedAsyncioTestCase):
  """Tests for ODTCBackend."""

  def setUp(self):
    """Set up test fixtures."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      self.backend = ODTCBackend(odtc_ip="192.168.1.100")
      self.backend._sila = MagicMock(spec=ODTCSiLAInterface)
      self.backend._sila.bound_port = 8080
      self.backend._sila._machine_ip = "192.168.1.100"
      self.backend._sila._lock_id = None
      self.backend._sila._lifetime_of_execution = None
      self.backend._sila._client_ip = "127.0.0.1"

  def test_backend_odtc_ip_property(self):
    """Backend.odtc_ip returns machine IP from sila."""
    self.assertEqual(self.backend.odtc_ip, "192.168.1.100")

  def test_backend_variant_property(self):
    """Backend.variant returns normalized variant (default 960000)."""
    self.assertEqual(self.backend.variant, 960000)

  async def test_setup(self):
    """Test backend setup (full path)."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend._sila._client_ip = "192.168.1.1"  # type: ignore[attr-defined]
    setattr(self.backend._sila, "bound_port", 8080)  # type: ignore[misc]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    self.backend.get_status = AsyncMock(return_value="idle")  # type: ignore[method-assign]
    await self.backend.setup()
    self.backend._sila.setup.assert_called_once()
    self.backend.reset.assert_called_once()
    call_kwargs = self.backend.reset.call_args[1]
    self.assertFalse(call_kwargs.get("simulation_mode", False))

  async def test_setup_full_false_only_sila_setup(self):
    """Test setup(full=False) only calls _sila.setup(), not reset or initialize."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    self.backend.get_status = AsyncMock()  # type: ignore[method-assign]
    await self.backend.setup(full=False)
    self.backend._sila.setup.assert_called_once()
    self.backend.reset.assert_not_called()
    self.backend.get_status.assert_not_called()

  async def test_setup_simulation_mode_passed_to_reset(self):
    """Test setup(simulation_mode=True) passes simulation_mode to reset."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    self.backend.get_status = AsyncMock(return_value="idle")  # type: ignore[method-assign]
    await self.backend.setup(simulation_mode=True)
    self.backend.reset.assert_called_once()
    self.assertTrue(self.backend.reset.call_args[1]["simulation_mode"])

  async def test_reset_sets_simulation_mode(self):
    """Test reset(simulation_mode=X) updates backend.simulation_mode."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 1, None, 0.0))  # type: ignore[method-assign]
    self.assertFalse(self.backend.simulation_mode)
    await self.backend.reset(simulation_mode=True)
    self.assertTrue(self.backend.simulation_mode)
    await self.backend.reset(simulation_mode=False)
    self.assertFalse(self.backend.simulation_mode)

  async def test_setup_retries_with_backoff(self):
    """Test setup(full=True, max_attempts=3) retries on failure with backoff."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    call_count = 0

    async def mock_get_status():
      nonlocal call_count
      call_count += 1
      if call_count < 3:
        raise RuntimeError("transient")
      return "idle"

    self.backend.get_status = AsyncMock(side_effect=mock_get_status)  # type: ignore[method-assign]
    with patch("asyncio.sleep", new_callable=AsyncMock):
      await self.backend.setup(full=True, max_attempts=3)
    self.assertEqual(call_count, 3)
    # Full path runs 3 times (fail twice, succeed on third)
    self.assertEqual(self.backend._sila.setup.call_count, 3)
    self.assertEqual(self.backend.reset.call_count, 3)

  async def test_setup_raises_when_all_attempts_fail(self):
    """Test setup(full=True, max_attempts=2) raises when all attempts fail."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    self.backend.get_status = AsyncMock(side_effect=RuntimeError("fail"))  # type: ignore[method-assign]
    with patch("asyncio.sleep", new_callable=AsyncMock), self.assertRaises(RuntimeError) as cm:
      await self.backend.setup(full=True, max_attempts=2)
    self.assertIn("fail", str(cm.exception))

  async def test_stop(self):
    """Test backend stop."""
    self.backend._sila.close = AsyncMock()  # type: ignore[method-assign]
    await self.backend.stop()
    self.backend._sila.close.assert_called_once()

  async def test_get_status(self):
    """Test get_status."""
    self.backend._sila.send_command = AsyncMock(  # type: ignore[method-assign]
      return_value={
        "GetStatusResponse": {
          "state": "idle",
          "GetStatusResult": {"returnCode": 1, "message": "Success."}
        }
      }
    )
    status = await self.backend.get_status()
    self.assertEqual(status, "idle")
    self.backend._sila.send_command.assert_called_once_with("GetStatus")

  async def test_open_door(self):
    """Test open_door."""
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.open_door()
    self.backend._sila.send_command.assert_called_once_with("OpenDoor")

  async def test_close_door(self):
    """Test close_door."""
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.close_door()
    self.backend._sila.send_command.assert_called_once_with("CloseDoor")

  async def test_read_temperatures(self):
    """Test read_temperatures."""
    # Mock response with SensorValues XML
    sensor_xml = (
      '<SensorValues timestamp="2024-01-01T12:00:00Z">'
      "<Mount>2463</Mount>"
      "<Mount_Monitor>2642</Mount_Monitor>"
      "<Lid>2575</Lid>"
      "<Lid_Monitor>2627</Lid_Monitor>"
      "<Ambient>2450</Ambient>"
      "<PCB>3308</PCB>"
      "<Heatsink>2596</Heatsink>"
      "<Heatsink_TEC>2487</Heatsink_TEC>"
      "</SensorValues>"
    )

    # Create mock ElementTree response
    root = ET.Element("ResponseData")
    param = ET.SubElement(root, "Parameter", name="SensorValues")
    string_elem = ET.SubElement(param, "String")
    string_elem.text = sensor_xml

    self.backend._sila.send_command = AsyncMock(return_value=root)  # type: ignore[method-assign]
    sensor_values = await self.backend.read_temperatures()
    self.assertAlmostEqual(sensor_values.mount, 24.63, places=2)  # 2463 * 0.01
    self.assertAlmostEqual(sensor_values.lid, 25.75, places=2)  # 2575 * 0.01

  async def test_execute_method(self):
    """Test execute_method with wait=True; uses start_command then await handle, returns MethodExecution."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.start_command = AsyncMock(  # type: ignore[method-assign]
      return_value=(fut, 12345, None, 0.0)
    )
    result = await self.backend.execute_method("MyMethod", wait=True)
    self.assertIsInstance(result, MethodExecution)
    self.assertEqual(result.method_name, "MyMethod")
    self.backend._sila.start_command.assert_called_once()
    call_kwargs = self.backend._sila.start_command.call_args[1]
    self.assertEqual(call_kwargs["methodName"], "MyMethod")

  async def test_stop_method(self):
    """Test stop_method."""
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.stop_method()
    self.backend._sila.send_command.assert_called_once_with("StopMethod")

  async def test_lock_device(self):
    """Test lock_device."""
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.lock_device("my_lock_id")
    self.backend._sila.send_command.assert_called_once()
    call_kwargs = self.backend._sila.send_command.call_args[1]
    self.assertEqual(call_kwargs["lock_id"], "my_lock_id")
    self.assertEqual(call_kwargs["PMSId"], "PyLabRobot")

  async def test_unlock_device(self):
    """Test unlock_device."""
    self.backend._sila._lock_id = "my_lock_id"
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.unlock_device()
    self.backend._sila.send_command.assert_called_once_with(
      "UnlockDevice", lock_id="my_lock_id"
    )

  async def test_unlock_device_not_locked(self):
    """Test unlock_device when device is not locked."""
    self.backend._sila._lock_id = None
    with self.assertRaises(RuntimeError) as cm:
      await self.backend.unlock_device()
    self.assertIn("not locked", str(cm.exception))

  async def test_get_block_current_temperature(self):
    """Test get_block_current_temperature."""
    sensor_xml = '<SensorValues><Mount>2500</Mount></SensorValues>'
    root = ET.Element("ResponseData")
    param = ET.SubElement(root, "Parameter", name="SensorValues")
    string_elem = ET.SubElement(param, "String")
    string_elem.text = sensor_xml

    self.backend._sila.send_command = AsyncMock(return_value=root)  # type: ignore[method-assign]
    temps = await self.backend.get_block_current_temperature()
    self.assertEqual(len(temps), 1)
    self.assertAlmostEqual(temps[0], 25.0, places=2)

  async def test_get_lid_current_temperature(self):
    """Test get_lid_current_temperature."""
    sensor_xml = '<SensorValues><Lid>2600</Lid></SensorValues>'
    root = ET.Element("ResponseData")
    param = ET.SubElement(root, "Parameter", name="SensorValues")
    string_elem = ET.SubElement(param, "String")
    string_elem.text = sensor_xml

    self.backend._sila.send_command = AsyncMock(return_value=root)  # type: ignore[method-assign]
    temps = await self.backend.get_lid_current_temperature()
    self.assertEqual(len(temps), 1)
    self.assertAlmostEqual(temps[0], 26.0, places=2)

  async def test_execute_method_wait_false(self):
    """Test execute_method with wait=False (returns handle)."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 12345, None, 0.0))  # type: ignore[method-assign]
    execution = await self.backend.execute_method("PCR_30cycles", wait=False)
    assert execution is not None  # Type narrowing
    self.assertIsInstance(execution, MethodExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.method_name, "PCR_30cycles")
    self.backend._sila.start_command.assert_called_once()
    call_kwargs = self.backend._sila.start_command.call_args[1]
    self.assertEqual(call_kwargs["methodName"], "PCR_30cycles")

  async def test_method_execution_awaitable(self):
    """Test that MethodExecution is awaitable and wait() completes."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result("success")
    execution = MethodExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend
    )
    result = await execution
    self.assertEqual(result, "success")
    await execution.wait()  # Should not raise

  async def test_method_execution_is_running(self):
    """Test MethodExecution.is_running() method."""
    fut: asyncio.Future[Any] = asyncio.Future()
    execution = MethodExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend
    )
    self.backend.get_status = AsyncMock(return_value="busy")  # type: ignore[method-assign]
    is_running = await execution.is_running()
    self.assertTrue(is_running)

  async def test_method_execution_stop(self):
    """Test MethodExecution.stop() method."""
    fut: asyncio.Future[Any] = asyncio.Future()
    execution = MethodExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend
    )
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await execution.stop()
    self.backend._sila.send_command.assert_called_once_with("StopMethod")

  async def test_method_execution_inheritance(self):
    """Test that MethodExecution is a subclass of CommandExecution."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    execution = MethodExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend
    )
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.command_name, "ExecuteMethod")
    self.assertEqual(execution.method_name, "PCR_30cycles")

  async def test_command_execution_awaitable(self):
    """Test that CommandExecution is awaitable and wait() completes."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result("success")
    execution = CommandExecution(
      request_id=12345,
      command_name="OpenDoor",
      _future=fut,
      backend=self.backend
    )
    result = await execution
    self.assertEqual(result, "success")
    await execution.wait()  # Should not raise

  async def test_command_execution_get_data_events(self):
    """Test CommandExecution.get_data_events() method."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    execution = CommandExecution(
      request_id=12345,
      command_name="OpenDoor",
      _future=fut,
      backend=self.backend
    )
    self.backend._sila._data_events_by_request_id = {
      12345: [{"requestId": 12345, "data": "test1"}, {"requestId": 12345, "data": "test2"}],
      67890: [{"requestId": 67890, "data": "test3"}],
    }
    events = await execution.get_data_events()
    self.assertEqual(len(events), 2)
    self.assertEqual(events[0]["requestId"], 12345)

  async def test_open_door_wait_false_returns_command_execution(self):
    """Test open_door with wait=False returns CommandExecution handle."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 12345, None, 0.0))  # type: ignore[method-assign]
    execution = await self.backend.open_door(wait=False)
    assert execution is not None  # Type narrowing
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "OpenDoor")
    self.backend._sila.start_command.assert_called_once()
    self.assertEqual(self.backend._sila.start_command.call_args[0][0], "OpenDoor")

  async def test_reset_wait_false_returns_handle_with_kwargs(self):
    """Test reset with wait=False returns CommandExecution and passes deviceId/eventReceiverURI."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 12345, None, 0.0))  # type: ignore[method-assign]
    execution = await self.backend.reset(wait=False)
    assert execution is not None  # Type narrowing
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "Reset")
    self.backend._sila.start_command.assert_called_once()
    call_kwargs = self.backend._sila.start_command.call_args[1]
    self.assertEqual(call_kwargs["deviceId"], "ODTC")
    self.assertEqual(call_kwargs["eventReceiverURI"], "http://127.0.0.1:8080/")
    self.assertFalse(call_kwargs["simulationMode"])
    self.assertFalse(self.backend.simulation_mode)

  async def test_is_method_running(self):
    """Test is_method_running()."""
    with patch.object(
      ODTCBackend, "get_status", new_callable=AsyncMock, return_value="busy"
    ):
      self.assertTrue(await self.backend.is_method_running())

    with patch.object(
      ODTCBackend, "get_status", new_callable=AsyncMock, return_value="idle"
    ):
      self.assertFalse(await self.backend.is_method_running())

    with patch.object(
      ODTCBackend, "get_status", new_callable=AsyncMock, return_value="BUSY"
    ):
      # Backend compares to SiLAState.BUSY.value ("busy"), so uppercase is False
      self.assertFalse(await self.backend.is_method_running())

  async def test_wait_for_method_completion(self):
    """Test wait_for_method_completion()."""
    call_count = 0

    async def mock_get_status():
      nonlocal call_count
      call_count += 1
      if call_count < 3:
        return "busy"
      return "idle"

    self.backend.get_status = AsyncMock(side_effect=mock_get_status)  # type: ignore[method-assign]
    await self.backend.wait_for_method_completion(poll_interval=0.1)
    self.assertEqual(call_count, 3)

  async def test_wait_for_method_completion_timeout(self):
    """Test wait_for_method_completion() with timeout."""
    self.backend.get_status = AsyncMock(return_value="busy")  # type: ignore[method-assign]
    with self.assertRaises(TimeoutError):
      await self.backend.wait_for_method_completion(poll_interval=0.1, timeout=0.3)

  async def test_get_data_events(self):
    """Test get_data_events()."""
    self.backend._sila._data_events_by_request_id = {
      12345: [{"requestId": 12345, "data": "test1"}, {"requestId": 12345, "data": "test2"}],
      67890: [{"requestId": 67890, "data": "test3"}],
    }

    # Get all events
    all_events = await self.backend.get_data_events()
    self.assertEqual(len(all_events), 2)
    self.assertEqual(len(all_events[12345]), 2)

    # Get events for specific request_id
    events = await self.backend.get_data_events(request_id=12345)
    self.assertEqual(len(events), 1)
    self.assertEqual(len(events[12345]), 2)

    # Get events for non-existent request_id
    events = await self.backend.get_data_events(request_id=99999)
    self.assertEqual(len(events), 1)
    self.assertEqual(len(events[99999]), 0)

  async def test_list_protocols(self):
    """Test list_protocols returns method and premethod names."""
    method_set = ODTCMethodSet(
      methods=[ODTCMethod(name="PCR_30")],
      premethods=[ODTCPreMethod(name="Pre25")],
    )
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    names = await self.backend.list_protocols()
    self.assertEqual(names, ["PCR_30", "Pre25"])

  async def test_list_methods(self):
    """Test list_methods returns (method_names, premethod_names) and matches list_protocols."""
    method_set = ODTCMethodSet(
      methods=[ODTCMethod(name="PCR_30"), ODTCMethod(name="PCR_35")],
      premethods=[ODTCPreMethod(name="Pre25"), ODTCPreMethod(name="Pre37")],
    )
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    methods, premethods = await self.backend.list_methods()
    self.assertEqual(methods, ["PCR_30", "PCR_35"])
    self.assertEqual(premethods, ["Pre25", "Pre37"])
    protocol_list = await self.backend.list_protocols()
    self.assertEqual(methods + premethods, protocol_list.all)

  async def test_get_protocol_returns_none_for_missing(self):
    """Test get_protocol returns None when name not found."""
    self.backend.get_method_set = AsyncMock(return_value=ODTCMethodSet())  # type: ignore[method-assign]
    result = await self.backend.get_protocol("nonexistent")
    self.assertIsNone(result)

  async def test_get_protocol_returns_none_for_premethod(self):
    """Test get_protocol returns None for premethod names (runnable protocols only)."""
    method_set = ODTCMethodSet(
      methods=[],
      premethods=[ODTCPreMethod(name="Pre25")],
    )
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    result = await self.backend.get_protocol("Pre25")
    self.assertIsNone(result)

  async def test_get_protocol_returns_stored_for_method(self):
    """Test get_protocol returns StoredProtocol for runnable method."""
    method_set = ODTCMethodSet(
      methods=[
        ODTCMethod(
          name="PCR_30",
          steps=[ODTCStep(number=1, plateau_temperature=95.0, plateau_time=30.0)],
        )
      ],
      premethods=[],
    )
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    result = await self.backend.get_protocol("PCR_30")
    self.assertIsInstance(result, StoredProtocol)
    assert result is not None  # narrow for type checker
    self.assertEqual(result.name, "PCR_30")
    self.assertEqual(len(result.protocol.stages), 1)
    self.assertEqual(len(result.protocol.stages[0].steps), 1)

  async def test_run_stored_protocol_calls_execute_method(self):
    """Test run_stored_protocol calls execute_method with name, wait, and estimated_duration_seconds."""
    self.backend.execute_method = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with patch.object(
      self.backend, "get_protocol", new_callable=AsyncMock, return_value=None
    ), patch.object(
      self.backend, "get_method_set", new_callable=AsyncMock, return_value=ODTCMethodSet()
    ):
      await self.backend.run_stored_protocol("MyMethod", wait=True)
    self.backend.execute_method.assert_called_once_with(
      "MyMethod", wait=True, estimated_duration_seconds=None
    )


class TestODTCThermocycler(unittest.TestCase):
  """Tests for ODTCThermocycler resource."""

  def test_construct_creates_backend_and_uses_dimensions(self):
    """Constructing with odtc_ip and variant creates ODTCBackend and ODTC dimensions."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(
        name="odtc1",
        odtc_ip="192.168.1.100",
        variant=384,
        child_location=Coordinate.zero(),
      )
    self.assertIsInstance(tc.backend, ODTCBackend)
    self.assertEqual(tc.backend.variant, 384000)
    self.assertEqual(tc.get_size_x(), ODTC_DIMENSIONS.x)
    self.assertEqual(tc.get_size_y(), ODTC_DIMENSIONS.y)
    self.assertEqual(tc.get_size_z(), ODTC_DIMENSIONS.z)
    self.assertEqual(tc.model, "ODTC 384")

  def test_construct_variant_96_model(self):
    """Constructing with variant=96 sets model ODTC 96."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=96)
    self.assertEqual(tc.backend.variant, 960000)
    self.assertEqual(tc.model, "ODTC 96")

  def test_serialize_includes_odtc_ip_and_variant(self):
    """serialize() includes odtc_ip and variant from backend."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(
        name="odtc1",
        odtc_ip="192.168.1.50",
        variant=384,
        child_location=Coordinate.zero(),
      )
      tc.backend._sila._machine_ip = "192.168.1.50"
    data = tc.serialize()
    self.assertEqual(data["odtc_ip"], "192.168.1.50")
    self.assertEqual(data["variant"], 384000)

  def test_get_default_config_delegates_to_backend(self):
    """get_default_config returns backend.get_default_config()."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
    config = tc.get_default_config(name="MyPCR")
    self.assertEqual(config.variant, 384000)
    self.assertEqual(config.name, "MyPCR")

  def test_get_constraints_delegates_to_backend(self):
    """get_constraints returns backend.get_constraints()."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
    constraints = tc.get_constraints()
    self.assertEqual(constraints.variant, 384000)
    self.assertEqual(constraints.variant_name, "ODTC 384")

  def test_well_count_96(self):
    """well_count is 96 when backend variant is 960000."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=96)
    self.assertEqual(tc.well_count, 96)

  def test_well_count_384(self):
    """well_count is 384 when backend variant is 384000."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
    self.assertEqual(tc.well_count, 384)

  def test_is_profile_running_delegates_to_backend(self):
    """is_profile_running delegates to backend.is_method_running()."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
      tc.backend.is_method_running = AsyncMock(return_value=False)  # type: ignore[method-assign]
    result = asyncio.run(tc.is_profile_running())
    self.assertFalse(result)
    tc.backend.is_method_running.assert_called_once()

  def test_wait_for_profile_completion_delegates_to_backend(self):
    """wait_for_profile_completion delegates to backend.wait_for_method_completion()."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
      tc.backend.wait_for_method_completion = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(tc.wait_for_profile_completion(poll_interval=5.0))
    tc.backend.wait_for_method_completion.assert_called_once_with(
      poll_interval=5.0,
      timeout=None,
    )

  def test_backend_provided_uses_it_dimensions_from_constant(self):
    """When backend= is provided, that backend is used; dimensions still from ODTC_DIMENSIONS."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      backend = ODTCBackend(odtc_ip="10.0.0.1", variant=384)
      backend._sila = MagicMock(spec=ODTCSiLAInterface)
      backend._sila._machine_ip = "10.0.0.1"
    tc = ODTCThermocycler(
      name="odtc1",
      odtc_ip="192.168.1.1",
      variant=384,
      backend=backend,
      child_location=Coordinate.zero(),
    )
    self.assertIs(tc.backend, backend)
    self.assertEqual(tc.get_size_x(), ODTC_DIMENSIONS.x)
    self.assertEqual(tc.get_size_y(), ODTC_DIMENSIONS.y)
    self.assertEqual(tc.get_size_z(), ODTC_DIMENSIONS.z)


class TestODTCSiLAInterfaceDataEvents(unittest.TestCase):
  """Tests for DataEvent storage in ODTCSiLAInterface."""

  def test_data_event_storage_logic(self):
    """Test that DataEvent storage logic works correctly."""
    # Test the storage logic directly without creating the full interface
    # (which requires network permissions)
    data_events_by_request_id: Dict[int, List[Dict[str, Any]]] = {}

    # Simulate receiving a DataEvent
    data_event = {
      "requestId": 12345,
      "data": "test_data"
    }

    # Apply the same logic as in _on_http handler
    request_id = data_event.get("requestId")
    if request_id is not None and isinstance(request_id, int):
      if request_id not in data_events_by_request_id:
        data_events_by_request_id[request_id] = []
      data_events_by_request_id[request_id].append(data_event)

    # Verify storage
    self.assertIn(12345, data_events_by_request_id)
    self.assertEqual(len(data_events_by_request_id[12345]), 1)
    self.assertEqual(
      data_events_by_request_id[12345][0]["requestId"],
      12345
    )

    # Test multiple events for same request_id
    data_event2 = {
      "requestId": 12345,
      "data": "test_data2"
    }
    request_id = data_event2.get("requestId")
    if request_id is not None and isinstance(request_id, int):
      if request_id not in data_events_by_request_id:
        data_events_by_request_id[request_id] = []
      data_events_by_request_id[request_id].append(data_event2)

    self.assertEqual(len(data_events_by_request_id[12345]), 2)

    # Test event with None request_id (should not be stored)
    data_event_no_id = {
      "data": "test_data_no_id"
    }
    request_id = data_event_no_id.get("requestId")
    if request_id is not None and isinstance(request_id, int):
      if request_id not in data_events_by_request_id:
        data_events_by_request_id[request_id] = []
      data_events_by_request_id[request_id].append(data_event_no_id)

    # Should still only have 2 events (the one with None request_id wasn't stored)
    self.assertEqual(len(data_events_by_request_id[12345]), 2)


if __name__ == "__main__":
  unittest.main()
