"""Reliability tests for the consolidated Celigo controller implementation."""

import asyncio
import struct
import unittest
from contextlib import ExitStack
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Tuple
from unittest.mock import AsyncMock, patch

from pylabrobot.io.ftdi import FTDI
from pylabrobot.revvity.celigo.celigo import (
  _MAX_RESPONSE_PAYLOAD_BYTES,
  _STATUS_INTERLOCK_OPEN,
  CeligoError,
  ControllerInfo,
  ControllerStatus,
  _fletcher16,
)
from pylabrobot.revvity.celigo.config import Calibrated2DPolynomialTransform, CeligoHardwareConfig
from pylabrobot.revvity.celigo.galvo import dac_count_to_volts
from pylabrobot.revvity.celigo.motion import _decode_oem_response
from pylabrobot.revvity.celigo.tests.helpers import (
  make_calibration_config,
  make_celigo,
  make_galvo_config,
  make_linear_axis_config,
)


def _oem_response(content: bytes) -> bytes:
  body = b"\x02" + content + b"\x03"
  checksum = 0
  for value in body:
    checksum ^= value
  return body + bytes([checksum])


def _motor_response(reply: bytes, status: int = 0) -> bytes:
  return struct.pack(">HH", status, len(reply)) + reply


class TestOemResponse(unittest.TestCase):
  def test_valid_response_is_unwrapped(self):
    self.assertEqual(_decode_oem_response(_oem_response(b"0`123")), "/0`123")

  def test_checksum_failure_is_rejected(self):
    response = bytearray(_oem_response(b"0`123"))
    response[-1] ^= 0x01
    with self.assertRaisesRegex(CeligoError, "checksum failure"):
      _decode_oem_response(bytes(response))

  def test_missing_frame_fields_are_rejected(self):
    for response in (b"0`123", b"\x020`123", b"\x020`123\x03"):
      with self.subTest(response=response), self.assertRaises(CeligoError):
        _decode_oem_response(response)


class TestMotorReliability(unittest.IsolatedAsyncioTestCase):
  async def test_public_z_move_converts_millimeters_to_internal_ticks(self):
    driver = make_celigo(
      hardware=CeligoHardwareConfig(
        z_axis=make_linear_axis_config(
          axis_index=3,
          min_position=0,
          max_position=10,
          mm_per_encoder_tick=0.5,
        )
      )
    )
    calls = []

    async def move_ticks(
      target_encoder_ticks,
      arrival_tolerance_ticks=None,
      **_kwargs,
    ):
      calls.append((target_encoder_ticks, arrival_tolerance_ticks))
      return target_encoder_ticks

    with patch.object(driver.z_axis, "move_to_ticks", move_ticks):
      settled_mm = await driver.z_axis.move_to(2.5)
    self.assertEqual(calls, [(5, None)])
    self.assertEqual(settled_mm, 2.5)

  async def test_public_position_read_returns_millimeters(self):
    driver = make_celigo(
      hardware=CeligoHardwareConfig(
        z_axis=make_linear_axis_config(
          axis_index=3,
          min_position=0,
          max_position=10,
          home_offset=1,
          mm_per_encoder_tick=0.5,
        )
      )
    )

    async def request_encoder_ticks():
      return 7

    with patch.object(driver.z_axis, "request_encoder_ticks", request_encoder_ticks):
      self.assertEqual(await driver.z_axis.request_position(), 2.5)

  async def test_xyz_move_requires_explicit_trust_after_vendor_homing(self):
    driver = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          min_position=0,
          max_position=10,
          mm_per_encoder_tick=1,
        )
      )
    )

    async def send(*_args, **_kwargs):
      self.fail("untrusted move reached motor IO")

    with (
      patch.object(driver.motor_controller, "send_command", send),
      self.assertRaisesRegex(CeligoError, "no position reference"),
    ):
      await driver.x_axis.move_to(5)

  async def test_assume_homed_adopts_an_in_range_external_position(self):
    driver = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          min_position=0,
          max_position=10,
          mm_per_encoder_tick=1,
        )
      )
    )
    restored_modes = []

    async def request_encoder_ticks():
      return 4

    async def set_mode(mode):
      restored_modes.append(mode)

    with (
      patch.object(driver.x_axis, "request_encoder_ticks", request_encoder_ticks),
      patch.object(driver.x_axis.motor, "_set_mode", set_mode),
    ):
      self.assertEqual(await driver.x_axis.assume_homed(), 4)
    self.assertTrue(driver.x_axis.has_position_reference)
    self.assertEqual(restored_modes, [0])

  async def test_invalid_axis_scale_fails_closed_before_io(self):
    driver = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          min_position=0,
          max_position=10,
          mm_per_encoder_tick=0,
        )
      )
    )

    async def send(*_args, **_kwargs):
      self.fail("invalid-scale move reached motor IO")

    with (
      patch.object(driver.motor_controller, "send_command", send),
      self.assertRaisesRegex(CeligoError, "invalid mm_per_encoder_tick"),
    ):
      await driver.x_axis.move_to(5)

  async def test_public_move_rejects_sub_tick_out_of_range_mm(self):
    driver = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          min_position=0,
          max_position=10,
          mm_per_encoder_tick=1,
        )
      )
    )

    async def move_ticks(*_args, **_kwargs):
      self.fail("out-of-range millimeter move reached tick motion")

    with (
      patch.object(driver.x_axis, "move_to_ticks", move_ticks),
      self.assertRaisesRegex(CeligoError, "outside configured range"),
    ):
      await driver.x_axis.move_to(-0.49)

  async def test_requested_move_tolerance_reaches_arrival_check(self):
    axis = make_linear_axis_config(
      axis_index=1,
      min_position=0,
      max_position=10,
      mm_per_encoder_tick=0.5,
      fine_position_error_window=4,
    )
    driver = make_celigo(hardware=CeligoHardwareConfig(x_axis=axis))
    calls = []

    async def configured_move(target, **kwargs):
      calls.append((target, kwargs))
      return target

    with patch.object(driver.x_axis, "move_to_ticks", configured_move):
      self.assertEqual(await driver.x_axis.move_to(2.5, tolerance_mm=0.5), 2.5)
    self.assertEqual(calls, [(5, {"arrival_tolerance_ticks": 1})])

  async def test_absolute_move_rejects_configured_out_of_range_target_before_io(self):
    driver = make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          min_position=1,
          max_position=3,
          mm_per_encoder_tick=0.5,
        )
      )
    )

    async def send(*_args, **_kwargs):
      self.fail("out-of-range move reached motor IO")

    with (
      patch.object(driver.motor_controller, "send_command", send),
      self.assertRaisesRegex(CeligoError, "outside configured range"),
    ):
      await driver.x_axis.move_to(7)

  async def test_bad_oem_checksum_is_retried(self):
    driver = make_celigo()
    driver.controller_info = ControllerInfo(0, (1, 3, 0), 512)
    bad = bytearray(_oem_response(b"0`42"))
    bad[-1] ^= 0x01
    responses = [
      _motor_response(bytes(bad)),
      _motor_response(_oem_response(b"0`42")),
    ]
    calls = 0

    async def transact(_opcode, _payload):
      nonlocal calls
      calls += 1
      return responses.pop(0)

    with patch.object(driver, "send_command", transact):
      self.assertEqual(await driver.motor_controller.send_command("/1?8\r"), "/0`42")
    self.assertEqual(calls, 2)

  async def test_wlen_motor_comm_error_is_retried(self):
    driver = make_celigo()
    driver.controller_info = ControllerInfo(0, (1, 3, 0), 512)
    responses = [
      struct.pack(">H", 5025),  # controller motor-communication error
      _motor_response(_oem_response(b"0`7")),
    ]

    async def transact(_opcode, _payload):
      return responses.pop(0)

    with patch.object(driver, "send_command", transact):
      self.assertEqual(await driver.motor_controller.send_command("/1?8\r"), "/0`7")

  async def test_truncated_motor_response_is_rejected(self):
    driver = make_celigo()
    driver.controller_info = ControllerInfo(0, (1, 2, 999), 512)

    async def transact(_opcode, _payload):
      return b"\x00"

    with (
      patch.object(driver, "send_command", transact),
      self.assertRaisesRegex(CeligoError, "Truncated motor query"),
    ):
      await driver.motor_controller.send_command("/1?8\r")

  async def test_oversize_motor_command_is_rejected_before_io(self):
    driver = make_celigo()
    driver.controller_info = ControllerInfo(0, (1, 2, 999), 512)

    async def transact(_opcode, _payload):
      self.fail("oversize command reached the transport")

    with (
      patch.object(driver, "send_command", transact),
      self.assertRaisesRegex(ValueError, "maximum is 512"),
    ):
      await driver.motor_controller.send_command("x" * 513)

  async def test_motor_command_framing_is_derived_from_controller_firmware(self):
    driver = make_celigo()
    with self.assertRaisesRegex(CeligoError, "before controller identification"):
      await driver.motor_controller.send_command("/1?8\r")

    opcodes = []

    async def transact(opcode, _payload):
      opcodes.append(opcode)
      reply = b"/0`" if opcode == 44 else _oem_response(b"0`")
      return _motor_response(reply)

    with patch.object(driver, "send_command", transact):
      driver.controller_info = ControllerInfo(0, (1, 2, 999), 512)
      await driver.motor_controller.send_command("/1?8\r")

      driver.controller_info = ControllerInfo(0, (1, 3, 0), 512)
      await driver.motor_controller.send_command("/1?8\r")
    self.assertEqual(opcodes, [44, 47])


class TestResponseValidation(unittest.IsolatedAsyncioTestCase):
  async def test_controller_status_is_decoded_into_named_fields(self):
    driver = make_celigo()

    async def transact(_opcode):
      return struct.pack(">II", 0b1101, 42)

    with patch.object(driver, "send_command", transact):
      status = await driver.request_controller_status()
    self.assertEqual(status.raw_flags, 0b1101)
    self.assertEqual(status.extended_status, 42)
    self.assertTrue(status.busy)
    self.assertFalse(status.error)
    self.assertTrue(status.interlock_open)
    self.assertTrue(status.controller_failed)
    self.assertTrue(status.has_controller_fault)
    self.assertTrue(status.has_laser_safety_fault)

  async def test_analog_output_reply_must_echo_the_requested_channel(self):
    driver = make_celigo()

    async def send_command(_opcode, _payload):
      return struct.pack(">HH", 1, 123)

    with (
      patch.object(driver, "send_command", send_command),
      self.assertRaisesRegex(CeligoError, "requested 2, received 1"),
    ):
      await driver.request_analog_output_count(2)

  async def test_corrupt_header_checksum_never_retransmits_command(self):
    # Independent reference implementation: no production checksum helper is used.
    def reference_fletcher(data):
      first = second = 0xFF
      for offset in range(0, len(data), 21):
        for value in data[offset : offset + 21]:
          first += value
          second += first
        first = (first & 0xFF) + (first >> 8)
        second = (second & 0xFF) + (second >> 8)
      first = (first & 0xFF) + (first >> 8)
      second = (second & 0xFF) + (second >> 8)
      return bytes((first & 0xFF, second & 0xFF))

    class CorruptReplyIO:
      def __init__(self):
        header = bytearray(12)
        header[1] = 23
        struct.pack_into(">i", header, 2, 1)
        struct.pack_into(">i", header, 6, 0)
        header[10:12] = reference_fletcher(header[:10])
        header[10] ^= 1
        self.reply = bytes(header)
        self.writes = 0

      async def write(self, data):
        self.writes += 1
        return len(data)

      async def read(self, count):
        result, self.reply = self.reply[:count], self.reply[count:]
        return result

      async def usb_purge_rx_buffer(self):
        return None

      async def usb_purge_tx_buffer(self):
        return None

    driver = make_celigo()
    driver._command_sequence = 0
    driver.reply_timeout = 0.1
    io = CorruptReplyIO()
    with (
      patch.object(driver, "io", io),
      self.assertRaisesRegex(CeligoError, "checksum failure"),
    ):
      await driver.send_command(23)
    self.assertEqual(io.writes, 1)

  async def test_oversize_payload_is_rejected_before_body_read(self):
    driver = make_celigo()
    header = bytearray(12)
    header[1] = 23
    struct.pack_into(">i", header, 2, 7)
    struct.pack_into(">i", header, 6, _MAX_RESPONSE_PAYLOAD_BYTES + 1)
    header[10], header[11] = _fletcher16(header, 10)
    reads = 0

    async def read_exact(_count, _reply_timeout):
      nonlocal reads
      reads += 1
      return bytes(header)

    with (
      patch.object(driver, "_read_exact_bytes", read_exact),
      self.assertRaisesRegex(CeligoError, "maximum"),
    ):
      await driver._read_controller_response(23, 7, 0.1)
    self.assertEqual(reads, 1)

  async def test_galvo_busy_bytes_match_vendor_semantics(self):
    driver = make_celigo()
    driver.config.hardware = CeligoHardwareConfig(
      x_galvo=make_galvo_config(enabled=True),
      y_galvo=make_galvo_config(enabled=True),
    )

    async def transact(_opcode, _payload=b""):
      return struct.pack(">BBHHiiiBhh", 0, 1, 32768, 32768, 0, 0, 0, 0, 0, 0)

    with patch.object(driver, "send_command", transact):
      status = await driver.galvo.request_controller_status()
    self.assertTrue(status.x_busy)
    self.assertFalse(status.y_busy)
    self.assertAlmostEqual(status.x_hardware_voltage, dac_count_to_volts(32768))
    self.assertAlmostEqual(status.y_hardware_voltage, dac_count_to_volts(32768))

  async def test_short_write_purges_both_buffers(self):
    class ShortWriteIO:
      def __init__(self):
        self.rx_purges = 0
        self.tx_purges = 0

      async def write(self, data):
        return len(data) - 1

      async def usb_purge_rx_buffer(self):
        self.rx_purges += 1

      async def usb_purge_tx_buffer(self):
        self.tx_purges += 1

    driver = make_celigo()
    driver._command_sequence = 1
    io = ShortWriteIO()
    with patch.object(driver, "io", io), self.assertRaisesRegex(CeligoError, "Short write"):
      await driver.send_command(23)
    self.assertEqual((io.rx_purges, io.tx_purges), (1, 1))

  async def test_cancelled_read_purges_both_buffers_before_returning(self):
    read_started = asyncio.Event()

    class BlockingReadIO:
      def __init__(self):
        self.purges = []

      async def write(self, data):
        return len(data)

      async def read(self, _count):
        read_started.set()
        await asyncio.Future()

      async def usb_purge_rx_buffer(self):
        self.purges.append("rx")

      async def usb_purge_tx_buffer(self):
        self.purges.append("tx")

    driver = make_celigo()
    io = BlockingReadIO()
    with patch.object(driver, "io", io):
      command = asyncio.create_task(driver.send_command(23))
      await read_started.wait()
      command.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await command

    self.assertEqual(io.purges, ["rx", "tx"])


class TestSelfTest(unittest.IsolatedAsyncioTestCase):
  @staticmethod
  def _configured_driver():
    return make_celigo(
      hardware=CeligoHardwareConfig(
        x_axis=make_linear_axis_config(
          axis_index=1,
          min_position=0,
          max_position=20,
          mm_per_encoder_tick=1,
          encoder_to_motor_tick_ratio=1,
        ),
        y_axis=make_linear_axis_config(
          axis_index=2,
          min_position=0,
          max_position=20,
          mm_per_encoder_tick=1,
          encoder_to_motor_tick_ratio=2,
        ),
        z_axis=make_linear_axis_config(
          axis_index=3,
          min_position=0,
          max_position=20,
          mm_per_encoder_tick=1,
          encoder_to_motor_tick_ratio=3,
        ),
      )
    )

  async def test_encoder_ratio_failure_is_attributed_to_the_correct_motor(self):
    driver = self._configured_driver()
    with ExitStack() as patches:
      patches.enter_context(
        patch.multiple(
          driver,
          request_controller_status=AsyncMock(return_value=ControllerStatus(0, 0)),
          request_controller_info=AsyncMock(return_value=ControllerInfo(1, (1, 3, 0), 256)),
          request_detected_motor_addresses=AsyncMock(return_value=[]),
          request_digital_input_bitmask=AsyncMock(return_value=0),
        )
      )
      for axis in driver._configured_motion_axes():
        patches.enter_context(
          patch.multiple(
            axis,
            request_encoder_ticks=AsyncMock(return_value=10),
            request_encoder_ratio=AsyncMock(
              return_value={1: 1.0, 2: 999.0, 3: 3.0}[axis.axis_index]
            ),
          )
        )
      report = await driver.run_self_test()

    self.assertFalse(report.passed)
    self.assertEqual(report.failures, ("motor_2_encoder_ratio",))
    self.assertFalse(report.checks["motor_2_encoder_ratio"]["matches"])

  async def test_unpopulated_generic_interlock_does_not_fail_controller_self_test(self):
    driver = self._configured_driver()
    with ExitStack() as patches:
      patches.enter_context(
        patch.multiple(
          driver,
          request_controller_status=AsyncMock(
            return_value=ControllerStatus(_STATUS_INTERLOCK_OPEN, 0)
          ),
          request_controller_info=AsyncMock(return_value=ControllerInfo(1, (1, 3, 0), 256)),
          request_detected_motor_addresses=AsyncMock(return_value=[]),
          request_digital_input_bitmask=AsyncMock(return_value=0),
        )
      )
      for axis in driver._configured_motion_axes():
        patches.enter_context(
          patch.multiple(
            axis,
            request_encoder_ticks=AsyncMock(return_value=10),
            request_encoder_ratio=AsyncMock(return_value=float(axis.axis_index)),
          )
        )
      report = await driver.run_self_test()

    self.assertTrue(report.passed)
    self.assertEqual(report.failures, ())
    self.assertTrue(report.checks["controller_status"].interlock_open)

  async def test_controller_fault_is_attributed_to_existing_status_check(self):
    driver = self._configured_driver()
    with ExitStack() as patches:
      patches.enter_context(
        patch.multiple(
          driver,
          request_controller_status=AsyncMock(return_value=ControllerStatus(2, 0)),
          request_controller_info=AsyncMock(return_value=ControllerInfo(1, (1, 3, 0), 256)),
          request_detected_motor_addresses=AsyncMock(return_value=[]),
          request_digital_input_bitmask=AsyncMock(return_value=0),
        )
      )
      for axis in driver._configured_motion_axes():
        patches.enter_context(
          patch.multiple(
            axis,
            request_encoder_ticks=AsyncMock(return_value=10),
            request_encoder_ratio=AsyncMock(return_value=float(axis.axis_index)),
          )
        )
      report = await driver.run_self_test()

    self.assertEqual(report.failures, ("controller_status",))
    self.assertIn("controller_status", report.checks)

  async def test_failed_galvo_calibration_has_a_named_check(self):
    driver = self._configured_driver()
    driver.config.galvo_calibrations = {
      3: Calibrated2DPolynomialTransform(
        forward={},
        reverse={},
        order=2,
        successful=False,
      ),
    }
    with ExitStack() as patches:
      patches.enter_context(
        patch.multiple(
          driver,
          request_controller_status=AsyncMock(return_value=ControllerStatus(0, 0)),
          request_controller_info=AsyncMock(return_value=ControllerInfo(1, (1, 3, 0), 256)),
          request_detected_motor_addresses=AsyncMock(return_value=[]),
          request_digital_input_bitmask=AsyncMock(return_value=0),
        )
      )
      for axis in driver._configured_motion_axes():
        patches.enter_context(
          patch.multiple(
            axis,
            request_encoder_ticks=AsyncMock(return_value=10),
            request_encoder_ratio=AsyncMock(return_value=float(axis.axis_index)),
          )
        )
      report = await driver.run_self_test()

    self.assertEqual(report.failures, ("galvo_calibration_3",))
    self.assertFalse(report.checks["galvo_calibration_3"])

  async def test_motion_checks_require_active_checks(self):
    driver = self._configured_driver()
    with self.assertRaisesRegex(ValueError, "requires run_active_checks"):
      await driver.run_self_test(run_motion_checks=True)

  async def test_motion_checks_round_trip_each_linear_axis(self):
    driver = self._configured_driver()
    moves = []

    async def no_op():
      return None

    with ExitStack() as patches:
      patches.enter_context(
        patch.multiple(
          driver,
          request_controller_status=AsyncMock(return_value=ControllerStatus(0, 0)),
          request_controller_info=AsyncMock(return_value=ControllerInfo(1, (1, 3, 0), 256)),
          request_detected_motor_addresses=AsyncMock(return_value=[]),
          request_digital_input_bitmask=AsyncMock(return_value=0),
          capture_frame=no_op,
        )
      )
      patches.enter_context(patch.object(driver.galvo, "home", no_op))
      for axis in driver._configured_motion_axes():

        async def move_to_ticks(target_encoder_ticks, axis_name=axis.name):
          moves.append((axis_name, target_encoder_ticks))
          return target_encoder_ticks

        patches.enter_context(
          patch.multiple(
            axis,
            request_encoder_ticks=AsyncMock(return_value=10),
            request_encoder_ratio=AsyncMock(return_value=float(axis.axis_index)),
            move_to_ticks=move_to_ticks,
          )
        )
      report = await driver.run_self_test(
        run_active_checks=True,
        run_motion_checks=True,
      )

    self.assertTrue(report.passed)
    self.assertEqual(
      moves,
      [
        ("x", 15),
        ("x", 10),
        ("y", 15),
        ("y", 10),
        ("z", 15),
        ("z", 10),
      ],
    )


class _LifecycleIO:
  def __init__(self):
    self.stopped = False

  async def setup(self):
    return None

  async def set_baudrate(self, _baudrate):
    return None

  async def set_line_property(self, *_args):
    return None

  async def set_latency_timer(self, _latency):
    return None

  async def usb_purge_rx_buffer(self):
    return None

  async def usb_purge_tx_buffer(self):
    return None

  async def stop(self):
    self.stopped = True


class _LifecycleCamera:
  def __init__(self):
    self.is_open = False
    self.width = 2464
    self.height = 2056
    self.exposure_ms = 1.0
    self.gain = 1.0
    self.setup_calls = 0
    self.stop_calls = 0
    self.format_calls = []

  async def setup(self):
    self.setup_calls += 1
    self.is_open = True

  async def stop(self):
    self.stop_calls += 1
    self.is_open = False

  async def set_frame_format(self, width, height, x_offset=None, y_offset=None):
    self.format_calls.append((width, height, x_offset, y_offset))
    self.width = width
    self.height = height
    return width, height


@dataclass(frozen=True)
class _UsbDevice:
  bus: int
  address: int
  port_numbers: Tuple[int, ...]


class TestLifecycleReliability(unittest.IsolatedAsyncioTestCase):
  async def test_setup_owns_camera_and_applies_calibrated_geometry(self):
    celigo = make_celigo()
    io = _LifecycleIO()
    camera = _LifecycleCamera()
    celigo.config.calibration = make_calibration_config(
      image_width_pixels=2048,
      image_height_pixels=2048,
    )
    celigo.baudrate = 230400
    celigo.latency_ms = 2
    celigo.controller_info = None
    celigo.config.hardware = CeligoHardwareConfig()
    celigo._connected = False
    initialization_calls = 0
    homing_calls = 0

    async def no_op(*_args, **_kwargs):
      return None

    async def status():
      return ControllerStatus(0, 0)

    async def identity():
      return ControllerInfo(1, (1, 3, 0), 256)

    async def track_hardware_initialization():
      nonlocal initialization_calls
      initialization_calls += 1

    async def track_homing():
      nonlocal homing_calls
      homing_calls += 1

    with patch.multiple(
      celigo,
      io=io,
      camera=camera,
      abort_controller_operation=no_op,
      request_controller_status=status,
      request_controller_info=identity,
      _initialize_hardware=track_hardware_initialization,
      _initialize_safe_outputs=no_op,
      home_imaging_axes=track_homing,
    ):
      await celigo.setup()
      self.assertEqual(initialization_calls, 1)
      self.assertEqual(homing_calls, 1)
      self.assertEqual(camera.setup_calls, 1)
      self.assertEqual(camera.format_calls, [(2048, 2048, None, None)])
      self.assertEqual((camera.width, camera.height), (2048, 2048))
      self.assertTrue(camera.is_open)
      self.assertTrue(celigo._connected)
      await celigo.stop()
    self.assertEqual(camera.stop_calls, 1)
    self.assertFalse(camera.is_open)

  async def test_normal_stop_aborts_and_clears_outputs_before_transport_close(self):
    celigo = make_celigo()
    io = _LifecycleIO()
    celigo._connected = True
    operations = []

    async def abort_controller_operation():
      operations.append("abort_controller_operation")

    async def safe_outputs():
      operations.append("safe_outputs")

    with patch.multiple(
      celigo,
      io=io,
      abort_controller_operation=abort_controller_operation,
      _initialize_safe_outputs=safe_outputs,
    ):
      await celigo.stop()
    self.assertEqual(operations, ["abort_controller_operation", "safe_outputs"])
    self.assertTrue(io.stopped)

  async def test_setup_closes_transport_when_identity_fails(self):
    celigo = make_celigo()
    io = _LifecycleIO()
    celigo.baudrate = 230400
    celigo.latency_ms = 2
    celigo.controller_info = None

    async def status():
      return ControllerStatus(0, 0)

    async def identity():
      raise CeligoError("simulated identity failure")

    async def abort_controller_operation():
      return None

    with (
      patch.multiple(
        celigo,
        io=io,
        request_controller_status=status,
        request_controller_info=identity,
        abort_controller_operation=abort_controller_operation,
      ),
      self.assertRaisesRegex(CeligoError, "identity failure"),
    ):
      await celigo.setup()
    self.assertTrue(io.stopped)

  async def test_stop_closes_transport_when_camera_stop_fails(self):
    class FailingCamera:
      async def stop(self):
        raise RuntimeError("simulated camera failure")

    celigo = make_celigo()
    io = _LifecycleIO()
    with (
      patch.multiple(celigo, io=io, camera=FailingCamera()),
      self.assertRaisesRegex(RuntimeError, "camera failure"),
    ):
      await celigo.stop()
    self.assertTrue(io.stopped)


class TestFtdiTopologySelection(unittest.TestCase):
  def test_topology_resolves_exact_bus_and_device_address(self):
    device = _UsbDevice(bus=3, address=17, port_numbers=(2, 4))
    usb_module = SimpleNamespace(core=SimpleNamespace(find=lambda **_kwargs: [device]))
    with (
      patch("pylabrobot.io.ftdi.HAS_PYLIBFTDI", True),
      patch("pylabrobot.io.ftdi.HAS_PYUSB", True),
      patch("pylabrobot.io.ftdi.usb", usb_module, create=True),
    ):
      ftdi = FTDI(
        human_readable_device_name="Celigo",
        vid=0x0403,
        pid=0x6001,
        usb_address="3-2.4",
      )
      self.assertEqual(ftdi._resolve_device_location(), (3, 17))


if __name__ == "__main__":
  unittest.main()
