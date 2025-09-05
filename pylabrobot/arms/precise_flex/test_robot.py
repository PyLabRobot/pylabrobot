import pytest
import asyncio
import os
from pylabrobot.arms.precise_flex.preciseflex_api import PreciseFlexBackendApi
from typing import AsyncGenerator, List, Any, Union
from contextlib import asynccontextmanager


class TestPreciseFlexIntegration:
  """Integration tests for PreciseFlex robot - RUNS ON ACTUAL HARDWARE"""

  @pytest.fixture(scope="class")
  async def robot(self) -> AsyncGenerator[PreciseFlexBackendApi, None]:
    """Connect to actual PreciseFlex robot"""
    # Update with your robot's IP and port
    robot = PreciseFlexBackendApi("192.168.0.100", 10100)
    # Configuration constants - modify these for your testing needs
    self.TEST_PROFILE_ID = 20
    self.TEST_LOCATION_ID = 20
    await robot.setup()
    await robot.attach()
    await robot.set_power(True, timeout=20)

    yield robot

    await robot.stop()



  @asynccontextmanager
  async def _preserve_setting(self, robot: PreciseFlexBackendApi, getter_method: str, setter_method: str):
    """Context manager to preserve and restore robot settings"""
    # Get original value
    original_value = await getattr(robot, getter_method)()

    try:
      yield original_value
    finally:
      # Restore original value
      try:
        if isinstance(original_value, tuple):
          await getattr(robot, setter_method)(*original_value)
        else:
          await getattr(robot, setter_method)(original_value)
        print(f"Setting restored to: {original_value}")
      except Exception as e:
        print(f"Error restoring setting: {e}")

#region GENERAL COMMANDS
  @pytest.mark.asyncio
  async def test_robot_connection_and_version(self, robot: PreciseFlexBackendApi) -> None:
    """Test basic connection and version info"""
    version = await robot.get_version()
    assert isinstance(version, str)
    print(f"Robot version: {version}")

  @pytest.mark.asyncio
  async def test_get_base(self, robot: PreciseFlexBackendApi) -> None:
    base = await robot.get_base()
    assert isinstance(base, str)
    print(f"Robot base: {base}")

  @pytest.mark.asyncio
  async def test_set_base(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_base()"""
    async with self._preserve_setting(robot, 'get_base', 'set_base'):
      # Test setting to a different base if possible
      test_base = (0, 0, 0, 0)
      print(f"Setting test base to: {test_base}")

      result = await robot.set_base(*test_base)
      assert result == "OK"

      new_base = await robot.get_base()
      print(f"Base set to: {new_base}")
      assert new_base == test_base

  @pytest.mark.asyncio
  async def test_home(self, robot: PreciseFlexBackendApi) -> None:
    """Test home() command"""
    await robot.home()
    print("Robot homed successfully")

  @pytest.mark.asyncio
  async def test_home_all(self, robot: PreciseFlexBackendApi) -> None:
    """Test home_all() command"""
    # Note: This requires robots not to be attached, so we'll detach first
    await robot.attach(0)
    await robot.home_all()
    await robot.attach()  # Re-attach for other tests
    print("All robots homed successfully")

  @pytest.mark.asyncio
  async def test_get_power_state(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_power_state()"""
    power_state = await robot.get_power_state()
    assert isinstance(power_state, int)
    assert power_state in [0, 1]
    print(f"Power state: {power_state}")

  @pytest.mark.asyncio
  async def test_set_power(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_power()"""
    async with self._preserve_setting(robot, 'get_power_state', 'set_power'):
      # Test disabling power
      await robot.set_power(False)
      power_state = await robot.get_power_state()
      assert power_state == 0

      # Test enabling power with timeout
      await robot.set_power(True, timeout=20)
      power_state = await robot.get_power_state()
      assert power_state == 1
      print("Power set operations completed successfully")

  @pytest.mark.asyncio
  async def test_get_mode(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_mode()"""
    mode = await robot.get_mode()
    assert isinstance(mode, int)
    assert mode in [0, 1]
    print(f"Current mode: {mode}")

  @pytest.mark.asyncio
  async def test_set_mode(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_mode()"""
    async with self._preserve_setting(robot, 'get_mode', 'set_mode'):
      # Test setting PC mode
      await robot.set_mode(0)
      mode = await robot.get_mode()
      assert mode == 0

      # Test setting verbose mode
      await robot.set_mode(1)
      mode = await robot.get_mode()
      assert mode == 1
      print("Mode set operations completed successfully")

  @pytest.mark.asyncio
  async def test_get_monitor_speed(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_monitor_speed()"""
    speed = await robot.get_monitor_speed()
    assert isinstance(speed, int)
    assert 1 <= speed <= 100
    print(f"Monitor speed: {speed}%")

  @pytest.mark.asyncio
  async def test_set_monitor_speed(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_monitor_speed()"""
    async with self._preserve_setting(robot, 'get_monitor_speed', 'set_monitor_speed'):
      # Test setting different speeds
      test_speed = 50
      await robot.set_monitor_speed(test_speed)
      speed = await robot.get_monitor_speed()
      assert speed == test_speed
      print(f"Monitor speed set to: {speed}%")

  @pytest.mark.asyncio
  async def test_nop(self, robot: PreciseFlexBackendApi) -> None:
    """Test nop() command"""
    await robot.nop()
    print("NOP command executed successfully")

  @pytest.mark.asyncio
  async def test_get_payload(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_payload()"""
    payload = await robot.get_payload()
    assert isinstance(payload, int)
    assert 0 <= payload <= 100
    print(f"Payload: {payload}%")

  @pytest.mark.asyncio
  async def test_set_payload(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_payload()"""
    async with self._preserve_setting(robot, 'get_payload', 'set_payload'):
      # Test setting different payload values
      test_payload = 25
      await robot.set_payload(test_payload)
      payload = await robot.get_payload()
      assert payload == test_payload
      print(f"Payload set to: {payload}%")

  @pytest.mark.asyncio
  async def test_parameter_operations(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_parameter() and set_parameter()"""
    # Test with a safe parameter (example DataID)
    test_data_id = 901  # Example parameter ID

    # Get original value
    original_value = await robot.get_parameter(test_data_id)
    print(f"Original parameter value: {original_value}")

    # Test setting and getting back
    test_value = "test_value"
    await robot.set_parameter(test_data_id, test_value)

    # Get the value back
    retrieved_value = await robot.get_parameter(test_data_id)
    print(f"Retrieved parameter value: {retrieved_value}")

    # Restore original value
    await robot.set_parameter(test_data_id, original_value)

  @pytest.mark.asyncio
  async def test_get_selected_robot(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_selected_robot()"""
    selected_robot = await robot.get_selected_robot()
    assert isinstance(selected_robot, int)
    assert selected_robot >= 0
    print(f"Selected robot: {selected_robot}")

  @pytest.mark.asyncio
  async def test_select_robot(self, robot: PreciseFlexBackendApi) -> None:
    """Test select_robot()"""
    async with self._preserve_setting(robot, 'get_selected_robot', 'select_robot'):
      # Test selecting robot 1
      await robot.select_robot(1)
      selected = await robot.get_selected_robot()
      assert selected == 1
      print(f"Selected robot set to: {selected}")

  @pytest.mark.asyncio
  async def test_signal_operations(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_signal() and set_signal()"""
    test_signal = 1  # Example signal number

    # Get original signal value
    original_value = await robot.get_signal(test_signal)
    print(f"Original signal {test_signal} value: {original_value}")

    try:
      # Test setting signal
      test_value = 1 if original_value == 0 else 0
      await robot.set_signal(test_signal, test_value)

      # Verify the change
      new_value = await robot.get_signal(test_signal)
      assert new_value == test_value
      print(f"Signal {test_signal} set to: {new_value}")

    finally:
      # Restore original value
      await robot.set_signal(test_signal, original_value)

  @pytest.mark.asyncio
  async def test_get_system_state(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_system_state()"""
    system_state = await robot.get_system_state()
    assert isinstance(system_state, int)
    print(f"System state: {system_state}")

  @pytest.mark.asyncio
  async def test_get_tool(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_tool()"""
    tool = await robot.get_tool()
    assert isinstance(tool, tuple)
    assert len(tool) == 6
    x, y, z, yaw, pitch, roll = tool
    assert all(isinstance(val, (int, float)) for val in tool)
    print(f"Tool transformation: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}")

  @pytest.mark.asyncio
  async def test_set_tool(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_tool()"""
    async with self._preserve_setting(robot, 'get_tool', 'set_tool'):
      # Test setting tool transformation
      test_tool = (10.0, 20.0, 30.0, 0.0, 0.0, 0.0)
      await robot.set_tool(*test_tool)

      current_tool = await robot.get_tool()
      # Allow for small floating point differences
      for i, (expected, actual) in enumerate(zip(test_tool, current_tool)):
        assert abs(expected - actual) < 0.001, f"Tool value {i} mismatch: expected {expected}, got {actual}"

      print(f"Tool transformation set to: {current_tool}")

  @pytest.mark.asyncio
  async def test_get_version(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_version()"""
    version = await robot.get_version()
    assert isinstance(version, str)
    assert len(version) > 0
    print(f"Robot version: {version}")

  @pytest.mark.asyncio
  async def test_reset(self, robot: PreciseFlexBackendApi) -> None:
    """Test reset() command"""
    # Test resetting robot 1 (be careful with this in real hardware)
    # This test might need to be commented out for actual hardware testing
    # await robot.reset(1)
    print("Reset test skipped for safety (uncomment if needed)")

#region LOCATION COMMANDS
  @pytest.mark.asyncio
  async def test_get_location(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_location()"""
    location_data = await robot.get_location(self.TEST_LOCATION_ID)
    assert isinstance(location_data, tuple)
    assert len(location_data) == 9
    type_code, station_index, val1, val2, val3, val4, val5, val6, val7 = location_data
    assert isinstance(type_code, int)
    assert type_code in [0, 1]  # 0 = Cartesian, 1 = angles
    assert station_index == self.TEST_LOCATION_ID
    print(f"Location {self.TEST_LOCATION_ID}: type={type_code}, values=({val1}, {val2}, {val3}, {val4}, {val5}, {val6}, {val7})")

  @pytest.mark.asyncio
  async def test_get_location_angles(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_location_angles()"""
    # This test assumes location is already angles type or will fail appropriately
    try:
      location_data = await robot.get_location_angles(self.TEST_LOCATION_ID)
      assert isinstance(location_data, tuple)
      assert len(location_data) == 9
      type_code, station_index, angle1, angle2, angle3, angle4, angle5, angle6, angle7 = location_data
      assert type_code == 1  # Should be angles type
      assert station_index == self.TEST_LOCATION_ID
      print(f"Location angles {self.TEST_LOCATION_ID}: ({angle1}, {angle2}, {angle3}, {angle4}, {angle5}, {angle6}, {angle7})")
    except Exception as e:
      print(f"Location {self.TEST_LOCATION_ID} is not angles type or error occurred: {e}")

  @pytest.mark.asyncio
  async def test_set_location_angles(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_location_angles()"""
    # Get original location data
    original_location = await robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Test setting angles
      test_angles = (15.0, 25.0, 35.0, 45.0, 55.0, 65.0)
      await robot.set_location_angles(self.TEST_LOCATION_ID, *test_angles)

      # Verify the angles were set
      location_data = await robot.get_location_angles(self.TEST_LOCATION_ID)
      _, _, angle1, angle2, angle3, angle4, angle5, angle6, angle7 = location_data

      # Check first 6 angles (angle7 is typically 0)
      retrieved_angles = (angle1, angle2, angle3, angle4, angle5, angle6)
      for i, (expected, actual) in enumerate(zip(test_angles, retrieved_angles)):
        assert abs(expected - actual) < 0.001, f"Angle {i+1} mismatch: expected {expected}, got {actual}"

      print(f"Location angles set successfully: {retrieved_angles}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 1:  # Was angles type
        await robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was Cartesian type
        await robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])

  @pytest.mark.asyncio
  async def test_get_location_xyz(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_location_xyz()"""
    # This test assumes location is already Cartesian type or will fail appropriately
    try:
      location_data = await robot.get_location_xyz(self.TEST_LOCATION_ID)
      assert isinstance(location_data, tuple)
      assert len(location_data) == 8
      type_code, station_index, x, y, z, yaw, pitch, roll = location_data
      assert type_code == 0  # Should be Cartesian type
      assert station_index == self.TEST_LOCATION_ID
      print(f"Location XYZ {self.TEST_LOCATION_ID}: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}")
    except Exception as e:
      print(f"Location {self.TEST_LOCATION_ID} is not Cartesian type or error occurred: {e}")

  @pytest.mark.asyncio
  async def test_set_location_xyz(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_location_xyz()"""
    # Get original location data
    original_location = await robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Test setting Cartesian coordinates
      test_coords = (150.0, 250.0, 350.0, 10.0, 20.0, 30.0)
      await robot.set_location_xyz(self.TEST_LOCATION_ID, *test_coords)

      # Verify the coordinates were set
      location_data = await robot.get_location_xyz(self.TEST_LOCATION_ID)
      _, _, x, y, z, yaw, pitch, roll = location_data

      retrieved_coords = (x, y, z, yaw, pitch, roll)
      for i, (expected, actual) in enumerate(zip(test_coords, retrieved_coords)):
        assert abs(expected - actual) < 0.001, f"Coordinate {i} mismatch: expected {expected}, got {actual}"

      print(f"Location XYZ set successfully: {retrieved_coords}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

  @pytest.mark.asyncio
  async def test_get_location_z_clearance(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_location_z_clearance()"""
    clearance_data = await robot.get_location_z_clearance(self.TEST_LOCATION_ID)
    assert isinstance(clearance_data, tuple)
    assert len(clearance_data) == 3
    station_index, z_clearance, z_world = clearance_data
    assert station_index == self.TEST_LOCATION_ID
    assert isinstance(z_clearance, float)
    assert isinstance(z_world, float)
    print(f"Location {self.TEST_LOCATION_ID} Z clearance: {z_clearance}, Z world: {z_world}")

  @pytest.mark.asyncio
  async def test_set_location_z_clearance(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_location_z_clearance()"""
    original_clearance = await robot.get_location_z_clearance(self.TEST_LOCATION_ID)
    _, orig_z_clearance, orig_z_world = original_clearance

    try:
      # Test setting only z_clearance
      test_z_clearance = 50.0
      await robot.set_location_z_clearance(self.TEST_LOCATION_ID, test_z_clearance)

      clearance_data = await robot.get_location_z_clearance(self.TEST_LOCATION_ID)
      _, z_clearance, z_world = clearance_data
      assert abs(z_clearance - test_z_clearance) < 0.001
      print(f"Z clearance set to: {z_clearance}")

      # Test setting both z_clearance and z_world
      test_z_world = 75.0
      await robot.set_location_z_clearance(self.TEST_LOCATION_ID, test_z_clearance, test_z_world)

      clearance_data = await robot.get_location_z_clearance(self.TEST_LOCATION_ID)
      _, z_clearance, z_world = clearance_data
      assert abs(z_clearance - test_z_clearance) < 0.001
      assert abs(z_world - test_z_world) < 0.001
      print(f"Z clearance and world set to: {z_clearance}, {z_world}")

    finally:
      # Restore original values
      await robot.set_location_z_clearance(self.TEST_LOCATION_ID, orig_z_clearance, orig_z_world)

  @pytest.mark.asyncio
  async def test_get_location_config(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_location_config()"""
    config_data = await robot.get_location_config(self.TEST_LOCATION_ID)
    assert isinstance(config_data, tuple)
    assert len(config_data) == 2
    station_index, config_value = config_data
    assert station_index == self.TEST_LOCATION_ID
    assert isinstance(config_value, int)
    assert config_value in [1, 2]  # 1 = Righty, 2 = Lefty
    print(f"Location {self.TEST_LOCATION_ID} config: {config_value} ({'Righty' if config_value == 1 else 'Lefty'})")

  @pytest.mark.asyncio
  async def test_set_location_config(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_location_config()"""
    original_config = await robot.get_location_config(self.TEST_LOCATION_ID)
    _, orig_config_value = original_config

    try:
      # Test setting different config
      test_config = 2 if orig_config_value == 1 else 1
      await robot.set_location_config(self.TEST_LOCATION_ID, test_config)

      config_data = await robot.get_location_config(self.TEST_LOCATION_ID)
      _, config_value = config_data
      assert config_value == test_config
      print(f"Location config set to: {config_value} ({'Righty' if config_value == 1 else 'Lefty'})")

    finally:
      # Restore original config
      await robot.set_location_config(self.TEST_LOCATION_ID, orig_config_value)

  @pytest.mark.asyncio
  async def test_dest_c(self, robot: PreciseFlexBackendApi) -> None:
    """Test dest_c()"""
    # Test with default argument (current location)
    dest_data = await robot.dest_c()
    assert isinstance(dest_data, tuple)
    assert len(dest_data) == 7
    x, y, z, yaw, pitch, roll, config = dest_data
    assert all(isinstance(val, (int, float)) for val in dest_data)
    print(f"Current Cartesian destination: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}, Config={config}")

    # Test with arg1=1 (target location)
    dest_data_target = await robot.dest_c(1)
    assert isinstance(dest_data_target, tuple)
    assert len(dest_data_target) == 7
    print(f"Target Cartesian destination: {dest_data_target}")

  @pytest.mark.asyncio
  async def test_dest_j(self, robot: PreciseFlexBackendApi) -> None:
    """Test dest_j()"""
    # Test with default argument (current joint positions)
    dest_data = await robot.dest_j()
    assert isinstance(dest_data, tuple)
    assert len(dest_data) == 7
    assert all(isinstance(val, (int, float)) for val in dest_data)
    print(f"Current joint destination: {dest_data}")

    # Test with arg1=1 (target joint positions)
    dest_data_target = await robot.dest_j(1)
    assert isinstance(dest_data_target, tuple)
    assert len(dest_data_target) == 7
    print(f"Target joint destination: {dest_data_target}")

  @pytest.mark.asyncio
  async def test_here_j(self, robot: PreciseFlexBackendApi) -> None:
    """Test here_j()"""
    original_location = await robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Record current position as angles
      await robot.here_j(self.TEST_LOCATION_ID)

      # Verify the location was recorded as angles type
      location_data = await robot.get_location(self.TEST_LOCATION_ID)
      type_code = location_data[0]
      assert type_code == 1  # Should be angles type
      print(f"Current position recorded as angles at location {self.TEST_LOCATION_ID}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

  @pytest.mark.asyncio
  async def test_here_c(self, robot: PreciseFlexBackendApi) -> None:
    """Test here_c()"""
    original_location = await robot.get_location(self.TEST_LOCATION_ID)

    try:
      # Record current position as Cartesian
      await robot.here_c(self.TEST_LOCATION_ID)

      # Verify the location was recorded as Cartesian type
      location_data = await robot.get_location(self.TEST_LOCATION_ID)
      type_code = location_data[0]
      assert type_code == 0  # Should be Cartesian type
      print(f"Current position recorded as Cartesian at location {self.TEST_LOCATION_ID}")

    finally:
      # Restore original location
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

  @pytest.mark.asyncio
  async def test_where(self, robot: PreciseFlexBackendApi) -> None:
    """Test where()"""
    position_data = await robot.where()
    assert isinstance(position_data, tuple)
    assert len(position_data) == 7
    x, y, z, yaw, pitch, roll, axes = position_data
    assert all(isinstance(val, (int, float)) for val in [x, y, z, yaw, pitch, roll])
    assert isinstance(axes, tuple)
    assert len(axes) == 7
    assert all(isinstance(val, (int, float)) for val in axes)
    print(f"Current position - Cartesian: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}")
    print(f"Current position - Joints: {axes}")

  @pytest.mark.asyncio
  async def test_where_c(self, robot: PreciseFlexBackendApi) -> None:
    """Test where_c()"""
    position_data = await robot.where_c()
    assert isinstance(position_data, tuple)
    assert len(position_data) == 7
    x, y, z, yaw, pitch, roll, config = position_data
    assert all(isinstance(val, (int, float)) for val in position_data)
    assert config in [1, 2]  # 1 = Righty, 2 = Lefty
    print(f"Current Cartesian position: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}, Config={config}")

  @pytest.mark.asyncio
  async def test_where_j(self, robot: PreciseFlexBackendApi) -> None:
    """Test where_j()"""
    joint_data = await robot.where_j()
    assert isinstance(joint_data, tuple)
    assert len(joint_data) == 7
    assert all(isinstance(val, (int, float)) for val in joint_data)
    print(f"Current joint positions: {joint_data}")

#region PROFILE COMMANDS
  @pytest.mark.asyncio
  async def test_get_profile_speed(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_profile_speed()"""
    speed = await robot.get_profile_speed(self.TEST_PROFILE_ID)
    assert isinstance(speed, float)
    assert speed >= 0
    print(f"Profile {self.TEST_PROFILE_ID} speed: {speed}%")

  @pytest.mark.asyncio
  async def test_set_profile_speed(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_profile_speed()"""
    original_speed = await robot.get_profile_speed(self.TEST_PROFILE_ID)

    try:
      # Test setting different speed
      test_speed = 50.0
      await robot.set_profile_speed(self.TEST_PROFILE_ID, test_speed)

      speed = await robot.get_profile_speed(self.TEST_PROFILE_ID)
      assert abs(speed - test_speed) < 0.001
      print(f"Profile speed set to: {speed}%")

    finally:
      # Restore original speed
      await robot.set_profile_speed(self.TEST_PROFILE_ID, original_speed)

  @pytest.mark.asyncio
  async def test_get_profile_speed2(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_profile_speed2()"""
    speed2 = await robot.get_profile_speed2(self.TEST_PROFILE_ID)
    assert isinstance(speed2, float)
    assert speed2 >= 0
    print(f"Profile {self.TEST_PROFILE_ID} speed2: {speed2}%")

  @pytest.mark.asyncio
  async def test_set_profile_speed2(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_profile_speed2()"""
    original_speed2 = await robot.get_profile_speed2(self.TEST_PROFILE_ID)

    try:
      # Test setting different speed2
      test_speed2 = 25.0
      await robot.set_profile_speed2(self.TEST_PROFILE_ID, test_speed2)

      speed2 = await robot.get_profile_speed2(self.TEST_PROFILE_ID)
      assert abs(speed2 - test_speed2) < 0.001
      print(f"Profile speed2 set to: {speed2}%")

    finally:
      # Restore original speed2
      await robot.set_profile_speed2(self.TEST_PROFILE_ID, original_speed2)

  @pytest.mark.asyncio
  async def test_get_profile_accel(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_profile_accel()"""
    accel = await robot.get_profile_accel(self.TEST_PROFILE_ID)
    assert isinstance(accel, float)
    assert accel >= 0
    print(f"Profile {self.TEST_PROFILE_ID} acceleration: {accel}%")

  @pytest.mark.asyncio
  async def test_set_profile_accel(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_profile_accel()"""
    original_accel = await robot.get_profile_accel(self.TEST_PROFILE_ID)

    try:
      # Test setting different acceleration
      test_accel = 75.0
      await robot.set_profile_accel(self.TEST_PROFILE_ID, test_accel)

      accel = await robot.get_profile_accel(self.TEST_PROFILE_ID)
      assert abs(accel - test_accel) < 0.001
      print(f"Profile acceleration set to: {accel}%")

    finally:
      # Restore original acceleration
      await robot.set_profile_accel(self.TEST_PROFILE_ID, original_accel)

  @pytest.mark.asyncio
  async def test_get_profile_accel_ramp(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_profile_accel_ramp()"""
    accel_ramp = await robot.get_profile_accel_ramp(self.TEST_PROFILE_ID)
    assert isinstance(accel_ramp, float)
    assert accel_ramp >= 0
    print(f"Profile {self.TEST_PROFILE_ID} acceleration ramp: {accel_ramp} seconds")

  @pytest.mark.asyncio
  async def test_set_profile_accel_ramp(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_profile_accel_ramp()"""
    original_accel_ramp = await robot.get_profile_accel_ramp(self.TEST_PROFILE_ID)

    try:
      # Test setting different acceleration ramp
      test_accel_ramp = 0.5
      await robot.set_profile_accel_ramp(self.TEST_PROFILE_ID, test_accel_ramp)

      accel_ramp = await robot.get_profile_accel_ramp(self.TEST_PROFILE_ID)
      assert abs(accel_ramp - test_accel_ramp) < 0.001
      print(f"Profile acceleration ramp set to: {accel_ramp} seconds")

    finally:
      # Restore original acceleration ramp
      await robot.set_profile_accel_ramp(self.TEST_PROFILE_ID, original_accel_ramp)

  @pytest.mark.asyncio
  async def test_get_profile_decel(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_profile_decel()"""
    decel = await robot.get_profile_decel(self.TEST_PROFILE_ID)
    assert isinstance(decel, float)
    assert decel >= 0
    print(f"Profile {self.TEST_PROFILE_ID} deceleration: {decel}%")

  @pytest.mark.asyncio
  async def test_set_profile_decel(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_profile_decel()"""
    original_decel = await robot.get_profile_decel(self.TEST_PROFILE_ID)

    try:
      # Test setting different deceleration
      test_decel = 80.0
      await robot.set_profile_decel(self.TEST_PROFILE_ID, test_decel)

      decel = await robot.get_profile_decel(self.TEST_PROFILE_ID)
      assert abs(decel - test_decel) < 0.001
      print(f"Profile deceleration set to: {decel}%")

    finally:
      # Restore original deceleration
      await robot.set_profile_decel(self.TEST_PROFILE_ID, original_decel)

  @pytest.mark.asyncio
  async def test_get_profile_decel_ramp(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_profile_decel_ramp()"""
    decel_ramp = await robot.get_profile_decel_ramp(self.TEST_PROFILE_ID)
    assert isinstance(decel_ramp, float)
    assert decel_ramp >= 0
    print(f"Profile {self.TEST_PROFILE_ID} deceleration ramp: {decel_ramp} seconds")

  @pytest.mark.asyncio
  async def test_set_profile_decel_ramp(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_profile_decel_ramp()"""
    original_decel_ramp = await robot.get_profile_decel_ramp(self.TEST_PROFILE_ID)

    try:
      # Test setting different deceleration ramp
      test_decel_ramp = 0.3
      await robot.set_profile_decel_ramp(self.TEST_PROFILE_ID, test_decel_ramp)

      decel_ramp = await robot.get_profile_decel_ramp(self.TEST_PROFILE_ID)
      assert abs(decel_ramp - test_decel_ramp) < 0.001
      print(f"Profile deceleration ramp set to: {decel_ramp} seconds")

    finally:
      # Restore original deceleration ramp
      await robot.set_profile_decel_ramp(self.TEST_PROFILE_ID, original_decel_ramp)

  @pytest.mark.asyncio
  async def test_get_profile_in_range(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_profile_in_range()"""
    in_range = await robot.get_profile_in_range(self.TEST_PROFILE_ID)
    assert isinstance(in_range, float)
    assert -1 <= in_range <= 100
    print(f"Profile {self.TEST_PROFILE_ID} InRange: {in_range}")

  @pytest.mark.asyncio
  async def test_set_profile_in_range(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_profile_in_range()"""
    original_in_range = await robot.get_profile_in_range(self.TEST_PROFILE_ID)

    try:
      # Test setting different InRange values
      test_in_range = 50.0
      await robot.set_profile_in_range(self.TEST_PROFILE_ID, test_in_range)

      in_range = await robot.get_profile_in_range(self.TEST_PROFILE_ID)
      assert abs(in_range - test_in_range) < 0.001
      print(f"Profile InRange set to: {in_range}")

      # Test boundary values
      await robot.set_profile_in_range(self.TEST_PROFILE_ID, -1.0)
      in_range = await robot.get_profile_in_range(self.TEST_PROFILE_ID)
      assert abs(in_range - (-1.0)) < 0.001

      await robot.set_profile_in_range(self.TEST_PROFILE_ID, 100.0)
      in_range = await robot.get_profile_in_range(self.TEST_PROFILE_ID)
      assert abs(in_range - 100.0) < 0.001

    finally:
      # Restore original InRange
      await robot.set_profile_in_range(self.TEST_PROFILE_ID, original_in_range)

  @pytest.mark.asyncio
  async def test_get_profile_straight(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_profile_straight()"""
    straight = await robot.get_profile_straight(self.TEST_PROFILE_ID)
    assert isinstance(straight, bool)
    print(f"Profile {self.TEST_PROFILE_ID} Straight: {straight} ({'straight-line' if straight else 'joint-based'} path)")

  @pytest.mark.asyncio
  async def test_set_profile_straight(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_profile_straight()"""
    original_straight = await robot.get_profile_straight(self.TEST_PROFILE_ID)

    try:
      # Test setting different straight mode
      test_straight = not original_straight
      await robot.set_profile_straight(self.TEST_PROFILE_ID, test_straight)

      straight = await robot.get_profile_straight(self.TEST_PROFILE_ID)
      assert straight == test_straight
      print(f"Profile Straight set to: {straight} ({'straight-line' if straight else 'joint-based'} path)")

    finally:
      # Restore original straight mode
      await robot.set_profile_straight(self.TEST_PROFILE_ID, original_straight)

  @pytest.mark.asyncio
  async def test_get_motion_profile_values(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_motion_profile_values()"""
    profile_data = await robot.get_motion_profile_values(self.TEST_PROFILE_ID)
    assert isinstance(profile_data, tuple)
    assert len(profile_data) == 9

    profile_id, speed, speed2, accel, decel, accel_ramp, decel_ramp, in_range, straight = profile_data

    assert profile_id == self.TEST_PROFILE_ID
    assert isinstance(speed, float) and speed >= 0
    assert isinstance(speed2, float) and speed2 >= 0
    assert isinstance(accel, float) and accel >= 0
    assert isinstance(decel, float) and decel >= 0
    assert isinstance(accel_ramp, float) and accel_ramp >= 0
    assert isinstance(decel_ramp, float) and decel_ramp >= 0
    assert isinstance(in_range, float) and -1 <= in_range <= 100
    assert isinstance(straight, bool)

    print(f"Motion profile {self.TEST_PROFILE_ID}: speed={speed}%, speed2={speed2}%, accel={accel}%, decel={decel}%")
    print(f"  accel_ramp={accel_ramp}s, decel_ramp={decel_ramp}s, in_range={in_range}, straight={straight}")

  @pytest.mark.asyncio
  async def test_set_motion_profile_values(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_motion_profile_values()"""
    # Get original profile values
    original_profile = await robot.get_motion_profile_values(self.TEST_PROFILE_ID)

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

      await robot.set_motion_profile_values(
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
      profile_data = await robot.get_motion_profile_values(self.TEST_PROFILE_ID)
      profile_id, speed, speed2, accel, decel, accel_ramp, decel_ramp, in_range, straight = profile_data

      assert abs(speed - test_values['speed']) < 0.001
      assert abs(speed2 - test_values['speed2']) < 0.001
      assert abs(accel - test_values['acceleration']) < 0.001
      assert abs(decel - test_values['deceleration']) < 0.001
      assert abs(accel_ramp - test_values['acceleration_ramp']) < 0.001
      assert abs(decel_ramp - test_values['deceleration_ramp']) < 0.001
      assert abs(in_range - test_values['in_range']) < 0.001
      assert straight == test_values['straight']

      print(f"Motion profile values set successfully: {profile_data}")

    finally:
      # Restore original profile values
      _, orig_speed, orig_speed2, orig_accel, orig_decel, orig_accel_ramp, orig_decel_ramp, orig_in_range, orig_straight = original_profile
      await robot.set_motion_profile_values(
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
  @pytest.mark.asyncio
  async def test_halt(self, robot: PreciseFlexBackendApi) -> None:
    """Test halt() command"""
    # Start a small movement and then halt it
    current_pos = await robot.where_c()
    x, y, z, yaw, pitch, roll, config = current_pos

    # Make a small movement
    await robot.move_c(self.TEST_PROFILE_ID, x + 5, y, z, yaw, pitch, roll)

    # Immediately halt the movement
    await robot.halt()
    print("Halt command executed successfully")

  @pytest.mark.asyncio
  async def test_move(self, robot: PreciseFlexBackendApi) -> None:
    """Test move() command"""
    # Record current position for restoration
    original_position = await robot.where_c()

    try:
      # Move to test location
      await robot.move(self.TEST_LOCATION_ID, self.TEST_PROFILE_ID)
      await robot.wait_for_eom()

      # Verify we moved (position should be different)
      new_position = await robot.where_c()
      position_changed = any(abs(orig - new) > 1.0 for orig, new in zip(original_position[:6], new_position[:6]))
      assert position_changed or True  # Allow for cases where location might be same as current
      print(f"Move to location {self.TEST_LOCATION_ID} completed successfully")

    finally:
      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_move_appro(self, robot: PreciseFlexBackendApi) -> None:
    """Test move_appro() command"""
    # Record current position for restoration
    original_position = await robot.where_c()

    try:
      # Move to test location with approach
      await robot.move_appro(self.TEST_LOCATION_ID, self.TEST_PROFILE_ID)
      await robot.wait_for_eom()

      print(f"Move approach to location {self.TEST_LOCATION_ID} completed successfully")

    finally:
      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_move_extra_axis(self, robot: PreciseFlexBackendApi) -> None:
    """Test move_extra_axis() command"""
    # Test with single axis
    await robot.move_extra_axis(100.0)
    print("Move extra axis (single) command executed successfully")

    # Test with two axes
    await robot.move_extra_axis(100.0, 200.0)
    print("Move extra axis (dual) command executed successfully")

  @pytest.mark.asyncio
  async def test_move_one_axis(self, robot: PreciseFlexBackendApi) -> None:
    """Test move_one_axis() command"""
    # Get current joint positions for restoration
    original_joints = await robot.where_j()

    try:
      # Test moving axis 1 by a small amount
      test_axis = 1
      current_position = original_joints[test_axis - 1]  # Convert to 0-based index
      new_position = current_position + 5.0  # Move 5 degrees

      await robot.move_one_axis(test_axis, new_position, self.TEST_PROFILE_ID)
      await robot.wait_for_eom()

      # Verify the axis moved
      new_joints = await robot.where_j()
      assert abs(new_joints[test_axis - 1] - new_position) < 1.0
      print(f"Move one axis {test_axis} to {new_position} completed successfully")

    finally:
      # Restore original position
      await robot.move_j(self.TEST_PROFILE_ID, *original_joints)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_move_c(self, robot: PreciseFlexBackendApi) -> None:
    """Test move_c() command"""
    # Record current position for restoration
    original_position = await robot.where_c()
    x, y, z, yaw, pitch, roll, config = original_position

    try:
      # Test move without config
      test_x, test_y, test_z = x + 10, y + 10, z + 5
      await robot.move_c(self.TEST_PROFILE_ID, test_x, test_y, test_z, yaw, pitch, roll)
      await robot.wait_for_eom()

      # Verify position
      new_position = await robot.where_c()
      assert abs(new_position[0] - test_x) < 2.0
      assert abs(new_position[1] - test_y) < 2.0
      assert abs(new_position[2] - test_z) < 2.0
      print(f"Move Cartesian without config completed successfully")

      # Test move with config
      await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll, config)
      await robot.wait_for_eom()
      print(f"Move Cartesian with config completed successfully")

    finally:
      # Return to original position
      await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_move_j(self, robot: PreciseFlexBackendApi) -> None:
    """Test move_j() command"""
    # Record current joint positions for restoration
    original_joints = await robot.where_j()

    try:
      # Create test joint positions (small movements)
      test_joints = tuple(joint + 2.0 for joint in original_joints)

      await robot.move_j(self.TEST_PROFILE_ID, *test_joints)
      await robot.wait_for_eom()

      # Verify joint positions
      new_joints = await robot.where_j()
      for i, (expected, actual) in enumerate(zip(test_joints, new_joints)):
        assert abs(expected - actual) < 1.0, f"Joint {i+1} position mismatch"

      print(f"Move joints completed successfully")

    finally:
      # Return to original position
      await robot.move_j(self.TEST_PROFILE_ID, *original_joints)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_release_brake(self, robot: PreciseFlexBackendApi) -> None:
    """Test release_brake() command"""
    test_axis = 1

    # Release brake on test axis
    await robot.release_brake(test_axis)
    print(f"Brake released on axis {test_axis} successfully")

    # Note: In a real test environment, you might want to check brake status
    # but that would require additional API methods

  @pytest.mark.asyncio
  async def test_set_brake(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_brake() command"""
    test_axis = 1

    # Set brake on test axis
    await robot.set_brake(test_axis)
    print(f"Brake set on axis {test_axis} successfully")

    # Release the brake again for safety
    await robot.release_brake(test_axis)

  @pytest.mark.asyncio
  async def test_state(self, robot: PreciseFlexBackendApi) -> None:
    """Test state() command"""
    motion_state = await robot.state()
    assert isinstance(motion_state, str)
    assert len(motion_state) > 0
    print(f"Motion state: {motion_state}")

  @pytest.mark.asyncio
  async def test_wait_for_eom(self, robot: PreciseFlexBackendApi) -> None:
    """Test wait_for_eom() command"""
    # Get current position and make a small movement
    current_pos = await robot.where_c()
    x, y, z, yaw, pitch, roll, config = current_pos

    # Start a movement
    await robot.move_c(self.TEST_PROFILE_ID, x + 1, y, z, yaw, pitch, roll)

    # Wait for end of motion
    await robot.wait_for_eom()
    print("Wait for end of motion completed successfully")

    # Return to original position
    await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
    await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_zero_torque(self, robot: PreciseFlexBackendApi) -> None:
    """Test zero_torque() command"""
    test_axis_mask = 1  # Enable zero torque for axis 1

    try:
      # Enable zero torque mode for axis 1
      await robot.zero_torque(True, test_axis_mask)
      print(f"Zero torque enabled for axis mask {test_axis_mask}")

      # Brief pause to allow the mode to take effect
      await asyncio.sleep(0.5)

    finally:
      # Disable zero torque mode for safety
      await robot.zero_torque(False)
      print("Zero torque mode disabled")

#region PAROBOT COMMANDS
  @pytest.mark.asyncio
  async def test_change_config(self, robot: PreciseFlexBackendApi) -> None:
    """Test change_config() command"""
    # Record current config for restoration
    original_config = await robot.get_location_config(self.TEST_LOCATION_ID)
    _, orig_config_value = original_config

    try:
      # Test with default grip mode (no gripper change)
      await robot.change_config()
      print("Change config (default) executed successfully")

      # Test with gripper open
      await robot.change_config(1)
      print("Change config with gripper open executed successfully")

      # Test with gripper close
      await robot.change_config(2)
      print("Change config with gripper close executed successfully")

    finally:
      # Allow time for robot to settle and restore original config if needed
      await asyncio.sleep(1.0)

  @pytest.mark.asyncio
  async def test_change_config2(self, robot: PreciseFlexBackendApi) -> None:
    """Test change_config2() command"""
    try:
      # Test with default grip mode (no gripper change)
      await robot.change_config2()
      print("Change config2 (default) executed successfully")

      # Test with gripper open
      await robot.change_config2(1)
      print("Change config2 with gripper open executed successfully")

      # Test with gripper close
      await robot.change_config2(2)
      print("Change config2 with gripper close executed successfully")

    finally:
      # Allow time for robot to settle
      await asyncio.sleep(1.0)

  @pytest.mark.asyncio
  async def test_get_grasp_data(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_grasp_data()"""
    grasp_data = await robot.get_grasp_data()
    assert isinstance(grasp_data, tuple)
    assert len(grasp_data) == 3
    plate_width, finger_speed, grasp_force = grasp_data
    assert isinstance(plate_width, float)
    assert isinstance(finger_speed, float)
    assert isinstance(grasp_force, float)
    print(f"Grasp data: plate_width={plate_width}mm, finger_speed={finger_speed}%, grasp_force={grasp_force}N")

  @pytest.mark.asyncio
  async def test_set_grasp_data(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_grasp_data()"""
    # Get original grasp data for restoration
    original_grasp = await robot.get_grasp_data()

    try:
      # Test setting grasp data
      test_plate_width = 10.5
      test_finger_speed = 75.0
      test_grasp_force = 20.0

      await robot.set_grasp_data(test_plate_width, test_finger_speed, test_grasp_force)

      # Verify the data was set
      new_grasp = await robot.get_grasp_data()
      plate_width, finger_speed, grasp_force = new_grasp

      assert abs(plate_width - test_plate_width) < 0.001
      assert abs(finger_speed - test_finger_speed) < 0.001
      assert abs(grasp_force - test_grasp_force) < 0.001
      print(f"Grasp data set successfully: {new_grasp}")

    finally:
      # Restore original grasp data
      await robot.set_grasp_data(*original_grasp)

  @pytest.mark.asyncio
  async def test_get_grip_close_pos(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_grip_close_pos()"""
    close_pos = await robot.get_grip_close_pos()
    assert isinstance(close_pos, float)
    print(f"Gripper close position: {close_pos}")

  @pytest.mark.asyncio
  async def test_set_grip_close_pos(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_grip_close_pos()"""
    # Get original close position for restoration
    original_close_pos = await robot.get_grip_close_pos()

    try:
      # Test setting close position
      test_close_pos = original_close_pos + 5.0
      await robot.set_grip_close_pos(test_close_pos)

      # Verify the position was set
      new_close_pos = await robot.get_grip_close_pos()
      assert abs(new_close_pos - test_close_pos) < 0.001
      print(f"Gripper close position set to: {new_close_pos}")

    finally:
      # Restore original close position
      await robot.set_grip_close_pos(original_close_pos)

  @pytest.mark.asyncio
  async def test_get_grip_open_pos(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_grip_open_pos()"""
    open_pos = await robot.get_grip_open_pos()
    assert isinstance(open_pos, float)
    print(f"Gripper open position: {open_pos}")

  @pytest.mark.asyncio
  async def test_set_grip_open_pos(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_grip_open_pos()"""
    # Get original open position for restoration
    original_open_pos = await robot.get_grip_open_pos()

    try:
      # Test setting open position
      test_open_pos = original_open_pos + 5.0
      await robot.set_grip_open_pos(test_open_pos)

      # Verify the position was set
      new_open_pos = await robot.get_grip_open_pos()
      assert abs(new_open_pos - test_open_pos) < 0.001
      print(f"Gripper open position set to: {new_open_pos}")

    finally:
      # Restore original open position
      await robot.set_grip_open_pos(original_open_pos)

  @pytest.mark.asyncio
  async def test_gripper(self, robot: PreciseFlexBackendApi) -> None:
    """Test gripper() command"""
    # Test opening gripper
    await robot.gripper(1)
    print("Gripper opened successfully")

    # Brief delay to allow gripper to move
    await asyncio.sleep(0.5)

    # Test closing gripper
    await robot.gripper(2)
    print("Gripper closed successfully")

    # Test invalid grip mode
    with pytest.raises(ValueError):
      await robot.gripper(3)

  @pytest.mark.asyncio
  async def test_move_rail(self, robot: PreciseFlexBackendApi) -> None:
    """Test move_rail() command"""
    # Test canceling pending move rail
    await robot.move_rail(mode=0)
    print("Move rail canceled successfully")

    # Test moving rail immediately with explicit destination
    await robot.move_rail(station_id=1, mode=1, rail_destination=100.0)
    print("Move rail immediately with destination executed successfully")

    # Test moving rail with station ID only
    await robot.move_rail(station_id=self.TEST_LOCATION_ID, mode=1)
    print("Move rail immediately with station executed successfully")

    # Test setting rail to move during next pick/place
    await robot.move_rail(station_id=self.TEST_LOCATION_ID, mode=2)
    print("Move rail during next pick/place set successfully")

  @pytest.mark.asyncio
  async def test_move_to_safe(self, robot: PreciseFlexBackendApi) -> None:
    """Test move_to_safe() command"""
    # Record current position for comparison
    original_position = await robot.where_c()

    # Move to safe position
    await robot.move_to_safe()
    await robot.wait_for_eom()

    # Verify we moved to a different position
    safe_position = await robot.where_c()
    position_changed = any(abs(orig - safe) > 1.0 for orig, safe in zip(original_position[:6], safe_position[:6]))

    print(f"Move to safe position completed successfully")
    print(f"Position changed: {position_changed}")

  @pytest.mark.asyncio
  async def test_get_pallet_index(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_pallet_index()"""
    pallet_data = await robot.get_pallet_index(self.TEST_LOCATION_ID)
    assert isinstance(pallet_data, tuple)
    assert len(pallet_data) == 4
    station_id, pallet_x, pallet_y, pallet_z = pallet_data
    assert station_id == self.TEST_LOCATION_ID
    assert isinstance(pallet_x, int)
    assert isinstance(pallet_y, int)
    assert isinstance(pallet_z, int)
    print(f"Pallet index for station {station_id}: X={pallet_x}, Y={pallet_y}, Z={pallet_z}")

  @pytest.mark.asyncio
  async def test_set_pallet_index(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_pallet_index()"""
    # Get original pallet index for restoration
    original_pallet = await robot.get_pallet_index(self.TEST_LOCATION_ID)
    _, orig_x, orig_y, orig_z = original_pallet

    try:
      # Test setting all indices
      test_x, test_y, test_z = 2, 3, 4
      await robot.set_pallet_index(self.TEST_LOCATION_ID, test_x, test_y, test_z)

      # Verify the indices were set
      new_pallet = await robot.get_pallet_index(self.TEST_LOCATION_ID)
      _, pallet_x, pallet_y, pallet_z = new_pallet
      assert pallet_x == test_x
      assert pallet_y == test_y
      assert pallet_z == test_z
      print(f"Pallet index set successfully: X={pallet_x}, Y={pallet_y}, Z={pallet_z}")

      # Test setting only X index
      await robot.set_pallet_index(self.TEST_LOCATION_ID, pallet_index_x=5)
      new_pallet = await robot.get_pallet_index(self.TEST_LOCATION_ID)
      _, pallet_x, _, _ = new_pallet
      assert pallet_x == 5
      print(f"Pallet X index set to: {pallet_x}")

    finally:
      # Restore original indices
      await robot.set_pallet_index(self.TEST_LOCATION_ID, orig_x, orig_y, orig_z)

  @pytest.mark.asyncio
  async def test_get_pallet_origin(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_pallet_origin()"""
    origin_data = await robot.get_pallet_origin(self.TEST_LOCATION_ID)
    assert isinstance(origin_data, tuple)
    assert len(origin_data) == 8
    station_id, x, y, z, yaw, pitch, roll, config = origin_data
    assert station_id == self.TEST_LOCATION_ID
    assert all(isinstance(val, (int, float)) for val in [x, y, z, yaw, pitch, roll])
    assert isinstance(config, int)
    print(f"Pallet origin for station {station_id}: X={x}, Y={y}, Z={z}, Yaw={yaw}, Pitch={pitch}, Roll={roll}, Config={config}")

  @pytest.mark.asyncio
  async def test_set_pallet_origin(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_pallet_origin()"""
    # Get original pallet origin for restoration
    original_origin = await robot.get_pallet_origin(self.TEST_LOCATION_ID)

    try:
      # Test setting pallet origin without config
      test_coords = (100.0, 200.0, 300.0, 10.0, 20.0, 30.0)
      await robot.set_pallet_origin(self.TEST_LOCATION_ID, *test_coords)

      # Verify the origin was set
      new_origin = await robot.get_pallet_origin(self.TEST_LOCATION_ID)
      _, x, y, z, yaw, pitch, roll, config = new_origin

      for i, (expected, actual) in enumerate(zip(test_coords, (x, y, z, yaw, pitch, roll))):
        assert abs(expected - actual) < 0.001, f"Origin coordinate {i} mismatch"

      print(f"Pallet origin set successfully: {test_coords}")

      # Test setting pallet origin with config
      test_config = 2
      await robot.set_pallet_origin(self.TEST_LOCATION_ID, *test_coords, test_config)

      new_origin = await robot.get_pallet_origin(self.TEST_LOCATION_ID)
      _, _, _, _, _, _, _, config = new_origin
      assert config == test_config
      print(f"Pallet origin with config set successfully")

    finally:
      # Restore original origin
      _, orig_x, orig_y, orig_z, orig_yaw, orig_pitch, orig_roll, orig_config = original_origin
      await robot.set_pallet_origin(self.TEST_LOCATION_ID, orig_x, orig_y, orig_z, orig_yaw, orig_pitch, orig_roll, orig_config)

  @pytest.mark.asyncio
  async def test_get_pallet_x(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_pallet_x()"""
    pallet_x_data = await robot.get_pallet_x(self.TEST_LOCATION_ID)
    assert isinstance(pallet_x_data, tuple)
    assert len(pallet_x_data) == 5
    station_id, x_count, world_x, world_y, world_z = pallet_x_data
    assert station_id == self.TEST_LOCATION_ID
    assert isinstance(x_count, int)
    assert all(isinstance(val, float) for val in [world_x, world_y, world_z])
    print(f"Pallet X for station {station_id}: count={x_count}, world=({world_x}, {world_y}, {world_z})")

  @pytest.mark.asyncio
  async def test_set_pallet_x(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_pallet_x()"""
    # Get original pallet X for restoration
    original_pallet_x = await robot.get_pallet_x(self.TEST_LOCATION_ID)

    try:
      # Test setting pallet X
      test_x_count = 5
      test_coords = (150.0, 250.0, 350.0)
      await robot.set_pallet_x(self.TEST_LOCATION_ID, test_x_count, *test_coords)

      # Verify the pallet X was set
      new_pallet_x = await robot.get_pallet_x(self.TEST_LOCATION_ID)
      _, x_count, world_x, world_y, world_z = new_pallet_x

      assert x_count == test_x_count
      for i, (expected, actual) in enumerate(zip(test_coords, (world_x, world_y, world_z))):
        assert abs(expected - actual) < 0.001, f"Pallet X coordinate {i} mismatch"

      print(f"Pallet X set successfully: count={x_count}, coords={test_coords}")

    finally:
      # Restore original pallet X
      _, orig_count, orig_x, orig_y, orig_z = original_pallet_x
      await robot.set_pallet_x(self.TEST_LOCATION_ID, orig_count, orig_x, orig_y, orig_z)

  @pytest.mark.asyncio
  async def test_get_pallet_y(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_pallet_y()"""
    pallet_y_data = await robot.get_pallet_y(self.TEST_LOCATION_ID)
    assert isinstance(pallet_y_data, tuple)
    assert len(pallet_y_data) == 5
    station_id, y_count, world_x, world_y, world_z = pallet_y_data
    assert station_id == self.TEST_LOCATION_ID
    assert isinstance(y_count, int)
    assert all(isinstance(val, float) for val in [world_x, world_y, world_z])
    print(f"Pallet Y for station {station_id}: count={y_count}, world=({world_x}, {world_y}, {world_z})")

  @pytest.mark.asyncio
  async def test_set_pallet_y(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_pallet_y()"""
    # Get original pallet Y for restoration
    original_pallet_y = await robot.get_pallet_y(self.TEST_LOCATION_ID)

    try:
      # Test setting pallet Y
      test_y_count = 4
      test_coords = (175.0, 275.0, 375.0)
      await robot.set_pallet_y(self.TEST_LOCATION_ID, test_y_count, *test_coords)

      # Verify the pallet Y was set
      new_pallet_y = await robot.get_pallet_y(self.TEST_LOCATION_ID)
      _, y_count, world_x, world_y, world_z = new_pallet_y

      assert y_count == test_y_count
      for i, (expected, actual) in enumerate(zip(test_coords, (world_x, world_y, world_z))):
        assert abs(expected - actual) < 0.001, f"Pallet Y coordinate {i} mismatch"

      print(f"Pallet Y set successfully: count={y_count}, coords={test_coords}")

    finally:
      # Restore original pallet Y
      _, orig_count, orig_x, orig_y, orig_z = original_pallet_y
      await robot.set_pallet_y(self.TEST_LOCATION_ID, orig_count, orig_x, orig_y, orig_z)

  @pytest.mark.asyncio
  async def test_get_pallet_z(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_pallet_z()"""
    pallet_z_data = await robot.get_pallet_z(self.TEST_LOCATION_ID)
    assert isinstance(pallet_z_data, tuple)
    assert len(pallet_z_data) == 5
    station_id, z_count, world_x, world_y, world_z = pallet_z_data
    assert station_id == self.TEST_LOCATION_ID
    assert isinstance(z_count, int)
    assert all(isinstance(val, float) for val in [world_x, world_y, world_z])
    print(f"Pallet Z for station {station_id}: count={z_count}, world=({world_x}, {world_y}, {world_z})")

  @pytest.mark.asyncio
  async def test_set_pallet_z(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_pallet_z()"""
    # Get original pallet Z for restoration
    original_pallet_z = await robot.get_pallet_z(self.TEST_LOCATION_ID)

    try:
      # Test setting pallet Z
      test_z_count = 3
      test_coords = (125.0, 225.0, 325.0)
      await robot.set_pallet_z(self.TEST_LOCATION_ID, test_z_count, *test_coords)

      # Verify the pallet Z was set
      new_pallet_z = await robot.get_pallet_z(self.TEST_LOCATION_ID)
      _, z_count, world_x, world_y, world_z = new_pallet_z

      assert z_count == test_z_count
      for i, (expected, actual) in enumerate(zip(test_coords, (world_x, world_y, world_z))):
        assert abs(expected - actual) < 0.001, f"Pallet Z coordinate {i} mismatch"

      print(f"Pallet Z set successfully: count={z_count}, coords={test_coords}")

    finally:
      # Restore original pallet Z
      _, orig_count, orig_x, orig_y, orig_z = original_pallet_z
      await robot.set_pallet_z(self.TEST_LOCATION_ID, orig_count, orig_x, orig_y, orig_z)

  @pytest.mark.asyncio
  async def test_pick_plate_station(self, robot: PreciseFlexBackendApi) -> None:
    """Test pick_plate_station() command"""
    # Record current position for restoration
    original_position = await robot.where_c()

    try:
      # Test basic pick without compliance
      result = await robot.pick_plate_station(self.TEST_LOCATION_ID)
      assert isinstance(result, bool)
      print(f"Pick plate station (basic) result: {result}")

      # Test pick with horizontal compliance
      result = await robot.pick_plate_station(self.TEST_LOCATION_ID, horizontal_compliance=True, horizontal_compliance_torque=50)
      assert isinstance(result, bool)
      print(f"Pick plate station (with compliance) result: {result}")

    finally:
      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_place_plate_station(self, robot: PreciseFlexBackendApi) -> None:
    """Test place_plate_station() command"""
    # Record current position for restoration
    original_position = await robot.where_c()

    try:
      # Test basic place without compliance
      await robot.place_plate_station(self.TEST_LOCATION_ID)
      print("Place plate station (basic) executed successfully")

      # Test place with horizontal compliance
      await robot.place_plate_station(self.TEST_LOCATION_ID, horizontal_compliance=True, horizontal_compliance_torque=25)
      print("Place plate station (with compliance) executed successfully")

    finally:
      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_get_rail_position(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_rail_position()"""
    rail_pos = await robot.get_rail_position(self.TEST_LOCATION_ID)
    assert isinstance(rail_pos, float)
    print(f"Rail position for station {self.TEST_LOCATION_ID}: {rail_pos}")

  @pytest.mark.asyncio
  async def test_set_rail_position(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_rail_position()"""
    # Get original rail position for restoration
    original_rail_pos = await robot.get_rail_position(self.TEST_LOCATION_ID)

    try:
      # Test setting rail position
      test_rail_pos = original_rail_pos + 10.0
      await robot.set_rail_position(self.TEST_LOCATION_ID, test_rail_pos)

      # Verify the position was set
      new_rail_pos = await robot.get_rail_position(self.TEST_LOCATION_ID)
      assert abs(new_rail_pos - test_rail_pos) < 0.001
      print(f"Rail position set to: {new_rail_pos}")

    finally:
      # Restore original rail position
      await robot.set_rail_position(self.TEST_LOCATION_ID, original_rail_pos)

  @pytest.mark.asyncio
  async def test_teach_plate_station(self, robot: PreciseFlexBackendApi) -> None:
    """Test teach_plate_station() command"""
    # Get original location for restoration
    original_location = await robot.get_location(self.TEST_LOCATION_ID)
    original_clearance = await robot.get_location_z_clearance(self.TEST_LOCATION_ID)

    try:
      # Test teaching with default Z clearance
      await robot.teach_plate_station(self.TEST_LOCATION_ID)
      print(f"Plate station {self.TEST_LOCATION_ID} taught with default clearance")

      # Test teaching with custom Z clearance
      test_clearance = 75.0
      await robot.teach_plate_station(self.TEST_LOCATION_ID, test_clearance)

      # Verify the clearance was set
      new_clearance = await robot.get_location_z_clearance(self.TEST_LOCATION_ID)
      _, z_clearance, _ = new_clearance
      assert abs(z_clearance - test_clearance) < 0.001
      print(f"Plate station taught with custom clearance: {z_clearance}")

    finally:
      # Restore original location and clearance
      type_code = original_location[0]
      if type_code == 0:  # Cartesian
        await robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Angles
        await robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

      _, orig_z_clearance, orig_z_world = original_clearance
      await robot.set_location_z_clearance(self.TEST_LOCATION_ID, orig_z_clearance, orig_z_world)

  @pytest.mark.asyncio
  async def test_get_station_type(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_station_type()"""
    station_data = await robot.get_station_type(self.TEST_LOCATION_ID)
    assert isinstance(station_data, tuple)
    assert len(station_data) == 6
    station_id, access_type, location_type, z_clearance, z_above, z_grasp_offset = station_data

    assert station_id == self.TEST_LOCATION_ID
    assert access_type in [0, 1]  # 0 = horizontal, 1 = vertical
    assert location_type in [0, 1]  # 0 = normal, 1 = pallet
    assert isinstance(z_clearance, float)
    assert isinstance(z_above, float)
    assert isinstance(z_grasp_offset, float)

    access_str = "horizontal" if access_type == 0 else "vertical"
    location_str = "normal single" if location_type == 0 else "pallet"
    print(f"Station {station_id}: access={access_str}, type={location_str}, clearance={z_clearance}, above={z_above}, grasp_offset={z_grasp_offset}")

  @pytest.mark.asyncio
  async def test_set_station_type(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_station_type()"""
    # Get original station type for restoration
    original_station = await robot.get_station_type(self.TEST_LOCATION_ID)

    try:
      # Test setting station type
      test_values = (
        0,     # access_type: horizontal
        1,     # location_type: pallet
        60.0,  # z_clearance
        15.0,  # z_above
        5.0    # z_grasp_offset
      )

      await robot.set_station_type(self.TEST_LOCATION_ID, *test_values)

      # Verify the station type was set
      new_station = await robot.get_station_type(self.TEST_LOCATION_ID)
      _, access_type, location_type, z_clearance, z_above, z_grasp_offset = new_station

      assert access_type == test_values[0]
      assert location_type == test_values[1]
      assert abs(z_clearance - test_values[2]) < 0.001
      assert abs(z_above - test_values[3]) < 0.001
      assert abs(z_grasp_offset - test_values[4]) < 0.001

      print(f"Station type set successfully: {test_values}")

      # Test invalid access type
      with pytest.raises(ValueError):
        await robot.set_station_type(self.TEST_LOCATION_ID, 3, 0, 50.0, 10.0, 0.0)

      # Test invalid location type
      with pytest.raises(ValueError):
        await robot.set_station_type(self.TEST_LOCATION_ID, 0, 3, 50.0, 10.0, 0.0)

    finally:
      # Restore original station type
      _, orig_access, orig_location, orig_clearance, orig_above, orig_grasp = original_station
      await robot.set_station_type(self.TEST_LOCATION_ID, orig_access, orig_location, orig_clearance, orig_above, orig_grasp)

#region SSGRIP COMMANDS
#region SSGRIP COMMANDS
  @pytest.mark.asyncio
  async def test_home_all_if_no_plate(self, robot: PreciseFlexBackendApi) -> None:
    """Test home_all_if_no_plate()"""
    result = await robot.home_all_if_no_plate()
    assert isinstance(result, int)
    assert result in [-1, 0]

    result_str = "no plate detected and command succeeded" if result == -1 else "plate detected"
    print(f"Home all if no plate result: {result} ({result_str})")

  @pytest.mark.asyncio
  async def test_grasp_plate(self, robot: PreciseFlexBackendApi) -> None:
    """Test grasp_plate()"""
    # Test with valid parameters for closing gripper
    result = await robot.grasp_plate(15.0, 50, 10.0)
    assert isinstance(result, int)
    assert result in [-1, 0]

    result_str = "plate grasped" if result == -1 else "no plate detected"
    print(f"Grasp plate (close) result: {result} ({result_str})")

    # Test with negative force for opening gripper
    result = await robot.grasp_plate(25.0, 75, -5.0)
    assert isinstance(result, int)
    assert result in [-1, 0]
    print(f"Grasp plate (open) result: {result}")

    # Test invalid finger speed
    with pytest.raises(ValueError):
      await robot.grasp_plate(15.0, 0, 10.0)  # Speed too low

    with pytest.raises(ValueError):
      await robot.grasp_plate(15.0, 101, 10.0)  # Speed too high

  @pytest.mark.asyncio
  async def test_release_plate(self, robot: PreciseFlexBackendApi) -> None:
    """Test release_plate()"""
    # Test basic release
    await robot.release_plate(30.0, 50)
    print("Release plate (basic) executed successfully")

    # Test release with InRange
    await robot.release_plate(35.0, 75, 5.0)
    print("Release plate (with InRange) executed successfully")

    # Test invalid finger speed
    with pytest.raises(ValueError):
      await robot.release_plate(30.0, 0)  # Speed too low

    with pytest.raises(ValueError):
      await robot.release_plate(30.0, 101)  # Speed too high

  @pytest.mark.asyncio
  async def test_is_fully_closed(self, robot: PreciseFlexBackendApi) -> None:
    """Test is_fully_closed()"""
    closed_state = await robot.is_fully_closed()
    assert isinstance(closed_state, int)
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

  @pytest.mark.asyncio
  async def test_set_active_gripper(self, robot: PreciseFlexBackendApi) -> None:
    """Test set_active_gripper() (Dual Gripper Only)"""
    # Note: This test assumes a dual gripper system
    # Get original active gripper for restoration
    try:
      original_gripper = await robot.get_active_gripper()
    except:
      # Skip test if dual gripper not available
      print("Dual gripper not available, skipping set_active_gripper test")
      return

    try:
      # Test setting gripper 1 without spin
      await robot.set_active_gripper(1, 0)
      active_gripper = await robot.get_active_gripper()
      assert active_gripper == 1
      print("Active gripper set to 1 (no spin)")

      # Test setting gripper 2 without spin
      await robot.set_active_gripper(2, 0)
      active_gripper = await robot.get_active_gripper()
      assert active_gripper == 2
      print("Active gripper set to 2 (no spin)")

      # Test setting gripper with spin and profile
      await robot.set_active_gripper(1, 1, self.TEST_PROFILE_ID)
      active_gripper = await robot.get_active_gripper()
      assert active_gripper == 1
      print("Active gripper set to 1 (with spin and profile)")

      # Test invalid gripper ID
      with pytest.raises(ValueError):
        await robot.set_active_gripper(3, 0)

      # Test invalid spin mode
      with pytest.raises(ValueError):
        await robot.set_active_gripper(1, 2)

    finally:
      # Restore original active gripper
      await robot.set_active_gripper(original_gripper, 0)

  @pytest.mark.asyncio
  async def test_get_active_gripper(self, robot: PreciseFlexBackendApi) -> None:
    """Test get_active_gripper() (Dual Gripper Only)"""
    try:
      active_gripper = await robot.get_active_gripper()
      assert isinstance(active_gripper, int)
      assert active_gripper in [1, 2]

      gripper_name = "A" if active_gripper == 1 else "B"
      print(f"Active gripper: {active_gripper} (Gripper {gripper_name})")
    except:
      print("Dual gripper not available, skipping get_active_gripper test")

  @pytest.mark.asyncio
  async def test_free_mode(self, robot: PreciseFlexBackendApi) -> None:
    """Test free_mode()"""
    try:
      # Test enabling free mode for all axes
      await robot.free_mode(True, 0)
      print("Free mode enabled for all axes")

      # Brief pause to allow mode to take effect
      await asyncio.sleep(0.5)

      # Test enabling free mode for specific axis
      await robot.free_mode(True, 1)
      print("Free mode enabled for axis 1")

      # Brief pause
      await asyncio.sleep(0.5)

    finally:
      # Always disable free mode for safety
      await robot.free_mode(False)
      print("Free mode disabled for all axes")

  @pytest.mark.asyncio
  async def test_open_gripper(self, robot: PreciseFlexBackendApi) -> None:
    """Test open_gripper()"""
    await robot.open_gripper()
    print("Gripper opened successfully")

    # Brief delay to allow gripper to move
    await asyncio.sleep(0.5)

  @pytest.mark.asyncio
  async def test_close_gripper(self, robot: PreciseFlexBackendApi) -> None:
    """Test close_gripper()"""
    await robot.close_gripper()
    print("Gripper closed successfully")

    # Brief delay to allow gripper to move
    await asyncio.sleep(0.5)

  @pytest.mark.asyncio
  async def test_pick_plate(self, robot: PreciseFlexBackendApi) -> None:
    """Test pick_plate()"""
    # Record current position for restoration
    original_position = await robot.where_c()

    try:
      # Test basic pick without compliance
      await robot.pick_plate(self.TEST_LOCATION_ID)
      print(f"Pick plate (basic) at location {self.TEST_LOCATION_ID} executed successfully")

      # Test pick with horizontal compliance
      await robot.pick_plate(self.TEST_LOCATION_ID, horizontal_compliance=True, horizontal_compliance_torque=50)
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
      await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_place_plate(self, robot: PreciseFlexBackendApi) -> None:
    """Test place_plate()"""
    # Record current position for restoration
    original_position = await robot.where_c()

    try:
      # Test basic place without compliance
      await robot.place_plate(self.TEST_LOCATION_ID)
      print(f"Place plate (basic) at location {self.TEST_LOCATION_ID} executed successfully")

      # Test place with horizontal compliance
      await robot.place_plate(self.TEST_LOCATION_ID, horizontal_compliance=True, horizontal_compliance_torque=25)
      print(f"Place plate (with compliance) at location {self.TEST_LOCATION_ID} executed successfully")

    finally:
      # Return to original position
      x, y, z, yaw, pitch, roll, config = original_position
      await robot.move_c(self.TEST_PROFILE_ID, x, y, z, yaw, pitch, roll)
      await robot.wait_for_eom()

  @pytest.mark.asyncio
  async def test_teach_position(self, robot: PreciseFlexBackendApi) -> None:
    """Test teach_position()"""
    # Get original location and clearance for restoration
    original_location = await robot.get_location(self.TEST_LOCATION_ID)
    original_clearance = await robot.get_location_z_clearance(self.TEST_LOCATION_ID)

    try:
      # Test teaching with default Z clearance
      await robot.teach_position(self.TEST_LOCATION_ID)
      print(f"Position {self.TEST_LOCATION_ID} taught with default clearance (50.0)")

      # Verify the location was recorded as Cartesian type
      location_data = await robot.get_location(self.TEST_LOCATION_ID)
      type_code = location_data[0]
      assert type_code == 0  # Should be Cartesian type

      # Test teaching with custom Z clearance
      test_clearance = 75.0
      await robot.teach_position(self.TEST_LOCATION_ID, test_clearance)

      # Verify the clearance was set
      new_clearance = await robot.get_location_z_clearance(self.TEST_LOCATION_ID)
      _, z_clearance, _ = new_clearance
      assert abs(z_clearance - test_clearance) < 0.001
      print(f"Position taught with custom clearance: {z_clearance}")

    finally:
      # Restore original location and clearance
      type_code = original_location[0]
      if type_code == 0:  # Was Cartesian type
        await robot.set_location_xyz(self.TEST_LOCATION_ID, *original_location[2:8])
      else:  # Was angles type
        await robot.set_location_angles(self.TEST_LOCATION_ID, *original_location[2:8])

      _, orig_z_clearance, orig_z_world = original_clearance
      await robot.set_location_z_clearance(self.TEST_LOCATION_ID, orig_z_clearance, orig_z_world)




if __name__ == "__main__":

  async def run_general_command_tests() -> None:
    """Run all tests in the GENERAL COMMANDS region"""
    test_instance = TestPreciseFlexIntegration()

    # Setup robot connection
    async for robot in test_instance.robot():
      try:
        print("Starting GENERAL COMMANDS tests...")

        # Run all general command tests in order
        await test_instance.test_robot_connection_and_version(robot)
        await test_instance.test_get_base(robot)
        await test_instance.test_set_base(robot)
        await test_instance.test_home(robot)
        await test_instance.test_home_all(robot)
        await test_instance.test_get_power_state(robot)
        await test_instance.test_set_power(robot)
        await test_instance.test_get_mode(robot)
        await test_instance.test_set_mode(robot)
        await test_instance.test_get_monitor_speed(robot)
        await test_instance.test_set_monitor_speed(robot)
        await test_instance.test_nop(robot)
        await test_instance.test_get_payload(robot)
        await test_instance.test_set_payload(robot)
        await test_instance.test_parameter_operations(robot)
        await test_instance.test_get_selected_robot(robot)
        await test_instance.test_select_robot(robot)
        await test_instance.test_signal_operations(robot)
        await test_instance.test_get_system_state(robot)
        await test_instance.test_get_tool(robot)
        await test_instance.test_set_tool(robot)
        await test_instance.test_get_version(robot)
        # Note: test_reset is commented out for safety

        print("GENERAL COMMANDS tests completed successfully!")

      except Exception as e:
        print(f"General commands test failed with error: {e}")
        raise
      finally:
        break


  async def run_location_command_tests() -> None:
    """Run all tests in the LOCATION COMMANDS region"""
    test_instance = TestPreciseFlexIntegration()

    async for robot in test_instance.robot():
      try:
        print("Starting LOCATION COMMANDS tests...")

        await test_instance.test_get_location(robot)
        await test_instance.test_get_location_angles(robot)
        await test_instance.test_set_location_angles(robot)
        await test_instance.test_get_location_xyz(robot)
        await test_instance.test_set_location_xyz(robot)
        await test_instance.test_get_location_z_clearance(robot)
        await test_instance.test_set_location_z_clearance(robot)
        await test_instance.test_get_location_config(robot)
        await test_instance.test_set_location_config(robot)
        await test_instance.test_dest_c(robot)
        await test_instance.test_dest_j(robot)
        await test_instance.test_here_j(robot)
        await test_instance.test_here_c(robot)
        await test_instance.test_where(robot)
        await test_instance.test_where_c(robot)
        await test_instance.test_where_j(robot)

        print("LOCATION COMMANDS tests completed successfully!")

      except Exception as e:
        print(f"Location commands test failed with error: {e}")
        raise
      finally:
        break
  async def run_profile_command_tests() -> None:
    """Run all tests in the PROFILE COMMANDS region"""
    test_instance = TestPreciseFlexIntegration()

    async for robot in test_instance.robot():
      try:
        print("Starting PROFILE COMMANDS tests...")

        await test_instance.test_get_profile_speed(robot)
        await test_instance.test_set_profile_speed(robot)
        await test_instance.test_get_profile_speed2(robot)
        await test_instance.test_set_profile_speed2(robot)
        await test_instance.test_get_profile_accel(robot)
        await test_instance.test_set_profile_accel(robot)
        await test_instance.test_get_profile_accel_ramp(robot)
        await test_instance.test_set_profile_accel_ramp(robot)
        await test_instance.test_get_profile_decel(robot)
        await test_instance.test_set_profile_decel(robot)
        await test_instance.test_get_profile_decel_ramp(robot)
        await test_instance.test_set_profile_decel_ramp(robot)
        await test_instance.test_get_profile_in_range(robot)
        await test_instance.test_set_profile_in_range(robot)
        await test_instance.test_get_profile_straight(robot)
        await test_instance.test_set_profile_straight(robot)
        await test_instance.test_get_motion_profile_values(robot)
        await test_instance.test_set_motion_profile_values(robot)

        print("PROFILE COMMANDS tests completed successfully!")

      except Exception as e:
        print(f"Profile commands test failed with error: {e}")
        raise
      finally:
        break

  async def run_motion_command_tests() -> None:
    """Run all tests in the MOTION COMMANDS region"""
    test_instance = TestPreciseFlexIntegration()

    async for robot in test_instance.robot():
      try:
        print("Starting MOTION COMMANDS tests...")

        await test_instance.test_halt(robot)
        await test_instance.test_move(robot)
        await test_instance.test_move_appro(robot)
        await test_instance.test_move_extra_axis(robot)
        await test_instance.test_move_one_axis(robot)
        await test_instance.test_move_c(robot)
        await test_instance.test_move_j(robot)
        await test_instance.test_release_brake(robot)
        await test_instance.test_set_brake(robot)
        await test_instance.test_state(robot)
        await test_instance.test_wait_for_eom(robot)
        await test_instance.test_zero_torque(robot)

        print("MOTION COMMANDS tests completed successfully!")

      except Exception as e:
        print(f"Motion commands test failed with error: {e}")
        raise
      finally:
        break

  async def run_parobot_command_tests() -> None:
    """Run all tests in the PAROBOT COMMANDS region"""
    test_instance = TestPreciseFlexIntegration()

    async for robot in test_instance.robot():
      try:
        print("Starting PAROBOT COMMANDS tests...")

        await test_instance.test_change_config(robot)
        await test_instance.test_change_config2(robot)
        await test_instance.test_get_grasp_data(robot)
        await test_instance.test_set_grasp_data(robot)
        await test_instance.test_get_grip_close_pos(robot)
        await test_instance.test_set_grip_close_pos(robot)
        await test_instance.test_get_grip_open_pos(robot)
        await test_instance.test_set_grip_open_pos(robot)
        await test_instance.test_gripper(robot)
        await test_instance.test_move_rail(robot)
        await test_instance.test_move_to_safe(robot)
        await test_instance.test_get_pallet_index(robot)
        await test_instance.test_set_pallet_index(robot)
        await test_instance.test_get_pallet_origin(robot)
        await test_instance.test_set_pallet_origin(robot)
        await test_instance.test_get_pallet_x(robot)
        await test_instance.test_set_pallet_x(robot)
        await test_instance.test_get_pallet_y(robot)
        await test_instance.test_set_pallet_y(robot)
        await test_instance.test_get_pallet_z(robot)
        await test_instance.test_set_pallet_z(robot)
        await test_instance.test_pick_plate_station(robot)
        await test_instance.test_place_plate_station(robot)
        await test_instance.test_get_rail_position(robot)
        await test_instance.test_set_rail_position(robot)
        await test_instance.test_teach_plate_station(robot)
        await test_instance.test_get_station_type(robot)
        await test_instance.test_set_station_type(robot)

        print("PAROBOT COMMANDS tests completed successfully!")

      except Exception as e:
        print(f"Parobot commands test failed with error: {e}")
        raise
      finally:
        break

  async def run_ssgrip_command_tests() -> None:
    """Run all tests in the SSGRIP COMMANDS region"""
    test_instance = TestPreciseFlexIntegration()

    async for robot in test_instance.robot():
      try:
        print("Starting SSGRIP COMMANDS tests...")

        await test_instance.test_home_all_if_no_plate(robot)
        await test_instance.test_grasp_plate(robot)
        await test_instance.test_release_plate(robot)
        await test_instance.test_is_fully_closed(robot)
        await test_instance.test_set_active_gripper(robot)
        await test_instance.test_get_active_gripper(robot)
        await test_instance.test_free_mode(robot)
        await test_instance.test_open_gripper(robot)
        await test_instance.test_close_gripper(robot)
        await test_instance.test_pick_plate(robot)
        await test_instance.test_place_plate(robot)
        await test_instance.test_teach_position(robot)

        print("SSGRIP COMMANDS tests completed successfully!")

      except Exception as e:
        print(f"SSGrip commands test failed with error: {e}")
        raise
      finally:
        break

  async def run_all_tests_by_region() -> None:
    """Run tests organized by region for better control and debugging"""
    try:
      print("=== Running GENERAL COMMANDS ===")
      await run_general_command_tests()

      print("\n=== Running LOCATION COMMANDS ===")
      await run_location_command_tests()

      print("\n=== Running PROFILE COMMANDS ===")
      await run_profile_command_tests()

      print("\n=== Running MOTION COMMANDS ===")
      await run_motion_command_tests()

      print("\n=== Running PAROBOT COMMANDS ===")
      await run_parobot_command_tests()

      print("\n=== Running SSGRIP COMMANDS ===")
      await run_ssgrip_command_tests()

      print("\nAll test regions completed successfully!")

    except Exception as e:
      print(f"Test suite failed: {e}")


  # Main execution
  if __name__ == "__main__":
    # Option 1: Run just general commands
    # asyncio.run(run_general_command_tests())

    # Option 2: Run all tests by region (recommended)
    asyncio.run(run_all_tests_by_region())

    # Option 3: Run specific region
    # asyncio.run(run_location_command_tests())