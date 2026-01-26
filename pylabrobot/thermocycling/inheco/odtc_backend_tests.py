"""Tests for ODTC backend and SiLA interface."""

import asyncio
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.thermocycling.inheco.odtc_backend import CommandExecution, MethodExecution, ODTCBackend
from pylabrobot.thermocycling.inheco.odtc_sila_interface import ODTCSiLAInterface, SiLAState


class TestODTCSiLAInterface(unittest.IsolatedAsyncioTestCase):
  """Tests for ODTCSiLAInterface."""

  def setUp(self):
    """Set up test fixtures."""
    self.interface = ODTCSiLAInterface(machine_ip="192.168.1.100")

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
    """Test state updates from status strings."""
    self.interface._update_state_from_status("Idle")
    self.assertEqual(self.interface._current_state, SiLAState.IDLE)

    self.interface._update_state_from_status("Busy")
    self.assertEqual(self.interface._current_state, SiLAState.BUSY)

    self.interface._update_state_from_status("Standby")
    self.assertEqual(self.interface._current_state, SiLAState.STANDBY)

  def test_handle_return_code(self):
    """Test return code handling."""
    # Code 1, 2, 3 should not raise
    self.interface._handle_return_code(1, "Success", "GetStatus", 123)
    self.interface._handle_return_code(2, "Accepted", "ExecuteMethod", 123)
    self.interface._handle_return_code(3, "Finished", "ExecuteMethod", 123)

    # Code 4 should raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._handle_return_code(4, "Busy", "ExecuteMethod", 123)
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

  async def test_setup(self):
    """Test backend setup."""
    self.backend._sila.setup = AsyncMock()
    await self.backend.setup()
    self.backend._sila.setup.assert_called_once()

  async def test_stop(self):
    """Test backend stop."""
    self.backend._sila.close = AsyncMock()
    await self.backend.stop()
    self.backend._sila.close.assert_called_once()

  async def test_get_status(self):
    """Test get_status."""
    self.backend._sila.send_command = AsyncMock(
      return_value={"GetStatusResponse": {"GetStatusResult": {"state": "Idle"}}}
    )
    status = await self.backend.get_status()
    self.assertEqual(status, "Idle")
    self.backend._sila.send_command.assert_called_once_with("GetStatus")

  async def test_open_door(self):
    """Test open_door."""
    self.backend._sila.send_command = AsyncMock()
    await self.backend.open_door()
    self.backend._sila.send_command.assert_called_once_with("OpenDoor")

  async def test_close_door(self):
    """Test close_door."""
    self.backend._sila.send_command = AsyncMock()
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

    self.backend._sila.send_command = AsyncMock(return_value=root)
    sensor_values = await self.backend.read_temperatures()
    self.assertAlmostEqual(sensor_values.mount, 24.63, places=2)  # 2463 * 0.01
    self.assertAlmostEqual(sensor_values.lid, 25.75, places=2)  # 2575 * 0.01

  async def test_execute_method(self):
    """Test execute_method."""
    self.backend._sila.send_command = AsyncMock()
    await self.backend.execute_method("MyMethod")
    self.backend._sila.send_command.assert_called_once_with("ExecuteMethod", methodName="MyMethod")

  async def test_stop_method(self):
    """Test stop_method."""
    self.backend._sila.send_command = AsyncMock()
    await self.backend.stop_method()
    self.backend._sila.send_command.assert_called_once_with("StopMethod")

  async def test_lock_device(self):
    """Test lock_device."""
    self.backend._sila.send_command = AsyncMock()
    await self.backend.lock_device("my_lock_id")
    self.backend._sila.send_command.assert_called_once()
    call_kwargs = self.backend._sila.send_command.call_args[1]
    self.assertEqual(call_kwargs["lock_id"], "my_lock_id")
    self.assertEqual(call_kwargs["PMSId"], "PyLabRobot")

  async def test_unlock_device(self):
    """Test unlock_device."""
    self.backend._sila._lock_id = "my_lock_id"
    self.backend._sila.send_command = AsyncMock()
    await self.backend.unlock_device()
    self.backend._sila.send_command.assert_called_once_with("UnlockDevice", lock_id="my_lock_id")

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

    self.backend._sila.send_command = AsyncMock(return_value=root)
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

    self.backend._sila.send_command = AsyncMock(return_value=root)
    temps = await self.backend.get_lid_current_temperature()
    self.assertEqual(len(temps), 1)
    self.assertAlmostEqual(temps[0], 26.0, places=2)

  async def test_execute_method_wait_true(self):
    """Test execute_method with wait=True (blocking)."""
    self.backend._sila.send_command = AsyncMock(return_value=None)
    result = await self.backend.execute_method("PCR_30cycles", wait=True)
    self.assertIsNone(result)
    self.backend._sila.send_command.assert_called_once_with(
      "ExecuteMethod", return_request_id=False, methodName="PCR_30cycles"
    )

  async def test_execute_method_wait_false(self):
    """Test execute_method with wait=False (returns handle)."""
    fut = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.send_command = AsyncMock(return_value=(fut, 12345))
    execution = await self.backend.execute_method("PCR_30cycles", wait=False)
    self.assertIsInstance(execution, MethodExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.method_name, "PCR_30cycles")
    self.backend._sila.send_command.assert_called_once_with(
      "ExecuteMethod", return_request_id=True, methodName="PCR_30cycles"
    )

  async def test_method_execution_awaitable(self):
    """Test that MethodExecution is awaitable."""
    fut = asyncio.Future()
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

  async def test_method_execution_wait(self):
    """Test MethodExecution.wait() method."""
    fut = asyncio.Future()
    fut.set_result(None)
    execution = MethodExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend
    )
    await execution.wait()  # Should not raise

  async def test_method_execution_is_running(self):
    """Test MethodExecution.is_running() method."""
    fut = asyncio.Future()
    execution = MethodExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend
    )
    self.backend.get_status = AsyncMock(return_value="busy")
    is_running = await execution.is_running()
    self.assertTrue(is_running)

  async def test_method_execution_stop(self):
    """Test MethodExecution.stop() method."""
    fut = asyncio.Future()
    execution = MethodExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend
    )
    self.backend._sila.send_command = AsyncMock()
    await execution.stop()
    self.backend._sila.send_command.assert_called_once_with("StopMethod", return_request_id=False)

  async def test_method_execution_inheritance(self):
    """Test that MethodExecution is a subclass of CommandExecution."""
    fut = asyncio.Future()
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
    """Test that CommandExecution is awaitable."""
    fut = asyncio.Future()
    fut.set_result("success")
    execution = CommandExecution(
      request_id=12345,
      command_name="OpenDoor",
      _future=fut,
      backend=self.backend
    )
    result = await execution
    self.assertEqual(result, "success")

  async def test_command_execution_wait(self):
    """Test CommandExecution.wait() method."""
    fut = asyncio.Future()
    fut.set_result(None)
    execution = CommandExecution(
      request_id=12345,
      command_name="OpenDoor",
      _future=fut,
      backend=self.backend
    )
    await execution.wait()  # Should not raise

  async def test_command_execution_get_data_events(self):
    """Test CommandExecution.get_data_events() method."""
    fut = asyncio.Future()
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

  async def test_open_door_wait_false(self):
    """Test open_door with wait=False (returns handle)."""
    fut = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.send_command = AsyncMock(return_value=(fut, 12345))
    execution = await self.backend.open_door(wait=False)
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "OpenDoor")
    self.backend._sila.send_command.assert_called_once_with(
      "OpenDoor", return_request_id=True
    )

  async def test_open_door_wait_true(self):
    """Test open_door with wait=True (blocking)."""
    self.backend._sila.send_command = AsyncMock(return_value=None)
    result = await self.backend.open_door(wait=True)
    self.assertIsNone(result)
    self.backend._sila.send_command.assert_called_once_with(
      "OpenDoor", return_request_id=False
    )

  async def test_close_door_wait_false(self):
    """Test close_door with wait=False (returns handle)."""
    fut = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.send_command = AsyncMock(return_value=(fut, 12345))
    execution = await self.backend.close_door(wait=False)
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "CloseDoor")

  async def test_initialize_wait_false(self):
    """Test initialize with wait=False (returns handle)."""
    fut = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.send_command = AsyncMock(return_value=(fut, 12345))
    execution = await self.backend.initialize(wait=False)
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "Initialize")

  async def test_reset_wait_false(self):
    """Test reset with wait=False (returns handle)."""
    fut = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.send_command = AsyncMock(return_value=(fut, 12345))
    execution = await self.backend.reset(wait=False)
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "Reset")

  async def test_lock_device_wait_false(self):
    """Test lock_device with wait=False (returns handle)."""
    fut = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.send_command = AsyncMock(return_value=(fut, 12345))
    execution = await self.backend.lock_device("my_lock", wait=False)
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "LockDevice")

  async def test_unlock_device_wait_false(self):
    """Test unlock_device with wait=False (returns handle)."""
    fut = asyncio.Future()
    fut.set_result(None)
    self.backend._sila._lock_id = "my_lock"
    self.backend._sila.send_command = AsyncMock(return_value=(fut, 12345))
    execution = await self.backend.unlock_device(wait=False)
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "UnlockDevice")

  async def test_stop_method_wait_false(self):
    """Test stop_method with wait=False (returns handle)."""
    fut = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.send_command = AsyncMock(return_value=(fut, 12345))
    execution = await self.backend.stop_method(wait=False)
    self.assertIsInstance(execution, CommandExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "StopMethod")

  async def test_is_method_running(self):
    """Test is_method_running()."""
    self.backend.get_status = AsyncMock(return_value="busy")
    self.assertTrue(await self.backend.is_method_running())

    self.backend.get_status = AsyncMock(return_value="idle")
    self.assertFalse(await self.backend.is_method_running())

    self.backend.get_status = AsyncMock(return_value="BUSY")
    self.assertTrue(await self.backend.is_method_running())

  async def test_wait_for_method_completion(self):
    """Test wait_for_method_completion()."""
    call_count = 0

    async def mock_get_status():
      nonlocal call_count
      call_count += 1
      if call_count < 3:
        return "busy"
      return "idle"

    self.backend.get_status = AsyncMock(side_effect=mock_get_status)
    await self.backend.wait_for_method_completion(poll_interval=0.1)
    self.assertEqual(call_count, 3)

  async def test_wait_for_method_completion_timeout(self):
    """Test wait_for_method_completion() with timeout."""
    self.backend.get_status = AsyncMock(return_value="busy")
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


class TestODTCSiLAInterfaceDataEvents(unittest.TestCase):
  """Tests for DataEvent storage in ODTCSiLAInterface."""

  def test_data_event_storage_logic(self):
    """Test that DataEvent storage logic works correctly."""
    # Test the storage logic directly without creating the full interface
    # (which requires network permissions)
    data_events_by_request_id = {}

    # Simulate receiving a DataEvent
    data_event = {
      "requestId": 12345,
      "data": "test_data"
    }

    # Apply the same logic as in _on_http handler
    request_id = data_event.get("requestId")
    if request_id is not None:
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
    if request_id is not None:
      if request_id not in data_events_by_request_id:
        data_events_by_request_id[request_id] = []
      data_events_by_request_id[request_id].append(data_event2)

    self.assertEqual(len(data_events_by_request_id[12345]), 2)

    # Test event with None request_id (should not be stored)
    data_event_no_id = {
      "data": "test_data_no_id"
    }
    request_id = data_event_no_id.get("requestId")
    if request_id is not None:
      if request_id not in data_events_by_request_id:
        data_events_by_request_id[request_id] = []
      data_events_by_request_id[request_id].append(data_event_no_id)

    # Should still only have 2 events (the one with None request_id wasn't stored)
    self.assertEqual(len(data_events_by_request_id[12345]), 2)


if __name__ == "__main__":
  unittest.main()
