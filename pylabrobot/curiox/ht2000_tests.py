import unittest
from typing import List
from unittest.mock import patch

from pylabrobot.curiox.ht2000 import (
  CurioxHT2000,
  HT2000Mode,
  HT2000Status,
  TrayPosition,
)


def ping_reply(mode: str = "0", status: str = "0", error: str = "00") -> bytes:
  """A 15-byte ping reply: mode at [8], status at [9], error code at [10:12]."""
  buf = bytearray(b"\x00" * 15)
  buf[8] = ord(mode)
  buf[9] = ord(status)
  buf[10:12] = error.encode("ascii")
  return bytes(buf)


def ack_reply(status: str = "0") -> bytes:
  """An 11-byte acknowledgement with the status digit at [7]."""
  buf = bytearray(b"\x00" * 11)
  buf[7] = ord(status)
  return bytes(buf)


def report_reply(
  mode: str = "0",
  status: str = "1",
  tray_out: bool = True,
  loaded: bool = True,
) -> bytes:
  """A 42-byte report: mode [8], status [9], tray [12], spill [13], load [14]."""
  buf = bytearray(b"\x00" * 42)
  buf[8] = ord(mode)
  buf[9] = ord(status)
  buf[12] = ord("1" if tray_out else "0")
  buf[14] = ord("1" if loaded else "0")
  return bytes(buf)


class FakeHT2000Serial:
  """In-memory serial stand-in that replays one scripted reply per write."""

  def __init__(self, replies: List[bytes]) -> None:
    self.port = "FAKE"
    self.written: List[bytes] = []
    self._replies = list(replies)
    self._rx = bytearray()

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def write(self, data: bytes) -> None:
    self.written.append(data)
    if self._replies:
      self._rx += self._replies.pop(0)

  async def read(self, num_bytes: int = 1) -> bytes:
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return out


def make_device(replies: List[bytes]) -> CurioxHT2000:
  fake = FakeHT2000Serial(replies)
  with patch("pylabrobot.curiox.ht2000.Serial", return_value=fake):
    device = CurioxHT2000(
      port="FAKE",
      wash_start_settle=0,
      prime_settle=0,
      mode_switch_settle=0,
      set_settle=0,
      poll_interval=0,
    )
  return device


class HT2000FrameTests(unittest.TestCase):
  def test_build_frame_ping(self):
    # "0": len 1, checksum = (0x30 + 1) = 0x0031.
    self.assertEqual(
      CurioxHT2000._build_frame("0"),
      bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x01, 0x30, 0x00, 0x31, 0xFF]),
    )

  def test_build_frame_wash(self):
    # "210": len 3, checksum = (0x32 + 0x31 + 0x30 + 3) = 0x0096.
    self.assertEqual(
      CurioxHT2000._build_frame("210"),
      bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x03, 0x32, 0x31, 0x30, 0x00, 0x96, 0xFF]),
    )


class HT2000ProtocolTests(unittest.IsolatedAsyncioTestCase):
  async def test_ping_ready(self):
    device = make_device([ping_reply(mode="0", status="0")])
    await device.io.setup()
    status, mode, error = await device.ping()
    self.assertEqual(status, HT2000Status.READY)
    self.assertEqual(mode, HT2000Mode.OPERATION)
    self.assertIsNone(error)

  async def test_ping_error_decodes_code(self):
    device = make_device([ping_reply(status="2", error="01")])
    await device.io.setup()
    status, _mode, error = await device.ping()
    self.assertEqual(status, HT2000Status.ERROR)
    self.assertEqual(error, "No plate.")

  async def test_wash_uploads_parameters_and_runs(self):
    device = make_device(
      [
        ping_reply(mode="0", status="0"),  # ping: operation mode
        ack_reply(),  # set parameters
        ack_reply(),  # standard wash
        report_reply(status="1", tray_out=True, loaded=True),  # running
        report_reply(status="0", tray_out=True, loaded=True),  # complete (ready)
      ]
    )
    await device.io.setup()
    await device.wash(wash_number=5, initial_volume=40, flow_rate=8, channel=2)

    # Second write is the parameter upload: "1" + wash + vol + flow + channel + trailer.
    param_frame = device.io.written[1]
    payload = param_frame[7:-3].decode("ascii")
    self.assertEqual(payload, "1" + "05" + "040" + "08" + "02" + "025060090000012")

  async def test_wash_waits_for_cycle_completion(self):
    device = make_device(
      [
        ping_reply(mode="0", status="0"),
        ack_reply(),  # set parameters
        ack_reply(),  # standard wash
        report_reply(status="1", tray_out=True, loaded=True),  # running
        report_reply(status="1", tray_out=True, loaded=True),  # still running
        report_reply(status="0", tray_out=True, loaded=True),  # complete
      ]
    )
    await device.io.setup()
    await device.wash()
    # ping + set + wash + three enquire polls.
    self.assertEqual(len(device.io.written), 6)

  async def test_wait_for_wash_raises_on_missing_plate(self):
    device = make_device(
      [
        ping_reply(mode="0", status="0"),
        ack_reply(),
        ack_reply(),
        report_reply(status="1", tray_out=True, loaded=False),  # no plate
      ]
    )
    await device.io.setup()
    with self.assertRaises(Exception):
      await device.wash()

  async def test_enquire_report_decodes_tray(self):
    device = make_device([report_reply(status="1", tray_out=False, loaded=True)])
    await device.io.setup()
    report = await device.enquire_report()
    self.assertEqual(report.tray_position, TrayPosition.IN)
    self.assertTrue(report.tray_loaded)

  def test_wash_parameter_validation(self):
    device = make_device([])
    for kwargs in (
      {"wash_number": 0},
      {"wash_number": 20},
      {"initial_volume": 100},
      {"flow_rate": 1},
      {"channel": 6},
      {"channel": 10},
    ):
      with self.assertRaises(ValueError):
        import asyncio

        asyncio.run(
          device._set_wash_parameters(
            **{"wash_number": 3, "initial_volume": 50, "flow_rate": 5, "channel": 0, **kwargs}
          )
        )


if __name__ == "__main__":
  unittest.main()
