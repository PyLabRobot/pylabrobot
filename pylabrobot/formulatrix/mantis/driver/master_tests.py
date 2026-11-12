"""MantisDriver tests: the bytes sent, against replies recorded from a Mantis v4."""

import asyncio
import json
import os
import struct
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple
from unittest import mock

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver import master as master_module
from pylabrobot.formulatrix.mantis.driver.configuration import DeviceConfiguration
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisConnectionLostError,
  MantisMotionError,
  MantisNotConnectedError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.events import (
  MOVE_DONE_EVENT,
  SEQUENCE_STOPPED_EVENT,
  decode_sequence_stopped,
)
from pylabrobot.formulatrix.mantis.driver.features.led_bar import LedBar
from pylabrobot.formulatrix.mantis.driver.features.pump import PressureController, Pump
from pylabrobot.formulatrix.mantis.driver.features.rfid import (
  RFID_PING_DEVICE_COMMAND,
  RFID_READ_TAG_COMMAND,
)
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master import ABSOLUTE_ENCODER_READS, MantisDriver
from pylabrobot.io.ftdi import FTDI, D2XXError

with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
  RECORDING = json.load(f)
# A status read of motor 0 with the event the device sent before its reply, as recorded.
RECORDED_EVENT = (
  "280000005d0000000000010200000000000000001096943f00000000000000000000000000000000f0ab"
)
# A reply to a command never sent, as recorded.
RECORDED_UNMATCHED_REPLY = "0c000000816d0000000000008d6d"
# The reply payloads a Mantis v4 sent, by (board, command, request payload), as hex.
RECORDED_REPLIES: Dict[Tuple[int, int, str], str] = {
  (0, 1, ""): "34002e00320030002e0036002e0032000000",
  (0, 2, ""): "4c0069007100750069006400480061006e0064006c00650072000000",
  (10, 1, ""): "31002e0030002e0038000000",
  (11, 1, ""): "30002e0031002e003700200044004200470000000000",
  (12, 1, ""): "30002e0031002e003800200044004200470000000000",
  (0, 10, "0000"): "000000000040afc0000000000040af40",
  (0, 10, "0100"): "000000000040afc0000000000040af40",
  (0, 10, "0200"): "00000000000064c00000000000f8a140",
  # The motor reads: currents, configuration, status, position, following error, encoder.
  (0, 12, "0000"): "000000000000f83f000000000000f03f000000403333e33f",
  (0, 12, "0200"): "000000000000f03f000000000000f03f000000606666e63f",
  (0, 14, "0100"): "01000000000000409f400000000000006940000000000000000064000100",
  (0, 14, "0200"): "00000000000000409f400000000000005940000000000000000064000100",
  (0, 20, "0000"): "0000",
  (0, 20, "0100"): "8000",
  (0, 20, "0200"): "0600",
  (
    0,
    27,
    "0000",
  ): "00000000000000000000000000000000000000000000000000000000000000000000000000000000"
  "0000000000407f40",
  (
    0,
    27,
    "0200",
  ): "00000000fc0c7840000000a076d385c00000004005c2bec000000000d0e74ec000000000b8eaa040"
  "0000004005c2be40",
  (0, 82, "0000"): "01000000000000001040",
  (0, 82, "0200"): "01000000000000005940",
  (0, 84, "0000"): "00000000b42350bf",
  (0, 84, "0100"): "0000000090b8d73f",
  # The absolute encoders, valid.
  (11, 30, "0000"): "010000000000f43f6740",
  (11, 30, "0100"): "010000000000a0d73940",
  # The pressure board: each controller's sensor calibration, its reading vented and its status,
  # and whether a controller is enabled and the pump on, with the pump off.
  (10, 20, "0000"): "0000004049d0853f000000e06f292bc0",
  (10, 20, "0100"): "00000080d1d5853f00000000b4572bc0",
  (10, 20, "0200"): "000000001723853f00000020486343c0",
  (10, 30, "0000"): "0000000000000000",
  (10, 30, "0100"): "0000000000000000",
  (10, 30, "0200"): "00000000c0d7e1bf",
  (10, 16, "0000"): "0000",
  (10, 12, "0000"): "0000",
  (10, 12, "0100"): "0000",
  (10, 14, ""): "0000",
  # Its writes, each answered with no payload: the controllers, the pump, the bottle and chip
  # valves, and the targets set when the pump starts after setup (20, 20 psi and -15 inHg).
  **{(10, 13, f"0{controller}000{enabled}00"): "" for controller in "012" for enabled in "01"},
  (10, 15, "0000"): "",
  (10, 15, "0100"): "",
  **{(10, 32, f"0{controller}00{pwm}"): "" for controller in "01" for pwm in ("0000", "204e")},
  **{
    (10, 34, f"0{controller}00{pwm}"): "" for controller in "01" for pwm in ("0000", "1027", "204e")
  },
  (10, 11, "00000000000000003440"): "",
  (10, 11, "01000000000000003440"): "",
  (10, 11, "02000000000000002ec0"): "",
  # The release and the lock at levels 1, 2 and 3.
  (12, 31, "000000000000e83f000000000000000088130000"): "",
  (12, 30, "0000000000000000105839b4c876be3fd0070000"): "",
  (12, 30, "0000000000000000e17a14ae47e1ca3fe8030000"): "",
  (12, 30, "0000000000000000333333333333d33ff4010000"): "",
  (12, 33, ""): "0600",
  # The RFID antenna switch to each of the six antennas.
  **{(0, 41, f"78000100{antenna:02x}00"): "" for antenna in (0, 1, 4, 5, 6, 7)},
  # The LED bar: the animation stopped, the idle animation, and setup's blue and green and stop's
  # off on all 20 LEDs.
  (12, 16, "0000"): "",
  (12, 17, "0000000056000100"): "0100",
  **{(12, 13, "00001400" + color * 20): "0100" for color in ("1f00", "e007", "0000")},
  # The sequencer cleared and started, as the pump's setup does; then aborted, and each motor
  # stopped at its default deceleration.
  (0, 61, ""): "0000",
  (0, 62, ""): "0000",
  (0, 64, ""): "0000",
  (0, 24, "0000814e1be8b40a8a40"): "0000",
  (0, 24, "0100814e1be8b40a8a40"): "0000",
  (0, 24, "0200bb34be4f05c2be40"): "0000",
}
# What a stop sends to the pressure board after the motion halt: every controller disabled, the
# pump off and read back off, both positive lines vented and read, as recorded.
PUMP_HALT: List[Tuple[int, int, bytes]] = [
  (10, 13, bytes.fromhex(request)) for request in ("00000000", "01000000", "02000000")
] + [
  (10, 15, bytes.fromhex("0000")),
  (10, 14, b""),
  *[
    (10, command, bytes.fromhex(request))
    for request in ("0000204e", "0100204e")
    for command in (34, 32)
  ],
  *[
    (10, command, bytes.fromhex(request))
    for request in ("00000000", "01000000")
    for command in (34, 32)
  ],
  (10, 30, bytes.fromhex("0000")),
  (10, 30, bytes.fromhex("0100")),
]
# The sensor calibration the recorded replies carry, by controller.
RECORDED_CALIBRATION: Dict[PressureController, Tuple[float, float]] = {
  "bottle": (0.010651180520653725, -13.580931663513184),
  "chip": (0.010661732405424118, -13.671295166015625),
  "vacuum": (0.010320834815502167, -38.775638580322266),
}
# The sequencer's replies, by (board, command, request payload), as hex: clear, start, stop and
# abort, and queue writes to address 40 of item 1 (a hold of 10) and items 3 to 5 (holds of 500).
RECORDED_SEQUENCER_REPLIES: Dict[Tuple[int, int, str], str] = {
  (0, 61, ""): "0000",
  (0, 62, ""): "0000",
  (0, 63, ""): "0000",
  (0, 64, ""): "0000",
  (0, 65, "01000a00280001001f00"): "c7000000",
  (0, 65, "0300f401280001000000"): "c7000000",
  (0, 65, "0400f401280001000000"): "c7000000",
  (0, 65, "0500f401280001000000"): "c6000000",
}
# A clear that answered 8.
RECORDED_CLEAR_REPLY_8 = "0800"
# Item 1 reported played, sent with the reply that queued it.
RECORDED_ITEM_1_PLAYED = "12000000100100000000100301000000c800db02"
# Items 3, 4 and 5 reported played, 0.5 s apart after their replies.
RECORDED_ITEMS_3_TO_5_PLAYED = [
  "120000002d0100000000100303000200c600e802",
  "120000002d0100000000100304000100c700ed02",
  "120000002d0100000000100305000000c800e202",
]
# Queue writes of one hold at address 40, items 368 to 373, and their replies: the queue was full
# until item 373.
RECORDED_QUEUE_FULL_REPLIES: Dict[str, str] = {
  "70010100280001001b00": "ffff0000",
  "71010100280001001b00": "ffff0000",
  "72010100280001001b00": "ffff0000",
  "73010100280001001b00": "ffff0000",
  "74010100280001001b00": "ffff0000",
  "75010100280001001b00": "01000000",
}
# The sequencer reported stopped, after a stop and after an abort.
RECORDED_STOPPED_AFTER_STOP = "100000002244000000001103363200001575"
RECORDED_STOPPED_AFTER_ABORT = "100000004f0d0000000011030c000000420e"
# Motor 0 reported error 9, the sequencer stopped, then the move ended.
RECORDED_MOTOR_ERROR_EVENTS = [
  "10000000c85f00000000030200000900d25d",
  "10000000c85f000000001103d9310000106d",
  "28000000c85f00000000010200000001000000a08ac75740000000000000000000000000000000003c7b",
]
# A frame with code 0x7FFF and no payload, sent instead of a reply.
RECORDED_CODE_7FFF_FRAME = "0c000000000000000000ff7ff37f"
# Motor 0's absolute encoder after connecting: its first read not valid, the next valid.
ABSOLUTE_ENCODER_REPLIES_AFTER_POWER_ON = ["00000000000000000000", "010000000000f43f6740"]
# The link reader's failure when the USB cable was pulled from a Mantis v4, through D2XX.
RECORDED_LINK_FAILURE = D2XXError("FT_GetQueueStatus returned FT_IO_ERROR (4)", status=4)
# Interface A: what the RFID reader on a Mantis v4 sent, chunked as it arrived.
READER_PING_REPLY = [b"TRF7970A EVM \r\n\r\n"]
READER_EMPTY_SLOT_REPLY = [b"Request mode.\r\n", b"[]\r\n"]


def make_answers(
  *, absent_boards: Tuple[int, ...] = ()
) -> Dict[Tuple[int, int, bytes], List[bytes]]:
  """The recorded reply payloads, by (board, command, payload); a board absent answers nothing."""
  return {
    (board, command, bytes.fromhex(request)): [bytes.fromhex(reply)]
    for (board, command, request), reply in RECORDED_REPLIES.items()
    if board not in absent_boards
  }


class FakeReader:
  """Interface A: answers the ping, and a tag read with the tag at the selected antenna, in its
  recorded chunks; an antenna with no tag answers as an empty slot."""

  def __init__(self, *, answers_ping: bool = True, tags: Optional[Dict[int, List[bytes]]] = None):
    self.answers_ping = answers_ping
    self.tags = tags or {}
    self.antenna: Optional[int] = None
    self.written: List[bytes] = []
    self.pending: List[bytes] = []
    self.io = mock.create_autospec(FTDI, instance=True)
    self.io.write.side_effect = self.write
    self.io.read.side_effect = self.read
    self.io.usb_purge_rx_buffer.side_effect = self.purge

  async def purge(self) -> None:
    self.pending = []

  async def write(self, data: bytes) -> int:
    self.written.append(data)
    if data == RFID_PING_DEVICE_COMMAND and self.answers_ping:
      self.pending = list(READER_PING_REPLY)
    elif data == RFID_READ_TAG_COMMAND and self.antenna is not None:
      self.pending = list(self.tags.get(self.antenna, READER_EMPTY_SLOT_REPLY))
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    return self.pending.pop(0) if self.pending else b""


class FakeLink:
  """Answers each frame written with its recorded reply, echoing packet number and board, then
  with the event frames recorded after it, if any. A list of replies is played in order and its
  last reply repeats. An antenna switch selects ``reader``'s antenna."""

  def __init__(
    self,
    answers: Dict[Tuple[int, int, bytes], List[bytes]],
    *,
    reader: Optional[FakeReader] = None,
    events: Optional[Dict[Tuple[int, int, bytes], List[bytes]]] = None,
  ):
    self.answers = answers
    self.events = events or {}
    self.reader = reader or FakeReader()
    self.written: List[bytes] = []
    self.incoming = bytearray()
    self.io = mock.create_autospec(FTDI, instance=True)
    self.io.device_id = "test"
    self.io.write.side_effect = self.write
    self.io.read.side_effect = self.read

  async def write(self, data: bytes) -> int:
    self.written.append(data)
    packet_number, board, command, payload = parse_frame(frame=data)
    if (board, command) == (0, 41):
      (self.reader.antenna,) = struct.unpack_from("<H", payload, 4)
    replies = self.answers.get((board, command, payload))
    if replies is not None:
      reply = replies.pop(0) if len(replies) > 1 else replies[0]
      self.incoming.extend(
        build_frame(packet_number=packet_number, board=board, command=0, payload=reply)
      )
      for event in self.events.get((board, command, payload), []):
        self.incoming.extend(event)
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    data = bytes(self.incoming[:num_bytes])
    del self.incoming[:num_bytes]
    return data


async def pass_at_once(self: Pump, *, seconds: float) -> None:
  """A pump hold that passes at once."""
  await asyncio.sleep(0)


class DriverTestCase(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    # The LED bar's holds are taken, not waited: setup holds "setup_done" for a second.
    patcher = mock.patch.object(LedBar, "_sleep", autospec=True)
    self.led_bar_sleep = patcher.start()
    self.addCleanup(patcher.stop)

  def make_driver(self, *, link: FakeLink, **kwargs) -> MantisDriver:
    driver = MantisDriver(io=link.io, rfid_io=link.reader.io, **kwargs)
    # short, as a board that never answers waits it out; long enough for a slow test runner
    driver.default_read_timeout = 0.25
    holds = mock.patch.object(Pump, "_sleep", pass_at_once)
    holds.start()
    self.addCleanup(holds.stop)
    return driver

  async def set_up_driver(
    self, *, link: FakeLink, skip_arm: bool = True, skip_self_test: bool = True, **kwargs
  ) -> MantisDriver:
    driver = self.make_driver(link=link, **kwargs)
    await driver.setup(skip_arm=skip_arm, skip_self_test=skip_self_test)
    self.addAsyncCleanup(driver.stop)
    return driver


class SetupTests(DriverTestCase):
  async def test_line_set_up(self):
    link = FakeLink(answers=make_answers())
    await self.set_up_driver(link=link)
    link.io.set_baudrate.assert_awaited_once_with(baudrate=115_200)
    link.io.set_line_property.assert_awaited_once_with(bits=8, stopbits=0, parity=0)
    link.io.set_flowctrl.assert_awaited_once_with(flowctrl=0x100)

  async def test_v4_discovery(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link)
    self.assertEqual(
      [parse_frame(frame=frame)[1:] for frame in link.written[:4]],
      [(board, 1, b"") for board in (0, 10, 11, 12)],
    )
    self.assertEqual(
      driver.configuration,
      DeviceConfiguration(
        generation="v4",
        firmware_versions={0: "4.20.6.2", 10: "1.0.8", 11: "0.1.7 DBG", 12: "0.1.8 DBG"},
        motor_limits={0: (-4000.0, 4000.0), 1: (-4000.0, 4000.0), 2: (-160.0, 2300.0)},
        feedback_sensor_parameters=RECORDED_CALIBRATION,
      ),
    )

  async def test_only_board_0_must_answer_and_each_other_board_is_asked_once(self):
    link = FakeLink(answers=make_answers(absent_boards=(10, 11, 12)))
    driver = await self.set_up_driver(link=link)
    self.assertEqual(driver.configuration.generation, "v3")
    self.assertEqual(
      driver.configuration.firmware_versions, {0: "4.20.6.2", 10: None, 11: None, 12: None}
    )
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    for board in (10, 11, 12):
      with self.subTest(board=board):
        self.assertEqual(sent.count((board, 1, b"")), 1)

  async def test_declared_configuration_that_disagrees_refuses_setup(self):
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "declared.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump({"device": {**RECORDING["device"], "generation": "v3"}}, f)
      link = FakeLink(answers=make_answers())
      driver = self.make_driver(link=link, declared_configuration_json=path)
      with self.assertRaisesRegex(
        MantisConfigurationError, "generation: declared 'v3', device 'v4'"
      ):
        await driver.setup()
    link.io.stop.assert_awaited()
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    self.assertEqual([command for board, command, _ in sent if board == 12], [1])

  async def test_declared_board_that_does_not_answer_refuses_setup(self):
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "declared.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump({"device": RECORDING["device"]}, f)
      link = FakeLink(answers=make_answers(absent_boards=(12,)))
      driver = self.make_driver(link=link, declared_configuration_json=path)
      with self.assertRaisesRegex(
        MantisConfigurationError, "board 12: declared '0.1.8 DBG', device did not"
      ):
        await driver.setup()
    self.assertIsNone(driver.plate_clamp)

  async def test_a_second_setup_opens_the_link_once(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link)
    reader = driver._reader
    await driver.setup(skip_arm=True, skip_self_test=True)
    link.io.setup.assert_awaited_once()
    link.io.set_baudrate.assert_awaited_once()
    self.assertIs(driver._reader, reader)

  async def test_setup_after_a_lost_link_closes_it_quietly_and_reopens_it(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
    read = link.io.read.side_effect
    link.io.read.side_effect = RECORDED_LINK_FAILURE
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.master", level="ERROR"):
      while driver._reader is not None and not driver._reader.done():
        await asyncio.sleep(0.01)
    link.io.read.side_effect = read
    sent_before = len(link.written)
    with self.assertLogs("pylabrobot.formulatrix.mantis", level="INFO") as logs:
      await driver.setup(skip_arm=True, skip_self_test=True)
    warnings = [record for record in logs.records if record.levelname != "INFO"]
    self.assertEqual(len(warnings), 1)
    self.assertIn("the link was lost", warnings[0].getMessage())
    self.assertTrue(all(record.exc_info is None for record in logs.records))
    link.io.stop.assert_awaited_once()
    self.assertEqual(link.io.setup.await_count, 2)
    # The first frame after the failure is setup's own firmware read: nothing went to the dead link.
    self.assertEqual(parse_frame(frame=link.written[sent_before])[1:], (0, 1, b""))
    assert driver._reader is not None
    self.assertFalse(driver._reader.done())

  async def test_an_older_declared_file_takes_what_it_lacks_from_the_recording(self):
    older = {block: values for block, values in RECORDING.items() if block != "led_bar"}
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "older.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(older, f)
      link = FakeLink(answers=make_answers())
      with self.assertLogs("pylabrobot.formulatrix.mantis.driver.master", level="WARNING") as logs:
        driver = await self.set_up_driver(link=link, declared_configuration_json=path)
    self.assertEqual(len(logs.output), 1)
    self.assertIn(f"led_bar from {RECORDING_MANTIS_V4}", logs.output[0])
    assert driver.led_bar is not None
    self.assertEqual(driver.led_bar.number_of_leds, 20)
    self.assertEqual(driver.configuration.feedback_sensor_parameters, RECORDED_CALIBRATION)

  async def test_saved_configuration_declares_the_same_device(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "mantis.json")
      driver.save_configuration(path=path)
      await driver.stop()
      with open(path, encoding="utf-8") as f:
        saved = json.load(f)
      for block in ("plate_clamp", "led_bar"):
        self.assertEqual(saved[block], RECORDING[block])
      link = FakeLink(answers=make_answers())
      await self.set_up_driver(link=link, declared_configuration_json=path)


class ReadTests(DriverTestCase):
  async def asyncSetUp(self):
    self.link = FakeLink(answers=make_answers())
    self.driver = await self.set_up_driver(link=self.link)

  async def test_reads(self):
    driver = self.driver
    cases = [
      (0, 1, "", lambda: driver.request_firmware_version(board=0), "4.20.6.2"),
      (0, 2, "", lambda: driver.request_name(), "LiquidHandler"),
      (0, 10, "0200", lambda: driver.request_motor_limits(motor=2), (-160.0, 2300.0)),
    ]
    for board, command, request, read, expected in cases:
      with self.subTest(board=board, command=command):
        result = await read()
        self.assertEqual(
          parse_frame(frame=self.link.written[-1])[1:], (board, command, bytes.fromhex(request))
        )
        self.assertEqual(result, expected)

  async def test_reads_refuse_unseen_motor_and_board(self):
    sent_before = len(self.link.written)
    with self.assertRaises(ValueError):
      await self.driver.request_motor_limits(motor=3)
    with self.assertRaises(ValueError):
      await self.driver.request_firmware_version(board=13)
    self.assertEqual(len(self.link.written), sent_before)

  async def test_event_arriving_with_the_reply_reaches_its_waiter(self):
    write = self.link.write

    async def event_then_reply(data: bytes) -> int:
      self.link.incoming.extend(bytes.fromhex(RECORDED_EVENT))
      return await write(data)

    self.link.io.write.side_effect = event_then_reply
    waiter = self.driver._expect_event(board=0, code=0x0201)
    self.assertEqual(await self.driver.request_motor_limits(motor=0), (-4000.0, 4000.0))
    payload = await self.driver._wait_for_event(waiter=waiter, timeout=1.0)
    self.assertEqual(payload.hex(), RECORDED_EVENT[24:-4])
    self.assertEqual(self.driver._event_waiters, [])

  async def test_unmatched_reply_is_ignored(self):
    write = self.link.write

    async def unmatched_then_reply(data: bytes) -> int:
      self.link.incoming.extend(bytes.fromhex(RECORDED_UNMATCHED_REPLY))
      return await write(data)

    self.link.io.write.side_effect = unmatched_then_reply
    self.assertEqual(await self.driver.request_name(), "LiquidHandler")

  async def test_failed_read_fails_the_command_in_flight_and_the_one_waiting(self):
    self.link.io.read.side_effect = OSError("device unplugged")
    in_flight, waiting = await asyncio.gather(
      self.driver.request_name(), self.driver.request_name(), return_exceptions=True
    )
    assert isinstance(in_flight, MantisConnectionLostError)
    self.assertIsInstance(in_flight.__cause__, OSError)
    assert isinstance(waiting, MantisConnectionLostError)
    self.assertIsInstance(waiting.__cause__, OSError)

  async def test_stop_fails_the_command_in_flight_and_every_event_waiter(self):
    del self.link.answers[(0, 2, b"")]
    self.driver.default_read_timeout = 5.0
    waiter = self.driver._expect_event(board=0, code=0x0201)
    command = asyncio.ensure_future(self.driver.request_name())
    await asyncio.sleep(0.02)
    await self.driver.stop()
    with self.assertRaisesRegex(MantisNotConnectedError, "stopped"):
      await command
    with self.assertRaisesRegex(MantisNotConnectedError, "stopped"):
      await self.driver._wait_for_event(waiter=waiter, timeout=1.0)

  async def test_unanswered_read_is_resent_with_its_packet_number_then_times_out(self):
    del self.link.answers[(0, 10, b"\x01\x00")]
    with self.assertRaises(MantisTimeoutError):
      await self.driver.request_motor_limits(motor=1)
    self.assertEqual(len(set(self.link.written[-3:])), 1)
    self.assertNotEqual(self.link.written[-4][4:6], self.link.written[-1][4:6])


class MotorReadTests(DriverTestCase):
  async def asyncSetUp(self):
    self.link = FakeLink(answers=make_answers())
    self.driver = await self.set_up_driver(link=self.link)

  async def test_reads(self):
    driver = self.driver
    cases = [
      (
        0,
        12,
        "0000",
        lambda: driver.request_motor_currents(motor=0),
        (1.5, 1.0, 0.6000000238418579),
      ),
      (
        0,
        14,
        "0100",
        lambda: driver.request_motor_configuration(motor=1),
        (1, 2000.0, 200.0, 0.0, 100, 1),
      ),
      (0, 20, "0100", lambda: driver.request_motor_status(motor=1), 0x80),
      (0, 20, "0200", lambda: driver.request_motor_status(motor=2), 6),
      (0, 27, "0000", lambda: driver.request_motor_position(motor=0), 0.0),
      (0, 27, "0200", lambda: driver.request_motor_position(motor=2), 384.8115234375),
      (0, 82, "0000", lambda: driver.request_following_error_configuration(motor=0), (True, 4.0)),
      (0, 82, "0200", lambda: driver.request_following_error_configuration(motor=2), (True, 100.0)),
      (0, 84, "0100", lambda: driver.request_encoder_position(motor=1), 0.3706398010253906),
      (11, 30, "0000", lambda: driver.request_absolute_encoder_position(motor=0), 185.99853515625),
    ]
    for board, command, request, read, expected in cases:
      with self.subTest(board=board, command=command, request=request):
        result = await read()
        self.assertEqual(
          parse_frame(frame=self.link.written[-1])[1:], (board, command, bytes.fromhex(request))
        )
        self.assertEqual(result, expected)

  async def test_reads_refuse_unseen_motors(self):
    driver = self.driver
    reads = [
      lambda: driver.request_motor_currents(motor=3),
      lambda: driver.request_motor_configuration(motor=3),
      lambda: driver.request_motor_status(motor=3),
      lambda: driver.request_motor_position(motor=3),
      lambda: driver.request_following_error_configuration(motor=3),
      lambda: driver.request_encoder_position(motor=3),
      lambda: driver.request_absolute_encoder_position(motor=2),
    ]
    sent_before = len(self.link.written)
    for read in reads:
      with self.assertRaises(ValueError):
        await read()
    self.assertEqual(len(self.link.written), sent_before)

  async def test_absolute_encoder_read_again_until_valid(self):
    self.link.answers[(11, 30, b"\x00\x00")] = [
      bytes.fromhex(reply) for reply in ABSOLUTE_ENCODER_REPLIES_AFTER_POWER_ON
    ]
    sent_before = len(self.link.written)
    self.assertEqual(await self.driver.request_absolute_encoder_position(motor=0), 185.99853515625)
    sent = [parse_frame(frame=frame)[1:] for frame in self.link.written[sent_before:]]
    self.assertEqual(sent, [(11, 30, b"\x00\x00")] * 2)

  async def test_absolute_encoder_never_valid_raises(self):
    self.link.answers[(11, 30, b"\x01\x00")] = [
      bytes.fromhex(ABSOLUTE_ENCODER_REPLIES_AFTER_POWER_ON[0])
    ]
    sent_before = len(self.link.written)
    with self.assertRaisesRegex(MantisTimeoutError, "no valid position in 5 reads"):
      await self.driver.request_absolute_encoder_position(motor=1)
    self.assertEqual(len(self.link.written) - sent_before, ABSOLUTE_ENCODER_READS)


class SequencerTests(DriverTestCase):
  async def asyncSetUp(self):
    answers = make_answers()
    for (board, command, request), reply in RECORDED_SEQUENCER_REPLIES.items():
      answers[(board, command, bytes.fromhex(request))] = [bytes.fromhex(reply)]
    for request, reply in RECORDED_QUEUE_FULL_REPLIES.items():
      answers[(0, 65, bytes.fromhex(request))] = [bytes.fromhex(reply)]
    self.link = FakeLink(
      answers=answers,
      events={
        (0, 65, bytes.fromhex("01000a00280001001f00")): [bytes.fromhex(RECORDED_ITEM_1_PLAYED)],
        (0, 63, b""): [bytes.fromhex(RECORDED_STOPPED_AFTER_STOP)],
        (0, 64, b""): [bytes.fromhex(RECORDED_STOPPED_AFTER_ABORT)],
      },
    )
    self.driver = await self.set_up_driver(link=self.link)

  def sent_since(self, count: int) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in self.link.written[count:]]

  async def test_sequencer_commands_are_sent_once_and_return_their_reply(self):
    driver = self.driver
    cases = [
      (61, driver._unchecked_fw_clear_sequencer),
      (62, driver._unchecked_fw_start_sequencer),
      (63, driver._unchecked_fw_stop_sequencer),
      (64, driver._unchecked_fw_abort_sequencer),
    ]
    for command, send in cases:
      with self.subTest(command=command):
        sent_before = len(self.link.written)
        self.assertEqual(await send(), 0)
        self.assertEqual(self.sent_since(sent_before), [(0, command, b"")])

  async def test_clear_returns_the_word_it_answers(self):
    self.link.answers[(0, 61, b"")] = [bytes.fromhex(RECORDED_CLEAR_REPLY_8)]
    self.assertEqual(await self.driver._unchecked_fw_clear_sequencer(), 8)

  async def test_unanswered_start_is_not_resent(self):
    del self.link.answers[(0, 62, b"")]
    sent_before = len(self.link.written)
    with self.assertRaises(MantisTimeoutError):
      await self.driver._unchecked_fw_start_sequencer()
    self.assertEqual(self.sent_since(sent_before), [(0, 62, b"")])

  async def test_stop_and_abort_are_followed_by_the_stopped_event(self):
    driver = self.driver
    cases = [
      (driver._unchecked_fw_stop_sequencer, (0x3236, 0)),
      (driver._unchecked_fw_abort_sequencer, (12, 0)),
    ]
    for send, expected in cases:
      with self.subTest(expected=expected):
        waiter = driver._expect_event(board=0, code=SEQUENCE_STOPPED_EVENT)
        await send()
        payload = await driver._wait_for_event(waiter=waiter, timeout=1.0)
        self.assertEqual(decode_sequence_stopped(payload=payload), expected)

  async def test_item_played_with_its_reply_is_not_missed(self):
    driver = self.driver
    await driver._unchecked_fw_clear_sequencer()
    await driver._unchecked_fw_start_sequencer()
    sent_before = len(self.link.written)
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    self.assertEqual(item, 1)
    self.assertEqual(self.sent_since(sent_before), [(0, 65, bytes.fromhex("01000a00280001001f00"))])
    await driver._wait_for_queue_item(item=item, timeout=1.0)
    self.assertEqual(driver._event_waiters, [])

  async def test_wait_for_the_last_item_passes_over_the_earlier_ones(self):
    driver = self.driver
    driver._next_item = 3
    items = [
      await driver._queue_ppi_write(duration=500, start_address=40, values=[0]) for _ in "345"
    ]
    self.assertEqual(items, [3, 4, 5])
    wait = asyncio.ensure_future(driver._wait_for_queue_item(item=5, timeout=1.0))
    await asyncio.sleep(0.02)
    for frame in RECORDED_ITEMS_3_TO_5_PLAYED[:2]:
      self.link.incoming.extend(bytes.fromhex(frame))
    await asyncio.sleep(0.05)
    self.assertFalse(wait.done())
    self.link.incoming.extend(bytes.fromhex(RECORDED_ITEMS_3_TO_5_PLAYED[2]))
    await wait

  async def test_every_item_played_is_kept_for_the_dispense_progress(self):
    driver = self.driver
    self.assertIsNone(driver._sequence_progress)
    driver._next_item = 3
    for _ in "345":
      await driver._queue_ppi_write(duration=500, start_address=40, values=[0])
    for frame, item in zip(RECORDED_ITEMS_3_TO_5_PLAYED, [3, 4, 5]):
      self.link.incoming.extend(bytes.fromhex(frame))
      await asyncio.sleep(0.02)
      self.assertEqual(driver._sequence_progress, item)
    self.assertEqual(driver._last_drained_item, 5)
    await driver.stop()
    await driver.setup(skip_arm=True, skip_self_test=True)
    self.assertIsNone(driver._sequence_progress)

  async def test_full_queue_is_sent_again_under_the_next_item(self):
    self.driver._next_item = 368
    sent_before = len(self.link.written)
    item = await self.driver._queue_ppi_write(duration=1, start_address=40, values=[27])
    self.assertEqual(item, 373)
    self.assertEqual(
      self.sent_since(sent_before),
      [(0, 65, bytes.fromhex(request)) for request in RECORDED_QUEUE_FULL_REPLIES],
    )

  async def test_queue_staying_full_times_out(self):
    self.driver._next_item = 368
    sent_before = len(self.link.written)
    with mock.patch.object(master_module, "time") as clock:
      clock.monotonic.side_effect = [0.0, 60.0]
      with self.assertRaisesRegex(MantisTimeoutError, "stayed full for 60.0 s"):
        await self.driver._queue_ppi_write(duration=1, start_address=40, values=[27])
    self.assertEqual(len(self.sent_since(sent_before)), 1)

  async def test_queue_write_refuses_out_of_range_arguments(self):
    cases: List[Tuple[int, int, List[int]]] = [
      (10, 120, [31]),
      (0, 40, [31]),
      (0x8000, 40, [31]),
      (10, 40, []),
      (10, 40, [0x10000]),
    ]
    sent_before = len(self.link.written)
    for duration, start_address, values in cases:
      with self.subTest(duration=duration, start_address=start_address, values=values):
        with self.assertRaises(ValueError):
          await self.driver._queue_ppi_write(
            duration=duration, start_address=start_address, values=values
          )
    self.assertEqual(len(self.link.written), sent_before)

  async def test_default_queue_full_timeout_is_checked_when_assigned(self):
    with self.assertRaises(ValueError):
      self.driver.default_queue_full_timeout = 0.0
    self.assertEqual(self.driver.default_queue_full_timeout, 60.0)

  async def test_motor_error_fails_every_motion_wait_at_once(self):
    driver = self.driver
    stopped = driver._expect_event(board=0, code=SEQUENCE_STOPPED_EVENT)
    move_done = driver._expect_event(board=0, code=MOVE_DONE_EVENT)
    wait = asyncio.ensure_future(driver._wait_for_queue_item(item=0x31D9, timeout=5.0))
    await asyncio.sleep(0.02)
    for frame in RECORDED_MOTOR_ERROR_EVENTS:
      self.link.incoming.extend(bytes.fromhex(frame))
    with self.assertRaisesRegex(MantisMotionError, "motor 0 reported error 9") as raised:
      await wait
    self.assertEqual((raised.exception.motor, raised.exception.code), (0, 9))
    with self.assertRaises(MantisMotionError):
      await driver._wait_for_event(waiter=move_done, timeout=1.0)
    payload = await driver._wait_for_event(waiter=stopped, timeout=1.0)
    self.assertEqual(decode_sequence_stopped(payload=payload), (0x31D9, 0))

  async def test_sequencer_stop_is_a_warning_and_a_move_done_is_not(self):
    with self.assertLogs(master_module.logger, level="WARNING") as logs:
      for frame in RECORDED_MOTOR_ERROR_EVENTS:
        self.link.incoming.extend(bytes.fromhex(frame))
      await asyncio.sleep(0.05)
    output = "".join(logs.output)
    self.assertIn("sequencer stopped at item", output)
    self.assertNotIn("moved", output)

  async def test_event_not_decoded_is_logged_by_its_number(self):
    with self.assertLogs(master_module.logger, level="WARNING") as logs:
      self.link.incoming.extend(bytes.fromhex(RECORDED_CODE_7FFF_FRAME))
      await asyncio.sleep(0.05)
    self.assertIn("event 0x7fff, not decoded", "".join(logs.output))


if __name__ == "__main__":
  unittest.main()
