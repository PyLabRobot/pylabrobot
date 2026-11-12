"""Simulated Mantis tests."""

import json
import os
import tempfile
import unittest

from pylabrobot.formulatrix.mantis import Mantis
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.arm_kinematics import (
  compute_device_position,
  compute_joint_angles,
)
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisMotionError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features.arm import SHUTDOWN_Z
from pylabrobot.formulatrix.mantis.driver.master import SEQUENCER_QUEUE_SIZE
from pylabrobot.formulatrix.mantis.driver.oem_config_tests import (
  SYNTHETIC_STAGE_BLOCK,
  SYNTHETIC_STATIONS_BLOCK,
  SYNTHETIC_WASTE_ROUTE,
  SYNTHETIC_ZONES_BLOCK,
)
from pylabrobot.formulatrix.mantis.driver.simulator import (
  MantisSimulationArm,
  MantisSimulationLedBar,
)
from pylabrobot.formulatrix.mantis.driver.stage_transform import (
  REFERENCE_HEIGHT,
  compute_arm_frame_position,
)
from pylabrobot.resources import Coordinate
from pylabrobot.resources.corning import cor_96_wellplate_360uL_Fb


class SimulationTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.mantis = Mantis(simulation=True)
    await self.mantis.setup()

  async def test_setup_reports_the_recorded_generation(self):
    self.assertEqual(self.mantis.driver.configuration.generation, "v4")

  async def test_clamp_starts_idle_then_locks_and_unlocks(self):
    clamp = self.mantis.plate_clamp
    assert clamp is not None
    self.assertEqual(await clamp.request_state(), 0)
    await clamp.lock(level=3)
    self.assertEqual(await clamp.request_state(), 3)
    await clamp.unlock()
    self.assertEqual(await clamp.request_state(), 6)

  async def test_stop_releases_a_locked_clamp(self):
    clamp = self.mantis.plate_clamp
    assert clamp is not None
    await clamp.lock(level=3)
    await self.mantis.stop()
    await self.mantis.setup()
    self.assertEqual(await clamp.request_state(), 6)

  async def test_board_the_recording_declares_absent_does_not_answer(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    recording["device"]["firmware_versions"]["11"] = None
    recording["device"]["generation"] = "v3"
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "recording.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(recording, f)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      await mantis.setup()
      self.addAsyncCleanup(mantis.stop)
    self.assertEqual(mantis.driver.configuration.generation, "v3")
    self.assertIsNone(mantis.led_bar)
    with self.assertRaises(MantisTimeoutError):
      await mantis.driver.request_firmware_version(board=11)

  async def test_rfid_reads_every_declared_slot_empty(self):
    rfid = self.mantis.rfid
    assert rfid is not None
    self.assertEqual(await rfid.request_tags(), {slot: None for slot in range(1, 7)})

  async def test_reads_answer_from_the_configuration(self):
    self.assertEqual(await self.mantis.driver.request_firmware_version(board=12), "0.1.8 DBG")
    self.assertEqual(await self.mantis.driver.request_motor_limits(motor=2), (-160.0, 2300.0))
    with self.assertRaisesRegex(NotImplementedError, "name is not simulated"):
      await self.mantis.driver.request_name()

  async def test_setup_brings_the_arm_to_idle_and_homes_z(self):
    driver = self.mantis.driver
    # Without offsets, the arm motors' idle targets are the links' angles for the head at idle.
    idle = [
      compute_device_position(motor=motor, position=angle)
      for motor, angle in enumerate(compute_joint_angles(x=15.0, y=31.177))
    ]
    for motor, status, position in ((0, 0, idle[0]), (1, 0, idle[1]), (2, 4, 0.0)):
      with self.subTest(motor=motor):
        self.assertEqual(await driver.request_motor_status(motor=motor), status)
        self.assertEqual(await driver.request_motor_position(motor=motor), position)
        self.assertEqual(await driver.request_encoder_position(motor=motor), position)
    with self.assertRaisesRegex(NotImplementedError, "absolute encoders are not simulated"):
      await driver.request_absolute_encoder_position(motor=0)
    with self.assertRaises(ValueError):
      await driver.request_motor_status(motor=3)

  async def test_the_head_stands_at_idle_without_offsets(self):
    arm = self.mantis.arm
    assert arm is not None
    head = await arm.request_head_position()
    self.assertAlmostEqual(head.x, 15.0, places=9)
    self.assertAlmostEqual(head.y, 31.177, places=9)
    joint_position = await arm.request_joint_position()
    self.assertEqual(
      await arm.request_absolute_joint_position(), {0: joint_position[0], 1: joint_position[1]}
    )

  async def test_stop_parks_and_lowers_z(self):
    arm = self.mantis.driver.arm
    assert isinstance(arm, MantisSimulationArm)
    await self.mantis.stop()
    self.assertAlmostEqual(arm._positions[2], compute_device_position(motor=2, position=SHUTDOWN_Z))
    await self.mantis.setup()
    self.assertEqual(arm._positions[2], 0.0)

  async def test_an_arm_away_from_idle_is_refused(self):
    mantis = Mantis(simulation=True)
    await mantis.setup(skip_arm=True)
    self.addAsyncCleanup(mantis.stop)
    arm = mantis.driver.arm
    assert isinstance(arm, MantisSimulationArm)
    theta_1, theta_2 = compute_joint_angles(x=-57.0, y=0.0)
    arm._absolute_positions = {
      0: compute_device_position(motor=0, position=theta_1),
      1: compute_device_position(motor=1, position=theta_2),
    }
    with self.assertRaisesRegex(MantisMotionError, "moved to idle only from x"):
      await arm.initialize()

  async def test_the_long_links_meet_at_the_head(self):
    assert self.mantis.arm is not None
    head = await self.mantis.arm.request_head_position()
    for name in ("right_long_arm", "left_long_arm"):
      with self.subTest(name=name):
        link = self.mantis.arm_links[name]
        assert link.distal_joint is not None
        matrix = link.get_absolute_rotation().get_rotation_matrix()
        corner = link.get_absolute_location()
        joint = (link.distal_joint.x, link.distal_joint.y, link.distal_joint.z)
        end_x = corner.x + sum(matrix[0][k] * joint[k] for k in range(3))
        end_y = corner.y + sum(matrix[1][k] * joint[k] for k in range(3))
        # The arm frame is the device frame moved to the right motor's axis and mirrored in y.
        self.assertAlmostEqual(end_x, 72.549 + head.x, places=2)
        self.assertAlmostEqual(end_y, 142.594 - head.y, places=2)

  async def test_pump_reads_off_with_the_recorded_calibration(self):
    pump = self.mantis.pump
    assert pump is not None
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recorded = json.load(f)["device"]["feedback_sensor_parameters"]
    self.assertEqual(
      self.mantis.driver.configuration.feedback_sensor_parameters,
      {controller: tuple(value) for controller, value in recorded.items()},
    )
    self.assertEqual(
      await pump.request_feedback_sensor_parameters(controller="vacuum"),
      tuple(recorded["vacuum"]),
    )
    self.assertEqual(await pump.measure_pressure(controller="chip"), 0.0)
    self.assertEqual(await pump.request_controller_status(controller="chip"), 0)
    self.assertFalse(await pump.request_controller_enabled(controller="chip"))
    self.assertFalse(await pump.request_pump_on())
    with self.assertRaises(ValueError):
      await pump.measure_pressure(controller="air")  # type: ignore[arg-type]

  async def test_an_older_declared_file_takes_what_it_lacks_from_the_recording(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    del recording["device"]["feedback_sensor_parameters"]
    del recording["led_bar"]
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "older.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(recording, f)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      with self.assertLogs("pylabrobot.formulatrix.mantis.driver.master", level="WARNING") as logs:
        await mantis.setup()
      self.addAsyncCleanup(mantis.stop)
    self.assertEqual(len(logs.output), 1)
    self.assertIn(f"led_bar from {RECORDING_MANTIS_V4}", logs.output[0])
    assert mantis.pump is not None and mantis.led_bar is not None
    self.assertEqual(mantis.led_bar.number_of_leds, 20)
    self.assertEqual(
      await mantis.pump.request_feedback_sensor_parameters(controller="bottle"),
      (0.010651180520653725, -13.580931663513184),
    )

  async def test_led_bar_takes_every_command(self):
    bar = self.mantis.led_bar
    assert isinstance(bar, MantisSimulationLedBar)
    self.assertEqual(bar.number_of_leds, 20)
    await bar.set_color(color="#FF0080")
    await bar.set_color(color=["green"] * 10 + ["off"] * 10)
    await bar.start_animation(effect="wave")
    await bar.stop_animation()
    await bar.turn_off()
    with self.assertRaises(ValueError):
      await bar.set_color(color="purple")

  async def test_sequencer_plays_what_is_queued_once_started(self):
    driver = self.mantis.driver
    # Setup leaves it started, after the pump's self-test queued seven valve states.
    await driver._unchecked_fw_stop_sequencer()
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    self.assertEqual(item, 8)
    with self.assertRaises(MantisTimeoutError):
      await driver._wait_for_queue_item(item=item, timeout=1.0)
    self.assertEqual(driver._sequence_progress, 7)
    await driver._unchecked_fw_start_sequencer()
    await driver._wait_for_queue_item(item=item, timeout=1.0)
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    await driver._wait_for_queue_item(item=item, timeout=1.0)
    self.assertEqual(driver._sequence_progress, item)
    await driver._unchecked_fw_stop_sequencer()
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    with self.assertRaises(MantisTimeoutError):
      await driver._wait_for_queue_item(item=item, timeout=1.0)

  async def test_stopped_sequencer_fills_its_queue_and_a_clear_empties_it(self):
    driver = self.mantis.driver
    await driver._unchecked_fw_stop_sequencer()
    free_slots = [
      (
        await driver._unchecked_fw_queue_write_ppi(
          item=item, duration=10, start_address=40, values=[31]
        )
      )[0]
      for item in range(1, SEQUENCER_QUEUE_SIZE + 2)
    ]
    self.assertEqual(free_slots, list(range(SEQUENCER_QUEUE_SIZE - 1, -2, -1)))
    driver.default_queue_full_timeout = 0.01
    with self.assertRaisesRegex(MantisTimeoutError, "stayed full"):
      await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    await driver._unchecked_fw_clear_sequencer()
    await driver._queue_ppi_write(duration=10, start_address=40, values=[31])

  async def test_simulated_mantis_sends_nothing(self):
    with self.assertRaisesRegex(NotImplementedError, "sends nothing"):
      await self.mantis.driver._send_command(board=0, command=3)


class SimulatedRouteTests(unittest.IsolatedAsyncioTestCase):
  """A simulated Mantis declaring the unit's zones and stations, synthetic."""

  async def asyncSetUp(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    path = os.path.join(directory.name, "declared.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump(
        {**recording, "zones": SYNTHETIC_ZONES_BLOCK, "stations": SYNTHETIC_STATIONS_BLOCK}, f
      )
    self.mantis = Mantis(simulation=True, declared_configuration_json=path)
    await self.mantis.setup()
    self.addAsyncCleanup(self.mantis.stop)
    arm = self.mantis.arm
    assert isinstance(arm, MantisSimulationArm)
    self.arm = arm

  async def assert_head_at(self, *, x: float, y: float, z: float):
    head = await self.arm.request_head_position()
    for name, value, expected in (("x", head.x, x), ("y", head.y, y), ("z", head.z, z)):
      with self.subTest(axis=name):
        self.assertAlmostEqual(value, expected, places=6)

  async def test_a_move_to_a_location_and_back_to_idle(self):
    await self.arm.move_to_location(Coordinate(-57.0, 0.0, -10.0))
    await self.assert_head_at(x=-57.0, y=0.0, z=-10.0)
    self.assertFalse(await self.arm.request_is_parked())
    await self.arm.park()
    await self.assert_head_at(x=15.0, y=31.177, z=0.0)
    self.assertTrue(await self.arm.request_is_parked())

  async def test_z_waypoints_and_joint_moves(self):
    await self.arm.move_z(1.57)
    await self.assert_head_at(x=15.0, y=31.177, z=1.57)
    await self.arm.move_through_waypoints(
      [Coordinate(60.0, 70.0, 0.0), Coordinate(-35.0, 70.0, 1.0)]
    )
    await self.assert_head_at(x=-35.0, y=70.0, z=1.0)
    await self.arm.move_to_joint_position({2: 5.0})
    self.assertEqual((await self.arm.request_joint_position())[2], 5.0)

  async def test_the_waste_station_is_reached_from_idle_only(self):
    await self.arm.move_to_waste_station()
    x, y, z = SYNTHETIC_WASTE_ROUTE[-1]
    assert x is not None and y is not None and z is not None
    await self.assert_head_at(x=x, y=y, z=-z)
    with self.assertRaisesRegex(MantisMotionError, "park"):
      await self.arm.move_to_wash_station(station=1)
    await self.arm.park()
    await self.arm.move_to_wash_station(station=1)

  async def test_stop_from_a_back_zone_parks_along_its_route_and_lowers_z(self):
    await self.arm.move_to_waste_station()
    await self.mantis.stop()
    self.assertAlmostEqual(
      self.arm._positions[2], compute_device_position(motor=2, position=SHUTDOWN_Z)
    )
    idle = compute_joint_angles(x=15.0, y=31.177)
    for motor in (0, 1):
      self.assertAlmostEqual(
        self.arm._positions[motor], compute_device_position(motor=motor, position=idle[motor])
      )
    await self.mantis.setup()

  async def test_recover_from_fault_brings_the_head_back_to_idle(self):
    await self.arm.move_to_location(Coordinate(80.0, -40.0, -5.0))
    await self.arm.recover_from_fault()
    self.assertTrue(self.arm.is_initialized)
    await self.assert_head_at(x=15.0, y=31.177, z=0.0)


class SimulatedWellplateTests(unittest.IsolatedAsyncioTestCase):
  """A simulated Mantis declaring synthetic zones and stage, a wellplate in its plate holder."""

  async def asyncSetUp(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    path = os.path.join(directory.name, "declared.json")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({**recording, "zones": SYNTHETIC_ZONES_BLOCK, "stage": SYNTHETIC_STAGE_BLOCK}, f)
    self.mantis = Mantis(simulation=True, declared_configuration_json=path)
    await self.mantis.setup()
    self.addAsyncCleanup(self.mantis.stop)
    self.wellplate = cor_96_wellplate_360uL_Fb(name="wellplate")
    self.mantis.plate_holder.assign_child_resource(self.wellplate)

  async def test_the_head_goes_over_each_corner_well(self):
    arm = self.mantis.arm
    assert arm is not None
    # Each well's centre from the wellplate's back-left corner, mm; 12.5 mm above its 14.2 mm frame.
    for name, x, y in (("A1", 14.3, 11.28), ("A12", 113.3, 11.28), ("H1", 14.3, 74.28)):
      with self.subTest(well=name):
        await arm.move_to_resource_location(
          self.wellplate.get_item(name), offset=Coordinate(z=10.0)
        )
        arm_x, arm_y, z = compute_arm_frame_position(
          position=(x, y, REFERENCE_HEIGHT - 26.7),
          origin=(-50.0, 40.0, 51.0),
          transformation_matrix=SYNTHETIC_STAGE_BLOCK["transformation_matrix"],
          offset_x=(0.0, 0.0, -0.0015),
          offset_y=(0.0, 0.0, 0.0025),
          arm_type_rotation_degree=0.0,
          arm_type_translation=(0.0, 0.0, 0.0),
        )
        head = await arm.request_head_position()
        self.assertAlmostEqual(head.x, arm_x, places=3)
        self.assertAlmostEqual(head.y, arm_y, places=3)
        self.assertAlmostEqual(head.z, -z, places=3)
    await arm.park()
    self.assertTrue(await arm.request_is_parked())


if __name__ == "__main__":
  unittest.main()
