import dataclasses
import logging
import os
import unittest
from typing import Any, List, Optional, Tuple, Union, cast
from unittest.mock import AsyncMock, patch

import pytest

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
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource
from pylabrobot.testing.concurrency import AnyioTestBase
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


class TestTheDevice(unittest.TestCase):
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


class TestBuiltAtSetupFromADeclaredConfiguration(AnyioTestBase):
  """A simulated arm answers as its declared configuration, and lifespan entry builds the arm."""

  async def test_the_arm_is_modelled_before_it_is_homed(self) -> None:
    device = simulated()
    modelled: list[bool] = []

    async def home() -> None:
      modelled.append(device.driver.arm.resource is not None)

    with patch.object(device.driver, "home", home):
      async with device:
        pass
    assert modelled == [True]

  async def test_the_declared_arm_stands_after_setup(self) -> None:
    device = simulated()
    async with device:
      column = device.get_resource("pf400_z_column")
      # From the base plate's top face: the datasheet's 679 mm robot height for 400 mm of travel.
      assert column.get_size_z() == pytest.approx(679.0 - 9.6)
      _, first, second, hand = hung(device.driver)
      assert (first.length, second.length, hand.tool_center_point.x) == (302.0, 289.0, 162.0)
      assert hand.jaw_range == (69.0, 134.0)

  def test_a_taller_arm_is_built_taller(self) -> None:
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
    assert column.get_size_z() == pytest.approx(1439.0 - 9.6)

  async def test_setup_builds_nothing_twice(self) -> None:
    device = simulated()
    async with device:
      before = [r.name for r in device.get_all_children()]
    async with device:
      assert [r.name for r in device.get_all_children()] == before

  def test_without_a_driver_or_simulation_there_is_nothing_to_drive(self) -> None:
    with pytest.raises(ValueError):
      PreciseFlex400()


class TestTheCarriageOnTheDevice(unittest.TestCase):
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


class TestTheDriverHangsTheArm(unittest.TestCase):
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


class TestEveryPartHasItsMesh(unittest.TestCase):
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


class TestTheModelFollowsTheJoints(AnyioTestBase):
  """A joint state stands the carriage and turns each member about the joint it turns on."""

  # As the arm answered where a finger touched the column.
  REPLY = "301.124 91.974 179.489 -184.25 119.971"

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.device = pf400()
    self.driver = self.device.driver
    self.driver._configuration = configuration()
    self.driver._create_feature_resources(self.driver.configuration)
    self.carriage, self.first, self.second, self.hand = hung(self.driver)

  def test_the_model_stands_its_joints_where_the_kinematics_put_them(self) -> None:
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
        assert got == pytest.approx(expected, abs=1e-3)

  def test_the_jaws_follow_a_width_within_their_range_only(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    self.driver.gripper.update_width(100.0)
    assert self.hand.jaw_width == 100.0
    with caplog.at_level(
      "WARNING", logger="pylabrobot.brooks.precise_flex.driver.features.gripper"
    ):
      self.driver.gripper.update_width(200.0)
    assert caplog.records
    assert self.hand.jaw_width == 100.0

  async def test_a_joint_read_stands_the_model_where_it_read(self) -> None:
    with patch.object(self.driver, "send_command", AsyncMock(return_value=self.REPLY)):
      with patch.object(self.driver.arm, "_wait_for_eom", AsyncMock()):
        await self.driver.arm.request_joint_state()
    assert self.carriage.location == pf400_manipulator.z_carriage_location(301.124)
    assert self.first.rotation.z == pytest.approx(91.974)
    assert self.second.rotation.z == pytest.approx(179.489)
    assert self.hand.rotation.z % 360.0 == pytest.approx(-184.25 % 360.0)
    assert self.hand.jaw_width == 119.971

  async def test_a_driver_without_a_base_plate_reads_joints(self) -> None:
    driver = PreciseFlexDriver(
      host="localhost", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=60.0
    )
    with patch.object(driver, "send_command", AsyncMock(return_value=self.REPLY)):
      with patch.object(driver.arm, "_wait_for_eom", AsyncMock()):
        joints = await driver.arm.request_joint_state()
    assert joints[PreciseFlexAxis.SHOULDER] == 91.974


def joints(shoulder: float, elbow: float, wrist: float, jaws: float, z: float = 301.1):
  return {
    PreciseFlexAxis.BASE: z,
    PreciseFlexAxis.SHOULDER: shoulder,
    PreciseFlexAxis.ELBOW: elbow,
    PreciseFlexAxis.WRIST: wrist,
    PreciseFlexAxis.GRIPPER: jaws,
  }


class TestTheGripperIsKeptClearOfTheColumn(AnyioTestBase):
  """What the gripper and link 2 sweep through is kept 5 mm clear of the column."""

  # As the arm answered after homing, its shoulder still past its limit.
  HOMED = (93.3, 179.4, -215.9, 70.7)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.device = built()
    self.driver = self.device.driver
    self.arm = self.driver.arm

  @pytest.mark.parametrize(
    ("pose", "part", "clearance"),
    [
      # Open to 120, a jaw reaches out past the body: where a finger touched the column.
      (joints(92.0, 179.48, -184.25, 120.0), "pf400_gripper_jaw_left", -0.9),
      # The same arm with the jaws closed, and opened wide.
      (joints(92.0, 179.48, 15.0, 70.7), "pf400_gripper_body", 9.4),
      (joints(92.0, 179.48, 15.0, 145.0), "pf400_gripper_jaw_right", 1.5),
      # Folded back over link 1, the wrist hub comes nearest.
      (joints(39.0, 159.0, 180.0, 70.7), "pf400_link_2", 4.1),
      (joints(*HOMED), "pf400_link_2", 21.7),
    ],
  )
  def test_how_near_each_part_stands(self, pose: Any, part: str, clearance: float) -> None:
    name, got = cast(Tuple[str, float], self.arm._get_column_clearance(pose))
    assert name == part
    assert got == pytest.approx(clearance, abs=0.05)

  def test_a_swing_past_the_column_is_refused_though_it_ends_clear(self) -> None:
    # Turning the wrist away from the column never comes near it.
    self.arm._check_path_reachable(joints(*self.HOMED), joints(93.3, 179.4, -250.0, 70.7))
    # Parking with the wrist at 180 ends 44.5 mm clear, and turns the gripper past the column.
    with pytest.raises(ValueError, match="on the way, with the shoulder at 8"):
      self.arm._check_path_reachable(joints(*self.HOMED), joints(0.0, 180.0, 180.0, 70.7))

  def test_an_elbow_swing_through_the_fold_sweeps_link_2_past_the_column(self) -> None:
    # Both ends stand far from the column; folding through 180 brings link 2 within 5 mm.
    start = joints(-30.0, 40.0, 180.0, 70.7)
    with pytest.raises(ValueError, match="on the way, .*pf400_link_2 would stand"):
      self.arm._check_path_reachable(start, joints(-40.0, 280.0, 180.0, 70.7))
    self.arm._check_path_reachable(start, joints(-40.0, 140.0, 180.0, 70.7))

  def test_far_from_the_column_few_poses_are_looked_at(self) -> None:
    def poses_looked_at(current: Any, target: Any) -> int:
      with patch.object(
        self.arm, "_get_column_clearance", wraps=self.arm._get_column_clearance
      ) as placed:
        self.arm._check_path_reachable(current, target)
      return placed.call_count

    stretched = (joints(-90.0, 30.0, 0.0, 120.0), joints(90.0, 30.0, 0.0, 120.0))
    assert poses_looked_at(*stretched) <= 2
    parking = (joints(*self.HOMED), joints(0.0, 180.0, -180.0, 70.7))
    assert poses_looked_at(*parking) < 160

  def test_an_arm_that_starts_too_near_may_move_away_and_no_nearer(self) -> None:
    stood = joints(92.087, 179.417, -185.505, 70.703)  # 0.3 mm off
    self.arm._check_path_reachable(stood, joints(92.087, 179.417, -200.0, 70.703))
    with pytest.raises(ValueError, match="on the way"):
      self.arm._check_path_reachable(stood, joints(92.087, 179.417, -180.0, 70.703))

  def test_a_check_leaves_the_model_where_it_was(self) -> None:
    before = self.device.serialize()
    with pytest.raises(ValueError):
      self.arm._check_path_reachable(joints(*self.HOMED), joints(0.0, 180.0, 180.0, 70.7))
    assert self.device.serialize() == before

  async def test_a_refused_swing_is_never_sent(self) -> None:
    sent = AsyncMock(return_value="301.12 93.25 179.478 -215.9 125.985")  # homed, as read
    with patch.object(self.driver, "send_command", sent):
      with patch.object(self.arm, "_wait_for_eom", AsyncMock()):
        with pytest.raises(ValueError, match="on the way"):
          await self.arm.move_to_joint_state(
            {
              PreciseFlexAxis.SHOULDER: 0.0,
              PreciseFlexAxis.ELBOW: 180.0,
              PreciseFlexAxis.WRIST: 180.0,
            }
          )
    assert [call.args[0] for call in sent.call_args_list] == ["wherej"]

  async def test_homing_is_refused_where_the_opening_jaws_would_reach_the_column(self) -> None:
    # Homing opens the jaws to their stop: 145 here, past the 120 they are sent to.
    self.driver.gripper.configuration = dataclasses.replace(
      self.driver.configuration.gripper,
      soft_limit_range=(60.0, 120.0),
      hard_limit_range=(60.0, 145.0),
    )
    sent = AsyncMock(return_value="301.1 92.0 179.48 15.0 70.7")
    with patch.object(self.driver, "send_command", sent):
      with patch.object(self.arm, "_wait_for_eom", AsyncMock()):
        with pytest.raises(ValueError, match="on the way, .*pf400_gripper_jaw_right would stand"):
          await self.driver.home()
    assert [call.args[0] for call in sent.call_args_list] == ["wherej"]


class TestAMoveKeepsTheModelInStep(AnyioTestBase):
  """The model stands where the arm was last read: before a move, and once it has stopped."""

  # A constructed pose, not a logged read: parked, with the jaws at 100 so 80 closes them.
  STOOD = "301.1 0.0 180.0 180.0 100.0"
  TARGET = {PreciseFlexAxis.SHOULDER: 30.0}

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.device = pf400()
    self.driver = self.device.driver
    self.driver._configuration = configuration()
    self.driver._create_feature_resources(self.driver.configuration)
    _, self.first, _, self.hand = hung(self.driver)
    self.seen: List[Tuple[float, float]] = []

  def controller(self, after_the_move: Union[str, Exception], refuse: bool = False) -> AsyncMock:
    """Answers `wherej` with where the arm stood, then with `after_the_move` once a move is sent."""
    moved: List[str] = []

    async def respond(command: str, **kwargs: Any) -> str:
      del kwargs
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

  async def move(self, sent: AsyncMock, call: Any) -> None:
    with patch.object(self.driver, "send_command", sent):
      with patch("pylabrobot.brooks.precise_flex.driver.features.arm.anyio.sleep", AsyncMock()):
        await call()

  async def test_the_model_stands_where_the_arm_was_read_while_it_travels(self) -> None:
    sent = self.controller(after_the_move="301.1 30.0 180.0 180.0 100.0")
    await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    assert self.seen == [(0.0, 100.0)]
    assert [call.args[0] for call in sent.call_args_list][-1] == "wherej"

  async def test_where_the_arm_stopped_has_the_last_word(self) -> None:
    sent = self.controller(after_the_move="301.1 29.6 180.0 180.0 100.0")
    await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    assert self.first.rotation.z == pytest.approx(29.6)

  async def test_a_refused_move_leaves_the_model_where_the_arm_is_and_keeps_its_error(self) -> None:
    self.driver._recover_out_of_range = False
    sent = self.controller(after_the_move=self.STOOD, refuse=True)
    with pytest.raises(PreciseFlexError) as raised:
      await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    assert raised.value.replycode == -1012
    assert self.first.rotation.z == pytest.approx(0.0)

  async def test_a_read_that_fails_after_a_refused_move_does_not_replace_its_error(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    self.driver._recover_out_of_range = False
    sent = self.controller(after_the_move="", refuse=True)
    with caplog.at_level("WARNING", logger="pylabrobot.brooks.precise_flex.driver.features.arm"):
      with pytest.raises(PreciseFlexError) as raised:
        await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    assert raised.value.replycode == -1012
    assert "its model is stale" in caplog.text

  async def test_a_fault_during_an_accepted_move_is_raised_by_the_read_after_it(self) -> None:
    # `moveJ` is answered once accepted; a stall on the way is the reply to the next `wherej`.
    sent = self.controller(after_the_move=PreciseFlexError(-3105, "Motor stalled"))
    with pytest.raises(PreciseFlexError) as raised:
      await self.move(sent, lambda: self.driver.arm.move_to_joint_state(dict(self.TARGET)))
    assert raised.value.replycode == -3105

  async def test_the_jaws_stand_where_they_were_read_then_where_they_stopped(self) -> None:
    # Force-sensed jaws stop on what they hold: sent to 80, they stop at 86.2.
    sent = self.controller(after_the_move="301.1 0.0 180.0 180.0 86.2")
    self.driver.gripper.configuration = self.driver.configuration.gripper
    await self.move(sent, lambda: self.driver.gripper.move_to_jaw_position(80.0))
    assert self.seen == [(0.0, 100.0)]
    assert self.hand.jaw_width == 86.2


class TestTheGripperIsKeptClearOfTheBasePlate(unittest.TestCase):
  """The base plate is only a few mm thick: the gripper reaches it only when low and over it."""

  # Clear of the column, with a finger over the base plate in front of it.
  OVER_THE_PLATE = (35.0, 120.0, 135.0, 70.7)

  def setUp(self):
    self.arm = built().driver.arm

  def test_how_near_the_gripper_stands(self):
    for pose, clearance in (
      (joints(*self.OVER_THE_PLATE, z=1.5), 1.7),
      (joints(*self.OVER_THE_PLATE, z=10.0), 10.2),
      # As low, but not over the plate.
      (joints(0.0, 90.0, 0.0, 70.7, z=1.5), 255.2),
    ):
      with self.subTest(pose=pose):
        _, got = cast(Tuple[str, float], self.arm._get_base_plate_clearance(pose))
        self.assertAlmostEqual(got, clearance, delta=0.05)

  def test_a_descent_over_the_plate_is_refused_on_the_way(self):
    high = joints(*self.OVER_THE_PLATE, z=50.0)
    with self.assertRaisesRegex(ValueError, "on the way.*from the base plate"):
      self.arm._check_path_reachable(high, joints(*self.OVER_THE_PLATE, z=1.5))
    self.arm._check_path_reachable(high, joints(*self.OVER_THE_PLATE, z=10.0))


class TestParkingTakesAClearTurn(AnyioTestBase):
  """A parking pose is a heading: the wrist reaches it by whichever full turn the way is clear."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.device = pf400()
    self.driver = self.device.driver
    self.read = configuration()
    self.driver._configuration = self.read
    # As setup adopts them, so the soft limits are known to the arm.
    self.driver.arm.configuration = self.read.arm
    self.driver.gripper.configuration = self.read.gripper
    self.driver._create_feature_resources(self.driver.configuration)
    self.driver.arm.parking_position = dict(self.driver.arm.PARKING_POSITION_RIGHT)

  async def park(self, stood: str) -> List[str]:
    sent = AsyncMock(return_value=stood)
    with patch.object(self.driver, "send_command", sent):
      with patch.object(self.driver.arm, "_wait_for_eom", AsyncMock()):
        await self.driver.arm.park()
    return [call.args[0] for call in sent.call_args_list if call.args[0].startswith("moveJ")]

  async def test_the_pose_as_written_is_taken_when_its_way_is_clear(self) -> None:
    # From the wrist at -60 the turn to -180 is shorter, and both ways are clear.
    assert await self.park("301.1 -60.0 90.0 -60.0 70.7") == [
      "moveJ 1 301.125 0.0 180.0 180.0 70.7"
    ]

  async def test_a_full_turn_the_other_way_is_taken_when_the_written_one_passes_the_column(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ) -> None:
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    with caplog.at_level("INFO", logger="pylabrobot.brooks.precise_flex.driver.features.arm"):
      moves = await self.park("301.1 92.0 179.4 -215.9 70.7")
    assert moves == ["moveJ 1 301.125 0.0 180.0 -180.0 70.7"]
    assert "parking with the wrist at -180.0, not 180.0" in caplog.text

  async def test_a_turn_past_the_wrists_soft_limit_is_not_taken(self) -> None:
    # From -215.9 the clear turn is -180, which this wrist may not reach: nothing is sent.
    self.read.arm.soft_limits[PreciseFlexAxis.WRIST] = (-970.0, -200.0)
    with pytest.raises(ValueError, match="WRIST target 180.0 is outside its soft limit"):
      await self.park("301.1 92.0 179.4 -215.9 70.7")

  async def test_a_pose_no_turn_reaches_clear_is_refused_as_written(self) -> None:
    self.driver.arm.parking_position = {
      PreciseFlexAxis.SHOULDER: 92.0,
      PreciseFlexAxis.ELBOW: 179.4,
      PreciseFlexAxis.WRIST: -150.0,
    }
    with pytest.raises(ValueError, match="would stand"):
      await self.park("301.1 92.0 179.4 -215.9 70.7")


class TestEveryMotionPathIsChecked(AnyioTestBase):
  """The controller's own pick and place, and a route of poses, on a modelled arm."""

  # As the arm answered after homing and its shoulder was brought back inside its limit.
  HOMED = "301.12 92.001 179.477 -215.899 125.967"
  # A station that stands the gripper against the column.
  AT_THE_COLUMN = joints(92.0, 179.48, -184.25, 120.0)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.device = built()
    self.driver = self.device.driver
    self.arm = self.driver.arm

  async def test_a_pick_or_place_at_the_column_is_refused_before_the_station_is_written(
    self,
  ) -> None:
    for plate in (self.arm._unchecked_fw_pick_plate, self.arm._unchecked_fw_place_plate):
      sent = AsyncMock(return_value=self.HOMED)
      with patch.object(self.driver, "send_command", sent):
        with patch.object(self.arm, "_wait_for_eom", AsyncMock()):
          with pytest.raises(ValueError, match="would stand"):
            await self.arm._guarded_plate_j(
              lambda current: {**current, **self.AT_THE_COLUMN}, plate
            )
      assert [call.args[0] for call in sent.call_args_list] == ["wherej"]

  async def test_each_move_of_a_route_of_poses_is_checked_from_where_the_last_ends(self) -> None:
    ahead = self.arm._forward_kinematics(joints(0.0, 180.0, -180.0, 70.7)).gripper_pose
    sent = AsyncMock(
      side_effect=lambda command, **kwargs: self.HOMED if command == "wherej" else ""
    )
    with patch.object(self.driver, "send_command", sent):
      with patch.object(self.arm, "_wait_for_eom", AsyncMock()):
        with patch.object(
          self.arm, "_check_path_reachable", wraps=self.arm._check_path_reachable
        ) as checked:
          await self.arm.move_through_cartesian_poses([ahead, ahead], blend=False)
    assert checked.call_count == 2
    assert checked.call_args_list[1].args[0] == checked.call_args_list[0].args[1]
