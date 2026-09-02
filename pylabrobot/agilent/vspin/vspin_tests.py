import dataclasses
import unittest
from collections import deque
from unittest.mock import AsyncMock, call, patch

from pylabrobot.agilent.vspin import _nmc, vspin as vspin_module
from pylabrobot.agilent.vspin.access2 import Access2
from pylabrobot.agilent.vspin.errors import CentrifugeDoorError
from pylabrobot.agilent.vspin.vspin import VSpin
from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.io.binary import Writer
from pylabrobot.resources import Coordinate, Resource


_SERVO_STATUS_MASK = (
  _nmc.SEND_POSITION | _nmc.SEND_ANALOG | _nmc.SEND_VELOCITY | _nmc.SEND_AUXILIARY | _nmc.SEND_HOME
)
_IO_STATUS_MASK = _nmc.SEND_INPUTS | _nmc.SEND_ANALOG_1


def _nmc_response(status: int, data: bytes = b"") -> bytes:
  return bytes([status]) + data + bytes([(status + sum(data)) & 0xFF])


def _servo_status_data(
  *,
  position: int = 0,
  velocity: int = 0,
  home_position: int = 0,
) -> bytes:
  return Writer().i32(position).u8(0).i16(velocity).u8(0).i32(home_position).finish()


def _io_status_data(*, inputs: int = 0) -> bytes:
  return Writer().u16(inputs).u8(0).finish()


def _servo_step(
  command: bytes,
  *,
  status: int = _nmc.STATUS_MOVE_DONE,
  position: int = 0,
  velocity: int = 0,
  home_position: int = 0,
) -> "_VSpinScriptStep":
  return _VSpinScriptStep(
    command,
    _nmc_response(
      status,
      _servo_status_data(
        position=position,
        velocity=velocity,
        home_position=home_position,
      ),
    ),
  )


def _io_step(
  command: bytes,
  *,
  status: int = _nmc.STATUS_MOVE_DONE,
  inputs: int = 0,
) -> "_VSpinScriptStep":
  return _VSpinScriptStep(command, _nmc_response(status, _io_status_data(inputs=inputs)))


def _empty_step(
  command: bytes,
  *,
  status: int = _nmc.STATUS_MOVE_DONE,
) -> "_VSpinScriptStep":
  return _VSpinScriptStep(command, _nmc_response(status))


@dataclasses.dataclass(frozen=True)
class _VSpinScriptStep:
  command: bytes
  response: bytes | None


class _ScriptedVSpinFTDI:
  """Validate VSpin writes and replay partial NMC responses from a fixed script."""

  def __init__(self, steps: list[_VSpinScriptStep], max_read_size: int = 3):
    self._steps = deque(steps)
    self._response = bytearray()
    self._max_read_size = max_read_size
    self.setup_called = False
    self.setup_call_count = 0
    self.stopped = False
    self.stop_call_count = 0
    self.writes: list[bytes] = []
    self.latency_timers: list[int] = []
    self.line_properties: list[tuple[int, int, int]] = []
    self.flow_controls: list[int] = []
    self.baudrates: list[int] = []
    self.rts_levels: list[bool] = []
    self.dtr_levels: list[bool] = []
    self.rx_purge_count = 0

  async def setup(self) -> None:
    self.setup_called = True
    self.setup_call_count += 1
    self.stopped = False

  async def stop(self) -> None:
    self.stopped = True
    self.stop_call_count += 1
    # Reopening the real FTDI connection discards replies already buffered on the host side.
    self._response.clear()

  async def set_latency_timer(self, latency: int) -> None:
    self.latency_timers.append(latency)

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    self.line_properties.append((bits, stopbits, parity))

  async def set_flowctrl(self, flowctrl: int) -> None:
    self.flow_controls.append(flowctrl)

  async def set_baudrate(self, baudrate: int) -> None:
    self.baudrates.append(baudrate)

  async def set_rts(self, level: bool) -> None:
    self.rts_levels.append(level)

  async def set_dtr(self, level: bool) -> None:
    self.dtr_levels.append(level)

  async def usb_purge_rx_buffer(self) -> None:
    self.rx_purge_count += 1

  async def write(self, data: bytes) -> int:
    if self._response:
      raise AssertionError(f"VSpin wrote before consuming response {self._response.hex()}")
    if not self._steps:
      raise AssertionError(f"Unexpected VSpin write: {data.hex()}")
    step = self._steps.popleft()
    if data != step.command:
      raise AssertionError(f"VSpin wrote {data.hex()}, expected {step.command.hex()}")
    self.writes.append(data)
    if step.response is not None:
      self._response.extend(step.response)
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


class TestVSpinEvents(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.vspin_ftdi = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.vspin_ftdi.start()
    self.addCleanup(self.vspin_ftdi.stop)

  async def test_spin_emits_loaded_bucket_resources_and_parameters(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=100000
    )
    vspin.request_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[0, 10000000, 20000000]
    )
    vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0)
    )
    vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await vspin.spin(g=500, duration=1, acceleration=0.5, deceleration=0.6)

    self.assertEqual(
      [event.name for event in events],
      [
        "centrifuge.spin.started",
        "centrifuge.spin.completed",
      ],
    )
    started, completed = events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["device"]["name"], "centrifuge")
    self.assertEqual(started.data["resources"][0]["name"], "plate_1")
    self.assertEqual(started.data["bucket_resources"][0]["holder"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["relative_centrifugal_force"], 500)
    self.assertEqual(started.data["duration"], 1)
    self.assertEqual(started.data["acceleration_fraction"], 0.5)
    self.assertEqual(started.data["deceleration_fraction"], 0.6)
    self.assertNotIn("relative_centrifugal_force_g", started.data)
    self.assertNotIn("duration_seconds", started.data)

    rpm = VSpin.g_to_rpm(500)
    spin_target = _nmc.spin_target_distance(rpm, duration=1, acceleration=0.5)
    expected_spin_command = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      0x97,
      position=spin_target,
      velocity=_nmc.rpm_to_nmc_velocity(rpm),
      acceleration=_nmc.acceleration_to_nmc(0.5),
    )
    expected_deceleration_command = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      0xB6,
      velocity=0,
      acceleration=_nmc.acceleration_to_nmc(0.6),
    )
    commands = [call.args[0] for call in vspin._send_nmc.await_args_list]
    self.assertIn(expected_spin_command, commands)
    self.assertIn(expected_deceleration_command, commands)

  async def test_spin_failure_emits_requested_parameters(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaisesRegex(ValueError, "G-force"):
        await vspin.spin(g=0)

    self.assertEqual(
      [event.name for event in events],
      [
        "centrifuge.spin.started",
        "centrifuge.spin.failed",
      ],
    )
    self.assertEqual(events[0].context["operation_id"], events[1].context["operation_id"])
    self.assertEqual(events[1].data["error_type"], "ValueError")

  async def test_spin_accepts_positional_parameters_with_event_bus(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=100000
    )
    vspin.request_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[0, 10000000, 20000000]
    )
    vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0)
    )
    vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await vspin.spin(500, 1, 0.5, 0.6)

    started = events[0]
    self.assertEqual(started.data["relative_centrifugal_force"], 500)
    self.assertEqual(started.data["duration"], 1)
    self.assertEqual(started.data["acceleration_fraction"], 0.5)
    self.assertEqual(started.data["deceleration_fraction"], 0.6)


class TestVSpinProtocol(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    ftdi_class = self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)
    self.io = ftdi_class.return_value
    self.vspin = VSpin(name="centrifuge")

  async def test_position_status_uses_fixed_length_and_checksum(self):
    response = bytes.fromhex("11222500004f000018e0050000a4")
    self.io.write = AsyncMock(return_value=4)
    self.io.read = AsyncMock(side_effect=[response[:5], response[5:]])

    status = await self.vspin.request_positions_and_tachometer()

    self.assertEqual(status.status, 0x11)
    self.assertEqual(status.position, 0x2522)
    self.assertEqual(status.velocity, 0)
    self.assertEqual(status.home_position, 0x05E0)
    self.io.write.assert_awaited_once_with(_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS))
    self.assertEqual([call.args[0] for call in self.io.read.await_args_list], [14, 9])

  async def test_position_status_rejects_bad_checksum(self):
    response = bytearray.fromhex("11222500004f000018e0050000a4")
    response[-1] ^= 0xFF
    self.io.write = AsyncMock(return_value=4)
    self.io.read = AsyncMock(return_value=bytes(response))

    with self.assertRaisesRegex(
      _nmc.NMCProtocolError,
      rf"command {_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS).hex()}.*"
      rf"response {bytes(response).hex()}.*checksum mismatch",
    ):
      await self.vspin.request_positions_and_tachometer()

  async def test_send_nmc_timeout_includes_command_and_partial_response(self):
    command = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    self.io.write = AsyncMock(return_value=len(command))
    self.io.read = AsyncMock(side_effect=[b"\x01", b""])

    with self.assertRaisesRegex(
      TimeoutError,
      rf"command {command.hex()} timed out.*1 of 2 expected.*01",
    ):
      await self.vspin._send_nmc(command, timeout=0)

  async def test_exact_response_times_out_with_partial_bytes(self):
    self.io.read = AsyncMock(side_effect=[b"\x01", b""])

    with self.assertRaisesRegex(TimeoutError, "1 of 2 expected"):
      await self.vspin._read_exact_response(length=2, timeout=0)

  async def test_send_nmc_uses_active_status_mask_length(self):
    self.vspin._servo_status_mask = _nmc.SEND_POSITION | _nmc.SEND_VELOCITY
    response = bytes.fromhex("0101000000020004")
    self.vspin.send_command = AsyncMock(return_value=response)  # type: ignore[method-assign]

    parsed = await self.vspin._send_nmc(_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS))

    self.assertEqual(parsed, _nmc.NMCResponse(status=1, data=bytes.fromhex("010000000200")))
    self.vspin.send_command.assert_awaited_once_with(  # type: ignore[attr-defined]
      _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
      expected_response_length=8,
      read_timeout=0.2,
    )

  async def test_io_sensor_polarities_match_vspin_wiring(self):
    self.vspin._request_input_flags = AsyncMock(  # type: ignore[method-assign]
      return_value=(1 << _nmc.INPUT_DOOR_OPEN) | (1 << _nmc.INPUT_BUCKET_LOCKED)
    )

    self.assertTrue(await self.vspin.request_door_open())
    self.assertTrue(await self.vspin.request_door_locked())
    self.assertFalse(await self.vspin.request_bucket_locked())

  async def test_io_output_updates_preserve_other_output_bits(self):
    self.vspin._io_output_word = 1 << _nmc.OUTPUT_BUCKET_LOCK_CYLINDER
    self.vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )

    await self.vspin._set_io_output_bit(_nmc.OUTPUT_DOOR_LOCK_CYLINDER, True)

    expected_word = (1 << _nmc.OUTPUT_BUCKET_LOCK_CYLINDER) | (1 << _nmc.OUTPUT_DOOR_LOCK_CYLINDER)
    self.vspin._send_nmc.assert_awaited_once_with(  # type: ignore[attr-defined]
      _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, expected_word)
    )
    self.assertEqual(self.vspin._io_output_word, expected_word)

  async def test_position_wait_reports_last_position(self):
    self.vspin.request_position = AsyncMock(return_value=25)  # type: ignore[method-assign]

    with self.assertRaisesRegex(TimeoutError, "last position was 25"):
      await self.vspin._wait_for_position(100, timeout=0, operation="test motion")

  async def test_spin_faults_decode_ground_truth_io_bits(self):
    self.vspin._request_input_flags = AsyncMock(  # type: ignore[method-assign]
      return_value=1 << _nmc.INPUT_IMBALANCE
    )

    with self.assertRaisesRegex(RuntimeError, "imbalance"):
      await self.vspin._raise_for_spin_faults()

  async def test_spin_rejects_long_run_position_overflow_before_servo_motion(self):
    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=2**31 - 1)  # type: ignore[method-assign]
    self.vspin._send_nmc = AsyncMock()  # type: ignore[method-assign]

    with self.assertRaisesRegex(NotImplementedError, "signed 32-bit position"):
      await self.vspin.spin(g=500, duration=1)

    self.vspin._send_nmc.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_stop_spin_commands_deceleration_and_confirms_zero_speed(self):
    self.vspin._spin_active = True
    self.vspin.request_tachometer = AsyncMock(return_value=1000)  # type: ignore[method-assign]
    self.vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      side_effect=[
        _nmc.ServoStatus(status=0, velocity=0),
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=-1),
        _nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=0),
      ]
    )
    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    self.vspin._command_deceleration = AsyncMock()  # type: ignore[method-assign]

    await self.vspin.stop_spin(deceleration=0.5)

    self.assertTrue(self.vspin._spin_cancel_requested)
    self.vspin._command_deceleration.assert_awaited_once_with(0.5)  # type: ignore[attr-defined]
    self.assertEqual(  # type: ignore[attr-defined]
      self.vspin.request_positions_and_tachometer.await_count,
      3,
    )

  async def test_deceleration_timeout_reports_motion_status_and_velocity(self):
    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    self.vspin.request_positions_and_tachometer = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.ServoStatus(status=_nmc.STATUS_MOVE_DONE, velocity=-1)
    )

    with (
      patch("pylabrobot.agilent.vspin.vspin._SPIN_TIMEOUT_MARGIN", 0),
      self.assertRaisesRegex(
        TimeoutError,
        "last status was 0x01 at 14.7 RPM",
      ),
    ):
      await self.vspin._wait_until_stopped(initial_rpm=0, deceleration=0.5)

  async def test_bucket_calibration_is_normalized_and_saved_consistently(self):
    self.vspin.request_position = AsyncMock(return_value=12_345)  # type: ignore[method-assign]
    self.vspin.request_home_position = AsyncMock(return_value=400)  # type: ignore[method-assign]
    self.io.request_serial = AsyncMock(return_value="vspin-serial")

    with patch("pylabrobot.agilent.vspin.vspin._save_vspin_calibrations") as save:
      await self.vspin.set_bucket_1_position_to_current()

    self.assertEqual(self.vspin.bucket_1_remainder, 4055)
    save.assert_called_once_with("vspin-serial", 4055)

  async def test_bucket_targets_use_shortest_path_independently(self):
    self.vspin._bucket_1_remainder = 100
    self.vspin.request_home_position = AsyncMock(return_value=500)  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=7900)  # type: ignore[method-assign]

    self.assertEqual(await self.vspin.request_bucket_1_position(), 8400)
    self.assertEqual(await self.vspin.request_bucket_2_position(), 4400)

  async def test_bucket_target_uses_saved_home_position_after_spin(self):
    self.vspin._bucket_1_remainder = 100
    self.vspin._home_position = 500
    self.vspin.request_home_position = AsyncMock()  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=7900)  # type: ignore[method-assign]

    self.assertEqual(await self.vspin.request_bucket_1_position(), 8400)
    self.vspin.request_home_position.assert_not_awaited()  # type: ignore[attr-defined]

  async def test_bucket_presentation_retries_alignment_one_revolution_later(self):
    self.vspin.request_bucket_1_position = AsyncMock(return_value=8400)  # type: ignore[method-assign]
    self.vspin.go_to_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[vspin_module._PositionAlignmentError("misaligned"), None]
    )

    await self.vspin.go_to_bucket1()

    self.vspin.go_to_position.assert_has_awaits([call(8400), call(16400)])  # type: ignore[attr-defined]
    self.assertIs(self.vspin.at_bucket, self.vspin.bucket1)


class TestVSpinScriptedFTDI(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)

  def _make_vspin(self, steps: list[_VSpinScriptStep]) -> tuple[VSpin, _ScriptedVSpinFTDI]:
    vspin = VSpin(name="centrifuge")
    io = _ScriptedVSpinFTDI(steps)
    vspin.io = io  # type: ignore[assignment]
    vspin._servo_status_mask = _SERVO_STATUS_MASK
    vspin._io_status_mask = _IO_STATUS_MASK
    return vspin, io

  @staticmethod
  def _bucket_presentation_steps(
    current_position: int, target_position: int
  ) -> list[_VSpinScriptStep]:
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    servo_status = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    closed_locked_bucket_unlocked = 1 << _nmc.INPUT_BUCKET_LOCKED
    position_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._POSITION_TRAJECTORY_MODE,
      position=target_position,
      velocity=0x28F5C3,
      acceleration=0x1AD7,
    )
    return [
      _servo_step(servo_status, position=current_position),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
      _servo_step(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
      _servo_step(position_trajectory, status=0, position=current_position),
      _servo_step(servo_status, position=target_position),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
      _io_step(io_status, inputs=closed_locked_bucket_unlocked),
      _io_step(_nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0x0100), inputs=0),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=0),
      _io_step(
        _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0x0500),
        inputs=1 << _nmc.INPUT_DOOR_LOCKED,
      ),
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_LOCKED),
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_LOCKED),
      _io_step(
        _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0x0700),
        inputs=(1 << _nmc.INPUT_DOOR_LOCKED) | (1 << _nmc.INPUT_DOOR_OPEN),
      ),
      _io_step(
        io_status,
        inputs=(1 << _nmc.INPUT_DOOR_LOCKED) | (1 << _nmc.INPUT_DOOR_OPEN),
      ),
    ]

  @staticmethod
  def _network_reset_steps(stale_response: bytes | None = None) -> list[_VSpinScriptStep]:
    steps = [_VSpinScriptStep(b"\x00" * 20, None)]
    steps.extend(
      _VSpinScriptStep(
        _nmc.build_no_op(address) + b"\x00" * 8,
        None,
      )
      for address in range(33)
    )
    steps.append(_VSpinScriptStep(_nmc.build_hard_reset(), stale_response))
    return steps

  @classmethod
  def _setup_steps(cls) -> list[_VSpinScriptStep]:
    steps = cls._network_reset_steps()
    steps.extend(cls._network_reset_steps())
    steps.extend(
      [
        _empty_step(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(
            _nmc.STATUS_MOVE_DONE,
            bytes([_nmc.PIC_SERVO_MODULE_TYPE, 1]),
          ),
        ),
        _empty_step(_nmc.build_set_address(_nmc.PIC_IO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(
            _nmc.STATUS_MOVE_DONE,
            bytes([_nmc.PIC_IO_MODULE_TYPE, 1]),
          ),
        ),
        _VSpinScriptStep(_nmc.build_set_address(3), None),
        _VSpinScriptStep(_nmc.build_set_baud(57600), None),
        _servo_step(_nmc.build_define_status(_nmc.PIC_SERVO_ADDRESS, _SERVO_STATUS_MASK)),
      ]
    )
    steps.extend(
      _empty_step(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, 0x0FFF)) for _ in range(8)
    )
    steps.extend(
      _empty_step(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, direction))
      for direction in (0x0FDF, 0x0EDF, 0x0CDF, 0x08DF)
    )
    steps.extend(_empty_step(_nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0)) for _ in range(4))
    safe_inputs = 1 << _nmc.INPUT_BUCKET_LOCKED
    steps.append(
      _io_step(
        _nmc.build_define_status(_nmc.PIC_IO_ADDRESS, _IO_STATUS_MASK),
        inputs=safe_inputs,
      )
    )
    for _ in range(5):
      steps.extend(
        [
          _io_step(
            _nmc.build_set_output(
              _nmc.PIC_IO_ADDRESS,
              1 << _nmc.OUTPUT_VERSION_TOGGLE,
            ),
            inputs=safe_inputs,
          ),
          _io_step(
            _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0),
            inputs=safe_inputs,
          ),
        ]
      )
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    steps.extend(
      [
        _io_step(io_status, inputs=safe_inputs),
        _io_step(io_status, inputs=safe_inputs),
        _io_step(
          _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, 0),
          inputs=safe_inputs,
        ),
        _io_step(io_status, inputs=safe_inputs),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
        _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
        _servo_step(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
        _servo_step(_nmc.build_reset_position(_nmc.PIC_SERVO_ADDRESS)),
        _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._HOMING_GAINS)),
        _servo_step(
          _nmc.build_load_trajectory(
            _nmc.PIC_SERVO_ADDRESS,
            vspin_module._VELOCITY_TRAJECTORY_MODE,
            velocity=0x8312,
            acceleration=0x0112,
          ),
          status=_nmc.STATUS_HOMING_IN_PROGRESS,
        ),
        _servo_step(
          _nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28),
          status=_nmc.STATUS_HOMING_IN_PROGRESS,
        ),
        _servo_step(
          _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
          status=_nmc.STATUS_HOMING_IN_PROGRESS,
          position=100,
        ),
        _servo_step(
          _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
          position=200,
          home_position=200,
        ),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
        _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
        _servo_step(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
        _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
        _servo_step(
          _nmc.build_load_trajectory(
            _nmc.PIC_SERVO_ADDRESS,
            vspin_module._POSITION_TRAJECTORY_MODE,
            position=0,
            velocity=0x28F5C3,
            acceleration=0x1AD7,
          ),
          status=0,
          position=200,
          home_position=200,
        ),
        _servo_step(
          _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
          status=0,
          position=50,
          home_position=200,
        ),
        _servo_step(
          _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
          position=0,
          home_position=200,
        ),
        _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
        _io_step(io_status, inputs=safe_inputs),
        _io_step(io_status, inputs=safe_inputs),
      ]
    )
    return steps

  async def test_complete_setup_and_homing_ftdi_transcript(self):
    vspin, io = self._make_vspin(self._setup_steps())

    with (
      patch("pylabrobot.agilent.vspin.vspin.asyncio.sleep", new=AsyncMock()),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
    ):
      await vspin.setup()

    io.assert_complete(self)
    self.assertTrue(io.setup_called)
    self.assertEqual(io.setup_call_count, 4)
    self.assertEqual(io.stop_call_count, 3)
    self.assertEqual(io.latency_timers, [16, 16, 16, 16])
    self.assertEqual(io.line_properties, [(8, 1, 0)] * 4)
    self.assertEqual(io.flow_controls, [0, 0, 0, 0])
    self.assertEqual(io.baudrates, [19200, 19200, 19200, 19200, 57600])
    self.assertEqual(io.rx_purge_count, 3)
    self.assertEqual(io.rts_levels, [True])
    self.assertEqual(io.dtr_levels, [True])
    self.assertEqual(vspin._home_position, 200)

  async def test_network_initialization_probes_the_next_baudrate(self):
    steps = self._network_reset_steps()
    steps.extend(self._network_reset_steps())
    steps.append(_VSpinScriptStep(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS), None))
    steps.extend(self._network_reset_steps())
    steps.extend(self._network_reset_steps())
    steps.extend(
      [
        _empty_step(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_SERVO_MODULE_TYPE, 1])),
        ),
        _empty_step(_nmc.build_set_address(_nmc.PIC_IO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_IO_MODULE_TYPE, 1])),
        ),
        _VSpinScriptStep(_nmc.build_set_address(3), None),
        _VSpinScriptStep(_nmc.build_set_baud(57600), None),
      ]
    )
    vspin, io = self._make_vspin(steps)

    with (
      patch("pylabrobot.agilent.vspin.vspin.asyncio.sleep", new=AsyncMock()),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
    ):
      await vspin._initialize_nmc_network()

    io.assert_complete(self)
    self.assertEqual(io.setup_call_count, 5)
    self.assertEqual(io.stop_call_count, 5)
    self.assertEqual(io.baudrates, [19200, 19200, 19200, 115200, 19200, 19200, 57600])
    self.assertEqual(io.rx_purge_count, 5)

  async def test_network_reopens_to_discard_stale_reset_responses(self):
    steps = self._network_reset_steps(stale_response=b"\xfa")
    steps.extend(self._network_reset_steps(stale_response=b"\xfb"))
    steps.extend(
      [
        _empty_step(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_SERVO_MODULE_TYPE, 1])),
        ),
        _empty_step(_nmc.build_set_address(_nmc.PIC_IO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_IO_MODULE_TYPE, 1])),
        ),
        _VSpinScriptStep(_nmc.build_set_address(3), None),
        _VSpinScriptStep(_nmc.build_set_baud(57600), None),
      ]
    )
    vspin, io = self._make_vspin(steps)

    with (
      patch("pylabrobot.agilent.vspin.vspin.asyncio.sleep", new=AsyncMock()),
      patch("pylabrobot.agilent.vspin.vspin._NETWORK_PROBE_TIMEOUT", 0),
    ):
      await vspin._initialize_nmc_network()

    io.assert_complete(self)
    self.assertEqual(io.setup_call_count, 3)
    self.assertEqual(io.stop_call_count, 3)

  async def test_network_initialization_rejects_a_third_module(self):
    steps = self._network_reset_steps()
    steps.extend(self._network_reset_steps())
    steps.extend(
      [
        _empty_step(_nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_SERVO_MODULE_TYPE, 1])),
        ),
        _empty_step(_nmc.build_set_address(_nmc.PIC_IO_ADDRESS)),
        _VSpinScriptStep(
          _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_IO_MODULE_TYPE, 1])),
        ),
        _empty_step(_nmc.build_set_address(3)),
        _VSpinScriptStep(
          _nmc.build_read_status(3, _nmc.SEND_MODULE_ID),
          _nmc_response(1, bytes([_nmc.PIC_SERVO_MODULE_TYPE, 2])),
        ),
      ]
    )
    vspin, io = self._make_vspin(steps)

    with self.assertRaisesRegex(RuntimeError, "unexpected third NMC module"):
      await vspin._initialize_nmc_network()

    io.assert_complete(self)

  async def test_complete_spin_ftdi_transcript(self):
    g = 500
    duration = 1
    acceleration = 0.5
    deceleration = 0.6
    rpm = VSpin.g_to_rpm(g)
    spin_start_position = 0
    cruise_start_position = 100_000
    deceleration_position = int(
      cruise_start_position + rpm / 60 * _nmc.COUNTS_PER_REVOLUTION * duration
    )
    spin_target = spin_start_position + _nmc.spin_target_distance(
      rpm,
      duration,
      acceleration,
    )
    measured_velocity = -int(rpm / abs(vspin_module._TACHOMETER_TO_RPM))
    safe_inputs = 1 << _nmc.INPUT_BUCKET_LOCKED
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    servo_status = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    spin_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._POSITION_TRAJECTORY_MODE,
      position=spin_target,
      velocity=_nmc.rpm_to_nmc_velocity(rpm),
      acceleration=_nmc.acceleration_to_nmc(acceleration),
    )
    deceleration_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      vspin_module._VELOCITY_TRAJECTORY_MODE,
      velocity=0,
      acceleration=_nmc.acceleration_to_nmc(deceleration),
    )
    steps = [
      _io_step(io_status, inputs=safe_inputs),
      _io_step(io_status, inputs=safe_inputs),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(servo_status, position=spin_start_position),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._POSITION_GAINS)),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT)),
      _servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE)),
      _servo_step(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS)),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._VELOCITY_GAINS)),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(spin_trajectory, status=0, position=spin_start_position),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(
        servo_status,
        status=0,
        position=50_000,
        velocity=measured_velocity,
      ),
      _servo_step(
        servo_status,
        status=0,
        position=cruise_start_position,
        velocity=measured_velocity,
      ),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(
        servo_status,
        status=0,
        position=deceleration_position,
        velocity=measured_velocity,
      ),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._VELOCITY_GAINS)),
      _servo_step(deceleration_trajectory, status=0, position=deceleration_position),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(
        servo_status,
        position=deceleration_position,
        velocity=0,
      ),
    ]
    vspin, io = self._make_vspin(steps)
    vspin._at_bucket = vspin.bucket1

    await vspin.spin(g, duration, acceleration, deceleration)

    io.assert_complete(self)
    self.assertIsNone(vspin.at_bucket)
    self.assertNotIn(_nmc.build_reset_position(_nmc.PIC_SERVO_ADDRESS), io.writes)
    self.assertNotIn(
      _nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28),
      io.writes,
    )

  async def test_complete_abort_ftdi_transcript(self):
    initial_velocity = -100
    deceleration = 0.5
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    servo_status = _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS)
    safe_inputs = 1 << _nmc.INPUT_BUCKET_LOCKED
    steps = [
      _servo_step(servo_status, status=0, velocity=initial_velocity),
      _servo_step(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, vspin_module._VELOCITY_GAINS)),
      _servo_step(
        _nmc.build_load_trajectory(
          _nmc.PIC_SERVO_ADDRESS,
          vspin_module._VELOCITY_TRAJECTORY_MODE,
          velocity=0,
          acceleration=_nmc.acceleration_to_nmc(deceleration),
        ),
        status=0,
        velocity=initial_velocity,
      ),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(servo_status, status=0, velocity=-10),
      _io_step(io_status, inputs=safe_inputs),
      _servo_step(servo_status, velocity=0),
    ]
    vspin, io = self._make_vspin(steps)
    vspin._spin_active = True

    with patch("pylabrobot.agilent.vspin.vspin.asyncio.sleep", new=AsyncMock()):
      await vspin.stop_spin(deceleration)

    io.assert_complete(self)
    self.assertTrue(vspin._spin_cancel_requested)

  async def test_complete_bucket_presentation_ftdi_transcripts(self):
    current_position = 7900
    cases = (
      ("go_to_bucket1", 8400, "bucket1"),
      ("go_to_bucket2", 4400, "bucket2"),
    )
    for method_name, target_position, bucket_name in cases:
      with self.subTest(bucket=bucket_name):
        steps = self._bucket_presentation_steps(current_position, target_position)
        vspin, io = self._make_vspin(steps)
        vspin._home_position = 500
        vspin._bucket_1_remainder = 100

        await getattr(vspin, method_name)()

        io.assert_complete(self)
        self.assertIs(vspin.at_bucket, getattr(vspin, bucket_name))
        self.assertTrue(vspin.door_open)

  async def test_bucket_motion_fault_prevents_lock_and_door_commands(self):
    position = 8400
    steps = self._bucket_presentation_steps(7900, position)[1:13]
    steps[-1] = _servo_step(
      _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
      status=_nmc.STATUS_POSITION_ERROR,
      position=7900,
    )
    steps.append(_servo_step(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF)))
    vspin, io = self._make_vspin(steps)

    with self.assertRaisesRegex(RuntimeError, "position error.*move to position 8400"):
      await vspin.go_to_position(position)

    io.assert_complete(self)
    self.assertFalse(vspin.door_open)

  async def test_door_and_lock_operations_are_idempotent(self):
    io_status = _nmc.build_no_op(_nmc.PIC_IO_ADDRESS)
    steps = [
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_OPEN),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=1 << _nmc.INPUT_DOOR_LOCKED),
      _io_step(io_status, inputs=0),
      _io_step(io_status, inputs=0),
    ]
    vspin, io = self._make_vspin(steps)

    await vspin.open_door()
    await vspin.close_door()
    await vspin.lock_door()
    await vspin.unlock_door()
    await vspin.lock_bucket()
    await vspin.unlock_bucket()

    io.assert_complete(self)
    self.assertTrue(all(write == io_status for write in io.writes))

  async def test_stop_closes_ftdi_without_resetting_the_nmc_network(self):
    vspin, io = self._make_vspin([])

    await vspin.stop()

    io.assert_complete(self)
    self.assertTrue(io.stopped)
    self.assertEqual(io.writes, [])


class TestAccess2Events(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.vspin_ftdi = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.access2_ftdi = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    self.vspin_ftdi.start()
    self.access2_ftdi.start()
    self.addCleanup(self.access2_ftdi.stop)
    self.addCleanup(self.vspin_ftdi.stop)

  async def asyncSetUp(self):
    self.vspin = VSpin(name="centrifuge", device_id="test")
    self.vspin._door_open = True
    self.vspin._at_bucket = self.vspin.bucket1
    self.vspin.request_door_open = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_spinning = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.loader = Access2(name="loader", device_id="test", vspin=self.vspin)
    self.loader.driver.load = AsyncMock()  # type: ignore[method-assign]
    self.loader.driver.unload = AsyncMock()  # type: ignore[method-assign]

  async def test_load_emits_loader_to_bucket_transfer(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.loader.load()

    lifecycle_events = [
      event for event in events if event.name.startswith("centrifuge_loader.load.")
    ]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      [
        "centrifuge_loader.load.started",
        "centrifuge_loader.load.completed",
      ],
    )
    started, completed = lifecycle_events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["resources"][0]["name"], "plate_1")
    self.assertEqual(started.data["source"]["name"], "loader")
    self.assertEqual(started.data["destination"]["name"], "centrifuge_bucket1")
    self.assertIs(self.vspin.bucket1.resource, plate)

  async def test_unload_failure_emits_bucket_to_loader_transfer(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    self.loader.driver.unload = AsyncMock(  # type: ignore[method-assign]
      side_effect=RuntimeError("loader fault")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaisesRegex(RuntimeError, "loader fault"):
        await self.loader.unload()

    lifecycle_events = [
      event for event in events if event.name.startswith("centrifuge_loader.unload.")
    ]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      [
        "centrifuge_loader.unload.started",
        "centrifuge_loader.unload.failed",
      ],
    )
    started, failed = lifecycle_events
    self.assertEqual(started.context["operation_id"], failed.context["operation_id"])
    self.assertEqual(started.data["source"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["destination"]["name"], "loader")
    self.assertEqual(failed.data["error_type"], "RuntimeError")

  async def test_load_requires_physical_bucket_lock_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with self.assertRaisesRegex(RuntimeError, "physically locked"):
      await self.loader.load()

    self.loader.driver.load.assert_not_awaited()  # type: ignore[attr-defined]
    self.assertIs(self.loader.resource, plate)
    self.assertIsNone(self.vspin.bucket1.resource)

  async def test_unload_requires_stopped_vspin_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin.request_spinning = AsyncMock(return_value=True)  # type: ignore[method-assign]

    with self.assertRaisesRegex(RuntimeError, "must be stopped"):
      await self.loader.unload()

    self.loader.driver.unload.assert_not_awaited()  # type: ignore[attr-defined]
    self.assertIs(self.vspin.bucket1.resource, plate)
    self.assertIsNone(self.loader.resource)

  async def test_load_requires_physical_door_open_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with self.assertRaisesRegex(CentrifugeDoorError, "door-open sensor"):
      await self.loader.load()

    self.loader.driver.load.assert_not_awaited()  # type: ignore[attr-defined]
