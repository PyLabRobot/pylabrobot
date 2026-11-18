"""Mantis device tests: the parts tree, and the arm model following the moves."""

import math
import os
import unittest
from unittest import mock

from pylabrobot.formulatrix.mantis import Mantis, MantisDevice
from pylabrobot.formulatrix.mantis.driver.arm_kinematics import (
  MantisArmCoordinate,
  compute_joint_angles,
)
from pylabrobot.formulatrix.mantis.driver.features.led_bar import LedBar
from pylabrobot.io.ftdi import FTDI

RESOURCE_MODEL = os.path.join(os.path.dirname(__file__), "resource_model")


class MantisTests(unittest.TestCase):
  def make_device(self) -> MantisDevice:
    return Mantis(io=mock.create_autospec(FTDI, instance=True))

  def test_every_part_has_its_model_file(self):
    device = self.make_device()
    for resource in (device, device.left_chip_rack, device.right_chip_rack):
      with self.subTest(resource=resource.name):
        self.assertTrue(os.path.isfile(os.path.join(RESOURCE_MODEL, f"{resource.model}.glb")))

  def test_simulation_refuses_a_link(self):
    with self.assertRaises(ValueError):
      Mantis(simulation=True, io=mock.create_autospec(FTDI, instance=True))


class ArmModelTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_move_poses_the_links_and_moves_the_z_column(self):
    device = Mantis(simulation=True)
    with mock.patch.object(LedBar, "_sleep", autospec=True):
      await device.setup()
    self.addAsyncCleanup(device.stop)
    assert device.arm is not None
    column = device.z_column.location
    assert column is not None
    # The simulated arm has no offsets: its joint state is the links' own angles.
    theta_1, theta_2 = compute_joint_angles(location=MantisArmCoordinate(x=15.0, y=60.0, z=None))
    await device.arm.move_to_joint_state(joint_state={0: theta_1, 1: theta_2, 2: 10.0})
    self.assertAlmostEqual(device.z_column.get_absolute_location().z, column.z - 10.0)
    idle, posed = Mantis(simulation=True), Mantis(simulation=True)
    posed._update_arm_model(theta_1=theta_1, theta_2=theta_2, z=-10.0)
    for name, link in device.arm_links.items():
      with self.subTest(link=name):
        self.assertAlmostEqual(link.rotation.z, posed.arm_links[name].rotation.z)
        self.assertNotAlmostEqual(link.rotation.z, idle.arm_links[name].rotation.z)

  def test_the_five_bar_published_places_every_link(self):
    device = Mantis(simulation=True)
    theta_1, theta_2 = compute_joint_angles(location=MantisArmCoordinate(x=-30.0, y=45.0, z=None))
    device._update_arm_model(theta_1=theta_1, theta_2=theta_2, z=0.0)
    five_bar = device.serialize_state()["five_bar"]
    lengths, bases, angles = five_bar["lengths"], five_bar["bases"], five_bar["angles"]
    ends = {
      side: (
        bases[side][0] + lengths[f"{side}_short_arm"] * math.cos(math.radians(angles[side])),
        bases[side][1] + lengths[f"{side}_short_arm"] * math.sin(math.radians(angles[side])),
      )
      for side in ("right", "left")
    }
    for side in ("right", "left"):
      short = device.arm_links[f"{side}_short_arm"]
      with self.subTest(side=side):
        self.assertAlmostEqual(short.rotation.z, angles[side] % 360)
        elbow = short.get_location_wrt(device.z_column)
        turned = short.rotation.z
        distal = short.distal_joint
        assert distal is not None
        self.assertAlmostEqual(
          elbow.x
          + distal.x * math.cos(math.radians(turned))
          - distal.y * math.sin(math.radians(turned)),
          ends[side][0],
          places=3,
        )
    # The head's side picks the solution the device drew: both long links reach it.
    gap = math.dist(ends["right"], ends["left"])
    self.assertLess(gap, lengths["right_long_arm"] + lengths["left_long_arm"])
    self.assertIn(five_bar["side_of_head"], (-1.0, 1.0))


if __name__ == "__main__":
  unittest.main()
