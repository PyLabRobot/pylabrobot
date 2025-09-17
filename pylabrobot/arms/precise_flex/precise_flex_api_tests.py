import unittest
import asyncio
from pylabrobot.arms.precise_flex.precise_flex_api import PreciseFlexBackendApi
from contextlib import asynccontextmanager



class PreciseFlexApiHardwareTests(unittest.IsolatedAsyncioTestCase):
  """Integration tests for PreciseFlex robot - RUNS ON ACTUAL HARDWARE"""

  async def asyncSetUp(self):
    """Connect to actual PreciseFlex robot"""
    # Update with your robot's IP and port
    self.robot = PreciseFlexBackendApi("192.168.0.1", 10100)
    # Configuration constants - modify these for your testing needs
    self.TEST_PROFILE_ID = 20
    self.TEST_LOCATION_ID = 20 # Default upper limit of station indices offered by GPL program running the TCS server
    self.TEST_PARAMETER_ID = 17018 # last parameter Id of "Custom Calibration Data and Test Results" parameters
    self.TEST_SIGNAL_ID = 20064 # unused software I/O
    await self.robot.setup()
    await self.robot.attach()
    await self.robot.set_power(True, timeout=20)

  async def asyncTearDown(self):
    """Cleanup robot connection"""
    if hasattr(self, 'robot'):
      await self.robot.stop()

  @asynccontextmanager
  async def _preserve_setting(self, getter_method: str, setter_method: str):
    """Context manager to preserve and restore robot settings"""
    # Get original value
    original_value = await getattr(self.robot, getter_method)()

    try:
      yield original_value
    finally:
      # Restore original value
      try:
        if isinstance(original_value, tuple):
          await getattr(self.robot, setter_method)(*original_value)
        else:
          await getattr(self.robot, setter_method)(original_value)
        print(f"Setting restored to: {original_value}")
      except Exception as e:
        print(f"Error restoring setting: {e}")

#region GENERAL COMMANDS
  async def test_robot_connection_and_version(self) -> None:
    """Test basic connection and version info"""
    version = await self.robot.get_version()
    self.assertIsInstance(version, str)
    print(f"Robot version: {version}")

  async def test_get_base(self) -> None:
    base = await self.robot.get_base()
    self.assertIsInstance(base, tuple)
    print(f"Robot base: {base}")

  async def test_set_base(self) -> None:
    """Test set_base()"""
    async with self._preserve_setting('get_base', 'set_base'):
      # Test setting to a different base if possible
      test_base = (0, 0, 0, 0)
      print(f"Setting test base to: {test_base}")
      await self.robot.set_base(*test_base)
      new_base = await self.robot.get_base()
      print(f"Base set to: {new_base}")
      self.assertEqual(new_base, test_base)

  async def test_home(self) -> None:
    """Test home() command"""
    await self.robot.home()
    print("Robot homed successfully")

  async def test_home_all(self) -> None:
    """Test home_all() command"""
    # Note: This requires robots not to be attached, so we'll detach first
    await self.robot.attach(0)
    await self.robot.home_all()
    await self.robot.attach()  # Re-attach for other tests
    print("All robots homed successfully")

  async def test_get_power_state(self) -> None:
    """Test get_power_state()"""
    power_state = await self.robot.get_power_state()
    self.assertIsInstance(power_state, int)
    self.assertIn(power_state, [0, 1])
    print(f"Power state: {power_state}")

  async def test_set_power(self) -> None:
    """Test set_power()"""
    async with self._preserve_setting('get_power_state', 'set_power'):
      # Test disabling power
      await self.robot.set_power(False)
      power_state = await self.robot.get_power_state()
      self.assertEqual(power_state, 0)
      await asyncio.sleep(2) # Wait a bit before re-enabling

      # Test enabling power with timeout
      await self.robot.set_power(True, timeout=20)
      await asyncio.sleep(2) # Wait a bit for power to stabilize
      power_state = await self.robot.get_power_state()
      self.assertEqual(power_state, 1)
      print("Power set operations completed successfully")

  async def test_get_mode(self) -> None:
    """Test get_mode()"""
    mode = await self.robot.get_mode()
    self.assertIsInstance(mode, int)
    self.assertIn(mode, [0, 1])
    print(f"Current mode: {mode}")

  async def test_set_mode(self) -> None:
    """Test set_mode()"""
    async with self._preserve_setting('get_mode', 'set_mode'):
      # Test setting PC mode
      await self.robot.set_mode(0)
      mode = await self.robot.get_mode()
      self.assertEqual(mode, 0)

      # Skip testing setting to verbose mode since it will break the parsing
      # await self.robot.set_mode(1)
      # mode = await self.robot.get_mode()
      # self.assertEqual(mode, 1)
      print("Mode set operations completed successfully")

  async def test_get_monitor_speed(self) -> None:
    """Test get_monitor_speed()"""
    speed = await self.robot.get_monitor_speed()
    self.assertIsInstance(speed, int)
    self.assertGreaterEqual(speed, 1)
    self.assertLessEqual(speed, 100)
    print(f"Monitor speed: {speed}%")

  async def test_set_monitor_speed(self) -> None:
    """Test set_monitor_speed()"""
    async with self._preserve_setting('get_monitor_speed', 'set_monitor_speed'):
      # Test setting different speeds
      test_speed = 50
      await self.robot.set_monitor_speed(test_speed)
      speed = await self.robot.get_monitor_speed()
      self.assertEqual(speed, test_speed)
      print(f"Monitor speed set to: {speed}%")

  async def test_nop(self) -> None:
    """Test nop() command"""
    await self.robot.nop()
    print("NOP command executed successfully")

  async def test_get_payload(self) -> None:
    """Test get_payload()"""
    payload = await self.robot.get_payload()
    self.assertIsInstance(payload, int)
    self.assertGreaterEqual(payload, 0)
    self.assertLessEqual(payload, 100)
    print(f"Payload: {payload}%")

  async def test_set_payload(self) -> None:
    """Test set_payload()"""
    async with self._preserve_setting('get_payload', 'set_payload'):
      # Test setting different payload values
      test_payload = 25
      await self.robot.set_payload(test_payload)
      payload = await self.robot.get_payload()
      self.assertEqual(payload, test_payload)
      print(f"Payload set to: {payload}%")

  async def test_parameter_operations(self) -> None:
    """Test get_parameter() and set_parameter()"""
    # Get original value
    original_value = await self.robot.get_parameter(self.TEST_PARAMETER_ID)
    print(f"Original parameter value: {original_value}")

    # Test setting and getting back
    test_value = "test_value"
    await self.robot.set_parameter(self.TEST_PARAMETER_ID, test_value)

    # Get the value back
    retrieved_value = await self.robot.get_parameter(self.TEST_PARAMETER_ID)
    print(f"Retrieved parameter value: {retrieved_value}")

    # Restore original value
    await self.robot.set_parameter(self.TEST_PARAMETER_ID, original_value)

  async def test_get_selected_robot(self) -> None:
    """Test get_selected_robot()"""
    selected_robot = await self.robot.get_selected_robot()
    self.assertIsInstance(selected_robot, int)
    self.assertGreaterEqual(selected_robot, 0)
    print(f"Selected robot: {selected_robot}")

  async def test_select_robot(self) -> None:
    """Test select_robot()"""
    async with self._preserve_setting('get_selected_robot', 'select_robot'):
      # Test selecting robot 1
      await self.robot.select_robot(1)
      selected = await self.robot.get_selected_robot()
      self.assertEqual(selected, 1)
      print(f"Selected robot set to: {selected}")

  async def test_signal_operations(self) -> None:
    """Test get_signal() and set_signal()"""

    # Get original signal value
    original_value = await self.robot.get_signal(self.TEST_SIGNAL_ID)
    print(f"Original signal {self.TEST_SIGNAL_ID} value: {original_value}")

    try:
      # Test setting signal
      test_value = 1 if original_value == 0 else 0
      await self.robot.set_signal(self.TEST_SIGNAL_ID, test_value)

      # Verify the change
      new_value = await self.robot.get_signal(self.TEST_SIGNAL_ID)
      if test_value == 0:
        self.assertEqual(new_value, 0)
      else:
        self.assertNotEqual(new_value, 0)
      print(f"Signal {self.TEST_SIGNAL_ID} set to: {new_value}")

    finally:
      # Restore original value
      await self.robot.set_signal(self.TEST_SIGNAL_ID, original_value)

  async def test_get_system_state(self) -> None:
    """Test get_system_state()"""
    system_state = await self.robot.get_system_state()
    self.assertIsInstance(system_state, int)
    print(f"System state: {system_state}")

  async def test_get_tool(self) -> None:
    """Test get_tool()"""
    tool = await self.robot.get_tool()
    self.assertIsInstance(tool, tuple)
    self.assertEqual(len(tool), 6)
    x, y, z, yaw, pitch, roll = tool
    self.assertTrue(all(isinstance(val, (int, float)) for val in tool))
    print(f"Tool transformation: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}")

  async def test_set_tool(self) -> None:
    """Test set_tool()"""
    async with self._preserve_setting('get_tool', 'set_tool'):
      # Test setting tool transformation
      test_tool = (10.0, 20.0, 30.0, 0.0, 0.0, 0.0)
      await self.robot.set_tool(*test_tool)

      current_tool = await self.robot.get_tool()
      # Allow for small floating point differences
      for i, (expected, actual) in enumerate(zip(test_tool, current_tool)):
        self.assertLess(abs(expected - actual), 0.001, f"Tool value {i} mismatch: expected {expected}, got {actual}")

      print(f"Tool transformation set to: {current_tool}")

  async def test_get_version(self) -> None:
    """Test get_version()"""
    version = await self.robot.get_version()
    self.assertIsInstance(version, str)
    self.assertGreater(len(version), 0)
    print(f"Robot version: {version}")

  async def test_reset(self) -> None:
    """Test reset() command"""
    # Test resetting robot 1 (be careful with this in real hardware)
    # This test might need to be commented out for actual hardware testing
    # await self.robot.reset(1)
    print("Reset test skipped for safety (uncomment if needed)")

#region LOCATION COMMANDS
  async def test_get_location(self) -> None:
    """Test get_location()"""
    location_data = await self.robot.get_location(self.TEST_LOCATION_ID)
    self.assertIsInstance(location_data, tuple)
    self.assertEqual(len(location_data), 9)
    type_code, station_index, val1, val2, val3, val4, val5, val6, val7 = location_data
    self.assertIsInstance(type_code, int)
    self.assertIn(type_code, [0, 1])  # 0 = Cartesian, 1 = angles
    self.assertEqual(station_index, self.TEST_LOCATION_ID)
    print(f"Location {self.TEST_LOCATION_ID}: type={type_code}, values=({val1}, {val2}, {val3}, {val4}, {val5}, {val6}, {val7})")

  async def test_get_location_angles(self) -> None:
    """Test get_location_angles()"""
    # This test assumes location is already angles type or will fail appropriately
    try:
      location_data = await self.robot.get_location_angles(self.TEST_LOCATION_ID)
      self.assertIsInstance(location_data, tuple)
      self.assertEqual(len(location_data), 9)
      type_code, station_index, angle1, angle2, angle3, angle4, angle5, angle6, angle7 = location_data
      self.assertEqual(type_code, 1)  # Should be angles type
      self.assertEqual(station_index, self.TEST_LOCATION_ID)
      print(f"Location angles {self.TEST_LOCATION_ID}: ({angle1}, {angle2}, {angle3}, {angle4}, {angle5}, {angle6}, {angle7})")
    except Exception as e:
      print(f"Location {self.TEST_LOCATION_ID} is not angles type or error occurred: {e}")

  async def test_set_location_angles(self) -> None:
    """Test set_location_angles()"""
    # Get original location data
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Test setting angles
      test_angles = (15.0, 25.0, 35.0, 45.0, 55.0, 0.0) # last is set to 0.0 as angle7 is typically unused on PF400, some other robots may use fewer angles
      await self.robot.set_location_angles(self.TEST_LOCATION_ID, *test_angles)

      # Verify the angles were set
      location_data = await self.robot.get_location_angles(self.TEST_LOCATION_ID)
      _, _, angle1, angle2, angle3, angle4, angle5, angle6, angle7 = location_data

      # Check first 6 angles (angle7 is typically 0)
      retrieved_angles = (angle1, angle2, angle3, angle4, angle5, angle6)
      for i, (expected, actual) in enumerate(zip(test_angles, retrieved_angles)):
        self.assertLess(abs(expected - actual), 0.001, f"Angle {i+1} mismatch: expected {expected}, got {actual}")

      print(f"Location angles set successfully: {retrieved_angles}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 1:  # Was angles type
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was Cartesian type
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])

  async def test_get_location_xyz(self) -> None:
    """Test get_location_xyz()"""
    # This test assumes location is already Cartesian type or will fail appropriately
    try:
      location_data = await self.robot.get_location_xyz(self.TEST_LOCATION_ID)
      self.assertIsInstance(location_data, tuple)
      self.assertEqual(len(location_data), 8)
      type_code, station_index, x, y, z, yaw, pitch, roll = location_data
      self.assertEqual(type_code, 0)  # Should be Cartesian type
      self.assertEqual(station_index, self.TEST_LOCATION_ID)
      print(f"Location XYZ {self.TEST_LOCATION_ID}: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}")
    except Exception as e:
      print(f"Location {self.TEST_LOCATION_ID} is not Cartesian type or error occurred: {e}")

  async def test_set_location_xyz(self) -> None:
    """Test set_location_xyz()"""
    # Get original location data
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Test setting Cartesian coordinates
      test_coords = (150.0, 250.0, 350.0, 10.0, 20.0, 30.0)
      await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *test_coords)

      # Verify the coordinates were set
      location_data = await self.robot.get_location_xyz(self.TEST_LOCATION_ID)
      _, _, x, y, z, yaw, pitch, roll = location_data

      retrieved_coords = (x, y, z, yaw, pitch, roll)
      for i, (expected, actual) in enumerate(zip(test_coords, retrieved_coords)):
        self.assertLess(abs(expected - actual), 0.001, f"Coordinate {i} mismatch: expected {expected}, got {actual}")

      print(f"Location XYZ set successfully: {retrieved_coords}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

  async def test_get_location_z_clearance(self) -> None:
    """Test get_location_z_clearance()"""
    clearance_data = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)
    self.assertIsInstance(clearance_data, tuple)
    self.assertEqual(len(clearance_data), 3)
    station_index, z_clearance, z_world = clearance_data
    self.assertEqual(station_index, self.TEST_LOCATION_ID)
    self.assertIsInstance(z_clearance, float)
    self.assertIsInstance(z_world, float)
    print(f"Location {self.TEST_LOCATION_ID} Z clearance: {z_clearance}, Z world: {z_world}")

  async def test_set_location_z_clearance(self) -> None:
    """Test set_location_z_clearance()"""
    original_clearance = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)
    _, orig_z_clearance, orig_z_world = original_clearance

    try:
      # Test setting only z_clearance
      test_z_clearance = 50.0
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, test_z_clearance)

      clearance_data = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)
      _, z_clearance, z_world = clearance_data
      self.assertLess(abs(z_clearance - test_z_clearance), 0.001)
      print(f"Z clearance set to: {z_clearance}")

      # Test setting both z_clearance and z_world
      test_z_world = True
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, test_z_clearance, test_z_world)

      clearance_data = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)
      _, z_clearance, z_world = clearance_data
      self.assertLess(abs(z_clearance - test_z_clearance), 0.001)
      self.assertEqual(z_world, test_z_world)
      print(f"Z clearance and world set to: {z_clearance}, {z_world}")

    finally:
      # Restore original values
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, orig_z_clearance, orig_z_world)

  async def test_get_location_config(self) -> None:
    """Test get_location_config()"""
    config_data = await self.robot.get_location_config(self.TEST_LOCATION_ID)
    self.assertIsInstance(config_data, tuple)
    self.assertEqual(len(config_data), 2)
    station_index, config_value = config_data
    self.assertEqual(station_index, self.TEST_LOCATION_ID)
    self.assertIsInstance(config_value, int)
    self.assertGreaterEqual(config_value, 0)

    # Decode config bits for display
    config_bits = []
    if config_value == 0:
      config_bits.append("None")
    else:
      if config_value & 0x01:
        config_bits.append("Righty")
      if config_value & 0x02:
        config_bits.append("Lefty")
      if config_value & 0x04:
        config_bits.append("Above")
      if config_value & 0x08:
        config_bits.append("Below")
      if config_value & 0x10:
        config_bits.append("Flip")
      if config_value & 0x20:
        config_bits.append("NoFlip")
      if config_value & 0x1000:
        config_bits.append("Single")

    config_str = " | ".join(config_bits)
    print(f"Location {self.TEST_LOCATION_ID} config: 0x{config_value:X} ({config_str})")

  async def test_set_location_config(self) -> None:
    """Test set_location_config()"""
    original_config = await self.robot.get_location_config(self.TEST_LOCATION_ID)
    _, orig_config_value = original_config

    try:
      # Test setting basic config (Righty)
      test_config = 0x01  # GPL_Righty
      await self.robot.set_location_config(self.TEST_LOCATION_ID, test_config)

      config_data = await self.robot.get_location_config(self.TEST_LOCATION_ID)
      _, config_value = config_data
      self.assertEqual(config_value, test_config)
      print(f"Location config set to: 0x{config_value:X} (Righty)")

      # Test setting combined config (Lefty + Above + NoFlip)
      test_config = 0x02 | 0x04 | 0x20  # GPL_Lefty | GPL_Above | GPL_NoFlip
      await self.robot.set_location_config(self.TEST_LOCATION_ID, test_config)

      config_data = await self.robot.get_location_config(self.TEST_LOCATION_ID)
      _, config_value = config_data
      self.assertEqual(config_value, test_config)
      print(f"Location config set to: 0x{config_value:X} (Lefty | Above | NoFlip)")

      # Test setting None config
      test_config = 0x00
      await self.robot.set_location_config(self.TEST_LOCATION_ID, test_config)

      config_data = await self.robot.get_location_config(self.TEST_LOCATION_ID)
      _, config_value = config_data
      self.assertEqual(config_value, test_config)
      print(f"Location config set to: 0x{config_value:X} (None)")

      # Test invalid config bits
      with self.assertRaises(ValueError):
        await self.robot.set_location_config(self.TEST_LOCATION_ID, 0x80)  # Invalid bit

      # Test conflicting configurations
      with self.assertRaises(ValueError):
        await self.robot.set_location_config(self.TEST_LOCATION_ID, 0x01 | 0x02)  # Righty + Lefty

      with self.assertRaises(ValueError):
        await self.robot.set_location_config(self.TEST_LOCATION_ID, 0x04 | 0x08)  # Above + Below

      with self.assertRaises(ValueError):
        await self.robot.set_location_config(self.TEST_LOCATION_ID, 0x10 | 0x20)  # Flip + NoFlip

    finally:
      # Restore original config
      await self.robot.set_location_config(self.TEST_LOCATION_ID, orig_config_value)

  async def test_dest_c(self) -> None:
    """Test dest_c()"""
    # Test with default argument (current location)
    dest_data = await self.robot.dest_c()
    self.assertIsInstance(dest_data, tuple)
    self.assertEqual(len(dest_data), 7)
    x, y, z, yaw, pitch, roll, config = dest_data
    self.assertTrue(all(isinstance(val, (int, float)) for val in dest_data))
    print(f"Current Cartesian destination: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}, Config={config}")

    # Test with arg1=1 (target location)
    dest_data_target = await self.robot.dest_c(1)
    self.assertIsInstance(dest_data_target, tuple)
    self.assertEqual(len(dest_data_target), 7)
    print(f"Target Cartesian destination: {dest_data_target}")

  async def test_dest_j(self) -> None:
    """Test dest_j()"""
    # Test with default argument (current joint positions)
    dest_data = await self.robot.dest_j()
    self.assertIsInstance(dest_data, tuple)
    self.assertEqual(len(dest_data), 7)
    self.assertTrue(all(isinstance(val, (int, float)) for val in dest_data))
    print(f"Current joint destination: {dest_data}")

    # Test with arg1=1 (target joint positions)
    dest_data_target = await self.robot.dest_j(1)
    self.assertIsInstance(dest_data_target, tuple)
    self.assertEqual(len(dest_data_target), 7)
    print(f"Target joint destination: {dest_data_target}")

  async def test_here_j(self) -> None:
    """Test here_j()"""
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Record current position as angles
      await self.robot.here_j(self.TEST_LOCATION_ID)

      # Verify the location was recorded as angles type
      location_data = await self.robot.get_location(self.TEST_LOCATION_ID)
      type_code = location_data[0]
      self.assertEqual(type_code, 1)  # Should be angles type
      print(f"Current position recorded as angles at location {self.TEST_LOCATION_ID}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

  async def test_here_c(self) -> None:
    """Test here_c()"""
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Record current position as Cartesian
      await self.robot.here_c(self.TEST_LOCATION_ID)

      # Verify the location was recorded as Cartesian type
      location_data = await self.robot.get_location(self.TEST_LOCATION_ID)
      type_code = location_data[0]
      self.assertEqual(type_code, 0)  # Should be Cartesian type
      print(f"Current position recorded as Cartesian at location {self.TEST_LOCATION_ID}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

  async def test_where(self) -> None:
    """Test where()"""
    position_data = await self.robot.where()
    self.assertIsInstance(position_data, tuple)
    self.assertEqual(len(position_data), 7)
    x, y, z, yaw, pitch, roll, axes = position_data
    self.assertTrue(all(isinstance(val, (int, float)) for val in [x, y, z, yaw, pitch, roll]))
    self.assertIsInstance(axes, tuple)
    self.assertEqual(len(axes), 7)
    self.assertTrue(all(isinstance(val, (int, float)) for val in axes))
    print(f"Current position - Cartesian: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}")
    print(f"Current position - Joints: {axes}")

  async def test_where_c(self) -> None:
    """Test where_c()"""
    position_data = await self.robot.where_c()
    self.assertIsInstance(position_data, tuple)
    self.assertEqual(len(position_data), 7)
    x, y, z, yaw, pitch, roll, config = position_data
    self.assertTrue(all(isinstance(val, (int, float)) for val in position_data))
    self.assertIn(config, [1, 2])  # 1 = Righty, 2 = Lefty
    print(f"Current Cartesian position: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}, Config={config}")

  async def test_where_j(self) -> None:
    """Test where_j()"""
    joint_data = await self.robot.where_j()
    self.assertIsInstance(joint_data, tuple)
    self.assertEqual(len(joint_data), 7)
    self.assertTrue(all(isinstance(val, (int, float)) for val in joint_data))
    print(f"Current joint positions: {joint_data}")

#region PROFILE COMMANDS
  async def test_get_profile_speed(self) -> None:
    """Test get_profile_speed()"""
    speed = await self.robot.get_profile_speed(self.TEST_PROFILE_ID)
    self.assertIsInstance(speed, float)
    self.assertGreaterEqual(speed, 0)
    print(f"Profile {self.TEST_PROFILE_ID} speed: {speed}%")

  async def test_set_profile_speed(self) -> None:
    """Test set_profile_speed()"""
    original_speed = await self.robot.get_profile_speed(self.TEST_PROFILE_ID)

    try:
      # Test setting different speed
      test_speed = 50.0
      await self.robot.set_profile_speed(self.TEST_PROFILE_ID, test_speed)

      speed = await self.robot.get_profile_speed(self.TEST_PROFILE_ID)
      self.assertLess(abs(speed - test_speed), 0.001)
      print(f"Profile speed set to: {speed}%")

    finally:
      # Restore original speed
      await self.robot.set_profile_speed(self.TEST_PROFILE_ID, original_speed)

  async def test_get_profile_speed2(self) -> None:
    """Test get_profile_speed2()"""
    speed2 = await self.robot.get_profile_speed2(self.TEST_PROFILE_ID)
    self.assertIsInstance(speed2, float)
    self.assertGreaterEqual(speed2, 0)
    print(f"Profile {self.TEST_PROFILE_ID} speed2: {speed2}%")

  async def test_set_profile_speed2(self) -> None:
    """Test set_profile_speed2()"""
    original_speed2 = await self.robot.get_profile_speed2(self.TEST_PROFILE_ID)

    try:
      # Test setting different speed2
      test_speed2 = 25.0
      await self.robot.set_profile_speed2(self.TEST_PROFILE_ID, test_speed2)

      speed2 = await self.robot.get_profile_speed2(self.TEST_PROFILE_ID)
      self.assertLess(abs(speed2 - test_speed2), 0.001)
      print(f"Profile speed2 set to: {speed2}%")

    finally:
      # Restore original speed2
      await self.robot.set_profile_speed2(self.TEST_PROFILE_ID, original_speed2)

  async def test_get_profile_accel(self) -> None:
    """Test get_profile_accel()"""
    accel = await self.robot.get_profile_accel(self.TEST_PROFILE_ID)
    self.assertIsInstance(accel, float)
    self.assertGreaterEqual(accel, 0)
    print(f"Profile {self.TEST_PROFILE_ID} acceleration: {accel}%")

  async def test_set_profile_accel(self) -> None:
    """Test set_profile_accel()"""
    original_accel = await self.robot.get_profile_accel(self.TEST_PROFILE_ID)

    try:
      # Test setting different acceleration
      test_accel = 75.0
      await self.robot.set_profile_accel(self.TEST_PROFILE_ID, test_accel)

      accel = await self.robot.get_profile_accel(self.TEST_PROFILE_ID)
      self.assertLess(abs(accel - test_accel), 0.001)
      print(f"Profile acceleration set to: {accel}%")

    finally:
      # Restore original acceleration
      await self.robot.set_profile_accel(self.TEST_PROFILE_ID, original_accel)

  async def test_get_profile_accel_ramp(self) -> None:
    """Test get_profile_accel_ramp()"""
    accel_ramp = await self.robot.get_profile_accel_ramp(self.TEST_PROFILE_ID)
    self.assertIsInstance(accel_ramp, float)
    self.assertGreaterEqual(accel_ramp, 0)
    print(f"Profile {self.TEST_PROFILE_ID} acceleration ramp: {accel_ramp} seconds")

  async def test_set_profile_accel_ramp(self) -> None:
    """Test set_profile_accel_ramp()"""
    original_accel_ramp = await self.robot.get_profile_accel_ramp(self.TEST_PROFILE_ID)

    try:
      # Test setting different acceleration ramp
      test_accel_ramp = 0.5
      await self.robot.set_profile_accel_ramp(self.TEST_PROFILE_ID, test_accel_ramp)

      accel_ramp = await self.robot.get_profile_accel_ramp(self.TEST_PROFILE_ID)
      self.assertLess(abs(accel_ramp - test_accel_ramp), 0.001)
      print(f"Profile acceleration ramp set to: {accel_ramp} seconds")

    finally:
      # Restore original acceleration ramp
      await self.robot.set_profile_accel_ramp(self.TEST_PROFILE_ID, original_accel_ramp)

  async def test_get_profile_decel(self) -> None:
    """Test get_profile_decel()"""
    decel = await self.robot.get_profile_decel(self.TEST_PROFILE_ID)
    self.assertIsInstance(decel, float)
    self.assertGreaterEqual(decel, 0)
    print(f"Profile {self.TEST_PROFILE_ID} deceleration: {decel}%")

  async def test_set_profile_decel(self) -> None:
    """Test set_profile_decel()"""
    original_decel = await self.robot.get_profile_decel(self.TEST_PROFILE_ID)

    try:
      # Test setting different deceleration
      test_decel = 80.0
      await self.robot.set_profile_decel(self.TEST_PROFILE_ID, test_decel)

      decel = await self.robot.get_profile_decel(self.TEST_PROFILE_ID)
      self.assertLess(abs(decel - test_decel), 0.001)
      print(f"Profile deceleration set to: {decel}%")

    finally:
      # Restore original deceleration
      await self.robot.set_profile_decel(self.TEST_PROFILE_ID, original_decel)

  async def test_get_profile_decel_ramp(self) -> None:
    """Test get_profile_decel_ramp()"""
    decel_ramp = await self.robot.get_profile_decel_ramp(self.TEST_PROFILE_ID)
    self.assertIsInstance(decel_ramp, float)
    self.assertGreaterEqual(decel_ramp, 0)
    print(f"Profile {self.TEST_PROFILE_ID} deceleration ramp: {decel_ramp} seconds")

  async def test_set_profile_decel_ramp(self) -> None:
    """Test set_profile_decel_ramp()"""
    original_decel_ramp = await self.robot.get_profile_decel_ramp(self.TEST_PROFILE_ID)

    try:
      # Test setting different deceleration ramp
      test_decel_ramp = 0.3
      await self.robot.set_profile_decel_ramp(self.TEST_PROFILE_ID, test_decel_ramp)

      decel_ramp = await self.robot.get_profile_decel_ramp(self.TEST_PROFILE_ID)
      self.assertLess(abs(decel_ramp - test_decel_ramp), 0.001)
      print(f"Profile deceleration ramp set to: {decel_ramp} seconds")

    finally:
      # Restore original deceleration ramp
      await self.robot.set_profile_decel_ramp(self.TEST_PROFILE_ID, original_decel_ramp)

  async def test_get_profile_in_range(self) -> None:
    """Test get_profile_in_range()"""
    in_range = await self.robot.get_profile_in_range(self.TEST_PROFILE_ID)
    self.assertIsInstance(in_range, float)
    self.assertGreaterEqual(in_range, -1)
    self.assertLessEqual(in_range, 100)
    print(f"Profile {self.TEST_PROFILE_ID} InRange: {in_range}")

  async def test_set_profile_in_range(self) -> None:
    """Test set_profile_in_range()"""
    original_in_range = await self.robot.get_profile_in_range(self.TEST_PROFILE_ID)

    try:
      # Test setting different InRange values
      test_in_range = 50.0
      await self.robot.set_profile_in_range(self.TEST_PROFILE_ID, test_in_range)

      in_range = await self.robot.get_profile_in_range(self.TEST_PROFILE_ID)
      self.assertLess(abs(in_range - test_in_range), 0.001)
      print(f"Profile InRange set to: {in_range}")

      # Test boundary values
      await self.robot.set_profile_in_range(self.TEST_PROFILE_ID, -1.0)
      in_range = await self.robot.get_profile_in_range(self.TEST_PROFILE_ID)
      self.assertLess(abs(in_range - (-1.0)), 0.001)

      await self.robot.set_profile_in_range(self.TEST_PROFILE_ID, 100.0)
      in_range = await self.robot.get_profile_in_range(self.TEST_PROFILE_ID)
      self.assertLess(abs(in_range - 100.0), 0.001)

    finally:
      # Restore original InRange
      await self.robot.set_profile_in_range(self.TEST_PROFILE_ID, original_in_range)

  async def test_get_profile_straight(self) -> None:
    """Test get_profile_straight()"""
    straight = await self.robot.get_profile_straight(self.TEST_PROFILE_ID)
    self.assertIsInstance(straight, bool)
    print(f"Profile {self.TEST_PROFILE_ID} Straight: {straight} ({'straight-line' if straight else 'joint-based'} path)")

  async def test_set_profile_straight(self) -> None:
    """Test set_profile_straight()"""
    original_straight = await self.robot.get_profile_straight(self.TEST_PROFILE_ID)

    try:
      # Test setting different straight mode
      test_straight = not original_straight
      await self.robot.set_profile_straight(self.TEST_PROFILE_ID, test_straight)

      straight = await self.robot.get_profile_straight(self.TEST_PROFILE_ID)
      self.assertEqual(straight, test_straight)
      print(f"Profile Straight set to: {straight} ({'straight-line' if straight else 'joint-based'} path)")

    finally:
      # Restore original straight mode
      await self.robot.set_profile_straight(self.TEST_PROFILE_ID, original_straight)

  async def test_get_motion_profile_values(self) -> None:
    """Test get_motion_profile_values()"""
    profile_data = await self.robot.get_motion_profile_values(self.TEST_PROFILE_ID)
    self.assertIsInstance(profile_data, tuple)
    self.assertEqual(len(profile_data), 9)

    profile_id, speed, speed2, accel, decel, accel_ramp, decel_ramp, in_range, straight = profile_data

    self.assertEqual(profile_id, self.TEST_PROFILE_ID)
    self.assertIsInstance(speed, float)
    self.assertGreaterEqual(speed, 0)
    self.assertIsInstance(speed2, float)
    self.assertGreaterEqual(speed2, 0)
    self.assertIsInstance(accel, float)
    self.assertGreaterEqual(accel, 0)
    self.assertIsInstance(decel, float)
    self.assertGreaterEqual(decel, 0)
    self.assertIsInstance(accel_ramp, float)
    self.assertGreaterEqual(accel_ramp, 0)
    self.assertIsInstance(decel_ramp, float)
    self.assertGreaterEqual(decel_ramp, 0)
    self.assertIsInstance(in_range, float)
    self.assertGreaterEqual(in_range, -1)
    self.assertLessEqual(in_range, 100)
    self.assertIsInstance(straight, bool)

    print(f"Motion profile {self.TEST_PROFILE_ID}: speed={speed}%, speed2={speed2}%, accel={accel}%, decel={decel}%")
    print(f"  accel_ramp={accel_ramp}s, decel_ramp={decel_ramp}s, in_range={in_range}, straight={straight}")

  async def test_set_motion_profile_values(self) -> None:
    """Test set_motion_profile_values()"""
    # Get original profile values
    original_profile = await self.robot.get_motion_profile_values(self.TEST_PROFILE_ID)

    try:
      # Test setting complete motion profile
      test_values = {
        'speed': 60.0,
        'speed2': 15.0,
        'acceleration': 70.0,
        'deceleration': 75.0,
        'acceleration_ramp': 0.4,
        'deceleration_ramp': 0.2,
        'in_range': 25.0,
        'straight': True
      }

      await self.robot.set_motion_profile_values(
        self.TEST_PROFILE_ID,
        test_values['speed'],
        test_values['speed2'],
        test_values['acceleration'],
        test_values['deceleration'],
        test_values['acceleration_ramp'],
        test_values['deceleration_ramp'],
        test_values['in_range'],
        test_values['straight']
      )

      # Verify the values were set
      profile_data = await self.robot.get_motion_profile_values(self.TEST_PROFILE_ID)
      profile_id, speed, speed2, accel, decel, accel_ramp, decel_ramp, in_range, straight = profile_data

      self.assertLess(abs(speed - test_values['speed']), 0.001)
      self.assertLess(abs(speed2 - test_values['speed2']), 0.001)
      self.assertLess(abs(accel - test_values['acceleration']), 0.001)
      self.assertLess(abs(decel - test_values['deceleration']), 0.001)
      self.assertLess(abs(accel_ramp - test_values['acceleration_ramp']), 0.001)
      self.assertLess(abs(decel_ramp - test_values['deceleration_ramp']), 0.001)
      self.assertLess(abs(in_range - test_values['in_range']), 0.001)
      self.assertEqual(straight, test_values['straight'])

      print(f"Motion profile values set successfully: {profile_data}")

    finally:
      # Restore original profile values
      _, orig_speed, orig_speed2, orig_accel, orig_decel, orig_accel_ramp, orig_decel_ramp, orig_in_range, orig_straight = original_profile
      await self.robot.set_motion_profile_values(
        self.TEST_PROFILE_ID,
        orig_speed,
        orig_speed2,
        orig_accel,
        orig_decel,
        orig_accel_ramp,
        orig_decel_ramp,
        orig_in_range,
        orig_straight
      )

#region MOTION COMMANDS
  async def test_halt(self) -> None:
    """Test halt() command"""
    # Start a small movement and then halt it
    current_pos = await self.robot.where_c()
    x, y, z, yaw, pitch, roll, config = current_pos

    # Make a very small movement (2mm in X direction)
    await self.robot.move_c(self.TEST_PROFILE_ID, x + 2, y, z, yaw, pitch, roll)

    # Immediately halt the movement
    await self.robot.halt()
    print("Halt command executed successfully")

  async def test_move(self) -> None:
    """Test move() command"""
    # Record current position for restoration
    original_position = await self.robot.where_c()

    # Get original location for restoration
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Create a test location with small movement from current position
      x, y, z, yaw, pitch, roll, config = original_position
      test_x, test_y, test_z = x + 5, y + 5, z + 3  # Small movements (5mm each direction)
      test_yaw = yaw + 2.0  # Small rotation change

      # Save test location
      await self.robot.set_location_xyz(self.TEST_LOCATION_ID, test_x, test_y, test_z, test_yaw, pitch, roll)

      # Move to test location
      await self.robot.move(self.TEST_LOCATION_ID, self.TEST_PROFILE_ID)
      await self.robot.wait_for_eom()

      # Verify we moved to the test location
      new_position = await self.robot.where_c()
      self.assertLess(abs(new_position[0] - test_x), 2.0)
      self.assertLess(abs(new_position[1] - test_y), 2.0)
      self.assertLess(abs(new_position[2] - test_z), 2.0)
      print(f"Move to location {self.TEST_LOCATION_ID} completed successfully")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await self.robot.wait_for_eom()

  async def test_move_appro(self) -> None:
    """Test move_appro() command"""
    # Record current position for restoration
    original_position = await self.robot.where_c()

    # Get original location for restoration
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)

    # Get the Z clearance for the test location
    z_clearance_data = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)
    _, z_clearance, _ = z_clearance_data
    try:
      # Create a test location with small movement from current position
      x, y, z, yaw, pitch, roll, config = original_position
      test_x, test_y, test_z = x + 8, y + 8, z + 5  # Small movements (8mm each direction)
      test_yaw = yaw + 3.0  # Small rotation change
      test_z_clearance = 20

      # Save test location
      await self.robot.set_location_xyz(self.TEST_LOCATION_ID, test_x, test_y, test_z, test_yaw, pitch, roll)

      # Save the z clearance
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, test_z_clearance)

      # Move to test location with approach
      await self.robot.move_appro(self.TEST_LOCATION_ID, self.TEST_PROFILE_ID)
      await self.robot.wait_for_eom()

      print(f"Move approach to location {self.TEST_LOCATION_ID} with z-clearance {test_z_clearance} completed successfully")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

      # Restore original z clearance
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, z_clearance)

      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await self.robot.wait_for_eom()

  async def test_move_extra_axis(self) -> None:
    """Test move_extra_axis() command"""
    # Test with single axis - very small movement (5mm)
    await self.robot.move_extra_axis(5.0)
    print("Move extra axis (single) command executed successfully")

    # Test with two axes - very small movements (5mm each)
    await self.robot.move_extra_axis(5.0, 5.0)
    print("Move extra axis (dual) command executed successfully")

  async def test_move_one_axis(self) -> None:
    """Test move_one_axis() command"""
    # Get current joint positions for restoration
    original_joints = await self.robot.where_j()

    try:
      # Test moving axis 1 by a very small amount (2 degrees)
      test_axis = 1
      current_position = original_joints[test_axis - 1]  # Convert to 0-based index
      new_position = current_position + 2.0  # Move only 2 degrees

      await self.robot.move_one_axis(test_axis, new_position, self.TEST_PROFILE_ID)
      await self.robot.wait_for_eom()

      # Verify the axis moved
      new_joints = await self.robot.where_j()
      self.assertLess(abs(new_joints[test_axis - 1] - new_position), 1.0)
      print(f"Move one axis {test_axis} to {new_position} completed successfully")

    finally:
      # Restore original position
      await self.robot.move_j(self.TEST_PROFILE_ID, *original_joints)
      await self.robot.wait_for_eom()

  async def test_move_c(self) -> None:
    """Test move_c() command"""
    # Record current position for restoration
    original_position = await self.robot.where_c()
    x, y, z, yaw, pitch, roll, config = original_position

    try:
      # Test move without config - very small movements (5mm in each direction, 2 degrees rotation)
      test_x, test_y, test_z = x + 5, y + 5, z + 3
      test_yaw = yaw + 2.0  # Small rotation change
      await self.robot.move_c(self.TEST_PROFILE_ID, test_x, test_y, test_z, test_yaw, pitch, roll)
      await self.robot.wait_for_eom()

      # Verify position
      new_position = await self.robot.where_c()
      self.assertLess(abs(new_position[0] - test_x), 2.0)
      self.assertLess(abs(new_position[1] - test_y), 2.0)
      self.assertLess(abs(new_position[2] - test_z), 2.0)
      print(f"Move Cartesian without config completed successfully")

      # Test move with config - return to original
      await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll, config)
      await self.robot.wait_for_eom()
      print(f"Move Cartesian with config completed successfully")

    finally:
      # Return to original position
      await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await self.robot.wait_for_eom()

  async def test_move_j(self) -> None:
    """Test move_j() command"""
    # Record current joint positions for restoration
    original_joints = await self.robot.where_j()

    try:
      # Create test joint positions (very small movements - 1 degree each)
      test_joints = tuple(joint + 1.0 for joint in original_joints)

      await self.robot.move_j(self.TEST_PROFILE_ID, *test_joints)
      await self.robot.wait_for_eom()

      # Verify joint positions
      new_joints = await self.robot.where_j()

      for i, (expected, actual) in enumerate(zip(test_joints, new_joints)):
        if i > 4 and original_joints[i] == 0.0: # not all robots have 6 axes
          if abs(expected - actual) >= 1.0:
            print(f"Warning: Joint {i+1} position mismatch: expected {expected}, got {actual}")
        else:
          self.assertLess(abs(expected - actual), 1.0, f"Joint {i+1} position mismatch")

      print(f"Move joints completed successfully")

    finally:
      # Return to original position
      await self.robot.move_j(self.TEST_PROFILE_ID, *original_joints)
      await self.robot.wait_for_eom()

  async def test_release_brake(self) -> None:
    """Test release_brake() command"""
    test_axis = 1

    # Release brake on test axis
    await self.robot.release_brake(test_axis)
    print(f"Brake released on axis {test_axis} successfully")

    # Note: In a real test environment, you might want to check brake status
    # but that would require additional API methods

  async def test_set_brake(self) -> None:
    """Test set_brake() command"""
    test_axis = 1

    # Set brake on test axis
    await self.robot.set_brake(test_axis)
    print(f"Brake set on axis {test_axis} successfully")

    # Release the brake again for safety
    await self.robot.release_brake(test_axis)

  async def test_state(self) -> None:
    """Test state() command"""
    motion_state = await self.robot.state()
    self.assertIsInstance(motion_state, str)
    self.assertGreater(len(motion_state), 0)
    print(f"Motion state: {motion_state}")

  async def test_wait_for_eom(self) -> None:
    """Test wait_for_eom() command"""
    # Get current position and make a very small movement (1mm)
    current_pos = await self.robot.where_c()
    x, y, z, yaw, pitch, roll, config = current_pos

    # Start a very small movement
    await self.robot.move_c(self.TEST_PROFILE_ID, x + 1, y, z, yaw, pitch, roll)

    # Wait for end of motion
    await self.robot.wait_for_eom()
    print("Wait for end of motion completed successfully")

    # Return to original position
    await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
    await self.robot.wait_for_eom()

  async def test_zero_torque(self) -> None:
    """Test zero_torque() command"""
    test_axis_mask = 1  # Enable zero torque for axis 1

    try:
      # Enable zero torque mode for axis 1
      await self.robot.zero_torque(True, test_axis_mask)
      print(f"Zero torque enabled for axis mask {test_axis_mask}")

      # Brief pause to allow the mode to take effect
      await asyncio.sleep(0.5)

    finally:
      # Disable zero torque mode for safety
      await self.robot.zero_torque(False)
      print("Zero torque mode disabled")

#region PAROBOT COMMANDS
  async def test_change_config(self) -> None:
    """Test change_config() command"""
    # Record current config for restoration
    original_config = await self.robot.get_location_config(self.TEST_LOCATION_ID)
    _, orig_config_value = original_config

    try:
      # Test with default grip mode (no gripper change)
      await self.robot.change_config()
      print("Change config (default) executed successfully")

      # Test with gripper open
      await self.robot.change_config(1)
      print("Change config with gripper open executed successfully")

      # Test with gripper close
      await self.robot.change_config(2)
      print("Change config with gripper close executed successfully")

    finally:
      # Allow time for robot to settle and restore original config if needed
      await asyncio.sleep(1.0)

  async def test_change_config2(self) -> None:
    """Test change_config2() command"""
    try:
      # Test with default grip mode (no gripper change)
      await self.robot.change_config2()
      print("Change config2 (default) executed successfully")

      # Test with gripper open
      await self.robot.change_config2(1)
      print("Change config2 with gripper open executed successfully")

      # Test with gripper close
      await self.robot.change_config2(2)
      print("Change config2 with gripper close executed successfully")

    finally:
      # Allow time for robot to settle
      await asyncio.sleep(1.0)

  async def test_get_grasp_data(self) -> None:
    """Test get_grasp_data()"""
    grasp_data = await self.robot.get_grasp_data()
    self.assertIsInstance(grasp_data, tuple)
    self.assertEqual(len(grasp_data), 3)
    plate_width, finger_speed, grasp_force = grasp_data
    self.assertIsInstance(plate_width, float)
    self.assertIsInstance(finger_speed, float)
    self.assertIsInstance(grasp_force, float)
    print(f"Grasp data: plate_width={plate_width}mm, finger_speed={finger_speed}%, grasp_force={grasp_force}N")

  async def test_set_grasp_data(self) -> None:
    """Test set_grasp_data()"""
    # Get original grasp data for restoration
    original_grasp = await self.robot.get_grasp_data()

    try:
      # Test setting grasp data
      test_plate_width = 10.5
      test_finger_speed = 75.0
      test_grasp_force = 20.0

      await self.robot.set_grasp_data(test_plate_width, test_finger_speed, test_grasp_force)

      # Verify the data was set
      new_grasp = await self.robot.get_grasp_data()
      plate_width, finger_speed, grasp_force = new_grasp

      self.assertLess(abs(plate_width - test_plate_width), 0.001)
      self.assertLess(abs(finger_speed - test_finger_speed), 0.001)
      self.assertLess(abs(grasp_force - test_grasp_force), 0.001)
      print(f"Grasp data set successfully: {new_grasp}")

    finally:
      # Restore original grasp data
      await self.robot.set_grasp_data(*original_grasp)

  async def test_get_grip_close_pos(self) -> None:
    """Test get_grip_close_pos()"""
    close_pos = await self.robot.get_grip_close_pos()
    self.assertIsInstance(close_pos, float)
    print(f"Gripper close position: {close_pos}")

  async def test_set_grip_close_pos(self) -> None:
    """Test set_grip_close_pos()"""
    # Get original close position for restoration
    original_close_pos = await self.robot.get_grip_close_pos()

    try:
      # Test setting close position
      test_close_pos = original_close_pos + 5.0
      await self.robot.set_grip_close_pos(test_close_pos)

      # Verify the position was set
      new_close_pos = await self.robot.get_grip_close_pos()
      self.assertLess(abs(new_close_pos - test_close_pos), 0.001)
      print(f"Gripper close position set to: {new_close_pos}")

    finally:
      # Restore original close position
      await self.robot.set_grip_close_pos(original_close_pos)

  async def test_get_grip_open_pos(self) -> None:
    """Test get_grip_open_pos()"""
    open_pos = await self.robot.get_grip_open_pos()
    self.assertIsInstance(open_pos, float)
    print(f"Gripper open position: {open_pos}")

  async def test_set_grip_open_pos(self) -> None:
    """Test set_grip_open_pos()"""
    # Get original open position for restoration
    original_open_pos = await self.robot.get_grip_open_pos()

    try:
      # Test setting open position
      test_open_pos = original_open_pos + 5.0
      await self.robot.set_grip_open_pos(test_open_pos)

      # Verify the position was set
      new_open_pos = await self.robot.get_grip_open_pos()
      self.assertLess(abs(new_open_pos - test_open_pos), 0.001)
      print(f"Gripper open position set to: {new_open_pos}")

    finally:
      # Restore original open position
      await self.robot.set_grip_open_pos(original_open_pos)

  async def test_gripper(self) -> None:
    """Test gripper() command"""
    # Test opening gripper
    await self.robot.gripper(1)
    print("Gripper opened successfully")

    # Brief delay to allow gripper to move
    await asyncio.sleep(0.5)

    # Test closing gripper
    await self.robot.gripper(2)
    print("Gripper closed successfully")

    # Test invalid grip mode
    with self.assertRaises(ValueError):
      await self.robot.gripper(3)

  async def test_move_rail(self) -> None:
    """Test move_rail() command"""
    # Test canceling pending move rail
    await self.robot.move_rail(mode=0)
    print("Move rail canceled successfully")

    # Test moving rail immediately with explicit destination
    await self.robot.move_rail(station_id=1, mode=1, rail_destination=100.0)
    print("Move rail immediately with destination executed successfully")

    # Test moving rail with station ID only
    await self.robot.move_rail(station_id=self.TEST_LOCATION_ID, mode=1)
    print("Move rail immediately with station executed successfully")

    # Test setting rail to move during next pick/place
    await self.robot.move_rail(station_id=self.TEST_LOCATION_ID, mode=2)
    print("Move rail during next pick/place set successfully")

  async def test_move_to_safe(self) -> None:
    """Test move_to_safe() command"""
    # Record current position for comparison
    original_position = await self.robot.where_c()

    # Move to safe position
    await self.robot.move_to_safe()
    await self.robot.wait_for_eom()

    # Verify we moved to a different position
    safe_position = await self.robot.where_c()
    position_changed = any(abs(orig - safe) > 1.0 for orig, safe in zip(original_position[:6], safe_position[:6]))

    print(f"Move to safe position completed successfully")
    print(f"Position changed: {position_changed}")

  async def test_set_pallet_index(self) -> None:
    """Test set_pallet_index() and get_pallet_index()"""
    # Get original station type to check if it's a pallet
    original_station = await self.robot.get_station_type(self.TEST_LOCATION_ID)
    _, orig_access_type, orig_location_type, orig_z_clearance, orig_z_above, orig_z_grasp_offset = original_station

    was_pallet = orig_location_type == 1
    original_pallet = None

    try:
      # If it's already a pallet, get the original pallet index
      if was_pallet:
        original_pallet = await self.robot.get_pallet_index(self.TEST_LOCATION_ID)
        _, orig_x, orig_y, orig_z = original_pallet
        print(f"Station {self.TEST_LOCATION_ID} is already pallet type with index X={orig_x}, Y={orig_y}, Z={orig_z}")
      else:
        # Convert to pallet type first
        await self.robot.set_station_type(self.TEST_LOCATION_ID, orig_access_type, 1, orig_z_clearance, orig_z_above, orig_z_grasp_offset)
        print(f"Station {self.TEST_LOCATION_ID} converted to pallet type for testing")

      # Test get_pallet_index()
      pallet_data = await self.robot.get_pallet_index(self.TEST_LOCATION_ID)
      self.assertIsInstance(pallet_data, tuple)
      self.assertEqual(len(pallet_data), 4)
      station_id, pallet_x, pallet_y, pallet_z = pallet_data
      self.assertEqual(station_id, self.TEST_LOCATION_ID)
      self.assertIsInstance(pallet_x, int)
      self.assertIsInstance(pallet_y, int)
      self.assertIsInstance(pallet_z, int)
      print(f"Current pallet index for station {station_id}: X={pallet_x}, Y={pallet_y}, Z={pallet_z}")

      # Test setting all indices
      test_x, test_y, test_z = 1, 1, 1
      await self.robot.set_pallet_index(self.TEST_LOCATION_ID, test_x, test_y, test_z)

      # Verify the indices were set
      new_pallet = await self.robot.get_pallet_index(self.TEST_LOCATION_ID)
      _, pallet_x, pallet_y, pallet_z = new_pallet
      self.assertEqual(pallet_x, test_x)
      self.assertEqual(pallet_y, test_y)
      self.assertEqual(pallet_z, test_z)
      print(f"Pallet index set successfully: X={pallet_x}, Y={pallet_y}, Z={pallet_z}")

      # Test setting only X index
      await self.robot.set_pallet_index(self.TEST_LOCATION_ID, pallet_index_x=1)
      new_pallet = await self.robot.get_pallet_index(self.TEST_LOCATION_ID)
      _, pallet_x, _, _ = new_pallet
      self.assertEqual(pallet_x, 1)
      print(f"Pallet X index set to: {pallet_x}")

    finally:
      # Restore everything to original state
      if was_pallet and original_pallet:
        # Restore original pallet indices
        _, orig_x, orig_y, orig_z = original_pallet
        await self.robot.set_pallet_index(self.TEST_LOCATION_ID, orig_x, orig_y, orig_z)
        print(f"Restored original pallet indices: X={orig_x}, Y={orig_y}, Z={orig_z}")
      else:
        # Convert back to original station type (not pallet)
        await self.robot.set_station_type(self.TEST_LOCATION_ID, orig_access_type, orig_location_type, orig_z_clearance, orig_z_above, orig_z_grasp_offset)
        print(f"Station {self.TEST_LOCATION_ID} restored to original non-pallet type")

  async def test_get_pallet_origin(self) -> None:
    """Test get_pallet_origin()"""
    origin_data = await self.robot.get_pallet_origin(self.TEST_LOCATION_ID)
    self.assertIsInstance(origin_data, tuple)
    self.assertEqual(len(origin_data), 8)
    station_id, x, y, z, yaw, pitch, roll, config = origin_data
    self.assertEqual(station_id, self.TEST_LOCATION_ID)
    self.assertTrue(all(isinstance(val, (int, float)) for val in [x, y, z, yaw, pitch, roll]))
    self.assertIsInstance(config, int)
    print(f"Pallet origin for station {station_id}: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}, Config={config}")

  async def test_set_pallet_origin_and_setup(self) -> None:
    """Test set_pallet_origin() and comprehensive pallet configuration"""
    # Get original pallet data for restoration
    original_origin = await self.robot.get_pallet_origin(self.TEST_LOCATION_ID)
    original_pallet_x = await self.robot.get_pallet_x(self.TEST_LOCATION_ID)
    original_pallet_y = await self.robot.get_pallet_y(self.TEST_LOCATION_ID)
    original_pallet_z = await self.robot.get_pallet_z(self.TEST_LOCATION_ID)

    try:
      # Test setting complete pallet configuration
      test_origin = (100.0, 200.0, 300.0, 10.0, 20.0, 30.0, 1)  # Include config
      test_x_count, test_x_offset = 3, (test_origin[0] + 10.0, test_origin[1], test_origin[2])  # X direction offset
      test_y_count, test_y_offset = 4, (test_origin[0], test_origin[1] + 15.0, test_origin[2])  # Y direction offset
      test_z_count, test_z_offset = 2, (test_origin[0], test_origin[1], test_origin[2] + 8.0)   # Z direction offset

      # Set pallet origin first
      await self.robot.set_pallet_origin(self.TEST_LOCATION_ID, *test_origin)
      print(f"Pallet origin set to: {test_origin}")

      # Set pallet X configuration
      await self.robot.set_pallet_x(self.TEST_LOCATION_ID, test_x_count, *test_x_offset)
      print(f"Pallet X set: count={test_x_count}, offset={test_x_offset}")

      # Set pallet Y configuration
      await self.robot.set_pallet_y(self.TEST_LOCATION_ID, test_y_count, *test_y_offset)
      print(f"Pallet Y set: count={test_y_count}, offset={test_y_offset}")

      # Set pallet Z configuration
      await self.robot.set_pallet_z(self.TEST_LOCATION_ID, test_z_count, *test_z_offset)
      print(f"Pallet Z set: count={test_z_count}, offset={test_z_offset}")

      # Verify all configurations were set correctly
      new_origin = await self.robot.get_pallet_origin(self.TEST_LOCATION_ID)
      new_pallet_x = await self.robot.get_pallet_x(self.TEST_LOCATION_ID)
      new_pallet_y = await self.robot.get_pallet_y(self.TEST_LOCATION_ID)
      new_pallet_z = await self.robot.get_pallet_z(self.TEST_LOCATION_ID)

      # Verify origin
      _, x, y, z, yaw, pitch, roll, config = new_origin
      for i, (expected, actual) in enumerate(zip(test_origin[:-1], (x, y, z, yaw, pitch, roll))):
        self.assertLess(abs(expected - actual), 0.001, f"Origin coordinate {i} mismatch")
      self.assertEqual(config, test_origin[-1])

      # Verify X configuration
      _, x_count, world_x, world_y, world_z = new_pallet_x
      self.assertEqual(x_count, test_x_count)
      for i, (expected, actual) in enumerate(zip(test_x_offset, (world_x, world_y, world_z))):
        self.assertLess(abs(expected - actual), 0.001, f"Pallet X offset {i} mismatch")

      # Verify Y configuration
      _, y_count, world_x, world_y, world_z = new_pallet_y
      self.assertEqual(y_count, test_y_count)
      for i, (expected, actual) in enumerate(zip(test_y_offset, (world_x, world_y, world_z))):
        self.assertLess(abs(expected - actual), 0.001, f"Pallet Y offset {i} mismatch")

      # Verify Z configuration
      _, z_count, world_x, world_y, world_z = new_pallet_z
      self.assertEqual(z_count, test_z_count)
      for i, (expected, actual) in enumerate(zip(test_z_offset, (world_x, world_y, world_z))):
        self.assertLess(abs(expected - actual), 0.001, f"Pallet Z offset {i} mismatch")

      # Test that pallet indexing works correctly by setting different indices
      # and verifying the calculated positions change appropriately
      test_indices = [(1, 1, 1), (2, 1, 1), (1, 2, 1), (1, 1, 2)]
      expected_offsets = [
        (0, 0, 0),  # Base position (index 1,1,1)
        (test_x_offset[0], test_x_offset[1], test_x_offset[2]),  # X+1
        (test_y_offset[0], test_y_offset[1], test_y_offset[2]),  # Y+1
        (test_z_offset[0], test_z_offset[1], test_z_offset[2])   # Z+1
      ]

      for (x_idx, y_idx, z_idx), (exp_x_off, exp_y_off, exp_z_off) in zip(test_indices, expected_offsets):
        # Set the pallet index
        await self.robot.set_pallet_index(self.TEST_LOCATION_ID, x_idx, y_idx, z_idx)

        # Verify the index was set
        pallet_index = await self.robot.get_pallet_index(self.TEST_LOCATION_ID)
        _, curr_x_idx, curr_y_idx, curr_z_idx = pallet_index
        self.assertEqual((curr_x_idx, curr_y_idx, curr_z_idx), (x_idx, y_idx, z_idx))

        # Calculate expected position based on origin + index offsets
        expected_x = test_origin[0] + (x_idx - 1) * test_x_offset[0] + (y_idx - 1) * test_y_offset[0] + (z_idx - 1) * test_z_offset[0]
        expected_y = test_origin[1] + (x_idx - 1) * test_x_offset[1] + (y_idx - 1) * test_y_offset[1] + (z_idx - 1) * test_z_offset[1]
        expected_z = test_origin[2] + (x_idx - 1) * test_x_offset[2] + (y_idx - 1) * test_y_offset[2] + (z_idx - 1) * test_z_offset[2]

        print(f"Index ({x_idx},{y_idx},{z_idx}) -> Expected position: ({expected_x:.1f}, {expected_y:.1f}, {expected_z:.1f})")

      print("Complete pallet configuration test passed successfully")

    finally:
      # Restore all original configurations in reverse order
      # Restore pallet indices first
      # Get the current pallet index to restore properly
      try:
        current_pallet_index = await self.robot.get_pallet_index(self.TEST_LOCATION_ID)
        _, orig_x_idx, orig_y_idx, orig_z_idx = current_pallet_index
      except:
        # Fallback to default values if getting pallet index fails
        orig_x_idx, orig_y_idx, orig_z_idx = 1, 1, 1
      await self.robot.set_pallet_index(self.TEST_LOCATION_ID, orig_x_idx, orig_y_idx, orig_z_idx)

      # Restore pallet Z
      _, orig_z_count, orig_z_x, orig_z_y, orig_z_z = original_pallet_z
      await self.robot.set_pallet_z(self.TEST_LOCATION_ID, orig_z_count, orig_z_x, orig_z_y, orig_z_z)

      # Restore pallet Y
      _, orig_y_count, orig_y_x, orig_y_y, orig_y_z = original_pallet_y
      await self.robot.set_pallet_y(self.TEST_LOCATION_ID, orig_y_count, orig_y_x, orig_y_y, orig_y_z)

      # Restore pallet X
      _, orig_x_count, orig_x_x, orig_x_y, orig_x_z = original_pallet_x
      await self.robot.set_pallet_x(self.TEST_LOCATION_ID, orig_x_count, orig_x_x, orig_x_y, orig_x_z)

      # Restore pallet origin last
      _, orig_x, orig_y, orig_z, orig_yaw, orig_pitch, orig_roll, orig_config = original_origin
      await self.robot.set_pallet_origin(self.TEST_LOCATION_ID, orig_x, orig_y, orig_z, orig_yaw, orig_pitch, orig_roll, orig_config)

      print("All pallet configurations restored to original values")

#####################################
##### TESTING HERE TESTING HERE #####
#####################################

  async def test_pick_plate_station(self) -> None:
    """Test pick_plate_station() command"""
    # Record current position for restoration
    original_position = await self.robot.where_c()
    # Get original location for restoration
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)
    # Get the Z clearance for the test location
    original_z_clearance = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)

    try:
      # Create a test location with small movement from current position
      x, y, z, yaw, pitch, roll, _ = original_position
      test_x, test_y, test_z = x + 10, y + 10, z + 5  # Small movements for pick test
      test_yaw = yaw + 1.0  # Small rotation change

      # Save test location - force it to Cartesian type for this test
      await self.robot.set_location_xyz(self.TEST_LOCATION_ID, test_x, test_y, test_z, test_yaw, pitch, roll)
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, 20.0)


      # Test basic pick without compliance
      result = await self.robot.pick_plate_station(self.TEST_LOCATION_ID)
      self.assertIsInstance(result, bool)
      print(f"Pick plate station (basic) result: {result}")

      # Test pick with horizontal compliance
      result = await self.robot.pick_plate_station(self.TEST_LOCATION_ID, horizontal_compliance=True, horizontal_compliance_torque=50)
      self.assertIsInstance(result, bool)
      print(f"Pick plate station (with compliance) result: {result}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])
      _, z_clearance, z_world = original_z_clearance
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, z_clearance, z_world)

      # Return to original position
      x, y, z, yaw, pitch, roll, _ = original_position
      await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await self.robot.wait_for_eom()

  async def test_place_plate_station(self) -> None:
    """Test place_plate_station() command"""
    # Record current position for restoration
    original_position = await self.robot.where_c()

    try:
      # Test basic place without compliance
      await self.robot.place_plate_station(self.TEST_LOCATION_ID)
      print("Place plate station (basic) executed successfully")

      # Test place with horizontal compliance
      await self.robot.place_plate_station(self.TEST_LOCATION_ID, horizontal_compliance=True, horizontal_compliance_torque=25)
      print("Place plate station (with compliance) executed successfully")

    finally:
      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await self.robot.wait_for_eom()

  async def test_get_rail_position(self) -> None:
    """Test get_rail_position()"""
    rail_pos = await self.robot.get_rail_position(self.TEST_LOCATION_ID)
    self.assertIsInstance(rail_pos, float)
    print(f"Rail position for station {self.TEST_LOCATION_ID}: {rail_pos}")

  async def test_set_rail_position(self) -> None:
    """Test set_rail_position()"""
    # Get original rail position for restoration
    original_rail_pos = await self.robot.get_rail_position(self.TEST_LOCATION_ID)

    try:
      # Test setting rail position
      test_rail_pos = original_rail_pos + 10.0
      await self.robot.set_rail_position(self.TEST_LOCATION_ID, test_rail_pos)

      # Verify the position was set
      new_rail_pos = await self.robot.get_rail_position(self.TEST_LOCATION_ID)
      self.assertLess(abs(new_rail_pos - test_rail_pos), 0.001)
      print(f"Rail position set to: {new_rail_pos}")

    finally:
      # Restore original rail position
      await self.robot.set_rail_position(self.TEST_LOCATION_ID, original_rail_pos)

  async def test_teach_plate_station(self) -> None:
    """Test teach_plate_station() command"""
    # Get original location for restoration
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)
    original_clearance = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)

    try:
      # Test teaching with default Z clearance
      await self.robot.teach_plate_station(self.TEST_LOCATION_ID)
      print(f"Plate station {self.TEST_LOCATION_ID} taught with default clearance")

      # Test teaching with custom Z clearance
      test_clearance = 75.0
      await self.robot.teach_plate_station(self.TEST_LOCATION_ID, test_clearance)

      # Verify the clearance was set
      new_clearance = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)
      _, z_clearance, _ = new_clearance
      self.assertLess(abs(z_clearance - test_clearance), 0.001)
      print(f"Plate station taught with custom clearance: {z_clearance}")

    finally:
      # Restore original location and clearance
      type_code = original_location[0]
      if type_code == 0:  # Cartesian
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Angles
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

      _, orig_z_clearance, orig_z_world = original_clearance
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, orig_z_clearance, orig_z_world)

  async def test_get_station_type(self) -> None:
    """Test get_station_type()"""
    station_data = await self.robot.get_station_type(self.TEST_LOCATION_ID)
    self.assertIsInstance(station_data, tuple)
    self.assertEqual(len(station_data), 6)
    station_id, access_type, location_type, z_clearance, z_above, z_grasp_offset = station_data

    self.assertEqual(station_id, self.TEST_LOCATION_ID)
    self.assertIn(access_type, [0, 1])  # 0 = horizontal, 1 = vertical
    self.assertIn(location_type, [0, 1])  # 0 = normal, 1 = pallet
    self.assertIsInstance(z_clearance, float)
    self.assertIsInstance(z_above, float)
    self.assertIsInstance(z_grasp_offset, float)

    access_str = "horizontal" if access_type == 0 else "vertical"
    location_str = "normal single" if location_type == 0 else "pallet"
    print(f"Station {station_id}: access={access_str}, type={location_str}, clearance={z_clearance}, above={z_above}, grasp_offset={z_grasp_offset}")

  async def test_set_station_type(self) -> None:
    """Test set_station_type()"""
    # Get original station type for restoration
    original_station = await self.robot.get_station_type(self.TEST_LOCATION_ID)

    try:
      # Test setting station type
      test_values = (
      0,     # access_type: horizontal
      1,     # location_type: pallet
      60.0,  # z_clearance
      15.0,  # z_above
      5.0    # z_grasp_offset
      )

      await self.robot.set_station_type(self.TEST_LOCATION_ID, *test_values)

      # Verify the station type was set
      new_station = await self.robot.get_station_type(self.TEST_LOCATION_ID)
      _, access_type, location_type, z_clearance, z_above, z_grasp_offset = new_station

      self.assertEqual(access_type, test_values[0])
      self.assertEqual(location_type, test_values[1])
      self.assertLess(abs(z_clearance - test_values[2]), 0.001)
      self.assertLess(abs(z_above - test_values[3]), 0.001)
      self.assertLess(abs(z_grasp_offset - test_values[4]), 0.001)

      print(f"Station type set successfully: {test_values}")

      # Test invalid access type
      with self.assertRaises(ValueError):
        await self.robot.set_station_type(self.TEST_LOCATION_ID, 3, 0, 50.0, 10.0, 0.0)

      # Test invalid location type
      with self.assertRaises(ValueError):
        await self.robot.set_station_type(self.TEST_LOCATION_ID, 0, 3, 50.0, 10.0, 0.0)

    finally:
      # Restore original station type
      _, orig_access, orig_location, orig_clearance, orig_above, orig_grasp = original_station
      await self.robot.set_station_type(self.TEST_LOCATION_ID, orig_access, orig_location, orig_clearance, orig_above, orig_grasp)

#region SSGRIP COMMANDS
  async def test_home_all_if_no_plate(self) -> None:
    """Test home_all_if_no_plate()"""
    result = await self.robot.home_all_if_no_plate()
    self.assertIsInstance(result, int)
    self.assertIn(result, [-1, 0])

    result_str = "no plate detected and command succeeded" if result == -1 else "plate detected"
    print(f"Home all if no plate result: {result} ({result_str})")

  async def test_grasp_plate(self) -> None:
    """Test grasp_plate()"""
    # Test with valid parameters for closing gripper
    result = await self.robot.grasp_plate(15.0, 50, 10.0)
    self.assertIsInstance(result, int)
    self.assertIn(result, [-1, 0])

    result_str = "plate grasped" if result == -1 else "no plate detected"
    print(f"Grasp plate (close) result: {result} ({result_str})")

    # Test with negative force for opening gripper
    result = await self.robot.grasp_plate(25.0, 75, -5.0)
    self.assertIsInstance(result, int)
    self.assertIn(result, [-1, 0])
    print(f"Grasp plate (open) result: {result}")

    # Test invalid finger speed
    with self.assertRaises(ValueError):
      await self.robot.grasp_plate(15.0, 0, 10.0)  # Speed too low

    with self.assertRaises(ValueError):
      await self.robot.grasp_plate(15.0, 101, 10.0)  # Speed too high

  async def test_release_plate(self) -> None:
    """Test release_plate()"""
    # Test basic release
    await self.robot.release_plate(30.0, 50)
    print("Release plate (basic) executed successfully")

    # Test release with InRange
    await self.robot.release_plate(35.0, 75, 5.0)
    print("Release plate (with InRange) executed successfully")

    # Test invalid finger speed
    with self.assertRaises(ValueError):
      await self.robot.release_plate(30.0, 0)  # Speed too low

    with self.assertRaises(ValueError):
      await self.robot.release_plate(30.0, 101)  # Speed too high

  async def test_is_fully_closed(self) -> None:
    """Test is_fully_closed()"""
    closed_state = await self.robot.is_fully_closed()
    self.assertIsInstance(closed_state, int)
    print(f"Gripper fully closed state: {closed_state}")

    # For standard gripper: -1 means within 2mm of fully closed, 0 means not
    # For dual gripper: bitmask where bit 0 = gripper 1, bit 1 = gripper 2
    if closed_state in [-1, 0]:
      state_str = "within 2mm of fully closed" if closed_state == -1 else "not fully closed"
      print(f"Standard gripper state: {state_str}")
    else:
      gripper1_closed = bool(closed_state & 1)
      gripper2_closed = bool(closed_state & 2)
      print(f"Dual gripper state: Gripper 1 {'closed' if gripper1_closed else 'open'}, Gripper 2 {'closed' if gripper2_closed else 'open'}")

  async def test_set_active_gripper(self) -> None:
    """Test set_active_gripper() (Dual Gripper Only)"""
    # Note: This test assumes a dual gripper system
    # Get original active gripper for restoration
    try:
      original_gripper = await self.robot.get_active_gripper()
    except:
      # Skip test if dual gripper not available
      print("Dual gripper not available, skipping set_active_gripper test")
      return

    try:
      # Test setting gripper 1 without spin
      await self.robot.set_active_gripper(1, 0)
      active_gripper = await self.robot.get_active_gripper()
      self.assertEqual(active_gripper, 1)
      print("Active gripper set to 1 (no spin)")

      # Test setting gripper 2 without spin
      await self.robot.set_active_gripper(2, 0)
      active_gripper = await self.robot.get_active_gripper()
      self.assertEqual(active_gripper, 2)
      print("Active gripper set to 2 (no spin)")

      # Test setting gripper with spin and profile
      await self.robot.set_active_gripper(1, 1, self.TEST_PROFILE_ID)
      active_gripper = await self.robot.get_active_gripper()
      self.assertEqual(active_gripper, 1)
      print("Active gripper set to 1 (with spin and profile)")

      # Test invalid gripper ID
      with self.assertRaises(ValueError):
        await self.robot.set_active_gripper(3, 0)

      # Test invalid spin mode
      with self.assertRaises(ValueError):
        await self.robot.set_active_gripper(1, 2)

    finally:
      # Restore original active gripper
      await self.robot.set_active_gripper(original_gripper, 0)

  async def test_get_active_gripper(self) -> None:
    """Test get_active_gripper() (Dual Gripper Only)"""
    try:
      active_gripper = await self.robot.get_active_gripper()
      self.assertIsInstance(active_gripper, int)
      self.assertIn(active_gripper, [1, 2])

      gripper_name = "A" if active_gripper == 1 else "B"
      print(f"Active gripper: {active_gripper} (Gripper {gripper_name})")
    except:
      print("Dual gripper not available, skipping get_active_gripper test")

  async def test_free_mode(self) -> None:
    """Test free_mode()"""
    try:
      # Test enabling free mode for all axes
      await self.robot.free_mode(True, 0)
      print("Free mode enabled for all axes")

      # Brief pause to allow mode to take effect
      await asyncio.sleep(0.5)

      # Test enabling free mode for specific axis
      await self.robot.free_mode(True, 1)
      print("Free mode enabled for axis 1")

      # Brief pause
      await asyncio.sleep(0.5)

    finally:
      # Always disable free mode for safety
      await self.robot.free_mode(False)
      print("Free mode disabled for all axes")

  async def test_open_gripper(self) -> None:
    """Test open_gripper()"""
    await self.robot.open_gripper()
    print("Gripper opened successfully")

    # Brief delay to allow gripper to move
    await asyncio.sleep(0.5)

  async def test_close_gripper(self) -> None:
    """Test close_gripper()"""
    await self.robot.close_gripper()
    print("Gripper closed successfully")

    # Brief delay to allow gripper to move
    await asyncio.sleep(0.5)

  async def test_pick_plate(self) -> None:
    """Test pick_plate()"""
    # Record current position for restoration
    original_position = await self.robot.where_c()

    try:
      # Test basic pick without compliance
      await self.robot.pick_plate(self.TEST_LOCATION_ID)
      print(f"Pick plate (basic) at location {self.TEST_LOCATION_ID} executed successfully")

      # Test pick with horizontal compliance
      await self.robot.pick_plate(self.TEST_LOCATION_ID, horizontal_compliance=True, horizontal_compliance_torque=50)
      print(f"Pick plate (with compliance) at location {self.TEST_LOCATION_ID} executed successfully")

    except Exception as e:
      # Handle case where no plate is detected (expected in some test scenarios)
      if "no plate present" in str(e):
        print(f"Pick plate detected no plate (expected): {e}")
      else:
        raise
    finally:
      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await self.robot.wait_for_eom()

  async def test_place_plate(self) -> None:
    """Test place_plate()"""
    # Record current position for restoration
    original_position = await self.robot.where_c()

    try:
      # Test basic place without compliance
      await self.robot.place_plate(self.TEST_LOCATION_ID)
      print(f"Place plate (basic) at location {self.TEST_LOCATION_ID} executed successfully")

      # Test place with horizontal compliance
      await self.robot.place_plate(self.TEST_LOCATION_ID, horizontal_compliance=True, horizontal_compliance_torque=25)
      print(f"Place plate (with compliance) at location {self.TEST_LOCATION_ID} executed successfully")

    finally:
      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await self.robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await self.robot.wait_for_eom()

  async def test_teach_position(self) -> None:
    """Test teach_position()"""
    # Get original location and clearance for restoration
    original_location = await self.robot.get_location(self.TEST_LOCATION_ID)
    original_clearance = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)

    try:
      # Test teaching with default Z clearance
      await self.robot.teach_position(self.TEST_LOCATION_ID)
      print(f"Position {self.TEST_LOCATION_ID} taught with default clearance (50.0)")

      # Verify the location was recorded as Cartesian type
      location_data = await self.robot.get_location(self.TEST_LOCATION_ID)
      type_code = location_data[0]
      self.assertEqual(type_code, 0)  # Should be Cartesian type

      # Test teaching with custom Z clearance
      test_clearance = 75.0
      await self.robot.teach_position(self.TEST_LOCATION_ID, test_clearance)

      # Verify the clearance was set
      new_clearance = await self.robot.get_location_z_clearance(self.TEST_LOCATION_ID)
      _, z_clearance, _ = new_clearance
      self.assertLess(abs(z_clearance - test_clearance), 0.001)
      print(f"Position taught with custom clearance: {z_clearance}")

    finally:
      # Restore original location and clearance
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await self.robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await self.robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

      _, orig_z_clearance, orig_z_world = original_clearance
      await self.robot.set_location_z_clearance(self.TEST_LOCATION_ID, orig_z_clearance, orig_z_world)


# if __name__ == "__main__":

#   async def run_general_command_tests() -> None:
#     """Run all tests in the GENERAL COMMANDS region"""
#     test_instance = PreciseFlexHardwareTests()

#     try:
#       await test_instance.asyncSetUp()
#       print("Starting GENERAL COMMANDS tests...")

#       # Run all general command tests in order
#       await test_instance.test_robot_connection_and_version()
#       await test_instance.test_get_base()
#       await test_instance.test_set_base()
#       await test_instance.test_home()
#       await test_instance.test_home_all()
#       await test_instance.test_get_power_state()
#       await test_instance.test_set_power()
#       await test_instance.test_get_mode()
#       await test_instance.test_set_mode()
#       await test_instance.test_get_monitor_speed()
#       await test_instance.test_set_monitor_speed()
#       await test_instance.test_nop()
#       await test_instance.test_get_payload()
#       await test_instance.test_set_payload()
#       await test_instance.test_parameter_operations()
#       await test_instance.test_get_selected_robot()
#       await test_instance.test_select_robot()
#       await test_instance.test_signal_operations()
#       await test_instance.test_get_system_state()
#       await test_instance.test_get_tool()
#       await test_instance.test_set_tool()
#       await test_instance.test_get_version()
#       # Note: test_reset is commented out for safety

#       print("GENERAL COMMANDS tests completed successfully!")

#     except Exception as e:
#       print(f"General commands test failed with error: {e}")
#       raise
#     finally:
#       await test_instance.asyncTearDown()


#   async def run_location_command_tests() -> None:
#     """Run all tests in the LOCATION COMMANDS region"""
#     test_instance = PreciseFlexHardwareTests()

#     try:
#       await test_instance.asyncSetUp()
#       print("Starting LOCATION COMMANDS tests...")

#       await test_instance.test_get_location()
#       await test_instance.test_get_location_angles()
#       await test_instance.test_set_location_angles()
#       await test_instance.test_get_location_xyz()
#       await test_instance.test_set_location_xyz()
#       await test_instance.test_get_location_z_clearance()
#       await test_instance.test_set_location_z_clearance()
#       await test_instance.test_get_location_config()
#       await test_instance.test_set_location_config()
#       await test_instance.test_dest_c()
#       await test_instance.test_dest_j()
#       await test_instance.test_here_j()
#       await test_instance.test_here_c()
#       await test_instance.test_where()
#       await test_instance.test_where_c()
#       await test_instance.test_where_j()

#       print("LOCATION COMMANDS tests completed successfully!")

#     except Exception as e:
#       print(f"Location commands test failed with error: {e}")
#       raise
#     finally:
#       await test_instance.asyncTearDown()

#   async def run_profile_command_tests() -> None:
#     """Run all tests in the PROFILE COMMANDS region"""
#     test_instance = PreciseFlexHardwareTests()

#     try:
#       await test_instance.asyncSetUp()
#       print("Starting PROFILE COMMANDS tests...")

#       await test_instance.test_get_profile_speed()
#       await test_instance.test_set_profile_speed()
#       await test_instance.test_get_profile_speed2()
#       await test_instance.test_set_profile_speed2()
#       await test_instance.test_get_profile_accel()
#       await test_instance.test_set_profile_accel()
#       await test_instance.test_get_profile_accel_ramp()
#       await test_instance.test_set_profile_accel_ramp()
#       await test_instance.test_get_profile_decel()
#       await test_instance.test_set_profile_decel()
#       await test_instance.test_get_profile_decel_ramp()
#       await test_instance.test_set_profile_decel_ramp()
#       await test_instance.test_get_profile_in_range()
#       await test_instance.test_set_profile_in_range()
#       await test_instance.test_get_profile_straight()
#       await test_instance.test_set_profile_straight()
#       await test_instance.test_get_motion_profile_values()
#       await test_instance.test_set_motion_profile_values()

#       print("PROFILE COMMANDS tests completed successfully!")

#     except Exception as e:
#       print(f"Profile commands test failed with error: {e}")
#       raise
#     finally:
#       await test_instance.asyncTearDown()

#   async def run_motion_command_tests() -> None:
#     """Run all tests in the MOTION COMMANDS region"""
#     test_instance = PreciseFlexHardwareTests()

#     try:
#       await test_instance.asyncSetUp()
#       print("Starting MOTION COMMANDS tests...")

#       await test_instance.test_halt()
#       await test_instance.test_move()
#       await test_instance.test_move_appro()
#       await test_instance.test_move_extra_axis()
#       await test_instance.test_move_one_axis()
#       await test_instance.test_move_c()
#       await test_instance.test_move_j()
#       await test_instance.test_release_brake()
#       await test_instance.test_set_brake()
#       await test_instance.test_state()
#       await test_instance.test_wait_for_eom()
#       await test_instance.test_zero_torque()

#       print("MOTION COMMANDS tests completed successfully!")

#     except Exception as e:
#       print(f"Motion commands test failed with error: {e}")
#       raise
#     finally:
#       await test_instance.asyncTearDown()

#   async def run_parobot_command_tests() -> None:
#     """Run all tests in the PAROBOT COMMANDS region"""
#     test_instance = PreciseFlexHardwareTests()

#     try:
#       await test_instance.asyncSetUp()
#       print("Starting PAROBOT COMMANDS tests...")

#       await test_instance.test_change_config()
#       await test_instance.test_change_config2()
#       await test_instance.test_get_grasp_data()
#       await test_instance.test_set_grasp_data()
#       await test_instance.test_get_grip_close_pos()
#       await test_instance.test_set_grip_close_pos()
#       await test_instance.test_get_grip_open_pos()
#       await test_instance.test_set_grip_open_pos()
#       await test_instance.test_gripper()
#       await test_instance.test_move_rail()
#       await test_instance.test_move_to_safe()
#       await test_instance.test_get_pallet_index()
#       await test_instance.test_set_pallet_index()
#       await test_instance.test_get_pallet_origin()
#       await test_instance.test_set_pallet_origin()
#       await test_instance.test_get_pallet_x()
#       await test_instance.test_set_pallet_x()
#       await test_instance.test_get_pallet_y()
#       await test_instance.test_set_pallet_y()
#       await test_instance.test_get_pallet_z()
#       await test_instance.test_set_pallet_z()
#       await test_instance.test_pick_plate_station()
#       await test_instance.test_place_plate_station()
#       await test_instance.test_get_rail_position()
#       await test_instance.test_set_rail_position()
#       await test_instance.test_teach_plate_station()
#       await test_instance.test_get_station_type()
#       await test_instance.test_set_station_type()

#       print("PAROBOT COMMANDS tests completed successfully!")

#     except Exception as e:
#       print(f"Parobot commands test failed with error: {e}")
#       raise
#     finally:
#       await test_instance.asyncTearDown()

#   async def run_ssgrip_command_tests() -> None:
#     """Run all tests in the SSGRIP COMMANDS region"""
#     test_instance = PreciseFlexHardwareTests()

#     try:
#       await test_instance.asyncSetUp()
#       print("Starting SSGRIP COMMANDS tests...")

#       await test_instance.test_home_all_if_no_plate()
#       await test_instance.test_grasp_plate()
#       await test_instance.test_release_plate()
#       await test_instance.test_is_fully_closed()
#       await test_instance.test_set_active_gripper()
#       await test_instance.test_get_active_gripper()
#       await test_instance.test_free_mode()
#       await test_instance.test_open_gripper()
#       await test_instance.test_close_gripper()
#       await test_instance.test_pick_plate()
#       await test_instance.test_place_plate()
#       await test_instance.test_teach_position()

#       print("SSGRIP COMMANDS tests completed successfully!")

#     except Exception as e:
#       print(f"SSGrip commands test failed with error: {e}")
#       raise
#     finally:
#       await test_instance.asyncTearDown()

#   async def run_all_tests_by_region() -> None:
#     """Run tests organized by region for better control and debugging"""
#     try:
#       print("=== Running GENERAL COMMANDS ===")
#       await run_general_command_tests()

#       print("\n=== Running LOCATION COMMANDS ===")
#       await run_location_command_tests()

#       print("\n=== Running PROFILE COMMANDS ===")
#       await run_profile_command_tests()

#       print("\n=== Running MOTION COMMANDS ===")
#       await run_motion_command_tests()

#       print("\n=== Running PAROBOT COMMANDS ===")
#       await run_parobot_command_tests()

#       print("\n=== Running SSGRIP COMMANDS ===")
#       await run_ssgrip_command_tests()

#       print("\nAll test regions completed successfully!")

#     except Exception as e:
#       print(f"Test suite failed: {e}")


# # Main execution
# if __name__ == "__main__":
#   # Option 1: Run just general commands
#   # asyncio.run(run_general_command_tests())

#   # Option 2: Run all tests by region (recommended)
#   asyncio.run(run_all_tests_by_region())

#   # Option 3: Run specific region
#   # asyncio.run(run_location_command_tests())
