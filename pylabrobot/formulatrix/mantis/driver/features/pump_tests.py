"""Pump tests: the commands sent, against replies recorded from a Mantis v4."""

import asyncio
import json
import os
import struct
import tempfile
import unittest
from typing import Dict, List, Tuple
from unittest import mock

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import MantisPressureError, MantisTimeoutError
from pylabrobot.formulatrix.mantis.driver.features import pump as pump_module
from pylabrobot.formulatrix.mantis.driver.features.arm_tests import TURN_OFF
from pylabrobot.formulatrix.mantis.driver.features.led_bar_tests import (
  BLUE,
  GREEN,
  OFF,
  RED,
  SHOWN,
  START_IDLE_ANIMATION,
  STOP_ANIMATION,
  bar_states,
  color_write,
  led_bar_frames,
  make_led_bar_answers,
)
from pylabrobot.formulatrix.mantis.driver.features.pump import PressureController, Pump
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master import MantisDriver
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  PUMP_HALT,
  RECORDED_CALIBRATION,
  RECORDING,
  DriverTestCase,
  FakeLink,
  make_answers,
)

Sent = Tuple[int, int, bytes]


def frames(*commands: str) -> List[Sent]:
  """Commands written as "board:command:payload hex"."""
  sent = []
  for command in commands:
    board, number, payload = command.split(":")
    sent.append((int(board), int(number), bytes.fromhex(payload)))
  return sent


# The pressure board's reads, as sent.
CALIBRATION_READS = [(10, 20, bytes.fromhex(request)) for request in ("0000", "0100", "0200")]
# Each line's pressure with the pump off, as recorded; the vacuum's as in the default replies.
RESTING_READINGS = {"0000": "000000003abcf23f", "0100": "000000004f60f23f"}
# With the pump running: the chip controller enabled and the pump on, as recorded.
CHIP_CONTROLLER_ENABLED = (10, 12, bytes.fromhex("0100"), bytes.fromhex("0100"))
PUMP_ON = (10, 14, b"", bytes.fromhex("0100"))
# The chip and vacuum controllers' status while regulating: settled, then unsettled, as recorded.
CHIP_CONTROLLER_SETTLED = (10, 16, bytes.fromhex("0100"), bytes.fromhex("0100"))
VACUUM_CONTROLLER_UNSETTLED = (10, 16, bytes.fromhex("0200"), bytes.fromhex("0200"))
# The tare with the lines reading 0 vented: nothing is written to the calibration.
TARE_AT_ZERO = frames(
  "10:14:",
  "10:20:0000", "10:12:0000", "10:13:00000000", "10:32:00000000", "10:34:00001027",
  "10:30:0000", "10:30:0000", "10:34:00000000",
  "10:20:0100", "10:12:0100", "10:13:01000000", "10:32:01000000", "10:34:01001027",
  "10:30:0100", "10:30:0100", "10:34:01000000",
)  # fmt: skip
# A tare on the device: each line's readings vented and the offset written, as recorded.
TARE_READINGS = {
  "0000": ["000000007f70f43f", "000000007f70f43f"],
  "0100": ["0000000080a2ef3f", "000000002a4bef3f"],
}
TARE_OFFSET_WRITES: Dict[PressureController, str] = {
  "bottle": "00000000004049d0853f000000c07fb72dc0",
  "chip": "010000000080d1d5853f000000a0664c2dc0",
}
# The pump started after setup: on, then each controller enabled at the self-test's last target.
PUMP_START = frames(
  "10:15:0100",
  "10:13:00000100", "10:11:00000000000000003440",
  "10:13:01000100", "10:11:01000000000000003440",
  "10:13:02000100", "10:11:02000000000000002ec0",
)  # fmt: skip
# The pump stopped: every controller disabled, the pump off, the reservoir released.
PUMP_STOP = frames(
  "10:13:00000000", "10:13:01000000", "10:13:02000000", "10:15:0000",
  "10:34:0000204e", "10:32:0000204e", "10:34:00000000", "10:32:00000000",
)  # fmt: skip
# The chip at 12 psi and the vacuum at -14 inHg, released first, each set and read settled.
CHIP_AT_12 = frames("10:13:01000100", "10:11:01000000000000002840", "10:16:0100", "10:30:0100")
VACUUM_AT_MINUS_14 = frames(
  "10:13:02000100", "10:11:02000000000000000000", "10:11:02000000000000002cc0",
  "10:16:0200", "10:30:0200",
)  # fmt: skip
# Both lines after a vent on the device: the bottle at 0 psi, the chip at 0.01 psi.
VENTED_READINGS = {"0000": "0000000000000000", "0100": "0000000000d6853f"}
# The chip at 20 psi, as read in the self-test.
CHIP_AT_20_READING = "000000c08c083440"
# The self-test's progress on the LED bar: round(20 * point / 14) LEDs lit after each of its 14
# test points.
SELF_TEST_PROGRESS = [
  [GREEN] * lit + [OFF] * (20 - lit) for lit in (1, 3, 4, 6, 7, 9, 10, 11, 13, 14, 16, 17, 19)
] + [GREEN]
# What ends a v4's setup on the LED bar: "setup_done", then the idle animation.
SETUP_DONE = [STOP_ANIMATION, color_write(colors=[GREEN] * 20), START_IDLE_ANIMATION]

# The connect self-test as a Mantis v4 was sent it, from the sequencer started after the tare:
# the pressure board's writes and the sequencer's clears, starts and queued valve states.
RECORDED_SELF_TEST_WRITES = (
  "0:61: 0:62: 0:65:01000a00280001001f00",
  "10:15:0100 10:13:00000100 10:13:01000100 10:13:02000100 10:13:01000100",
  "10:11:01000000000000002840 10:13:02000100 10:11:02000000000000000000",
  "10:11:02000000000000002cc0 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000002840 10:13:00000000 10:13:01000000 10:13:02000000 10:15:0000",
  "10:34:0000204e 10:32:0000204e 10:34:00000000 10:32:00000000",
  "0:61: 0:62: 0:65:02000a00280001001100",
  "0:61: 0:62: 0:65:0300f401280001000000 0:65:0400f401280001000000 0:65:0500f401280001000000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000002840",
  "10:13:02000100 10:11:02000000000000002cc0 10:13:01000100 10:11:01000000000000001440",
  "10:13:01000100 10:11:01000000000000002440 10:13:01000100 10:11:01000000000000002e40",
  "10:13:01000100 10:11:01000000000000003440 10:13:01000100 10:11:01000000000000000000",
  "0:65:0600f401280001001f00 10:13:02000100 10:11:02000000000000000000 10:11:020000000000000014c0",
  "10:13:02000100 10:11:02000000000000000000 10:11:020000000000000024c0 10:13:02000100",
  "10:11:02000000000000000000 10:11:02000000000000002ec0 10:13:02000100",
  "10:11:02000000000000000000 10:11:02000000000000000000 0:65:0700f401280001000000 10:13:00000100",
  "10:11:00000000000000001440 10:13:00000100 10:11:00000000000000002440 10:13:00000100",
  "10:11:00000000000000002e40 10:13:00000100 10:11:00000000000000003440 10:13:00000100",
  "10:11:00000000000000000000 10:13:00000000 10:13:01000000 10:13:02000000 10:15:0000",
  "10:34:0000204e 10:32:0000204e 10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000000000 10:13:02000100 10:11:02000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:01000000 10:13:00000000 10:13:01000000 10:13:02000000",
  "10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000000000 10:13:01000100 10:13:00000000 10:13:01000000",
  "10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e 10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000000000 10:13:02000100",
  "10:11:02000000000000000000 10:11:02000000000000002ec0 10:13:02000000 10:13:00000000",
  "10:13:01000000 10:13:02000000 10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:02000100 10:11:02000000000000000000",
  "10:13:00000000 10:13:01000000 10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e",
  "10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000002ec0 10:13:01000100",
  "10:11:01000000000000003440 10:13:01000000 10:13:00000000 10:13:01000000 10:13:02000000",
  "10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:01000100 10:13:00000000 10:13:01000000",
  "10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e 10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000002ec0 10:13:02000100",
  "10:11:02000000000000000000 10:11:02000000000000002ec0 10:13:02000000 10:13:00000000",
  "10:13:01000000 10:13:02000000 10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:02000100 10:11:02000000000000000000",
  "10:13:00000000 10:13:01000000 10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e",
  "10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000002ec0 10:13:01000100",
  "10:11:01000000000000003440 10:13:01000000 10:13:00000000 10:13:01000000 10:13:02000000",
  "10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:01000100 10:13:00000000 10:13:01000000",
  "10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e 10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000002ec0 10:13:02000100",
  "10:11:02000000000000000000 10:11:02000000000000002ec0 10:13:02000000 10:13:00000000",
  "10:13:01000000 10:13:02000000 10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:02000100 10:11:02000000000000000000",
  "10:13:00000000 10:13:01000000 10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e",
  "10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000002ec0 10:13:01000100",
  "10:11:01000000000000003440 10:13:01000000 10:13:00000000 10:13:01000000 10:13:02000000",
  "10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:01000100 10:13:00000000 10:13:01000000",
  "10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e 10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000002ec0 10:13:02000100",
  "10:11:02000000000000000000 10:11:02000000000000002ec0 10:13:02000000 10:13:00000000",
  "10:13:01000000 10:13:02000000 10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:02000100 10:11:02000000000000000000",
  "10:13:00000000 10:13:01000000 10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e",
  "10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000000000 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000002ec0 10:13:00000100",
  "10:11:00000000000000003440 10:13:00000000 10:13:00000000 10:13:01000000 10:13:02000000",
  "10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000003440 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:00000100 10:13:00000000 10:13:01000000",
  "10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e 10:34:00000000 10:32:00000000",
  "0:61: 0:62: 10:15:0100 10:13:00000100 10:11:00000000000000003440 10:13:01000100",
  "10:11:01000000000000003440 10:13:02000100 10:11:02000000000000002ec0 10:13:02000100",
  "10:11:02000000000000000000 10:11:02000000000000002ec0 10:13:02000000 10:13:00000000",
  "10:13:01000000 10:13:02000000 10:15:0000",
  "10:15:0100 10:13:00000100 10:11:00000000000000003440 10:13:01000100 10:11:01000000000000003440",
  "10:13:02000100 10:11:02000000000000002ec0 10:13:02000100 10:11:02000000000000000000",
  "10:13:00000000 10:13:01000000 10:13:02000000 10:15:0000 10:34:0000204e 10:32:0000204e",
  "10:34:00000000 10:32:00000000",
)
# Its queued valve states: each item's reply, and the item played reports sent after it.
SELF_TEST_QUEUE_REPLIES = {
  "01000a00280001001f00": "c7000000",
  "02000a00280001001100": "c7000000",
  "0300f401280001000000": "c7000000",
  "0400f401280001000000": "c7000000",
  "0500f401280001000000": "c6000000",
  "0600f401280001001f00": "c7000000",
  "0700f401280001000000": "c7000000",
}
SELF_TEST_ITEMS_PLAYED = {
  "01000a00280001001f00": ["12000000650100000000100301000000c800ae02"],
  "02000a00280001001100": ["120000007d0100000000100302000000c800b502"],
  "0500f401280001000000": [
    "12000000820100000000100303000200c6004702",
    "12000000820100000000100304000100c7004202",
    "12000000820100000000100305000000c8004d02",
  ],
  "0600f401280001001f00": ["12000000040200000000100306000000c800c801"],
  "0700f401280001000000": ["12000000810200000000100307000000c8004c01"],
}
# Each line's reading at each test point, settled, by (controller, target), as recorded.
SELF_TEST_SETTLED_READINGS: Dict[Tuple[int, float], str] = {
  (1, 5.0): "00000040a1e51440",
  (1, 10.0): "000000e0dabe2340",
  (1, 15.0): "0000002015fb2d40",
  (1, 20.0): "000000c08c083440",
  (1, 0.0): "000000005863cf3f",
  (2, -5.0): "00000000dff013c0",
  (2, -10.0): "000000004efb23c0",
  (2, -15.0): "00000080b6772ec0",
  (2, 0.0): "00000000c0d7e1bf",
  (0, 5.0): "00000080031c1440",
  (0, 10.0): "000000c077212440",
  (0, 15.0): "000000c0bd4a2e40",
  (0, 20.0): "000000201df33340",
  (0, 0.0): "0000000000d085bf",
}
SETTLED = "0100"


class FakeClock:
  """The pump's clock: each hold advances it by its seconds, at once."""

  def __init__(self):
    self.now = 0.0
    self.holds: List[float] = []

  def monotonic(self) -> float:
    return self.now

  async def sleep(self, *, seconds: float) -> None:
    self.holds.append(seconds)
    self.now += seconds
    await asyncio.sleep(0)


class SelfTestLink(FakeLink):
  """Answers the self-test: a line's pressure read with its recorded reading at the last test
  point set, and every controller settled; the LED bar's recorded colour writes answered shown."""

  def __init__(self):
    answers = make_led_bar_answers()
    for write in frames(*" ".join(RECORDED_SELF_TEST_WRITES).split()):
      if write[0] == 10:
        answers[write] = [b""]
    for request, reply in SELF_TEST_QUEUE_REPLIES.items():
      answers[(0, 65, bytes.fromhex(request))] = [bytes.fromhex(reply)]
    for controller in ("0000", "0100", "0200"):
      answers[(10, 16, bytes.fromhex(controller))] = [bytes.fromhex(SETTLED)]
    super().__init__(
      answers=answers,
      events={
        (0, 65, bytes.fromhex(request)): [bytes.fromhex(frame) for frame in played]
        for request, played in SELF_TEST_ITEMS_PLAYED.items()
      },
    )

  async def write(self, data: bytes) -> int:
    _, board, command, payload = parse_frame(frame=data)
    if (board, command) == (10, 11):
      controller, target = struct.unpack("<hd", payload)
      reading = SELF_TEST_SETTLED_READINGS.get((controller, target))
      if reading is not None:
        self.answers[(10, 30, payload[:2])] = [bytes.fromhex(reading)]
    return await super().write(data)


class PumpTestCase(DriverTestCase):
  async def set_up_pump(self, *, link: FakeLink, **kwargs) -> Tuple[MantisDriver, Pump]:
    self.link = link
    driver = await self.set_up_driver(link=link, **kwargs)
    assert driver.pump is not None
    self.sent_before = len(link.written)
    return driver, driver.pump

  def sent(self) -> List[Sent]:
    return [parse_frame(frame=frame)[1:] for frame in self.link.written[self.sent_before :]]

  def use_fake_clock(self) -> FakeClock:
    """Give every pump the fake clock; after ``make_driver``, whose holds pass at once."""
    clock = FakeClock()

    async def sleep(_: Pump, *, seconds: float) -> None:
      await clock.sleep(seconds=seconds)

    for patch in (
      mock.patch.object(pump_module, "time", clock),
      mock.patch.object(Pump, "_sleep", sleep),
    ):
      patch.start()
      self.addCleanup(patch.stop)
    return clock

  def answer_writes(self, *, writes: List[Sent]) -> None:
    """Answer each write with no payload, as each was recorded."""
    for write in writes:
      self.link.answers[write] = [b""]


class PumpReadTests(PumpTestCase):
  async def asyncSetUp(self):
    self.driver, self.pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))

  async def test_measure_pressure(self):
    for request, reading in RESTING_READINGS.items():
      self.link.answers[(10, 30, bytes.fromhex(request))] = [bytes.fromhex(reading)]
    cases: List[Tuple[PressureController, str, float]] = [
      ("bottle", "0000", 1.1709537506103516),
      ("chip", "0100", 1.148512840270996),
      ("vacuum", "0200", -0.557586669921875),
    ]
    for controller, request, expected in cases:
      with self.subTest(controller=controller):
        self.assertEqual(await self.pump.measure_pressure(controller=controller), expected)
        self.assertEqual(self.sent()[-1], (10, 30, bytes.fromhex(request)))

  async def test_request_feedback_sensor_parameters(self):
    self.assertEqual(
      await self.pump.request_feedback_sensor_parameters(controller="vacuum"),
      RECORDED_CALIBRATION["vacuum"],
    )
    self.assertEqual(self.sent(), [CALIBRATION_READS[2]])

  async def test_pump_off_reads(self):
    self.assertEqual(await self.pump.request_controller_status(controller="bottle"), 0)
    self.assertFalse(await self.pump.request_controller_enabled(controller="bottle"))
    self.assertFalse(await self.pump.request_pump_on())
    self.assertEqual(self.sent(), [(10, 16, bytes(2)), (10, 12, bytes(2)), (10, 14, b"")])

  RUNNING_READS = (
    CHIP_CONTROLLER_ENABLED,
    PUMP_ON,
    CHIP_CONTROLLER_SETTLED,
    VACUUM_CONTROLLER_UNSETTLED,
  )

  async def test_pump_running_reads(self):
    for board, command, request, reply in self.RUNNING_READS:
      self.link.answers[(board, command, request)] = [reply]
    self.assertTrue(await self.pump.request_controller_enabled(controller="chip"))
    self.assertTrue(await self.pump.request_pump_on())
    self.assertEqual(await self.pump.request_controller_status(controller="chip"), 1)
    self.assertEqual(await self.pump.request_controller_status(controller="vacuum"), 2)
    self.assertEqual(
      self.sent(),
      [(board, command, request) for board, command, request, _ in self.RUNNING_READS],
    )
    self.link.answers[(10, 14, b"")] = [bytes(2)]  # off again for the stop

  async def test_unknown_controller_sends_nothing(self):
    for read in (
      self.pump.measure_pressure,
      self.pump.request_feedback_sensor_parameters,
      self.pump.request_controller_status,
      self.pump.request_controller_enabled,
    ):
      with self.subTest(read=read.__name__):
        with self.assertRaises(ValueError):
          await read(controller="air")  # type: ignore[arg-type]
    self.assertEqual(self.sent(), [])

  async def test_defaults_are_checked_when_assigned(self):
    for name in ("default_settle_timeout", "default_vent_duration"):
      with self.subTest(name=name):
        with self.assertRaises(ValueError):
          setattr(self.pump, name, 0.0)
        self.assertEqual(getattr(self.pump, name), 2.0)


class TareTests(PumpTestCase):
  async def test_setup_tares_and_writes_nothing_at_zero(self):
    link = FakeLink(answers=make_answers())
    driver, _ = await self.set_up_pump(link=link)
    self.assertEqual(driver.configuration.feedback_sensor_parameters, RECORDED_CALIBRATION)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    self.assertEqual(
      [read for read in sent if read[0] == 10], [(10, 1, b"")] + CALIBRATION_READS + TARE_AT_ZERO
    )
    self.assertEqual(sent[-5:], [(0, 61, b""), (0, 62, b"")] + SETUP_DONE)

  async def test_the_vented_reading_is_taken_off_the_offset(self):
    driver, pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))
    for request, readings in TARE_READINGS.items():
      self.link.answers[(10, 30, bytes.fromhex(request))] = [
        bytes.fromhex(reading) for reading in readings
      ]
    for write in TARE_OFFSET_WRITES.values():
      self.link.answers[(10, 21, bytes.fromhex(write))] = [b""]
    clock = self.use_fake_clock()
    await pump.tare_pressure_sensors()
    expected = list(TARE_AT_ZERO)
    expected.insert(8, (10, 21, bytes.fromhex(TARE_OFFSET_WRITES["bottle"])))
    expected.insert(17, (10, 21, bytes.fromhex(TARE_OFFSET_WRITES["chip"])))
    self.assertEqual(self.sent(), expected)
    self.assertEqual(clock.holds, [pump_module.TUNE_DELAY] * 4)
    for controller, write in TARE_OFFSET_WRITES.items():
      with self.subTest(controller=controller):
        scale, offset = struct.unpack("<dd", bytes.fromhex(write)[2:])
        self.assertEqual(
          driver.configuration.feedback_sensor_parameters[controller], (scale, offset)
        )
        self.assertAlmostEqual(offset - RECORDED_CALIBRATION[controller][1], -1.2775, delta=0.3)
    self.assertEqual(
      driver.configuration.feedback_sensor_parameters["vacuum"], RECORDED_CALIBRATION["vacuum"]
    )

  async def test_refused_with_the_pump_on(self):
    _, pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))
    self.link.answers[(10, 14, b"")] = [PUMP_ON[3]]
    with self.assertRaisesRegex(MantisPressureError, "the pump runs"):
      await pump.tare_pressure_sensors()
    self.assertEqual(self.sent(), [(10, 14, b"")])
    self.link.answers[(10, 14, b"")] = [bytes(2)]  # off again for the stop

  async def test_initialize_with_the_pump_on_turns_it_off(self):
    _, pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))
    self.use_fake_clock()
    self.link.answers[(10, 14, b"")] = [PUMP_ON[3]]
    with self.assertRaisesRegex(MantisPressureError, "the pump runs"):
      await pump.initialize()
    self.assertEqual(self.sent(), [(10, 14, b"")] + PUMP_STOP)
    self.link.answers[(10, 14, b"")] = [bytes(2)]  # off again for the stop

  async def test_readings_that_never_agree_write_nothing(self):
    driver, pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))
    alternating = [
      bytes.fromhex(reading) for reading in (TARE_READINGS["0000"][0], CHIP_AT_20_READING)
    ]
    self.link.answers[(10, 30, bytes(2))] = alternating * 20
    self.use_fake_clock()
    with self.assertLogs(pump_module.logger, level="WARNING") as logs:
      await pump.tare_pressure_sensors()
    self.assertTrue(any("bottle sensor did not read steady" in line for line in logs.output))
    self.assertNotIn(21, [command for _, command, _ in self.sent()])
    self.assertEqual(driver.configuration.feedback_sensor_parameters, RECORDED_CALIBRATION)
    self.assertEqual(self.sent()[-1], (10, 34, bytes.fromhex("01000000")))

  async def test_a_failure_closes_the_vent(self):
    _, pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))
    del self.link.answers[(10, 30, bytes(2))]
    with self.assertRaises(MantisTimeoutError):
      await pump.tare_pressure_sensors()
    self.assertEqual(self.sent()[-1], (10, 34, bytes.fromhex("00000000")))

  async def test_a_v3_is_not_tared(self):
    link = FakeLink(answers=make_answers(absent_boards=(11,)))
    driver, _ = await self.set_up_pump(link=link)
    self.assertEqual(driver.configuration.generation, "v3")
    sent = [parse_frame(frame=frame)[1:] for frame in link.written[: self.sent_before]]
    self.assertEqual([read for read in sent if read[0] == 10], [(10, 1, b"")] + CALIBRATION_READS)
    self.assertEqual(led_bar_frames(written=link.written), [])


class PumpTests(PumpTestCase):
  async def asyncSetUp(self):
    self.driver, self.pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))
    self.clock = self.use_fake_clock()

  async def test_start_enables_each_controller_at_its_last_target(self):
    await self.pump.start_pump()
    self.assertEqual(self.sent(), PUMP_START)
    self.assertEqual(self.clock.holds, [pump_module.START_PUMP_WAIT])
    await self.pump.stop_pump()
    self.assertEqual(self.sent(), PUMP_START + PUMP_STOP)
    self.assertEqual(self.clock.holds, [pump_module.START_PUMP_WAIT] * 2)

  async def test_starts_are_counted(self):
    await self.pump.start_pump()
    await self.pump.start_pump()
    await self.pump.stop_pump()
    self.assertEqual(self.sent(), PUMP_START)
    await self.pump.stop_pump()
    self.assertEqual(self.sent(), PUMP_START + PUMP_STOP)

  async def test_a_failed_start_turns_the_pump_off(self):
    chip_enabled = self.link.answers.pop((10, 13, bytes.fromhex("01000100")))
    with self.assertRaises(MantisTimeoutError):
      await self.pump.start_pump()
    self.assertEqual(self.sent(), PUMP_START[:4] + PUMP_STOP[:4])
    self.link.answers[(10, 13, bytes.fromhex("01000100"))] = chip_enabled
    await self.pump.start_pump()
    self.assertEqual(self.sent()[8:], PUMP_START)
    await self.pump.stop_pump()

  async def test_a_failed_stop_runs_every_step_then_raises_the_first_failure(self):
    await self.pump.start_pump()
    del self.link.answers[(10, 13, bytes.fromhex("00000000"))]
    with self.assertLogs(pump_module.logger, level="WARNING"):
      with self.assertRaisesRegex(MantisTimeoutError, "board 10 command 13"):
        await self.pump.stop_pump()
    self.assertEqual(self.sent()[-7:], PUMP_STOP[1:])

  async def test_a_reservoir_release_that_fails_closes_its_valves_then_raises(self):
    await self.pump.start_pump()
    del self.link.answers[(10, 32, bytes.fromhex("0000204e"))]
    with self.assertLogs(pump_module.logger, level="WARNING"):
      with self.assertRaisesRegex(MantisTimeoutError, "board 10 command 32"):
        await self.pump.stop_pump()
    self.assertEqual(self.sent()[-3:], PUMP_STOP[-3:-2] + PUMP_STOP[-2:])

  async def test_pressure_control_sets_its_target_and_waits_until_settled(self):
    for request in ("0100", "0200"):
      self.link.answers[(10, 16, bytes.fromhex(request))] = [bytes.fromhex(SETTLED)]
    self.answer_writes(writes=CHIP_AT_12[1:2] + VACUUM_AT_MINUS_14[1:3])
    await self.pump.start_pump()
    sent_before = len(self.sent())
    await self.pump.start_pressure_control(controller="chip", pressure=12.0)
    await self.pump.start_pressure_control(controller="vacuum", pressure=-14.0)
    self.assertEqual(self.sent()[sent_before:], CHIP_AT_12 + VACUUM_AT_MINUS_14)
    self.assertEqual(self.clock.holds[1:], [pump_module.RELEASE_VACUUM_DELAY])
    await self.pump.stop_pump()
    await self.pump.start_pump()
    self.assertEqual(self.sent()[-3], (10, 11, bytes.fromhex("01000000000000002840")))
    self.assertEqual(self.sent()[-1], (10, 11, bytes.fromhex("02000000000000002cc0")))
    await self.pump.stop_pump()

  async def test_pressure_control_refuses_before_sending(self):
    cases = [
      ({"controller": "chip", "pressure": -1.0}, ValueError),
      ({"controller": "chip", "pressure": 21.0}, ValueError),
      ({"controller": "bottle", "pressure": 200.0}, ValueError),
      ({"controller": "vacuum", "pressure": 1.0}, ValueError),
      ({"controller": "vacuum", "pressure": -17.0}, ValueError),
      ({"controller": "air", "pressure": 1.0}, ValueError),
      ({"controller": "chip", "pressure": 12.0, "timeout": 0.0}, ValueError),
      ({"controller": "chip", "pressure": 12.0}, MantisPressureError),
    ]
    for arguments, error in cases:
      with self.subTest(arguments=arguments):
        with self.assertRaises(error):
          await self.pump.start_pressure_control(**arguments)  # type: ignore[arg-type]
    self.assertEqual(self.sent(), [])

  async def test_a_controller_that_does_not_settle_raises(self):
    self.link.answers[(10, 16, bytes.fromhex("0100"))] = [VACUUM_CONTROLLER_UNSETTLED[3]]
    self.answer_writes(writes=CHIP_AT_12[1:2])
    await self.pump.start_pump()
    with self.assertRaisesRegex(MantisPressureError, "did not settle at 12.0 within 2.0 s"):
      await self.pump.start_pressure_control(controller="chip", pressure=12.0)
    polls = [hold for hold in self.clock.holds if hold == pump_module.PRESSURE_SETTLE_POLL_INTERVAL]
    # 40 polls of 0.05 s sum to just under 2 s in floating point, so a 41st is read.
    self.assertEqual(len(polls), 41)
    await self.pump.stop_pump()

  async def test_a_controller_that_turns_off_raises_at_once(self):
    self.answer_writes(writes=frames("10:11:00000000000000001440"))
    await self.pump.start_pump()
    with self.assertRaisesRegex(MantisPressureError, "turned off while settling"):
      await self.pump.start_pressure_control(controller="bottle", pressure=5.0)
    self.assertEqual(self.clock.holds, [pump_module.START_PUMP_WAIT])
    await self.pump.stop_pump()

  async def test_vent(self):
    for request, reading in VENTED_READINGS.items():
      self.link.answers[(10, 30, bytes.fromhex(request))] = [bytes.fromhex(reading)]
    readings = await self.pump.vent()
    self.assertEqual(self.sent(), PUMP_HALT[5:])
    self.assertEqual(readings, {"bottle": 0.0, "chip": 0.010662078857421875})
    self.assertEqual(self.clock.holds, [self.pump.default_vent_duration])

  async def test_a_line_left_under_pressure_is_logged(self):
    self.link.answers[(10, 30, bytes.fromhex("0100"))] = [bytes.fromhex(CHIP_AT_20_READING)]
    with self.assertLogs(pump_module.logger, level="WARNING") as logs:
      readings = await self.pump.vent()
    self.assertAlmostEqual(readings["chip"], 20.03, places=2)
    self.assertTrue(any("not vented" in line for line in logs.output))

  async def test_a_vent_that_fails_to_open_closes_both_lines(self):
    del self.link.answers[(10, 34, bytes.fromhex("0100204e"))]
    with self.assertRaises(MantisTimeoutError):
      await self.pump.vent()
    self.assertEqual(self.sent()[-4:], PUMP_HALT[9:13])


class StopTests(PumpTestCase):
  async def test_stop_turns_the_pump_off_and_vents_after_the_motion_halt(self):
    driver, pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))
    await pump.start_pump()
    await driver.stop()
    sent = self.sent()[len(PUMP_START) :]
    self.assertEqual([command for _, command, _ in sent[:4]], [64, 24, 24, 24])
    self.assertEqual(sent[4:], PUMP_HALT + [(12, 33, b"")] + TURN_OFF)
    self.assertFalse({41, 42} & {command for board, command, _ in sent if board == 10})
    self.assertEqual(pump._pump_starts, 0)

  async def test_a_pump_that_does_not_turn_off_still_vents(self):
    driver, pump = await self.set_up_pump(link=FakeLink(answers=make_answers()))
    del self.link.answers[(10, 15, bytes.fromhex("0000"))]
    with self.assertLogs(pump_module.logger, level="ERROR"):
      await driver.stop()
    self.assertEqual(self.sent()[4:], PUMP_HALT + [(12, 33, b"")] + TURN_OFF)
    self.link.io.stop.assert_awaited()

  async def test_a_failed_setup_turns_the_pump_off_and_vents(self):
    link = SelfTestLink()
    del link.answers[(10, 11, bytes.fromhex("01000000000000002e40"))]
    driver = self.make_driver(link=link)
    self.use_fake_clock()
    with self.assertRaises(MantisTimeoutError):
      await driver.setup(skip_arm=True)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    failed_at = sent.index((10, 11, bytes.fromhex("01000000000000002e40")))
    self.assertEqual(sent[failed_at + 1 : failed_at + 9], PUMP_STOP)
    self.assertEqual(sent[-len(PUMP_HALT) - 3 :], PUMP_HALT + [(12, 33, b"")] + TURN_OFF)


class SelfTestTests(PumpTestCase):
  def setUp(self):
    # Windows of 5 readings rather than 40, to keep the test short.
    window = mock.patch.object(pump_module, "SETTLE_CHECK_DURATION", 0.1)
    window.start()
    self.addCleanup(window.stop)

  async def test_the_self_test_sends_the_recorded_writes(self):
    link = SelfTestLink()
    driver = self.make_driver(link=link)
    clock = self.use_fake_clock()
    with self.assertNoLogs(pump_module.logger, level="WARNING"):
      await driver.setup(skip_arm=True)
    self.addAsyncCleanup(driver.stop)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    first = sent.index((0, 61, b""))
    writes = [
      write
      for write in sent[first:]
      if write[:2] in {(10, 11), (10, 13), (10, 15), (10, 32), (10, 34), (0, 61), (0, 62), (0, 65)}
    ]
    self.assertEqual(writes, frames(*" ".join(RECORDED_SELF_TEST_WRITES).split()))
    self.assertEqual(sent[first:].count((10, 16, bytes.fromhex("0100"))), 5)
    holds = [hold for hold in clock.holds if hold == pump_module.RELEASE_RESERVOIR_DELAY]
    self.assertEqual(len(holds), 12)  # the connect block, the ramp and each toggle's release

  async def test_points_that_do_not_settle_are_logged_and_setup_completes(self):
    link = SelfTestLink()
    for controller in ("0000", "0100", "0200"):
      link.answers[(10, 16, bytes.fromhex(controller))] = [VACUUM_CONTROLLER_UNSETTLED[3]]
    driver = self.make_driver(link=link)
    self.use_fake_clock()
    with mock.patch.object(pump_module, "SETTLE_CHECK_TIMEOUT", 0.0):
      with self.assertLogs(pump_module.logger, level="WARNING") as logs:
        await driver.setup(skip_arm=True)
    self.addAsyncCleanup(driver.stop)
    self.assertEqual(sum("did not settle" in line for line in logs.output), 14)

  async def test_skipped(self):
    link = SelfTestLink()
    driver = self.make_driver(link=link)
    await driver.setup(skip_arm=True, skip_self_test=True)
    self.addAsyncCleanup(driver.stop)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    self.assertEqual(sent[-5:], [(0, 61, b""), (0, 62, b"")] + SETUP_DONE)


class LedBarTests(PumpTestCase):
  async def test_the_pump_setup_runs_while_the_bar_shows_setup(self):
    link = FakeLink(answers=make_answers())
    await self.set_up_pump(link=link)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    blue_at = sent.index(color_write(colors=[BLUE] * 20))
    self.assertEqual(sent[blue_at + 1], TARE_AT_ZERO[0])
    self.assertEqual(sent[-5:], [(0, 61, b""), (0, 62, b"")] + SETUP_DONE)
    self.assertEqual(bar_states(written=link.written), [BLUE, GREEN, "idle"])

  async def test_a_failed_pump_setup_shows_red_then_turns_off(self):
    link = SelfTestLink()
    link.answers[color_write(colors=[RED] * 20)] = [bytes.fromhex(SHOWN)]
    del link.answers[(10, 11, bytes.fromhex("01000000000000002e40"))]
    driver = self.make_driver(link=link)
    self.use_fake_clock()
    with self.assertRaises(MantisTimeoutError):
      await driver.setup(skip_arm=True)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    turned_off_at = sent.index((10, 11, bytes.fromhex("01000000000000002e40"))) + 9
    self.assertEqual(
      sent[turned_off_at : turned_off_at + 2], [STOP_ANIMATION, color_write(colors=[RED] * 20)]
    )
    self.assertEqual(bar_states(written=link.written), [BLUE, *SELF_TEST_PROGRESS[:2], RED, OFF])

  async def test_the_self_test_shows_its_progress_one_step_per_test_point(self):
    link = SelfTestLink()
    driver = self.make_driver(link=link)
    self.use_fake_clock()
    with mock.patch.object(pump_module, "SETTLE_CHECK_DURATION", 0.1):
      await driver.setup(skip_arm=True)
    self.addAsyncCleanup(driver.stop)
    self.assertEqual(bar_states(written=link.written), [BLUE, *SELF_TEST_PROGRESS, GREEN, "idle"])

  async def test_without_show_actions_the_self_test_shows_no_progress(self):
    link = SelfTestLink()
    driver = self.make_driver(link=link, show_actions=False)
    self.use_fake_clock()
    with mock.patch.object(pump_module, "SETTLE_CHECK_DURATION", 0.1):
      await driver.setup(skip_arm=True)
    self.addAsyncCleanup(driver.stop)
    self.assertEqual(led_bar_frames(written=link.written), [START_IDLE_ANIMATION])

  async def test_a_progress_fault_only_warns(self):
    link = SelfTestLink()
    del link.answers[color_write(colors=[GREEN] * 7 + [OFF] * 13)]
    driver = self.make_driver(link=link)
    self.use_fake_clock()
    with mock.patch.object(pump_module, "SETTLE_CHECK_DURATION", 0.1):
      with self.assertLogs("pylabrobot.formulatrix.mantis.driver.features.led_bar", "WARNING"):
        await driver.setup(skip_arm=True)
    self.addAsyncCleanup(driver.stop)
    self.assertEqual(bar_states(written=link.written)[-2:], [GREEN, "idle"])

  async def test_a_bar_that_does_not_answer_does_not_fail_the_pump_setup(self):
    link = FakeLink(answers=make_answers())
    for colors in ([BLUE] * 20, [GREEN] * 20):
      del link.answers[color_write(colors=colors)]
    with self.assertLogs("pylabrobot.formulatrix.mantis.driver.features.led_bar", level="WARNING"):
      await self.set_up_pump(link=link)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    self.assertEqual([read for read in sent if read[0] == 10][-len(TARE_AT_ZERO) :], TARE_AT_ZERO)
    self.assertEqual(sent[-1], START_IDLE_ANIMATION)


class PumpDiscoveryTests(DriverTestCase):
  async def test_built_only_when_the_pressure_board_answers(self):
    for absent_boards, built in (((), True), ((10,), False)):
      with self.subTest(absent_boards=absent_boards):
        link = FakeLink(answers=make_answers(absent_boards=absent_boards))
        driver = await self.set_up_driver(link=link)
        self.assertEqual(driver.pump is not None, built)
        sent = [parse_frame(frame=frame)[1:] for frame in link.written]
        self.assertEqual(any(read in sent for read in CALIBRATION_READS), built)

  async def test_the_reservoir_release_and_the_self_test_on_a_v4_only(self):
    for absent_boards, controller, tare_and_self_test in (
      ((), "bottle", True),
      ((11,), None, False),
    ):
      with self.subTest(absent_boards=absent_boards):
        link = FakeLink(answers=make_answers(absent_boards=absent_boards))
        driver = await self.set_up_driver(link=link)
        assert driver.pump is not None
        self.assertEqual(driver.pump.reservoir_release_controller, controller)
        self.assertEqual(driver.pump.tare_and_self_test, tare_and_self_test)

  async def test_calibration_not_answered_on_a_v3_leaves_it_empty(self):
    answers = make_answers(absent_boards=(11,))
    for board, command, request in CALIBRATION_READS:
      del answers[(board, command, request)]
    driver = await self.set_up_driver(link=FakeLink(answers=answers))
    self.assertIsNotNone(driver.pump)
    self.assertEqual(driver.configuration.feedback_sensor_parameters, {})

  async def test_declared_calibration_is_ignored_and_not_saved(self):
    calibration = {"bottle": [0.01, -14.0], "chip": [0.01, -14.0], "vacuum": [0.01, -38.0]}
    device = RECORDING["device"].items()
    without = {field: value for field, value in device if field != "feedback_sensor_parameters"}
    for device in (without, {**without, "feedback_sensor_parameters": calibration}):
      with self.subTest(declared="feedback_sensor_parameters" in device):
        with tempfile.TemporaryDirectory() as directory:
          path = os.path.join(directory, "declared.json")
          with open(path, "w", encoding="utf-8") as f:
            json.dump({**RECORDING, "device": device}, f)
          with self.assertNoLogs("pylabrobot.formulatrix.mantis", level="WARNING"):
            driver = await self.set_up_driver(
              link=FakeLink(answers=make_answers()), declared_configuration_json=path
            )
          self.assertEqual(driver.configuration.feedback_sensor_parameters, RECORDED_CALIBRATION)
          saved = os.path.join(directory, "saved.json")
          driver.save_configuration(path=saved)
          with open(saved, encoding="utf-8") as f:
            self.assertNotIn("feedback_sensor_parameters", json.load(f)["device"])

  async def test_recording_holds_the_recorded_calibration(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      declared = json.load(f)["device"]["feedback_sensor_parameters"]
    self.assertEqual(
      declared, {controller: list(value) for controller, value in RECORDED_CALIBRATION.items()}
    )


if __name__ == "__main__":
  unittest.main()
