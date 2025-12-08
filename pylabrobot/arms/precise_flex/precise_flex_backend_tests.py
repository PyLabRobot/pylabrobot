import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.arms.backend import HorizontalAccess, VerticalAccess
from pylabrobot.arms.precise_flex.coords import ElbowOrientation, PreciseFlexCartesianCoords
from pylabrobot.arms.precise_flex.joints import PreciseFlexJointCoords
from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend, PreciseFlexError
from pylabrobot.io.socket import Socket  # Import Socket for mocking
from pylabrobot.resources import Coordinate, Rotation


class PreciseFlexBackendHardwareTests(unittest.IsolatedAsyncioTestCase):
  """Integration tests for PreciseFlex robot - RUNS ON ACTUAL HARDWARE"""


class PreciseFlexBackendTests(unittest.IsolatedAsyncioTestCase):
  """Unit tests for PreciseFlexBackend"""

  def setUp(self):
    self.mock_socket_instance = AsyncMock(spec=Socket)
    self.mock_socket_instance.read.return_value = b""
    self.mock_socket_instance.readline.return_value = b""
    self.mock_socket_instance.write.return_value = None
    self.mock_socket_instance.setup.return_value = None  # Configure setup to return None
    self.mock_socket_instance._writer = AsyncMock()  # Mock the _writer attribute

    # Patch the Socket class where it's used in PreciseFlexBackend
    patcher = patch(
      "pylabrobot.arms.precise_flex.precise_flex_backend.Socket",
      return_value=self.mock_socket_instance,
    )
    self.MockSocketClass = patcher.start()  # Store the mock of the class
    self.addCleanup(patcher.stop)

    self.backend = PreciseFlexBackend(has_rail=False, host="localhost", port=10100)
    # self.backend.io is already self.mock_socket_instance because of the patch

  async def test_init(self):
    self.assertFalse(self.backend._has_rail)
    self.MockSocketClass.assert_called_once_with(host="localhost", port=10100)
    self.assertEqual(self.backend.profile_index, 1)
    self.assertEqual(self.backend.location_index, 1)
    self.assertFalse(self.backend.horizontal_compliance)
    self.assertEqual(self.backend.horizontal_compliance_torque, 0)
    self.assertEqual(self.backend.timeout, 20)

    # Reset the mock for the next test (or if we want to test another instance)
    self.MockSocketClass.reset_mock()

    backend_with_rail = PreciseFlexBackend(has_rail=True, host="127.0.0.1", port=12345)
    self.assertTrue(backend_with_rail._has_rail)
    self.MockSocketClass.assert_called_once_with(host="127.0.0.1", port=12345)
    self.assertEqual(backend_with_rail.profile_index, 1)
    self.assertEqual(backend_with_rail.location_index, 1)
    self.assertFalse(backend_with_rail.horizontal_compliance)
    self.assertEqual(backend_with_rail.horizontal_compliance_torque, 0)
    self.assertEqual(backend_with_rail.timeout, 20)

  async def test_convert_to_joint_space_no_rail(self):
    position = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    joint_coords = self.backend._convert_to_joint_space(position)
    self.assertEqual(joint_coords.rail, 0.0)
    self.assertEqual(joint_coords.base, 10.0)
    self.assertEqual(joint_coords.shoulder, 20.0)
    self.assertEqual(joint_coords.elbow, 30.0)
    self.assertEqual(joint_coords.wrist, 40.0)
    self.assertEqual(joint_coords.gripper, 50.0)

  async def test_convert_to_joint_space_no_rail_error(self):
    position = [1.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    with self.assertRaisesRegex(
      ValueError, r"Position\[0\] \(rail\) must be 0.0 for robot without rail."
    ):
      self.backend._convert_to_joint_space(position)

  async def test_convert_to_joint_space_too_few_elements(self):
    position = [0.0, 10.0, 20.0, 30.0, 40.0]
    with self.assertRaisesRegex(
      ValueError, "Position must have 6 joint angles for robot with rail."
    ):
      self.backend._convert_to_joint_space(position)

  async def test_convert_to_cartesian_space(self):
    position = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, ElbowOrientation.RIGHT)
    cartesian_coords = self.backend._convert_to_cartesian_space(position)
    self.assertEqual(cartesian_coords.location.x, 1.0)
    self.assertEqual(cartesian_coords.location.y, 2.0)
    self.assertEqual(cartesian_coords.location.z, 3.0)
    self.assertEqual(cartesian_coords.rotation.yaw, 4.0)
    self.assertEqual(cartesian_coords.rotation.pitch, 5.0)
    self.assertEqual(cartesian_coords.rotation.roll, 6.0)
    self.assertEqual(cartesian_coords.orientation, ElbowOrientation.RIGHT)

  async def test_convert_to_cartesian_space_too_few_elements(self):
    position = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    with self.assertRaisesRegex(ValueError, "Position must be a tuple of 7 values"):
      self.backend._convert_to_cartesian_space(position)  # type: ignore

  async def test_convert_to_cartesian_array(self):
    cartesian_coords = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.LEFT,
    )
    cartesian_array = self.backend._convert_to_cartesian_array(cartesian_coords)
    self.assertEqual(cartesian_array, (1.0, 2.0, 3.0, 6.0, 5.0, 4.0, 2))

  async def test_setup(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 OK\r\n",  # set_mode
      b"0 OK\r\n",  # power_on_robot
      b"0 OK\r\n",  # attach
      b"0 OK\r\n",  # home
    ]
    await self.backend.setup()
    self.mock_socket_instance.setup.assert_called_once()
    self.mock_socket_instance.write.assert_any_call(b"mode 0\n")
    self.mock_socket_instance.write.assert_any_call(b"hp 1 20\n")
    self.mock_socket_instance.write.assert_any_call(b"attach 1\n")
    self.mock_socket_instance.write.assert_any_call(b"home\n")

  async def test_stop(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 attach\r\n",  # detach
      b"0 hp\r\n",  # power_off_robot
      b"0 exit\r\n",  # exit
    ]
    await self.backend.stop()
    self.mock_socket_instance.write.assert_any_call(b"attach 0\n")
    self.mock_socket_instance.write.assert_any_call(b"hp 0\n")
    self.mock_socket_instance.write.assert_any_call(b"exit\n")
    self.mock_socket_instance.stop.assert_called_once()

  async def test_set_speed(self):
    self.mock_socket_instance.readline.return_value = b"0 Speed 1 50.0\r\n"
    await self.backend.set_speed(50.0)
    self.mock_socket_instance.write.assert_called_with(b"Speed 1 50.0\n")

  async def test_get_speed(self):
    self.mock_socket_instance.readline.return_value = b"0 1 50.0\r\n"
    speed = await self.backend.get_speed()
    self.assertEqual(speed, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"Speed 1\n")

  async def test_open_gripper(self):
    self.mock_socket_instance.readline.return_value = b"0 gripper 1\r\n"
    await self.backend.open_gripper(50.0)
    self.mock_socket_instance.write.assert_any_call(b"GripOpenPos 50.0\n")
    self.mock_socket_instance.write.assert_called_with(b"gripper 1\n")

  async def test_close_gripper(self):
    self.mock_socket_instance.readline.return_value = b"0 gripper 2\r\n"
    await self.backend.close_gripper(50.0)
    self.mock_socket_instance.write.assert_any_call(b"GripClosePos 50.0\n")
    self.mock_socket_instance.write.assert_called_with(b"gripper 2\n")

  async def test_is_gripper_closed(self):
    self.mock_socket_instance.readline.return_value = b"0 -1\r\n"
    is_closed = await self.backend.is_gripper_closed()
    self.assertTrue(is_closed)
    self.mock_socket_instance.write.assert_called_with(b"IsFullyClosed\n")

    self.mock_socket_instance.readline.return_value = b"0 0\r\n"
    is_closed = await self.backend.is_gripper_closed()
    self.assertFalse(is_closed)

  async def test_halt(self):
    self.mock_socket_instance.readline.return_value = b"0 halt\r\n"
    await self.backend.halt()
    self.mock_socket_instance.write.assert_called_with(b"halt\n")

  async def test_home(self):
    self.mock_socket_instance.readline.return_value = b"0 home\r\n"
    await self.backend.home()
    self.mock_socket_instance.write.assert_called_with(b"home\n")

  async def test_move_to_safe(self):
    self.mock_socket_instance.readline.return_value = b"0 movetosafe\r\n"
    await self.backend.move_to_safe()
    self.mock_socket_instance.write.assert_called_with(b"movetosafe\n")

  async def test_convert_orientation_int_to_enum(self):
    self.assertEqual(self.backend._convert_orientation_int_to_enum(1), ElbowOrientation.RIGHT)
    self.assertEqual(self.backend._convert_orientation_int_to_enum(2), ElbowOrientation.LEFT)
    self.assertIsNone(self.backend._convert_orientation_int_to_enum(0))
    self.assertIsNone(self.backend._convert_orientation_int_to_enum(99))

  async def test_convert_orientation_enum_to_int(self):
    self.assertEqual(self.backend._convert_orientation_enum_to_int(ElbowOrientation.LEFT), 2)
    self.assertEqual(self.backend._convert_orientation_enum_to_int(ElbowOrientation.RIGHT), 1)
    self.assertEqual(self.backend._convert_orientation_enum_to_int(None), 0)

  async def test_home_all(self):
    self.mock_socket_instance.readline.return_value = b"0 homeAll\r\n"
    await self.backend.home_all()
    self.mock_socket_instance.write.assert_called_with(b"homeAll\n")

  async def test_attach_get_state(self):
    self.mock_socket_instance.readline.return_value = b"0 -1\r\n"
    state = await self.backend.attach()
    self.assertEqual(state, -1)
    self.mock_socket_instance.write.assert_called_with(b"attach\n")

    self.mock_socket_instance.readline.return_value = b"0 0\r\n"
    state = await self.backend.attach()
    self.assertEqual(state, 0)

  async def test_attach_set_state(self):
    self.mock_socket_instance.readline.return_value = b"0 attach 1\r\n"
    await self.backend.attach(1)
    self.mock_socket_instance.write.assert_called_with(b"attach 1\n")

  async def test_detach(self):
    self.mock_socket_instance.readline.return_value = b"0 attach 0\r\n"
    await self.backend.detach()
    self.mock_socket_instance.write.assert_called_with(b"attach 0\n")

  async def test_power_on_robot(self):
    self.mock_socket_instance.readline.return_value = b"0 hp 1 20\r\n"
    await self.backend.power_on_robot()
    self.mock_socket_instance.write.assert_called_with(b"hp 1 20\n")

  async def test_power_off_robot(self):
    self.mock_socket_instance.readline.return_value = b"0 hp 0\r\n"
    await self.backend.power_off_robot()
    self.mock_socket_instance.write.assert_called_with(b"hp 0\n")

  async def test_version(self):
    self.mock_socket_instance.readline.return_value = b"0 v1.0.0\r\n"
    version = await self.backend.get_version()
    self.assertEqual(version, "v1.0.0")
    self.mock_socket_instance.write.assert_called_with(b"version\n")

  async def test_approach_joint_space(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locAngles 1 0.0 10.0 20.0 30.0 40.0 50.0\r\n",  # set_joint_angles
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 moveAppro 1 1\r\n",  # move_to_stored_location_appro
    ]
    position = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    await self.backend.approach(position)
    self.mock_socket_instance.write.assert_any_call(b"locAngles 1 10.0 20.0 30.0 40.0 50.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"moveAppro 1 1\n")

  async def test_approach_cartesian_space(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locXyz 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n",  # set_location_xyz
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 locConfig 1 1\r\n",  # set_location_config
      b"0 moveAppro 1 1\r\n",  # move_to_stored_location_appro
    ]
    position = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend.approach(position)
    self.mock_socket_instance.write.assert_any_call(b"locXyz 1 1.0 2.0 3.0 6.0 5.0 4.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"locConfig 1 1\n")
    self.mock_socket_instance.write.assert_any_call(b"moveAppro 1 1\n")

  async def test_approach_invalid_position_type(self):
    with self.assertRaisesRegex(
      TypeError, r"Position must be of type Iterable\[float\] or CartesianCoords."
    ):
      await self.backend.approach("invalid")  # type: ignore

  async def test_pick_plate(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 OK\r\n",  # set_grasp_data
      b"0 OK\r\n",  # set_location_xyz
      b"0 OK\r\n",  # _set_grip_detail (StationType)
      b"0 OK\r\n",  # set_location_config
      b"0 1\r\n",  # pick_plate_from_stored_position
    ]
    position = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend.pick_up_resource(
      position, plate_width=85.6, finger_speed_percent=50.0, grasp_force=10.0
    )
    self.mock_socket_instance.write.assert_any_call(b"GraspData 85.6 50.0 10.0\n")
    self.mock_socket_instance.write.assert_any_call(b"locXyz 1 1.0 2.0 3.0 6.0 5.0 4.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"locConfig 1 4097\n")
    self.mock_socket_instance.write.assert_any_call(b"pickplate 1 0 0\n")

  async def test_pick_plate_invalid_position_type(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 OK\r\n",  # For set_grasp_data
    ]
    with self.assertRaisesRegex(
      TypeError, r"Position must be of type Iterable\[float\] or CartesianCoords."
    ):
      await self.backend.pick_up_resource("invalid", plate_width=1.0)  # type: ignore

  async def test_place_plate(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locXyz 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n",  # set_location_xyz
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 locConfig 1 4097\r\n",  # set_location_config
      b"0 placeplate 1 0 0\r\n",  # place_plate_to_stored_position
    ]
    position = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend.drop_resource(position)
    self.mock_socket_instance.write.assert_any_call(b"locXyz 1 1.0 2.0 3.0 6.0 5.0 4.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"locConfig 1 4097\n")
    self.mock_socket_instance.write.assert_any_call(b"placeplate 1 0 0\n")

  async def test_place_plate_invalid_position_type(self):
    with self.assertRaisesRegex(
      TypeError, "place_plate only supports CartesianCoords for PreciseFlex."
    ):
      await self.backend.drop_resource([1, 2, 3, 4, 5, 6])

  async def test_move_to_joint_space(self):
    self.mock_socket_instance.readline.return_value = b"0 moveJ 1 0.0 10.0 20.0 30.0 40.0 50.0\r\n"
    position = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    await self.backend.move_to(position)
    self.mock_socket_instance.write.assert_called_with(b"moveJ 1 10.0 20.0 30.0 40.0 50.0\n")

  async def test_move_to_cartesian_space(self):
    self.mock_socket_instance.readline.return_value = b"0 moveC 1 1.0 2.0 3.0 4.0 5.0 6.0 1\r\n"
    position = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend.move_to(position)
    self.mock_socket_instance.write.assert_called_with(b"moveC 1 1.0 2.0 3.0 6.0 5.0 4.0 4097\n")

  async def test_move_to_invalid_position_type(self):
    with self.assertRaisesRegex(
      TypeError, "Position must be of type Iterable\\[float\\] or CartesianCoords."
    ):
      await self.backend.move_to("invalid")  # type: ignore

  async def test_get_joint_position(self):
    self.mock_socket_instance.readline.return_value = (
      b"0 10.0 20.0 30.0 40.0 50.0\r\n"  # 5 values for base, shoulder, elbow, wrist, gripper
    )
    joint_coords = await self.backend.get_joint_position()
    self.assertEqual(joint_coords.rail, 0.0)
    self.assertEqual(joint_coords.base, 10.0)
    self.assertEqual(joint_coords.shoulder, 20.0)
    self.assertEqual(joint_coords.elbow, 30.0)
    self.assertEqual(joint_coords.wrist, 40.0)
    self.assertEqual(joint_coords.gripper, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"wherej\n")

  async def test_get_cartesian_position(self):
    self.mock_socket_instance.readline.return_value = b"0 1.0 2.0 3.0 4.0 5.0 6.0 1\r\n"
    cartesian_coords = await self.backend.get_cartesian_position()
    self.assertEqual(cartesian_coords.location.x, 1.0)
    self.assertEqual(cartesian_coords.location.y, 2.0)
    self.assertEqual(cartesian_coords.location.z, 3.0)
    self.assertEqual(cartesian_coords.rotation.yaw, 4.0)
    self.assertEqual(cartesian_coords.rotation.pitch, 5.0)
    self.assertEqual(cartesian_coords.rotation.roll, 6.0)
    self.assertEqual(cartesian_coords.orientation, ElbowOrientation.RIGHT)
    self.mock_socket_instance.write.assert_called_with(b"wherec\n")

  async def test_send_command_success(self):
    self.mock_socket_instance.readline.return_value = b"0 OK\r\n"
    response = await self.backend.send_command("test_command")
    self.assertEqual(response, "OK")
    self.mock_socket_instance.write.assert_called_with(b"test_command\n")

  async def test_send_command_error(self):
    self.mock_socket_instance.readline.return_value = b"123 Error Message\r\n"
    with self.assertRaisesRegex(PreciseFlexError, "PreciseFlexError 123: Error Message"):
      await self.backend.send_command("test_command")

  async def test_parse_reply_ensure_successful_empty_reply(self):
    with self.assertRaisesRegex(PreciseFlexError, "Empty reply from device."):
      self.backend._parse_reply_ensure_successful(b"\r\n")

  async def test_parse_reply_ensure_successful_error_code_only(self):
    with self.assertRaisesRegex(PreciseFlexError, "PreciseFlexError 123: "):
      self.backend._parse_reply_ensure_successful(b"123\r\n")

  async def test_approach_j(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locAngles 1 0.0 10.0 20.0 30.0 40.0 50.0\r\n",  # set_joint_angles
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 moveAppro 1 1\r\n",  # move_to_stored_location_appro
    ]
    joint_coords = PreciseFlexJointCoords(
      rail=0.0, base=10.0, shoulder=20.0, elbow=30.0, wrist=40.0, gripper=50.0
    )
    await self.backend._approach_j(joint_coords, VerticalAccess())
    self.mock_socket_instance.write.assert_any_call(b"locAngles 1 10.0 20.0 30.0 40.0 50.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"moveAppro 1 1\n")

  async def test_pick_plate_j(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locAngles 1 0.0 10.0 20.0 30.0 40.0 50.0\r\n",  # set_joint_angles
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 1\r\n",  # pick_plate_from_stored_position
    ]
    joint_coords = PreciseFlexJointCoords(
      rail=0.0, base=10.0, shoulder=20.0, elbow=30.0, wrist=40.0, gripper=50.0
    )
    await self.backend._pick_plate_j(joint_coords, VerticalAccess())
    self.mock_socket_instance.write.assert_any_call(b"locAngles 1 10.0 20.0 30.0 40.0 50.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"pickplate 1 0 0\n")

  async def test_place_plate_j(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locAngles 1 0.0 10.0 20.0 30.0 40.0 50.0\r\n",  # set_joint_angles
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 placeplate 1 0 0\r\n",  # place_plate_to_stored_position
    ]
    joint_coords = PreciseFlexJointCoords(
      rail=0.0, base=10.0, shoulder=20.0, elbow=30.0, wrist=40.0, gripper=50.0
    )
    await self.backend._place_plate_j(joint_coords, VerticalAccess())
    self.mock_socket_instance.write.assert_any_call(b"locAngles 1 10.0 20.0 30.0 40.0 50.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"placeplate 1 0 0\n")

  async def test_approach_c(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locXyz 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n",  # set_location_xyz
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 locConfig 1 1\r\n",  # set_location_config
      b"0 moveAppro 1 1\r\n",  # move_to_stored_location_appro
    ]
    cartesian_coords = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend._approach_c(cartesian_coords, VerticalAccess())
    self.mock_socket_instance.write.assert_any_call(b"locXyz 1 1.0 2.0 3.0 6.0 5.0 4.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"locConfig 1 1\n")
    self.mock_socket_instance.write.assert_any_call(b"moveAppro 1 1\n")

  async def test_pick_plate_c(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locXyz 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n",  # set_location_xyz
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 locConfig 1 4097\r\n",  # set_location_config
      b"0 1\r\n",  # pick_plate_from_stored_position
    ]
    cartesian_coords = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend._pick_plate_c(cartesian_coords, VerticalAccess())
    self.mock_socket_instance.write.assert_any_call(b"locXyz 1 1.0 2.0 3.0 6.0 5.0 4.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"locConfig 1 4097\n")
    self.mock_socket_instance.write.assert_any_call(b"pickplate 1 0 0\n")

  async def test_place_plate_c(self):
    self.mock_socket_instance.readline.side_effect = [
      b"0 locXyz 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n",  # set_location_xyz
      b"0 StationType 1 1 0 50.0 0.0 0.0\r\n",  # _set_grip_detail
      b"0 locConfig 1 4097\r\n",  # set_location_config
      b"0 placeplate 1 0 0\r\n",  # place_plate_to_stored_position
    ]
    cartesian_coords = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend._place_plate_c(cartesian_coords, VerticalAccess())
    self.mock_socket_instance.write.assert_any_call(b"locXyz 1 1.0 2.0 3.0 6.0 5.0 4.0\n")
    self.mock_socket_instance.write.assert_any_call(b"StationType 1 1 0 100 0 10\n")
    self.mock_socket_instance.write.assert_any_call(b"locConfig 1 4097\n")
    self.mock_socket_instance.write.assert_any_call(b"placeplate 1 0 0\n")

  async def test_set_grip_detail_vertical_access(self):
    self.mock_socket_instance.readline.return_value = b"0 StationType 1 1 0 50.0 0.0 0.0\r\n"
    await self.backend._set_grip_detail(VerticalAccess(clearance_mm=50.0, gripper_offset_mm=0.0))
    self.mock_socket_instance.write.assert_called_with(b"StationType 1 1 0 50.0 0 0.0\n")

  async def test_set_grip_detail_horizontal_access(self):
    self.mock_socket_instance.readline.return_value = b"0 StationType 1 0 0 50.0 100.0 0.0\r\n"
    await self.backend._set_grip_detail(
      HorizontalAccess(clearance_mm=50.0, lift_height_mm=100.0, gripper_offset_mm=0.0)
    )
    self.mock_socket_instance.write.assert_called_with(b"StationType 1 0 0 50.0 100.0 0.0\n")

  async def test_set_grip_detail_invalid_access(self):
    with self.assertRaisesRegex(
      TypeError, "Access pattern must be VerticalAccess or HorizontalAccess."
    ):
      await self.backend._set_grip_detail("invalid")  # type: ignore

  async def test_get_base(self):
    self.mock_socket_instance.readline.return_value = b"0 1.0 2.0 3.0 4.0\r\n"
    x, y, z, z_rotation = await self.backend.get_base()
    self.assertEqual(x, 1.0)
    self.assertEqual(y, 2.0)
    self.assertEqual(z, 3.0)
    self.assertEqual(z_rotation, 4.0)
    self.mock_socket_instance.write.assert_called_with(b"base\n")

  async def test_get_base_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1.0 2.0 3.0\r\n"
    with self.assertRaisesRegex(PreciseFlexError, "Unexpected response format from base command."):
      await self.backend.get_base()

  async def test_set_base(self):
    self.mock_socket_instance.readline.return_value = b"0 base 1.0 2.0 3.0 4.0\r\n"
    await self.backend.set_base(1.0, 2.0, 3.0, 4.0)
    self.mock_socket_instance.write.assert_called_with(b"base 1.0 2.0 3.0 4.0\n")

  async def test_exit(self):
    await self.backend.exit()
    self.mock_socket_instance.write.assert_called_with(b"exit\n")

  async def test_get_power_state(self):
    self.mock_socket_instance.readline.return_value = b"0 1\r\n"
    state = await self.backend.get_power_state()
    self.assertEqual(state, 1)
    self.mock_socket_instance.write.assert_called_with(b"hp\n")

  async def test_set_power(self):
    self.mock_socket_instance.readline.return_value = b"0 hp 1 10\r\n"
    await self.backend.set_power(True, 10)
    self.mock_socket_instance.write.assert_called_with(b"hp 1 10\n")

    self.mock_socket_instance.readline.return_value = b"0 hp 0\r\n"
    await self.backend.set_power(False)
    self.mock_socket_instance.write.assert_called_with(b"hp 0\n")

  async def test_get_mode(self):
    self.mock_socket_instance.readline.return_value = b"0 0\r\n"
    mode = await self.backend.get_mode()
    self.assertEqual(mode, "pc")
    self.mock_socket_instance.write.assert_called_with(b"mode\n")

    self.mock_socket_instance.readline.return_value = b"0 1\r\n"
    mode = await self.backend.get_mode()
    self.assertEqual(mode, "verbose")

  async def test_set_mode(self):
    self.mock_socket_instance.readline.return_value = b"0 mode 0\r\n"
    await self.backend.set_response_mode("pc")
    self.mock_socket_instance.write.assert_called_with(b"mode 0\n")

    self.mock_socket_instance.readline.return_value = b"0 mode 1\r\n"
    await self.backend.set_response_mode("verbose")
    self.mock_socket_instance.write.assert_called_with(b"mode 1\n")

    with self.assertRaisesRegex(ValueError, "Mode must be 'pc' or 'verbose'"):
      await self.backend.set_response_mode("invalid")  # type: ignore

  async def test_get_monitor_speed(self):
    self.mock_socket_instance.readline.return_value = b"0 50\r\n"
    speed = await self.backend.get_monitor_speed()
    self.assertEqual(speed, 50)
    self.mock_socket_instance.write.assert_called_with(b"mspeed\n")

  async def test_set_monitor_speed(self):
    self.mock_socket_instance.readline.return_value = b"0 mspeed 50\r\n"
    await self.backend.set_monitor_speed(50)
    self.mock_socket_instance.write.assert_called_with(b"mspeed 50\n")

    with self.assertRaisesRegex(ValueError, "Speed percent must be between 1 and 100"):
      await self.backend.set_monitor_speed(0)
    with self.assertRaisesRegex(ValueError, "Speed percent must be between 1 and 100"):
      await self.backend.set_monitor_speed(101)

  async def test_nop(self):
    self.mock_socket_instance.readline.return_value = b"0 OK\r\n"
    await self.backend.nop()
    self.mock_socket_instance.write.assert_called_with(b"nop\n")

  async def test_get_payload(self):
    self.mock_socket_instance.readline.return_value = b"0 50\r\n"
    payload = await self.backend.get_payload()
    self.assertEqual(payload, 50)
    self.mock_socket_instance.write.assert_called_with(b"payload\n")

  async def test_set_payload(self):
    self.mock_socket_instance.readline.return_value = b"0 payload 50\r\n"
    await self.backend.set_payload(50)
    self.mock_socket_instance.write.assert_called_with(b"payload 50\n")

    with self.assertRaisesRegex(ValueError, "Payload percent must be between 0 and 100"):
      await self.backend.set_payload(-1)
    with self.assertRaisesRegex(ValueError, "Payload percent must be between 0 and 100"):
      await self.backend.set_payload(101)

  async def test_set_parameter_two_args_int(self):
    self.mock_socket_instance.readline.return_value = b"0 pc 1 10\r\n"
    await self.backend.set_parameter(1, 10)
    self.mock_socket_instance.write.assert_called_with(b"pc 1 10\n")

  async def test_set_parameter_two_args_str(self):
    self.mock_socket_instance.readline.return_value = b'0 pc 1 "test"\r\n'
    await self.backend.set_parameter(1, "test")
    self.mock_socket_instance.write.assert_called_with(b'pc 1 "test"\n')

  async def test_set_parameter_five_args_int(self):
    self.mock_socket_instance.readline.return_value = b"0 pc 1 2 3 4 10\r\n"
    await self.backend.set_parameter(1, 10, 2, 3, 4)
    self.mock_socket_instance.write.assert_called_with(b"pc 1 2 3 4 10\n")

  async def test_set_parameter_five_args_str(self):
    self.mock_socket_instance.readline.return_value = b'0 pc 1 2 3 4 "test"\r\n'
    await self.backend.set_parameter(1, "test", 2, 3, 4)
    self.mock_socket_instance.write.assert_called_with(b'pc 1 2 3 4 "test"\n')

  async def test_get_parameter_one_arg(self):
    self.mock_socket_instance.readline.return_value = b"0 10\r\n"
    value = await self.backend.get_parameter(1)
    self.assertEqual(value, "10")
    self.mock_socket_instance.write.assert_called_with(b"pd 1\n")

  async def test_get_parameter_two_args(self):
    self.mock_socket_instance.readline.return_value = b"0 10\r\n"
    value = await self.backend.get_parameter(1, 2)
    self.assertEqual(value, "10")
    self.mock_socket_instance.write.assert_called_with(b"pd 1 2\n")

  async def test_get_parameter_three_args(self):
    self.mock_socket_instance.readline.return_value = b"0 10\r\n"
    value = await self.backend.get_parameter(1, 2, 3)
    self.assertEqual(value, "10")
    self.mock_socket_instance.write.assert_called_with(b"pd 1 2 3\n")

  async def test_get_parameter_four_args(self):
    self.mock_socket_instance.readline.return_value = b"0 10\r\n"
    value = await self.backend.get_parameter(1, 2, 3, 4)
    self.assertEqual(value, "10")
    self.mock_socket_instance.write.assert_called_with(b"pd 1 2 3 4\n")

  async def test_reset(self):
    self.mock_socket_instance.readline.return_value = b"0 reset 1\r\n"
    await self.backend.reset(1)
    self.mock_socket_instance.write.assert_called_with(b"reset 1\n")

    with self.assertRaisesRegex(ValueError, "Robot number must be greater than zero"):
      await self.backend.reset(0)
    with self.assertRaisesRegex(ValueError, "Robot number must be greater than zero"):
      await self.backend.reset(-1)

  async def test_get_selected_robot(self):
    self.mock_socket_instance.readline.return_value = b"0 1\r\n"
    robot_id = await self.backend.get_selected_robot()
    self.assertEqual(robot_id, 1)
    self.mock_socket_instance.write.assert_called_with(b"selectRobot\n")

  async def test_select_robot(self):
    self.mock_socket_instance.readline.return_value = b"0 selectRobot 1\r\n"
    await self.backend.select_robot(1)
    self.mock_socket_instance.write.assert_called_with(b"selectRobot 1\n")

  async def test_get_signal(self):
    self.mock_socket_instance.readline.return_value = b"0 1 0\r\n"
    signal_value = await self.backend.get_signal(1)
    self.assertEqual(signal_value, 0)
    self.mock_socket_instance.write.assert_called_with(b"sig 1\n")

  async def test_set_signal(self):
    self.mock_socket_instance.readline.return_value = b"0 sig 1 1\r\n"
    await self.backend.set_signal(1, 1)
    self.mock_socket_instance.write.assert_called_with(b"sig 1 1\n")

  async def test_get_system_state(self):
    self.mock_socket_instance.readline.return_value = b"0 123\r\n"
    state = await self.backend.get_system_state()
    self.assertEqual(state, 123)
    self.mock_socket_instance.write.assert_called_with(b"sysState\n")

  async def test_get_tool(self):
    self.mock_socket_instance.readline.return_value = b"0 1.0 2.0 3.0 4.0 5.0 6.0\r\n"
    x, y, z, yaw, pitch, roll = await self.backend.get_tool_transformation_values()
    self.assertEqual(x, 1.0)
    self.assertEqual(y, 2.0)
    self.assertEqual(z, 3.0)
    self.assertEqual(yaw, 4.0)
    self.assertEqual(pitch, 5.0)
    self.assertEqual(roll, 6.0)
    self.mock_socket_instance.write.assert_called_with(b"tool\n")

  async def test_get_tool_with_prefix(self):
    self.mock_socket_instance.readline.return_value = b"0 tool: 1.0 2.0 3.0 4.0 5.0 6.0\r\n"
    x, y, z, yaw, pitch, roll = await self.backend.get_tool_transformation_values()
    self.assertEqual(x, 1.0)
    self.assertEqual(y, 2.0)
    self.assertEqual(z, 3.0)
    self.assertEqual(yaw, 4.0)
    self.assertEqual(pitch, 5.0)
    self.assertEqual(roll, 6.0)
    self.mock_socket_instance.write.assert_called_with(b"tool\n")

  async def test_get_tool_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1.0 2.0 3.0 4.0 5.0\r\n"
    with self.assertRaisesRegex(PreciseFlexError, "Unexpected response format from tool command."):
      await self.backend.get_tool_transformation_values()

  async def test_set_tool(self):
    self.mock_socket_instance.readline.return_value = b"0 tool 1.0 2.0 3.0 4.0 5.0 6.0\r\n"
    await self.backend.set_tool_transformation_values(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    self.mock_socket_instance.write.assert_called_with(b"tool 1.0 2.0 3.0 4.0 5.0 6.0\n")

  async def test_get_location_angles(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 10.0 20.0 30.0 40.0 50.0 60.0\r\n"  # type_code, station_index, 5 values for base, shoulder, elbow, wrist, gripper
    type_code, station_index, a1, a2, a3, a4, a5, a6 = await self.backend.get_location_angles(1)
    self.assertEqual(type_code, 1)
    self.assertEqual(station_index, 1)
    self.assertEqual(a1, 0.0)  # rail
    self.assertEqual(a2, 10.0)  # base
    self.assertEqual(a3, 20.0)
    self.assertEqual(a4, 30.0)
    self.assertEqual(a5, 40.0)
    self.assertEqual(a6, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"locAngles 1\n")

  async def test_get_location_angles_invalid_type(self):
    self.mock_socket_instance.readline.return_value = b"0 0 1 0.0 10.0 20.0 30.0 40.0 50.0\r\n"
    with self.assertRaisesRegex(PreciseFlexError, "Location is not of angles type."):
      await self.backend.get_location_angles(1)

  async def test_set_joint_angles_no_rail(self):
    self.mock_socket_instance.readline.return_value = b"0 locAngles 1 10.0 20.0 30.0 40.0 50.0\r\n"
    joint_coords = PreciseFlexJointCoords(
      rail=0.0, base=10.0, shoulder=20.0, elbow=30.0, wrist=40.0, gripper=50.0
    )
    await self.backend.set_joint_angles(1, joint_coords)
    self.mock_socket_instance.write.assert_called_with(b"locAngles 1 10.0 20.0 30.0 40.0 50.0\n")

  async def test_set_joint_angles_with_rail(self):
    backend_with_rail = PreciseFlexBackend(has_rail=True, host="localhost", port=10100)
    backend_with_rail.io = self.mock_socket_instance
    self.mock_socket_instance.readline.return_value = (
      b"0 locAngles 1 0.0 10.0 20.0 30.0 40.0 50.0\r\n"
    )
    joint_coords = PreciseFlexJointCoords(
      rail=0.0, base=10.0, shoulder=20.0, elbow=30.0, wrist=40.0, gripper=50.0
    )
    await backend_with_rail.set_joint_angles(1, joint_coords)
    self.mock_socket_instance.write.assert_called_with(
      b"locAngles 1 0.0 10.0 20.0 30.0 40.0 50.0\n"
    )

  async def test_get_location_xyz(self):
    self.mock_socket_instance.readline.return_value = b"0 0 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n"
    type_code, station_index, x, y, z, yaw, pitch, roll = await self.backend.get_location_xyz(1)
    self.assertEqual(type_code, 0)
    self.assertEqual(station_index, 1)
    self.assertEqual(x, 1.0)
    self.assertEqual(y, 2.0)
    self.assertEqual(z, 3.0)
    self.assertEqual(yaw, 4.0)
    self.assertEqual(pitch, 5.0)
    self.assertEqual(roll, 6.0)
    self.mock_socket_instance.write.assert_called_with(b"locXyz 1\n")

  async def test_get_location_xyz_invalid_type(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n"
    with self.assertRaisesRegex(PreciseFlexError, "Location is not of Cartesian type."):
      await self.backend.get_location_xyz(1)

  async def test_get_location_xyz_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 0 1 1.0 2.0 3.0 4.0 5.0\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from locXyz command."
    ):
      await self.backend.get_location_xyz(1)

  async def test_set_location_xyz(self):
    self.mock_socket_instance.readline.return_value = b"0 locXyz 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n"
    cartesian_coords = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend.set_location_xyz(1, cartesian_coords)
    self.mock_socket_instance.write.assert_called_with(b"locXyz 1 1.0 2.0 3.0 6.0 5.0 4.0\n")

  async def test_get_location_z_clearance(self):
    self.mock_socket_instance.readline.return_value = b"0 1 50.0 1\r\n"
    station_index, z_clearance, z_world = await self.backend.get_location_z_clearance(1)
    self.assertEqual(station_index, 1)
    self.assertEqual(z_clearance, 50.0)
    self.assertTrue(z_world)
    self.mock_socket_instance.write.assert_called_with(b"locZClearance 1\n")

  async def test_get_location_z_clearance_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1 50.0\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from locZClearance command."
    ):
      await self.backend.get_location_z_clearance(1)

  async def test_set_location_z_clearance(self):
    self.mock_socket_instance.readline.return_value = b"0 locZClearance 1 50.0\r\n"
    await self.backend.set_location_z_clearance(1, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"locZClearance 1 50.0\n")

    self.mock_socket_instance.readline.return_value = b"0 locZClearance 1 50.0 1\r\n"
    await self.backend.set_location_z_clearance(1, 50.0, True)
    self.mock_socket_instance.write.assert_called_with(b"locZClearance 1 50.0 1\n")

  async def test_get_location_config(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1\r\n"
    station_index, config_value = await self.backend.get_location_config(1)
    self.assertEqual(station_index, 1)
    self.assertEqual(config_value, 1)
    self.mock_socket_instance.write.assert_called_with(b"locConfig 1\n")

  async def test_get_location_config_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from locConfig command."
    ):
      await self.backend.get_location_config(1)

  async def test_set_location_config(self):
    self.mock_socket_instance.readline.return_value = b"0 locConfig 1 1\r\n"
    await self.backend.set_location_config(1, 1)
    self.mock_socket_instance.write.assert_called_with(b"locConfig 1 1\n")

    with self.assertRaisesRegex(ValueError, "Invalid config bits specified: 0x8000"):
      await self.backend.set_location_config(1, 0x8000)
    with self.assertRaisesRegex(ValueError, "Cannot specify both GPL_Righty and GPL_Lefty"):
      await self.backend.set_location_config(1, 0x01 | 0x02)
    with self.assertRaisesRegex(ValueError, "Cannot specify both GPL_Above and GPL_Below"):
      await self.backend.set_location_config(1, 0x04 | 0x08)
    with self.assertRaisesRegex(ValueError, "Cannot specify both GPL_Flip and GPL_NoFlip"):
      await self.backend.set_location_config(1, 0x10 | 0x20)

  async def test_dest_c(self):
    self.mock_socket_instance.readline.return_value = b"0 1.0 2.0 3.0 4.0 5.0 6.0 1\r\n"
    x, y, z, yaw, pitch, roll, config = await self.backend.dest_c()
    self.assertEqual(x, 1.0)
    self.assertEqual(y, 2.0)
    self.assertEqual(z, 3.0)
    self.assertEqual(yaw, 4.0)
    self.assertEqual(pitch, 5.0)
    self.assertEqual(roll, 6.0)
    self.assertEqual(config, 1)
    self.mock_socket_instance.write.assert_called_with(b"destC\n")

    self.mock_socket_instance.readline.return_value = b"0 1.0 2.0 3.0 4.0 5.0 6.0 1\r\n"
    x, y, z, yaw, pitch, roll, config = await self.backend.dest_c(1)
    self.assertEqual(x, 1.0)
    self.assertEqual(y, 2.0)
    self.assertEqual(z, 3.0)
    self.assertEqual(yaw, 4.0)
    self.assertEqual(pitch, 5.0)
    self.assertEqual(roll, 6.0)
    self.assertEqual(config, 1)
    self.mock_socket_instance.write.assert_called_with(b"destC 1\n")

  async def test_dest_c_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1.0 2.0 3.0 4.0 5.0 6.0\r\n"
    with self.assertRaisesRegex(PreciseFlexError, "Unexpected response format from destC command."):
      await self.backend.dest_c()

  async def test_dest_j(self):
    self.mock_socket_instance.readline.return_value = (
      b"0 10.0 20.0 30.0 40.0 50.0\r\n"  # 5 values for base, shoulder, elbow, wrist, gripper
    )
    a1, a2, a3, a4, a5, a6 = await self.backend.dest_j()
    self.assertEqual(a1, 0.0)  # rail
    self.assertEqual(a2, 10.0)  # base
    self.assertEqual(a3, 20.0)
    self.assertEqual(a4, 30.0)
    self.assertEqual(a5, 40.0)
    self.assertEqual(a6, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"destJ\n")

    self.mock_socket_instance.readline.return_value = (
      b"0 10.0 20.0 30.0 40.0 50.0\r\n"  # 5 values for base, shoulder, elbow, wrist, gripper
    )
    a1, a2, a3, a4, a5, a6 = await self.backend.dest_j(1)
    self.assertEqual(a1, 0.0)  # rail
    self.assertEqual(a2, 10.0)  # base
    self.assertEqual(a3, 20.0)
    self.assertEqual(a4, 30.0)
    self.assertEqual(a5, 40.0)
    self.assertEqual(a6, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"destJ 1\n")

  async def test_dest_j_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0\r\n"
    with self.assertRaisesRegex(PreciseFlexError, "Unexpected response format from destJ command."):
      await self.backend.dest_j()

  async def test_here_j(self):
    self.mock_socket_instance.readline.return_value = b"0 hereJ 1\r\n"
    await self.backend.here_j(1)
    self.mock_socket_instance.write.assert_called_with(b"hereJ 1\n")

  async def test_here_c(self):
    self.mock_socket_instance.readline.return_value = b"0 hereC 1\r\n"
    await self.backend.here_c(1)
    self.mock_socket_instance.write.assert_called_with(b"hereC 1\n")

  async def test_get_profile_speed2(self):
    self.mock_socket_instance.readline.return_value = b"0 1 50.0\r\n"
    speed2 = await self.backend.get_profile_speed2(1)
    self.assertEqual(speed2, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"Speed2 1\n")

  async def test_set_profile_speed2(self):
    self.mock_socket_instance.readline.return_value = b"0 Speed2 1 50.0\r\n"
    await self.backend.set_profile_speed2(1, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"Speed2 1 50.0\n")

  async def test_get_profile_accel(self):
    self.mock_socket_instance.readline.return_value = b"0 1 50.0\r\n"
    accel = await self.backend.get_profile_accel(1)
    self.assertEqual(accel, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"Accel 1\n")

  async def test_set_profile_accel(self):
    self.mock_socket_instance.readline.return_value = b"0 Accel 1 50.0\r\n"
    await self.backend.set_profile_accel(1, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"Accel 1 50.0\n")

  async def test_get_profile_accel_ramp(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1.0\r\n"
    accel_ramp = await self.backend.get_profile_accel_ramp(1)
    self.assertEqual(accel_ramp, 1.0)
    self.mock_socket_instance.write.assert_called_with(b"AccRamp 1\n")

  async def test_set_profile_accel_ramp(self):
    self.mock_socket_instance.readline.return_value = b"0 AccRamp 1 1.0\r\n"
    await self.backend.set_profile_accel_ramp(1, 1.0)
    self.mock_socket_instance.write.assert_called_with(b"AccRamp 1 1.0\n")

  async def test_get_profile_decel(self):
    self.mock_socket_instance.readline.return_value = b"0 1 50.0\r\n"
    decel = await self.backend.get_profile_decel(1)
    self.assertEqual(decel, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"Decel 1\n")

  async def test_set_profile_decel(self):
    self.mock_socket_instance.readline.return_value = b"0 Decel 1 50.0\r\n"
    await self.backend.set_profile_decel(1, 50.0)
    self.mock_socket_instance.write.assert_called_with(b"Decel 1 50.0\n")

  async def test_get_profile_decel_ramp(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1.0\r\n"
    decel_ramp = await self.backend.get_profile_decel_ramp(1)
    self.assertEqual(decel_ramp, 1.0)
    self.mock_socket_instance.write.assert_called_with(b"DecRamp 1\n")

  async def test_set_profile_decel_ramp(self):
    self.mock_socket_instance.readline.return_value = b"0 DecRamp 1 1.0\r\n"
    await self.backend.set_profile_decel_ramp(1, 1.0)
    self.mock_socket_instance.write.assert_called_with(b"DecRamp 1 1.0\n")

  async def test_get_profile_in_range(self):
    self.mock_socket_instance.readline.return_value = b"0 1 10.0\r\n"
    in_range = await self.backend.get_profile_in_range(1)
    self.assertEqual(in_range, 10.0)
    self.mock_socket_instance.write.assert_called_with(b"InRange 1\n")

  async def test_set_profile_in_range(self):
    self.mock_socket_instance.readline.return_value = b"0 InRange 1 10.0\r\n"
    await self.backend.set_profile_in_range(1, 10.0)
    self.mock_socket_instance.write.assert_called_with(b"InRange 1 10.0\n")

    with self.assertRaisesRegex(ValueError, "InRange value must be between -1 and 100"):
      await self.backend.set_profile_in_range(1, -2)
    with self.assertRaisesRegex(ValueError, "InRange value must be between -1 and 100"):
      await self.backend.set_profile_in_range(1, 101)

  async def test_get_profile_straight(self):
    self.mock_socket_instance.readline.return_value = b"0 1 True\r\n"
    straight = await self.backend.get_profile_straight(1)
    self.assertTrue(straight)
    self.mock_socket_instance.write.assert_called_with(b"Straight 1\n")

    self.mock_socket_instance.readline.return_value = b"0 1 False\r\n"
    straight = await self.backend.get_profile_straight(1)
    self.assertFalse(straight)

  async def test_set_profile_straight(self):
    self.mock_socket_instance.readline.return_value = b"0 Straight 1 1\r\n"
    await self.backend.set_profile_straight(1, True)
    self.mock_socket_instance.write.assert_called_with(b"Straight 1 1\n")

    self.mock_socket_instance.readline.return_value = b"0 Straight 1 0\r\n"
    await self.backend.set_profile_straight(1, False)
    self.mock_socket_instance.write.assert_called_with(b"Straight 1 0\n")

  async def test_set_motion_profile_values(self):
    self.mock_socket_instance.readline.return_value = (
      b"0 Profile 1 100.0 0.0 100.0 100.0 0.0 0.0 0.0 -1\r\n"
    )
    await self.backend.set_motion_profile_values(1, 100.0, 0.0, 100.0, 100.0, 0.0, 0.0, 0.0, True)
    self.mock_socket_instance.write.assert_called_with(
      b"Profile 1 100.0 0.0 100.0 100.0 0.0 0.0 0.0 -1\n"
    )

    with self.assertRaisesRegex(ValueError, "Speed must be > 0 \\(percent\\)."):
      await self.backend.set_motion_profile_values(1, -1.0, 0.0, 100.0, 100.0, 0.0, 0.0, 0.0, True)
    with self.assertRaisesRegex(ValueError, "Speed2 must be > 0 \\(percent\\)."):
      await self.backend.set_motion_profile_values(
        1, 100.0, -1.0, 100.0, 100.0, 0.0, 0.0, 0.0, True
      )
    with self.assertRaisesRegex(
      ValueError, "Acceleration must be between 0 and 100 \\(percent\\)."
    ):
      await self.backend.set_motion_profile_values(1, 100.0, 0.0, -1.0, 100.0, 0.0, 0.0, 0.0, True)
    with self.assertRaisesRegex(
      ValueError, "Acceleration must be between 0 and 100 \\(percent\\)."
    ):
      await self.backend.set_motion_profile_values(1, 100.0, 0.0, 101.0, 100.0, 0.0, 0.0, 0.0, True)
    with self.assertRaisesRegex(
      ValueError, "Deceleration must be between 0 and 100 \\(percent\\)."
    ):
      await self.backend.set_motion_profile_values(1, 100.0, 0.0, 100.0, -1.0, 0.0, 0.0, 0.0, True)
    with self.assertRaisesRegex(
      ValueError, "Deceleration must be between 0 and 100 \\(percent\\)."
    ):
      await self.backend.set_motion_profile_values(1, 100.0, 0.0, 100.0, 101.0, 0.0, 0.0, 0.0, True)
    with self.assertRaisesRegex(ValueError, "Acceleration ramp must be >= 0 \\(seconds\\)."):
      await self.backend.set_motion_profile_values(
        1, 100.0, 0.0, 100.0, 100.0, -1.0, 0.0, 0.0, True
      )
    with self.assertRaisesRegex(ValueError, "Deceleration ramp must be >= 0 \\(seconds\\)."):
      await self.backend.set_motion_profile_values(
        1, 100.0, 0.0, 100.0, 100.0, 0.0, -1.0, 0.0, True
      )
    with self.assertRaisesRegex(ValueError, "InRange must be between -1 and 100."):
      await self.backend.set_motion_profile_values(
        1, 100.0, 0.0, 100.0, 100.0, 0.0, 0.0, -2.0, True
      )
    with self.assertRaisesRegex(ValueError, "InRange must be between -1 and 100."):
      await self.backend.set_motion_profile_values(
        1, 100.0, 0.0, 100.0, 100.0, 0.0, 0.0, 101.0, True
      )

  async def test_get_motion_profile_values(self):
    self.mock_socket_instance.readline.return_value = (
      b"0 1 100.0 0.0 100.0 100.0 0.0 0.0 0.0 -1\r\n"
    )
    (
      profile,
      speed,
      speed2,
      accel,
      decel,
      accel_ramp,
      decel_ramp,
      in_range,
      straight,
    ) = await self.backend.get_motion_profile_values(1)
    self.assertEqual(profile, 1)
    self.assertEqual(speed, 100.0)
    self.assertEqual(speed2, 0.0)
    self.assertEqual(accel, 100.0)
    self.assertEqual(decel, 100.0)
    self.assertEqual(accel_ramp, 0.0)
    self.assertEqual(decel_ramp, 0.0)
    self.assertEqual(in_range, 0.0)
    self.assertTrue(straight)
    self.mock_socket_instance.write.assert_called_with(b"Profile 1\n")

    self.mock_socket_instance.readline.return_value = b"0 1 100.0 0.0 100.0 100.0 0.0 0.0 0.0 0\r\n"
    (
      profile,
      speed,
      speed2,
      accel,
      decel,
      accel_ramp,
      decel_ramp,
      in_range,
      straight,
    ) = await self.backend.get_motion_profile_values(1)
    self.assertFalse(straight)

  async def test_get_motion_profile_values_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1 100.0 0.0 100.0 100.0 0.0 0.0 0.0\r\n"
    with self.assertRaisesRegex(PreciseFlexError, "Unexpected response format from device."):
      await self.backend.get_motion_profile_values(1)

  async def test_move_to_stored_location(self):
    self.mock_socket_instance.readline.return_value = b"0 move 1 1\r\n"
    await self.backend.move_to_stored_location(1, 1)
    self.mock_socket_instance.write.assert_called_with(b"move 1 1\n")

  async def test_move_extra_axis_one_axis(self):
    self.mock_socket_instance.readline.return_value = b"0 moveExtraAxis 1.0\r\n"
    await self.backend.move_extra_axis(1.0)
    self.mock_socket_instance.write.assert_called_with(b"moveExtraAxis 1.0\n")

  async def test_move_extra_axis_two_axes(self):
    self.mock_socket_instance.readline.return_value = b"0 moveExtraAxis 1.0 2.0\r\n"
    await self.backend.move_extra_axis(1.0, 2.0)
    self.mock_socket_instance.write.assert_called_with(b"moveExtraAxis 1.0 2.0\n")

  async def test_move_one_axis(self):
    self.mock_socket_instance.readline.return_value = b"0 moveOneAxis 1 10.0 1\r\n"
    await self.backend.move_one_axis(1, 10.0, 1)
    self.mock_socket_instance.write.assert_called_with(b"moveOneAxis 1 10.0 1\n")

  async def test_release_brake(self):
    self.mock_socket_instance.readline.return_value = b"0 releaseBrake 1\r\n"
    await self.backend.release_brake(1)
    self.mock_socket_instance.write.assert_called_with(b"releaseBrake 1\n")

  async def test_set_brake(self):
    self.mock_socket_instance.readline.return_value = b"0 setBrake 1\r\n"
    await self.backend.set_brake(1)
    self.mock_socket_instance.write.assert_called_with(b"setBrake 1\n")

  async def test_state(self):
    self.mock_socket_instance.readline.return_value = b"0 IDLE\r\n"
    state = await self.backend.state()
    self.assertEqual(state, "IDLE")
    self.mock_socket_instance.write.assert_called_with(b"state\n")

  async def test_wait_for_eom(self):
    self.mock_socket_instance.readline.return_value = b"0 waitForEom\r\n"
    await self.backend.wait_for_eom()
    self.mock_socket_instance.write.assert_called_with(b"waitForEom\n")

  async def test_zero_torque_enable(self):
    self.mock_socket_instance.readline.return_value = b"0 zeroTorque 1 1\r\n"
    await self.backend.zero_torque(True, 1)
    self.mock_socket_instance.write.assert_called_with(b"zeroTorque 1 1\n")

    with self.assertRaisesRegex(AssertionError, "axis_mask must be greater than 0"):
      await self.backend.zero_torque(True, 0)

  async def test_zero_torque_disable(self):
    self.mock_socket_instance.readline.return_value = b"0 zeroTorque 0\r\n"
    await self.backend.zero_torque(False)
    self.mock_socket_instance.write.assert_called_with(b"zeroTorque 0\n")

  async def test_change_config(self):
    self.mock_socket_instance.readline.return_value = b"0 ChangeConfig 0\r\n"
    await self.backend.change_config()
    self.mock_socket_instance.write.assert_called_with(b"ChangeConfig 0\n")

    self.mock_socket_instance.readline.return_value = b"0 ChangeConfig 1\r\n"
    await self.backend.change_config(1)
    self.mock_socket_instance.write.assert_called_with(b"ChangeConfig 1\n")

  async def test_change_config2(self):
    self.mock_socket_instance.readline.return_value = b"0 ChangeConfig2 0\r\n"
    await self.backend.change_config2()
    self.mock_socket_instance.write.assert_called_with(b"ChangeConfig2 0\n")

    self.mock_socket_instance.readline.return_value = b"0 ChangeConfig2 1\r\n"
    await self.backend.change_config2(1)
    self.mock_socket_instance.write.assert_called_with(b"ChangeConfig2 1\n")

  async def test_get_grasp_data(self):
    self.mock_socket_instance.readline.return_value = b"0 10.0 50.0 10.0\r\n"
    plate_width, finger_speed, grasp_force = await self.backend.get_grasp_data()
    self.assertEqual(plate_width, 10.0)
    self.assertEqual(finger_speed, 50.0)
    self.assertEqual(grasp_force, 10.0)
    self.mock_socket_instance.write.assert_called_with(b"GraspData\n")

  async def test_get_grasp_data_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 10.0 50.0\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from GraspData command."
    ):
      await self.backend.get_grasp_data()

  async def test_set_grasp_data(self):
    self.mock_socket_instance.readline.return_value = b"0 GraspData 10.0 50.0 10.0\r\n"
    await self.backend.set_grasp_data(10.0, 50.0, 10.0)
    self.mock_socket_instance.write.assert_called_with(b"GraspData 10.0 50.0 10.0\n")

  async def test_get_grip_close_pos(self):
    self.mock_socket_instance.readline.return_value = b"0 10.0\r\n"
    pos = await self.backend._get_grip_close_pos()
    self.assertEqual(pos, 10.0)
    self.mock_socket_instance.write.assert_called_with(b"GripClosePos\n")

  async def test_set_grip_close_pos(self):
    self.mock_socket_instance.readline.return_value = b"0 GripClosePos 10.0\r\n"
    await self.backend._set_grip_close_pos(10.0)
    self.mock_socket_instance.write.assert_called_with(b"GripClosePos 10.0\n")

  async def test_get_grip_open_pos(self):
    self.mock_socket_instance.readline.return_value = b"0 20.0\r\n"
    pos = await self.backend._get_grip_open_pos()
    self.assertEqual(pos, 20.0)
    self.mock_socket_instance.write.assert_called_with(b"GripOpenPos\n")

  async def test_set_grip_open_pos(self):
    self.mock_socket_instance.readline.return_value = b"0 GripOpenPos 20.0\r\n"
    await self.backend._set_grip_open_pos(20.0)
    self.mock_socket_instance.write.assert_called_with(b"GripOpenPos 20.0\n")

  async def test_move_rail_no_args(self):
    self.mock_socket_instance.readline.return_value = b"0 MoveRail 0\r\n"
    await self.backend.move_rail(mode=0)
    self.mock_socket_instance.write.assert_called_with(b"MoveRail 0\n")

  async def test_move_rail_station_id_and_mode(self):
    self.mock_socket_instance.readline.return_value = b"0 MoveRail 1 1\r\n"
    await self.backend.move_rail(station_id=1, mode=1)
    self.mock_socket_instance.write.assert_called_with(b"MoveRail 1 1\n")

  async def test_move_rail_all_args(self):
    self.mock_socket_instance.readline.return_value = b"0 MoveRail 1 1 100.0\r\n"
    await self.backend.move_rail(station_id=1, mode=1, rail_destination=100.0)
    self.mock_socket_instance.write.assert_called_with(b"MoveRail 1 1 100.0\n")

  async def test_get_pallet_index(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 2 3\r\n"
    station_id, x, y, z = await self.backend.get_pallet_index(1)
    self.assertEqual(station_id, 1)
    self.assertEqual(x, 1)
    self.assertEqual(y, 2)
    self.assertEqual(z, 3)
    self.mock_socket_instance.write.assert_called_with(b"PalletIndex 1\n")

  async def test_get_pallet_index_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 2\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from PalletIndex command."
    ):
      await self.backend.get_pallet_index(1)

  async def test_set_pallet_index(self):
    self.mock_socket_instance.readline.return_value = b"0 PalletIndex 1 1 2 3\r\n"
    await self.backend.set_pallet_index(1, 1, 2, 3)
    self.mock_socket_instance.write.assert_called_with(b"PalletIndex 1 1 2 3\n")

    with self.assertRaisesRegex(ValueError, "Pallet index X cannot be negative"):
      await self.backend.set_pallet_index(1, -1, 2, 3)
    with self.assertRaisesRegex(ValueError, "Pallet index Y cannot be negative"):
      await self.backend.set_pallet_index(1, 1, -1, 3)
    with self.assertRaisesRegex(ValueError, "Pallet index Z cannot be negative"):
      await self.backend.set_pallet_index(1, 1, 2, -1)

  async def test_get_pallet_origin(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1.0 2.0 3.0 4.0 5.0 6.0 1\r\n"
    station_id, x, y, z, yaw, pitch, roll, config = await self.backend.get_pallet_origin(1)
    self.assertEqual(station_id, 1)
    self.assertEqual(x, 1.0)
    self.assertEqual(y, 2.0)
    self.assertEqual(z, 3.0)
    self.assertEqual(yaw, 4.0)
    self.assertEqual(pitch, 5.0)
    self.assertEqual(roll, 6.0)
    self.assertEqual(config, 1)
    self.mock_socket_instance.write.assert_called_with(b"PalletOrigin 1\n")

  async def test_get_pallet_origin_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1.0 2.0 3.0 4.0 5.0 6.0\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from PalletOrigin command."
    ):
      await self.backend.get_pallet_origin(1)

  async def test_set_pallet_origin(self):
    self.mock_socket_instance.readline.return_value = (
      b"0 PalletOrigin 1 1.0 2.0 3.0 4.0 5.0 6.0 1\r\n"
    )
    position = PreciseFlexCartesianCoords(
      location=Coordinate(1.0, 2.0, 3.0),
      rotation=Rotation(4.0, 5.0, 6.0),
      orientation=ElbowOrientation.RIGHT,
    )
    await self.backend.set_pallet_origin(1, position)
    self.mock_socket_instance.write.assert_called_with(
      b"PalletOrigin 1 1.0 2.0 3.0 6.0 5.0 4.0 1\n"
    )

  async def test_get_pallet_x(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 1.0 2.0 3.0\r\n"
    station_id, x_count, wx, wy, wz = await self.backend.get_pallet_x(1)
    self.assertEqual(station_id, 1)
    self.assertEqual(x_count, 1)
    self.assertEqual(wx, 1.0)
    self.assertEqual(wy, 2.0)
    self.assertEqual(wz, 3.0)
    self.mock_socket_instance.write.assert_called_with(b"PalletX 1\n")

  async def test_get_pallet_x_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 1.0 2.0\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from PalletX command."
    ):
      await self.backend.get_pallet_x(1)

  async def test_set_pallet_x(self):
    self.mock_socket_instance.readline.return_value = b"0 PalletX 1 1 1.0 2.0 3.0\r\n"
    await self.backend.set_pallet_x(1, 1, 1.0, 2.0, 3.0)
    self.mock_socket_instance.write.assert_called_with(b"PalletX 1 1 1.0 2.0 3.0\n")

  async def test_get_pallet_y(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 1.0 2.0 3.0\r\n"
    station_id, y_count, wx, wy, wz = await self.backend.get_pallet_y(1)
    self.assertEqual(station_id, 1)
    self.assertEqual(y_count, 1)
    self.assertEqual(wx, 1.0)
    self.assertEqual(wy, 2.0)
    self.assertEqual(wz, 3.0)
    self.mock_socket_instance.write.assert_called_with(b"PalletY 1\n")

  async def test_get_pallet_y_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 1.0 2.0\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from PalletY command."
    ):
      await self.backend.get_pallet_y(1)

  async def test_set_pallet_y(self):
    self.mock_socket_instance.readline.return_value = b"0 PalletY 1 1 1.0 2.0 3.0\r\n"
    await self.backend.set_pallet_y(1, 1, 1.0, 2.0, 3.0)
    self.mock_socket_instance.write.assert_called_with(b"PalletY 1 1 1.0 2.0 3.0\n")

  async def test_get_pallet_z(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 1.0 2.0 3.0\r\n"
    station_id, z_count, wx, wy, wz = await self.backend.get_pallet_z(1)
    self.assertEqual(station_id, 1)
    self.assertEqual(z_count, 1)
    self.assertEqual(wx, 1.0)
    self.assertEqual(wy, 2.0)
    self.assertEqual(wz, 3.0)
    self.mock_socket_instance.write.assert_called_with(b"PalletZ 1\n")

  async def test_get_pallet_z_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1 1 1.0 2.0\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from PalletZ command."
    ):
      await self.backend.get_pallet_z(1)

  async def test_set_pallet_z(self):
    self.mock_socket_instance.readline.return_value = b"0 PalletZ 1 1 1.0 2.0 3.0\r\n"
    await self.backend.set_pallet_z(1, 1, 1.0, 2.0, 3.0)
    self.mock_socket_instance.write.assert_called_with(b"PalletZ 1 1 1.0 2.0 3.0\n")

  async def test_pick_plate_station(self):
    self.mock_socket_instance.readline.return_value = b"0 1\r\n"
    result = await self.backend.pick_plate_station(1)
    self.assertTrue(result)
    self.mock_socket_instance.write.assert_called_with(b"PickPlate 1 0 0\n")

    self.mock_socket_instance.readline.return_value = b"0 0\r\n"
    result = await self.backend.pick_plate_station(
      1, horizontal_compliance=True, horizontal_compliance_torque=10
    )
    self.assertFalse(result)
    self.mock_socket_instance.write.assert_called_with(b"PickPlate 1 1 10\n")

  async def test_place_plate_station(self):
    self.mock_socket_instance.readline.return_value = b"0 PlacePlate 1 0 0\r\n"
    await self.backend.place_plate_station(1)
    self.mock_socket_instance.write.assert_called_with(b"PlacePlate 1 0 0\n")

    self.mock_socket_instance.readline.return_value = b"0 PlacePlate 1 1 10\r\n"
    await self.backend.place_plate_station(
      1, horizontal_compliance=True, horizontal_compliance_torque=10
    )
    self.mock_socket_instance.write.assert_called_with(b"PlacePlate 1 1 10\n")

  async def test_get_rail_position(self):
    self.mock_socket_instance.readline.return_value = b"0 100.0\r\n"
    pos = await self.backend.get_rail_position(1)
    self.assertEqual(pos, 100.0)
    self.mock_socket_instance.write.assert_called_with(b"Rail 1\n")

  async def test_set_rail_position(self):
    self.mock_socket_instance.readline.return_value = b"0 Rail 1 100.0\r\n"
    await self.backend.set_rail_position(1, 100.0)
    self.mock_socket_instance.write.assert_called_with(b"Rail 1 100.0\n")

  async def test_teach_plate_station(self):
    self.mock_socket_instance.readline.return_value = b"0 TeachPlate 1 50.0\r\n"
    await self.backend.teach_plate_station(1)
    self.mock_socket_instance.write.assert_called_with(b"TeachPlate 1 50.0\n")

    self.mock_socket_instance.readline.return_value = b"0 TeachPlate 1 100.0\r\n"
    await self.backend.teach_plate_station(1, 100.0)
    self.mock_socket_instance.write.assert_called_with(b"TeachPlate 1 100.0\n")

  async def test_get_station_type(self):
    self.mock_socket_instance.readline.return_value = b"0 1 0 0 50.0 0.0 0.0\r\n"
    (
      station_id,
      access_type,
      location_type,
      z_clearance,
      z_above,
      z_grasp_offset,
    ) = await self.backend.get_station_type(1)
    self.assertEqual(station_id, 1)
    self.assertEqual(access_type, 0)
    self.assertEqual(location_type, 0)
    self.assertEqual(z_clearance, 50.0)
    self.assertEqual(z_above, 0.0)
    self.assertEqual(z_grasp_offset, 0.0)
    self.mock_socket_instance.write.assert_called_with(b"StationType 1\n")

  async def test_get_station_type_invalid_response(self):
    self.mock_socket_instance.readline.return_value = b"0 1 0 0 50.0 0.0\r\n"
    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format from StationType command."
    ):
      await self.backend.get_station_type(1)

  async def test_set_station_type(self):
    self.mock_socket_instance.readline.return_value = b"0 StationType 1 0 0 50.0 0.0 0.0\r\n"
    await self.backend.set_station_type(1, 0, 0, 50.0, 0.0, 0.0)
    self.mock_socket_instance.write.assert_called_with(b"StationType 1 0 0 50.0 0.0 0.0\n")

    with self.assertRaisesRegex(
      ValueError, r"Access type must be 0 \(horizontal\) or 1 \(vertical\)"
    ):
      await self.backend.set_station_type(1, 2, 0, 50.0, 0.0, 0.0)
    with self.assertRaisesRegex(
      ValueError, r"Location type must be 0 \(normal single\) or 1 \(pallet\)"
    ):
      await self.backend.set_station_type(1, 0, 2, 50.0, 0.0, 0.0)

  async def test_home_all_if_no_plate(self):
    self.mock_socket_instance.readline.return_value = b"0 -1\r\n"
    result = await self.backend.home_all_if_no_plate()
    self.assertEqual(result, -1)
    self.mock_socket_instance.write.assert_called_with(b"HomeAll_IfNoPlate\n")

    self.mock_socket_instance.readline.return_value = b"0 0\r\n"
    result = await self.backend.home_all_if_no_plate()
    self.assertEqual(result, 0)

  async def test_grasp_plate(self):
    self.mock_socket_instance.readline.return_value = b"0 -1\r\n"
    result = await self.backend._grasp_plate(10.0, 50, 10.0)
    self.assertEqual(result, -1)
    self.mock_socket_instance.write.assert_called_with(b"GraspPlate 10.0 50 10.0\n")

    with self.assertRaisesRegex(ValueError, "Finger speed percent must be between 1 and 100"):
      await self.backend._grasp_plate(10.0, 0, 10.0)
    with self.assertRaisesRegex(ValueError, "Finger speed percent must be between 1 and 100"):
      await self.backend._grasp_plate(10.0, 101, 10.0)

  async def test_release_plate(self):
    self.mock_socket_instance.readline.return_value = b"0 ReleasePlate 20.0 50 0.0\r\n"
    await self.backend._release_plate(20.0, 50)
    self.mock_socket_instance.write.assert_called_with(b"ReleasePlate 20.0 50 0.0\n")

    self.mock_socket_instance.readline.return_value = b"0 ReleasePlate 20.0 50 10.0\r\n"
    await self.backend._release_plate(20.0, 50, 10.0)
    self.mock_socket_instance.write.assert_called_with(b"ReleasePlate 20.0 50 10.0\n")

    with self.assertRaisesRegex(ValueError, "Finger speed percent must be between 1 and 100"):
      await self.backend._release_plate(20.0, 0)
    with self.assertRaisesRegex(ValueError, "Finger speed percent must be between 1 and 100"):
      await self.backend._release_plate(20.0, 101)

  async def test_set_active_gripper(self):
    self.mock_socket_instance.readline.return_value = b"0 SetActiveGripper 1 0\r\n"
    await self.backend.set_active_gripper(1)
    self.mock_socket_instance.write.assert_called_with(b"SetActiveGripper 1 0\n")

    self.mock_socket_instance.readline.return_value = b"0 SetActiveGripper 2 1 1\r\n"
    await self.backend.set_active_gripper(2, 1, 1)
    self.mock_socket_instance.write.assert_called_with(b"SetActiveGripper 2 1 1\n")

    with self.assertRaisesRegex(ValueError, "Gripper ID must be 1 or 2"):
      await self.backend.set_active_gripper(0)
    with self.assertRaisesRegex(ValueError, "Spin mode must be 0 or 1"):
      await self.backend.set_active_gripper(1, 2)

  async def test_get_active_gripper(self):
    # patch the backend to set ._dual_grippers to True for this test
    with patch.object(self.backend, "_is_dual_gripper", True):
      self.mock_socket_instance.readline.return_value = b"0 1\r\n"
      gripper_id = await self.backend.get_active_gripper()
      self.assertEqual(gripper_id, 1)
      self.mock_socket_instance.write.assert_called_with(b"GetActiveGripper\n")

  async def test_free_mode_enable(self):
    self.mock_socket_instance.readline.return_value = b"0 freemode 1\r\n"
    await self.backend.set_free_mode(True, 1)
    self.mock_socket_instance.write.assert_called_with(b"freemode 1\n")

  async def test_free_mode_disable(self):
    self.mock_socket_instance.readline.return_value = b"0 freemode -1\r\n"
    await self.backend.set_free_mode(False)
    self.mock_socket_instance.write.assert_called_with(b"freemode -1\n")

  async def test_teach_position(self):
    self.mock_socket_instance.readline.return_value = b"0 teachplate 1 50.0\r\n"
    await self.backend.teach_position(1)
    self.mock_socket_instance.write.assert_called_with(b"teachplate 1 50.0\n")

    self.mock_socket_instance.readline.return_value = b"0 teachplate 1 100.0\r\n"
    await self.backend.teach_position(1, 100.0)
    self.mock_socket_instance.write.assert_called_with(b"teachplate 1 100.0\n")

  async def test_parse_xyz_response(self):
    parts = ["1.0", "2.0", "3.0", "4.0", "5.0", "6.0"]
    x, y, z, yaw, pitch, roll = self.backend._parse_xyz_response(parts)
    self.assertEqual(x, 1.0)
    self.assertEqual(y, 2.0)
    self.assertEqual(z, 3.0)
    self.assertEqual(yaw, 4.0)
    self.assertEqual(pitch, 5.0)
    self.assertEqual(roll, 6.0)

    with self.assertRaisesRegex(
      PreciseFlexError, "Unexpected response format for Cartesian coordinates."
    ):
      self.backend._parse_xyz_response(["1.0", "2.0", "3.0", "4.0", "5.0"])

  async def test_parse_angles_response_no_rail(self):
    parts = ["10.0", "20.0", "30.0", "40.0", "50.0"]
    a1, a2, a3, a4, a5, a6 = self.backend._parse_angles_response(parts)
    self.assertEqual(a1, 0.0)
    self.assertEqual(a2, 10.0)
    self.assertEqual(a3, 20.0)
    self.assertEqual(a4, 30.0)
    self.assertEqual(a5, 40.0)
    self.assertEqual(a6, 50.0)

    with self.assertRaisesRegex(PreciseFlexError, "Unexpected response format for angles."):
      self.backend._parse_angles_response(["10.0", "20.0"])

  async def test_parse_angles_response_with_rail(self):
    backend_with_rail = PreciseFlexBackend(has_rail=True, host="localhost", port=10100)
    backend_with_rail.io = self.mock_socket_instance
    parts = ["0.0", "10.0", "20.0", "30.0", "40.0", "50.0"]
    a1, a2, a3, a4, a5, a6 = backend_with_rail._parse_angles_response(parts)
    self.assertEqual(a1, 0.0)
    self.assertEqual(a2, 10.0)
    self.assertEqual(a3, 20.0)
    self.assertEqual(a4, 30.0)
    self.assertEqual(a5, 40.0)
    self.assertEqual(a6, 50.0)

    with self.assertRaisesRegex(PreciseFlexError, "Unexpected response format for angles."):
      backend_with_rail._parse_angles_response(["0.0", "10.0"])
