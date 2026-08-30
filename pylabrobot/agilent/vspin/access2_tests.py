import dataclasses
import unittest
from collections import deque
from unittest.mock import AsyncMock, call, patch

from pylabrobot.agilent.vspin import _access2_protocol as protocol
from pylabrobot.agilent.vspin.access2 import Access2Driver
from pylabrobot.io.binary import Writer


_READY_FLAGS = protocol.STATUS_INITIALIZED | protocol.STATUS_HOMED


def _status(*, flags: int) -> protocol.Access2Status:
  return protocol.Access2Status(access2_status=flags, vspin_status=0)


def _short_status_data(flags: int = _READY_FLAGS) -> bytes:
  return Writer().u8(flags).u8(0).u8(0).u8(0).finish()


def _full_status_data(
  *,
  flags: int = _READY_FLAGS,
  gripper_status: int = protocol.AXIS_STATUS_MOVE_DONE,
  gripper_position: float = 0,
  y_status: int = protocol.AXIS_STATUS_MOVE_DONE,
  y_position: float = 100,
  z_status: int = protocol.AXIS_STATUS_MOVE_DONE,
  z_position: float = 20,
) -> bytes:
  return (
    Writer()
    .u8(flags)
    .u8(0)
    .u8(gripper_status)
    .f32(gripper_position)
    .u8(y_status)
    .f32(y_position)
    .u8(z_status)
    .f32(z_position)
    .finish()
  )


def _build_ftdi_reply(command: bytes, data: bytes = b"", result: int = 0) -> bytes:
  inner = (
    Writer().u8((command[0] + 1) & 0xFF).u16(len(data) + 1).u8(result).raw_bytes(data).finish()
  )
  return protocol.build_ftdi_frame(inner)


@dataclasses.dataclass(frozen=True)
class _ScriptStep:
  command: bytes
  response_data: bytes = b""
  result: int = 0


class _ScriptedFTDI:
  """Validate writes and replay partial FTDI reads from a fixed script."""

  def __init__(self, steps: list[_ScriptStep], max_read_size: int = 3):
    self._steps = deque(steps)
    self._response = bytearray()
    self._max_read_size = max_read_size
    self.setup_called = False
    self.stopped = False
    self.baudrate: int | None = None
    self.writes: list[bytes] = []

  async def setup(self) -> None:
    self.setup_called = True

  async def stop(self) -> None:
    self.stopped = True

  async def set_baudrate(self, baudrate: int) -> None:
    self.baudrate = baudrate

  async def write(self, data: bytes) -> int:
    if self._response:
      raise AssertionError(f"Access2 wrote before consuming response {self._response.hex()}")
    if not self._steps:
      raise AssertionError(f"Unexpected Access2 write: {data.hex()}")
    step = self._steps.popleft()
    expected = protocol.build_ftdi_frame(step.command)
    if data != expected:
      raise AssertionError(f"Access2 wrote {data.hex()}, expected {expected.hex()}")
    self.writes.append(data)
    self._response.extend(_build_ftdi_reply(step.command, step.response_data, step.result))
    return len(data)

  async def read(self, length: int) -> bytes:
    count = min(length, self._max_read_size, len(self._response))
    if count == 0:
      return b""
    chunk = bytes(self._response[:count])
    del self._response[:count]
    return chunk

  def assert_complete(self, test: unittest.TestCase) -> None:
    test.assertEqual(list(self._steps), [])
    test.assertEqual(bytes(self._response), b"")


class Access2TransportTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    ftdi_class = self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)
    self.io = ftdi_class.return_value
    self.driver = Access2Driver(device_id="test", timeout=0)

  async def test_status_response_supports_partial_reads(self):
    inner_response = (
      Writer().u8(protocol.GET_STATUS + 1).u16(5).u8(0).raw_bytes(_short_status_data()).finish()
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


class Access2ScriptedFTDITests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)

  def _make_driver(
    self, steps: list[_ScriptStep], *, timeout: int = 60
  ) -> tuple[Access2Driver, _ScriptedFTDI]:
    driver = Access2Driver(device_id="test", timeout=timeout)
    io = _ScriptedFTDI(steps)
    driver.io = io  # type: ignore[assignment]
    return driver, io

  async def test_complete_setup_ftdi_transcript(self):
    steps = [
      _ScriptStep(protocol.build_get_status(), _short_status_data(flags=0)),
      _ScriptStep(protocol.build_ping()),
      _ScriptStep(protocol.build_initialize()),
    ]
    steps.extend(
      _ScriptStep(protocol.build_read_flash(address, length), bytes(length))
      for address, length in ((0, 128), (128, 128), (256, 128), (384, 128), (512, 64))
    )
    steps.extend(
      [
        _ScriptStep(protocol.build_home()),
        _ScriptStep(protocol.build_get_status(), _full_status_data()),
        _ScriptStep(
          protocol.build_move_axis_to_position(
            protocol.AXIS_GRIPPER,
            0,
            protocol.PROFILE_DYNAMIC_EMPTY,
            protocol.SPEED_FAST,
          )
        ),
        _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=0)),
        _ScriptStep(
          protocol.build_move_to_teachpoint(
            protocol.TEACHPOINT_PARK,
            0,
            15,
            protocol.PROFILE_DYNAMIC_EMPTY,
            protocol.SPEED_FAST,
          )
        ),
        _ScriptStep(protocol.build_get_status(), _full_status_data()),
        _ScriptStep(protocol.build_get_status(), _short_status_data()),
      ]
    )
    driver, io = self._make_driver(steps)

    await driver.setup()

    io.assert_complete(self)
    self.assertTrue(io.setup_called)
    self.assertEqual(io.baudrate, 115384)

  async def test_complete_home_ftdi_transcript(self):
    steps = [
      _ScriptStep(protocol.build_home()),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
    ]
    driver, io = self._make_driver(steps)

    await driver.home()

    io.assert_complete(self)

  async def test_home_timeout_reports_last_status(self):
    steps = [
      _ScriptStep(protocol.build_home()),
      _ScriptStep(
        protocol.build_get_status(),
        _short_status_data(flags=protocol.STATUS_INITIALIZED),
      ),
    ]
    driver, io = self._make_driver(steps, timeout=0)

    with self.assertRaisesRegex(TimeoutError, "last status was 0x01"):
      await driver.home()

    io.assert_complete(self)

  async def test_complete_park_ftdi_transcript(self):
    steps = [
      _ScriptStep(protocol.build_get_status(), _short_status_data()),
      _ScriptStep(
        protocol.build_move_to_teachpoint(
          protocol.TEACHPOINT_PARK,
          8,
          15,
          protocol.PROFILE_DYNAMIC_FULL,
          protocol.SPEED_SLOW,
        )
      ),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(protocol.build_get_status(), _short_status_data()),
    ]
    driver, io = self._make_driver(steps)

    await driver.park()

    io.assert_complete(self)

  async def test_complete_load_ftdi_transcript(self):
    steps = [
      _ScriptStep(protocol.build_get_status(), _short_status_data()),
      _ScriptStep(
        protocol.build_move_axis_to_position(
          protocol.AXIS_GRIPPER,
          0,
          protocol.PROFILE_DYNAMIC_EMPTY,
          protocol.SPEED_FAST,
        )
      ),
      _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=0)),
      _ScriptStep(protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(protocol.build_get_sensor_values(), Writer().u32(0).finish()),
      _ScriptStep(protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 5.68)),
      _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=5.68)),
      _ScriptStep(
        protocol.build_move_to_teachpoint(
          protocol.TEACHPOINT_BUCKET_1,
          3,
          10,
          protocol.PROFILE_DYNAMIC_FULL,
        )
      ),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 0)),
      _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=0)),
      _ScriptStep(protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PARK, 3, 10)),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(protocol.build_get_status(), _short_status_data()),
    ]
    driver, io = self._make_driver(steps)

    await driver.load()

    io.assert_complete(self)

  async def test_complete_unload_ftdi_transcript(self):
    steps = [
      _ScriptStep(protocol.build_get_status(), _short_status_data()),
      _ScriptStep(
        protocol.build_move_axis_to_position(
          protocol.AXIS_GRIPPER,
          0,
          protocol.PROFILE_DYNAMIC_EMPTY,
          protocol.SPEED_FAST,
        )
      ),
      _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=0)),
      _ScriptStep(protocol.build_move_to_teachpoint(protocol.TEACHPOINT_BUCKET_1, 3, 10)),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(protocol.build_get_sensor_values(), Writer().u32(0).finish()),
      _ScriptStep(protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 5.69)),
      _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=5.69)),
      _ScriptStep(
        protocol.build_move_to_teachpoint(
          protocol.TEACHPOINT_PICK,
          3,
          10,
          protocol.PROFILE_DYNAMIC_FULL,
        )
      ),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 0)),
      _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=0)),
      _ScriptStep(protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PARK, 0, 10)),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(protocol.build_get_status(), _short_status_data()),
    ]
    driver, io = self._make_driver(steps)

    await driver.unload()

    io.assert_complete(self)

  async def test_load_stops_after_captured_no_plate_response(self):
    steps = [
      _ScriptStep(protocol.build_get_status(), _short_status_data()),
      _ScriptStep(
        protocol.build_move_axis_to_position(
          protocol.AXIS_GRIPPER,
          0,
          protocol.PROFILE_DYNAMIC_EMPTY,
          protocol.SPEED_FAST,
        )
      ),
      _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=0)),
      _ScriptStep(protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(
        protocol.build_get_sensor_values(), Writer().u32(protocol.SENSOR_NO_PLATE).finish()
      ),
    ]
    driver, io = self._make_driver(steps)

    with self.assertRaisesRegex(RuntimeError, "no plate found on stage"):
      await driver.load()

    io.assert_complete(self)

  async def test_motion_polls_until_axis_is_done_and_at_target(self):
    command = protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 5.68)
    steps = [
      _ScriptStep(command),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(gripper_status=0, gripper_position=1),
      ),
      _ScriptStep(protocol.build_get_status(), _full_status_data(gripper_position=5.68)),
    ]
    driver, io = self._make_driver(steps)

    with patch("pylabrobot.agilent.vspin.access2.asyncio.sleep", new=AsyncMock()):
      await driver._move_axis_to_position(protocol.AXIS_GRIPPER, 5.68)

    io.assert_complete(self)

  async def test_motion_timeout_reports_last_axis_state(self):
    command = protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 5.68)
    steps = [
      _ScriptStep(command),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(gripper_status=0, gripper_position=1),
      ),
    ]
    driver, io = self._make_driver(steps, timeout=0)

    with self.assertRaisesRegex(TimeoutError, "gripper=0x00, gripper=1.000 mm"):
      await driver._move_axis_to_position(protocol.AXIS_GRIPPER, 5.68)

    io.assert_complete(self)

  async def test_estop_during_motion_prevents_follow_up_commands(self):
    command = protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)
    steps = [
      _ScriptStep(command),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(flags=_READY_FLAGS | protocol.STATUS_ESTOP_ACTIVE),
      ),
    ]
    driver, io = self._make_driver(steps)

    with self.assertRaisesRegex(RuntimeError, "emergency stop"):
      await driver._move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)

    io.assert_complete(self)

  async def test_motor_fault_during_motion_names_failed_transition(self):
    command = protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)
    steps = [
      _ScriptStep(command),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(flags=_READY_FLAGS | protocol.STATUS_MOTOR_POWER_FAULT),
      ),
    ]
    driver, io = self._make_driver(steps)

    with self.assertRaisesRegex(RuntimeError, "during move to teachpoint 1"):
      await driver._move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)

    io.assert_complete(self)

  async def test_axis_fault_prevents_follow_up_commands(self):
    command = protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)
    steps = [
      _ScriptStep(command),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(y_status=protocol.AXIS_STATUS_POSITION_ERROR),
      ),
    ]
    driver, io = self._make_driver(steps)

    with self.assertRaisesRegex(RuntimeError, "failed on Y axis with status 0x10"):
      await driver._move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)

    io.assert_complete(self)

  async def test_motion_requires_full_axis_status(self):
    command = protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)
    steps = [
      _ScriptStep(command),
      _ScriptStep(protocol.build_get_status(), _short_status_data()),
    ]
    driver, io = self._make_driver(steps)

    with self.assertRaisesRegex(RuntimeError, "full axis status was not returned"):
      await driver._move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)

    io.assert_complete(self)

  async def test_version_queries_use_ftdi_protocol(self):
    steps = [
      _ScriptStep(protocol.build_get_firmware_version(), b"1.2.3\x00"),
      _ScriptStep(protocol.build_get_hardware_version(), Writer().i16(7).finish()),
    ]
    driver, io = self._make_driver(steps)

    self.assertEqual(await driver.request_firmware_version(), "1.2.3")
    self.assertEqual(await driver.request_hardware_version(), 7)

    io.assert_complete(self)


class Access2WorkflowTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)
    self.driver = Access2Driver(device_id="test")
    self.driver._move_axis_to_position = AsyncMock()  # type: ignore[method-assign]
    self.driver._move_to_teachpoint = AsyncMock()  # type: ignore[method-assign]

  async def test_gripper_state_methods_use_absolute_positions(self):
    ready = _status(flags=_READY_FLAGS)
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      side_effect=[ready, ready, ready, ready]
    )

    await self.driver.close_gripper()
    await self.driver.open_gripper()

    self.driver._move_axis_to_position.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(protocol.AXIS_GRIPPER, 5.68),
        call(protocol.AXIS_GRIPPER, 0.0),
      ]
    )

  async def test_close_gripper_is_idempotent_at_closed_position(self):
    closed = protocol.Access2Status(
      access2_status=_READY_FLAGS,
      vspin_status=0,
      gripper_status=protocol.AXIS_STATUS_MOVE_DONE,
      gripper_position=5.68,
    )
    self.driver.request_status = AsyncMock(return_value=closed)  # type: ignore[method-assign]

    await self.driver.close_gripper()

    self.driver._move_axis_to_position.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_load_uses_named_motion_sequence(self):
    ready = _status(flags=_READY_FLAGS)
    self.driver.request_status = AsyncMock(side_effect=[ready, ready])  # type: ignore[method-assign]
    self.driver.request_sensor_values = AsyncMock(return_value=0)  # type: ignore[method-assign]

    await self.driver.load()

    self.driver._move_axis_to_position.assert_has_awaits(  # type: ignore[attr-defined]
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
    self.driver._move_to_teachpoint.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(protocol.TEACHPOINT_PICK, 3, 10),
        call(
          protocol.TEACHPOINT_BUCKET_1,
          3,
          10,
          profile=protocol.PROFILE_DYNAMIC_FULL,
        ),
        call(protocol.TEACHPOINT_PARK, 3, 10),
      ]
    )

  async def test_load_stops_before_gripping_when_plate_is_absent(self):
    ready = _status(flags=_READY_FLAGS)
    self.driver.request_status = AsyncMock(return_value=ready)  # type: ignore[method-assign]
    self.driver.request_sensor_values = AsyncMock(  # type: ignore[method-assign]
      return_value=protocol.SENSOR_NO_PLATE
    )

    with self.assertRaisesRegex(RuntimeError, "no plate found on stage"):
      await self.driver.load()

    self.driver._move_axis_to_position.assert_awaited_once()  # type: ignore[attr-defined]
    self.driver._move_to_teachpoint.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.TEACHPOINT_PICK, 3, 10
    )

  async def test_estop_prevents_load_motion(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS | protocol.STATUS_ESTOP_ACTIVE)
    )

    with self.assertRaisesRegex(RuntimeError, "emergency stop"):
      await self.driver.load()

    self.driver._move_axis_to_position.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_to_teachpoint.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_motor_fault_prevents_load_motion(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS | protocol.STATUS_MOTOR_POWER_FAULT)
    )

    with self.assertRaisesRegex(RuntimeError, "motor power fault"):
      await self.driver.load()

    self.driver._move_axis_to_position.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_to_teachpoint.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_homed_status_does_not_hide_estop(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=protocol.STATUS_HOMED | protocol.STATUS_ESTOP_ACTIVE)
    )

    with self.assertRaisesRegex(RuntimeError, "emergency stop"):
      await self.driver._wait_until_homed()
