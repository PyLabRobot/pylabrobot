import unittest

from pylabrobot.brooks.precise_flex.tests.wire_tests import _FakeController, _make_arm


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
