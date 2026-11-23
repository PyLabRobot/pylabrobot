"""Mantis device tests: the parts tree, lifespan, and the arm model following the moves."""

import json
import math
import os
import tempfile
from typing import cast
from unittest import mock

import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.formulatrix.mantis import RECORDING_MANTIS_V4, Mantis, MantisDevice
from pylabrobot.formulatrix.mantis.driver.arm_kinematics import (
  MantisArmCoordinate,
  compute_joint_angles,
)
from pylabrobot.formulatrix.mantis.driver.errors import MantisMotionError
from pylabrobot.formulatrix.mantis.driver.features.arm_tests import CHIP_SLOT_1_PATH
from pylabrobot.formulatrix.mantis.driver.features.led_bar import LedBar
from pylabrobot.formulatrix.mantis.driver.master_tests import FakeFTDI
from pylabrobot.formulatrix.mantis.driver.oem_config_tests import SYNTHETIC_ZONES_BLOCK
from pylabrobot.resources import Coordinate
from pylabrobot.resources.formulatrix.mantis_chips import mantis_chip_low_volume_hi_fi
from pylabrobot.testing.concurrency import AnyioTestBase

RESOURCE_MODEL = os.path.join(os.path.dirname(__file__), "resource_model")


class TestMantisDevice(AnyioTestBase):
  def make_device(self) -> MantisDevice:
    return Mantis(io=FakeFTDI())

  def test_inherits_managed_lifespan_methods(self) -> None:
    device = self.make_device()
    assert isinstance(device, HasLifespan)
    assert MantisDevice.setup is HasLifespan.setup
    assert MantisDevice.stop is HasLifespan.stop
    assert MantisDevice.__aenter__ is HasLifespan.__aenter__
    assert MantisDevice.__aexit__ is HasLifespan.__aexit__

  def test_every_part_has_its_model_file(self) -> None:
    device = self.make_device()
    for resource in (device, device.left_chip_rack, device.right_chip_rack):
      assert os.path.isfile(os.path.join(RESOURCE_MODEL, f"{resource.model}.glb"))

  def test_a_chip_spot_per_slot_in_its_rack(self) -> None:
    device = self.make_device()
    assert sorted(device.chip_spots) == [1, 2, 3, 4, 5, 6]
    for slot, spot in device.chip_spots.items():
      rack = device.right_chip_rack if slot in (1, 2) else device.left_chip_rack
      assert spot.parent is rack

  def test_a_chip_in_its_spot_lies_over_its_rack(self) -> None:
    device = self.make_device()
    for slot, spot in device.chip_spots.items():
      chip = mantis_chip_low_volume_hi_fi(name=f"chip_{slot}")
      spot.assign_child_resource(resource=chip)
      rack = device.right_chip_rack if slot in (1, 2) else device.left_chip_rack
      centre = chip.get_location_wrt(device, x="c", y="c", z="b")
      rack_front_left = rack.get_location_wrt(device)
      assert centre.x > rack_front_left.x
      assert centre.x < rack_front_left.x + rack.get_size_x()
      assert centre.y > rack_front_left.y
      assert centre.y < rack_front_left.y + rack.get_size_y()

  def test_simulation_refuses_a_link(self) -> None:
    with pytest.raises(ValueError):
      Mantis(simulation=True, io=FakeFTDI())

  async def test_exit_with_attached_chip_refuses_unless_accepted(self) -> None:
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "declared.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(
          {
            **recording,
            "zones": SYNTHETIC_ZONES_BLOCK,
            "approach_paths": {"chip_slot_1": CHIP_SLOT_1_PATH},
          },
          f,
        )
      device = Mantis(simulation=True, declared_configuration_json=path)
      with mock.patch.object(LedBar, "_sleep", autospec=True):
        with pytest.raises(MantisMotionError, match="a chip is still on the head"):
          async with device:
            assert device.arm is not None
            await device.arm.pick_up_chip(slot=1)
        assert not device.is_active
        assert not device.driver._connected

        async with device.lifespan(accept_chip_left_on_head=True):
          assert device.arm is not None
          assert device.arm.attached_chip == 1
        assert not device.is_active
        assert device.arm is not None
        assert device.arm.attached_chip == 1

        async with device:
          assert device.arm is not None
          assert device.arm.attached_chip == 1
          await device.arm.return_chip()
          assert device.arm.attached_chip is None


class TestArmModel(AnyioTestBase):
  async def test_a_move_poses_the_links_and_moves_the_z_column(self) -> None:
    device = Mantis(simulation=True)
    with mock.patch.object(LedBar, "_sleep", autospec=True):
      async with device:
        assert device.arm is not None
        column = device.z_column.location
        assert column is not None
        # The simulated arm has no offsets: its joint state is the links' own angles.
        theta_1, theta_2 = compute_joint_angles(
          location=MantisArmCoordinate(x=15.0, y=60.0, z=None)
        )
        await device.arm.move_to_joint_state(joint_state={0: theta_1, 1: theta_2, 2: 10.0})
        assert device.z_column.get_absolute_location().z == pytest.approx(column.z - 10.0)
        idle, posed = Mantis(simulation=True), Mantis(simulation=True)
        posed._update_arm_model(theta_1=theta_1, theta_2=theta_2, z=-10.0)
        for name, link in device.arm_links.items():
          assert link.rotation.z == pytest.approx(posed.arm_links[name].rotation.z)
          assert link.rotation.z != pytest.approx(idle.arm_links[name].rotation.z)

  async def test_the_five_bar_follows_the_motors_and_places_every_link(self) -> None:
    device = Mantis(simulation=True)
    with mock.patch.object(LedBar, "_sleep", autospec=True):
      async with device:
        assert device.arm is not None
        theta_1, theta_2 = compute_joint_angles(
          location=MantisArmCoordinate(x=-30.0, y=45.0, z=None)
        )
        await device.arm.move_to_joint_state(joint_state={0: theta_1, 1: theta_2, 2: 10.0})
        five_bar = device.serialize_state()["five_bar"]
        # Unwrapped as the motors turn: a viewer turns each short link the way its motor does.
        assert five_bar["angles"] == {"right": theta_1 - 180.0, "left": -theta_2}
        for side in ("right", "left"):
          short = device.arm_links[f"{side}_short_arm"]
          angle = math.radians(five_bar["angles"][side])
          length = five_bar["lengths"][f"{side}_short_arm"]
          base = five_bar["bases"][side]
          distal = cast(Coordinate, short.distal_joint)
          turned = math.radians(short.rotation.z)
          location = cast(Coordinate, short.location)
          assert base[0] + length * math.cos(angle) == pytest.approx(
            location.x + distal.x * math.cos(turned) - distal.y * math.sin(turned), abs=1e-6
          )
          assert base[1] + length * math.sin(angle) == pytest.approx(
            location.y + distal.x * math.sin(turned) + distal.y * math.cos(turned), abs=1e-6
          )
