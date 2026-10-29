"""Temperature controller tests over a fake io, with frames this driver exchanged with a
CLARIOstar Plus."""

import logging
import unittest
from unittest.mock import patch

import pytest

from pylabrobot.bmg_labtech.clariostar.device import CLARIOstarPlus
from pylabrobot.bmg_labtech.clariostar.driver import master as master_module
from pylabrobot.bmg_labtech.clariostar.driver.configuration_tests import decoded
from pylabrobot.bmg_labtech.clariostar.driver.errors import CommandRefusedError
from pylabrobot.bmg_labtech.clariostar.driver.features import (
  temperature_controller as temperature_controller_module,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.temperature_controller import (
  TemperatureController,
)
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  FIRMWARE_INFO_REPLY,
  PARAMETERS_REPLY,
  REFUSED_TEMPERATURE,
  STATUS_IDLE,
  STATUS_QUERY_FRAME,
  STATUS_TEMPERATURES,
  FakeFTDI,
  make_driver,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.testing.concurrency import AnyioTestBase

# Frames this driver exchanged with a CLARIOstar Plus (FTDI, firmware 1.35) on its second run.
MONITOR_FRAME = bytes.fromhex("02000b0c0600010000200d")
MONITOR_REPLY = bytes.fromhex("0200180c010500240000000000000000000000c00001100d")
OFF_FRAME = bytes.fromhex("02000b0c06000000001f0d")
OFF_REPLY = bytes.fromhex("0200180c010500240000000000000000000000c00001100d")
# A 20.0 C setpoint, refused with quit code 110 (REFUSED_TEMPERATURE).
SETPOINT_20_FRAME = bytes.fromhex("02000b0c0600c80000e70d")


def make_temperature_controller(io: FakeFTDI, extended: bool = False) -> TemperatureController:
  driver = make_driver(io)
  driver.configuration = decoded()
  driver.configuration.extended_temperature_range = extended
  temperature_controller = TemperatureController(driver)
  temperature_controller.default_poll_interval = 0
  return temperature_controller


class TestGating(AnyioTestBase):
  async def discover_with(self, temperature_controller_fitted: bool):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY, STATUS_IDLE, FIRMWARE_INFO_REPLY]
    driver = make_driver(io)
    driver.configuration = decoded()
    driver.configuration.temperature_controller = temperature_controller_fitted
    await driver.discover()
    return driver

  async def test_a_fitted_temperature_controller_is_built(self):
    assert isinstance((await self.discover_with(True)).temperature_controller, TemperatureController)

  async def test_no_temperature_controller_is_built(self):
    assert (await self.discover_with(False)).temperature_controller is None


class TestTemperatureController(AnyioTestBase):
  async def test_stop_temperature_control_keeps_monitoring(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, MONITOR_REPLY]
    temperature_controller = make_temperature_controller(io)
    temperature_controller.target_temperature = 30.0
    await temperature_controller.stop_temperature_control()
    assert io.written == [STATUS_QUERY_FRAME, MONITOR_FRAME]
    assert temperature_controller.target_temperature is None

  async def test_stop_monitoring_switches_off(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, OFF_REPLY]
    await make_temperature_controller(io).stop_monitoring()
    assert io.written == [STATUS_QUERY_FRAME, OFF_FRAME]

  async def test_setpoint_outside_range_is_refused_before_sending(self):
    io = FakeFTDI()
    temperature_controller = make_temperature_controller(io)
    for temperature in (24.9, 45.1):
      with pytest.raises(ValueError):
        await temperature_controller.start_temperature_control(temperature)
    assert io.written == []

  async def test_setpoint_is_sent_in_tenths_and_a_refusal_keeps_no_target(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ):
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    io = FakeFTDI()
    # sensors on (23.1 C), then the setpoint's ready check and its refusal
    io.replies = [STATUS_TEMPERATURES, STATUS_IDLE, REFUSED_TEMPERATURE]
    temperature_controller = make_temperature_controller(
      io, extended=True
    )  # 10-65 C, so 20.0 C reaches the reader
    with caplog.at_level(logging.WARNING, logger=temperature_controller_module.logger.name):
      with pytest.raises(CommandRefusedError) as ctx:
        await temperature_controller.start_temperature_control(20.0)
    assert "below the bottom plate's 23.1 C" in caplog.records[0].getMessage()
    assert io.written == [STATUS_QUERY_FRAME] * 2 + [SETPOINT_20_FRAME]
    assert ctx.value.quit_codes[0] == 110
    assert temperature_controller.target_temperature is None

  async def test_a_target_within_the_overshoot_tolerance_does_not_warn(self):
    io = FakeFTDI()
    io.replies = [STATUS_TEMPERATURES, STATUS_IDLE, REFUSED_TEMPERATURE]
    temperature_controller = make_temperature_controller(io, extended=True)
    with patch.object(temperature_controller_module.logger, "warning") as warning:
      with pytest.raises(CommandRefusedError):
        await temperature_controller.start_temperature_control(22.7)  # 0.4 C below 23.1 C
    warning.assert_not_called()

  async def test_setup_starts_monitoring_when_the_sensors_are_off(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, STATUS_IDLE, MONITOR_REPLY]
    driver = make_temperature_controller(io)._driver
    driver.temperature_controller = TemperatureController(driver)
    await driver._start_temperature_monitoring()
    assert io.written == [STATUS_QUERY_FRAME, STATUS_QUERY_FRAME, MONITOR_FRAME]

  async def test_setup_leaves_sensors_that_already_report(self):
    io = FakeFTDI()
    io.replies = [STATUS_TEMPERATURES]
    driver = make_temperature_controller(io)._driver
    driver.temperature_controller = TemperatureController(driver)
    await driver._start_temperature_monitoring()
    assert io.written == [STATUS_QUERY_FRAME]

  async def test_a_failed_monitoring_start_at_setup_only_warns(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ):
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, STATUS_IDLE, REFUSED_TEMPERATURE]
    driver = make_temperature_controller(io)._driver
    driver.temperature_controller = TemperatureController(driver)
    with caplog.at_level(logging.WARNING, logger=master_module.logger.name):
      await driver._start_temperature_monitoring()
    assert "could not start temperature monitoring" in caplog.records[0].getMessage()

  async def test_measure_temperature_from_the_sensors(self):
    io = FakeFTDI()
    io.replies = [STATUS_TEMPERATURES] * 3
    temperature_controller = make_temperature_controller(io)
    assert await temperature_controller.measure_temperature() == pytest.approx(23.55)
    assert await temperature_controller.measure_temperature("bottom") == 23.1
    assert await temperature_controller.measure_temperature("top") == 24.0

  async def test_measure_temperature_starts_monitoring(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, STATUS_IDLE, MONITOR_REPLY, STATUS_TEMPERATURES]
    temperature_controller = make_temperature_controller(io)
    assert await temperature_controller.measure_temperature() == pytest.approx(23.55)
    assert io.written == [STATUS_QUERY_FRAME, STATUS_QUERY_FRAME, MONITOR_FRAME, STATUS_QUERY_FRAME]

  async def test_measuring_while_heating_never_replaces_the_setpoint(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, STATUS_TEMPERATURES]  # sensors not reporting yet, then reporting
    temperature_controller = make_temperature_controller(io)
    temperature_controller.target_temperature = 30.0
    assert await temperature_controller.measure_temperature() == pytest.approx(23.55)
    assert io.written == [STATUS_QUERY_FRAME, STATUS_QUERY_FRAME]

  async def test_wait_without_a_target_raises(self):
    with pytest.raises(RuntimeError):
      await make_temperature_controller(FakeFTDI()).wait_for_temperature()


class TestSimulatedTemperatureController(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.reader = CLARIOstarPlus(simulation=True)
    await stack.enter_async_context(self.reader.lifespan())

  async def test_target_is_reached_at_once(self):
    temperature_controller = self.reader.temperature_controller
    assert temperature_controller is not None
    await temperature_controller.start_temperature_control(37.0)
    assert await temperature_controller.wait_for_temperature() == 37.0
    status = await self.reader.driver.request_status()
    assert (status.temperature_bottom, status.temperature_top) == (37.0, 37.0)

  async def test_no_ambient_without_a_target(self):
    temperature_controller = self.reader.temperature_controller
    assert temperature_controller is not None
    with pytest.raises(NotImplementedError):
      await temperature_controller.measure_temperature()


if __name__ == "__main__":
  unittest.main()
