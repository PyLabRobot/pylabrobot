"""MantisDriver tests: the bytes sent, against replies recorded from a Mantis v4."""

import inspect
import json
import logging
import os
import struct
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest import mock

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
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
from pylabrobot.formulatrix.mantis.driver.features.pump import PressureController
from pylabrobot.formulatrix.mantis.driver.features.rfid import (
  RFID_PING_DEVICE_COMMAND,
  RFID_READ_TAG_COMMAND,
)
from pylabrobot.formulatrix.mantis.driver.framing import build_frame, parse_frame
from pylabrobot.formulatrix.mantis.driver.master import ABSOLUTE_ENCODER_READS, MantisDriver
from pylabrobot.io.ftdi import FTDI, D2XXError
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import CustomReadMock, CustomWriteMock

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
  # The pressure board: each controller's sensor calibration, its reading and its status, and
  # whether a controller is enabled and the pump on, with the pump off.
  (10, 20, "0000"): "0000004049d0853f000000e06f292bc0",
  (10, 20, "0100"): "00000080d1d5853f00000000b4572bc0",
  (10, 20, "0200"): "000000001723853f00000020486343c0",
  (10, 30, "0000"): "000000003abcf23f",
  (10, 30, "0100"): "000000004f60f23f",
  (10, 30, "0200"): "00000000c0d7e1bf",
  (10, 16, "0000"): "0000",
  (10, 12, "0000"): "0000",
  (10, 14, ""): "0000",
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
  # The sequencer aborted, and each motor stopped at its default deceleration.
  (0, 64, ""): "0000",
  (0, 24, "0000814e1be8b40a8a40"): "0000",
  (0, 24, "0100814e1be8b40a8a40"): "0000",
  (0, 24, "0200bb34be4f05c2be40"): "0000",
}
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


class FakeFTDI(FTDI):
  """Loop-agnostic FTDI software double recording lifecycle and line-configuration calls."""

  read: CustomReadMock  # type: ignore[assignment]
  write: CustomWriteMock  # type: ignore[assignment]

  def __init__(self, *, device_id: str = "test") -> None:
    HasLifespan.__init__(self)
    self._device_id = device_id
    self.read = CustomReadMock()
    self.write = CustomWriteMock()
    self.entry_error: Optional[BaseException] = None
    self.entered_count = 0
    self.exited_count = 0
    self.baudrates: List[int] = []
    self.line_properties: List[Tuple[int, int, int]] = []
    self.flowctrls: List[int] = []
    self.rts_levels: List[bool] = []
    self.latencies: List[int] = []
    self.purge_rx_handler: Optional[Callable[[], Any]] = None

  def _record_exit(self) -> None:
    self.exited_count += 1

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    # Deliberately omits super()._enter_lifespan(stack), substituting hardware FTDI open.
    if self.entry_error is not None:
      raise self.entry_error
    self.entered_count += 1
    stack.callback(self._record_exit)

  async def reconnect(self) -> None:
    await anyio.lowlevel.checkpoint()

  async def usb_reset(self) -> None:
    await anyio.lowlevel.checkpoint()

  async def set_baudrate(self, baudrate: int) -> None:
    await anyio.lowlevel.checkpoint()
    self.baudrates.append(baudrate)

  async def set_line_property(self, bits: int, stopbits: int, parity: Any, break_: int = 0) -> None:
    del break_
    await anyio.lowlevel.checkpoint()
    self.line_properties.append((bits, stopbits, int(parity)))

  async def set_flowctrl(self, flowctrl: Any) -> None:
    await anyio.lowlevel.checkpoint()
    self.flowctrls.append(int(flowctrl))

  async def set_rts(self, level: bool) -> None:
    await anyio.lowlevel.checkpoint()
    self.rts_levels.append(level)

  async def set_latency_timer(self, latency: int) -> None:
    await anyio.lowlevel.checkpoint()
    self.latencies.append(latency)

  async def usb_purge_rx_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    if self.purge_rx_handler is not None:
      result = self.purge_rx_handler()
      if inspect.isawaitable(result):
        await result

  async def usb_purge_tx_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()


class FakeReader:
  """Interface A: answers the ping, and a tag read with the tag at the selected antenna, in its
  recorded chunks; an antenna with no tag answers as an empty slot."""

  def __init__(self, *, answers_ping: bool = True, tags: Optional[Dict[int, List[bytes]]] = None):
    self.answers_ping = answers_ping
    self.tags = tags or {}
    self.antenna: Optional[int] = None
    self.written: List[bytes] = []
    self.pending: List[bytes] = []
    self.io = FakeFTDI()
    self.io.write.side_effect = self.write
    self.io.read.side_effect = self.read
    self.io.purge_rx_handler = self.purge

  async def purge(self) -> None:
    self.pending = []

  async def write(self, data: bytes) -> int:
    self.written.append(data)
    if data == RFID_PING_DEVICE_COMMAND and self.answers_ping:
      self.pending = list(READER_PING_REPLY)
    elif data == RFID_READ_TAG_COMMAND and self.antenna is not None:
      self.pending = list(self.tags.get(self.antenna, READER_EMPTY_SLOT_REPLY))
    return len(data)

  async def read(self, num_bytes: int = 1, timeout: Optional[float] = None) -> bytes:
    del num_bytes, timeout
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
    self.io = FakeFTDI(device_id="test")
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

  async def read(self, num_bytes: int = 1, timeout: Optional[float] = None) -> bytes:
    del timeout
    data = bytes(self.incoming[:num_bytes])
    del self.incoming[:num_bytes]
    return data


class DriverTestBase(AnyioTestBase):
  # every recorded reply is answered; a slow test runner must not time one out
  default_read_timeout = 5.0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self._stack = stack
    plr_logger = logging.getLogger("pylabrobot")
    old_propagate = plr_logger.propagate
    plr_logger.propagate = True

    def restore_propagate() -> None:
      plr_logger.propagate = old_propagate

    stack.callback(restore_propagate)

    # The LED bar's holds are taken, not waited: setup holds "setup_done" for a second.
    self.led_bar_sleep_calls: List[Tuple[LedBar, float]] = []

    async def record_sleep(bar: LedBar, *, seconds: float) -> None:
      await anyio.lowlevel.checkpoint()
      self.led_bar_sleep_calls.append((bar, seconds))

    stack.enter_context(mock.patch.object(LedBar, "_sleep", record_sleep))

  def make_driver(self, *, link: FakeLink, **kwargs: Any) -> MantisDriver:
    driver = MantisDriver(io=link.io, rfid_io=link.reader.io, **kwargs)
    driver.default_read_timeout = self.default_read_timeout
    return driver

  async def set_up_driver(
    self, *, link: FakeLink, skip_arm: bool = True, **kwargs: Any
  ) -> MantisDriver:
    driver = self.make_driver(link=link, **kwargs)
    await self._stack.enter_async_context(driver.lifespan(skip_arm=skip_arm))
    return driver


class TestSetup(DriverTestBase):
  def test_inherits_managed_lifespan_methods(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = self.make_driver(link=link)
    assert isinstance(driver, HasLifespan)
    assert MantisDriver.setup is HasLifespan.setup
    assert MantisDriver.stop is HasLifespan.stop
    assert MantisDriver.__aenter__ is HasLifespan.__aenter__
    assert MantisDriver.__aexit__ is HasLifespan.__aexit__

  async def test_line_set_up(self) -> None:
    link = FakeLink(answers=make_answers())
    await self.set_up_driver(link=link)
    assert link.io.baudrates == [115_200]
    assert link.io.line_properties == [(8, 0, 0)]
    assert link.io.flowctrls == [0x100]

  async def test_v4_discovery(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link)
    assert [parse_frame(frame=frame)[1:] for frame in link.written[:4]] == [
      (board, 1, b"") for board in (0, 10, 11, 12)
    ]
    assert driver.configuration == DeviceConfiguration(
      generation="v4",
      firmware_versions={0: "4.20.6.2", 10: "1.0.8", 11: "0.1.7 DBG", 12: "0.1.8 DBG"},
      motor_limits={0: (-4000.0, 4000.0), 1: (-4000.0, 4000.0), 2: (-160.0, 2300.0)},
      feedback_sensor_parameters=RECORDED_CALIBRATION,
    )

  async def test_only_board_0_must_answer_and_each_other_board_is_asked_once(self) -> None:
    self.default_read_timeout = 0.25  # boards 10 to 12 never answer
    link = FakeLink(answers=make_answers(absent_boards=(10, 11, 12)))
    driver = await self.set_up_driver(link=link)
    assert driver.configuration.generation == "v3"
    assert driver.configuration.firmware_versions == {
      0: "4.20.6.2",
      10: None,
      11: None,
      12: None,
    }
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    for board in (10, 11, 12):
      assert sent.count((board, 1, b"")) == 1

  async def test_declared_configuration_that_disagrees_refuses_setup(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "declared.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump({"device": {**RECORDING["device"], "generation": "v3"}}, f)
      link = FakeLink(answers=make_answers())
      driver = self.make_driver(link=link, declared_configuration_json=path)
      with pytest.raises(MantisConfigurationError, match="generation: declared 'v3', device 'v4'"):
        async with driver:
          pass
    assert link.io.exited_count == 1
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    assert [command for board, command, _ in sent if board == 12] == [1]

  async def test_declared_board_that_does_not_answer_refuses_setup(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "declared.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump({"device": RECORDING["device"]}, f)
      link = FakeLink(answers=make_answers(absent_boards=(12,)))
      driver = self.make_driver(link=link, declared_configuration_json=path)
      driver.default_read_timeout = 0.25  # board 12 never answers
      with pytest.raises(
        MantisConfigurationError, match="board 12: declared '0.1.8 DBG', device did not"
      ):
        async with driver:
          pass
    assert driver.plate_clamp is None

  async def test_overlapping_lifespan_entry_is_rejected(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link)
    with pytest.raises(RuntimeError, match="already has an active lifespan"):
      async with driver.lifespan(skip_arm=True):
        pass
    assert link.io.entered_count == 1
    assert link.io.baudrates == [115_200]

  async def test_reentry_after_a_lost_link_closes_it_quietly_and_reopens_it(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    link = FakeLink(answers=make_answers())
    driver = self.make_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
    read = link.io.read.side_effect
    sent_before = 0
    async with driver.lifespan(skip_arm=True):
      link.io.read.side_effect = RECORDED_LINK_FAILURE
      with caplog.at_level(logging.ERROR, logger="pylabrobot.formulatrix.mantis.driver.master"):
        with anyio.fail_after(2.0):
          while driver._reader_running:
            await anyio.sleep(0.01)
      link.io.read.side_effect = read
      sent_before = len(link.written)
      caplog.clear()
      with caplog.at_level(logging.INFO, logger="pylabrobot.formulatrix.mantis"):
        pass

    caplog.clear()
    driver2 = self.make_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
    # Also verify exiting a lost-link lifespan logs the lost-link warning without stack trace.
    link_lost = FakeLink(answers=make_answers())
    lost_driver = self.make_driver(link=link_lost, declared_configuration_json=RECORDING_MANTIS_V4)
    saved_read = link_lost.io.read.side_effect
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="pylabrobot.formulatrix.mantis"):
      async with lost_driver.lifespan(skip_arm=True):
        link_lost.io.read.side_effect = RECORDED_LINK_FAILURE
        with anyio.fail_after(2.0):
          while lost_driver._reader_running:
            await anyio.sleep(0.01)
        caplog.clear()
        link_lost.io.read.side_effect = saved_read
        sent_before = len(link_lost.written)
      async with lost_driver.lifespan(skip_arm=True):
        assert lost_driver._reader_running
    del driver2
    warnings = [
      record
      for record in caplog.records
      if record.name.startswith("pylabrobot.formulatrix.mantis") and record.levelname != "INFO"
    ]
    assert len(warnings) == 1
    assert "the link was lost" in warnings[0].getMessage()
    assert all(record.exc_info is None for record in caplog.records)
    assert link_lost.io.exited_count == 2
    assert link_lost.io.entered_count == 2
    # The first frame after the failure is re-entry's own firmware read: nothing went to the dead link.
    assert parse_frame(frame=link_lost.written[sent_before])[1:] == (0, 1, b"")

  async def test_an_older_declared_file_takes_what_it_lacks_from_the_recording(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    older = {block: values for block, values in RECORDING.items() if block != "led_bar"}
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "older.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(older, f)
      link = FakeLink(answers=make_answers())
      caplog.clear()
      with caplog.at_level(logging.WARNING, logger="pylabrobot.formulatrix.mantis.driver.master"):
        driver = await self.set_up_driver(link=link, declared_configuration_json=path)
    warnings = [
      rec.getMessage()
      for rec in caplog.records
      if rec.name == "pylabrobot.formulatrix.mantis.driver.master"
      and rec.levelno >= logging.WARNING
    ]
    assert len(warnings) == 1
    assert f"led_bar from {RECORDING_MANTIS_V4}" in warnings[0]
    assert driver.led_bar is not None
    assert driver.led_bar.number_of_leds == 20
    assert driver.configuration.feedback_sensor_parameters == RECORDED_CALIBRATION

  async def test_saved_configuration_declares_the_same_device(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "mantis.json")
      link = FakeLink(answers=make_answers())
      driver = self.make_driver(link=link, declared_configuration_json=RECORDING_MANTIS_V4)
      async with driver.lifespan(skip_arm=True):
        driver.save_configuration(path=path)
      with open(path, encoding="utf-8") as f:
        saved = json.load(f)
      for block in ("plate_clamp", "led_bar"):
        assert saved[block] == RECORDING[block]
      await self.set_up_driver(
        link=FakeLink(answers=make_answers()), declared_configuration_json=path
      )


class TestRead(DriverTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.link = FakeLink(answers=make_answers())
    self.driver = await self.set_up_driver(link=self.link)

  async def test_reads(self) -> None:
    driver = self.driver
    cases: List[Tuple[int, int, str, Callable[[], Any], Any]] = [
      (0, 1, "", lambda: driver.request_firmware_version(board=0), "4.20.6.2"),
      (0, 2, "", lambda: driver.request_name(), "LiquidHandler"),
      (0, 10, "0200", lambda: driver.request_motor_limits(motor=2), (-160.0, 2300.0)),
    ]
    for board, command, request, read, expected in cases:
      result = await read()
      assert parse_frame(frame=self.link.written[-1])[1:] == (
        board,
        command,
        bytes.fromhex(request),
      )
      assert result == expected

  async def test_reads_refuse_unseen_motor_and_board(self) -> None:
    sent_before = len(self.link.written)
    with pytest.raises(ValueError):
      await self.driver.request_motor_limits(motor=3)
    with pytest.raises(ValueError):
      await self.driver.request_firmware_version(board=13)
    assert len(self.link.written) == sent_before

  async def test_event_arriving_with_the_reply_reaches_its_waiter(self) -> None:
    write = self.link.write

    async def event_then_reply(data: bytes) -> int:
      self.link.incoming.extend(bytes.fromhex(RECORDED_EVENT))
      return await write(data)

    self.link.io.write.side_effect = event_then_reply
    waiter = self.driver._expect_event(board=0, code=0x0201)
    assert await self.driver.request_motor_limits(motor=0) == (-4000.0, 4000.0)
    payload = await self.driver._wait_for_event(waiter=waiter, timeout=1.0)
    assert payload.hex() == RECORDED_EVENT[24:-4]
    assert self.driver._event_waiters == []

  async def test_unmatched_reply_is_ignored(self) -> None:
    write = self.link.write

    async def unmatched_then_reply(data: bytes) -> int:
      self.link.incoming.extend(bytes.fromhex(RECORDED_UNMATCHED_REPLY))
      return await write(data)

    self.link.io.write.side_effect = unmatched_then_reply
    assert await self.driver.request_name() == "LiquidHandler"

  async def test_failed_read_fails_the_command_in_flight_and_the_one_waiting(self) -> None:
    self.link.io.read.side_effect = OSError("device unplugged")
    outcomes: List[BaseException] = []

    async def run_request() -> None:
      try:
        await self.driver.request_name()
      except BaseException as exc:
        if isinstance(exc, anyio.get_cancelled_exc_class()):
          raise
        outcomes.append(exc)

    async with anyio.create_task_group() as tg:
      tg.start_soon(run_request)
      tg.start_soon(run_request)

    assert len(outcomes) == 2
    for outcome in outcomes:
      assert isinstance(outcome, MantisConnectionLostError)
      assert isinstance(outcome.__cause__, OSError)

  async def test_exit_fails_the_command_in_flight_and_every_event_waiter(self) -> None:
    link = FakeLink(answers=make_answers())
    driver = self.make_driver(link=link)
    command_error: List[BaseException] = []
    started = anyio.Event()

    async def run_inflight() -> None:
      started.set()
      try:
        await driver.request_name()
      except MantisNotConnectedError as exc:
        command_error.append(exc)

    async with anyio.create_task_group() as tg:
      async with driver.lifespan(skip_arm=True):
        del link.answers[(0, 2, b"")]
        waiter = driver._expect_event(board=0, code=0x0201)
        tg.start_soon(run_inflight)
        await started.wait()
        await anyio.sleep(0.02)

    assert len(command_error) == 1
    assert "stopped" in str(command_error[0])
    with pytest.raises(MantisNotConnectedError, match="stopped"):
      await driver._wait_for_event(waiter=waiter, timeout=1.0)

  async def test_unanswered_read_is_resent_with_its_packet_number_then_times_out(self) -> None:
    del self.link.answers[(0, 10, b"\x01\x00")]
    self.driver.default_read_timeout = 0.25
    with pytest.raises(MantisTimeoutError):
      await self.driver.request_motor_limits(motor=1)
    assert len(set(self.link.written[-3:])) == 1
    assert self.link.written[-4][4:6] != self.link.written[-1][4:6]


class TestMotorRead(DriverTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.link = FakeLink(answers=make_answers())
    self.driver = await self.set_up_driver(link=self.link)

  async def test_reads(self) -> None:
    driver = self.driver
    cases: List[Tuple[int, int, str, Callable[[], Any], Any]] = [
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
      result = await read()
      assert parse_frame(frame=self.link.written[-1])[1:] == (
        board,
        command,
        bytes.fromhex(request),
      )
      assert result == expected

  async def test_reads_refuse_unseen_motors(self) -> None:
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
      with pytest.raises(ValueError):
        await read()
    assert len(self.link.written) == sent_before

  async def test_absolute_encoder_read_again_until_valid(self) -> None:
    self.link.answers[(11, 30, b"\x00\x00")] = [
      bytes.fromhex(reply) for reply in ABSOLUTE_ENCODER_REPLIES_AFTER_POWER_ON
    ]
    sent_before = len(self.link.written)
    assert await self.driver.request_absolute_encoder_position(motor=0) == 185.99853515625
    sent = [parse_frame(frame=frame)[1:] for frame in self.link.written[sent_before:]]
    assert sent == [(11, 30, b"\x00\x00")] * 2

  async def test_absolute_encoder_never_valid_raises(self) -> None:
    self.link.answers[(11, 30, b"\x01\x00")] = [
      bytes.fromhex(ABSOLUTE_ENCODER_REPLIES_AFTER_POWER_ON[0])
    ]
    sent_before = len(self.link.written)
    with pytest.raises(MantisTimeoutError, match="no valid position in 5 reads"):
      await self.driver.request_absolute_encoder_position(motor=1)
    assert len(self.link.written) - sent_before == ABSOLUTE_ENCODER_READS


class TestSequencer(DriverTestBase):
  def make_sequencer_link(self) -> FakeLink:
    answers = make_answers()
    for (board, command, request), reply in RECORDED_SEQUENCER_REPLIES.items():
      answers[(board, command, bytes.fromhex(request))] = [bytes.fromhex(reply)]
    for request, reply in RECORDED_QUEUE_FULL_REPLIES.items():
      answers[(0, 65, bytes.fromhex(request))] = [bytes.fromhex(reply)]
    return FakeLink(
      answers=answers,
      events={
        (0, 65, bytes.fromhex("01000a00280001001f00")): [bytes.fromhex(RECORDED_ITEM_1_PLAYED)],
        (0, 63, b""): [bytes.fromhex(RECORDED_STOPPED_AFTER_STOP)],
        (0, 64, b""): [bytes.fromhex(RECORDED_STOPPED_AFTER_ABORT)],
      },
    )

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    await super()._enter_lifespan(stack, **kwargs)
    self.link = self.make_sequencer_link()
    self.driver = await self.set_up_driver(link=self.link)

  def sent_since(self, count: int) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in self.link.written[count:]]

  async def test_sequencer_commands_are_sent_once_and_return_their_reply(self) -> None:
    driver = self.driver
    cases = [
      (61, driver._unchecked_fw_clear_sequencer),
      (62, driver._unchecked_fw_start_sequencer),
      (63, driver._unchecked_fw_stop_sequencer),
      (64, driver._unchecked_fw_abort_sequencer),
    ]
    for command, send in cases:
      sent_before = len(self.link.written)
      assert await send() == 0
      assert self.sent_since(sent_before) == [(0, command, b"")]

  async def test_clear_returns_the_word_it_answers(self) -> None:
    self.link.answers[(0, 61, b"")] = [bytes.fromhex(RECORDED_CLEAR_REPLY_8)]
    assert await self.driver._unchecked_fw_clear_sequencer() == 8

  async def test_unanswered_start_is_not_resent(self) -> None:
    del self.link.answers[(0, 62, b"")]
    self.driver.default_read_timeout = 0.25
    sent_before = len(self.link.written)
    with pytest.raises(MantisTimeoutError):
      await self.driver._unchecked_fw_start_sequencer()
    assert self.sent_since(sent_before) == [(0, 62, b"")]

  async def test_stop_and_abort_are_followed_by_the_stopped_event(self) -> None:
    driver = self.driver
    cases = [
      (driver._unchecked_fw_stop_sequencer, (0x3236, 0)),
      (driver._unchecked_fw_abort_sequencer, (12, 0)),
    ]
    for send, expected in cases:
      waiter = driver._expect_event(board=0, code=SEQUENCE_STOPPED_EVENT)
      await send()
      payload = await driver._wait_for_event(waiter=waiter, timeout=1.0)
      assert decode_sequence_stopped(payload=payload) == expected

  async def test_item_played_with_its_reply_is_not_missed(self) -> None:
    driver = self.driver
    await driver._unchecked_fw_clear_sequencer()
    await driver._unchecked_fw_start_sequencer()
    sent_before = len(self.link.written)
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    assert item == 1
    assert self.sent_since(sent_before) == [(0, 65, bytes.fromhex("01000a00280001001f00"))]
    await driver._wait_for_queue_item(item=item, timeout=1.0)
    assert driver._event_waiters == []

  async def test_wait_for_the_last_item_passes_over_the_earlier_ones(self) -> None:
    driver = self.driver
    driver._next_item = 3
    items = [
      await driver._queue_ppi_write(duration=500, start_address=40, values=[0]) for _ in "345"
    ]
    assert items == [3, 4, 5]
    finished = anyio.Event()

    async def wait_for_last() -> None:
      await driver._wait_for_queue_item(item=5, timeout=1.0)
      finished.set()

    async with anyio.create_task_group() as tg:
      tg.start_soon(wait_for_last)
      await anyio.sleep(0.02)
      for frame in RECORDED_ITEMS_3_TO_5_PLAYED[:2]:
        self.link.incoming.extend(bytes.fromhex(frame))
      await anyio.sleep(0.05)
      assert not finished.is_set()
      self.link.incoming.extend(bytes.fromhex(RECORDED_ITEMS_3_TO_5_PLAYED[2]))

    assert finished.is_set()

  async def test_every_item_played_is_kept_for_the_dispense_progress(self) -> None:
    link = self.make_sequencer_link()
    driver = self.make_driver(link=link)
    async with driver.lifespan(skip_arm=True):
      assert driver._sequence_progress is None
      driver._next_item = 3
      for _ in "345":
        await driver._queue_ppi_write(duration=500, start_address=40, values=[0])
      for frame, item in zip(RECORDED_ITEMS_3_TO_5_PLAYED, [3, 4, 5]):
        link.incoming.extend(bytes.fromhex(frame))
        await anyio.sleep(0.02)
        assert driver._sequence_progress == item
      assert driver._last_drained_item == 5
    async with driver.lifespan(skip_arm=True):
      assert driver._sequence_progress is None

  async def test_full_queue_is_sent_again_under_the_next_item(self) -> None:
    self.driver._next_item = 368
    sent_before = len(self.link.written)
    item = await self.driver._queue_ppi_write(duration=1, start_address=40, values=[27])
    assert item == 373
    assert self.sent_since(sent_before) == [
      (0, 65, bytes.fromhex(request)) for request in RECORDED_QUEUE_FULL_REPLIES
    ]

  async def test_queue_staying_full_times_out(self) -> None:
    self.driver._next_item = 368
    sent_before = len(self.link.written)
    with mock.patch.object(master_module.anyio, "current_time", side_effect=[0.0, 60.0]):
      with pytest.raises(MantisTimeoutError, match="stayed full for 60.0 s"):
        await self.driver._queue_ppi_write(duration=1, start_address=40, values=[27])
    assert len(self.sent_since(sent_before)) == 1

  async def test_queue_write_refuses_out_of_range_arguments(self) -> None:
    cases: List[Tuple[int, int, List[int]]] = [
      (10, 120, [31]),
      (0, 40, [31]),
      (0x8000, 40, [31]),
      (10, 40, []),
      (10, 40, [0x10000]),
    ]
    sent_before = len(self.link.written)
    for duration, start_address, values in cases:
      with pytest.raises(ValueError):
        await self.driver._queue_ppi_write(
          duration=duration, start_address=start_address, values=values
        )
    assert len(self.link.written) == sent_before

  async def test_default_queue_full_timeout_is_checked_when_assigned(self) -> None:
    with pytest.raises(ValueError):
      self.driver.default_queue_full_timeout = 0.0
    assert self.driver.default_queue_full_timeout == 60.0

  async def test_motor_error_fails_every_motion_wait_at_once(self) -> None:
    driver = self.driver
    stopped = driver._expect_event(board=0, code=SEQUENCE_STOPPED_EVENT)
    move_done = driver._expect_event(board=0, code=MOVE_DONE_EVENT)
    caught: List[MantisMotionError] = []

    async def wait_queue() -> None:
      try:
        await driver._wait_for_queue_item(item=0x31D9, timeout=5.0)
      except MantisMotionError as exc:
        caught.append(exc)

    async with anyio.create_task_group() as tg:
      tg.start_soon(wait_queue)
      await anyio.sleep(0.02)
      for frame in RECORDED_MOTOR_ERROR_EVENTS:
        self.link.incoming.extend(bytes.fromhex(frame))

    assert len(caught) == 1
    assert "motor 0 reported error 9" in str(caught[0])
    assert (caught[0].motor, caught[0].code) == (0, 9)
    with pytest.raises(MantisMotionError):
      await driver._wait_for_event(waiter=move_done, timeout=1.0)
    payload = await driver._wait_for_event(waiter=stopped, timeout=1.0)
    assert decode_sequence_stopped(payload=payload) == (0x31D9, 0)

  async def test_sequencer_stop_is_a_warning_and_a_move_done_is_not(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=master_module.logger.name):
      for frame in RECORDED_MOTOR_ERROR_EVENTS:
        self.link.incoming.extend(bytes.fromhex(frame))
      await anyio.sleep(0.05)
    output = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "sequencer stopped at item" in output
    assert "moved" not in output

  async def test_event_not_decoded_is_logged_by_its_number(
    self, caplog: pytest.LogCaptureFixture
  ) -> None:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=master_module.logger.name):
      self.link.incoming.extend(bytes.fromhex(RECORDED_CODE_7FFF_FRAME))
      await anyio.sleep(0.05)
    output = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "event 0x7fff, not decoded" in output
