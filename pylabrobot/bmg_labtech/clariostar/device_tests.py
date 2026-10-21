"""Device tests over a fake io."""

import unittest

from pylabrobot.bmg_labtech.clariostar.device import (
  CLARIOstar,
  CLARIOstarDevice,
  CLARIOstarPlus,
)
from pylabrobot.bmg_labtech.clariostar.driver import master as master_module
from pylabrobot.bmg_labtech.clariostar.driver.configuration_tests import decoded
from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance_tests import (
  STATUS_MEASURING,
  STOP_FRAME,
  STOP_REPLY,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.temperature_controller import (
  TemperatureController,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.temperature_controller_tests import (
  MONITOR_REPLY,
  OFF_FRAME,
  OFF_REPLY,
)
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  INIT_REPLY,
  PARAMETERS_REPLY,
  STATUS_BUSY,
  STATUS_IDLE,
  STATUS_QUERY_FRAME,
  STATUS_TEMPERATURES,
  FakeFTDI,
  make_driver,
)
from pylabrobot.resources import Resource


class DeviceTests(unittest.IsolatedAsyncioTestCase):
  def test_two_readers_in_one_tree(self):
    deck = Resource("deck", size_x=1000, size_y=1000, size_z=0)
    for name in ("reader_1", "reader_2"):
      deck.assign_child_resource(CLARIOstar(name, io=FakeFTDI()), location=deck.location)
    self.assertEqual(
      {r.name for r in deck.get_all_children()},
      {"reader_1", "reader_1_plate_holder", "reader_2", "reader_2_plate_holder"},
    )

  def test_the_setup_summary_names_the_reader(self):
    reader = CLARIOstar("reader_2", io=FakeFTDI())
    reader.driver.configuration = decoded()
    summary = reader.driver.format_setup_summary()
    self.assertTrue(summary.startswith("[reader_2] Connected on FakeFTDI 0x0403:0xbb68"), summary)
    self.assertIn("Firmware: 1.35", summary)

  async def setup_reader(self, reader: CLARIOstarDevice) -> None:
    assert isinstance(reader.driver.io, FakeFTDI)
    reader.driver._RETRY_SETTLE = 0
    reader.driver.io.replies = [
      INIT_REPLY,
      STATUS_IDLE,
      STATUS_IDLE,
      STATUS_IDLE,
      PARAMETERS_REPLY,
      STATUS_IDLE,  # temperature monitoring: sensors off
      STATUS_IDLE,
      MONITOR_REPLY,
    ]
    await reader.setup()

  async def test_reader_matching_the_factory_sets_up(self):
    reader = CLARIOstarPlus(io=FakeFTDI())
    await self.setup_reader(reader)
    self.assertEqual(reader.driver.configuration.model, "CLARIOstarPlus")

  async def test_reader_of_another_model_is_refused(self):
    with self.assertRaises(RuntimeError) as ctx:
      await self.setup_reader(CLARIOstar(io=FakeFTDI()))
    self.assertIn("built as a CLARIOstar, the reader is a CLARIOstarPlus", str(ctx.exception))

  async def test_generic_device_is_not_checked(self):
    await self.setup_reader(CLARIOstarDevice(io=FakeFTDI()))


class StopTests(unittest.IsolatedAsyncioTestCase):
  def make_driver_with_temperature_controller(self, io: FakeFTDI):
    driver = make_driver(io)
    driver.temperature_controller = TemperatureController(driver)
    return driver

  async def test_stop_switches_heating_off(self):
    io = FakeFTDI()
    # idle; sensors reporting; OFF; drawer in; no plate
    io.replies = [
      STATUS_IDLE,
      STATUS_TEMPERATURES,
      STATUS_IDLE,
      OFF_REPLY,
      STATUS_IDLE,
      STATUS_IDLE,
    ]
    await self.make_driver_with_temperature_controller(io).stop()
    self.assertEqual(io.written, [STATUS_QUERY_FRAME] * 3 + [OFF_FRAME] + [STATUS_QUERY_FRAME] * 2)

  async def test_stop_ends_a_running_measurement(self):
    io = FakeFTDI()
    # a RUN running; stopped; sensors off and no target; drawer in; no plate
    io.replies = [STATUS_MEASURING, STOP_REPLY, STATUS_IDLE, STATUS_IDLE, STATUS_IDLE]
    await self.make_driver_with_temperature_controller(io).stop()
    self.assertEqual(io.written, [STATUS_QUERY_FRAME, STOP_FRAME] + [STATUS_QUERY_FRAME] * 3)

  async def test_stop_only_warns_about_another_busy_command(self):
    io = FakeFTDI()
    io.replies = [STATUS_BUSY, STATUS_IDLE, STATUS_IDLE, STATUS_IDLE]  # busy with the drawer
    with self.assertLogs(master_module.logger, "WARNING") as logs:
      await self.make_driver_with_temperature_controller(io).stop()
    self.assertNotIn(STOP_FRAME, io.written)
    self.assertIn("other than a measurement; it keeps running", logs.output[0])
