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
from pylabrobot.formulatrix.mantis.driver.configuration import DeviceConfiguration
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisConnectionLostError,
  MantisNotConnectedError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features.rfid import (
  RFID_PING_DEVICE_COMMAND,
  RFID_READ_TAG_COMMAND,
)
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.io.ftdi import FTDI

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
  # The release and the lock at levels 1, 2 and 3.
  (12, 31, "000000000000e83f000000000000000088130000"): "",
  (12, 30, "0000000000000000105839b4c876be3fd0070000"): "",
  (12, 30, "0000000000000000e17a14ae47e1ca3fe8030000"): "",
  (12, 30, "0000000000000000333333333333d33ff4010000"): "",
  (12, 33, ""): "0600",
  # The RFID antenna switch to each of the six antennas.
  **{(0, 41, f"78000100{antenna:02x}00"): "" for antenna in (0, 1, 4, 5, 6, 7)},
}
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
  """Answers each frame written with its recorded reply, echoing packet number and board. A list
  of replies is played in order and its last reply repeats. An antenna switch selects
  ``reader``'s antenna."""

  def __init__(
    self,
    answers: Dict[Tuple[int, int, bytes], List[bytes]],
    *,
    reader: Optional[FakeReader] = None,
  ):
    self.answers = answers
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
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    data = bytes(self.incoming[:num_bytes])
    del self.incoming[:num_bytes]
    return data


class DriverTestCase(unittest.IsolatedAsyncioTestCase):
  def make_driver(self, *, link: FakeLink, **kwargs) -> MantisDriver:
    driver = MantisDriver(io=link.io, rfid_io=link.reader.io, **kwargs)
    driver.default_read_timeout = 0.05
    return driver

  async def set_up_driver(self, *, link: FakeLink, **kwargs) -> MantisDriver:
    driver = self.make_driver(link=link, **kwargs)
    await driver.setup()
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
    await driver.setup()
    link.io.setup.assert_awaited_once()
    link.io.set_baudrate.assert_awaited_once()
    self.assertIs(driver._reader, reader)

  async def test_saved_configuration_declares_the_same_device(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "mantis.json")
      driver.save_configuration(path=path)
      await driver.stop()
      with open(path, encoding="utf-8") as f:
        self.assertEqual(json.load(f)["plate_clamp"], RECORDING["plate_clamp"])
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


if __name__ == "__main__":
  unittest.main()
