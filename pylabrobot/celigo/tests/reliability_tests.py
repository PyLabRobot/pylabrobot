"""Reliability tests for the consolidated Celigo controller implementation."""

import asyncio
import struct
import unittest
from dataclasses import dataclass
from typing import Tuple
from unittest.mock import patch

from pylabrobot.celigo.celigo import (
  _EXT_MOTOR_COMM_ERROR,
  _MAX_RESPONSE_PAYLOAD_BYTES,
  CeligoError,
  ControllerStatus,
  DeviceInfo,
  _fletcher16,
  _from_oem_response,
)
from pylabrobot.celigo.config import (
  AxisConfig,
  CalibrationConfig,
  CeligoHardwareConfig,
  GalvoConfig,
)
from pylabrobot.celigo.tests.helpers import make_celigo, stub
from pylabrobot.io.ftdi import FTDI


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
    self.assertEqual(_from_oem_response(_oem_response(b"0`123")), "/0`123")

  def test_checksum_failure_is_rejected(self):
    response = bytearray(_oem_response(b"0`123"))
    response[-1] ^= 0x01
    with self.assertRaisesRegex(CeligoError, "checksum failure"):
      _from_oem_response(bytes(response))

  def test_missing_frame_fields_are_rejected(self):
    for response in (b"0`123", b"\x020`123", b"\x020`123\x03"):
      with self.subTest(response=response), self.assertRaises(CeligoError):
        _from_oem_response(response)


class TestMotorReliability(unittest.IsolatedAsyncioTestCase):
  async def test_public_z_move_converts_millimeters_to_internal_ticks(self):
    driver = make_celigo()
    driver.config = CeligoHardwareConfig(
      z_axis=AxisConfig(min_position=0, max_position=10, mm_per_encoder_tick=0.5)
    )
    calls = []

    async def move_ticks(axis, ticks, wait=True, tolerance=None):
      calls.append((axis, ticks, wait, tolerance))
      return ticks

    stub(driver, _move_ticks=move_ticks)
    settled_mm = await driver.move_z(2.5)
    self.assertEqual(calls, [("z", 5, True, None)])
    self.assertEqual(settled_mm, 2.5)

  async def test_xyz_move_requires_explicit_trust_after_vendor_homing(self):
    driver = make_celigo()
    driver.config = CeligoHardwareConfig(
      x_axis=AxisConfig(min_position=0, max_position=10, mm_per_encoder_tick=1)
    )
    driver._trusted_axes = set()

    async def send(_command):
      self.fail("untrusted move reached motor IO")

    stub(driver, _send_ez=send)
    with self.assertRaisesRegex(CeligoError, "not trusted"):
      await driver.move("x", 5)

  async def test_invalid_axis_scale_fails_closed_before_io(self):
    driver = make_celigo()
    driver.config = CeligoHardwareConfig(
      x_axis=AxisConfig(min_position=0, max_position=10, mm_per_encoder_tick=0)
    )

    async def send(_command):
      self.fail("invalid-scale move reached motor IO")

    stub(driver, _send_ez=send)
    with self.assertRaisesRegex(CeligoError, "invalid mm_per_encoder_tick"):
      await driver.move("x", 5)

  async def test_public_move_rejects_sub_tick_out_of_range_mm(self):
    driver = make_celigo()
    driver.config = CeligoHardwareConfig(
      x_axis=AxisConfig(min_position=0, max_position=10, mm_per_encoder_tick=1)
    )

    async def move_ticks(*_args, **_kwargs):
      self.fail("out-of-range millimeter move reached tick motion")

    stub(driver, _move_ticks=move_ticks)
    with self.assertRaisesRegex(CeligoError, "outside configured range"):
      await driver.move("x", -0.49)

  async def test_requested_move_tolerance_reaches_arrival_check(self):
    driver = make_celigo()
    axis = AxisConfig(
      min_position=0,
      max_position=10,
      mm_per_encoder_tick=0.5,
      fine_position_error_window=4,
    )
    driver.config = CeligoHardwareConfig(x_axis=axis)
    driver._trusted_axes = {"x"}
    calls = []

    async def configured_move(axis_config, target, **kwargs):
      calls.append((axis_config, target, kwargs))
      return target

    stub(driver, _move_configured_absolute=configured_move)
    self.assertEqual(await driver.move("x", 2.5, tolerance_mm=0.5), 2.5)
    self.assertEqual(calls[0][1:], (5, {"arrival_tolerance": 1}))

  async def test_public_relative_motion_is_disabled(self):
    driver = make_celigo()
    with self.assertRaisesRegex(CeligoError, "relative motion is disabled"):
      await driver.move_relative("x", 1)

  async def test_absolute_move_rejects_configured_out_of_range_target_before_io(self):
    driver = make_celigo()
    driver.config = CeligoHardwareConfig(
      x_axis=AxisConfig(min_position=1, max_position=3, mm_per_encoder_tick=0.5)
    )

    async def send(_command):
      self.fail("out-of-range move reached motor IO")

    stub(driver, _send_ez=send)
    with self.assertRaisesRegex(CeligoError, "outside configured range"):
      await driver.move("x", 7)

  async def test_bad_oem_checksum_is_retried(self):
    driver = make_celigo()
    driver._motor_wlen = True
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

    stub(driver, _transact=transact)
    self.assertEqual(await driver._motor_query("/1?8\r"), "/0`42")
    self.assertEqual(calls, 2)

  async def test_wlen_motor_comm_error_is_retried(self):
    driver = make_celigo()
    driver._motor_wlen = True
    responses = [
      struct.pack(">H", _EXT_MOTOR_COMM_ERROR),
      _motor_response(_oem_response(b"0`7")),
    ]

    async def transact(_opcode, _payload):
      return responses.pop(0)

    stub(driver, _transact=transact)
    self.assertEqual(await driver._motor_query("/1?8\r"), "/0`7")

  async def test_truncated_motor_response_is_rejected(self):
    driver = make_celigo()
    driver._motor_wlen = False

    async def transact(_opcode, _payload):
      return b"\x00"

    stub(driver, _transact=transact)
    with self.assertRaisesRegex(CeligoError, "Truncated motor query"):
      await driver._motor_query("/1?8\r")

  async def test_oversize_motor_command_is_rejected_before_io(self):
    driver = make_celigo()
    driver._motor_wlen = False

    async def transact(_opcode, _payload):
      self.fail("oversize command reached the transport")

    stub(driver, _transact=transact)
    with self.assertRaisesRegex(ValueError, "maximum is 512"):
      await driver._motor_query("x" * 513)


class TestResponseValidation(unittest.IsolatedAsyncioTestCase):
  async def test_controller_status_is_decoded_into_named_fields(self):
    driver = make_celigo()

    async def transact(_opcode):
      return struct.pack(">II", 0b1101, 42)

    stub(driver, _transact=transact)
    status = await driver.request_status()
    self.assertEqual(status.raw_flags, 0b1101)
    self.assertEqual(status.extended_status, 42)
    self.assertTrue(status.busy)
    self.assertFalse(status.error)
    self.assertTrue(status.interlock_open)
    self.assertTrue(status.controller_failed)
    self.assertTrue(status.has_safety_fault)

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
    driver._lock = asyncio.Lock()
    driver._seq = 0
    driver.reply_timeout = 0.1
    io = CorruptReplyIO()
    stub(driver, io=io)
    with self.assertRaisesRegex(CeligoError, "checksum failure"):
      await driver._transact(23)
    self.assertEqual(io.writes, 1)

  async def test_oversize_payload_is_rejected_before_body_read(self):
    driver = make_celigo()
    header = bytearray(12)
    header[1] = 23
    struct.pack_into(">i", header, 2, 7)
    struct.pack_into(">i", header, 6, _MAX_RESPONSE_PAYLOAD_BYTES + 1)
    header[10], header[11] = _fletcher16(header, 10)
    reads = 0

    async def read_exact(_count):
      nonlocal reads
      reads += 1
      return bytes(header)

    stub(driver, _read_exact=read_exact)
    with self.assertRaisesRegex(CeligoError, "maximum"):
      await driver._read_response(23, 7)
    self.assertEqual(reads, 1)

  async def test_truncated_counted_response_is_rejected(self):
    driver = make_celigo()

    async def transact(_opcode, _payload=b""):
      return struct.pack(">h", 2) + struct.pack(">h", 10)

    stub(driver, _transact=transact)
    with self.assertRaisesRegex(CeligoError, "Truncated autofocus positions"):
      await driver.request_autofocus_positions()

  async def test_galvo_busy_bytes_match_vendor_semantics(self):
    driver = make_celigo()
    driver.config = CeligoHardwareConfig(
      x_galvo=GalvoConfig(enabled=True), y_galvo=GalvoConfig(enabled=True)
    )

    async def transact(_opcode, _payload=b""):
      return struct.pack(">BBHH", 0, 1, 32768, 32768)

    stub(driver, _transact=transact)
    status = await driver.request_galvo_status()
    self.assertTrue(status.x_busy)
    self.assertFalse(status.y_busy)

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
    driver._lock = asyncio.Lock()
    driver._seq = 1
    io = ShortWriteIO()
    stub(driver, io=io)
    with self.assertRaisesRegex(CeligoError, "Short write"):
      await driver._transact(23)
    self.assertEqual((io.rx_purges, io.tx_purges), (1, 1))


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
    stub(celigo, io=io, camera=camera)
    celigo.calibration = CalibrationConfig(
      image_width_pixels=2048,
      image_height_pixels=2048,
    )
    celigo.baudrate = 230400
    celigo.latency_ms = 2
    celigo.device_info = None
    celigo.config = CeligoHardwareConfig()
    celigo._connected = False
    initialization_calls = 0

    async def no_op(*_args, **_kwargs):
      return None

    async def status():
      return ControllerStatus(0)

    async def identity():
      return DeviceInfo(1, (1, 3, 0), 256)

    async def initialize_hardware():
      nonlocal initialization_calls
      initialization_calls += 1

    stub(celigo, abort=no_op)
    stub(celigo, request_status=status)
    stub(celigo, request_device_info=identity)
    stub(celigo, initialize_hardware=initialize_hardware)
    stub(celigo, initialize_safe_outputs=no_op)
    await celigo.setup()
    self.assertEqual(initialization_calls, 1)
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
    stub(celigo, io=io, camera=None)
    celigo._connected = True
    operations = []

    async def abort():
      operations.append("abort")

    async def safe_outputs():
      operations.append("safe_outputs")

    stub(celigo, abort=abort)
    stub(celigo, initialize_safe_outputs=safe_outputs)
    await celigo.stop()
    self.assertEqual(operations, ["abort", "safe_outputs"])
    self.assertTrue(io.stopped)

  async def test_setup_closes_transport_when_identity_fails(self):
    celigo = make_celigo()
    io = _LifecycleIO()
    stub(celigo, io=io)
    celigo.baudrate = 230400
    celigo.latency_ms = 2
    celigo.device_info = None
    celigo.config = None
    stub(celigo, camera=None)

    async def status():
      return ControllerStatus(0)

    async def identity():
      raise CeligoError("simulated identity failure")

    async def abort():
      return None

    stub(celigo, request_status=status)
    stub(celigo, request_device_info=identity)
    stub(celigo, abort=abort)

    with self.assertRaisesRegex(CeligoError, "identity failure"):
      await celigo.setup()
    self.assertTrue(io.stopped)

  async def test_stop_closes_transport_when_camera_stop_fails(self):
    class FailingCamera:
      async def stop(self):
        raise RuntimeError("simulated camera failure")

    celigo = make_celigo()
    io = _LifecycleIO()
    stub(celigo, io=io, camera=FailingCamera())
    with self.assertRaisesRegex(RuntimeError, "camera failure"):
      await celigo.stop()
    self.assertTrue(io.stopped)


class TestFtdiTopologySelection(unittest.TestCase):
  def test_topology_resolves_exact_bus_and_device_address(self):
    device = _UsbDevice(bus=3, address=17, port_numbers=(2, 4))
    with (
      patch("pylabrobot.io.ftdi.HAS_PYLIBFTDI", True),
      patch("pylabrobot.io.ftdi.HAS_PYUSB", True),
    ):
      ftdi = FTDI(
        human_readable_device_name="Celigo",
        vid=0x0403,
        pid=0x6001,
        usb_address="3-2.4",
      )
    with patch("pylabrobot.io.ftdi.usb.core.find", return_value=[device]):
      self.assertEqual(ftdi._resolve_device_location(), (3, 17))


if __name__ == "__main__":
  unittest.main()
