import unittest
from unittest import mock

from pylabrobot.centrifuge.v11_vspin_backend import (
  DEFAULT_BUCKET_1_REMAINDER,
  V11VSpinBackend,
  _build_vspin_deceleration_command,
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


def _make_backend(io: _FakeIO) -> V11VSpinBackend:
  backend = object.__new__(V11VSpinBackend)
  backend.io = io
  backend._command_lock = None
  backend._last_position = 0
  backend._last_home_position = 0
  backend._bucket_1_remainder = DEFAULT_BUCKET_1_REMAINDER
  backend._motion_is_prepared = False
  backend._stop_requested = False
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

  async def test_default_bucket_positions_follow_home_offset(self):
    backend = _make_backend(_FakeIO([]))

    async def get_home_position() -> int:
      return 6733

    async def get_position() -> int:
      return 7000

    backend.get_home_position = get_home_position
    backend.get_position = get_position

    self.assertEqual(await backend._get_bucket_position(1), 12070)
    self.assertEqual(await backend._get_bucket_position(2), 16070)

  async def test_bucket_2_position_does_not_skip_when_bucket_1_is_behind_current_position(self):
    backend = _make_backend(_FakeIO([]))

    async def get_home_position() -> int:
      return 6733

    async def get_position() -> int:
      return 14000

    backend.get_home_position = get_home_position
    backend.get_position = get_position

    self.assertEqual(await backend._get_bucket_position(2), 16070)

  def test_deceleration_command_uses_observed_checksum(self):
    self.assertEqual(
      _build_vspin_deceleration_command(0.8),
      bytes.fromhex("aa0194b600000000dc02000029"),
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
