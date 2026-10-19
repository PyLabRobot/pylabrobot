"""Driver tests over a fake io. Replies are frames recorded from a CLARIOstar Plus over FTDI."""

import gzip
import json
import os
import unittest
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.bmg_labtech.clariostar.driver import master as master_module
from pylabrobot.bmg_labtech.clariostar.driver.errors import (
  CommandRefusedError,
  FrameError,
  parse_quit_codes,
)
from pylabrobot.bmg_labtech.clariostar.driver.framing import (
  ftdi_extract_payload,
)
from pylabrobot.bmg_labtech.clariostar.driver.master import CLARIOstarDriver
from pylabrobot.io.ftdi import FTDI
from pylabrobot.io.usb import USB

# Frames this driver exchanged with a CLARIOstar Plus (FTDI, firmware 1.35) on its first runs.
STATUS_QUERY_FRAME = bytes.fromhex("0200090c800000970d")
STATUS_IDLE = bytes.fromhex("0200180c010506240000000000000000000000c00001160d")
STATUS_BUSY = bytes.fromhex("0200180c012506240000030000000000000000c00001390d")
STATUS_DRAWER_OPEN = bytes.fromhex("0200180c010506210000000000000000000000c00001130d")
STATUS_PLATE_DETECTED = bytes.fromhex("0200180c010500260000000000000000000000c00001120d")
STATUS_TEMPERATURES = bytes.fromhex("0200180c010500240000000000000000e700f0c00002e70d")
# 18 of 24 bytes, as received on the second run ("Size field says 24 bytes, got 18").
STATUS_TRUNCATED = bytes.fromhex("0200180c010500210000000000000000000d")
FIRMWARE_INFO_FRAME = bytes.fromhex("02000f0c0509000000000000002b0d")
FIRMWARE_INFO_REPLY = bytes.fromhex(
  "0200270c0a050624000005464e6f7620323020323032300031313a35313a32310000010004f20d"
)
REFUSED_TEMPERATURE = bytes.fromhex("0200180c0115002400006e0000000000e700f0c00003650d")
INIT_FRAME = bytes.fromhex("02000d0c010000100300002f0d")
# 476 bytes, 0x0D inside the payload. The board serials in records 09, 1f and 2e are zeroed,
# and the checksum recomputed.
INIT_REPLY = bytes.fromhex(
  "0201dc0c1325062400000100060546010302020900090d094c6001000000000d000220000e00032700000f000300"
  "000011000300000012000200001f00090100ec0000043a00212e00090104600003000000002f017845585f53503a"
  "313d6f70656e2c323d46696c74657220312c333d46696c74657220320d0a45585f4c503a313d6f70656e2c323d46"
  "696c74657220312c333d46696c74657220320d0a4253503a313d6f70656e2c323d46696c74657220312c333d4669"
  "6c74657220322c343d46696c74657220330d0a454d5f53503a313d6f70656e2c323d46696c74657220312c333d46"
  "696c74657220320d0a454d5f4c503a313d6f70656e2c323d46696c74657220312c333d46696c74657220320d0a45"
  "4d5f534c49543a313d6f70656e2c323d536c697420302e382c333d536c697420312e322c343d536c697420322e30"
  "2c353d536c697420332e302c373d4650207061722c383d4650207065722c363d4c554d206c656e73650d0a45585f"
  "534c49543a313d6f70656e2c323d536c697420302e362c333d536c697420312e302c343d536c697420312e362c35"
  "3d536c697420322e332c383d46502053746472642c373d46502055562c393d4142532c363d462d436865636b0d0a"
  "0001310007000000000000000071710d"
)

# Frames the vendor software exchanged with the same reader.
# Idle at power-on, motors off.
STATUS_MOTORS_OFF = bytes.fromhex("0200180c010500040000000000000000000000c00000f00d")
# Stored parameters; the project and serial number, reply bytes 161-164, are zeroed and the checksum
# recomputed.
PARAMETERS_FRAME = bytes.fromhex("02000f0c050700000000000000290d")
PARAMETERS_REPLY = bytes.fromhex(
  "02010f0c070500240000000100000a0101010100000100ee0200000f001904000000000000030400000100000102"
  "0000000000000000000032000000000000000000000000000000000000000074006f0000000000000065000000dc"
  "050000000000000000f4010803a70408076009da08ac0d0000000000000000000000000000000000000000000000"
  "000100000001010000000000000001010000000000000012029806000000004605ee01fbff700c00000000a40058"
  "ff8e03f20460ff5511fe0b55118f1a170298065aff970668042603bc14b804080791009001463228460a0046071e"
  "0000000000000000002103d40628002c01900146001e00001411001209ac0d600900000000001e680d"
)
# Commands it had refused, as (sent, received), one per quit code.
REFUSED = {
  "refused_busy": (
    bytes.fromhex("02000a0c08000000200d"),
    bytes.fromhex("0200180c013500040000ca0000000000000000e000020a0d"),
  ),
  "refused_no_results": (
    bytes.fromhex("02000f0c0502ffffffff000004200d"),
    bytes.fromhex("0200180c011507260000d50000000000000000c00001fe0d"),
  ),
  "refused_no_active_run": (
    bytes.fromhex("02000a0c0b000000230d"),
    bytes.fromhex("0200180c011500040000d60000000000000000e00001f60d"),
  ),
  "refused_cycle_time": (
    bytes.fromhex(
      "0200900c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff0000000000000000000000000000"
      "000000000000000000000000000000000000000000000a0200000000000000000000000000000000000000000000"
      "0000000000000000270f270f1901177000000064232826ca000000640000000508020001003b0100000003000500"
      "3c0000139e0d"
    ),
    bytes.fromhex("0200180c011504060000410100000000000000c00001480d"),
  ),
}


def load_session_replies() -> Dict[str, Any]:
  """What this driver sent and the reader answered: frames as hex, and a spectrum page's first
  data request read by read."""
  path = os.path.join(os.path.dirname(__file__), "features", "test_data", "session_replies.json.gz")
  with gzip.open(path, "rt") as f:
    return dict(json.load(f))


# Replies an earlier driver received from the same reader.
STATUS_NOT_INITIALIZED = bytes.fromhex("0200180c011500000000c90000000000000000c00001c50d")
STATUS_WARNING = bytes.fromhex("0200180c01a504260000fa0500000000000000c00002b50d")


class FakeFTDI(FTDI):
  """Records writes and answers each write with the next queued frame, in reads of ``chunk``.

  Subclasses ``FTDI`` so the driver takes it as one, without opening a device.
  """

  # No super().__init__(): FTDI.__init__ checks for the FTDI libraries.
  def __init__(self, chunk: int = 25) -> None:
    self.written: List[bytes] = []
    self.replies: List[bytes] = []
    self.purges = 0
    self._pending = b""
    self._chunk = chunk

  async def setup(self) -> None: ...

  def serialize(self) -> dict:
    return {"vid": 0x0403, "pid": 0xBB68, "device_id": None}

  async def set_baudrate(self, baudrate: int) -> None: ...

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None: ...

  async def set_latency_timer(self, latency: int) -> None: ...

  async def stop(self) -> None: ...

  async def usb_purge_rx_buffer(self) -> None:
    self.purges += 1
    self._pending = b""

  async def write(self, data: bytes) -> int:
    self.written.append(bytes(data))
    if self.replies:
      self._pending += self.replies.pop(0)
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    size = min(num_bytes, self._chunk)
    data, self._pending = self._pending[:size], self._pending[size:]
    return data


def parse_status(frame: bytes):
  return CLARIOstarDriver._parse_status(ftdi_extract_payload(frame))


def make_driver(io: FakeFTDI) -> CLARIOstarDriver:
  driver = CLARIOstarDriver(io=io)
  driver._PACKET_READ_TIMEOUT = 0.01
  driver._FRAME_STALL_TIMEOUT = 0.05
  driver._RETRY_SETTLE = 0
  driver.default_poll_interval = 0
  driver.drawer.default_poll_interval = 0
  return driver


# The driver's asyncio.Lock needs an event loop on Python 3.9: build drivers inside one.
class UseIOTests(unittest.IsolatedAsyncioTestCase):
  def test_io_type_sets_use_io(self):
    self.assertEqual(CLARIOstarDriver(io=FakeFTDI()).use_io, "ftdi")
    self.assertEqual(CLARIOstarDriver(io=MagicMock(spec=USB)).use_io, "usb")
    self.assertEqual(CLARIOstarDriver(use_io="usb", io=MagicMock(spec=USB)).use_io, "usb")

  def test_mismatched_use_io_and_io_rejected(self):
    with self.assertRaises(ValueError):
      CLARIOstarDriver(use_io="usb", io=FakeFTDI())
    with self.assertRaises(ValueError):
      CLARIOstarDriver(use_io="ftdi", io=MagicMock(spec=USB))

  def test_other_io_rejected(self):
    with self.assertRaises(TypeError):
      CLARIOstarDriver(io=MagicMock())  # type: ignore[arg-type]

  def test_unknown_use_io_rejected(self):
    with self.assertRaises(ValueError):
      CLARIOstarDriver(use_io="serial", io=FakeFTDI())  # type: ignore[arg-type]


class StatusTests(unittest.TestCase):
  def test_flags_from_real_replies(self):
    busy = parse_status(STATUS_BUSY)
    self.assertTrue(busy.busy)
    self.assertFalse(busy.drawer_open)

    idle = parse_status(STATUS_IDLE)
    self.assertFalse(idle.busy)
    self.assertTrue(idle.motors_enabled)

    self.assertTrue(parse_status(STATUS_PLATE_DETECTED).plate_detected)

    drawer_open = parse_status(STATUS_DRAWER_OPEN)
    self.assertTrue(drawer_open.drawer_open)
    self.assertFalse(drawer_open.busy)

    motors_off = parse_status(STATUS_MOTORS_OFF)
    self.assertFalse(motors_off.motors_enabled)
    self.assertFalse(motors_off.busy)

  def test_no_plate_is_reported_with_the_drawer_out(self):
    # As the reader answered with the plate on the plate holder and the drawer out.
    status = parse_status(
      bytes.fromhex(load_session_replies()["step_9_status_drawer_out_plate_on_holder"])
    )
    self.assertEqual((status.drawer_open, status.plate_detected), (True, False))

  def test_temperatures_from_real_replies(self):
    status = parse_status(STATUS_TEMPERATURES)
    self.assertEqual((status.temperature_bottom, status.temperature_top), (23.1, 24.0))
    status = parse_status(STATUS_IDLE)
    self.assertEqual((status.temperature_bottom, status.temperature_top), (None, None))


class SessionReadsFTDI(FakeFTDI):
  """Replays a reply read by read, at the times it arrived after its request: a read of more than
  one chunk blocks until its bytes arrive, a chunk read returns nothing until then."""

  def __init__(self, reads: List[Tuple[float, bytes]], clock: List[float]) -> None:
    super().__init__()
    self._reads, self._clock = list(reads), clock

  async def read(self, num_bytes: int = 1) -> bytes:
    if self._reads and (num_bytes > 25 or self._reads[0][0] <= self._clock[0]):
      arrival, data = self._reads.pop(0)
      self._clock[0] = max(self._clock[0], arrival)
      return data
    self._clock[0] += 0.01
    return b""


class ExchangeTests(unittest.IsolatedAsyncioTestCase):
  async def test_reply_split_into_single_bytes(self):
    io = FakeFTDI(chunk=1)
    io.replies = [STATUS_PLATE_DETECTED]
    status = await make_driver(io).request_status()
    self.assertTrue(status.plate_detected)

  async def test_leading_garbage_is_dropped(self):
    io = FakeFTDI()
    # Not a reader's reply: no garbage before a reply is recorded; two bytes stand in for it.
    io.replies = [b"\x0d\x00" + STATUS_IDLE]
    status = await make_driver(io).request_status()
    self.assertFalse(status.busy)

  async def test_truncated_reply_is_retried_after_purge(self):
    io = FakeFTDI()
    io.replies = [STATUS_TRUNCATED, STATUS_IDLE]
    status = await make_driver(io).request_status()
    self.assertEqual(io.written, [STATUS_QUERY_FRAME, STATUS_QUERY_FRAME])
    self.assertEqual(io.purges, 1)
    self.assertFalse(status.busy)

  async def test_a_long_read_does_not_count_as_silence(self):
    # As the reader answered a spectrum page's first request: the read of the rest blocked
    # 1.65 s, and the page stayed 421 bytes short.
    reads = [
      (at, bytes.fromhex(data))
      for at, data in load_session_replies()["step_17_first_attempt_reads"]
    ]
    clock = [0.0]
    driver = make_driver(SessionReadsFTDI(reads, clock))
    driver._PACKET_READ_TIMEOUT, driver._FRAME_STALL_TIMEOUT = 1.0, 2.0  # the driver's own
    with patch.object(master_module.time, "time", side_effect=lambda: clock[0]):
      frame = await driver._ftdi_read_frame()
    self.assertEqual(frame, b"".join(data for _, data in reads))
    self.assertGreaterEqual(clock[0], reads[-1][0] + 2.0)  # 2 s of silence after the last bytes

  async def test_no_reply_raises_after_retries(self):
    io = FakeFTDI()
    with self.assertRaises(FrameError):
      await make_driver(io).request_status(retries=2)
    self.assertEqual(len(io.written), 2)

  def test_default_read_timeout_is_checked_on_assignment(self):
    driver = make_driver(FakeFTDI())
    driver.default_read_timeout = 60.0
    self.assertEqual(driver.default_read_timeout, 60.0)
    with self.assertRaises(ValueError):
      driver.default_read_timeout = 0

  async def test_writing_stored_parameters_is_refused(self):
    io = FakeFTDI()
    with self.assertRaises(ValueError):
      await make_driver(io).send_command(
        CLARIOstarDriver.CommandFamily.SET_PARAMETER,
        CLARIOstarDriver.Command.SET_PARAMETER_WORD,
        parameters=b"\x12\x00\x00\x04\x19",
      )
    self.assertEqual(io.written, [])

  async def test_command_byte_rules(self):
    driver = make_driver(FakeFTDI())
    with self.assertRaises(ValueError):
      await driver.send_command(driver.CommandFamily.STATUS, 0x00)
    with self.assertRaises(ValueError):
      await driver.send_command(driver.CommandFamily.DRAWER)


class FirmwareTests(unittest.IsolatedAsyncioTestCase):
  async def test_discover_applies_the_stored_parameters(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, PARAMETERS_REPLY]
    driver = make_driver(io)
    await driver.discover()
    self.assertEqual(driver.configuration.extended_temperature_range, False)

  def test_unconfirmed_firmware_warns(self):
    driver = make_driver(FakeFTDI())
    driver.configuration.firmware_version = "9.99"
    with self.assertLogs(master_module.logger, "WARNING"):
      driver._check_firmware_compatibility()


class WhenIdleTests(unittest.IsolatedAsyncioTestCase):
  async def test_command_waits_until_the_device_is_idle(self):
    io = FakeFTDI()
    io.replies = [
      STATUS_BUSY,
      STATUS_BUSY,
      STATUS_IDLE,
      PARAMETERS_REPLY,
    ]
    driver = make_driver(io)
    await driver.discover()
    self.assertEqual(io.written, [STATUS_QUERY_FRAME] * 3 + [PARAMETERS_FRAME])

  async def test_when_idle_false_sends_at_once(self):
    io = FakeFTDI()
    io.replies = [PARAMETERS_REPLY]
    payload = ftdi_extract_payload(PARAMETERS_FRAME)
    await make_driver(io).send_command(
      payload[0], payload[1], parameters=payload[2:], when_idle=False
    )
    self.assertEqual(io.written, [PARAMETERS_FRAME])


class RefusedTests(unittest.IsolatedAsyncioTestCase):
  def test_quit_codes_from_real_refusals(self):
    expected = {
      "refused_busy": 202,
      "refused_no_results": 213,
      "refused_no_active_run": 214,
      "refused_cycle_time": 65,
    }
    refused_temperature = parse_quit_codes(ftdi_extract_payload(REFUSED_TEMPERATURE))
    assert refused_temperature is not None
    self.assertEqual(refused_temperature[0], 110)
    for name, code in expected.items():
      quit_codes = parse_quit_codes(ftdi_extract_payload(REFUSED[name][1]))
      assert quit_codes is not None, name
      self.assertEqual(quit_codes[0], code, name)

  def test_accepted_replies_have_no_quit_codes(self):
    for reply in (STATUS_IDLE, STATUS_BUSY, STATUS_DRAWER_OPEN, FIRMWARE_INFO_REPLY, INIT_REPLY):
      self.assertIsNone(parse_quit_codes(ftdi_extract_payload(reply)))

  async def test_refused_command_raises_with_quit_codes(self):
    sent, received = REFUSED["refused_no_results"]
    payload = ftdi_extract_payload(sent)
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, received]
    with self.assertRaises(CommandRefusedError) as ctx:
      await make_driver(io).send_command(payload[0], payload[1], parameters=payload[2:])
    self.assertEqual(io.written, [STATUS_QUERY_FRAME, sent])
    self.assertEqual(ctx.exception.quit_codes, (213, 0, 0, 0, 0))
    self.assertIn("quit code 213", str(ctx.exception))


class StatusRefusalAndWarningTests(unittest.IsolatedAsyncioTestCase):
  async def test_refused_status_raises(self):
    io = FakeFTDI()
    io.replies = [STATUS_NOT_INITIALIZED]
    with self.assertRaises(CommandRefusedError) as ctx:
      await make_driver(io).request_status()
    self.assertEqual(ctx.exception.quit_codes[0], 201)
    self.assertIn("not initialized", str(ctx.exception))

  async def test_refused_status_while_polling_raises(self):
    io = FakeFTDI()
    io.replies = [STATUS_NOT_INITIALIZED]
    with self.assertRaises(CommandRefusedError):
      await make_driver(io).discover()

  async def test_warning_is_logged_once_and_not_raised(self):
    io = FakeFTDI()
    io.replies = [
      STATUS_WARNING,
      STATUS_WARNING,
      STATUS_IDLE,
      PARAMETERS_REPLY,
    ]
    driver = make_driver(io)
    with self.assertLogs(master_module.logger, "WARNING") as logs:
      await driver.discover()
    warnings = [r for r in logs.output if "device warning" in r]
    self.assertEqual(len(warnings), 1)
    self.assertIn("quit code 250", warnings[0])

  def test_line_error_codes_are_named(self):
    for code in (1, 2, 3):
      error = CommandRefusedError(b"\x80", (code, 0, 0, 0, 0))
      self.assertIn("the reader received a frame", str(error))


class SetupTests(unittest.IsolatedAsyncioTestCase):
  async def setup_and_return_written(self, replies: List[bytes]) -> List[bytes]:
    io = FakeFTDI()
    io.replies = replies
    driver = make_driver(io)
    driver.discover = AsyncMock()  # type: ignore[method-assign]
    await driver.setup()
    self.assertEqual(io.purges, 1)
    return io.written

  async def test_setup_initializes_an_initialized_reader(self):
    written = await self.setup_and_return_written(
      [INIT_REPLY, STATUS_BUSY, STATUS_IDLE, STATUS_IDLE]
    )
    self.assertEqual(written, [INIT_FRAME] + [STATUS_QUERY_FRAME] * 3)

  async def test_motors_still_off_after_init_raises(self):
    with self.assertRaises(RuntimeError):
      await self.setup_and_return_written([INIT_REPLY, STATUS_MOTORS_OFF, STATUS_MOTORS_OFF])

  async def test_malformed_init_reply_is_not_resent(self):
    io = FakeFTDI()
    # Not a reader's reply: no short INIT reply is recorded; the recorded one cut at 100 bytes.
    io.replies = [INIT_REPLY[:100], STATUS_BUSY, STATUS_IDLE]
    driver = make_driver(io)
    await driver.initialize()
    self.assertEqual(io.written, [INIT_FRAME] + [STATUS_QUERY_FRAME] * 2)
    self.assertEqual(io.purges, 1)
    self.assertIsNone(driver.configuration.model)  # the configuration stays unread


class USBTests(unittest.IsolatedAsyncioTestCase):
  def make_usb_driver(self) -> "tuple[CLARIOstarDriver, MagicMock]":
    io = MagicMock(spec=USB)
    io.setup = AsyncMock()
    io.write = AsyncMock()
    io.read = AsyncMock(side_effect=TimeoutError)
    io.drain = AsyncMock()
    driver = CLARIOstarDriver(use_io="usb", io=io)
    driver._RETRY_SETTLE = 0
    return driver, io

  async def test_setup_enables_bulk_protocol_and_sets_clock(self):
    driver, io = self.make_usb_driver()
    await driver._usb_setup()
    enable, clock = io.ctrl_transfer.call_args_list
    self.assertEqual(enable.args, (0x22, 0x8B, 0, 0, b"\x01"))
    self.assertEqual(clock.args[:4], (0x22, 0x88, 0, 0))
    self.assertEqual(clock.args[4][:4], b"\x00\x00\x00\x00")
    self.assertEqual(len(clock.args[4]), 8)

  async def test_status_query_packet_and_retry_on_silence(self):
    driver, io = self.make_usb_driver()
    with self.assertRaises(FrameError):
      await driver.request_status(retries=2)
    sent = [call.args[0] for call in io.write.call_args_list]
    self.assertEqual(sent, [bytes.fromhex("000c010000000180")] * 2)
    io.drain.assert_awaited_once()


if __name__ == "__main__":
  unittest.main()
