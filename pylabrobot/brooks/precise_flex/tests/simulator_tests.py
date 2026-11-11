"""A simulated PreciseFlex arm: set up, moved and gripped with nothing sent."""

import unittest
from typing import cast

from pylabrobot.brooks.precise_flex import PreciseFlex400, PreciseFlexAxis
from pylabrobot.resources.end_effector import MechanicalGripper


class TestASimulatedArm(unittest.IsolatedAsyncioTestCase):
  """Driven like the real arm, answering from its declared configuration and its held joints."""

  async def asyncSetUp(self):
    # `send_command` raises on a simulated arm: a setup that returns sent nothing.
    self.device = PreciseFlex400(simulation=True, name="pf400")
    await self.device.setup()
    self.driver = self.device.driver
    self.hand = cast(MechanicalGripper, self.driver.gripper.resource)

  async def test_it_stands_parked_with_its_jaws_open(self):
    joints = await self.driver.arm.request_joint_state()
    parked = self.driver.arm.PARKING_POSITION_RIGHT
    self.assertEqual({axis: joints[axis] for axis in parked}, parked)
    self.assertEqual(joints[PreciseFlexAxis.BASE], 0.75 * 401.5)
    self.assertEqual(self.hand.jaw_width, 134.0)

  async def test_a_joint_move_moves_the_model(self):
    await self.driver.arm.move_to_joint_state({PreciseFlexAxis.WRIST: 170.0})
    self.assertEqual((await self.driver.arm.request_joint_state())[PreciseFlexAxis.WRIST], 170.0)
    self.assertAlmostEqual(self.hand.rotation.z, 170.0)

  async def test_a_jaw_move_moves_the_jaws(self):
    await self.driver.gripper.move_to_jaw_position(100.0)
    self.assertAlmostEqual(self.hand.jaw_width, 100.0, places=0)

  async def test_a_command_it_does_not_simulate_names_itself(self):
    with self.assertRaisesRegex(NotImplementedError, "'version'"):
      await self.driver.request_version()


if __name__ == "__main__":
  unittest.main()
