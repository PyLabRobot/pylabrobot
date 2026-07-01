import unittest
from unittest import mock

from pylabrobot.centrifuge.v11_vspin_backend import (
  DEFAULT_BUCKET_1_REMAINDER,
  POSITION_SETTLE_TOLERANCE,
  POSITION_TOLERANCE,
  V11VSpinBackend,
  _IDLE_VSPIN_STATUSES,
  _build_vspin_deceleration_command,
  _normalize_vspin_home_position,
  _vspin_position_matches_target,
)
from pylabrobot.centrifuge.vspin_backend import VSpinBackend, create_vspin_backend


class _FakeIO:
  def __init__(self, read_chunks):
    self.read_chunks = list(read_chunks)
    self.writes = []

  async def read(self, num_bytes: int) -> bytes:
    if self.read_chunks:
      return self.read_chunks.pop(0)
    return b""

  async def write(self, data: bytes) -> int:
    self.writes.append(data)
    return len(data)


class _RecordingConfigIO:
  def __init__(self):
    self.calls = []

  async def set_latency_timer(self, latency: int):
    self.calls.append(("set_latency_timer", latency))

  async def set_line_property(self, bits: int, stopbits: int, parity: int):
    self.calls.append(("set_line_property", bits, stopbits, parity))

  async def set_flowctrl(self, flowctrl: int):
    self.calls.append(("set_flowctrl", flowctrl))

  async def set_rts(self, level: bool):
    self.calls.append(("set_rts", level))

  async def set_dtr(self, level: bool):
    self.calls.append(("set_dtr", level))

  async def set_baudrate(self, baudrate: int):
    self.calls.append(("set_baudrate", baudrate))


def _make_backend(io: _FakeIO) -> V11VSpinBackend:
  backend = object.__new__(V11VSpinBackend)
  backend.io = io
  backend._command_lock = None
  backend._last_position = 0
  backend._last_home_position = 0
  backend._home_sensor_position = None
  backend._bucket_1_remainder = DEFAULT_BUCKET_1_REMAINDER
  backend._motion_is_prepared = False
  backend._stop_requested = False
  backend._last_command_at = 0.0
  return backend


def _status_packet(
  status: int = 0x11,
  current_position: int = 12070,
  tachometer: int = -10,
  home_position: int = 6733,
) -> bytes:
  packet = (
    bytes([status])
    + current_position.to_bytes(4, "little")
    + b"\x4f"
    + tachometer.to_bytes(2, "little", signed=True)
    + b"\x18"
    + home_position.to_bytes(4, "little")
  )
  return packet + bytes([sum(packet) & 0xFF])


class V11VSpinBackendTests(unittest.IsolatedAsyncioTestCase):
  async def test_read_resp_returns_expected_binary_packet_without_cr(self):
    backend = _make_backend(_FakeIO([b"\x00\x30", b"\x08\x30\x68"]))

    resp = await backend._read_resp(timeout=1.0, expected_len=5)

    self.assertEqual(resp, bytes.fromhex("0030083068"))

  async def test_send_command_repairs_checksum_and_uses_expected_length(self):
    io = _FakeIO([bytes.fromhex("0030083068")])
    backend = _make_backend(io)

    resp = await backend._send_command(bytes.fromhex("aa020e00"))

    self.assertEqual(resp, bytes.fromhex("0030083068"))
    self.assertEqual(io.writes, [bytes.fromhex("aa020e10")])

  def test_find_status_packet_scans_noise_and_validates_checksum(self):
    packet = _status_packet()

    parsed = V11VSpinBackend._find_status_packet(b"\x00\xff" + packet + b"\x00")

    assert parsed is not None
    self.assertEqual(parsed.status, 0x11)
    self.assertEqual(parsed.current_position, 12070)
    self.assertEqual(parsed.tachometer, -10)
    self.assertEqual(parsed.home_position, 6733)

  def test_find_status_packet_rejects_bad_checksum(self):
    packet = bytearray(_status_packet())
    packet[-1] ^= 0xFF

    self.assertIsNone(V11VSpinBackend._find_status_packet(bytes(packet)))

  def test_find_short_status_from_io_packet(self):
    self.assertEqual(V11VSpinBackend._find_short_status(bytes.fromhex("0030083068")), 0x08)

  def test_status_0x89_is_idle_when_stopped_at_target(self):
    self.assertIn(0x89, _IDLE_VSPIN_STATUSES)

  def test_home_position_is_normalized_to_one_rotation(self):
    self.assertEqual(_normalize_vspin_home_position(839825), 7825)

  def test_position_settle_tolerance_allows_mechanical_stop_variance(self):
    self.assertGreaterEqual(POSITION_SETTLE_TOLERANCE, 29)
    self.assertGreater(POSITION_SETTLE_TOLERANCE, POSITION_TOLERANCE)

  def test_position_target_matching_handles_rotation_wrap(self):
    self.assertTrue(
      _vspin_position_matches_target(
        position=5395,
        target=13258,
        tolerance=POSITION_SETTLE_TOLERANCE,
      )
    )

  async def test_wait_for_full_status_can_accept_zero_home_fallback(self):
    backend = _make_backend(
      _FakeIO(
        [
          _status_packet(
            status=0x89,
            current_position=5548,
            tachometer=-1,
            home_position=0,
          )
        ]
      )
    )

    status = await backend._wait_for_full_status(timeout=0.01, allow_zero_home_fallback=True)

    self.assertEqual(status.home_position, 0)
    self.assertEqual(status.current_position, 5548)

  async def test_status_poll_does_not_overwrite_home_sensor_reference(self):
    backend = _make_backend(
      _FakeIO(
        [
          _status_packet(
            status=0x09,
            current_position=8025,
            tachometer=0,
            home_position=5312,
          )
        ]
      )
    )
    backend._home_sensor_position = 2710

    await backend._get_positions_and_tachometer()

    self.assertEqual(backend._home_sensor_position, 2710)

  async def test_default_bucket_positions_follow_home_offset(self):
    backend = _make_backend(_FakeIO([]))
    backend._home_sensor_position = 6733

    async def get_home_position() -> int:
      return 9999

    async def get_position() -> int:
      return 7000

    backend.get_home_position = get_home_position
    backend.get_position = get_position

    self.assertEqual(await backend._get_bucket_position(1), 12070)
    self.assertEqual(await backend._get_bucket_position(2), 16070)

  async def test_bucket_2_position_does_not_skip_when_bucket_1_is_behind_current_position(self):
    backend = _make_backend(_FakeIO([]))
    backend._home_sensor_position = 6733

    async def get_home_position() -> int:
      return 9999

    async def get_position() -> int:
      return 14000

    backend.get_home_position = get_home_position
    backend.get_position = get_position

    self.assertEqual(await backend._get_bucket_position(2), 16070)

  async def test_bucket_positions_use_homed_reference_when_live_home_changes(self):
    backend = _make_backend(_FakeIO([]))
    backend._home_sensor_position = 7936

    async def get_home_position() -> int:
      return 12115

    async def get_position() -> int:
      return 13287

    backend.get_home_position = get_home_position
    backend.get_position = get_position

    self.assertEqual(await backend._get_bucket_position(2), 17273)

  def test_deceleration_command_uses_observed_checksum(self):
    self.assertEqual(
      _build_vspin_deceleration_command(0.8),
      bytes.fromhex("aa0194b600000000dc02000029"),
    )

  def test_rpm_to_g_roundtrips_1500_rpm(self):
    g = V11VSpinBackend.rpm_to_g(1500)

    self.assertEqual(V11VSpinBackend.g_to_rpm(g), 1500)

  async def test_spin_uses_rpm_command_path(self):
    backend = object.__new__(V11VSpinBackend)
    backend.spin_rpm = mock.AsyncMock()

    await V11VSpinBackend.spin(
      backend,
      g=V11VSpinBackend.rpm_to_g(1500),
      duration=10,
      acceleration=0.7,
      deceleration=0.6,
    )

    backend.spin_rpm.assert_awaited_once_with(
      rpm=1500,
      duration=10,
      acceleration=0.7,
      deceleration=0.6,
    )

  async def test_spin_rpm_validates_target_rpm(self):
    backend = object.__new__(V11VSpinBackend)

    with self.assertRaises(ValueError):
      await V11VSpinBackend.spin_rpm(backend, rpm=0, duration=10)

  async def test_setup_uses_cold_startup_without_runtime_probe_by_default(self):
    backend = object.__new__(V11VSpinBackend)
    backend.io = mock.Mock()
    backend.io.setup = mock.AsyncMock()
    backend.io.stop = mock.AsyncMock()
    backend.io.usb_purge_rx_buffer = mock.AsyncMock()
    backend.io.usb_purge_tx_buffer = mock.AsyncMock()
    backend.io.set_dtr = mock.AsyncMock()
    backend.io.set_rts = mock.AsyncMock()
    backend._bucket_1_remainder = 123
    backend._try_runtime_attach_after_startup_failure = False
    events = []

    async def configure_and_initialize():
      events.append("cold-start")

    async def startup_handshake():
      events.append("startup")

    async def enable_telemetry_and_pneumatics():
      events.append("telemetry")

    async def home_rotor():
      events.append("home")

    backend._try_attach_to_runtime_controller = mock.AsyncMock()
    backend.configure_and_initialize = configure_and_initialize
    backend._startup_handshake = startup_handshake
    backend._enable_telemetry_and_pneumatics = enable_telemetry_and_pneumatics
    backend._home_rotor = home_rotor

    await V11VSpinBackend.setup(backend)

    self.assertEqual(events, ["cold-start", "startup", "telemetry", "home"])
    backend._try_attach_to_runtime_controller.assert_not_awaited()

  async def test_setup_closes_io_with_clear_error_when_controller_never_responds(self):
    backend = object.__new__(V11VSpinBackend)
    backend.io = mock.Mock()
    backend.io.setup = mock.AsyncMock()
    backend.io.stop = mock.AsyncMock()
    backend.io.usb_purge_rx_buffer = mock.AsyncMock()
    backend.io.usb_purge_tx_buffer = mock.AsyncMock()
    backend.io.set_dtr = mock.AsyncMock()
    backend.io.set_rts = mock.AsyncMock()
    backend._bucket_1_remainder = 123
    backend._try_runtime_attach_after_startup_failure = False

    backend._try_attach_to_runtime_controller = mock.AsyncMock(return_value=False)
    backend.configure_and_initialize = mock.AsyncMock()
    backend._startup_handshake = mock.AsyncMock(side_effect=TimeoutError("empty"))
    backend._enable_telemetry_and_pneumatics = mock.AsyncMock()
    backend._home_rotor = mock.AsyncMock()

    with self.assertRaisesRegex(TimeoutError, "Power-cycle or restart"):
      await V11VSpinBackend.setup(backend)

    backend._try_attach_to_runtime_controller.assert_not_awaited()
    backend.io.stop.assert_awaited_once()

  async def test_setup_can_optionally_recover_with_runtime_attach_after_startup_failure(self):
    backend = object.__new__(V11VSpinBackend)
    backend.io = mock.Mock()
    backend.io.setup = mock.AsyncMock()
    backend.io.stop = mock.AsyncMock()
    backend._bucket_1_remainder = 123
    backend._try_runtime_attach_after_startup_failure = True
    events = []

    async def configure_and_initialize():
      events.append("cold-start")

    async def startup_handshake():
      events.append("startup")
      raise TimeoutError("empty")

    attach_results = [False, True]

    async def try_attach():
      events.append("attach")
      return attach_results.pop(0)
    async def home_rotor():
      events.append("home")

    backend.configure_and_initialize = configure_and_initialize
    backend._startup_handshake = startup_handshake
    backend._enable_telemetry_and_pneumatics = mock.AsyncMock()
    backend._try_attach_to_runtime_controller = try_attach
    backend._home_rotor = home_rotor

    await V11VSpinBackend.setup(backend)

    self.assertEqual(events, ["attach", "cold-start", "startup", "attach", "home"])

  async def test_setup_tries_runtime_attach_before_cold_start_when_enabled(self):
    backend = object.__new__(V11VSpinBackend)
    backend.io = mock.Mock()
    backend.io.setup = mock.AsyncMock()
    backend.io.get_serial = mock.AsyncMock(return_value="TEST")
    backend._bucket_1_remainder = 123
    backend._try_runtime_attach_after_startup_failure = True
    backend._command_lock = None
    backend._motion_is_prepared = False
    backend._try_attach_to_runtime_controller = mock.AsyncMock(return_value=True)
    backend.configure_and_initialize = mock.AsyncMock()
    backend._startup_handshake = mock.AsyncMock()
    backend._enable_telemetry_and_pneumatics = mock.AsyncMock()
    backend._home_rotor = mock.AsyncMock()

    await V11VSpinBackend.setup(backend)

    backend._try_attach_to_runtime_controller.assert_awaited_once()
    backend.configure_and_initialize.assert_not_awaited()
    backend._startup_handshake.assert_not_awaited()
    backend._enable_telemetry_and_pneumatics.assert_not_awaited()
    backend._home_rotor.assert_awaited_once()

  async def test_runtime_attach_tries_multiple_status_commands(self):
    backend = _make_backend(_FakeIO([]))
    backend.io.set_baudrate = mock.AsyncMock()
    backend.io.set_rts = mock.AsyncMock()
    backend.io.set_dtr = mock.AsyncMock()
    backend._purge_io_buffers = mock.AsyncMock()
    backend._drain_startup_silence = mock.AsyncMock()
    commands = []

    async def send_command(cmd: bytes, read_timeout: float, expected_len: int):
      commands.append(cmd)
      if cmd == bytes.fromhex("aa01121f32"):
        return _status_packet()
      return b""

    backend._send_command = send_command

    attached = await backend._try_attach_to_runtime_controller()

    self.assertTrue(attached)
    self.assertEqual(
      commands,
      [
        bytes.fromhex("aa010e0f"),
        bytes.fromhex("aa01121f32"),
      ],
    )
    self.assertEqual(backend._last_position, 12070)
    self.assertEqual(backend._last_home_position, 6733)

  async def test_runtime_attach_rejects_blank_zero_status(self):
    backend = _make_backend(_FakeIO([]))
    backend.io.set_baudrate = mock.AsyncMock()
    backend.io.set_rts = mock.AsyncMock()
    backend.io.set_dtr = mock.AsyncMock()
    backend._purge_io_buffers = mock.AsyncMock()
    backend._drain_startup_silence = mock.AsyncMock()

    async def send_command(cmd: bytes, read_timeout: float, expected_len: int):
      return _status_packet(status=0x09, current_position=0, tachometer=0, home_position=0)

    backend._send_command = send_command

    self.assertFalse(await backend._try_attach_to_runtime_controller())
    self.assertEqual(backend._last_position, 0)
    self.assertEqual(backend._last_home_position, 0)

  async def test_wait_for_idle_uses_full_status_when_short_idle_has_no_position(self):
    full_status = _status_packet(
      status=0x09,
      current_position=8006,
      tachometer=0,
      home_position=7924,
    )
    backend = _make_backend(_FakeIO([bytes.fromhex("0909"), full_status]))
    reference = backend._make_status(0x09, 0, home_position=0)

    status = await backend._wait_for_idle(
      label="homing",
      timeout=1.0,
      require_activity_from=reference,
      activity_tolerance=POSITION_SETTLE_TOLERANCE,
    )

    self.assertEqual(status.current_position, 8006)
    self.assertEqual(status.home_position, 7924)
    self.assertEqual(
      backend.io.writes,
      [bytes.fromhex("aa010e0f"), bytes.fromhex("aa01121f32")],
    )

  async def test_wait_for_idle_can_reject_stale_idle_before_homing_activity(self):
    stale_status = _status_packet(
      status=0x09,
      current_position=1000,
      tachometer=0,
      home_position=250,
    )
    backend = _make_backend(_FakeIO([stale_status]))
    backend._last_position = 1000
    backend._last_home_position = 250
    reference = backend._make_status(0x09, 1000, home_position=250)

    with self.assertRaisesRegex(TimeoutError, "homing"):
      await backend._wait_for_idle(
        label="homing",
        timeout=0.01,
        require_activity_from=reference,
        activity_tolerance=POSITION_SETTLE_TOLERANCE,
      )

  async def test_home_rotor_requires_nonzero_home_position_after_idle(self):
    backend = _make_backend(_FakeIO([]))
    backend._get_positions_and_tachometer = mock.AsyncMock(
      return_value=backend._make_status(0x09, 1000, home_position=0),
    )
    backend._motor_enable = mock.AsyncMock()
    backend._send_safe = mock.AsyncMock()
    backend._wait_for_idle = mock.AsyncMock(
      return_value=backend._make_status(0x09, 1000, home_position=0),
    )
    backend._wait_for_full_status = mock.AsyncMock(
      return_value=backend._make_status(0x09, 1000, home_position=7859),
    )

    await backend._home_rotor()

    backend._wait_for_full_status.assert_awaited_once_with(timeout=15.0)
    self.assertEqual(backend._home_sensor_position, 7859)

  async def test_speed_wait_requires_measured_rpm_not_position_only(self):
    backend = _make_backend(
      _FakeIO([
        _status_packet(status=0x09, current_position=5000, tachometer=0, home_position=100),
      ])
    )

    with self.assertRaisesRegex(TimeoutError, "target speed"):
      await backend._wait_for_speed_or_motion(rpm=1000, final_position=2000, timeout=0.01)

  async def test_speed_wait_accepts_measured_target_rpm(self):
    tachometer = int(-(1000 * 0.95) / 14.69320388)
    backend = _make_backend(
      _FakeIO([
        _status_packet(status=0x08, current_position=1000, tachometer=tachometer, home_position=100),
      ])
    )

    await backend._wait_for_speed_or_motion(rpm=1000, final_position=2000, timeout=0.5)

  async def test_prepare_spin_motion_waits_for_spin_ready_io_state(self):
    backend = _make_backend(_FakeIO([]))
    commands = []
    statuses = [
      bytes.fromhex("0088090091"),
      bytes.fromhex("0008080010"),
    ]

    async def send_safe(cmd: bytes, **kwargs):
      del kwargs
      commands.append(cmd)
      return b""

    async def get_status() -> bytes:
      commands.append(bytes.fromhex("aa020e10"))
      return statuses.pop(0)

    backend._send_safe = send_safe
    backend._get_status = get_status

    await backend._prepare_spin_motion()

    self.assertEqual(
      commands,
      [
        bytes.fromhex("aa0226000129"),
        bytes.fromhex("aa020e10"),
        bytes.fromhex("aa0226000028"),
        bytes.fromhex("aa020e10"),
      ],
    )

  async def test_prepare_spin_motion_rejects_locked_bucket_before_spin(self):
    backend = _make_backend(_FakeIO([]))

    async def send_safe(cmd: bytes, **kwargs):
      del cmd, kwargs
      return b""

    async def get_status() -> bytes:
      return bytes.fromhex("0088090091")

    backend._send_safe = send_safe
    backend._get_status = get_status

    with self.assertRaisesRegex(TimeoutError, "spin ready"):
      await backend._prepare_spin_motion()

  async def test_spin_rpm_sends_spin_command_immediately_after_spin_profile(self):
    backend = _make_backend(_FakeIO([]))
    events = []

    async def get_door_open() -> bool:
      return False

    async def get_door_locked() -> bool:
      return True

    async def get_bucket_locked() -> bool:
      return False

    async def get_position() -> int:
      events.append("get_position")
      return 12074

    async def send_safe(cmd: bytes, **kwargs):
      del kwargs
      events.append(cmd.hex())
      return _status_packet(status=0x09, current_position=12074, tachometer=0)

    backend.get_door_open = get_door_open
    backend.get_door_locked = get_door_locked
    backend.get_bucket_locked = get_bucket_locked
    backend.get_position = get_position
    backend._prepare_spin_motion = mock.AsyncMock()
    backend._motor_enable = mock.AsyncMock()
    backend._send_safe = send_safe
    backend._wait_for_speed_or_motion = mock.AsyncMock()
    backend._hold_spin = mock.AsyncMock()
    backend._wait_for_idle = mock.AsyncMock()
    backend._home_rotor = mock.AsyncMock()
    backend.lock_door = mock.AsyncMock(side_effect=AssertionError("unexpected lock door"))
    backend.unlock_bucket = mock.AsyncMock(side_effect=AssertionError("unexpected unlock bucket"))

    await backend.spin_rpm(rpm=1500, duration=10)

    profile_index = events.index("aa01e60500640000000000fd00803e01000c")
    self.assertEqual(events[profile_index + 1][:8], "aa01d497")
    self.assertLess(events.index("get_position"), profile_index)

  async def test_configuration_reasserts_control_lines_before_startup_baud(self):
    io = _RecordingConfigIO()
    backend = _make_backend(io)  # type: ignore[arg-type]

    await backend.set_configuration_data()

    self.assertEqual(
      io.calls,
      [
        ("set_latency_timer", 16),
        ("set_line_property", 8, 1, 0),
        ("set_flowctrl", 0),
        ("set_rts", True),
        ("set_dtr", True),
        ("set_baudrate", 19200),
      ],
    )


class VSpinBackendSelectionTests(unittest.TestCase):
  def test_factory_defaults_to_agilent_backend(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      backend = create_vspin_backend()

    self.assertIsInstance(backend, VSpinBackend)

  def test_factory_can_select_legacy_v11_backend(self):
    with mock.patch("pylabrobot.centrifuge.v11_vspin_backend.FTDI"):
      backend = create_vspin_backend(variant="v11")

    self.assertIsInstance(backend, V11VSpinBackend)
