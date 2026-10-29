import dataclasses
import json
import os
import tempfile
import unittest

from pylabrobot.brooks.precise_flex import kinematics
from pylabrobot.brooks.precise_flex.device import RECORDING_PF400
from pylabrobot.brooks.precise_flex.driver.configuration import (
  PreciseFlexAxis,
  PreciseFlexConfiguration,
  read_configuration,
  to_jsonable,
)
from pylabrobot.brooks.precise_flex.driver.features.arm import PreciseFlexArmConfiguration
from pylabrobot.brooks.precise_flex.driver.features.gripper import PreciseFlexGripperConfiguration
from pylabrobot.brooks.precise_flex.driver.features.rail import PreciseFlexRailConfiguration
from pylabrobot.brooks.precise_flex.driver.master import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.tests.wire_tests import (
  _RAIL_REPLIES,
  _FakeController,
  _make_arm,
)


def configuration(rail=None) -> PreciseFlexConfiguration:
  per_axis = {
    axis: float(axis)
    for axis in (
      PreciseFlexAxis.BASE,
      PreciseFlexAxis.SHOULDER,
      PreciseFlexAxis.ELBOW,
      PreciseFlexAxis.WRIST,
    )
  }
  return PreciseFlexConfiguration(
    manufacturer="Precise Automation",
    controller_model="G1400",
    hardware_version="B",
    gpl_version="4.2",
    controller_serial="",
    robot_name="PreciseFlex 400SX",
    robot_type=12,
    tcs_version="3.0",
    modules=("PARobot Module 3.0", "IntelliGuide 1.0"),
    num_axes=5,
    extra_axes=0,
    axis_mask=47,
    arm=PreciseFlexArmConfiguration(
      soft_limits={
        PreciseFlexAxis.BASE: (1.5, 401.5),
        PreciseFlexAxis.SHOULDER: (-93.0, 93.0),
        PreciseFlexAxis.ELBOW: (12.0, 348.0),
        PreciseFlexAxis.WRIST: (-970.0, 970.0),
      },
      hard_limits={PreciseFlexAxis.BASE: (0.0, 403.0)},
      max_joint_speed=per_axis,
      max_joint_acceleration=per_axis,
      max_joint_deceleration=per_axis,
      max_cartesian_speed=500.0,
      max_cartesian_acceleration=2500.0,
      kinematics=kinematics.PF400Params(gripper_length=162.0),
      kinematics_source="device",
    ),
    gripper=PreciseFlexGripperConfiguration(
      soft_limit_range=(69.0, 134.0),
      hard_limit_range=(60.0, 145.0),
      max_speed=100.0,
      max_acceleration=1000.0,
      max_deceleration=1000.0,
    ),
    rail=rail,
    camera_count=2,
    _power_state=20,
  )


class TestSavedConfiguration(unittest.IsolatedAsyncioTestCase):
  """A configuration is written to a file and read back as the dataclasses it was."""

  def round_trip(self, written: PreciseFlexConfiguration) -> PreciseFlexConfiguration:
    driver = PreciseFlexDriver(
      host="localhost", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=60.0
    )
    driver._configuration = written
    driver._adopt_configuration(written)
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "configuration.json")
      driver.save_configuration(path)
      with open(path, encoding="utf-8") as f:
        self.saved = json.load(f)
      read = read_configuration(path)
    self.assertEqual(set(read) - {"device"}, set(self.saved) - {"device"})
    device: PreciseFlexConfiguration = read["device"]
    return device

  def test_it_reads_back_what_was_written(self):
    written = configuration()
    read = self.round_trip(written)
    self.assertEqual(read.arm, written.arm)
    self.assertEqual(read.gripper, written.gripper)
    self.assertEqual(read.modules, written.modules)
    self.assertEqual(read.robot_name, "PreciseFlex 400SX")
    self.assertIsNone(read.rail)

  def test_axes_are_written_by_name_and_read_back_as_axes(self):
    read = self.round_trip(configuration())
    self.assertEqual(
      list(self.saved["arm"]["soft_limits"]), list("BASE SHOULDER ELBOW WRIST".split())
    )
    self.assertEqual(read.arm.soft_limits[PreciseFlexAxis.SHOULDER], (-93.0, 93.0))
    self.assertIsInstance(read.arm.kinematics, kinematics.PF400Params)

  def test_a_rail_is_read_back_with_what_it_did_not_report_left_out(self):
    read = self.round_trip(configuration(rail=PreciseFlexRailConfiguration((0.0, 1000.0))))
    self.assertEqual(read.rail, PreciseFlexRailConfiguration(soft_limit_range=(0.0, 1000.0)))
    self.assertTrue(read.has_rail)

  def test_each_feature_is_saved_beside_the_device(self):
    self.round_trip(configuration(rail=PreciseFlexRailConfiguration((0.0, 1000.0))))
    self.assertEqual(list(self.saved), ["device", "arm", "gripper", "rail"])
    self.assertNotIn("arm", self.saved["device"])

  def test_live_state_is_not_written(self):
    read = self.round_trip(configuration())
    self.assertNotIn("_power_state", self.saved["device"])
    self.assertIsNone(read._power_state)

  def test_a_field_this_driver_no_longer_has_is_left_out(self):
    written = to_jsonable(configuration())
    saved = {
      "device": {n: v for n, v in written.items() if n not in ("arm", "gripper", "rail")},
      "arm": {**written["arm"], "dropped_since": 1},
      "gripper": written["gripper"],
      "dropped_feature": {},
    }
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "configuration.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(saved, f)
      read = read_configuration(path)
    self.assertEqual(read["device"].arm, configuration().arm)
    self.assertNotIn("dropped_feature", read)

  def test_nothing_is_saved_before_setup(self):
    driver = PreciseFlexDriver(
      host="localhost", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=60.0
    )
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "configuration.json")
      with self.assertRaises(RuntimeError):
        driver.save_configuration(path)
      self.assertFalse(os.path.exists(path))


class TestTheRecordedPF400(unittest.IsolatedAsyncioTestCase):
  """The recording shipped with the driver: what one extended-reach PF400 answered."""

  def setUp(self):
    self.recorded = read_configuration(RECORDING_PF400)["device"]

  def test_it_is_the_arm_it_is_named_for(self):
    self.assertEqual(self.recorded.robot_name, "PreciseFlex 400SX")
    self.assertEqual(self.recorded.arm.reach_class, "extended")
    self.assertEqual(self.recorded.arm.z_range, (1.5, 401.5))
    self.assertEqual(self.recorded.arm.soft_limits[PreciseFlexAxis.WRIST], (-960.0, 960.0))
    self.assertEqual(self.recorded.arm.kinematics.gripper_length, 162.0)
    self.assertEqual(self.recorded.gripper.soft_limit_range, (69.0, 134.0))
    self.assertEqual(self.recorded.camera_count, 2)
    self.assertTrue(self.recorded.has_vision_server)
    self.assertTrue(self.recorded.has_vision_gripper)
    self.assertFalse(self.recorded.has_rail)

  def test_it_does_not_say_which_arm_it_was(self):
    self.assertEqual(self.recorded.controller_serial, "")

  def test_it_is_as_the_driver_writes_it(self):
    driver = PreciseFlexDriver(
      host="localhost", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=80.0
    )
    driver._configuration = self.recorded
    driver._adopt_configuration(self.recorded)
    with open(RECORDING_PF400, encoding="utf-8") as f:
      self.assertEqual(json.load(f), driver._saved_configuration())


class TestADeclaredConfigurationIsCrossChecked(unittest.IsolatedAsyncioTestCase):
  """Discovery refuses a declaration that does not describe the arm that answers."""

  def setUp(self):
    self.driver = PreciseFlexDriver(
      host="localhost",
      gripper_length=162.0,
      gripper_z_offset=0.0,
      closed_gripper_position=60.0,
      declared_configuration_json=RECORDING_PF400,
    )
    self.recorded = read_configuration(RECORDING_PF400)["device"]

  def test_the_file_is_read_when_the_driver_is_built(self):
    self.assertEqual(self.driver.declared, read_configuration(RECORDING_PF400))
    self.assertEqual(self.driver.declared["device"], self.recorded)
    self.assertEqual(self.driver.declared_configuration_json, RECORDING_PF400)

  def test_another_arm_of_the_same_build_passes(self):
    another = dataclasses.replace(
      self.recorded, controller_serial="another", gpl_version="GPL 5.2", modules=()
    )
    self.driver._check_declared_against(another)

  def test_what_differs_is_named(self):
    links = dataclasses.replace(self.recorded.arm.kinematics, l1=225.0, l2=210.0)
    standard = dataclasses.replace(self.recorded.arm, kinematics=links)
    tool = dataclasses.replace(self.recorded.arm.kinematics, gripper_length=170.0)
    longer_tool = dataclasses.replace(self.recorded.arm, kinematics=tool)
    narrowed = dataclasses.replace(
      self.recorded.arm,
      soft_limits={**self.recorded.arm.soft_limits, PreciseFlexAxis.SHOULDER: (-90.0, 90.0)},
    )
    for answered, named in (
      (
        dataclasses.replace(self.recorded, arm=standard),
        r"arm.kinematics \(l1, l2, gripper_length\): declared \(302.0",
      ),
      (
        dataclasses.replace(self.recorded, arm=longer_tool),
        r"device answers \(302.0, 289.0, 170.0\)",
      ),
      (dataclasses.replace(self.recorded, arm=narrowed), "arm.soft_limits"),
      (
        dataclasses.replace(self.recorded, rail=PreciseFlexRailConfiguration((0.0, 1000.0))),
        "has_rail: declared False, device answers True",
      ),
    ):
      with self.subTest(named=named), self.assertRaisesRegex(ValueError, named):
        self.driver._check_declared_against(answered)

  def test_nothing_declared_checks_nothing(self):
    driver = PreciseFlexDriver(
      host="localhost", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=60.0
    )
    self.assertEqual(driver.declared, {})
    driver._check_declared_against(configuration())


class TestDiscoveryAgainstADeclaration(unittest.IsolatedAsyncioTestCase):
  """What `discover` does with a declaration, against the wire tests' controller."""

  async def test_a_declaration_saved_off_the_same_arm_is_accepted(self):
    first = _make_arm(_FakeController())
    await first.discover()
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "configuration.json")
      first.save_configuration(path)
      second = _make_arm(_FakeController())
      second.declared = read_configuration(path)
    self.assertEqual(await second.discover(), first.configuration)

  async def test_a_declaration_for_another_arm_is_refused_and_nothing_is_adopted(self):
    arm = _make_arm(_FakeController(_RAIL_REPLIES), has_rail=True)
    arm.declared = read_configuration(RECORDING_PF400)
    with self.assertRaisesRegex(ValueError, "has_rail: declared False, device answers True"):
      await arm.discover()
    with self.assertRaises(RuntimeError):
      arm.configuration


class TestAVisionGripperIsItsCameras(unittest.IsolatedAsyncioTestCase):
  """Discovery counts the cameras through the controller; the arm's name does not decide it."""

  NAME = "pd 2002"
  COUNT = "VToolProperty System CameraCount"

  async def discovered(self, **replies: str):
    fake = _FakeController({self.NAME: "0 PreciseFlex 400SX", **replies})
    arm = _make_arm(fake)
    return await arm.discover(), fake

  async def test_an_arm_named_without_a_v_that_counts_two_cameras_has_one(self):
    configuration, _ = await self.discovered()
    self.assertEqual(configuration.camera_count, 2)
    self.assertTrue(configuration.has_vision_gripper)

  async def test_an_arm_that_counts_none_has_none_whatever_it_is_called(self):
    configuration, _ = await self.discovered(**{self.NAME: "0 PreciseFlex 400SXV", self.COUNT: "0"})
    self.assertFalse(configuration.has_vision_gripper)

  async def test_an_arm_that_will_not_say_has_none(self):
    # The relay answers a bare negative code where the vision server is not there to count.
    with self.assertLogs("pylabrobot.brooks.precise_flex.driver.master", "WARNING"):
      configuration, _ = await self.discovered(**{self.COUNT: "not a number"})
    self.assertEqual(configuration.camera_count, 0)
    refused, _ = await self.discovered(**{self.COUNT: "-4015"})
    self.assertFalse(refused.has_vision_gripper)

  async def test_an_arm_without_the_vision_module_is_not_asked(self):
    configuration, fake = await self.discovered(
      version="0 TCP Command Server 3.0D4, PARobot Module 3.0D4"
    )
    self.assertNotIn(self.COUNT, fake.sent)
    self.assertFalse(configuration.has_vision_gripper)
