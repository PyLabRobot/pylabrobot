"""End-to-end BenchCel backend tests against the in-process mock server."""

from __future__ import annotations

import unittest

from pylabrobot.storage.agilent.benchcel_backend import BenchCel4RBackend, BenchCelDeviceError
from pylabrobot.storage.agilent.benchcel_mock_server import BenchCelMockServer


class BenchCelMockServerTests(unittest.IsolatedAsyncioTestCase):
  async def test_motion_status_and_axis_bounds_over_tcp(self):
    async with BenchCelMockServer() as server:
      backend = BenchCel4RBackend(
        host=server.host,
        port=server.port,
        timeout=2.0,
        read_poll_timeout=0.01,
      )
      await backend.setup()
      try:
        await backend.home()
        await backend.move_to_stacker(3)
        status = await backend.request_arm_status()
        self.assertAlmostEqual(status.x, 90.0)
        self.assertAlmostEqual(status.z, 0.0)

        await backend.move_x(5.0)
        status = await backend.request_arm_status()
        self.assertAlmostEqual(status.x, 95.0)

        bounds = await backend.request_axis_bounds()
        self.assertAlmostEqual(bounds.theta_min, -115.0)
        self.assertAlmostEqual(bounds.x_max, 360.9, places=3)
      finally:
        await backend.stop()

  async def test_stacker_sensor_query_over_tcp(self):
    async with BenchCelMockServer() as server:
      backend = BenchCel4RBackend(
        host=server.host,
        port=server.port,
        timeout=2.0,
        read_poll_timeout=0.01,
      )
      await backend.setup()
      try:
        sensors = await backend.request_all_stacker_sensors()
        self.assertEqual([s.stacker for s in sensors], [1, 2, 3, 4])
        self.assertEqual(sensors[2].plate_presence, 128)
        self.assertTrue(sensors[2].plate_present())
      finally:
        await backend.stop()

  async def test_jog_out_of_bounds_raises_device_error(self):
    async with BenchCelMockServer() as server:
      backend = BenchCel4RBackend(
        host=server.host,
        port=server.port,
        timeout=2.0,
        read_poll_timeout=0.01,
      )
      await backend.setup()
      try:
        with self.assertRaises(BenchCelDeviceError) as cm:
          await backend.move_x(500.0)
        self.assertEqual(cm.exception.message, "X position out of bounds")
      finally:
        await backend.stop()

  async def test_teachpoint_save_then_move_over_tcp(self):
    async with BenchCelMockServer() as server:
      backend = BenchCel4RBackend(
        host=server.host,
        port=server.port,
        timeout=2.0,
        read_poll_timeout=0.01,
      )
      await backend.setup()
      try:
        await backend.save_test_left_teachpoint()
        await backend.move_to_teachpoint(0x1F, approach_height=20.0)
        status = await backend.request_arm_status()
        self.assertAlmostEqual(status.theta, 89.99874114990234, places=3)
        self.assertAlmostEqual(status.x, -360.8802795410156, places=3)
        self.assertAlmostEqual(status.z, 10.0)
      finally:
        await backend.stop()

  async def test_stacker_gripper_diagnostic_over_tcp(self):
    async with BenchCelMockServer() as server:
      backend = BenchCel4RBackend(
        host=server.host,
        port=server.port,
        timeout=2.0,
        read_poll_timeout=0.01,
      )
      await backend.setup()
      try:
        await backend.dangerously_open_stacker_grippers(1)
        self.assertTrue(server.stacker_grippers_open[0])
        await backend.close_stacker_grippers(1)
        self.assertFalse(server.stacker_grippers_open[0])
      finally:
        await backend.stop()

  async def test_set_labware_over_tcp(self):
    from pylabrobot.resources.plate import Plate

    async with BenchCelMockServer() as server:
      backend = BenchCel4RBackend(
        host=server.host,
        port=server.port,
        timeout=2.0,
        read_poll_timeout=0.01,
      )
      await backend.setup()
      try:
        plate = Plate("p", size_x=127.76, size_y=85.48, size_z=14.4, ordered_items={})
        settings = await backend.set_labware(plate)
        self.assertAlmostEqual(settings.stacking_thickness, 12.9)
        self.assertIs(backend.labware_settings, settings)
        assert server.labware is not None
        self.assertAlmostEqual(server.labware.plate_size_z, 14.4, places=3)
        # A second call still works (stream stays in sync after the commit echo).
        await backend.set_labware(settings)
      finally:
        await backend.stop()

  async def test_set_labware_rejects_too_close_gripper_positions(self):
    from pylabrobot.storage.agilent import BenchCelLabwareSettings, PlateNotchSettings

    async with BenchCelMockServer() as server:
      backend = BenchCel4RBackend(
        host=server.host,
        port=server.port,
        timeout=2.0,
        read_poll_timeout=0.01,
      )
      await backend.setup()
      try:
        bad = BenchCelLabwareSettings(
          name="bad",
          plate_size_x=127.76,
          plate_size_y=85.48,
          plate_size_z=14.4,
          stacking_thickness=12.9,
          robot_gripper_offset=8.0,
          stacker_gripper_offset=5.0,
          sensor_offset=8.0,
          gripper_holding_plate_position=8.0,
          gripper_holding_stack_position=8.0,  # not above plate -> rejected
          notch_settings=PlateNotchSettings(),
        )
        with self.assertRaises(BenchCelDeviceError) as cm:
          await backend.set_labware(bad)
        self.assertEqual(cm.exception.message, "The labware gripper positions are too close")
        # The connection is still usable after a rejection.
        status = await backend.request_arm_status()
        self.assertIsNotNone(status)
      finally:
        await backend.stop()


if __name__ == "__main__":
  unittest.main()
