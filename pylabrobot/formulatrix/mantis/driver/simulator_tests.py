"""Simulated Mantis tests."""

import json
import logging
import os
import tempfile
from typing import Any

import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
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
from pylabrobot.formulatrix.mantis.driver.features.arm_v3_tests import SYNTHETIC_V3_ARM_BLOCK
from pylabrobot.formulatrix.mantis.driver.features.plate_clamp import PlateClamp
from pylabrobot.formulatrix.mantis.driver.features.pneumatic_plate_clamp import PneumaticPlateClamp
from pylabrobot.formulatrix.mantis.driver.master import SEQUENCER_QUEUE_SIZE
from pylabrobot.formulatrix.mantis.driver.simulator import (
  MantisSimulationArm,
  MantisSimulationDriver,
  MantisSimulationLedBar,
)
from pylabrobot.testing.concurrency import AnyioTestBase


class TestMantisSimulation(AnyioTestBase):
  """Verify simulated Mantis v3 and v4 lifecycles, clamp restoration, and LED bar state."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    plr_logger = logging.getLogger("pylabrobot")
    old_propagate = plr_logger.propagate
    plr_logger.propagate = True

    def restore_propagate() -> None:
      plr_logger.propagate = old_propagate

    stack.callback(restore_propagate)
    self.mantis = Mantis(simulation=True)
    await stack.enter_async_context(self.mantis.lifespan())

  def test_inherits_managed_lifespan_methods(self) -> None:
    assert isinstance(self.mantis.driver, HasLifespan)
    assert MantisSimulationDriver.setup is HasLifespan.setup
    assert MantisSimulationDriver.stop is HasLifespan.stop
    assert MantisSimulationDriver.__aenter__ is HasLifespan.__aenter__
    assert MantisSimulationDriver.__aexit__ is HasLifespan.__aexit__

  async def test_setup_reports_the_recorded_generation(self) -> None:
    assert self.mantis.driver.configuration.generation == "v4"

  async def test_clamp_starts_idle_then_locks_and_unlocks(self) -> None:
    clamp = self.mantis.plate_clamp
    assert isinstance(clamp, PlateClamp)
    assert await clamp.request_state() == 0
    await clamp.lock(level=3)
    assert await clamp.request_state() == 3
    await clamp.unlock()
    assert await clamp.request_state() == 6

  async def test_pneumatic_clamp_uses_and_restores_the_simulated_pump(self) -> None:
    """Pneumatic simulation follows the valve and pump lifecycle without any USB link."""
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as file:
      recording = json.load(file)
    recording["device"]["generation"] = "v3"
    recording["device"]["firmware_versions"].update({"11": None, "12": None})
    recording["plate_clamp"] = {"controller": "pressure_regulator", "auxiliary_output": 2}
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "pneumatic.json")
      with open(path, "w", encoding="utf-8") as file:
        json.dump(recording, file)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      async with mantis.lifespan(skip_arm=True):
        assert isinstance(mantis.plate_clamp, PneumaticPlateClamp) and mantis.pump is not None
        clamp = mantis.plate_clamp
        pump = mantis.pump
        await clamp.lock()
        assert await clamp.request_locked()
        assert await pump.request_pump_on()
      assert not await clamp.request_locked()
      assert not await pump.request_pump_on()

  async def test_exit_releases_a_locked_clamp(self) -> None:
    mantis = Mantis(simulation=True)
    async with mantis:
      clamp = mantis.plate_clamp
      assert isinstance(clamp, PlateClamp)
      await clamp.lock(level=3)
    async with mantis:
      assert mantis.plate_clamp is clamp
      assert await clamp.request_state() == 6

  async def test_board_the_recording_declares_absent_does_not_answer(self) -> None:
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    recording["device"]["firmware_versions"]["11"] = None
    recording["device"]["generation"] = "v3"
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "recording.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(recording, f)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      async with mantis.lifespan(skip_arm=True):
        assert mantis.driver.configuration.generation == "v3"
        assert mantis.led_bar is None
        with pytest.raises(MantisTimeoutError):
          await mantis.driver.request_firmware_version(board=11)

  async def test_v3_initialization_uses_the_declared_route_and_can_repeat(self) -> None:
    """A v3 simulation homes every axis and exits without the v4 park move."""
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as file:
      recording = json.load(file)
    recording["device"]["firmware_versions"]["11"] = None
    recording["device"]["generation"] = "v3"
    recording["arm"] = SYNTHETIC_V3_ARM_BLOCK
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "v3.json")
      with open(path, "w", encoding="utf-8") as file:
        json.dump(recording, file)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      for _ in range(2):
        async with mantis:
          assert mantis.arm is not None
          assert mantis.arm.is_initialized
          position = await mantis.arm.request_head_position()
          assert position.x == pytest.approx(15.0)
          assert position.y == pytest.approx(31.177)
          for motor in (0, 1, 2):
            assert await mantis.driver.request_motor_status(motor=motor) == 4
        assert mantis.arm is not None
        assert not mantis.arm.is_initialized
        assert await mantis.driver.request_motor_position(motor=2) == 0.0

  async def test_rfid_reads_every_declared_slot_empty(self) -> None:
    rfid = self.mantis.rfid
    assert rfid is not None
    assert await rfid.request_tags() == {slot: None for slot in range(1, 7)}

  async def test_reads_answer_from_the_configuration(self) -> None:
    assert await self.mantis.driver.request_firmware_version(board=12) == "0.1.8 DBG"
    assert await self.mantis.driver.request_motor_limits(motor=2) == (-160.0, 2300.0)
    with pytest.raises(NotImplementedError, match="name is not simulated"):
      await self.mantis.driver.request_name()

  async def test_setup_brings_the_arm_to_idle_and_homes_z(self) -> None:
    driver = self.mantis.driver
    # Without offsets, the arm motors' idle targets are the links' angles for the head at idle.
    idle = [
      compute_device_position(motor=motor, position=angle)
      for motor, angle in enumerate(compute_joint_angles(x=15.0, y=31.177))
    ]
    for motor, status, position in ((0, 0, idle[0]), (1, 0, idle[1]), (2, 4, 0.0)):
      assert await driver.request_motor_status(motor=motor) == status
      assert await driver.request_motor_position(motor=motor) == position
      assert await driver.request_encoder_position(motor=motor) == position
    with pytest.raises(NotImplementedError, match="absolute encoders are not simulated"):
      await driver.request_absolute_encoder_position(motor=0)
    with pytest.raises(ValueError):
      await driver.request_motor_status(motor=3)

  async def test_the_head_stands_at_idle_without_offsets(self) -> None:
    arm = self.mantis.arm
    assert arm is not None
    head = await arm.request_head_position()
    assert head.x == pytest.approx(15.0, abs=1e-9)
    assert head.y == pytest.approx(31.177, abs=1e-9)
    joint_position = await arm.request_joint_position()
    assert await arm.request_absolute_joint_position() == {
      0: joint_position[0],
      1: joint_position[1],
    }

  async def test_exit_parks_and_lowers_z(self) -> None:
    mantis = Mantis(simulation=True)
    async with mantis:
      arm = mantis.driver.arm
      assert isinstance(arm, MantisSimulationArm)
    assert arm._positions[2] == pytest.approx(compute_device_position(motor=2, position=SHUTDOWN_Z))
    async with mantis:
      assert arm._positions[2] == 0.0

  async def test_an_arm_away_from_idle_is_refused(self) -> None:
    mantis = Mantis(simulation=True)
    async with mantis.lifespan(skip_arm=True):
      arm = mantis.driver.arm
      assert isinstance(arm, MantisSimulationArm)
      theta_1, theta_2 = compute_joint_angles(x=-57.0, y=0.0)
      arm._absolute_positions = {
        0: compute_device_position(motor=0, position=theta_1),
        1: compute_device_position(motor=1, position=theta_2),
      }
      with pytest.raises(MantisMotionError, match="moved to idle only from x"):
        await arm.initialize()

  async def test_the_long_links_meet_at_the_head(self) -> None:
    assert self.mantis.arm is not None
    head = await self.mantis.arm.request_head_position()
    for name in ("right_long_arm", "left_long_arm"):
      link = self.mantis.arm_links[name]
      assert link.distal_joint is not None
      matrix = link.get_absolute_rotation().get_rotation_matrix()
      corner = link.get_absolute_location()
      joint = (link.distal_joint.x, link.distal_joint.y, link.distal_joint.z)
      end_x = corner.x + sum(matrix[0][k] * joint[k] for k in range(3))
      end_y = corner.y + sum(matrix[1][k] * joint[k] for k in range(3))
      # The arm frame is the device frame moved to the right motor's axis and mirrored in y.
      assert end_x == pytest.approx(72.549 + head.x, abs=1e-2)
      assert end_y == pytest.approx(142.594 - head.y, abs=1e-2)

  async def test_pump_reads_off_with_the_recorded_calibration(self) -> None:
    pump = self.mantis.pump
    assert pump is not None
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recorded = json.load(f)["device"]["feedback_sensor_parameters"]
    assert self.mantis.driver.configuration.feedback_sensor_parameters == {
      controller: tuple(value) for controller, value in recorded.items()
    }
    assert await pump.request_feedback_sensor_parameters(controller="vacuum") == tuple(
      recorded["vacuum"]
    )
    assert await pump.measure_pressure(controller="chip") == 0.0
    assert await pump.request_controller_status(controller="chip") == 0
    assert not await pump.request_controller_enabled(controller="chip")
    assert not await pump.request_pump_on()
    with pytest.raises(ValueError):
      await pump.measure_pressure(controller="air")  # type: ignore[arg-type]

  async def test_an_older_declared_file_takes_what_it_lacks_from_the_recording(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    del recording["device"]["feedback_sensor_parameters"]
    del recording["led_bar"]
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "older.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(recording, f)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      caplog.clear()
      with caplog.at_level(logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.master"):
        async with mantis:
          assert mantis.pump is not None and mantis.led_bar is not None
          assert mantis.led_bar.number_of_leds == 20
          assert await mantis.pump.request_feedback_sensor_parameters(controller="bottle") == (
            0.010651180520653725,
            -13.580931663513184,
          )
    warnings = [
      rec.getMessage()
      for rec in caplog.records
      if rec.name == "pylabrobot.formulatrix.mantis.driver.master"
      and rec.levelno >= logging.WARNING
    ]
    assert len(warnings) == 1
    assert f"led_bar from {RECORDING_MANTIS_V4}" in warnings[0]

  async def test_led_bar_takes_every_command(self) -> None:
    bar = self.mantis.led_bar
    assert isinstance(bar, MantisSimulationLedBar)
    assert bar.number_of_leds == 20
    await bar.set_color(color="#FF0080")
    await bar.set_color(color=["green"] * 10 + ["off"] * 10)
    await bar.start_animation(effect="wave")
    await bar.stop_animation()
    await bar.turn_off()
    with pytest.raises(ValueError):
      await bar.set_color(color="purple")

  async def test_sequencer_plays_what_is_queued_once_started(self) -> None:
    driver = self.mantis.driver
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    assert item == 1
    with pytest.raises(MantisTimeoutError):
      await driver._wait_for_queue_item(item=item, timeout=1.0)
    assert driver._sequence_progress is None
    await driver._unchecked_fw_start_sequencer()
    await driver._wait_for_queue_item(item=item, timeout=1.0)
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    await driver._wait_for_queue_item(item=item, timeout=1.0)
    assert driver._sequence_progress == item
    await driver._unchecked_fw_stop_sequencer()
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    with pytest.raises(MantisTimeoutError):
      await driver._wait_for_queue_item(item=item, timeout=1.0)

  async def test_stopped_sequencer_fills_its_queue_and_a_clear_empties_it(self) -> None:
    driver = self.mantis.driver
    free_slots = [
      (
        await driver._unchecked_fw_queue_write_ppi(
          item=item, duration=10, start_address=40, values=[31]
        )
      )[0]
      for item in range(1, SEQUENCER_QUEUE_SIZE + 2)
    ]
    assert free_slots == list(range(SEQUENCER_QUEUE_SIZE - 1, -2, -1))
    driver.default_queue_full_timeout = 0.01
    with pytest.raises(MantisTimeoutError, match="stayed full"):
      await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    await driver._unchecked_fw_clear_sequencer()
    await driver._queue_ppi_write(duration=10, start_address=40, values=[31])

  async def test_simulated_mantis_sends_nothing(self) -> None:
    with pytest.raises(NotImplementedError, match="sends nothing"):
      await self.mantis.driver._send_command(board=0, command=3)
