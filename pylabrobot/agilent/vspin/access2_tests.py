from __future__ import annotations

import dataclasses
import unittest
from collections import deque
from unittest.mock import AsyncMock, call, patch

from pylabrobot.agilent.vspin import _access2_protocol as protocol
from pylabrobot.agilent.vspin._state import (
  Access2Activity,
  ConnectionState,
  TransferDirection,
  TransferPhase,
  TransferProgress,
)
from pylabrobot.agilent.vspin.access2 import Access2Driver
from pylabrobot.io.binary import Writer

_READY_FLAGS = protocol.STATUS_INITIALIZED | protocol.STATUS_HOMED


def _mark_driver_ready(driver: Access2Driver) -> None:
  """Put a mock-backed driver at the verified lifecycle boundary under test."""
  driver._state = dataclasses.replace(
    driver.state,
    connection=ConnectionState.CONNECTED,
    last_teachpoint=protocol.TEACHPOINT_PARK,
  )


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

  async def test_connected_transport_does_not_imply_controller_readiness(self):
    self.driver._state = dataclasses.replace(
      self.driver.state,
      connection=ConnectionState.CONNECTED,
    )
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=0)
    )

    with self.assertRaisesRegex(RuntimeError, "not initialized and homed"):
      await self.driver._require_ready()

  async def test_ftdi_error_invalidates_connection_state(self):
    _mark_driver_ready(self.driver)
    self.driver.io.write = AsyncMock(side_effect=RuntimeError("transport lost"))  # type: ignore[method-assign]

    with (
      patch("pylabrobot.agilent.vspin.access2.is_ftdi_transport_error", return_value=True),
      self.assertRaisesRegex(RuntimeError, "transport lost"),
    ):
      await self.driver.open_gripper()

    self.assertEqual(self.driver.state.connection, ConnectionState.DISCONNECTED)
    self.assertEqual(self.driver.state.operation, Access2Activity.IDLE)
    self.assertIsNone(self.driver.state.last_teachpoint)


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
    _mark_driver_ready(driver)
    return driver, io

  async def test_complete_setup_ftdi_transcript(self):
    steps = [
      _ScriptStep(protocol.build_get_status(), _short_status_data(flags=0)),
      _ScriptStep(protocol.build_ping()),
      _ScriptStep(protocol.build_initialize()),
    ]
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
    self.assertEqual(driver.state.connection, ConnectionState.CONNECTED)
    self.assertEqual(driver.state.operation, Access2Activity.IDLE)
    self.assertEqual(driver.state.last_teachpoint, protocol.TEACHPOINT_PARK)

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
      _ScriptStep(
        protocol.build_get_sensor_values(),
        Writer().u32(0x03 | protocol.STATUS_OPTICAL_PLATE_SENSOR).finish(),
      ),
      _ScriptStep(
        protocol.build_move_axis_to_position(
          protocol.AXIS_GRIPPER,
          5.68,
          protocol.PROFILE_DYNAMIC_EMPTY,
        )
      ),
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
      _ScriptStep(
        protocol.build_move_axis_to_position(
          protocol.AXIS_GRIPPER,
          0,
          protocol.PROFILE_DYNAMIC_EMPTY,
        )
      ),
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
      _ScriptStep(
        protocol.build_get_sensor_values(),
        Writer().u32(0x03 | protocol.STATUS_OPTICAL_PLATE_SENSOR).finish(),
      ),
      _ScriptStep(
        protocol.build_move_axis_to_position(
          protocol.AXIS_GRIPPER,
          5.68,
          protocol.PROFILE_DYNAMIC_EMPTY,
        ),
        result=0x51,
      ),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(
          flags=_READY_FLAGS | protocol.STATUS_OPTICAL_PLATE_SENSOR,
          gripper_position=1.94,
        ),
      ),
      _ScriptStep(
        protocol.build_move_to_teachpoint(
          protocol.TEACHPOINT_PICK,
          3,
          10,
          protocol.PROFILE_DYNAMIC_FULL,
        )
      ),
      _ScriptStep(protocol.build_get_status(), _full_status_data()),
      _ScriptStep(
        protocol.build_move_axis_to_position(
          protocol.AXIS_GRIPPER,
          0,
          protocol.PROFILE_DYNAMIC_EMPTY,
        )
      ),
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

  async def test_nonzero_result_stops_an_exact_axis_move(self):
    command = protocol.build_move_axis_to_position(protocol.AXIS_Y, 100)
    driver, io = self._make_driver([_ScriptStep(command, result=7)])

    with self.assertRaisesRegex(protocol.Access2ProtocolError, "result 0x07"):
      await driver._move_axis_to_position(protocol.AXIS_Y, 100)

    io.assert_complete(self)

  async def test_gripper_close_requires_the_configured_threshold(self):
    command = protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 5.68)
    steps = [
      _ScriptStep(command, result=0x51),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(
          flags=_READY_FLAGS | protocol.STATUS_OPTICAL_PLATE_SENSOR,
          gripper_position=1.4,
        ),
      ),
    ]
    driver, io = self._make_driver(steps)

    with self.assertRaisesRegex(
      protocol.Access2ProtocolError,
      "position 1.400, threshold 1.500, axis status 0x01, "
      "optical plate sensor True, command result 0x51",
    ):
      await driver._close_gripper()

    io.assert_complete(self)

  async def test_gripper_contact_uses_each_calls_target_and_threshold(self):
    for threshold, accepted in ((1.8, True), (2.0, False)):
      with self.subTest(threshold=threshold):
        driver, io = self._make_driver(
          [
            _ScriptStep(
              protocol.build_move_axis_to_position(
                protocol.AXIS_GRIPPER, 4.75, speed=protocol.SPEED_FAST
              ),
              result=0x51,
            ),
            _ScriptStep(
              protocol.build_get_status(),
              _full_status_data(
                flags=_READY_FLAGS | protocol.STATUS_OPTICAL_PLATE_SENSOR,
                gripper_position=1.94,
              ),
            ),
          ]
        )
        if accepted:
          await driver._close_gripper(
            gripper_closed_position=4.75,
            gripper_close_threshold=threshold,
            speed=protocol.SPEED_FAST,
          )
        else:
          with self.assertRaisesRegex(protocol.Access2ProtocolError, "threshold 2.000"):
            await driver._close_gripper(
              gripper_closed_position=4.75,
              gripper_close_threshold=threshold,
              speed=protocol.SPEED_FAST,
            )
        io.assert_complete(self)

  async def test_gripper_close_rejects_a_non_contact_error(self):
    command = protocol.build_move_axis_to_position(protocol.AXIS_GRIPPER, 5.68)
    steps = [
      _ScriptStep(command, result=0x07),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(gripper_position=5.68),
      ),
    ]
    driver, io = self._make_driver(steps)

    with self.assertRaisesRegex(protocol.Access2ProtocolError, "command result 0x07"):
      await driver._close_gripper()

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

  async def test_unverified_axis_status_bits_are_not_treated_as_faults(self):
    command = protocol.build_move_to_teachpoint(protocol.TEACHPOINT_PICK, 3, 10)
    steps = [
      _ScriptStep(command),
      _ScriptStep(
        protocol.build_get_status(),
        _full_status_data(y_status=0x13),
      ),
    ]
    driver, io = self._make_driver(steps)

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
    _mark_driver_ready(self.driver)
    self.driver._move_axis_to_position = AsyncMock()  # type: ignore[method-assign]
    self.driver._move_to_teachpoint = AsyncMock()  # type: ignore[method-assign]
    self.driver._close_gripper = AsyncMock()  # type: ignore[method-assign]

  async def test_park_parameters_are_per_call(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS)
    )
    await self.driver.park(plate_height=22, z_offset=4, speed="medium")
    await self.driver.park()
    self.driver._move_to_teachpoint.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(
          protocol.TEACHPOINT_PARK,
          4,
          22,
          profile=protocol.PROFILE_DYNAMIC_FULL,
          speed=protocol.SPEED_MEDIUM,
        ),
        call(
          protocol.TEACHPOINT_PARK,
          8,
          15,
          profile=protocol.PROFILE_DYNAMIC_FULL,
          speed=protocol.SPEED_SLOW,
        ),
      ]
    )
    self.assertEqual(self.driver.state.operation, Access2Activity.IDLE)
    self.assertEqual(self.driver.state.last_teachpoint, protocol.TEACHPOINT_PARK)

  async def test_invalid_motion_settings_fail_before_io(self):
    self.driver.request_status = AsyncMock()  # type: ignore[method-assign]
    before = self.driver.state
    for transfer in (self.driver.load, self.driver.unload):
      for parameter in (
        "source_speed",
        "destination_speed",
        "park_speed",
        "gripper_open_speed",
        "gripper_close_speed",
        "gripper_release_speed",
      ):
        with self.subTest(operation=transfer.__name__, parameter=parameter):
          with self.assertRaisesRegex(ValueError, "Access2 speed"):
            await transfer(**{parameter: "invalid"})  # type: ignore[arg-type]
          self.assertEqual(self.driver.state, before)
    for operation in (self.driver.park, self.driver.open_gripper, self.driver.close_gripper):
      with self.subTest(operation=operation.__name__):
        with self.assertRaisesRegex(ValueError, "Access2 speed"):
          await operation(speed="invalid")  # type: ignore[arg-type]
        self.assertEqual(self.driver.state, before)
    for parameters in (
      {"plate_height": 0},
      {"plate_height": float("nan")},
      {"z_offset": float("inf")},
    ):
      with self.subTest(parameters=parameters):
        with self.assertRaises(ValueError):
          await self.driver.park(**parameters)
        self.assertEqual(self.driver.state, before)
    self.driver.request_status.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_axis_to_position.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_to_teachpoint.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_standalone_gripper_speeds_are_per_call(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS)
    )
    await self.driver.open_gripper(speed="medium")
    await self.driver.open_gripper()
    self.driver._move_axis_to_position.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(
          protocol.AXIS_GRIPPER,
          0,
          profile=protocol.PROFILE_DYNAMIC_EMPTY,
          speed=protocol.SPEED_MEDIUM,
        ),
        call(
          protocol.AXIS_GRIPPER,
          0,
          profile=protocol.PROFILE_DYNAMIC_EMPTY,
          speed=protocol.SPEED_SLOW,
        ),
      ]
    )
    await self.driver.close_gripper(speed="fast")
    await self.driver.close_gripper()
    self.driver._close_gripper.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(gripper_closed_position=5.68, gripper_close_threshold=1.5, speed=protocol.SPEED_FAST),
        call(gripper_closed_position=5.68, gripper_close_threshold=1.5, speed=protocol.SPEED_SLOW),
      ]
    )

  async def test_transfer_speeds_are_per_call_in_both_directions(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS)
    )
    self.driver.request_sensor_values = AsyncMock(  # type: ignore[method-assign]
      return_value=protocol.STATUS_OPTICAL_PLATE_SENSOR
    )
    for transfer in (self.driver.load, self.driver.unload):
      with self.subTest(direction=transfer.__name__):
        await transfer(
          source_speed="medium",
          destination_speed="fast",
          park_speed="medium",
          gripper_open_speed="slow",
          gripper_close_speed="fast",
          gripper_release_speed="medium",
        )
        source, destination = (
          (protocol.TEACHPOINT_PICK, protocol.TEACHPOINT_BUCKET_1)
          if transfer == self.driver.load
          else (protocol.TEACHPOINT_BUCKET_1, protocol.TEACHPOINT_PICK)
        )
        self.driver._move_to_teachpoint.assert_has_awaits(  # type: ignore[attr-defined]
          [
            call(source, 3, 10, speed=protocol.SPEED_MEDIUM),
            call(
              destination, 3, 10, profile=protocol.PROFILE_DYNAMIC_FULL, speed=protocol.SPEED_FAST
            ),
            call(
              protocol.TEACHPOINT_PARK,
              3 if transfer == self.driver.load else 0,
              10,
              speed=protocol.SPEED_MEDIUM,
            ),
          ]
        )
        self.driver._move_axis_to_position.assert_has_awaits(  # type: ignore[attr-defined]
          [
            call(
              protocol.AXIS_GRIPPER,
              0,
              profile=protocol.PROFILE_DYNAMIC_EMPTY,
              speed=protocol.SPEED_SLOW,
            ),
            call(
              protocol.AXIS_GRIPPER,
              0,
              profile=protocol.PROFILE_DYNAMIC_EMPTY,
              speed=protocol.SPEED_MEDIUM,
            ),
          ]
        )
        self.driver._close_gripper.assert_awaited_with(  # type: ignore[attr-defined]
          gripper_closed_position=5.68, gripper_close_threshold=1.5, speed=protocol.SPEED_FAST
        )
        await transfer()
        self.driver._close_gripper.assert_awaited_with(  # type: ignore[attr-defined]
          gripper_closed_position=5.68, gripper_close_threshold=1.5, speed=protocol.SPEED_SLOW
        )

  async def test_transfer_parameters_are_per_call_in_both_directions(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS)
    )
    self.driver.request_sensor_values = AsyncMock(  # type: ignore[method-assign]
      return_value=protocol.STATUS_OPTICAL_PLATE_SENSOR
    )
    for transfer in (self.driver.load, self.driver.unload):
      with self.subTest(direction=transfer.__name__):
        await transfer(
          protocol.TEACHPOINT_BUCKET_2,
          plate_height=22,
          source_z_offset=4,
          destination_z_offset=2,
          park_z_offset=1,
          gripper_open_position=0.25,
          gripper_closed_position=4.75,
          gripper_close_threshold=1.8,
        )
        source, destination = (
          (protocol.TEACHPOINT_PICK, protocol.TEACHPOINT_BUCKET_2)
          if transfer == self.driver.load
          else (protocol.TEACHPOINT_BUCKET_2, protocol.TEACHPOINT_PICK)
        )
        self.driver._move_to_teachpoint.assert_has_awaits(  # type: ignore[attr-defined]
          [
            call(source, 4, 22, speed=protocol.SPEED_SLOW),
            call(
              destination, 2, 22, profile=protocol.PROFILE_DYNAMIC_FULL, speed=protocol.SPEED_SLOW
            ),
            call(protocol.TEACHPOINT_PARK, 1, 22, speed=protocol.SPEED_SLOW),
          ]
        )
        self.driver._close_gripper.assert_awaited_with(  # type: ignore[attr-defined]
          gripper_closed_position=4.75, gripper_close_threshold=1.8, speed=protocol.SPEED_SLOW
        )
        self.driver._move_axis_to_position.assert_awaited_with(  # type: ignore[attr-defined]
          protocol.AXIS_GRIPPER,
          0.25,
          profile=protocol.PROFILE_DYNAMIC_EMPTY,
          speed=protocol.SPEED_SLOW,
        )
        self.assertEqual(self.driver.state.operation, Access2Activity.IDLE)
        await transfer()
        self.driver._close_gripper.assert_awaited_with(  # type: ignore[attr-defined]
          gripper_closed_position=5.68, gripper_close_threshold=1.5, speed=protocol.SPEED_SLOW
        )
        self.driver._move_axis_to_position.assert_awaited_with(  # type: ignore[attr-defined]
          protocol.AXIS_GRIPPER,
          0,
          profile=protocol.PROFILE_DYNAMIC_EMPTY,
          speed=protocol.SPEED_SLOW,
        )
        self.driver._move_to_teachpoint.assert_awaited_with(  # type: ignore[attr-defined]
          protocol.TEACHPOINT_PARK,
          3 if transfer == self.driver.load else 0,
          10,
          speed=protocol.SPEED_SLOW,
        )

  async def test_invalid_transfer_parameters_fail_before_io(self):
    self.driver.request_status = AsyncMock()  # type: ignore[method-assign]
    before = self.driver.state
    for transfer in (self.driver.load, self.driver.unload):
      for parameters in (
        {"gripper_close_threshold": 0},
        {"gripper_closed_position": 1},
        {"gripper_open_position": float("nan")},
        {"gripper_closed_position": float("inf")},
        {"plate_height": 0},
        {"plate_height": -1},
        {"plate_height": float("nan")},
        {"source_z_offset": float("inf")},
        {"destination_z_offset": float("nan")},
        {"park_z_offset": float("inf")},
      ):
        with self.subTest(direction=transfer.__name__, parameters=parameters):
          with self.assertRaises(ValueError):
            await transfer(**parameters)
          self.assertEqual(self.driver.state, before)
    self.driver.request_status.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_axis_to_position.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_to_teachpoint.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_gripper_state_methods_use_absolute_positions(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS)
    )

    await self.driver.close_gripper()
    await self.driver.open_gripper()

    self.driver._close_gripper.assert_awaited_once_with(  # type: ignore[attr-defined]
      gripper_closed_position=5.68, gripper_close_threshold=1.5, speed=protocol.SPEED_SLOW
    )
    self.driver._move_axis_to_position.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.AXIS_GRIPPER,
      0.0,
      profile=protocol.PROFILE_DYNAMIC_EMPTY,
      speed=protocol.SPEED_SLOW,
    )

  async def test_failure_before_actuation_restores_operation(self):
    before = self.driver.state

    with self.assertRaisesRegex(RuntimeError, "precondition failed"):
      async with self.driver._operation_scope(Access2Activity.MOVING):
        raise RuntimeError("precondition failed")

    self.assertEqual(self.driver.state, before)

  async def test_actuated_failure_retains_transfer_phase_and_requires_recovery(self):
    progress = TransferProgress(
      direction=TransferDirection.INTO_CENTRIFUGE,
      bucket_teachpoint=protocol.TEACHPOINT_BUCKET_1,
    )

    with self.assertRaisesRegex(RuntimeError, "motion failed"):
      async with self.driver._operation_scope(progress) as transition:
        self.driver._set_transfer_phase(TransferPhase.MOVING_TO_DESTINATION)
        transition.mark_actuated(position_uncertain=True)
        raise RuntimeError("motion failed")

    self.assertTrue(self.driver.state.recovery_required)
    self.assertIsNone(self.driver.state.last_teachpoint)
    self.assertIsInstance(self.driver.state.operation, TransferProgress)
    assert isinstance(self.driver.state.operation, TransferProgress)
    self.assertEqual(
      self.driver.state.operation.phase,
      TransferPhase.MOVING_TO_DESTINATION,
    )

  async def test_stop_invalidates_connection_and_teachpoint(self):
    self.driver.io.stop = AsyncMock()  # type: ignore[method-assign]

    await self.driver.stop()

    self.assertEqual(self.driver.state.connection, ConnectionState.DISCONNECTED)
    self.assertIsNone(self.driver.state.last_teachpoint)

  async def test_setup_opens_gripper_with_default_position_and_profile(self):
    driver = Access2Driver(device_id="test")
    driver.io.setup = AsyncMock()  # type: ignore[method-assign]
    driver.io.set_baudrate = AsyncMock()  # type: ignore[method-assign]
    driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS)
    )
    driver.send_command = AsyncMock()  # type: ignore[method-assign]
    driver._home = AsyncMock()  # type: ignore[method-assign]
    driver._move_axis_to_position = AsyncMock()  # type: ignore[method-assign]
    driver._move_to_teachpoint = AsyncMock()  # type: ignore[method-assign]

    await driver.setup()

    driver._move_axis_to_position.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.AXIS_GRIPPER,
      0.0,
      profile=protocol.PROFILE_DYNAMIC_EMPTY,
      speed=protocol.SPEED_FAST,
    )

  async def test_close_gripper_is_idempotent_at_closed_position(self):
    closed = protocol.Access2Status(
      access2_status=_READY_FLAGS,
      vspin_status=0,
      gripper_status=0x03,
      gripper_position=5.671,
    )
    self.driver.request_status = AsyncMock(return_value=closed)  # type: ignore[method-assign]

    await self.driver.close_gripper()

    self.driver._close_gripper.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_close_gripper_is_idempotent_at_contact_position(self):
    closed = protocol.Access2Status(
      access2_status=_READY_FLAGS | protocol.STATUS_OPTICAL_PLATE_SENSOR,
      vspin_status=0,
      gripper_status=protocol.AXIS_STATUS_MOVE_DONE,
      gripper_position=1.94,
    )
    self.driver.request_status = AsyncMock(return_value=closed)  # type: ignore[method-assign]

    await self.driver.close_gripper()

    self.driver._close_gripper.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_load_uses_named_motion_sequence(self):
    ready = _status(flags=_READY_FLAGS)
    self.driver.request_status = AsyncMock(side_effect=[ready, ready])  # type: ignore[method-assign]
    self.driver.request_sensor_values = AsyncMock(  # type: ignore[method-assign]
      return_value=0x03 | protocol.STATUS_OPTICAL_PLATE_SENSOR
    )

    await self.driver.load()

    self.driver._move_axis_to_position.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(
          protocol.AXIS_GRIPPER,
          0,
          profile=protocol.PROFILE_DYNAMIC_EMPTY,
          speed=protocol.SPEED_FAST,
        ),
        call(
          protocol.AXIS_GRIPPER,
          0,
          profile=protocol.PROFILE_DYNAMIC_EMPTY,
          speed=protocol.SPEED_SLOW,
        ),
      ]
    )
    self.driver._close_gripper.assert_awaited_once_with(  # type: ignore[attr-defined]
      gripper_closed_position=5.68, gripper_close_threshold=1.5, speed=protocol.SPEED_SLOW
    )
    self.driver._move_to_teachpoint.assert_has_awaits(  # type: ignore[attr-defined]
      [
        call(protocol.TEACHPOINT_PICK, 3, 10, speed=protocol.SPEED_SLOW),
        call(
          protocol.TEACHPOINT_BUCKET_1,
          3,
          10,
          profile=protocol.PROFILE_DYNAMIC_FULL,
          speed=protocol.SPEED_SLOW,
        ),
        call(protocol.TEACHPOINT_PARK, 3, 10, speed=protocol.SPEED_SLOW),
      ]
    )
    self.assertEqual(self.driver.state.operation, Access2Activity.IDLE)
    self.assertEqual(self.driver.state.last_teachpoint, protocol.TEACHPOINT_PARK)

  async def test_load_uses_selected_bucket_teachpoint(self):
    ready = _status(flags=_READY_FLAGS)
    self.driver.request_status = AsyncMock(side_effect=[ready, ready])  # type: ignore[method-assign]
    self.driver.request_sensor_values = AsyncMock(  # type: ignore[method-assign]
      return_value=protocol.STATUS_OPTICAL_PLATE_SENSOR
    )

    await self.driver.load(protocol.TEACHPOINT_BUCKET_2)

    self.driver._move_to_teachpoint.assert_any_await(  # type: ignore[attr-defined]
      protocol.TEACHPOINT_BUCKET_2,
      3,
      10,
      profile=protocol.PROFILE_DYNAMIC_FULL,
      speed=protocol.SPEED_SLOW,
    )

  async def test_load_reports_each_transfer_phase_at_its_actuation_boundary(self):
    ready = _status(flags=_READY_FLAGS)
    observed: list[TransferPhase] = []

    def record_phase() -> None:
      operation = self.driver.state.operation
      assert isinstance(operation, TransferProgress)
      observed.append(operation.phase)

    async def move_axis(*args: object, **kwargs: object) -> None:
      del args, kwargs
      record_phase()

    async def move_to_teachpoint(*args: object, **kwargs: object) -> None:
      del args, kwargs
      record_phase()

    async def sense_plate() -> int:
      record_phase()
      return protocol.STATUS_OPTICAL_PLATE_SENSOR

    async def close_gripper(**parameters: float) -> protocol.Access2Status:
      record_phase()
      return ready

    self.driver.request_status = AsyncMock(side_effect=[ready, ready])  # type: ignore[method-assign]
    self.driver.request_sensor_values = AsyncMock(side_effect=sense_plate)  # type: ignore[method-assign]
    self.driver._move_axis_to_position = AsyncMock(side_effect=move_axis)  # type: ignore[method-assign]
    self.driver._move_to_teachpoint = AsyncMock(side_effect=move_to_teachpoint)  # type: ignore[method-assign]
    self.driver._close_gripper = AsyncMock(side_effect=close_gripper)  # type: ignore[method-assign]

    await self.driver.load()

    self.assertEqual(
      observed,
      [
        TransferPhase.APPROACHING_SOURCE,
        TransferPhase.APPROACHING_SOURCE,
        TransferPhase.AT_SOURCE,
        TransferPhase.GRIPPING,
        TransferPhase.MOVING_TO_DESTINATION,
        TransferPhase.RELEASING,
        TransferPhase.RETURNING_TO_PARK,
      ],
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
    self.driver._close_gripper.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_to_teachpoint.assert_awaited_once_with(  # type: ignore[attr-defined]
      protocol.TEACHPOINT_PICK, 3, 10, speed=protocol.SPEED_SLOW
    )

  async def test_estop_prevents_load_motion(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS | protocol.STATUS_ESTOP_ACTIVE)
    )

    with self.assertRaisesRegex(RuntimeError, "emergency stop"):
      await self.driver.load()

    self.driver._move_axis_to_position.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._close_gripper.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_to_teachpoint.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_motor_fault_prevents_load_motion(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=_READY_FLAGS | protocol.STATUS_MOTOR_POWER_FAULT)
    )

    with self.assertRaisesRegex(RuntimeError, "motor power fault"):
      await self.driver.load()

    self.driver._move_axis_to_position.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._close_gripper.assert_not_awaited()  # type: ignore[attr-defined]
    self.driver._move_to_teachpoint.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_homed_status_does_not_hide_estop(self):
    self.driver.request_status = AsyncMock(  # type: ignore[method-assign]
      return_value=_status(flags=protocol.STATUS_HOMED | protocol.STATUS_ESTOP_ACTIVE)
    )

    with self.assertRaisesRegex(RuntimeError, "emergency stop"):
      await self.driver._wait_until_homed()
