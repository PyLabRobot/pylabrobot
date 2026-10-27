import dataclasses
import os
import unittest
from typing import List, Optional, Tuple, Union, cast
from unittest.mock import AsyncMock, patch

from pylabrobot.brooks.precise_flex import kinematics
from pylabrobot.brooks.precise_flex.device import (
  RECORDING_PF400,
  PreciseFlex400,
  PreciseFlexDevice,
)
from pylabrobot.brooks.precise_flex.driver.configuration import (
  PreciseFlexAxis,
  PreciseFlexConfiguration,
  read_configuration,
)
from pylabrobot.brooks.precise_flex.driver.errors import PreciseFlexError
from pylabrobot.brooks.precise_flex.driver.features.arm import PreciseFlexArmConfiguration
from pylabrobot.brooks.precise_flex.driver.features.gripper import PreciseFlexGripperConfiguration
from pylabrobot.brooks.precise_flex.driver.master import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.resource_model import pf400_manipulator
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3


def configuration(links=kinematics.ARM_LINKS_EXTENDED, z_range=(1.5, 401.5)):
  """What an extended-reach arm on a 400 mm column reports, as far as the model reads it."""
  return PreciseFlexConfiguration(
    manufacturer="",
    controller_model="",
    hardware_version="",
    gpl_version="",
    controller_serial="",
    robot_name="PF400",
    robot_type=12,
    tcs_version="",
    modules=(),
    num_axes=5,
    extra_axes=0,
    axis_mask=47,
    arm=PreciseFlexArmConfiguration(
      soft_limits={
        PreciseFlexAxis.BASE: z_range,
        PreciseFlexAxis.SHOULDER: (-93.0, 93.0),
        PreciseFlexAxis.ELBOW: (12.0, 348.0),
        PreciseFlexAxis.WRIST: (-970.0, 970.0),
      },
      hard_limits={},
      max_joint_speed={},
      max_joint_acceleration={},
      max_joint_deceleration={},
      max_cartesian_speed=0.0,
      max_cartesian_acceleration=0.0,
      kinematics=kinematics.PF400Params(l1=links[0], l2=links[1], gripper_length=162.0),
    ),
    gripper=PreciseFlexGripperConfiguration(
      soft_limit_range=(60.0, 145.0),
      hard_limit_range=(0.0, 0.0),
      max_speed=0.0,
      max_acceleration=0.0,
      max_deceleration=0.0,
    ),
    # The arm the tests stand in for has the v23 gripper, with its two cameras.
    camera_count=2,
  )


def hung(driver: PreciseFlexDriver) -> Tuple[Resource, LinkBody, LinkBody, MechanicalGripper]:
  """The carriage, both links and the gripper the driver hung, which are None before it has."""
  return (
    cast(Resource, driver.arm.resource),
    cast(LinkBody, driver.arm.link_1),
    cast(LinkBody, driver.arm.link_2),
    cast(MechanicalGripper, driver.gripper.resource),
  )


def pf400(declared: Optional[str] = None) -> PreciseFlexDevice:
  driver = PreciseFlexDriver(
    host="localhost",
    gripper_length=162.0,
    gripper_z_offset=0.0,
    closed_gripper_position=60.0,
    declared_configuration_json=declared,
  )
  return PreciseFlex400(driver=driver, name="pf400")


def built() -> PreciseFlexDevice:
  """A device whose driver has read the stand-in configuration and built the arm from it."""
  device = pf400()
  device.driver._configuration = configuration()
  device.driver._create_feature_resources(device.driver.configuration)
  return device


class TestTheDevice(unittest.IsolatedAsyncioTestCase):
  """The device carries its base plate and hands it to its driver."""

  def setUp(self):
    self.device = pf400()

  def test_the_base_plate_is_its_child_and_the_drivers(self):
    self.assertIs(self.device.base_plate.parent, self.device)
    self.assertIs(self.device.driver.base_plate, self.device.base_plate)
    self.assertEqual(self.device.base_plate.name, "pf400_base_plate")

  def test_before_a_configuration_only_the_base_plate_stands(self):
    self.assertEqual([r.name for r in self.device.get_all_children()], ["pf400_base_plate"])
    self.assertIsNone(self.device.arm.resource)


def simulated(declared: str = RECORDING_PF400) -> PreciseFlexDevice:
  return PreciseFlex400(simulation=True, declared_configuration_json=declared, name="pf400")


class TestBuiltAtSetupFromADeclaredConfiguration(unittest.IsolatedAsyncioTestCase):
  """A simulated arm answers as its declared configuration, and setup builds the arm from it."""

  async def test_the_declared_arm_stands_after_setup(self):
    device = simulated()
    await device.setup()
    column = device.get_resource("pf400_z_column")
    # From the base plate's top face: the datasheet's 679 mm robot height for 400 mm of travel.
    self.assertAlmostEqual(column.get_size_z(), 679.0 - 9.6)
    _, first, second, hand = hung(device.driver)
    self.assertEqual((first.length, second.length, hand.tool_center_point.x), (302.0, 289.0, 162.0))
    self.assertEqual(hand.jaw_range, (69.0, 134.0))

  def test_a_taller_arm_is_built_taller(self):
    declared = read_configuration(RECORDING_PF400)["device"]
    # The 1160 mm column, with the declared arm's 1.5 mm margin at each end of the Z travel.
    soft_limits = {**declared.arm.soft_limits, PreciseFlexAxis.BASE: (1.5, 1161.5)}
    taller = dataclasses.replace(
      declared, arm=dataclasses.replace(declared.arm, soft_limits=soft_limits)
    )
    device = pf400()
    device.driver._create_feature_resources(taller)
    column = device.get_resource("pf400_z_column")
    # From the base plate's top face: the datasheet's 1439 mm robot height for 1160 mm of travel.
    self.assertAlmostEqual(column.get_size_z(), 1439.0 - 9.6)

  async def test_setup_builds_nothing_twice(self):
    device = simulated()
    await device.setup()
    before = [r.name for r in device.get_all_children()]
    await device.setup()
    self.assertEqual([r.name for r in device.get_all_children()], before)

  def test_without_a_driver_or_simulation_there_is_nothing_to_drive(self):
    with self.assertRaises(ValueError):
      PreciseFlex400()


class TestTheCarriageOnTheDevice(unittest.IsolatedAsyncioTestCase):
  """The carriage on the column stands where the J1 drive says it is."""

  def setUp(self):
    self.device = built()
    self.column = self.device.get_resource("pf400_z_column")
    self.carriage = self.device.get_resource("pf400_z_carriage")

  def _flange_plane(self, z: float) -> Coordinate:
    self.carriage.location = pf400_manipulator.z_carriage_location(z)
    return self.carriage.get_absolute_location() + pf400_manipulator.Z_CARRIAGE_REFERENCE_POINT

  def test_the_drive_reports_the_shoulder_axis_raised_by_what_it_read(self):
    axis = self.device.base_plate.get_absolute_location() + pf400_manipulator.SHOULDER_AXIS
    for z in (0.0, 250.0):
      self.assertEqual(self._flange_plane(z), axis + Coordinate(0.0, 0.0, z))

  def test_the_carriage_stays_within_the_columns_travel(self):
    top = pf400_manipulator.z_carriage_location(400.0).z + self.carriage.get_size_z()
    self.assertLessEqual(top, self.column.get_size_z())


class TestTheDriverHangsTheArm(unittest.IsolatedAsyncioTestCase):
  """At setup the driver hangs the carriage, the links and the gripper, each by its joint."""

  def setUp(self):
    self.device = pf400()
    self.driver = self.device.driver
    self.driver._configuration = configuration()
    self.driver._create_feature_resources(self.driver.configuration)

  def test_the_chain_runs_from_the_base_plate_to_the_gripper(self):
    carriage, first, second, hand = hung(self.driver)
    column = self.device.get_resource("pf400_z_column")
    self.assertIs(column.parent, self.device.base_plate)
    self.assertIs(carriage, self.device.get_resource("pf400_z_carriage"))
    self.assertIs(carriage.parent, column)
    self.assertEqual([first.parent, second.parent, hand.parent], [carriage, first, second])
    self.assertEqual((first.length, second.length, hand.tool_center_point.x), (302.0, 289.0, 162.0))

  def test_every_joint_lands_on_the_one_before_it(self):
    _, first, second, hand = hung(self.driver)
    axis = pf400_manipulator.SHOULDER_AXIS
    shoulder = first.get_location_wrt(self.device) + first.proximal_joint
    self.assertAlmostEqual(shoulder.x, axis.x)
    self.assertAlmostEqual(shoulder.y, axis.y)
    elbow = second.get_location_wrt(self.device) + second.proximal_joint
    self.assertAlmostEqual(elbow.x - shoulder.x, 302.0)
    wrist = hand.get_location_wrt(self.device) + hand.proximal_joint
    self.assertAlmostEqual(wrist.x - elbow.x, 289.0)
    # The wrist joint is in the flange plane, which is the shoulder axis's height at Z 0.
    self.assertAlmostEqual(wrist.z, axis.z)
    self.assertAlmostEqual(elbow.z, axis.z)

  def test_the_gripper_has_the_body_its_cameras_say(self):
    self.assertEqual(hung(self.driver)[3].body.model, "brooks_pf400_gripper_body_v23")
    without = pf400().driver
    without._configuration = dataclasses.replace(configuration(), camera_count=0)
    without._create_feature_resources(without.configuration)
    self.assertEqual(hung(without)[3].body.model, "brooks_pf400_gripper_body_s23")

  def test_a_driver_without_a_base_plate_hangs_nothing(self):
    driver = PreciseFlexDriver(
      host="localhost", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=60.0
    )
    driver._configuration = configuration()
    driver._create_feature_resources(driver.configuration)
    self.assertIsNone(driver.arm.resource)


class TestEveryPartHasItsMesh(unittest.IsolatedAsyncioTestCase):
  """A part is drawn from the mesh its model names, shipped beside the resource model."""

  def test_every_part_of_every_arm_names_a_shipped_mesh(self):
    meshes = os.path.dirname(os.path.abspath(pf400_manipulator.__file__))
    for links in (kinematics.ARM_LINKS_STANDARD, kinematics.ARM_LINKS_EXTENDED):
      for z_range in ((1.5, 401.5), (1.5, 751.5), (1.5, 1161.5)):
        for camera_count in (0, 2):
          with self.subTest(links=links, z_range=z_range, camera_count=camera_count):
            device = pf400()
            stand_in = dataclasses.replace(configuration(links, z_range), camera_count=camera_count)
            device.driver._create_feature_resources(stand_in)
            for part in device.get_all_children():
              # The gripper itself is drawn as its body, jaws and fingers.
              if part.model is not None and part.category != "mechanical_gripper":
                self.assertTrue(
                  os.path.exists(os.path.join(meshes, f"{part.model}.glb")), part.model
                )


class TestTheModelFollowsTheJoints(unittest.IsolatedAsyncioTestCase):
  """A joint state stands the carriage and turns each member about the joint it turns on."""

  # As the arm answered where a finger touched the column.
  REPLY = "301.124 91.974 179.489 -184.25 119.971"

  def setUp(self):
    self.device = pf400()
    self.driver = self.device.driver
    self.driver._configuration = configuration()
    self.driver._create_feature_resources(self.driver.configuration)
    self.carriage, self.first, self.second, self.hand = hung(self.driver)

  def test_the_model_stands_its_joints_where_the_kinematics_put_them(self):
    joints = {
      PreciseFlexAxis.BASE: 100.0,
      PreciseFlexAxis.SHOULDER: 30.0,
      PreciseFlexAxis.ELBOW: 70.0,
      PreciseFlexAxis.WRIST: -40.0,
    }
    self.driver.arm.update_joint_state(joints)
    pose = self.driver.arm._forward_kinematics(joints)
    # The kinematics count from the shoulder axis, which the device states within its base plate.
    origin = self.device.base_plate.get_absolute_location() + pf400_manipulator.SHOULDER_AXIS
    for member, joint, want in (
      (self.second, self.second.proximal_joint, pose.elbow_joint_location),
      (self.hand, self.hand.proximal_joint, pose.wrist_joint_location),
    ):
      matrix = member.get_absolute_rotation().get_rotation_matrix()
      here = member.get_absolute_location() + Coordinate(
        *matrix_vector_multiply_3x3(matrix, joint.vector())
      )
      for got, expected in zip(here.vector(), (origin + want).vector()):
        self.assertAlmostEqual(got, expected, places=3)

  def test_the_jaws_follow_a_width_within_their_range_only(self):
    self.driver.gripper.update_width(100.0)
    self.assertEqual(self.hand.jaw_width, 100.0)
    with self.assertLogs("pylabrobot.brooks.precise_flex.driver.features.gripper", "WARNING"):
      self.driver.gripper.update_width(200.0)
    self.assertEqual(self.hand.jaw_width, 100.0)

  async def test_a_joint_read_stands_the_model_where_it_read(self):
    with patch.object(self.driver, "send_command", AsyncMock(return_value=self.REPLY)):
      with patch.object(self.driver.arm, "_wait_for_eom", AsyncMock()):
        await self.driver.arm.request_joint_state()
    self.assertEqual(self.carriage.location, pf400_manipulator.z_carriage_location(301.124))
    self.assertAlmostEqual(self.first.rotation.z, 91.974)
    self.assertAlmostEqual(self.second.rotation.z, 179.489)
    self.assertAlmostEqual(self.hand.rotation.z % 360.0, -184.25 % 360.0)
    self.assertEqual(self.hand.jaw_width, 119.971)

  async def test_a_driver_without_a_base_plate_reads_as_before(self):
    driver = PreciseFlexDriver(
      host="localhost", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=60.0
    )
    with patch.object(driver, "send_command", AsyncMock(return_value=self.REPLY)):
      with patch.object(driver.arm, "_wait_for_eom", AsyncMock()):
        joints = await driver.arm.request_joint_state()
    self.assertEqual(joints[PreciseFlexAxis.SHOULDER], 91.974)


class TestAMoveKeepsTheModelInStep(unittest.IsolatedAsyncioTestCase):
  """The model stands where the arm was last read: before a move, and once it has stopped."""

  # A constructed pose, not a logged read: parked, with the jaws at 100 so 80 closes them.
  STOOD = "301.1 0.0 180.0 180.0 100.0"
  TARGET = {PreciseFlexAxis.SHOULDER: 30.0}

  def setUp(self):
    self.device = pf400()
    self.driver = self.device.driver
    self.driver._configuration = configuration()
    self.driver._create_feature_resources(self.driver.configuration)
    _, self.first, _, self.hand = hung(self.driver)
    self.seen: List[Tuple[float, float]] = []

  def controller(self, after_the_move: Union[str, Exception], refuse: bool = False) -> AsyncMock:
    """Answers `wherej` with where the arm stood, then with `after_the_move` once a move is sent."""
    moved: List[str] = []

    async def respond(command: str) -> str:
      if command == "wherej":
        if not moved:
          return self.STOOD
        if isinstance(after_the_move, Exception):
          raise after_the_move
        return after_the_move
      if command.startswith(("moveJ", "gripper", "GraspPlate")):
        # What the model says while the move is under way.
        self.seen.append((self.first.rotation.z, self.hand.jaw_width))
        moved.append(command)
        if refuse:
          raise PreciseFlexError(-1012, "refused")
      return ""

    return AsyncMock(side_effect=respond)

  async def move(self, sent: AsyncMock, call) -> None:
    with patch.object(self.driver, "send_command", sent):
      with patch("pylabrobot.brooks.precise_flex.driver.features.arm.asyncio.sleep", AsyncMock()):
        await call()

  async def test_the_model_stands_where_the_arm_was_read_while_it_travels(self):
    sent = self.controller(after_the_move="301.1 30.0 180.0 180.0 100.0")
    await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    self.assertEqual(self.seen, [(0.0, 100.0)])
    self.assertEqual([call.args[0] for call in sent.call_args_list][-1], "wherej")

  async def test_where_the_arm_stopped_has_the_last_word(self):
    sent = self.controller(after_the_move="301.1 29.6 180.0 180.0 100.0")
    await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    self.assertAlmostEqual(self.first.rotation.z, 29.6)

  async def test_a_refused_move_leaves_the_model_where_the_arm_is_and_keeps_its_error(self):
    self.driver._recover_out_of_range = False
    sent = self.controller(after_the_move=self.STOOD, refuse=True)
    with self.assertRaises(PreciseFlexError) as raised:
      await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    self.assertEqual(raised.exception.replycode, -1012)
    self.assertAlmostEqual(self.first.rotation.z, 0.0)

  async def test_a_read_that_fails_after_a_refused_move_does_not_replace_its_error(self):
    self.driver._recover_out_of_range = False
    sent = self.controller(after_the_move="", refuse=True)
    with self.assertLogs("pylabrobot.brooks.precise_flex.driver.features.arm", "WARNING") as logs:
      with self.assertRaises(PreciseFlexError) as raised:
        await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    self.assertEqual(raised.exception.replycode, -1012)
    self.assertIn("its model is stale", "\n".join(logs.output))

  async def test_a_fault_during_an_accepted_move_is_raised_by_the_read_after_it(self):
    # `moveJ` is answered once accepted; a stall on the way is the reply to the next `wherej`.
    sent = self.controller(after_the_move=PreciseFlexError(-3105, "Motor stalled"))
    with self.assertRaises(PreciseFlexError) as raised:
      await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    self.assertEqual(raised.exception.replycode, -3105)

  async def test_the_jaws_stand_where_they_were_read_then_where_they_stopped(self):
    # Force-sensed jaws stop on what they hold: sent to 80, they stop at 86.2.
    sent = self.controller(after_the_move="301.1 0.0 180.0 180.0 86.2")
    self.driver.gripper.configuration = self.driver.configuration.gripper
    await self.move(sent, lambda: self.driver.gripper.move_to_jaw_position(80.0))
    self.assertEqual(self.seen, [(0.0, 100.0)])
    self.assertEqual(self.hand.jaw_width, 86.2)
