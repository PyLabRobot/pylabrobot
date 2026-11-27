"""Mantis device tests: the parts tree, and the arm model following the moves."""

import json
import math
import os
import tempfile
import unittest
from typing import cast
from unittest import mock

from pylabrobot.formulatrix.mantis import RECORDING_MANTIS_V4, Mantis, MantisDevice
from pylabrobot.formulatrix.mantis.driver.arm_kinematics import (
  MantisArmCoordinate,
  compute_joint_angles,
)
from pylabrobot.formulatrix.mantis.driver.errors import MantisMotionError
from pylabrobot.formulatrix.mantis.driver.features.arm_tests import CHIP_SLOT_1_PATH
from pylabrobot.formulatrix.mantis.driver.features.led_bar import LedBar
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.formulatrix.mantis_chips import mantis_chip_low_volume_hi_fi

RESOURCE_MODEL = os.path.join(os.path.dirname(__file__), "resource_model")


class MantisTests(unittest.TestCase):
  def make_device(self) -> MantisDevice:
    return Mantis(io=mock.create_autospec(FTDI, instance=True))

  def test_every_part_has_its_model_file(self):
    device = self.make_device()
    for resource in (device, device.left_chip_rack, device.right_chip_rack):
      with self.subTest(resource=resource.name):
        self.assertTrue(os.path.isfile(os.path.join(RESOURCE_MODEL, f"{resource.model}.glb")))

  def test_a_chip_spot_per_slot_in_its_rack(self):
    device = self.make_device()
    self.assertEqual(sorted(device.chip_spots), [1, 2, 3, 4, 5, 6])
    for slot, spot in device.chip_spots.items():
      rack = device.right_chip_rack if slot in (1, 2) else device.left_chip_rack
      with self.subTest(slot=slot):
        self.assertIs(spot.parent, rack)

  def test_a_chip_in_its_spot_lies_over_its_rack(self):
    device = self.make_device()
    for slot, spot in device.chip_spots.items():
      chip = mantis_chip_low_volume_hi_fi(name=f"chip_{slot}")
      spot.assign_child_resource(resource=chip)
      rack = device.right_chip_rack if slot in (1, 2) else device.left_chip_rack
      centre = chip.get_location_wrt(device, x="c", y="c", z="b")
      rack_front_left = rack.get_location_wrt(device)
      with self.subTest(slot=slot):
        self.assertGreater(centre.x, rack_front_left.x)
        self.assertLess(centre.x, rack_front_left.x + rack.get_size_x())
        self.assertGreater(centre.y, rack_front_left.y)
        self.assertLess(centre.y, rack_front_left.y + rack.get_size_y())

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

  async def test_the_five_bar_follows_the_motors_and_places_every_link(self):
    device = Mantis(simulation=True)
    with mock.patch.object(LedBar, "_sleep", autospec=True):
      await device.setup()
    self.addAsyncCleanup(device.stop)
    assert device.arm is not None
    theta_1, theta_2 = compute_joint_angles(location=MantisArmCoordinate(x=-30.0, y=45.0, z=None))
    await device.arm.move_to_joint_state(joint_state={0: theta_1, 1: theta_2, 2: 10.0})
    five_bar = device.serialize_state()["five_bar"]
    # Unwrapped as the motors turn: a viewer turns each short link the way its motor does.
    self.assertEqual(five_bar["angles"], {"right": theta_1 - 180.0, "left": -theta_2})
    for side in ("right", "left"):
      short = device.arm_links[f"{side}_short_arm"]
      angle = math.radians(five_bar["angles"][side])
      length = five_bar["lengths"][f"{side}_short_arm"]
      base = five_bar["bases"][side]
      distal = cast(Coordinate, short.distal_joint)
      turned = math.radians(short.rotation.z)
      location = cast(Coordinate, short.location)
      with self.subTest(side=side):
        self.assertAlmostEqual(
          base[0] + length * math.cos(angle),
          location.x + distal.x * math.cos(turned) - distal.y * math.sin(turned),
          places=6,
        )
        self.assertAlmostEqual(
          base[1] + length * math.sin(angle),
          location.y + distal.x * math.sin(turned) + distal.y * math.cos(turned),
          places=6,
        )


class SimulatedChipSlotTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_simulated_chip_slot_is_checked_against_its_chip_spot(self):
    device = Mantis(simulation=True)
    with mock.patch.object(LedBar, "_sleep", autospec=True):
      await device.setup(skip_arm=True)
    self.addAsyncCleanup(device.stop, accept_chip_left_on_head=True)
    arm = device.arm
    assert arm is not None
    with self.assertRaisesRegex(MantisMotionError, "chip slot 1 holds no chip in the model"):
      await arm._check_chip_slot_tag(slot=1, action="pick_up_chip")
    device.chip_spots[1].assign_child_resource(resource=mantis_chip_low_volume_hi_fi(name="chip_1"))
    await arm._check_chip_slot_tag(slot=1, action="pick_up_chip")
    with self.assertRaisesRegex(MantisMotionError, "chip slot 1 already holds chip chip_1"):
      await arm._check_chip_slot_tag(slot=1, action="place_chip")
    arm._current_chip = 1  # its chip is on the head
    await arm._check_chip_slot_tag(slot=1, action="place_chip")


class ChipModelTests(unittest.IsolatedAsyncioTestCase):
  async def set_up_device(self) -> MantisDevice:
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as file:
      configuration = json.load(file)
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    path = os.path.join(directory.name, "mantis.json")
    with open(path, "w", encoding="utf-8") as file:
      json.dump({**configuration, "approach_paths": {"chip_slot_1": CHIP_SLOT_1_PATH}}, file)
    device = Mantis(simulation=True, declared_configuration_json=path)
    with mock.patch.object(LedBar, "_sleep", autospec=True):
      await device.setup()
    self.addAsyncCleanup(device.stop, accept_chip_left_on_head=True)
    return device

  async def test_a_picked_up_chip_rides_the_link_and_a_placed_one_its_spot(self):
    device = await self.set_up_device()
    assert device.arm is not None
    chip = mantis_chip_low_volume_hi_fi(name="chip_1")
    device.chip_spots[1].assign_child_resource(resource=chip)
    in_spot, turned_in_spot = chip.get_absolute_location(), chip.get_absolute_rotation().z
    with self.assertRaisesRegex(ValueError, "in no chip slot's spot"):
      await device.pick_up_chip(chip_number=mantis_chip_low_volume_hi_fi(name="loose"))
    await device.pick_up_chip(chip_number=chip)  # the chip itself, found by its spot
    self.assertIs(device.attached_chip, chip)
    self.assertEqual(device.current_chip, 1)
    self.assertIs(chip.parent, device.arm_links["right_long_arm"])
    # The head at slot 1's attach line, CHIP_SLOT_1_PATH's line 6: the chip where it lay in its spot.
    theta_1, theta_2 = compute_joint_angles(
      location=MantisArmCoordinate(x=124.052, y=12.077, z=None)
    )
    device._update_arm_model(theta_1=theta_1, theta_2=theta_2, z=-10.938)
    on_head = chip.get_absolute_location()
    pairs = {"x": (on_head.x, in_spot.x), "y": (on_head.y, in_spot.y), "z": (on_head.z, in_spot.z)}
    for axis, (moved, placed) in pairs.items():
      with self.subTest(axis=axis):
        self.assertAlmostEqual(moved, placed, places=3)
    turned = chip.get_absolute_rotation().z - turned_in_spot
    self.assertAlmostEqual((turned + 180.0) % 360.0 - 180.0, 0.0, places=6)
    await device.place_chip(chip_number=1)
    self.assertIsNone(device.attached_chip)
    self.assertIsNone(device.current_chip)
    self.assertIs(chip.parent, device.chip_spots[1])
    self.assertEqual(chip.rotation.z, 0.0)
    self.assertEqual(chip.get_absolute_location(), in_spot)

  async def test_a_chip_on_the_head_keeps_its_pose_through_serialize_and_deserialize(self):
    device = await self.set_up_device()
    assert device.arm is not None
    device.chip_spots[1].assign_child_resource(resource=mantis_chip_low_volume_hi_fi(name="chip_1"))
    await device.pick_up_chip(chip_number=1)
    link = device.arm_links["right_long_arm"]
    loaded = Resource.deserialize(link.serialize())
    chip, loaded_chip = link.get_resource("chip_1"), loaded.get_resource("chip_1")
    self.assertEqual(type(loaded_chip), type(chip))
    self.assertEqual(loaded_chip.location, chip.location)
    self.assertAlmostEqual(loaded_chip.rotation.z, chip.rotation.z)

  async def test_a_slot_without_a_chip_resource_changes_nothing(self):
    device = await self.set_up_device()
    device._update_chip_model(slot=1, on_head=True)
    device._update_chip_model(slot=1, on_head=False)
    self.assertIsNone(device.chip_spots[1].resource)
    self.assertEqual(device.arm_links["right_long_arm"].children, [])


if __name__ == "__main__":
  unittest.main()
