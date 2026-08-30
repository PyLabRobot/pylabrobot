import unittest
from unittest.mock import AsyncMock, call, patch

from pylabrobot.agilent.vspin import _access2_protocol as protocol
from pylabrobot.agilent.vspin.access2 import Access2Driver
from pylabrobot.io.binary import Writer


def _status(*, flags: int) -> protocol.Access2Status:
  return protocol.Access2Status(access2_status=flags, vspin_status=0)


class Access2TransportTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    ftdi_class = self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)
    self.io = ftdi_class.return_value
    self.driver = Access2Driver(device_id="test", timeout=0)

  async def test_status_response_supports_partial_reads(self):
    inner_response = (
      Writer()
      .u8(protocol.GET_STATUS + 1)
      .u16(5)
      .u8(0)
      .u8(protocol.STATUS_INITIALIZED | protocol.STATUS_HOMED)
      .u8(0)
      .u8(0)
      .u8(0)
      .finish()
    )
    response = protocol.build_ftdi_frame(inner_response)
    command_frame = protocol.build_ftdi_frame(protocol.build_get_status())
    self.io.write = AsyncMock(return_value=len(command_frame))
    self.io.read = AsyncMock(side_effect=[response[:2], response[2:5], response[5:8], response[8:]])

    status = await self.driver.request_status()

    self.assertTrue(status.initialized)
    self.assertTrue(status.homed)
    self.io.write.assert_awaited_once_with(command_frame)
    self.assertEqual(
      [read.args[0] for read in self.io.read.await_args_list],
      [5, 3, 10, 7],
    )

  async def test_partial_header_times_out_with_context(self):
    self.io.read = AsyncMock(side_effect=[b"\x11\x05", b""])

    with self.assertRaisesRegex(TimeoutError, "2 of 5 expected bytes"):
      await self.driver._read_frame()


class Access2WorkflowTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)
    self.driver = Access2Driver(device_id="test")
    self.driver._move_to_position = AsyncMock()  # type: ignore[method-assign]
    self.driver._move_to_location = AsyncMock()  # type: ignore[method-assign]

  async def test_load_uses_named_motion_sequence(self):
    ready = _status(flags=protocol.STATUS_INITIALIZED | protocol.STATUS_HOMED)
    self.driver.request_status = AsyncMock(side_effect=[ready, ready])  # type: ignore[method-assign]
    self.driver.request_sensor_values = AsyncMock(return_value=0)  # type: ignore[method-assign]

    await self.driver.load()

    self.driver._move_to_position.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(
          protocol.AXIS_GRIPPER,
          0,
          profile=protocol.PROFILE_DYNAMIC_EMPTY,
          speed=protocol.SPEED_FAST,
        ),
        call(protocol.AXIS_GRIPPER, 5.68),
        call(protocol.AXIS_GRIPPER, 0),
      ]
    )
    self.driver._move_to_location.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(protocol.LOCATION_PICK, 3, 10),
        call(
          protocol.LOCATION_BUCKET_1,
          3,
          10,
          profile=protocol.PROFILE_DYNAMIC_FULL,
        ),
        call(protocol.LOCATION_PARK, 3, 10),
      ]
    )

  async def test_load_stops_before_gripping_when_plate_is_absent(self):
    ready = _status(flags=protocol.STATUS_INITIALIZED | protocol.STATUS_HOMED)
    self.driver.request_status = AsyncMock(return_value=ready)  # type: ignore[method-assign]
    self.driver.request_sensor_values = AsyncMock(  # type: ignore[method-assign]
      return_value=protocol.SENSOR_NO_PLATE
    )

    with self.assertRaisesRegex(RuntimeError, "no plate found on stage"):
      await self.driver.load()

    self.driver._move_to_position.assert_awaited_once()  # type: ignore[attr-defined]
    self.driver._move_to_location.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.LOCATION_PICK, 3, 10
    )

  async def test_estop_prevents_load_motion(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(
        flags=(protocol.STATUS_INITIALIZED | protocol.STATUS_HOMED | protocol.STATUS_ESTOP_ACTIVE)
      )
    )

    with self.assertRaisesRegex(RuntimeError, "emergency stop"):
      await self.driver.load()

    self.driver._move_to_position.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_to_location.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_homed_status_does_not_hide_estop(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=protocol.STATUS_HOMED | protocol.STATUS_ESTOP_ACTIVE)
    )

    with self.assertRaisesRegex(RuntimeError, "emergency stop"):
      await self.driver._wait_until_homed()
