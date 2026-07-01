import unittest
from unittest import mock

from pylabrobot.centrifuge.vspin_backend import VSpinBackend, _with_vspin_checksum


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


def _make_backend(io: _FakeIO) -> VSpinBackend:
  backend = object.__new__(VSpinBackend)
  backend.io = io
  backend._command_set = "agilent"
  backend._bucket_1_remainder = None
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


class VSpinCommandSetTests(unittest.IsolatedAsyncioTestCase):
  def test_default_command_set_is_agilent(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      backend = VSpinBackend()

    self.assertEqual(backend._command_set, "agilent")
    self.assertEqual(backend._command("open_door"), bytes.fromhex("aa022600062e"))
    self.assertEqual(backend._command("lock_bucket"), bytes.fromhex("aa022600072f"))

  def test_old_firmware_command_set_uses_legacy_pneumatic_commands(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      backend = VSpinBackend(command_set="old_firmware")

    self.assertEqual(backend._command_set, "old_firmware")
    self.assertEqual(backend._command("open_door"), bytes.fromhex("aa022600072f"))
    self.assertEqual(backend._command("close_door"), bytes.fromhex("aa022600052d"))
    self.assertEqual(backend._command("lock_bucket"), bytes.fromhex("aa0226000129"))
    self.assertEqual(backend._command("unlock_bucket"), bytes.fromhex("aa0226200048"))

  def test_velocity11_label_is_not_a_command_set(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      with self.assertRaisesRegex(ValueError, "command_set"):
        VSpinBackend(command_set="velocity11")

  def test_unknown_command_set_raises(self):
    with mock.patch("pylabrobot.centrifuge.vspin_backend.FTDI"):
      with self.assertRaisesRegex(ValueError, "command_set"):
        VSpinBackend(command_set="velocity11-label")

  def test_with_vspin_checksum_repairs_final_byte(self):
    self.assertEqual(
      _with_vspin_checksum(bytes.fromhex("aa020e00")),
      bytes.fromhex("aa020e10"),
    )

  async def test_send_command_repairs_checksum_before_write(self):
    io = _FakeIO([b"\r"])
    backend = _make_backend(io)

    await backend._send_command(bytes.fromhex("aa020e00"))

    self.assertEqual(io.writes, [bytes.fromhex("aa020e10")])

  def test_find_status_packet_scans_noise_and_validates_checksum(self):
    packet = _status_packet()

    parsed = VSpinBackend._find_status_packet(b"\x00\xff" + packet + b"\x00")

    assert parsed is not None
    self.assertEqual(parsed.status, 0x11)
    self.assertEqual(parsed.current_position, 12070)
    self.assertEqual(parsed.tachometer, -10)
    self.assertEqual(parsed.home_position, 6733)

  def test_find_status_packet_rejects_bad_checksum(self):
    packet = bytearray(_status_packet())
    packet[-1] ^= 0xFF

    self.assertIsNone(VSpinBackend._find_status_packet(bytes(packet)))
